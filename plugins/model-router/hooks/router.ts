import type { EngineInterface, ModelEffort, PluginOptions, Register } from 'claude-code'

// The engine's own events, forwarded to the Python runtime the settings hooks use, so
// one router decides everything. agent.spawn sees every subagent, teammate and workflow
// agent; turn.step carries the effort that agent.spawn has no field for.

const EFFORTS: readonly ModelEffort[] = ['low', 'medium', 'high', 'xhigh', 'max']
const KEPT = 500

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

async function ask($: EngineInterface, options: Record<string, string>, command: string, payload: object): Promise<Pick> {
  const env = { ...(await $.process.hookEnv()), ...options }
  const run = await $.process.run(['sh', `${$.plugin.root}/scripts/model-router.sh`, command], {
    stdin: JSON.stringify({ session_id: env.CLAUDE_CODE_SESSION_ID, ...payload }),
    env,
    timeoutMs: 8000,
  })
  if (run.exitCode !== 0 || !run.stdout.trim()) return null
  try {
    return JSON.parse(run.stdout) as Pick
  } catch {
    return null
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

  on('tool.call', { tool: 'Agent' }, ($, e, next) => {
    if (e.tool_use_id && typeof e.effort === 'string') remember(callerEffort, e.tool_use_id, e.effort)
    return next(e)
  }).catch(($, e, next) => next(e))

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await ask($, opts, 'mod-start', {})
    return started
  }).catch(($, e, next) => next(e))

  on('agent.spawn', async ($, e, next) => {
    if (e.fork || e.resume) return next(e)
    const effort = callerEffort.get(e.tool_use_id)
    const pick = await ask($, opts, 'mod-spawn', {
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

  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      const pick = await ask($, opts, 'mod-turn', { text: e.text })
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
    return yield* next(apply ? { ...e, effort: want } : e)
  }).catch(async function* ($, e, next) {
    return yield* next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    if (e.agentId === undefined) turnEffort.delete(e.turnId)
    if (e.usage) await ask($, opts, 'mod-usage', { agent_id: e.agentId, usage: e.usage })
    return done
  }).catch(($, e, next) => next(e))
}
