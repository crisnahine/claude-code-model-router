import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import type { On, PluginOptions } from 'claude-code'
import { register } from '../../plugins/model-router/hooks/router.ts'
import payloads from '../fixtures/mod-payloads.json' with { type: 'json' }

type Hook = (...args: any[]) => any
type Reply = { exitCode?: number; stdout: string }
type Call = { command: string; argv: readonly string[]; stdin: any; env: Record<string, string> }

const same = <T>(e: T): T => e
// What the runtime answers to the commands that set nothing (scripts/modelrouter/mod.py OK).
const RAN: Reply = { stdout: JSON.stringify({ ok: true }) }

function load(replies: Record<string, Reply> = {}, options: PluginOptions = {}, settings: Record<string, object | Error> = {}) {
  const hooks: Record<string, Hook> = {}
  const matchers: Record<string, unknown> = {}
  const catches: Record<string, Hook> = {}
  const calls: Call[] = []
  const writes: [string, string][] = []
  // What $.agent.list() and $.session.usage() answer; a test sets them.
  const engine: { agents: object[]; rateLimits: object[] } = { agents: [], rateLimits: [] }
  const on = (event: string, ...rest: unknown[]) => {
    hooks[event] = rest.pop() as Hook
    if (rest.length) matchers[event] = rest[0]
    return { catch: (handler: Hook) => { catches[event] = handler } }
  }
  const $ = {
    plugin: { root: '/plugin' },
    clock: {
      after: (ms: number, fn: () => void) => {
        const id = setTimeout(fn, ms)
        return { cancel: () => clearTimeout(id) }
      },
    },
    settings: {
      read: async ({ source }: { source: string }) => {
        const v = settings[source]
        if (v instanceof Error) throw v
        return v ?? {}
      },
    },
    fs: {
      write: async (path: string, text: string) => { writes.push([path, text]) },
    },
    agent: { list: async () => engine.agents },
    session: { usage: async () => ({ startedAt: 0, context: {}, rateLimits: engine.rateLimits }) },
    process: {
      hookEnv: async () => ({ CLAUDE_CODE_SESSION_ID: 's1', CLAUDE_PLUGIN_DATA: '/tmp/x' }),
      run: async (argv: readonly string[], init: { stdin: string; env: Record<string, string> }) => {
        const command = argv[2] ?? ''
        calls.push({ command, argv, stdin: JSON.parse(init.stdin), env: init.env })
        const reply = replies[command] ?? (command === 'mod-spawn' ? { stdout: '' } : RAN)
        return { exitCode: reply.exitCode ?? 0, stdout: reply.stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false }
      },
    },
  }
  register(on as unknown as On, options)
  return {
    hooks, matchers, catches, calls, writes, $, engine,
    toolCall: (e: object) => hooks['tool.call']!($, e, same),
    sessionStart: (e: object) => hooks['session.start']!($, e, same),
    spawn: (e: object, next: Hook = (x: object) => ({ ...x, agentId: 'a1' })) => hooks['agent.spawn']!($, e, next),
    start: (e: object) => hooks['turn.start']!($, e, same),
    complete: (e: object) => hooks['turn.complete']!($, e, same),
    async step(e: object, result: object = { done: true }): Promise<any> {
      let sent: any
      async function* next(input: any) {
        sent = input
        yield { kind: 'text', text: 'hi' }
        return result
      }
      const gen = hooks['turn.step']!($, e, next)
      let r = await gen.next()
      while (!r.done) r = await gen.next()
      assert.deepEqual(r.value, result)
      return sent
    },
  }
}

function spawnEvent(extra: object = {}) {
  return {
    tool_use_id: 'tu1', prompt: 'list the files under src', description: 'list files', subagentType: 'general-purpose',
    parentModel: 'claude-opus-5-5', background: false, fork: false, ...extra,
  }
}

const stepEvent = (extra: object) => ({ turnId: 't1', model: 'claude-opus-5-5', index: 0, messageCount: 1, ...extra })
const completeEvent = (extra: object) => ({ turnId: 't1', answer: '', durationMs: 1, isAborted: false, reason: 'answer', ...extra })
const PICK = { stdout: JSON.stringify({ model: 'haiku', effort: 'low', reason: 'lookup subtask' }) }
const TURN = { model: 'claude-haiku-5-5', input_tokens: 10, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }

