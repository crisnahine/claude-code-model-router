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

## What the router does

Model Router keeps the `model` and `effort` an Agent call names, within the user's cap, and fills in the effort when a call names only a model. A call that names neither runs on its agent's own model, or else on the conversation's model. So you pick both on each call, from the table below. The Agent tool sets `effort` only when instructions ask for it, and this skill asks for it.

To see what a call would run on, run:

`sh "${CLAUDE_PLUGIN_ROOT}/scripts/model-router.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" explain <the brief>`

Add `--agent <name>` before the brief to see it for a specific agent.

## Which model and effort fit the work

Judge the work by how easy its result is to check, what a mistake costs, how open-ended it is and how much there is to read. A contract and a code change can sit on the same row.

| Work | `model` | `effort` | For example |
| :- | :- | :- | :- |
| Finding, reading and listing, where a wrong answer is easy to spot | `haiku` | `medium` | the files that import a module, the open invoices in a sheet, the dates in a letter |
| Everyday work to a clear brief | `sonnet` | `medium` | a function to its spec, a refactor inside one module, notes turned into a summary, an email drafted from an outline |
| Hard to check, high-stakes, ambiguous or broad | `opus` | `high` | a bug's cause, a design, a review, a migration, a contract, financial figures, anything published or sent out |
| Open-ended research with no known approach | `fable` | `high` | a literature survey weighed for evidence, a question spread over many sources |

An agent whose definition sets its own model runs on it when the call names none, and the policy above shows which role agents do. Leave `model` off those calls unless the work needs a stronger model. Under `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` a `model` on a call has no effect, so name only `effort`.

## How to split the work

- Don't delegate what takes you one or two tool calls.
- Split into independent pieces and start them in one message so they run in parallel, when they don't touch the same files.
- Use the agent that fits the work, from any plugin or the project. When none fits, the role agents in the policy above cover search, build, verify, plan, review and research-grade problems.
- The agent starts without your context, so its brief carries:
  - the objective, and what done looks like
  - the output format: what to report, and in what shape
  - the tools and sources to use: the files, documents, pages or commands
  - the boundaries: what is out of scope, what not to change, and any rule it must keep
  - how to verify the result, and that the report includes the check it ran and its output
- Have code changes verified, and reviewed when they are more than trivial. Other work gets a check that fits it, such as figures recomputed or a document read against the request.

## Redo or continue

- Mark a redo only when a check failed or the report shows a gap, such as a part left out or no output from the check it was asked to run. Put "Redo:" in front of the same `description`, so the redo is found however its brief is rewritten.
- A redo that names no `model` or `effort` runs one step above the run it redoes. To pick the lever yourself, name it: `effort` one step up for work that was skipped (a file not read, a check not run), a stronger `model` for work that tried and was wrong.
- A run cut short by its turn limit or an API error is continued with `SendMessage` to its agent ID, not sent again. It keeps its history, model and effort.

## Output

When the user asked which model to use or why one was used, answer with the row of the table the work fits, the model and effort it gives, and what `explain` says the call runs on. Otherwise go straight to delegating.
