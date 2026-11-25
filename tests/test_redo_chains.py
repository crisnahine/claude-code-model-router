"""Redo chains driven through the real hooks on each path. What a spawn ran at comes from that path's own mechanics
(the fallback's rewritten input, the mod's answer and its per-request effort, Claude Code's effort order), never
from the test: the PostToolUse input is what that path delivers, and on the mod path the observed pair arrives
before the result, after it, or never."""
from __future__ import annotations

import contextlib
import functools
import itertools
import os
import shutil
import sys
import time
import unittest
from dataclasses import dataclass
from unittest import mock

from test_model_router import Sandbox, cfg, hooks, modpath, router, session, settings, store
from modelrouter import agents, calls
from modelrouter import spawn as procedure

IDS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "fable": "claude-fable-5-2"}
ORDER = ("haiku", "sonnet", "opus", "fable")
EFFORTS = cfg.EFFORTS
# Haiku, Sonnet and Opus 5.5 and Fable 5.2 default to medium (model-config.md#adjust-effort-level).
DEFAULT_EFFORT = "medium"
FIRST = list(itertools.product((None, *ORDER), (None, *EFFORTS)))
PROMPT = "make the parser keep quoted fields whole"
TASK = "Parser fix"
DEPTH = 6
# The mod path three ways: what the agent ran at reaches the runtime before the Agent result, after it, or never.
PATHS = ("fallback", "mod", "mod-late", "mod-unseen")


@dataclass(frozen=True)
class Run:
    """What one spawn ran at: the model, and the effort its requests carried."""
    model: str
    effort: str

    def __str__(self) -> str:
        return f"{self.model}@{self.effort}"


@dataclass(frozen=True)
class Setup:
    session: str
    cap: str | None = None
    env: tuple = ()
    session_effort: str = DEFAULT_EFFORT
    agent: str = "general-purpose"  # "reader" can change nothing and "writer" can, so a call naming no model differs

    def __str__(self) -> str:
        bits = [f"{self.agent} in session {self.session} at {self.session_effort}", f"max_tier {self.cap}"]
        return ", ".join(bits + [f"{k}={v}" for k, v in self.env])


def _up(effort: str) -> str:
    return EFFORTS[min(EFFORTS.index(effort) + 1, len(EFFORTS) - 1)]


# What Claude names on each redo, from the first call's pair and the run before.
POLICIES = {
    "names nothing": lambda first, last: (None, None),
    "repeats the first pair": lambda first, last: first,
    "repeats the last run": lambda first, last: (last.model, last.effort),
    "one effort step up": lambda first, last: (last.model, _up(last.effort)),
    "the next stronger model": lambda first, last: (ORDER[min(ORDER.index(last.model) + 1, len(ORDER) - 1)], None),
    "haiku at max": lambda first, last: ("haiku", "max"),
}


def _shape(record: dict | None) -> str | None:
    """A done record without its stamps, which never change what a redo decides."""
    return repr(sorted((k, v) for k, v in record.items() if k not in ("at", "spawn"))) if record else None


