---
name: route
description: Explains which Claude model Model Router would give a task and why, and how to split the work. Use when the user asks which model should do something, why an agent ran on a particular model, or how to delegate a large task.
when_to_use: Which model should do this, why did that agent run on haiku, how should I split this up, is this routed right.
argument-hint: "[task description]"
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" *)
---

# How this task would be routed

## Task

$ARGUMENTS

## Current policy

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" policy`

## What the router does on its own

Every subagent, teammate and workflow agent gets its model and reasoning effort set automatically from its brief, starting on the cheapest that should do the job. You don't choose a model per call. Your part is the split and the briefs.

To see what the router makes of a piece of work, run:

`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" explain <the brief>`

Add `--agent <name>` before the brief to see it for a specific agent.

## How to split the work

Judge the task on these signals. The router reads the same ones from each brief's wording.

| Signal | Low | High |
| :- | :- | :- |
| Ambiguity | Spec is clear, one obvious approach | Requirements unclear, several valid designs |
| Scope | One file or function | Several modules, services or repositories |
| Risk | Easy to revert, covered by tests | Security, data, money, public API, migrations |

- Don't delegate what takes you one or two tool calls.
- Split into independent pieces and start them in one message so they run in parallel, when they don't touch the same files.
- Give each agent a complete brief: goal, files, constraints, how to verify, what to report. The brief is what the model is chosen from, so say what makes a piece hard (the security angle, the cross-service reach) in the brief itself.
- Use the agent that fits the work, from any plugin or the project. When none fits, the role agents in the policy above cover search, build, verify, plan, review and research-grade problems.
- If the estimate is wrong for a reason only you can see, pass `model` on that call. It is kept, within the cap.
- If a result isn't good enough, re-issue the same call unchanged: it runs one level higher, on a stronger model or, when it stays on the same one, at more effort. Name a `model` only when you need that exact one. The router learns from both.
- Have code changes verified, and reviewed when they are more than trivial.

## Output

When the user asked which model to use or why one was used, answer with the level, the model, and the signals that decided it. Otherwise go straight to delegating.