test('a spawn passes the pick model to next', async () => {
  const mod = load({ 'mod-spawn': PICK })
  let seen: any
  await mod.spawn(spawnEvent(), (e: any) => { seen = e; return { model: e.model, agentId: 'a1' } })
  assert.equal(seen.model, 'haiku')
  assert.deepEqual(mod.calls.map(c => c.argv), [['sh', '/plugin/scripts/model-router.sh', 'mod-spawn']])
})

test('mod-start and mod-spawn carry the allowlist and effort cap keys of each settings source, and only the caps of modelSettings', async () => {
  const policy = { availableModels: ['opus'], maxEffortLevel: 'high', modelSettings: { 'claude-opus-5-5': { maxEffortLevel: 'max' } } }
  const sent = { ...policy, modelSettings: { 'claude-opus-5-5': { maxEffortLevel: 'max', alwaysThinking: true }, 'claude-haiku-5-5': { fastMode: true } } }
  const mod = load({ 'mod-spawn': PICK }, {}, { policy: { ...sent, env: { TOKEN: 'x' }, permissions: {} } })
  await mod.sessionStart({ source: 'startup' })
  await mod.spawn(spawnEvent())
  assert.equal(mod.calls.length, 2)
  for (const call of mod.calls) {
    assert.deepEqual(Object.keys(call.stdin.settings).sort(), ['flag', 'local', 'policy', 'project', 'user'])
    assert.deepEqual(call.stdin.settings.policy, policy)
    assert.equal(JSON.stringify(call.stdin).includes('TOKEN'), false)
  }
})

test('a settings read that fails leaves that source out and sends the others', async () => {
  const settings: Record<string, object | Error> = { policy: new Error('denied'), user: { availableModels: ['haiku'] } }
  const mod = load({ 'mod-spawn': PICK }, {}, settings)
  await mod.sessionStart({ source: 'startup' })
  await mod.spawn(spawnEvent())
  assert.equal(mod.calls.length, 2)
  for (const call of mod.calls) {
    assert.deepEqual(Object.keys(call.stdin.settings).sort(), ['flag', 'local', 'project', 'user'])
    assert.deepEqual(call.stdin.settings.user, { availableModels: ['haiku'] })
  }
  settings.user = new Error('gone') // one that answered before is left out too
  await mod.spawn(spawnEvent({ tool_use_id: 'tu2' }))
  assert.deepEqual(Object.keys(mod.calls[2]!.stdin.settings).sort(), ['flag', 'local', 'project'])
})

test('settings reads that all fail send no settings', async () => {
  const failing = Object.fromEntries(['policy', 'user', 'project', 'local', 'flag'].map(s => [s, new Error('no')]))
  const mod = load({ 'mod-spawn': PICK }, {}, failing)
  await mod.sessionStart({ source: 'startup' })
  await mod.spawn(spawnEvent())
  for (const call of mod.calls) assert.equal('settings' in call.stdin, false)
})

test('a settings read that hangs is left out after two seconds', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const mod = load({}, {}, { user: { availableModels: ['haiku'] } })
    mod.$.settings.read = async ({ source }: { source: string }) =>
      source === 'policy' ? new Promise(() => {}) : source === 'user' ? { availableModels: ['haiku'] } : {}
    const started = mod.sessionStart({ source: 'startup' })
    await new Promise(resolve => setImmediate(resolve))
    mock.timers.tick(2000)
    await started
    assert.deepEqual(Object.keys(mod.calls[0]!.stdin.settings).sort(), ['flag', 'local', 'project', 'user'])
  } finally {
    mock.timers.reset()
  }
})

test("a source still out after two seconds isn't waited on again, and its late answer goes with a later command", async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    const mod = load({ 'mod-spawn': PICK })
    const answers: ((layer: object) => void)[] = []
    mod.$.settings.read = async ({ source }: { source: string }) =>
      source === 'policy' ? new Promise(resolve => { answers.push(resolve) }) : {}
    const started = mod.sessionStart({ source: 'startup' })
    await new Promise(resolve => setImmediate(resolve))
    mock.timers.tick(2000)
    await started
    const unwaited = (e: object) => Promise.race([mod.spawn(e), new Promise(resolve => setImmediate(() => resolve('waited')))])
    assert.notEqual(await unwaited(spawnEvent({ tool_use_id: 'tu1' })), 'waited')
    assert.equal(answers.length, 1) // not read again while its first read is out
    answers[0]!({ deniedModels: ['opus'] })
    await new Promise(resolve => setImmediate(resolve))
    assert.notEqual(await unwaited(spawnEvent({ tool_use_id: 'tu2' })), 'waited')
    assert.deepEqual(mod.calls.map(c => c.stdin.settings.policy), [undefined, undefined, { deniedModels: ['opus'] }])
    assert.equal(answers.length, 2) // read again, without waiting, once it answered
  } finally {
    mock.timers.reset()
  }
})

