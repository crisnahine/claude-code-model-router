---
name: ship
description: Take a task from request to reviewed, verified change with multi-model orchestration. Plans on Opus, builds on Sonnet, verifies on Haiku, reviews on Opus, loops on failures, and stops before anything irreversible.
argument-hint: "[task, issue URL or issue number]"
disable-model-invocation: true
---

# Ship: plan, build, verify, review

Task: $ARGUMENTS

Run this pipeline yourself as the coordinator. Delegate each stage to the named agent with a complete brief, since agents do not see this conversation. Keep a todo list with one item per stage so the user can follow along.

## 0. Understand

- If the task is a GitHub issue number or URL, read it with `gh issue view` (ask once if `gh` isn't available).
- Read `CLAUDE.md` and the parts of the codebase the task touches. Use `orchestrator:scout` if that takes more than a few searches.
- Decide the size with the `orchestrator:route` rubric. If the task is trivial (one obvious edit), skip step 1 and implement directly.
- Make sure work happens on a branch, not the default branch. Create one named after the task if needed.

## 1. Plan

Send the task, the relevant files and the constraints to `orchestrator:architect`. Use `orchestrator:deep-solver` instead if routing scored 4 or more high signals.

Show the user the plan summary in a few lines. If the plan changes a public API, a data schema, security behavior or dependencies, stop and ask the user to confirm before building. Otherwise continue.

## 2. Build

Give `orchestrator:implementer` the plan. When the plan marks steps as independent and they touch different files, run several implementers in parallel, one per step group.

## 3. Verify

Run `orchestrator:verifier` on the result. If it fails, send the failures to `orchestrator:implementer` and verify again.

## 4. Review

Run `orchestrator:reviewer` on the diff. Send blocking and should-fix findings back to the implementer, then verify again.

Limit steps 3 and 4 to **2 fix loops** in total. If problems remain after that, escalate the remaining problem once to `orchestrator:architect` for a diagnosis, apply its fix, and verify. If it still fails, stop and report.

## 5. Deliver

- Commit on the branch with a clear message describing the change.
- Report to the user: what changed, the verification output, the review verdict, and anything left open.
- **Do not push, open a pull request, merge, deploy or publish** unless the user asked for that in this request. Offer it in one line instead.

## Guardrails

- Never skip or disable a failing test to get green.
- Never change CI, release or deployment config unless the task is about them.
- If an agent's result contradicts the code, trust the code and re-check.
