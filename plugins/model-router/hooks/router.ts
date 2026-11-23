import type { EngineInterface, ModelEffort, PluginOptions, Register, SettingsSource, TurnUsage } from 'claude-code'

// The engine's own events, forwarded to the Python runtime the settings hooks use, so
// one router decides everything. agent.spawn sees every subagent, teammate and workflow
// agent; turn.step carries the effort that agent.spawn has no field for.

const EFFORTS: readonly ModelEffort[] = ['low', 'medium', 'high', 'xhigh', 'max']
const KEPT = 500
const SETTINGS_MS = 2000
const SOURCES: readonly SettingsSource[] = ['policy', 'user', 'project', 'local', 'flag']
const KEYS = ['availableModels', 'deniedModels', 'availableModelsMatch', 'modelOverrides']

type Pick = { model?: string; effort?: ModelEffort } | null

// Settings hooks get the plugin's options as CLAUDE_PLUGIN_OPTION_* variables; a mod gets
// them in register(), so they are passed on the same way.
function optionEnv(options: PluginOptions): Record<string, string> {
  const env: Record<string, string> = {}
  for (const [key, value] of Object.entries(options)) {
    env[`CLAUDE_PLUGIN_OPTION_${key.toUpperCase()}`] = Array.isArray(value) ? value.join(',') : String(value)
  }
  return env
}

// Never throws: a hook that already called next(e) must not reach its .catch and call it again.
async function ask($: EngineInterface, options: Record<string, string>, command: string, payload: object): Promise<Pick> {
  try {
    const env = { ...(await $.process.hookEnv()), ...options }
    const run = await $.process.run(['sh', `${$.plugin.root}/scripts/model-router.sh`, command], {
      stdin: JSON.stringify({ session_id: env.CLAUDE_CODE_SESSION_ID, ...payload }),
      env,
      timeoutMs: 8000,
    })
    if (run.exitCode !== 0 || !run.stdout.trim()) return null
    return JSON.parse(run.stdout) as Pick
  } catch {
    return null
  }
}

// Behind a gateway or a third-party provider an effort change drops the cache, and the
// session holds no first-party credential. The handle is only looked at, never spent.
// The desktop app never lends its sign-in to plugins, so there a null says nothing.
async function hasFirstPartyCredential($: EngineInterface): Promise<boolean | undefined> {
  const auth = await Promise.resolve().then(() => $.session.authorize()).catch(() => undefined)
  if (auth === undefined) return undefined
  if (auth !== null) return true
  const surfaces = await Promise.resolve().then(() => $.session.surfaces()).catch(() => undefined)
  return surfaces && !surfaces.includes('desktop') ? false : undefined
}