test('a clock that throws or hands back no cancel leaves the settings out and runs next once', async () => {
  const clocks = [
    () => { throw new Error('no clock') },
    () => ({}),
  ]
  for (const after of clocks) {
    const mod = load({}, {}, { user: { availableModels: ['haiku'] } })
    ;(mod.$.clock as { after: unknown }).after = after
    let runs = 0
    const next = (x: object) => { runs++; return x }
    const begin = { source: 'startup' }
    const result = await Promise.resolve(mod.hooks['session.start']!(mod.$, begin, next)).catch(() => mod.catches['session.start']!(mod.$, begin, next))
    assert.equal(result, begin)
    assert.equal(runs, 1)
    assert.equal('settings' in mod.calls[0]!.stdin, false)
  }
})

test('forks and resumed runs pass through with no CLI call', async () => {
  const mod = load({ 'mod-spawn': PICK })
  for (const extra of [{ fork: true }, { resume: { agentId: 'a9' } }]) {
    const e = spawnEvent(extra)
    let seen: unknown
    await mod.spawn(e, (x: unknown) => { seen = x; return { model: 'm' } })
    assert.equal(seen, e)
  }
  assert.equal(mod.calls.length, 0)
})

test("a spawn never sends the subagent's own cwd, so the project stays the session's", async () => {
  const mod = load({ 'mod-spawn': PICK })
  await mod.spawn(spawnEvent({ cwd: '/somewhere/else' }))
  assert.equal('cwd' in mod.calls[0]!.stdin, false)
})

test('a spawn sends its Agent call id, and a workflow agent sends none', async () => {
  const mod = load({ 'mod-spawn': PICK })
  await mod.spawn(spawnEvent())
  await mod.spawn(spawnEvent({ workflow: { runId: 'w1' } }))
  await mod.spawn(spawnEvent({ tool_use_id: '' }))
  assert.equal(mod.calls[0]!.stdin.tool_use_id, 'tu1')
  assert.equal('tool_use_id' in mod.calls[1]!.stdin, false)
  assert.equal('tool_use_id' in mod.calls[2]!.stdin, false)
})

test('a routed agent steps at the pick effort', async () => {
  const mod = load({ 'mod-spawn': PICK })
  await mod.spawn(spawnEvent())
  assert.equal((await mod.step(stepEvent({ agentId: 'a1', effort: 'high' }))).effort, 'low')
})

test("the main loop's effort reaches mod-spawn, and the agent steps at the effort the CLI decided", async () => {
  const mod = load({ 'mod-spawn': { stdout: JSON.stringify({ model: 'opus', effort: 'xhigh', reason: 'complex subtask' }) } })
  await mod.step(stepEvent({ effort: 'xhigh' })) // the main loop, as under --effort ultracode
  await mod.spawn(spawnEvent())
  assert.equal(mod.calls.find(c => c.command === 'mod-spawn')!.stdin.session_effort, 'xhigh')
  assert.equal((await mod.step(stepEvent({ turnId: 'ta1', agentId: 'a1', effort: 'medium' }))).effort, 'xhigh')
  const fresh = load({ 'mod-spawn': PICK })
  await fresh.spawn(spawnEvent())
  assert.equal(fresh.calls.find(c => c.command === 'mod-spawn')!.stdin.session_effort, undefined)
})

test('an agent stepping before its spawn resolves still gets the pick effort', async () => {
  const mod = load({ 'mod-spawn': PICK })
  let sent: any
  await mod.spawn(spawnEvent(), async (e: any) => {
    await mod.start({ turnId: 't1', agentId: 'a1', text: e.prompt })
    sent = await mod.step(stepEvent({ agentId: 'a1', effort: 'high' }))
    return { model: e.model }
  })
  assert.equal(sent.effort, 'low')
})