class Walk:
    """Every chain of one setup on every path. A step depends only on the done record, the call and the setup,
    so each distinct (record, call) runs once through the hooks, and every chain through it shares that run."""

    def __init__(self, case: Sandbox, setup: Setup, paths=PATHS):
        self.case, self.setup, self.paths = case, setup, paths
        if setup.agent == "reader":
            case.agent("reader", extra="tools: Read, Grep, Glob\n")
        elif setup.agent == "writer":
            case.agent("writer")
        self.memo: dict = {p: {} for p in paths}
        self.tid = 0
        snap = settings.Snapshot({"session_id": "x", "cwd": str(case.project)})
        self.reg, self.opts, self.usable = snap.registry, snap.options, snap.usable
        self.forced = self.opts.force
        # Under an effort variable effort is the environment's, so a redo's one lever is the model.
        self.env_effort = bool(self.opts.effort_level or self.opts.subagent_effort) and not self.forced
        self.decided: list = []
        real = procedure.record

        def record(spawned, *args, **kwargs):
            self.decided.append(spawned.decision)
            return real(spawned, *args, **kwargs)
        # The registry, the agent files and the data folder can't change inside one setup, so each is read once.
        load = functools.lru_cache(maxsize=None)(functools.partial(cfg.load, {"cwd": str(case.project)}))
        find = functools.lru_cache(maxsize=None)(agents.find)
        folder = store.data_dir()
        # One process, so the cross-process lock and the atomic rename only cost time here.
        self.patches = [mock.patch.object(procedure, "record", record),
                        mock.patch.object(cfg, "load", lambda hook_input=None: load()),
                        mock.patch.object(agents, "find", find),
                        mock.patch.object(store, "data_dir", lambda: folder),
                        mock.patch.object(store, "locked", lambda path: contextlib.nullcontext()),
                        mock.patch.object(store, "write_atomic", lambda path, text: path.write_text(text, encoding="utf-8"))]

    # Claude Code ---------------------------------------------------------------------------------------
    def engine(self, model: str | None, effort: str | None) -> Run:
        """What Claude Code runs for the input that reaches it (sub-agents.md, model and effort order)."""
        o = self.opts
        forced = self.reg.family(o.subagent_model) if o.force else None
        model = forced or model or self.reg.family(o.subagent_model) or self.setup.session
        effort = (o.effort_level or effort or o.subagent_effort
                  or (self.setup.session_effort if model == self.setup.session else DEFAULT_EFFORT))
        return Run(model, effort)

    def rank(self, run: Run) -> tuple:
        return self.reg.strength(run.model), EFFORTS.index(run.effort)

    def top(self) -> str:
        if self.forced:
            return self.reg.family(self.opts.subagent_model)
        return max((n for n in self.usable if self.reg.models[n].trusted), key=self.reg.strength)

    def at_top(self, run: Run) -> bool:
        return run.model == self.top() and (self.env_effort or run.effort == "max")

    # one spawn -------------------------------------------------------------------------------------------
    def spawn(self, path: str, sid: str, named: tuple, redo: bool) -> tuple[Run, dict, str | None]:
        self.tid += 1
        tid = f"toolu_{self.tid}"
        model, effort = named
        tool_input = {"subagent_type": self.setup.agent, "prompt": PROMPT,
                      "description": f"Redo: {TASK.lower()}" if redo else TASK,
                      **({"model": model} if model else {}), **({"effort": effort} if effort else {})}
        common = {"session_id": sid, "tool_use_id": tid, "cwd": str(self.case.project)}
        if path == "fallback":
            out = (hooks.pre_agent({**common, "tool_input": tool_input,
                                    "effort": {"level": self.setup.session_effort}}) or {}).get("hookSpecificOutput") or {}
            assert out.get("permissionDecision") != "deny", out
            ran_input = out.get("updatedInput") or tool_input
            run, note, post_input = self.engine(ran_input.get("model"), ran_input.get("effort")), out.get("additionalContext"), ran_input
        else:
            # hooks/router.ts: tool.call marks the call, so PreToolUse stands down, and agent.spawn asks mod-spawn.
            marks = store.data_dir() / modpath.MARKS
            marks.mkdir(parents=True, exist_ok=True)
            (marks / tid).write_text("")
            assert hooks.pre_agent({**common, "tool_input": tool_input}) is None
            answer = hooks.HANDLERS["mod-spawn"]({**common, "parent_model": IDS[self.setup.session], "kind": "subagent",
                                                  "session_effort": self.setup.session_effort, "tool_input": tool_input})
            # The answer's model goes on at agent.spawn; its effort replaces each request's at turn.step.
            run = self.engine(self.reg.family(answer.get("model")) or model, effort)
            run = Run(run.model, answer.get("effort") or run.effort)
            note, post_input = answer.get("note"), tool_input
        seen = {"session_id": sid, "agent_id": f"agent_{tid}", "tool_use_id": tid,
                "ran": {"model": IDS[run.model], "effort": run.effort}}
        if path == "mod":
            hooks.HANDLERS["mod-usage"](seen)
        hooks.post_agent({**common, "tool_input": post_input,
                          "tool_response": {"resolvedModel": IDS[run.model], "status": "completed"}})
        if path == "mod-late":
            hooks.HANDLERS["mod-usage"](seen)
        return run, dict(self.decided[-1]), note

    def step(self, path: str, record: dict | None, named: tuple) -> tuple:
        """One spawn from a done record (None: the first call). Memoized on the record and the call."""
        key = (_shape(record), named)
        if key not in self.memo[path]:
            # One session per path keeps the state files small; the done record is all a step reads.
            sid, task = path, calls.task({"subagent_type": self.setup.agent, "description": TASK})
            session.record(sid, IDS[self.setup.session])
            store.save_entry("done", sid, {task: record} if record else {})
            store.save_entry("routes", sid, {})
            run, decision, note = self.spawn(path, sid, named, redo=record is not None)
            self.memo[path][key] = (run, decision, note, dict(calls.last_run(sid, task) or {}))
        return self.memo[path][key]

    def chain(self, path: str, calls_named: list) -> list:
        """The first call and its redos, in order, on one path."""
        record, out = None, []
        for named in calls_named:
            run, decision, note, record = self.step(path, record, named)
            out.append((run, decision, note))
        return out

    # the walk --------------------------------------------------------------------------------------------
    def run(self, check) -> tuple[int, int]:
        """Walk every chain; `check(ok, message)` is each invariant's one assertion. Returns (states, longest)."""
        seen: set = set()
        longest = 0
        for first in FIRST:
            starts = {p: self.step(p, None, first) for p in self.paths}
            for p, (run, decision, _, record) in starts.items():
                where = f"{self.setup} | {p} | first {first} -> ran {run}"
                self.held(check, where, run)
                self.recorded(check, where, run, record)
            stack = [({p: (starts[p][3], starts[p][0]) for p in self.paths}, [f"first {first} -> {self.show(starts)}"])]
            while stack:
                state, chain = stack.pop()
                key = (first, tuple((_shape(state[p][0]), state[p][1]) for p in self.paths))
                if key in seen:
                    continue
                seen.add(key)
                longest = max(longest, len(chain) - 1)
                if not check(len(chain) <= 2 * len(ORDER) * len(EFFORTS),
                             f"[reaches the top] {self.setup} | never settles: {' ; '.join(chain)}"):
                    continue
                for policy in POLICIES:
                    outs = {}
                    for p in self.paths:
                        record, last = state[p]
                        outs[p] = self.step(p, record, POLICIES[policy](first, last))
                        self.invariants(check, p, chain + [policy], last, POLICIES[policy](first, last), *outs[p])
                    runs = {p: (outs[p][0], bool(outs[p][1]["no_step"])) for p in self.paths}
                    check(len(set(runs.values())) == 1,
                          f"[paths agree] {self.setup} | {' ; '.join(chain)} ; {policy}: {runs}")
                    if all(outs[p][1]["no_step"] for p in self.paths):
                        continue  # at the top, where the invariants just held that it stays
                    stack.append(({p: (outs[p][3], outs[p][0]) for p in self.paths}, chain + [f"{policy} -> {self.show(outs)}"]))
        return len(seen), longest

    def show(self, outs: dict) -> str:
        runs = {p: str(o[0]) for p, o in outs.items()}
        return next(iter(runs.values())) if len(set(runs.values())) == 1 else str(runs)

    def held(self, check, where: str, run: Run) -> bool:
        # The force pin outranks the cap by design: the user pinned the model.
        cap = self.opts.cap
        return check(self.forced or not cap or not self.reg.stronger(run.model, cap), f"[max_tier] {where}, over {cap}")

    def recorded(self, check, where: str, run: Run, record: dict) -> bool:
        climbs_from = (record.get("model"), router._ran_at(record) if cfg.Level.parse(record.get("level")) else None)
        return check(climbs_from == (run.model, run.effort), f"[climbs from what ran] {where}: recorded {record}")

    def invariants(self, check, path, chain, prev: Run, named: tuple, run: Run, decision: dict, note, record) -> None:
        where = f"{self.setup} | {path} | {' ; '.join(chain)} {named} -> ran {run}, no_step {decision['no_step']}"
        top, same = self.at_top(prev), run == prev
        check(not same or (top and decision["no_step"] and note and "the same as the run it redoes" in note),
              f"[never the same] {where}")
        check(not decision["no_step"] or (top and same), f"[no_step only at the top] {where}")
        check(top or self.rank(run) > self.rank(prev), f"[strictly above] {where}")
        asked = Run(named[0], named[1] or "") if named[0] else None
        if (asked and asked.model in self.usable and not self.forced and not self.opts.effort_level
                and (self.reg.stronger(asked.model, prev.model)
                     or (asked.model == prev.model and asked.effort and EFFORTS.index(asked.effort) > EFFORTS.index(prev.effort)))):
            check(run.model == asked.model and (not asked.effort or run.effort == asked.effort), f"[runs as named] {where}")
        self.recorded(check, where, run, record)
        self.held(check, where, run)


