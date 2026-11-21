---
name: route
description: Choose which Claude model and which orchestrator agent should handle a task, by complexity, risk and cost. Use before delegating any non-trivial coding, debugging, review or research task, and when the user asks which model to use.
when_to_use: Which model should do this, plan this work, delegate, split this up, this is a big task, use the cheapest model that can do it.
argument-hint: "[task description]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator.py *)
---

# Route a task to the right model

## Current routing policy

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator.py --data-dir "${CLAUDE_PLUGIN_DATA}" routing-table`

## Task

$ARGUMENTS

## How to decide

Score the task on these signals. Each "high" pushes it one tier up.

| Signal | Low | High |
| :- | :- | :- |
| Ambiguity | Spec is clear, one obvious approach | Requirements unclear, several valid designs |
| Scope | One file or function | Several modules, services or repositories |
| Risk | Easy to revert, covered by tests | Security, data, money, public API, migrations |
| Novelty | Pattern already exists in the repo | New subsystem, unfamiliar library, research |
| Verification | A test or command proves it | Correctness needs reasoning or review |

Then pick the **cheapest tier that can do it reliably**:

- **0 to 1 high signals, read-only**: `orchestrator:scout` (haiku). Don't delegate at all if it takes you one or two tool calls yourself.
- **0 to 1 high signals, writes code**: `orchestrator:implementer` (sonnet).
- **2 to 3 high signals**: `orchestrator:architect` (opus) plans, then `orchestrator:implementer` executes the plan step by step.
- **4 or more high signals, or the task already failed once on opus**: `orchestrator:deep-solver` (fable).
- **Every code change**: `orchestrator:verifier` (haiku) runs the checks, and `orchestrator:reviewer` (opus) reviews the diff if the change is more than trivial.

## Rules

- Split work into independent pieces and run them in parallel when they don't touch the same files. Use one message with several Agent calls.
- Give each agent a complete brief: goal, relevant files, constraints, how to verify, and what to report. Agents don't see this conversation.
- Escalate one tier when an agent reports that it is blocked or its result fails verification. Don't retry the same tier with the same brief.
- Respect the tier cap shown above. If a spawn is refused for the cap, use the suggested agent and tell the user the cap is limiting quality, once.
- Explore, Plan and general-purpose remain available, but prefer the orchestrator agents so the routing is explicit.

## Output

When the user asked which model to use, answer with the chosen agent and model, the two or three signals that decided it, and the plan for delegation. Otherwise, go straight to delegating.