test("an agent never steps at another spawn's effort for the same brief", async () => {
  const LEFT = { stdout: JSON.stringify({ model: 'sonnet', reason: 'the call named its own effort' }) }
  // Each order of a picked spawn (a1, low) and an unpicked one (a2) with the same prompt, agents started late.
  for (const order of [[PICK, LEFT], [LEFT, PICK]]) {
    const replies: Record<string, Reply> = {}
    const mod = load(replies)
    const sent: Record<string, any> = {}
    for (const [i, reply] of order.entries()) {
      replies['mod-spawn'] = reply
      const id = reply === PICK ? 'a1' : 'a2'
      await mod.spawn(spawnEvent({ tool_use_id: `tu${i}` }), async (e: any) => {
        await mod.start({ turnId: `t${id}`, agentId: id, text: e.prompt })
        sent[id] = await mod.step(stepEvent({ turnId: `t${id}`, agentId: id, effort: 'max' }))
        return { model: e.model, agentId: id }
      })
    }
    assert.equal(sent.a2.effort, 'max', JSON.stringify(order))
    assert.equal((await mod.step(stepEvent({ turnId: 'ta1', agentId: 'a1', effort: 'max', index: 1 }))).effort, 'low')
  }
})

test("a redo's first request gets the redo's effort, not the run it replaces", async () => {
  const replies: Record<string, Reply> = { 'mod-spawn': PICK }
  const mod = load(replies)
  await mod.spawn(spawnEvent())
  replies['mod-spawn'] = { stdout: JSON.stringify({ model: 'sonnet', effort: 'high', reason: 'redo, one level up' }) }
  let sent: any
  await mod.spawn(spawnEvent({ tool_use_id: 'tu2' }), async (e: any) => {
    await mod.start({ turnId: 't2', agentId: 'a2', text: e.prompt })
    sent = await mod.step(stepEvent({ turnId: 't2', agentId: 'a2', effort: 'medium' }))
    return { model: e.model, agentId: 'a2' }
  })
  assert.equal(sent.effort, 'high')
})

test("two spawns of one brief in flight together never trade efforts, whichever pick lands first", async () => {
  const mod = load()
  // Each mod-spawn call waits until the test answers it, so the two picks can land out of order.
  const answer: Record<string, (stdout: string) => void> = {}
  const run = mod.$.process.run
  mod.$.process.run = (argv: readonly string[], init: { stdin: string; env: Record<string, string> }) =>
    argv[2] !== 'mod-spawn' ? run(argv, init) : new Promise((resolve) => {
      answer[JSON.parse(init.stdin).tool_use_id] = (stdout) =>
        resolve({ exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false })
    })
  const sent: Record<string, any> = {}
  const a = mod.spawn(spawnEvent({ tool_use_id: 'tuA' }), async (e: any) => ({ model: e.model, agentId: 'a1' }))
  const b = mod.spawn(spawnEvent({ tool_use_id: 'tuB' }), async (e: any) => {
    await a // b's agent starts only after a's spawn has its pick
    await mod.start({ turnId: 'ta2', agentId: 'a2', text: e.prompt })
    sent.a2 = await mod.step(stepEvent({ turnId: 'ta2', agentId: 'a2', effort: 'max' }))
    return { model: e.model, agentId: 'a2' }
  })
  while (!answer.tuA || !answer.tuB) await new Promise((r) => setImmediate(r))
  answer.tuB(JSON.stringify({ model: 'sonnet', reason: 'the call named its own effort' }))
  await new Promise((r) => setImmediate(r))
  answer.tuA(PICK.stdout)
  await Promise.all([a, b])
  assert.equal(sent.a2.effort, 'max')
  assert.equal((await mod.step(stepEvent({ turnId: 'ta1', agentId: 'a1', effort: 'max' }))).effort, 'low')
})

test('an effort on the Agent call reaches mod-spawn', async () => {
  const mod = load()
  await mod.sessionStart({ source: 'startup' })
  const call = { tool: 'Agent', tool_use_id: 'tu1', effort: 'high', prompt: 'p' }
  assert.equal(await mod.toolCall(call), call)
  assert.deepEqual(mod.matchers['tool.call'], { tool: 'Agent' })
  await mod.spawn(spawnEvent())
  assert.equal(mod.calls.find(c => c.command === 'mod-spawn')!.stdin.tool_input.effort, 'high')
})