MAIN = [Setup(s, cap) for s in ("haiku", "sonnet", "opus") for cap in (None, "haiku", "sonnet", "opus")]
ENV_CASES = ((("CLAUDE_CODE_SUBAGENT_EFFORT", "high"),), (("CLAUDE_CODE_EFFORT_LEVEL", "medium"),),
             (("CLAUDE_CODE_SUBAGENT_MODEL", "sonnet"),),
             (("CLAUDE_CODE_SUBAGENT_MODEL", "haiku"), ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "1")),
             (("CLAUDE_CODE_SUBAGENT_MODEL", "opus"), ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "1")))
# A first call that names no model runs at the level its agent's tools put it: lookup for a reader, standard otherwise.
AGENTS = [Setup(s, cap, agent=agent) for agent in ("reader", "writer") for s in ("haiku", "opus") for cap in (None, "sonnet")]
# The environment walk, smaller: each variable on a weak and a strong session with no cap, and under a Sonnet cap
# on the strong one; then the session's level away from the default.
ENVS = ([Setup(s, None, env) for env in ENV_CASES for s in ("haiku", "opus")]
        + [Setup("opus", "sonnet", env) for env in ENV_CASES]
        + [Setup(s, None, (), effort) for s in ("haiku", "opus") for effort in ("low", "xhigh")])


