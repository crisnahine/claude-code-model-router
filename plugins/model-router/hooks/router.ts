import type { EngineInterface, ModelEffort, PluginOptions, Register, SettingsSource } from 'claude-code'

// The engine's own events, forwarded to the Python runtime the settings hooks use, so
// one router decides everything. agent.spawn sees every subagent, teammate and workflow
// agent; turn.step carries the effort that agent.spawn has no field for.

const KEPT = 500
const SETTINGS_MS = 2000
const SOURCES: readonly SettingsSource[] = ['policy', 'user', 'project', 'local', 'flag']
const KEYS = ['availableModels', 'deniedModels', 'availableModelsMatch', 'modelOverrides', 'maxEffortLevel', 'modelSettings']
const EFFORTS: readonly string[] = ['low', 'medium', 'high', 'xhigh', 'max']
// The max_tier ladder, weakest first, for a call the runtime didn't answer.
const TIERS: readonly string[] = ['haiku', 'sonnet', 'opus', 'fable']
// The id names a file the fallback looks up, so only a plain one is marked (scripts/modelrouter/mod.py routes).
const PLAIN_ID = /^[A-Za-z0-9_-]{1,128}$/

// `note` is for Claude after the Agent call's result; `rules` is the session note, for the Agent tool's description.
type Pick = { model?: string; effort?: ModelEffort; note?: unknown; rules?: unknown; workflow_rules?: unknown } | null
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

// The runtime's answer (every command answers at least { ok: true }), null when no Python ran, or undefined when
// the round trip failed. Never throws: a hook that already called next(e) must not reach its .catch and call it again.
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

// An empty file a settings hook takes (scripts/modelrouter/mod.py): `<id>` stands the fallback down for the call, and
// `<id>.moved` tells post-agent that another mod moved its spawn. Whether it was written. Never throws.
async function mark($: EngineInterface, name: string): Promise<boolean> {
  try {
    const data = (await $.process.hookEnv()).CLAUDE_PLUGIN_DATA
    if (!data) return false
    await $.fs.write(`${data}/mod-calls/${name}`, '')
    return true
  } catch {
    return false
  }
}

function family(model: string | undefined): string | undefined {
  return model === undefined ? undefined : TIERS.find(tier => model.toLowerCase().includes(tier)) ?? model.toLowerCase()
}