test("a main-loop turn's start asks the runtime nothing, and its effort is left alone", async () => {
  const mod = load({}, {}, { policy: { deniedModels: ['opus'] } })
  const start = { turnId: 't1', text: 'fix the auth migration' }
  assert.equal(await mod.start(start), start)
  assert.equal(mod.calls.length, 0)
  assert.equal((await mod.step(stepEvent({ effort: 'medium' }))).effort, 'medium')
})

const agentCall = (id: string) => ({ tool: 'Agent', tool_use_id: id, prompt: 'p' })

test('an Agent call is marked for the fallback only while the runtime answers', async () => {
  const replies: Record<string, Reply> = { 'mod-spawn': PICK }
  const mod = load(replies)
  await mod.toolCall(agentCall('toolu_0')) // no round trip yet
  await mod.sessionStart({ source: 'startup' })
  let marked: unknown
  await mod.hooks['tool.call']!(mod.$, agentCall('toolu_1'), (e: object) => { marked = [...mod.writes]; return e })
  assert.deepEqual(marked, [['/tmp/x/mod-calls/toolu_1', '']]) // written before the settings hooks run
  replies['mod-spawn'] = { exitCode: 1, stdout: '' }
  await mod.spawn(spawnEvent())
  await mod.toolCall(agentCall('toolu_2'))
  const run = mod.$.process.run
  mod.$.process.run = async () => { throw new Error('refused by another mod') }
  await mod.complete(completeEvent({ agentId: 'a1', usage: TURN }))
  await mod.toolCall(agentCall('toolu_3'))
  mod.$.process.run = run
  await mod.complete(completeEvent({ agentId: 'a1', usage: TURN }))
  await mod.toolCall(agentCall('toolu_4'))
  assert.deepEqual(mod.writes.map(([path]) => path), ['/tmp/x/mod-calls/toolu_1', '/tmp/x/mod-calls/toolu_4'])
})

test('with no Python the launcher answers nothing, so no call is marked', async () => {
  const none = { stdout: '' }
  const mod = load({ 'mod-start': none, 'mod-usage': none, 'mod-spawn': none })
  await mod.sessionStart({ source: 'startup' })
  await mod.toolCall(agentCall('toolu_1'))
  await mod.complete(completeEvent({ agentId: 'a1', usage: TURN }))
  await mod.spawn(spawnEvent({ tool_use_id: 'tu9' }))
  await mod.toolCall(agentCall('toolu_2'))
  assert.deepEqual(mod.writes, [])
})

test("a call the mod left unmarked is the fallback's alone, so its spawn asks the runtime nothing", async () => {
  const mod = load({ 'mod-spawn': PICK })
  await mod.toolCall(agentCall('tu1')) // before any round trip: the fallback routes it
  const e = spawnEvent({ tool_use_id: 'tu1' })
  let seen: unknown
  await mod.spawn(e, (x: unknown) => { seen = x; return { model: 'm', agentId: 'a1' } })
  assert.equal(seen, e)
  assert.equal(mod.calls.length, 0)
  await mod.sessionStart({ source: 'startup' })
  await mod.toolCall(agentCall('tu2'))
  await mod.spawn(spawnEvent({ tool_use_id: 'tu2' }))
  await mod.spawn(spawnEvent({ tool_use_id: 'tu3' })) // a spawn no Agent call this mod heard started
  assert.deepEqual(mod.calls.map(c => [c.command, c.stdin.tool_use_id]), [['mod-start', undefined], ['mod-spawn', 'tu2'], ['mod-spawn', 'tu3']])
})

test('only a plain call id is marked, and a failing write still passes the call on once', async () => {
  const mod = load()
  await mod.sessionStart({ source: 'startup' })
  for (const id of ['../x', 'a/b', '', 'x'.repeat(200)]) await mod.toolCall(agentCall(id))
  await mod.toolCall({ tool: 'Agent', prompt: 'p' })
  assert.deepEqual(mod.writes, [])
  mod.$.fs.write = async () => { throw new Error('read-only') }
  const e = agentCall('toolu_5')
  let runs = 0
  const next = (x: unknown) => { runs++; return x }
  const result = await Promise.resolve(mod.hooks['tool.call']!(mod.$, e, next)).catch(() => mod.catches['tool.call']!(mod.$, e, next))
  assert.equal(result, e)
  assert.equal(runs, 1)
  const asked = mod.calls.length
  await mod.spawn(spawnEvent({ tool_use_id: 'toolu_5' }))
  await mod.spawn(spawnEvent({ tool_use_id: '../x' }))
  assert.equal(mod.calls.length, asked) // both unmarked, so the fallback routed them
})