def walk(case: Sandbox, setups: list, check, paths=PATHS) -> dict:
    stats = {"setups": 0, "states": 0, "longest": 0, "spawns": 0}
    for setup in setups:
        shutil.rmtree(case.data, ignore_errors=True)
        with mock.patch.dict(os.environ):
            for name in [n for n in os.environ if n.startswith(("CLAUDE_CODE_SUBAGENT", "CLAUDE_CODE_EFFORT", "CLAUDE_PLUGIN_OPTION_MAX"))]:
                del os.environ[name]
            case.options(**({"max_tier": setup.cap} if setup.cap else {}), **dict(setup.env))
            w = Walk(case, setup, paths)
            with contextlib.ExitStack() as stack:
                for patch in w.patches:
                    stack.enter_context(patch)
                states, longest = w.run(check)
        stats["setups"] += 1
        stats["states"] += states
        stats["longest"] = max(stats["longest"], longest)
        stats["spawns"] += sum(len(m) for m in w.memo.values())
    stats["chains"] = len(setups) * len(FIRST) * len(POLICIES) ** DEPTH * len(paths)
    return stats


class RedoChains(Sandbox):
    def check(self, ok, message) -> bool:
        self.assertTrue(ok, message)
        return True

    def test_every_redo_chain_on_every_path(self):
        walk(self, MAIN, self.check)

    def test_every_redo_chain_under_the_environment_variables(self):
        walk(self, ENVS, self.check)

    def test_every_redo_chain_from_a_reader_and_a_writer(self):
        walk(self, AGENTS, self.check)