// Whether a later link moved the spawn to another family than this mod sent: the engine's own entry holds what core
// was asked for. A trace without one, or without a model there, says nothing. Never throws.
function movedOn(trace: readonly { plugin: string; received: { model?: string } }[], sent: string | undefined): boolean {
  try {
    const asked = trace.find(entry => entry.plugin === 'engine')?.received?.model
    return typeof asked === 'string' && family(asked) !== family(sent)
  } catch {
    return false
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

type Layers = Record<string, Record<string, unknown>>
// Each source's newest answer, the reads not answered yet, and the sources that once missed SETTINGS_MS.
type Reads = { latest: Layers; out: Set<SettingsSource>; slow: Set<SettingsSource> }

// Only these keys cross to the runtime. A source whose read fails is left out, and the runtime keeps what it last
// sent or reads its file. A source still out after two seconds is read without waiting from then on, so its answer
// goes with a later command. Never throws, like ask.
async function modelSettings($: EngineInterface, reads: Reads): Promise<Layers | undefined> {
  try {
    // Only this call's own reads can turn out slow: one a concurrent call started may be milliseconds old.
    const pending = new Set<SettingsSource>()
    const waited = SOURCES.filter(source => !reads.out.has(source)).map(source => {
      reads.out.add(source)
      pending.add(source)
      const read = (async () => {
        try {
          const found = (await $.settings.read({ source })) as Record<string, unknown>
          reads.latest[source] = Object.fromEntries(KEYS.filter(key => key in found)
            .map(key => [key, key === 'modelSettings' ? effortCaps(found[key]) : found[key]]))
        } catch {
          delete reads.latest[source]
        } finally {
          reads.out.delete(source)
          pending.delete(source)
        }
      })()
      return reads.slow.has(source) ? undefined : read
    })
    let timer: { cancel: () => void } | undefined
    await Promise.race([Promise.all(waited), new Promise<void>(resolve => { timer = $.clock.after(SETTINGS_MS, resolve) })])
    timer?.cancel()
    for (const source of pending) reads.slow.add(source)
    return Object.keys(reads.latest).length ? { ...reads.latest } : undefined
  } catch {
    return undefined
  }
}

// The settings layers, as session.start sends them, and the runtime's answer. Never throws, like ask.
async function sendLayers($: EngineInterface, opts: Record<string, string>, reads: Reads): Promise<Pick | undefined> {
  const layers = await modelSettings($, reads)
  return await ask($, opts, 'mod-start', { ...(layers ? { settings: layers } : {}) })
}

// The session's id, or undefined when it can't be read. Never throws.
async function sessionId($: EngineInterface): Promise<string | undefined> {
  try {
    return await $.session.id()
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

// The user's cap on a model the call names, for a call that neither path routed. Only the family is known here.
function capped(model: string | undefined, maxTier: string | undefined): string | undefined {
  const cap = TIERS.indexOf(maxTier ?? '')
  const named = TIERS.findIndex(tier => model?.toLowerCase().includes(tier))
  return cap >= 0 && named > cap ? TIERS[cap] : undefined
}

// routing_mode as the runtime reads it (config.parse_mode): anything but off is auto.
function routing(opts: Record<string, string>): boolean {
  return (opts.CLAUDE_PLUGIN_OPTION_ROUTING_MODE ?? '').trim().toLowerCase() !== 'off'
}

function remember<V>(map: Map<string, V>, key: string, value: V): void {
  map.set(key, value)
  if (map.size > KEPT) map.delete(map.keys().next().value as string)
}

export const register: Register = (on, options) => {
  const opts = optionEnv(options)
  // Whether the runtime answers. Only an answer turns it on, and none, or a failed round trip, turns it off. While
  // it's off, the fallback routes each Agent call.
  let answering = false
  const heard = (reply: Pick | undefined): Pick | undefined => {
    answering = !!reply
    return reply
  }
  // Each Agent call this mod heard, by tool_use_id: whether it marked the call or left it to the fallback.
  const marked = new Map<string, boolean>()
  const reads: Reads = { latest: {}, out: new Set(), slow: new Set() }
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
  // What the runtime has Claude read after an Agent call's result, by tool_use_id.
  const notes = new Map<string, string>()
  // The session note the last mod-start answer carried, and the session id the layers last went to.
  // The runtime's table for each tool whose description carries it.
  const rules: Record<string, string> = {}
  let sentFor: string | undefined
  const started = (reply: Pick | undefined): void => {
    const answer = heard(reply)
    if (typeof answer?.rules === 'string') rules.Agent = answer.rules
    if (typeof answer?.workflow_rules === 'string') rules.Workflow = answer.workflow_rules
  }

  on('tool.call', { tool: 'Agent' }, async ($, e, next) => {
    const id = typeof e.tool_use_id === 'string' ? e.tool_use_id : ''
    if (id && typeof e.effort === 'string') remember(callerEffort, id, e.effort)
    if (id) remember(marked, id, answering && PLAIN_ID.test(id) && await mark($, id))
    const done = await next(e)
    const note = notes.get(id)
    notes.delete(id)
    if (!note || !done || done.deny !== undefined || done.isError) return done
    return { ...done, context: [...(Array.isArray(done.context) ? done.context : []), note] }
  }).catch(($, e, next) => next(e))

  on('tool.describe', async ($, e, next) => {
    const described = await next(e)
    // Claude reads the table where it fills in the call, and the tool's own text sets `effort` only when asked to.
    const text = Object.hasOwn(rules, e.tool) ? rules[e.tool] : undefined
    return text && typeof described?.description === 'string'
      ? { ...described, description: `${described.description}\n\n${text}` } : described
  }).catch(($, e, next) => next(e))

  on('session.start', async ($, e, next) => {
    const done = await next(e)
    sentFor = await sessionId($) ?? sentFor
    started(await sendLayers($, opts, reads))
    return done
  }).catch(($, e, next) => next(e))

  on('session.end', ($, e, next) => {
    if (e.reason === 'clear') sentFor = undefined
    return next(e)
  }).catch(($, e, next) => next(e))

  on('agent.spawn', async ($, e, next) => {
    if (e.fork || e.resume) return next(e)
    const unmarked = marked.get(e.tool_use_id) === false
    marked.delete(e.tool_use_id)
    if (unmarked) return next(e) // the fallback routed it
    const effort = callerEffort.get(e.tool_use_id)
    const layers = await modelSettings($, reads)
    const facts = {
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
    }
    // The fallback already stood down for this call, so a failed round trip is asked once more.
    let reply = await ask($, opts, 'mod-spawn', facts)
    if (reply === undefined) reply = await ask($, opts, 'mod-spawn', facts)
    const pick = heard(reply)
    if (typeof pick?.note === 'string' && e.tool_use_id) remember(notes, e.tool_use_id, pick.note)
    // Without an answer neither path routed this call, so only the cap can still be held, and only while routing.
    const model = pick ? pick.model : routing(opts) ? capped(e.model, opts.CLAUDE_PLUGIN_OPTION_MAX_TIER) : undefined
    const mine: Want | null = pick?.effort ? { effort: pick.effort, raiseOnly: !!e.workflow } : null
    const efforts = inFlight.get(e.prompt) ?? []
    efforts.push(mine)
    inFlight.set(e.prompt, efforts)
    try {
      const started = await next(model ? { ...e, model } : e)
      if (mine && started.agentId) remember(agentEffort, started.agentId, mine)
      if (started.agentId && e.workflow && e.subagentType) remember(agentType, started.agentId, e.subagentType)
      // A run on the model another mod moved it to is no substitution for the one this mod sent.
      if (!e.workflow && PLAIN_ID.test(e.tool_use_id) && movedOn(next.trace, model || e.model)) {
        await mark($, `${e.tool_use_id}.moved`)
      }
      return started
    } finally {
      efforts.splice(efforts.indexOf(mine), 1)
      if (!efforts.length) inFlight.delete(e.prompt)
    }
  }).catch(($, e, next) => next(e))

  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      // session.start doesn't fire after /clear, so the new session's first turn sends the layers.
      const id = await sessionId($)
      if (id !== undefined && id !== sentFor) {
        sentFor = id
        started(await sendLayers($, opts, reads))
      }
    } else if (!agentEffort.has(e.agentId)) {
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
    if (figures) heard(await ask($, opts, 'mod-usage', { agent_id: e.agentId, ...figures }))
    return done
  }).catch(($, e, next) => next(e))
}