test("a workflow agent's effort is raised to the pick, never lowered", async () => {
  const mod = load({ 'mod-spawn': { stdout: JSON.stringify({ model: 'sonnet', effort: 'high', reason: 'standard subtask' }) } })
  await mod.spawn(spawnEvent({ workflow: { runId: 'w1' } }), (x: object) => ({ ...x, agentId: 'w1' }))
  assert.equal((await mod.step(stepEvent({ turnId: 'tw', agentId: 'w1', effort: 'medium' }))).effort, 'high')
  assert.equal((await mod.step(stepEvent({ turnId: 'tw', agentId: 'w1', effort: 'xhigh', index: 1 }))).effort, 'xhigh')
  assert.equal((await mod.step(stepEvent({ turnId: 'tw', agentId: 'w1', effort: 'auto', index: 2 }))).effort, 'auto')
})

test('a workflow agent stepping before its spawn resolves is only raised too', async () => {
  const mod = load({ 'mod-spawn': PICK })
  let sent: any
  await mod.spawn(spawnEvent({ workflow: { runId: 'w1' } }), async (e: any) => {
    await mod.start({ turnId: 'tw', agentId: 'w1', text: e.prompt })
    sent = await mod.step(stepEvent({ turnId: 'tw', agentId: 'w1', effort: 'max' }))
    return { model: e.model, agentId: 'w1' }
  })
  assert.equal(sent.effort, 'max')
})

test("a subagent's turn end sends Claude Code's own figures for it, and the main loop's sends nothing", async () => {
  const mod = load()
  const [opus, haiku] = payloads['mod-usage'].models
  mod.engine.agents = [{ id: 'a1', type: 'Explore', usage: [opus, haiku], cost: { usd: 6 } }, { id: 'a2', type: 'x' }]
  await mod.complete(completeEvent({ usage: TURN }))
  assert.equal(mod.calls.length, 0)
  await mod.complete(completeEvent({ agentId: 'a1', usage: TURN }))
  mod.engine.rateLimits = [{ kind: 'spend_limit', percentUsed: 5 }]
  await mod.complete(completeEvent({ agentId: 'a1', usage: TURN }))
  mod.engine.rateLimits = [{ kind: 'five_hour', percentUsed: 5 }]
  await mod.complete(completeEvent({ agentId: 'a1', usage: TURN }))
  await mod.complete(completeEvent({ agentId: 'a2' })) // listed, with nothing counted yet
  const sent = { session_id: 's1', agent_id: 'a1', subagent_type: 'Explore', models: [opus, haiku], cost: { usd: 6 } }
  assert.deepEqual(mod.calls.map(c => c.stdin), [sent, sent, { ...sent, subscription: true }])
})

test('plugin options reach the CLI env', async () => {
  const mod = load({}, { max_tier: 'sonnet', verify_gate: false, tiers: ['haiku', 'sonnet'], budget: 3 })
  await mod.sessionStart({ source: 'startup' })
  const env = mod.calls[0]!.env
  assert.equal(env.CLAUDE_PLUGIN_OPTION_MAX_TIER, 'sonnet')
  assert.equal(env.CLAUDE_PLUGIN_OPTION_VERIFY_GATE, 'false')
  assert.equal(env.CLAUDE_PLUGIN_OPTION_TIERS, 'haiku,sonnet')
  assert.equal(env.CLAUDE_PLUGIN_OPTION_BUDGET, '3')
  assert.equal(env.CLAUDE_CODE_SESSION_ID, 's1')
})

test('a failing or garbled CLI leaves every event unchanged', async () => {
  const bad = [{ exitCode: 1, stdout: PICK.stdout }, { stdout: 'not json' }, { stdout: '' }]
  for (const reply of bad) {
    const mod = load({ 'mod-spawn': reply, 'mod-start': reply, 'mod-usage': reply })
    const begin = { source: 'startup' }
    assert.equal(await mod.sessionStart(begin), begin)
    const e = spawnEvent()
    let seen: unknown
    await mod.spawn(e, (x: unknown) => { seen = x; return { agentId: 'a1' } })
    assert.equal(seen, e)
    const start = { turnId: 't1', text: 'fix the auth migration' }
    assert.equal(await mod.start(start), start)
    for (const step of [stepEvent({ agentId: 'a1', effort: 'high' }), stepEvent({ effort: 'medium' })]) {
      assert.equal(await mod.step(step), step)
    }
    const done = completeEvent({ agentId: 'a1', usage: TURN })
    assert.equal(await mod.complete(done), done)
    assert.equal(mod.calls.length, 3)
  }
})

