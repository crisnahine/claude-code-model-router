import type { EngineInterface, ModelEffort, PluginOptions, Register, SettingsSource } from 'claude-code'

// The engine's own events, forwarded to the Python runtime the settings hooks use, so
// one router decides everything. agent.spawn sees every subagent, teammate and workflow
// agent; turn.step carries the effort that agent.spawn has no field for.

const KEPT = 500
const SETTINGS_MS = 2000
const SOURCES: readonly SettingsSource[] = ['policy', 'user', 'project', 'local', 'flag']
const KEYS = ['availableModels', 'deniedModels', 'availableModelsMatch', 'modelOverrides', 'maxEffortLevel', 'modelSettings']
const EFFORTS: readonly string[] = ['low', 'medium', 'high', 'xhigh', 'max']
// The id names a file the fallback looks up, so only a plain one is marked (scripts/modelrouter/mod.py routes).
const PLAIN_ID = /^[A-Za-z0-9_-]{1,128}$/

type Pick = { model?: string; effort?: ModelEffort } | null
// The effort a routed agent steps at. A workflow script can set a stage's effort, which agent.spawn doesn't carry,
// so a workflow agent's is only ever raised.
type Want = { effort: ModelEffort; raiseOnly: boolean }

// Settings hooks get the plugin's options as CLAUDE_PLUGIN_OPTION_* variables; a mod gets
// them in register(), so they are passed on the same way.
function optionEnv(options: PluginOptions): Record<string, string> {
  const env: Record<string, string> = {}
  for (const [key, value] of Object.entries(options)) {
    env[`CLAUDE_PLUGIN_OPTION_${key.toUpperCase()}`] = Array.isArray(value) ? value.join(',') : String(value)
  }
  return env
}

// The runtime's answer, null for none, or undefined when the round trip failed. Never throws: a hook that
// already called next(e) must not reach its .catch and call it again.
async function ask($: EngineInterface, options: Record<string, string>, command: string, payload: object): Promise<Pick | undefined> {
  try {
    const env = { ...(await $.process.hookEnv()), ...options }
    const run = await $.process.run(['sh', `${$.plugin.root}/scripts/model-router.sh`, command], {
      stdin: JSON.stringify({ session_id: env.CLAUDE_CODE_SESSION_ID, ...payload }),
      env,
      timeoutMs: 8000,
    })
    if (run.exitCode !== 0) return undefined
    return run.stdout.trim() ? JSON.parse(run.stdout) as Pick : null
  } catch {
    return undefined
  }
}

// Tells the fallback's PreToolUse hook, which runs after this one, that agent.spawn routes this call. Never throws.
async function mark($: EngineInterface, toolUseId: string): Promise<void> {
  try {
    const data = (await $.process.hookEnv()).CLAUDE_PLUGIN_DATA
    if (data) await $.fs.write(`${data}/mod-calls/${toolUseId}`, '')
  } catch {
    // unmarked: the fallback routes the call too
  }
}

function raises(want: string, current: string): boolean {
  const from = EFFORTS.indexOf(current)
  return from >= 0 && EFFORTS.indexOf(want) > from
}

// modelSettings can hold more per model than the effort cap, and the runtime reads only the cap.
function effortCaps(raw: unknown): unknown {
  if (!raw || typeof raw !== 'object') return raw
  return Object.fromEntries(Object.entries(raw as Record<string, unknown>).flatMap(([id, entry]) =>
    entry && typeof entry === 'object' && 'maxEffortLevel' in entry
      ? [[id, { maxEffortLevel: (entry as Record<string, unknown>).maxEffortLevel }]] : []))
}

// Only these keys cross to the runtime. A source whose read fails or is still pending after
// two seconds is left out, and the runtime fills it from the files. Never throws, like ask.
async function modelSettings($: EngineInterface): Promise<Record<string, Record<string, unknown>> | undefined> {
  try {
    const layers: Record<string, Record<string, unknown>> = {}
    const reads = SOURCES.map(async source => {
      try {
        const read = (await $.settings.read({ source })) as Record<string, unknown>
        layers[source] = Object.fromEntries(KEYS.filter(key => key in read)
          .map(key => [key, key === 'modelSettings' ? effortCaps(read[key]) : read[key]]))
      } catch {
        // left out
      }
    })
    let timer: { cancel: () => void } | undefined
    await Promise.race([Promise.all(reads), new Promise<void>(resolve => { timer = $.clock.after(SETTINGS_MS, resolve) })])
    timer?.cancel()
    return Object.keys(layers).length ? { ...layers } : undefined
  } catch {
    return undefined
  }
}

// Claude Code's own figures for one subagent: its tokens per model and its estimate in dollars, both over
// all of its turns so far. None for an agent the engine doesn't list, such as a workflow's. Never throws.
async function agentUsage($: EngineInterface, id: string): Promise<object | undefined> {
  try {
    const agent = (await $.agent.list()).find(a => a.id === id)
    if (!agent || (!agent.usage?.length && !agent.cost)) return undefined
    // Plan windows appear on a subscription only; a gateway's spend_limit is not one.
    const plan = (await $.session.usage()).rateLimits.some(r => r.kind === 'five_hour' || r.kind === 'seven_day')
    return {
      subagent_type: agent.type, models: agent.usage ?? [],
      ...(agent.cost ? { cost: agent.cost } : {}), ...(plan ? { subscription: true } : {}),
    }
  } catch {
    return undefined
  }
}

