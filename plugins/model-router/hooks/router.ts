import type { EngineInterface, ModelEffort, PluginOptions, Register, SettingsSource } from 'claude-code'

// The engine's own events, forwarded to the Python runtime the settings hooks use, so
// one router decides everything. agent.spawn sees every subagent, teammate and workflow
// agent; turn.step carries the effort that agent.spawn has no field for.

const KEPT = 500
const SETTINGS_MS = 2000
const SOURCES: readonly SettingsSource[] = ['policy', 'user', 'project', 'local', 'flag']
const KEYS = ['availableModels', 'deniedModels', 'availableModelsMatch', 'modelOverrides', 'maxEffortLevel', 'modelSettings', 'effortLevel']
const EFFORTS: readonly string[] = ['low', 'medium', 'high', 'xhigh', 'max']
// The max_tier ladder, weakest first, for a call the runtime didn't answer, and the other names a family answers to
// (config/models.json `twins`).
const TIERS: readonly string[] = ['haiku', 'sonnet', 'opus', 'fable']
const TWINS: Readonly<Record<string, string>> = { mythos: 'fable' }
// The id names a file the fallback looks up, so only a plain one is marked (scripts/modelrouter/mod.py routes).
const PLAIN_ID = /^[A-Za-z0-9_-]{1,128}$/
// A model a repository's settings can set is shown only as a plain name, as the runtime shows one (config._PLAIN_NAME).
const PLAIN_NAME = /^[A-Za-z0-9._:/@[\]-]{1,100}$/
// The mod's slash command, and the session check's /config row, keyed as Claude Code keys a plugin's userConfig field.
const COMMAND = 'model-router'
const CHECK = 'model-router.session_check'