class RedoChainCases(Sandbox):
    """Chains that went wrong in live runs, on every path through the same simulator as the walk."""

    def chain(self, setup: Setup, *named: tuple) -> list:
        """Each path's runs, the same on all of them, as "model@effort", with "=" on a run that had no step left."""
        with mock.patch.dict(os.environ):
            self.options(**({"max_tier": setup.cap} if setup.cap else {}), **dict(setup.env))
            w = Walk(self, setup)
            with contextlib.ExitStack() as stack:
                for patch in w.patches:
                    stack.enter_context(patch)
                seen = {p: [f"{run}{'=' if decision['no_step'] else ''}" for run, decision, _ in w.chain(p, list(named))]
                        for p in PATHS}
        self.assertEqual(len({tuple(v) for v in seen.values()}), 1, seen)
        self.last = w
        return seen["fallback"]

    def test_a_redo_naming_the_same_model_again_climbs(self):
        self.assertEqual(self.chain(Setup("opus"), *[("haiku", None)] * 4),
                         ["haiku@medium", "haiku@high", "sonnet@high", "opus@high"])

    def test_a_redo_naming_the_same_model_and_effort_again_climbs_each_time(self):
        self.assertEqual(self.chain(Setup("opus"), *[("haiku", "medium")] * 4),
                         ["haiku@medium", "haiku@high", "sonnet@high", "opus@high"])

    def test_a_redo_naming_more_effort_on_haiku_runs_as_named(self):
        self.assertEqual(self.chain(Setup("opus"), ("haiku", "high"), ("haiku", "xhigh"), ("haiku", "xhigh")),
                         ["haiku@high", "haiku@xhigh", "sonnet@high"])

    def test_under_a_haiku_cap_a_redo_climbs_to_max_and_stays(self):
        self.assertEqual(self.chain(Setup("opus", "haiku"), *[(None, None)] * 5),
                         ["haiku@high", "haiku@xhigh", "haiku@max", "haiku@max=", "haiku@max="])

    def test_under_the_force_pin_a_redo_climbs_effort_to_max_and_stays(self):
        pin = (("CLAUDE_CODE_SUBAGENT_MODEL", "opus"), ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "1"))
        self.assertEqual(self.chain(Setup("sonnet", None, pin), *[(None, None)] * 4),
                         ["opus@high", "opus@xhigh", "opus@max", "opus@max="])
        self.assertIn("and opus gets no more effort", self.last.decided[-1]["reason"])

    def mod_run(self, tid: str, description: str, ran: dict | None, **named) -> dict:
        tool_input = {"subagent_type": "general-purpose", "prompt": PROMPT, "description": description, **named}
        common = {"session_id": "s1", "tool_use_id": tid, "cwd": str(self.project)}
        answer = hooks.HANDLERS["mod-spawn"]({**common, "parent_model": IDS["opus"], "kind": "subagent",
                                              "session_effort": "medium", "tool_input": tool_input})
        if ran is not None:
            hooks.HANDLERS["mod-usage"]({"session_id": "s1", "agent_id": f"a{tid}", "tool_use_id": tid, "ran": ran})
        hooks.post_agent({**common, "tool_input": tool_input,
                          "tool_response": {"resolvedModel": IDS["opus"], "status": "completed"}})
        return answer

    def test_a_redo_climbs_from_the_effort_the_requests_carried(self):
        # A cap lowered the run's medium to low, so the same call again asks for more than what ran.
        self.mod_run("t1", "Parser fix", {"model": IDS["opus"], "effort": "low"}, model="opus", effort="medium")
        self.assertEqual(self.mod_run("t2", "Redo: parser fix", None, model="opus", effort="medium"), modpath.OK)
        # With nothing observed, the record from the spawn holds medium, and the same call climbs one step.
        self.mod_run("t3", "Other fix", None, model="opus", effort="medium")
        redo = self.mod_run("t4", "Redo: other fix", None, model="opus", effort="medium")
        self.assertEqual((redo["model"], redo["effort"]), ("opus", "high"))

    def test_a_run_that_sent_no_effort_is_redone_on_a_stronger_model(self):
        # On a provider where haiku is Haiku 4.5, which takes no effort, an effort step gains nothing.
        self.mod_run("t1", "Parser fix", {"model": "claude-haiku-4-5", "effort": None}, model="haiku", effort="medium")
        redo = self.mod_run("t2", "Redo: parser fix", None, model="haiku", effort="medium")
        self.assertEqual((redo["model"], redo["effort"]), ("sonnet", "high"))
        self.assertIn("(haiku at no effort)", redo["reason"])

    def test_an_observation_without_its_spawn_or_with_a_bad_pair_changes_nothing(self):
        for ran in ({"model": IDS["opus"], "effort": 3}, {"model": "gpt-x", "effort": "high"}, "high"):
            self.assertEqual(hooks.HANDLERS["mod-usage"]({"session_id": "s1", "agent_id": "a", "tool_use_id": "t9", "ran": ran}),
                             modpath.OK)
        self.assertEqual(store.load_entry("done", "s1"), {})


if __name__ == "__main__":
    found: dict = {}

    class Collect(RedoChains):
        def check(self, ok, message):
            if not ok:
                found.setdefault(message.split("]")[0] + "]", []).append(message)
            return ok

        def runTest(self):
            started = time.time()
            print(walk(self, ENVS if "env" in sys.argv else MAIN, self.check), f"{time.time() - started:.1f}s")

    unittest.TextTestRunner().run(Collect())
    for kind, messages in found.items():
        print(f"\n{kind}: {len(messages)}")
        for m in messages[:int(os.environ.get("SHOW", "3"))]:
            print("  ", m)
