---
name: route
description: Explains which model Model Router gives a task and why, and how to split the work. Use when the user asks which model should do something, why an agent ran on a model, or whether it's routed right.
argument-hint: "[task description]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" policy)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" explain *)
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

Judge the task on these signals. The router reads them from each brief's wording, and also performance or capacity limits the brief asks it to design against.

| Signal | Low | High |
| :- | :- | :- |
| Ambiguity | Spec is clear, one obvious approach | Requirements unclear, several valid designs |
| Scope | One file or function | Several modules, services or repositories |
| Risk | Easy to revert, covered by tests | Security, data, money, public API, migrations |

A brief that fixes or hunts a defect routes at complex or above, however small the fix, except for search and check agents, which stop at standard.

- Don't delegate what takes you one or two tool calls.
- Split into independent pieces and start them in one message so they run in parallel, when they don't touch the same files.
- Give each agent a complete brief: goal, files, constraints, how to verify, what to report. The brief is what the model is chosen from, so say what makes a piece hard (the security angle, the cross-service reach) in the brief itself.
- Use the agent that fits the work, from any plugin or the project. When none fits, the role agents in the policy above cover search, build, verify, plan, review and research-grade problems.
- If the estimate is wrong for a reason only you can see, pass `model` on that call. It is kept, within the cap.
- If a result isn't good enough, send the call again with "Redo:" in front of the same `description`. It runs one level higher however the brief is rewritten, on a stronger model or, when it stays on the same one, at more effort. Name a `model` only when you need that exact one.
- Have code changes verified, and reviewed when they are more than trivial.

## Output

When the user asked which model to use or why one was used, answer with the level, the model, and the signals that decided it. Otherwise go straight to delegating.
