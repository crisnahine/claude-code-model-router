---
name: deliver
description: Takes a task from request to reviewed, verified change with multi-model orchestration. Plans and reviews run on Opus and checks stay on Haiku; it loops on failures and stops before anything irreversible.
argument-hint: "[task, issue URL or issue number]"
disable-model-invocation: true
---

# Deliver: plan, build, verify, review

Task: $ARGUMENTS

Run this pipeline yourself as the coordinator. Delegate each stage to the named agent with a complete brief, since agents do not see this conversation. Keep a todo list with one item per stage so the user can follow along.

## 0. Understand

- If the task is a GitHub issue number or URL, read it with `gh issue view` (ask once if `gh` isn't available).
- Read `CONTRIBUTING.md` and the parts of the codebase the task touches. Use `model-router:scout` if that takes more than a few searches.
- Decide the size. When a few reads of the code settle the design and the change is a few edits, a single file or sequential steps, skip steps 1 and 2 and make it yourself. Otherwise plan with the architect, and delegate building only when it is more than a few edits or its parts can run in parallel or need their own context.
- Make sure work happens on a branch, not the default branch. Create one named after the task if needed.

## 1. Plan

Send the task, the relevant files and the constraints to `model-router:architect`. When the task is research-grade (no known approach, open-ended, across several systems), name `effort: "xhigh"` on that call.

Show the user the plan summary in a few lines. If the plan changes a public API, a data schema, security behavior or dependencies, stop and ask the user to confirm before building. Otherwise continue.

## 2. Build

A plan of a few edits is built directly. For more, give `model-router:implementer` the plan, with the `model` and `effort` the step needs on the call: the plan rates each step. When the plan marks steps as independent and they touch different files, run several implementers in parallel, one per step group.

## 3. Verify

Run `model-router:verifier` on the result and wait for it before starting the review. If it reports that it couldn't run a command because of permissions, run that command yourself and use its output. If it fails, send the failures to `model-router:implementer` and verify again.

## 4. Review

Save the full diff against the base branch, including new files, to a file in the session's scratchpad (for example `git add -N . && git diff <base> > <scratchpad>/review.diff`), then run `model-router:reviewer` with that path and the task's acceptance criteria. Send blocking and should-fix findings back to the implementer, then verify again.

Limit steps 3 and 4 to **2 fix loops** in total. If problems remain after that, escalate the remaining problem once to `model-router:architect` for a diagnosis, apply its fix, and verify. If it still fails, give the problem and both attempts once to `model-router:implementer` with `model: "opus"` and `effort: "xhigh"` on the call, or `model: "fable"` and `effort: "high"` when Opus already fell short. If that fails too, stop and report.

## 5. Deliver

- Commit on the branch with a clear message describing the change.
- Report to the user: what changed, the verification output, the review verdict, and anything left open.
- **Do not push, open a pull request, merge, deploy or publish** unless the user asked for that in this request. Offer it in one line instead.

## Guardrails

- Never skip or disable a failing test to get green.
- Never change CI, release or deployment config unless the task is about them.
- If an agent's result contradicts the code, trust the code and re-check.
