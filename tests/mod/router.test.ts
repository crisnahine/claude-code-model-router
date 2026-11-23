import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import type { On, PluginOptions } from 'claude-code'
import { register } from '../../plugins/model-router/hooks/router.ts'
import payloads from '../fixtures/mod-payloads.json' with { type: 'json' }

type Hook = (...args: any[]) => any
type Reply = { exitCode?: number; stdout: string }
type Call = { command: string; argv: readonly string[]; stdin: any; env: Record<string, string> }

const same = <T>(e: T): T => e

function load(replies: Record<string, Reply> = {}, options: PluginOptions = {}, settings: Record<string, object | Error> = {}) {
  const hooks: Record<string, Hook> = {}
  const matchers: Record<string, unknown> = {}
  const catches: Record<string, Hook> = {}
  const calls: Call[] = []
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
    session: {
      model: async () => 'claude-opus-5-5',
      authorize: async (): Promise<{ handle: string; kind: string } | null> => ({ handle: 'h1', kind: 'bearer' }),
      surfaces: async (): Promise<readonly string[]> => ['terminal'],
    },
    process: {
      hookEnv: async () => ({ CLAUDE_CODE_SESSION_ID: 's1', CLAUDE_PLUGIN_DATA: '/tmp/x' }),
      run: async (argv: readonly string[], init: { stdin: string; env: Record<string, string> }) => {
        const command = argv[2] ?? ''
        calls.push({ command, argv, stdin: JSON.parse(init.stdin), env: init.env })
        const reply = replies[command] ?? { stdout: '' }
        return { exitCode: reply.exitCode ?? 0, stdout: reply.stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false }
      },
    },
  }
  register(on as unknown as On, options)
  return {
    hooks, matchers, catches, calls, $,
    toolCall: (e: object) => hooks['tool.call']!($, e, same),
    sessionStart: (e: object) => hooks['session.start']!($, e, same),
    // The engine starts the turn inside next(e), so the turn's own start runs before submit returns.
    submit: (e: object, turn?: object, entered: (x: object) => object = same) =>
      hooks['prompt.submit']!($, e, async (x: object) => { if (turn) await hooks['turn.start']!($, turn, same); return entered(x) }),
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

test('a spawn passes the pick model to next', async () => {
  const mod = load({ 'mod-spawn': PICK })
  let seen: any
  await mod.spawn(spawnEvent(), (e: any) => { seen = e; return { model: e.model, agentId: 'a1' } })
  assert.equal(seen.model, 'haiku')
  assert.deepEqual(mod.calls.map(c => c.argv), [['sh', '/plugin/scripts/model-router.sh', 'mod-spawn']])
})

test('mod-start and mod-spawn carry the four allowlist keys of each settings source', async () => {
  const mod = load({ 'mod-spawn': PICK }, {}, { policy: { availableModels: ['opus'], env: { TOKEN: 'x' }, permissions: {} } })
  await mod.sessionStart({ source: 'startup' })
  await mod.spawn(spawnEvent())
  assert.equal(mod.calls.length, 2)
  for (const call of mod.calls) {
    assert.deepEqual(Object.keys(call.stdin.settings).sort(), ['flag', 'local', 'policy', 'project', 'user'])
    assert.deepEqual(call.stdin.settings.policy, { availableModels: ['opus'] })
    assert.equal(JSON.stringify(call.stdin).includes('TOKEN'), false)
  }
})

test('a settings read that fails leaves that source out and sends the others', async () => {
  const mod = load({ 'mod-spawn': PICK }, {}, { policy: new Error('denied'), user: { availableModels: ['haiku'] } })
  await mod.sessionStart({ source: 'startup' })
  await mod.spawn(spawnEvent())
  assert.equal(mod.calls.length, 2)
  for (const call of mod.calls) {
    assert.deepEqual(Object.keys(call.stdin.settings).sort(), ['flag', 'local', 'project', 'user'])
    assert.deepEqual(call.stdin.settings.user, { availableModels: ['haiku'] })
  }
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

test('a main-loop turn carries the settings layers too', async () => {
  const mod = load({}, {}, { policy: { deniedModels: ['opus'] } })
  await mod.start({ turnId: 't1', text: 'list the files under src' })
  assert.deepEqual(mod.calls[0]!.stdin.settings.policy, { deniedModels: ['opus'] })
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

test('a routed agent steps at the pick effort', async () => {
  const mod = load({ 'mod-spawn': PICK })
  await mod.spawn(spawnEvent())
  assert.equal((await mod.step(stepEvent({ agentId: 'a1', effort: 'high' }))).effort, 'low')
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

test('an effort on the Agent call reaches mod-spawn', async () => {
  const mod = load()
  const call = { tool: 'Agent', tool_use_id: 'tu1', effort: 'high', prompt: 'p' }
  assert.equal(await mod.toolCall(call), call)
  assert.deepEqual(mod.matchers['tool.call'], { tool: 'Agent' })
  await mod.spawn(spawnEvent())
  assert.equal(mod.calls[0]!.stdin.tool_input.effort, 'high')
})

test('the main loop effort only raises and ends with the turn', async () => {
  const mod = load({ 'mod-turn': { stdout: JSON.stringify({ level: 'complex', effort: 'high' }) } })
  await mod.start({ turnId: 't1', text: 'fix the auth migration' })
  assert.equal(mod.calls[0]!.stdin.text, 'fix the auth migration')
  assert.equal((await mod.step(stepEvent({ effort: 'medium' }))).effort, 'high')
  assert.equal((await mod.step(stepEvent({ effort: 'max' }))).effort, 'max')
  await mod.complete(completeEvent({}))
  assert.equal((await mod.step(stepEvent({ effort: 'medium' }))).effort, 'medium')
})

test('the main loop says whether the session holds a first-party credential', async () => {
  const mod = load()
  await mod.start({ turnId: 't1', text: 'fix the auth migration' })
  assert.equal(mod.calls[0]!.stdin.first_party, true)
  mod.$.session.authorize = async () => null  // a gateway or a third-party provider
  await mod.start({ turnId: 't2', text: 'fix the auth migration' })
  assert.equal(mod.calls[1]!.stdin.first_party, false)
  mod.$.session.authorize = async () => { throw new Error('denied') }
  await mod.start({ turnId: 't3', text: 'fix the auth migration' })
  assert.equal('first_party' in mod.calls[2]!.stdin, false)
})

test('a missing credential says nothing in the desktop app, which never lends its sign-in', async () => {
  const mod = load()
  mod.$.session.authorize = async () => null
  mod.$.session.surfaces = async () => ['desktop']
  await mod.start({ turnId: 't1', text: 'fix the auth migration' })
  assert.equal('first_party' in mod.calls[0]!.stdin, false)
})

test('the credential is not asked for when main_effort is off', async () => {
  const mod = load({}, { main_effort: false })
  let asked = false
  mod.$.session.authorize = async () => { asked = true; return null }
  await mod.start({ turnId: 't1', text: 'fix the auth migration' })
  assert.equal(asked, false)
  assert.equal('first_party' in mod.calls[0]!.stdin, false)
})

test('the main loop passes where its prompt came from, once', async () => {
  const mod = load()
  await mod.submit({ text: 'done', wait: false, origin: { kind: 'task-notification' } }, { turnId: 't1', text: 'done' })
  assert.equal(mod.calls[0]!.stdin.origin, 'task-notification')
  await mod.submit({ text: 'fix it', wait: false, origin: { kind: 'composer' } }, { turnId: 't2', text: 'fix it' })
  assert.equal(mod.calls[1]!.stdin.origin, 'composer')
  await mod.start({ turnId: 't3', text: 'Another Claude session sent a message:' })  // a drain with no submit
  assert.equal('origin' in mod.calls[2]!.stdin, false)
  await mod.submit({ text: 'into the running turn', wait: false, turnId: 't3', origin: { kind: 'peer' } })
  await mod.start({ turnId: 't4', text: 'next' })
  assert.equal('origin' in mod.calls[3]!.stdin, false)
  await mod.submit({ text: 'no', wait: false, origin: { kind: 'peer' } }, undefined, () => ({ drop: 'refused' }))
  await mod.start({ turnId: 't5', text: 'next' })
  assert.equal('origin' in mod.calls[4]!.stdin, false)
})

test('a typed skill sends its command and body with the turn', async () => {
  const mod = load()
  const skill = (e: object) => mod.hooks['skill.prompt']!(mod.$, e, async (x: { text: string }) => ({ text: x.text }))
  const line = '<command-message>hard</command-message>'
  await mod.submit({ text: '/hard src', command: { name: 'hard', args: 'src' }, wait: false, origin: { kind: 'composer' } })
  await skill({ skill: 'hard', text: 'Investigate the race.', source: 'projectSettings', frontmatter: {} })
  await mod.start({ turnId: 't1', text: line })
  assert.deepEqual(mod.calls[0]!.stdin.command, { name: 'hard', args: 'src', body: 'Investigate the race.' })
  await skill({ skill: 'other', text: 'A skill Claude ran mid-turn.', source: 'userSettings', frontmatter: {} })
  await mod.start({ turnId: 't2', text: 'next' })
  assert.equal('command' in mod.calls[1]!.stdin, false)
  // A plugin's skill is named plugin:skill in both events (probed live on 2.1.330).
  await mod.submit({ text: '/plugin:hard', command: { name: 'plugin:hard', args: '' }, wait: false, origin: { kind: 'composer' } })
  await skill({ skill: 'other', text: 'not this one', source: 'plugin', frontmatter: {} })
  await skill({ skill: 'plugin:hard', text: 'The plugin body.', source: 'plugin', frontmatter: {}, pluginName: 'plugin' })
  await mod.start({ turnId: 't3', text: '<command-message>plugin:hard</command-message>' })
  assert.deepEqual(mod.calls[2]!.stdin.command, { name: 'plugin:hard', args: '', body: 'The plugin body.' })
  // A command whose turn never starts (a local built-in) must not ride on the next drained message.
  await mod.submit({ text: '/rename x', command: { name: 'rename', args: 'fix the auth race everywhere' }, wait: false, origin: { kind: 'composer' } })
  await mod.start({ turnId: 't4', text: 'Another Claude session sent a message:\n<cross-session-message>' })
  assert.equal('command' in mod.calls[3]!.stdin, false)
})

test('a lower main loop effort is ignored', async () => {
  const mod = load({ 'mod-turn': { stdout: JSON.stringify({ level: 'lookup', effort: 'low' }) } })
  await mod.start({ turnId: 't1', text: 'list files' })
  assert.equal((await mod.step(stepEvent({ effort: 'medium' }))).effort, 'medium')
})

test('a turn sends each request\'s usage, so each is priced on its own', async () => {
  const mod = load()
  const request = (input_tokens: number) => ({
    model: 'claude-haiku-5-5', input_tokens, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0,
  })
  await mod.step(stepEvent({ agentId: 'a1' }), { usage: request(10) })
  await mod.step(stepEvent({ agentId: 'a1', index: 1 }), { usage: null })
  await mod.step(stepEvent({ turnId: 't2', agentId: 'a2' }), { usage: request(99) })
  await mod.step(stepEvent({ agentId: 'a1', index: 2 }), { usage: request(20) })
  await mod.complete(completeEvent({ agentId: 'a1', usage: request(30) }))
  assert.deepEqual(mod.calls[0]!.stdin.requests, [request(10), request(20)])
  await mod.complete(completeEvent({ agentId: 'a1', usage: request(30) }))
  assert.equal(mod.calls[1]!.stdin.requests, undefined)
})

test('plugin options reach the CLI env', async () => {
  const mod = load({}, { priority: 'fast', main_effort: false, tiers: ['haiku', 'sonnet'], budget: 3 })
  await mod.start({ turnId: 't1', text: 'x' })
  const env = mod.calls[0]!.env
  assert.equal(env.CLAUDE_PLUGIN_OPTION_PRIORITY, 'fast')
  assert.equal(env.CLAUDE_PLUGIN_OPTION_MAIN_EFFORT, 'false')
  assert.equal(env.CLAUDE_PLUGIN_OPTION_TIERS, 'haiku,sonnet')
  assert.equal(env.CLAUDE_PLUGIN_OPTION_BUDGET, '3')
  assert.equal(env.CLAUDE_CODE_SESSION_ID, 's1')
})

test('a failing or garbled CLI leaves every event unchanged', async () => {
  const bad = [{ exitCode: 1, stdout: PICK.stdout }, { stdout: 'not json' }, { stdout: '' }]
  for (const reply of bad) {
    const mod = load({ 'mod-spawn': reply, 'mod-turn': reply, 'mod-start': reply, 'mod-usage': reply })
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
    const done = completeEvent({ usage: payloads['mod-usage'].usage })
    assert.equal(await mod.complete(done), done)
    assert.equal(mod.calls.length, 4)
  }
})

test('a CLI that throws after next(e) ran does not run next again', async () => {
  const mod = load()
  mod.$.process.run = async () => { throw new Error('spawn failed') }
  const events = [['session.start', { source: 'startup' }], ['turn.complete', completeEvent({ usage: payloads['mod-usage'].usage })]] as const
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
  const events = ['agent.spawn', 'prompt.submit', 'session.start', 'skill.prompt', 'tool.call', 'turn.complete',
    'turn.start', 'turn.step']
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

test('each payload the mod sends matches its fixture', async () => {
  const mod = load({}, {}, payloads['mod-start'].settings)
  const spawn = payloads['mod-spawn']
  const usage = payloads['mod-usage']
  await mod.sessionStart({ source: 'startup' })
  await mod.toolCall({ tool: 'Agent', tool_use_id: 'tu1', effort: spawn.tool_input.effort })
  await mod.spawn(spawnEvent({
    cwd: spawn.cwd, parentAgentId: spawn.parent_agent_id, parentModel: spawn.parent_model, model: spawn.tool_input.model,
    subagentType: spawn.tool_input.subagent_type, prompt: spawn.tool_input.prompt, description: spawn.tool_input.description,
  }))
  await mod.start({ turnId: 't1', text: payloads['mod-turn'].text })
  for (const [index, request] of usage.requests.entries()) {
    await mod.step(stepEvent({ turnId: 't2', agentId: usage.agent_id, index }), { usage: request })
  }
  await mod.complete(completeEvent({ turnId: 't2', agentId: usage.agent_id, usage: usage.usage }))
  assert.deepEqual(Object.fromEntries(mod.calls.map(c => [c.command, c.stdin])), payloads)
})