function remember<V>(map: Map<string, V>, key: string, value: V): void {
  map.set(key, value)
  if (map.size > KEPT) map.delete(map.keys().next().value as string)
}

export const register: Register = (on, options) => {
  const opts = optionEnv(options)
  // Whether the runtime's last round trip worked. Until one has, and after one fails, the fallback routes.
  let answering = false
  const agentEffort = new Map<string, Want>()
  // The type of each workflow agent this mod started, so its usage can name it: the engine doesn't list it.
  const agentType = new Map<string, string>()
  // The main conversation's level, from its latest request: an agent on its model runs at it unless told otherwise.
  let sessionEffort: string | undefined
  // agent.spawn carries no effort: an effort the caller put on the Agent call is read here.
  const callerEffort = new Map<string, ModelEffort>()
  // A subagent's first request can go out before its spawn hook has the agent's id. The spawns of each prompt
  // between their pick and next() returning, with the effort each got (null for none): the first request takes
  // that effort only while every one of them got the same, so it never runs at another spawn's.
  const inFlight = new Map<string, (Want | null)[]>()

  on('tool.call', { tool: 'Agent' }, async ($, e, next) => {
    if (e.tool_use_id && typeof e.effort === 'string') remember(callerEffort, e.tool_use_id, e.effort)
    if (answering && typeof e.tool_use_id === 'string' && PLAIN_ID.test(e.tool_use_id)) await mark($, e.tool_use_id)
    return next(e)
  }).catch(($, e, next) => next(e))

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    const layers = await modelSettings($)
    answering = (await ask($, opts, 'mod-start', { ...(layers ? { settings: layers } : {}) })) !== undefined
    return started
  }).catch(($, e, next) => next(e))

  on('agent.spawn', async ($, e, next) => {
    if (e.fork || e.resume) return next(e)
    const effort = callerEffort.get(e.tool_use_id)
    const layers = await modelSettings($)
    const pick = await ask($, opts, 'mod-spawn', {
      ...(layers ? { settings: layers } : {}),
      parent_agent_id: e.parentAgentId,
      parent_model: e.parentModel,
      ...(sessionEffort ? { session_effort: sessionEffort } : {}),
      kind: e.workflow ? 'workflow' : e.isTeammate ? 'teammate' : 'subagent',
      // A workflow's id is shared by all of its agents, so it can't tell one route from another.
      ...(e.tool_use_id && !e.workflow ? { tool_use_id: e.tool_use_id } : {}),
      tool_input: {
        subagent_type: e.subagentType,
        prompt: e.prompt,
        description: e.description,
        ...(e.model ? { model: e.model } : {}),
        ...(effort ? { effort } : {}),
      },
    })
    answering = pick !== undefined
    const mine: Want | null = pick?.effort ? { effort: pick.effort, raiseOnly: !!e.workflow } : null
    const efforts = inFlight.get(e.prompt) ?? []
    efforts.push(mine)
    inFlight.set(e.prompt, efforts)
    try {
      const started = await next(pick?.model ? { ...e, model: pick.model } : e)
      if (mine && started.agentId) remember(agentEffort, started.agentId, mine)
      if (started.agentId && e.workflow && e.subagentType) remember(agentType, started.agentId, e.subagentType)
      return started
    } finally {
      efforts.splice(efforts.indexOf(mine), 1)
      if (!efforts.length) inFlight.delete(e.prompt)
    }
  }).catch(($, e, next) => next(e))

  on('turn.start', ($, e, next) => {
    if (e.agentId !== undefined && !agentEffort.has(e.agentId)) {
      const [want, ...others] = inFlight.get(e.text) ?? []
      if (want && others.every((other) => other?.effort === want.effort && other.raiseOnly === want.raiseOnly)) {
        remember(agentEffort, e.agentId, want)
      }
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('turn.step', async function* ($, e, next) {
    const current = typeof e.effort === 'string' ? e.effort : undefined
    if (e.agentId === undefined && current !== undefined) sessionEffort = current
    // A subagent runs at the effort its subtask was given.
    const want = e.agentId !== undefined ? agentEffort.get(e.agentId) : undefined
    const apply = want !== undefined && current !== undefined
      && (want.raiseOnly ? raises(want.effort, current) : want.effort !== current)
    return yield* next(apply ? { ...e, effort: want.effort } : e)
  }).catch(async function* ($, e, next) {
    return yield* next(e)
  })

  // The main loop's cost and tokens are Claude Code's own (/usage), so only a subagent's turn is recorded.
  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    if (e.agentId === undefined) return done
    const type = agentType.get(e.agentId)
    const figures = await agentUsage($, e.agentId) ?? (e.usage ? { ...(type ? { subagent_type: type } : {}), usage: e.usage } : undefined)
    if (figures) answering = (await ask($, opts, 'mod-usage', { agent_id: e.agentId, ...figures })) !== undefined
    return done
  }).catch(($, e, next) => next(e))
}