test('a CLI that throws after next(e) ran does not run next again', async () => {
  const mod = load()
  mod.$.process.run = async () => { throw new Error('spawn failed') }
  const events = [['session.start', { source: 'startup' }], ['turn.complete', completeEvent({ agentId: 'a1', usage: TURN })]] as const
  for (const [event, e] of events) {
    let runs = 0
    const next = (x: unknown) => { runs++; return x }
    // The engine runs a hook's .catch when the hook rejects.
    const result = await Promise.resolve(mod.hooks[event]!(mod.$, e, next)).catch(() => mod.catches[event]!(mod.$, e, next))
    assert.equal(result, e)
    assert.equal(runs, 1, event)
  }
})

test('every hook falls back to next(e) when it throws', async () => {
  const mod = load()
  const events = ['agent.spawn', 'session.start', 'tool.call', 'turn.complete', 'turn.start', 'turn.step']
  assert.deepEqual(Object.keys(mod.hooks).sort(), events)
  assert.deepEqual(Object.keys(mod.catches).sort(), events)
  for (const [event, handler] of Object.entries(mod.catches)) {
    const e = { any: 'event' }
    let seen: unknown
    if (event === 'turn.step') {
      const stream = handler(mod.$, e, async function* (x: unknown) { seen = x; yield { kind: 'text' }; return 'result' })
      let step = await stream.next()
      while (!step.done) step = await stream.next()
      assert.equal(step.value, 'result')
    } else {
      assert.equal(await handler(mod.$, e, (x: unknown) => { seen = x; return 'result' }), 'result')
    }
    assert.equal(seen, e)
  }
})

test("a workflow agent, which Claude Code doesn't list, sends its turn's tokens and the type it was spawned as", async () => {
  const mod = load({ 'mod-spawn': PICK })
  mod.engine.agents = [{ id: 's1', type: 'general-purpose', usage: [TURN], cost: { usd: 1 } }]
  await mod.spawn(spawnEvent({ subagentType: 'workflow-subagent', workflow: { runId: 'wf_1', agentIndex: 1 } }), (x: object) => ({ ...x, agentId: 'w1' }))
  await mod.spawn(spawnEvent({ tool_use_id: 'tu2' }), (x: object) => ({ ...x, agentId: 's1' }))
  await mod.complete(completeEvent({ turnId: 'tw', agentId: 'w1', usage: TURN }))
  await mod.complete(completeEvent({ turnId: 'ts', agentId: 's1', usage: TURN }))
  const sent = mod.calls.filter(c => c.command === 'mod-usage').map(c => c.stdin)
  assert.deepEqual(sent, [
    { session_id: 's1', agent_id: 'w1', subagent_type: 'workflow-subagent', usage: TURN },
    { session_id: 's1', agent_id: 's1', subagent_type: 'general-purpose', models: [TURN], cost: { usd: 1 } },
  ])
})

test('each payload the mod sends matches its fixture', async () => {
  const mod = load({}, {}, payloads['mod-start'].settings)
  const spawn = payloads['mod-spawn']
  const usage = payloads['mod-usage']
  await mod.sessionStart({ source: 'startup' })
  await mod.toolCall({ tool: 'Agent', tool_use_id: 'tu1', effort: spawn.tool_input.effort })
  await mod.spawn(spawnEvent({
    parentAgentId: spawn.parent_agent_id, parentModel: spawn.parent_model, model: spawn.tool_input.model,
    subagentType: spawn.tool_input.subagent_type, prompt: spawn.tool_input.prompt, description: spawn.tool_input.description,
  }))
  mod.engine.agents = [{ id: usage.agent_id, type: usage.subagent_type, usage: usage.models, cost: usage.cost }]
  mod.engine.rateLimits = [{ kind: 'seven_day', percentUsed: 1 }]
  await mod.complete(completeEvent({ turnId: 't2', agentId: usage.agent_id, usage: usage.models[0] }))
  assert.deepEqual(Object.fromEntries(mod.calls.map(c => [c.command, c.stdin])), payloads)
})
