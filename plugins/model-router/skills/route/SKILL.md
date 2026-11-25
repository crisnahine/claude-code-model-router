---
name: route
description: Says which model and effort fit a task, how to brief and split it, and when to redo. Use when the user asks which model should do something, why an agent ran on a model, or whether it's routed right.
argument-hint: "[task description]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" policy *)
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" explain *)
---

# How this task would be routed

## Task

$ARGUMENTS

## Current policy

!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" policy --routing-mode='${user_config.routing_mode}' --max-tier='${user_config.max_tier}' --verify-gate='${user_config.verify_gate}'`

## What the router does

Model Router keeps the `model` and `effort` an Agent call names, within the user's cap, and fills in the effort when a call names only a model. A call that names no model runs on its agent's own model, else on `CLAUDE_CODE_SUBAGENT_MODEL` when it is set, else at the level its tools put it: `lookup` for an agent that can't edit, write or run commands, `standard` for any other. The brief plays no part. A rule in `model-router.json` can set or raise it. So you pick both on each call, from the table below. The Agent tool sets `effort` only when instructions ask for it, and this skill asks for it.

To see what a call would run on, run:

`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" explain --routing-mode='${user_config.routing_mode}' --max-tier='${user_config.max_tier}' --verify-gate='${user_config.verify_gate}'`

Add `--agent <name>` to see it for a specific agent, and `--model <model>` or `--effort <effort>` to see it for a call that names them. The brief doesn't change the answer: the router never reads it.

## Which model and effort fit the work

Judge the work by whether it has a check to run (tests, a recompute, a reread against the brief) and how open-ended it is. A contract and a code change can sit on the same row.

| Work | `model` | `effort` | For example |
| :- | :- | :- | :- |
| Finding, reading and listing, where a wrong answer is easy to spot | `haiku` | `medium` | the files that import a module, the open invoices in a sheet, the dates in a letter |
| Writing and fixing code, figures and documents to a brief | `haiku` | `high` | a function to its spec, a bug fixed against a failing test, a refactor inside one module, a contract summarized, an email drafted from an outline |
| A judgment call with no check to run: a design or a review | `sonnet` | `high` | two designs compared, a diff reviewed for security, a bug's cause with no reproduction |
| Open-ended research with no known approach | `fable` | `high` | a literature survey weighed for evidence, a question spread over many sources |

An agent whose definition sets its own model runs on it when the call names none, and the policy above shows which role agents do. Leave `model` off those calls unless the work needs a stronger model. Under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` a `model` on a call has no effect, so name only `effort`.

## How to split the work

- Don't delegate what takes you one or two tool calls.
- Split into independent pieces and start them in one message so they run in parallel, when they don't touch the same files.
- Use the agent that fits the work, from any plugin or the project. When none fits, the role agents in the policy above cover search, build, verify, plan and review.
- The agent starts without your context, so its brief carries:
  - the objective, and what done looks like
  - the output format: what to report, and in what shape
  - the tools and sources to use: the files, documents, pages or commands
  - the boundaries: what is out of scope, what not to change, and any rule it must keep
  - how to verify the result, and that the report includes the check it ran and its output
- Have code changes verified, and reviewed when they are more than trivial. Other work gets a check that fits it, such as figures recomputed or a document read against the request.

## Redo or continue

- Mark a redo only when a check failed or the report shows a gap, such as a part left out or no output from the check it was asked to run. Put "Redo:" in front of the same `description`, on the same agent, so the redo is found however its brief is rewritten. Workflow stages are never redone.
- A redo runs above the run it redoes. If you name a `model` or `effort` that asks for more than that run, it runs as named; if you name the same or less, or nothing, it climbs from the run it redoes, the one of the same agent and `description` that finished in this session in the last 30 minutes, or what the call would run at unmarked when there is none: one `effort` step up while that run was below `high`, else the next stronger `model`, else one more `effort` step. When `CLAUDE_CODE_EFFORT_LEVEL` or `CLAUDE_CODE_SUBAGENT_EFFORT` sets the effort, it goes straight to the next stronger `model`. To pick the lever yourself, name it: `effort` one step up for work that was skipped (a file not read, a check not run), a stronger `model` for work that tried and was wrong.
- A run cut short by its turn limit or an API error is continued with `SendMessage` to its agent ID, not sent again. It keeps its history, model and effort.

## Output

When the user asked which model to use or why one was used, answer with the row of the table the work fits, the model and effort it gives, and what `explain` says the call runs on. Otherwise go straight to delegating.