// What the runtime answers. A spawn's: `note` is for Claude after the Agent call's result; `rules` is the session note,
// for the Agent tool's description. mod-status: `line`, `text`, `reply`, `routing`. mod-check: a Step's fields.
type Answer = {
  model?: string; effort?: ModelEffort; note?: unknown; rules?: unknown; workflow_rules?: unknown
  line?: unknown; text?: unknown; reply?: unknown; routing?: unknown; question?: unknown; header?: unknown; options?: unknown
} | null
// One question of the session check; each choice may lead to the next (`then`) and says what picking it does.
type Step = { question: string; header?: string; options: Choice[] }
type Choice = { label: string; family?: string; model?: string; effort?: string; check?: string; done?: string; then?: Step }
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
async function ask($: EngineInterface, options: Record<string, string>, command: string, payload: object): Promise<Answer | undefined> {
  try {
    const env = { ...(await $.process.hookEnv()), ...options }
    const run = await $.process.run(['sh', `${$.plugin.root}/scripts/model-router.sh`, command], {
      stdin: JSON.stringify({ session_id: env.CLAUDE_CODE_SESSION_ID, ...payload }),
      env,
      timeoutMs: 8000,
    })
    if (run.exitCode !== 0) return undefined
    return run.stdout.trim() ? JSON.parse(run.stdout) as Answer : null
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
  const name = model?.toLowerCase()
  return name === undefined ? undefined
    : TIERS.find(tier => name.includes(tier)) ?? Object.entries(TWINS).find(([twin]) => name.includes(twin))?.[1] ?? name
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

// modelSettings can hold more per model than its effort fields, and the runtime reads only those.
function effortFields(raw: unknown): unknown {
  if (!raw || typeof raw !== 'object') return raw
  return Object.fromEntries(Object.entries(raw as Record<string, unknown>).flatMap(([id, entry]) => {
    const kept = entry && typeof entry === 'object'
      ? Object.fromEntries(Object.entries(entry).filter(([key]) => key === 'maxEffortLevel' || key === 'effortLevel')) : {}
    return Object.keys(kept).length ? [[id, kept]] : []
  }))
}

// A copy at most `depth` levels deep, null below. The runtime reads these keys three levels deep at most
// (modelSettings, a model, its field), and a value nested past its JSON parser's limit would fail the whole command.
function shallow(value: unknown, depth = 3): unknown {
  if (value === null || typeof value !== 'object') return value
  if (depth === 0) return null
  return Array.isArray(value) ? value.map(item => shallow(item, depth - 1))
    : Object.fromEntries(Object.entries(value).map(([key, item]) => [key, shallow(item, depth - 1)]))
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
            .map(key => [key, shallow(key === 'modelSettings' ? effortFields(found[key]) : found[key])]))
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
async function sendLayers($: EngineInterface, opts: Record<string, string>, reads: Reads): Promise<Answer | undefined> {
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

type Listed = Awaited<ReturnType<EngineInterface['agent']['list']>>[number]

// The model and effort the agent's last request carried, past caps and this mod's own turn.step rewrite: what a redo
// climbs from. A null effort means none was sent. A number (internal builds only) says nothing, so no pair is sent.
function ranAt(agent: Listed, usage: { model?: string } | undefined): object | undefined {
  const last = agent.lastRequest
  if (!last || typeof last.effort === 'number') return undefined
  const model = last.model ?? usage?.model ?? agent.model
  return model ? { model, effort: last.effort ?? null } : undefined
}

// Claude Code's own figures for one subagent: its tokens per model and its estimate in dollars, both over
// all of its turns so far, and what it ran at. None for an agent the engine doesn't list, such as a workflow's.
// Never throws.
async function agentUsage($: EngineInterface, id: string, usage?: { model?: string }): Promise<{ figures?: object; ran?: object } | undefined> {
  try {
    const agent = (await $.agent.list()).find(a => a.id === id)
    if (!agent) return undefined
    const ran = ranAt(agent, usage)
    if (!agent.usage?.length && !agent.cost) return { ran }
    // Plan windows appear on a subscription only; a gateway's spend_limit is not one.
    const plan = (await $.session.usage()).rateLimits.some(r => r.kind === 'five_hour' || r.kind === 'seven_day')
    return {
      ran,
      figures: {
        subagent_type: agent.type, models: agent.usage ?? [],
        ...(agent.cost ? { cost: agent.cost } : {}), ...(plan ? { subscription: true } : {}),
      },
    }
  } catch {
    return undefined
  }
}

// The user's cap on a model the call names, for a call that neither path routed. Only the family is known here,
// so a model on no tier is left as it is.
function capped(model: string | undefined, maxTier: string | undefined): string | undefined {
  const cap = TIERS.indexOf(maxTier ?? '')
  return cap >= 0 && TIERS.indexOf(family(model) ?? '') > cap ? TIERS[cap] : undefined
}

// routing_mode as the runtime reads it (config.parse_mode): anything but off is auto.
function routing(opts: Record<string, string>): boolean {
  return (opts.CLAUDE_PLUGIN_OPTION_ROUTING_MODE ?? '').trim().toLowerCase() !== 'off'
}

// The interactive features call each platform call inside a try: on a Claude Code without it, or a surface that can't
// draw it, the feature is absent and routing goes on. The mod loader takes $ only as calls, so nothing is probed first.
function toast($: EngineInterface, text: string): void {
  try {
    $.ui.toast(text)
  } catch {}
}

// Runs `task` once the calling hook has returned, so nothing waits on it. Whether it was scheduled. Never throws.
function later($: EngineInterface, task: () => Promise<unknown>): boolean {
  try {
    $.clock.after(0, () => { task().catch(() => {}) })
    return true
  } catch {
    return false
  }
}

// Whether managed settings own the session check's option; a list that fails counts as owned, so nothing offers to write it.
async function locked($: EngineInterface): Promise<boolean> {
  try {
    return (await $.config.list()).find(row => row.key === CHECK)?.isLocked !== false
  } catch {
    return true
  }
}

// Writes session_check, and says what happened. Claude Code itself refuses the write when managed settings own the row.
async function setCheck($: EngineInterface, value: 'ask' | 'off'): Promise<string> {
  try {
    const done = await $.config.set({ key: CHECK, value })
    if (done.deny !== undefined) {
      const row = (await $.config.list().catch(() => [])).find(found => found.key === CHECK)
      return row?.isLocked ? `Session check is set by managed settings, so it stays ${String(row.value)}.`
        : `Session check stays as it is: ${done.deny}`
    }
  } catch {
    return 'Session check stays as it is: Claude Code did not take the change.'
  }
  return value === 'off' ? 'Session check is off. /model-router check on, or Session check in /config, turns it back on.'
    : 'Session check is on: Model Router asks once when a new session starts below the recommended setups.'
}

// Asks `step` and each question its pick leads to: the choices picked, or undefined for a dismissal or an answer
// that is no label, such as text typed under the dialog's own rows, so a half-answered question switches nothing.
async function walk($: EngineInterface, step: Step): Promise<Choice[] | undefined> {
  let picked: string
  try {
    picked = await $.ui.ask(step.question, { options: step.options.map(choice => choice.label), header: step.header })
  } catch {
    return undefined
  }
  const choice = step.options.find(found => found.label === picked)
  if (!choice?.then) return choice && [choice]
  const rest = await walk($, choice.then)
  return rest && [choice, ...rest]
}

// Runs the picked setup through Claude Code's own commands, and says what the session is on now.
async function apply($: EngineInterface, picks: Choice[]): Promise<void> {
  if (picks.some(choice => choice.check === 'off')) return toast($, await setCheck($, 'off'))
  const chosen = picks.find(choice => choice.family)?.family
  const model = picks.find(choice => choice.model)?.model
  const effort = picks.find(choice => choice.effort)?.effort
  const done = picks.filter(choice => choice.done).at(-1)?.done
  if (!chosen || (!model && !effort)) return
  let now: string | undefined // the model a /model this pick ran switched to
  for (const [command, args] of [['model', model], ['effort', effort]] as const) {
    if (!args) continue
    try {
      await $.command.run({ command, args })
    } catch {
      return toast($, now ? `This session is now on ${PLAIN_NAME.test(now) ? now : 'the model you picked'}, but Claude Code did not run /effort ${args}.`
        : `Claude Code did not run /${command} ${args}, so the session is left as it is.`)
    }
    now = await $.session.model()
    // A switch the allowlist or a declined consent refused: an effort set now would be saved for the old model.
    if (family(now) !== chosen) {
      return toast($, `This session stays on ${PLAIN_NAME.test(now) ? now : 'its model'}: Claude Code did not switch it.`)
    }
    if ((command === 'effort' || !effort) && done) toast($, done)
  }
}

// What one load of the module knows of the person at the prompt and of this session.
type Live = {
  interactive: boolean // only then does the mod draw, ask, or register its command
  routingOn?: boolean // this session's routing as the runtime last answered it, a /model-router switch included
  setup?: { model: string; effort: string | null } // the main loop's model and effort, as its latest request carried them
  refreshing: boolean // one status refresh at a time: one asked for during it runs once more after it
  stale: boolean
  turned: boolean // the main loop's first turn started: a question now would land in the middle of work
  working: boolean // a main-loop turn is in flight: an option change now reloads the module under running agents
}

function shown($: EngineInterface, live: Live, answer: Answer | undefined): void {
  if (typeof answer?.routing === 'boolean') live.routingOn = answer.routing
  try {
    if (typeof answer?.line === 'string') $.ui.status(answer.line)
  } catch {}
}

function refresh($: EngineInterface, opts: Record<string, string>, live: Live): void {
  if (!live.interactive) return
  if (live.refreshing) {
    live.stale = true
    return
  }
  live.refreshing = later($, async () => {
    try {
      do {
        live.stale = false
        shown($, live, await ask($, opts, 'mod-status', live.setup ? { setup: live.setup } : {}))
      } while (live.stale)
    } finally {
      live.refreshing = false
    }
  })
}

// A --effort flag at launch is in no settings layer and no mod call, so it is read from the session's own command
// line, of the process Claude Code names. Only the level leaves this function.
async function launchEffort($: EngineInterface): Promise<string | undefined> {
  try {
    // shortcut: ps joins the arguments with spaces and drops their quotes, so "--effort max" inside another flag's
    // quoted text reads as the flag; read the arguments one by one if Claude Code ever hands them to a mod.
    const run = await $.process.run(['sh', '-c', 'ps -o args= -p "$CLAUDE_PID"'], { env: await $.process.hookEnv(), timeoutMs: 2000 })
    const level = /\s--effort[= ](low|medium|high|xhigh|max|ultracode)(?=\s|$)/.exec(run.stdout)?.[1]
    return level === 'ultracode' ? 'xhigh' : level
  } catch {
    return undefined
  }
}

// At most once per session id, which the runtime keeps across the reload a /config change causes.
async function checkSession($: EngineInterface, opts: Record<string, string>, live: Live): Promise<void> {
  // A conversation that already has prompts was resumed or forked, not started.
  if (await $.session.turns() !== 0) return
  const launched = await launchEffort($)
  const step = await ask($, opts, 'mod-check', { model: await $.session.model(), locked: await locked($),
    ...(launched ? { launch_effort: launched } : {}) })
  if (typeof step?.question !== 'string' || !Array.isArray(step.options) || live.turned) return
  const picks = await walk($, step as Step)
  if (picks) await apply($, picks)
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
  const heard = (reply: Answer | undefined): Answer | undefined => {
    answering = !!reply
    return reply
  }
  // Each Agent call this mod heard, by tool_use_id: whether it marked the call or left it to the fallback.
  const marked = new Map<string, boolean>()
  const reads: Reads = { latest: {}, out: new Set(), slow: new Set() }
  const agentEffort = new Map<string, Want>()
  // The type of each workflow agent this mod started, so its usage can name it: the engine doesn't list it.
  const agentType = new Map<string, string>()
  // The Agent call each routed subagent came from, so what it ran at reaches that call's route.
  const agentCall = new Map<string, string>()
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
  const live: Live = { interactive: false, refreshing: false, stale: false, turned: false, working: false }
  const started = (reply: Answer | undefined): void => {
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
    live.interactive = e.isInteractive === true
    if (live.interactive) {
      try {
        await $.command.register({ name: COMMAND, description: "Shows Model Router's state, or switches routing for this session",
          argumentHint: '[on | off | check on | check off]', immediate: true })
      } catch {}
      refresh($, opts, live)
      later($, () => checkSession($, opts, live))
    }
    return done
  }).catch(($, e, next) => next(e))

  on('session.end', async ($, e, next) => {
    if (e.reason === 'clear') sentFor = undefined
    const done = await next(e)
    if (live.interactive) {
      live.routingOn = undefined
      live.setup = undefined
      await ask($, opts, 'mod-end', { session_id: e.sessionId })
    }
    return done
  }).catch(($, e, next) => next(e))

  // Answered here, with no model turn; the text is for the person, and Claude Code puts the plugin's name before it.
  on('command.run', { command: COMMAND }, async ($, e, next) => {
    const words = e.args.trim().toLowerCase().split(/\s+/).join(' ')
    let text = 'Takes on, off, check on or check off.'
    if (words === 'on' || words === 'off') {
      const answer = await ask($, opts, 'mod-status', { routing: words, ...(live.setup ? { setup: live.setup } : {}) })
      shown($, live, answer)
      text = typeof answer?.reply === 'string' ? answer.reply : 'Routing is unchanged: the runtime did not answer.'
    } else if (words === 'check on' || words === 'check off') {
      text = live.working ? 'Run this when Claude is idle: changing an option reloads Model Router.'
        : await setCheck($, words === 'check on' ? 'ask' : 'off')
    } else if (!words) {
      const answer = await ask($, opts, 'mod-status', live.setup ? { setup: live.setup } : {})
      shown($, live, answer)
      text = typeof answer?.text === 'string' ? answer.text : 'The runtime did not answer, so the state is unknown.'
    }
    return { text, isTextDisplayOnly: true }
  }).catch(($, e, next) => next(e))

  on('agent.spawn', async ($, e, next) => {
    if (e.fork || e.resume) return next(e)
    const unmarked = marked.get(e.tool_use_id) === false
    marked.delete(e.tool_use_id)
    if (unmarked) {
      refresh($, opts, live)
      return next(e) // the fallback routed it
    }
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
    const model = pick ? pick.model : (live.routingOn ?? routing(opts)) ? capped(e.model, opts.CLAUDE_PLUGIN_OPTION_MAX_TIER) : undefined
    const mine: Want | null = pick?.effort ? { effort: pick.effort, raiseOnly: !!e.workflow } : null
    const efforts = inFlight.get(e.prompt) ?? []
    efforts.push(mine)
    inFlight.set(e.prompt, efforts)
    try {
      const started = await next(model ? { ...e, model } : e)
      if (mine && started.agentId) remember(agentEffort, started.agentId, mine)
      if (started.agentId && e.workflow && e.subagentType) remember(agentType, started.agentId, e.subagentType)
      if (started.agentId && !e.workflow && PLAIN_ID.test(e.tool_use_id)) remember(agentCall, started.agentId, e.tool_use_id)
      // A run on the model another mod moved it to is no substitution for the one this mod sent.
      if (!e.workflow && PLAIN_ID.test(e.tool_use_id) && movedOn(next.trace, model || e.model)) {
        await mark($, `${e.tool_use_id}.moved`)
      }
      refresh($, opts, live)
      return started
    } finally {
      efforts.splice(efforts.indexOf(mine), 1)
      if (!efforts.length) inFlight.delete(e.prompt)
    }
  }).catch(($, e, next) => next(e))

  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      live.turned = live.working = true
      // session.start doesn't fire after /clear, so the new session's first turn sends the layers.
      const id = await sessionId($)
      if (id !== undefined && id !== sentFor) {
        sentFor = id
        started(await sendLayers($, opts, reads))
        refresh($, opts, live)
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
    if (e.agentId === undefined) sessionEffort = current
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
    if (e.agentId === undefined) live.working = false
    const done = await next(e)
    if (e.agentId === undefined) {
      if (live.interactive) {
        try {
          live.setup = { model: await $.session.model(), effort: sessionEffort ?? null }
        } catch {}
        refresh($, opts, live)
      }
      return done
    }
    const type = agentType.get(e.agentId)
    const listed = await agentUsage($, e.agentId, e.usage)
    const figures = listed?.figures ?? (e.usage ? { ...(type ? { subagent_type: type } : {}), usage: e.usage } : undefined)
    const call = agentCall.get(e.agentId)
    const ran = call && listed?.ran ? { tool_use_id: call, ran: listed.ran } : undefined
    if (figures || ran) heard(await ask($, opts, 'mod-usage', { agent_id: e.agentId, ...figures, ...ran }))
    return done
  }).catch(($, e, next) => next(e))
}