// Only these keys cross to the runtime. A source whose read fails or is still pending after
// two seconds is left out, and the runtime fills it from the files. Never throws, like ask.
async function modelSettings($: EngineInterface): Promise<Record<string, Record<string, unknown>> | undefined> {
  try {
    const layers: Record<string, Record<string, unknown>> = {}
    const reads = SOURCES.map(async source => {
      try {
        const read = (await $.settings.read({ source })) as Record<string, unknown>
        layers[source] = Object.fromEntries(KEYS.filter(key => key in read).map(key => [key, read[key]]))
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

function effortRank(effort: ModelEffort | undefined): number {
  return effort === undefined ? -1 : EFFORTS.indexOf(effort)
}

function remember<V>(map: Map<string, V>, key: string, value: V): void {
  map.set(key, value)
  if (map.size > KEPT) map.delete(map.keys().next().value as string)
}

export const register: Register = (on, options) => {
  const opts = optionEnv(options)
  const agentEffort = new Map<string, ModelEffort>()
  const turnEffort = new Map<string, ModelEffort>()
  // agent.spawn carries no effort: an effort the caller put on the Agent call is read here.
  const callerEffort = new Map<string, ModelEffort>()
  // A subagent's first request can go out before its spawn hook has the agent's id.
  const promptEffort = new Map<string, ModelEffort>()
  // Each request's usage by turn: Haiku 5.5 bills a long prompt per request, not per turn.
  const turnRequests = new Map<string, TurnUsage[]>()

  on('tool.call', { tool: 'Agent' }, ($, e, next) => {
    if (e.tool_use_id && typeof e.effort === 'string') remember(callerEffort, e.tool_use_id, e.effort)
    return next(e)
  }).catch(($, e, next) => next(e))

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    const layers = await modelSettings($)
    await ask($, opts, 'mod-start', { ...(layers ? { settings: layers } : {}) })
    return started
  }).catch(($, e, next) => next(e))

  on('agent.spawn', async ($, e, next) => {
    if (e.fork || e.resume) return next(e)
    const effort = callerEffort.get(e.tool_use_id)
    const layers = await modelSettings($)
    const pick = await ask($, opts, 'mod-spawn', {
      ...(layers ? { settings: layers } : {}),
      cwd: e.cwd,
      parent_agent_id: e.parentAgentId,
      parent_model: e.parentModel,
      kind: e.workflow ? 'workflow' : e.isTeammate ? 'teammate' : 'subagent',
      tool_input: {
        subagent_type: e.subagentType,
        prompt: e.prompt,
        description: e.description,
        ...(e.model ? { model: e.model } : {}),
        ...(effort ? { effort } : {}),
      },
    })
    if (pick?.effort) remember(promptEffort, e.prompt, pick.effort)
    const started = await next(pick?.model ? { ...e, model: pick.model } : e)
    if (pick?.effort && started.agentId) remember(agentEffort, started.agentId, pick.effort)
    return started
  }).catch(($, e, next) => next(e))

  // Where the prompt that starts the next main-loop turn came from: a teammate's report or
  // a notification is not the person's request. Set before next(e), since the turn starts
  // inside it; taken once, and a turn with no submit sends none.
  let idleOrigin: string | undefined
  // A typed /skill turn starts with a <command-message> line; what it asks for is the
  // command's arguments and the body skill.prompt renders for it before the turn starts.
  let idleCommand: { name: string; args: string; body?: string } | undefined
  on('prompt.submit', async ($, e, next) => {
    const idle = e.turnId === undefined
    const kind = idle && typeof e.origin?.kind === 'string' ? e.origin.kind : undefined
    if (kind) idleOrigin = kind
    if (idle) idleCommand = e.command ? { name: e.command.name, args: e.command.args } : undefined
    const entered = await next(e)
    if (idle && typeof (entered as { drop?: unknown } | undefined)?.drop === 'string') {
      idleOrigin = undefined
      idleCommand = undefined
    }
    return entered
  }).catch(($, e, next) => next(e))

  on('skill.prompt', async ($, e, next) => {
    const rendered = await next(e)
    if (idleCommand && idleCommand.body === undefined && idleCommand.name === e.skill) {
      idleCommand.body = rendered.text
    }
    return rendered
  }).catch(($, e, next) => next(e))

  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      const origin = idleOrigin
      const command = idleCommand
      idleOrigin = undefined
      idleCommand = undefined
      const model = await Promise.resolve().then(() => $.session.model()).catch(() => undefined)
      const firstParty = options.main_effort === false ? undefined : await hasFirstPartyCredential($)
      const layers = await modelSettings($)
      const pick = await ask($, opts, 'mod-turn', {
        ...(layers ? { settings: layers } : {}),
        text: e.text, ...(model ? { model } : {}), ...(firstParty !== undefined ? { first_party: firstParty } : {}),
        ...(origin ? { origin } : {}), ...(command && e.text.trimStart().startsWith('<command-') ? { command } : {}),
      })
      if (pick?.effort) remember(turnEffort, e.turnId, pick.effort)
    } else if (!agentEffort.has(e.agentId)) {
      const effort = promptEffort.get(e.text)
      if (effort) remember(agentEffort, e.agentId, effort)
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('turn.step', async function* ($, e, next) {
    const current = typeof e.effort === 'string' ? e.effort : undefined
    // A subagent runs at the effort its subtask was given; the main loop's turn effort
    // only ever raises what the session already asks for.
    const want = e.agentId !== undefined ? agentEffort.get(e.agentId) : turnEffort.get(e.turnId)
    const apply = want !== undefined && current !== undefined && want !== current
      && (e.agentId !== undefined || effortRank(want) > effortRank(current))
    const result = yield* next(apply ? { ...e, effort: want } : e)
    if (result?.usage) {
      const requests = turnRequests.get(e.turnId)
      if (requests) requests.push(result.usage)
      else remember(turnRequests, e.turnId, [result.usage])
    }
    return result
  }).catch(async function* ($, e, next) {
    return yield* next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    const requests = turnRequests.get(e.turnId)
    turnRequests.delete(e.turnId)
    if (e.agentId === undefined) turnEffort.delete(e.turnId)
    if (e.usage) await ask($, opts, 'mod-usage', { agent_id: e.agentId, usage: e.usage, ...(requests ? { requests } : {}) })
    return done
  }).catch(($, e, next) => next(e))
}
