#!/usr/bin/env python3
"""Router overhead with no paid calls: hook wall time, the mod path, and state growth.

    python3 bench/overhead.py [--runs 30] [--calls 1000] [--json]

Every run uses throwaway CLAUDE_PLUGIN_DATA, CLAUDE_CONFIG_DIR and project folders, so the
real plugin state is never read or written, and the daily check is off, so nothing goes
over the network. Standard library only.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "model-router"
LAUNCHER = (PLUGIN / "scripts" / "model-router.sh").as_posix()
BRIEFS = ("list the files under src and summarize what each one does",
          "implement the retry helper in src/http.py and add a unit test",
          "fix the race condition in the payments service across all services and find the root cause")
MODEL_IDS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5",
             "fable": "claude-fable-5-2"}
SESSION_MODEL = "claude-opus-5-5"
USAGE = {"model": "claude-sonnet-5-5", "input_tokens": 12000, "output_tokens": 800,
         "cache_read_input_tokens": 40000, "cache_creation_input_tokens": 2000}
MODEL_ERROR = "model claude-fable-5-2 is not available on this account"  # matches health.MODEL_ERROR

# Hook input per settings hook command, from the documented fields
# (https://code.claude.com/docs/en/hooks.md); hook_input() adds the common ones.
# refresh-cache is left out: it is async, so nothing waits on it, and it fetches.
HOOK_INPUTS = {
    "session-start": {"hook_event_name": "SessionStart", "source": "startup", "model": SESSION_MODEL},
    "user-prompt": {"hook_event_name": "UserPromptSubmit", "prompt": BRIEFS[2]},
    "pre-agent": {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                  "tool_input": {"subagent_type": "general-purpose", "description": "fix", "prompt": BRIEFS[1]}},
    "post-agent": {"hook_event_name": "PostToolUse", "tool_name": "Agent",
                   "tool_input": {"subagent_type": "general-purpose", "description": "fix", "prompt": BRIEFS[1]},
                   "tool_response": {"resolvedModel": "claude-sonnet-5-5", "status": "completed"}},
    "post-edit": {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": "src/app.py"}},
    "agent-failed": {"hook_event_name": "PostToolUseFailure", "tool_name": "Agent",
                     "tool_input": {"subagent_type": "general-purpose", "prompt": BRIEFS[0]}, "error": MODEL_ERROR},
    "agent-stopped": {"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_type": "general-purpose",
                      "last_assistant_message": "done"},
    "stop": {"hook_event_name": "Stop", "stop_hook_active": False},
    "model-switch": {"hook_event_name": "PostModelSwitch", "to_model": "claude-sonnet-5-5"},
}
# What the mod's ask() sends (hooks/router.ts), through the same launcher.
MOD_INPUTS = {
    "mod-start": {},
    "mod-spawn": {"parent_model": SESSION_MODEL, "kind": "subagent",
                  "tool_input": {"subagent_type": "general-purpose", "prompt": BRIEFS[1], "description": "fix"}},
    "mod-turn": {"text": BRIEFS[2], "model": SESSION_MODEL},
    "mod-usage": {"agent_id": "a1", "usage": USAGE},
}
# Files the simulation must have written, or the run didn't exercise their path.
STATE_FILES = ("ledger.jsonl", "state.json", "learned.json")
_TOP_KEYS = ("prompt", "text")


def percentile(samples: list[float], share: float) -> float:
    """Nearest rank."""
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def summary(samples: list[float], digits: int = 1, **labels) -> dict:
    return {**labels, "runs": len(samples), "p50_ms": round(percentile(samples, 0.5), digits),
            "p95_ms": round(percentile(samples, 0.95), digits), "max_ms": round(max(samples), digits)}


def vary(payload: dict, run: int) -> dict:
    """The same call with a different brief each run, so repeats aren't read as redos or
    refused rewrites."""
    payload = dict(payload)
    for key in _TOP_KEYS:
        if key in payload:
            payload[key] = f"{payload[key]} (run {run})"
    if "prompt" in payload.get("tool_input", {}):
        payload["tool_input"] = {**payload["tool_input"], "prompt": f"{payload['tool_input']['prompt']} (run {run})"}
    return payload


class Sandbox:
    """Throwaway data, home, project and bytecode folders, plus a 1 MB transcript. Each reset
    makes new folders rather than deleting the old, so a file a scanner holds open can't break
    the next run."""
    counter = itertools.count()

    def __init__(self, base: Path):
        self.base = base
        self.reset()

    def reset(self) -> None:
        self.root = self.base / f"box{next(self.counter)}"
        for name in ("data", "home", "project", "pycache"):
            (self.root / name).mkdir(parents=True)
        self.transcript = self.root / "transcript.jsonl"
        row = json.dumps({"type": "assistant", "message": {"model": SESSION_MODEL, "content": [
            {"type": "text", "text": "x" * 900}]}})
        self.transcript.write_text((row + "\n") * (1024 * 1024 // (len(row) + 1)), encoding="utf-8")

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE_", "ANTHROPIC_")) and k != "PYTHONDONTWRITEBYTECODE"}
        env.update({"CLAUDE_PLUGIN_DATA": str(self.root / "data"), "CLAUDE_CONFIG_DIR": str(self.root / "home"),
                    "CLAUDE_PROJECT_DIR": str(self.root / "project"), "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
                    "CLAUDE_PLUGIN_OPTION_UPDATE_CHECK": "false",
                    "PYTHONPYCACHEPREFIX": str(self.root / "pycache")})
        return env

    def hook_input(self, payload: dict) -> str:
        return json.dumps({"session_id": "bench", "cwd": str(self.root / "project"),
                           "transcript_path": str(self.transcript), **payload})


def run_command(box: Sandbox, command: str, payload: dict) -> float:
    started = time.perf_counter()
    run = subprocess.run(["sh", LAUNCHER, command], input=box.hook_input(payload), env=box.env(),
                         capture_output=True, text=True, timeout=60)
    elapsed = (time.perf_counter() - started) * 1000
    if run.returncode != 0:
        raise RuntimeError(f"{command} exited {run.returncode}: {run.stderr.strip()}")
    return elapsed


def seed(box: Sandbox) -> None:
    """One session's worth of state, so warm hooks find what a real session leaves behind."""
    for command in ("session-start", "user-prompt", "pre-agent", "post-agent", "post-edit"):
        run_command(box, command, HOOK_INPUTS[command])


def hook_latency(base: Path, inputs: dict, runs: int) -> list[dict]:
    """Cold: no bytecode cache and no state, as on the first hook after installing or updating.
    Warm: bytecode and a seeded session in place, as on every later hook."""
    rows = []
    for command, payload in inputs.items():
        cold = []
        for i in range(runs):
            box = Sandbox(base / "hooks")
            cold.append(run_command(box, command, vary(payload, i)))
        box = Sandbox(base / "hooks")
        seed(box)
        warm = [run_command(box, command, vary(payload, i)) for i in range(runs)]
        rows += [summary(cold, command=command, phase="cold"), summary(warm, command=command, phase="warm")]
    return rows


# The launcher's own loop (scripts/model-router.sh) with no router code after it.
FLOOR = ("for py in python3 python; do if command -v \"$py\" >/dev/null 2>&1; then "
         "\"$py\" -c pass; code=$?; [ \"$code\" -eq 87 ] || exit \"$code\"; fi; done; exit 1")


def python_floor(runs: int) -> dict:
    """sh and one interpreter start, as the launcher runs them: the floor under every hook."""
    samples = []
    for _ in range(runs + 1):
        started = time.perf_counter()
        subprocess.run(["sh", "-c", FLOOR], check=True, timeout=60)
        samples.append((time.perf_counter() - started) * 1000)
    return summary(samples[1:], command="launcher with no router code (floor)", phase="warm")


def in_process(base: Path, calls: int, runs: int):
    """Imports the runtime against a throwaway data dir, times one routing decision, and
    simulates routed calls with the real hook functions to see how state grows."""
    box = Sandbox(base / "inproc")
    for key in [k for k in os.environ if k.startswith(("CLAUDE_", "ANTHROPIC_"))]:
        del os.environ[key]
    os.environ.update(box.env())
    sys.pycache_prefix = str(box.root / "pycache")
    sys.path.insert(0, str(PLUGIN / "scripts"))
    from modelrouter import hooks, router, settings, spawn, store
    from modelrouter import mod as modpath

    project = str(box.root / "project")
    decide = []
    for i in range(max(runs, 100)):
        hook_input = {"session_id": "bench", "cwd": project,
                      "tool_input": {"subagent_type": "general-purpose", "prompt": f"{BRIEFS[i % 3]} ({i})"}}
        started = time.perf_counter()
        snap = settings.Snapshot(hook_input)
        router.decide(spawn.facts(hook_input, snap), snap.registry, snap.options)
        decide.append((time.perf_counter() - started) * 1000)

    def routed_call(session: str, i: int, mod: bool) -> None:
        tool_input = {"subagent_type": "general-purpose", "prompt": f"{BRIEFS[i % 3]} (call {i})"}
        call = {"session_id": session, "cwd": project, "tool_input": tool_input}
        if mod:
            model = (modpath.spawn({**call, "parent_model": SESSION_MODEL, "kind": "subagent"}) or {}).get("model")
        else:
            out = hooks.pre_agent(call)
            model = ((out or {}).get("hookSpecificOutput") or {}).get("updatedInput", {}).get("model")
        ran_as = MODEL_IDS.get(model or "", SESSION_MODEL)  # the model it was routed to, never a substitute
        if i % 4 == 3:  # a background agent: launched now, finished when it stops
            hooks.post_agent({**call, "tool_response": {"resolvedModel": ran_as, "status": "async_launched",
                                                        "agentId": f"bg{i}"}})
            hooks.agent_stopped({"session_id": session, "agent_id": f"bg{i}", "agent_type": "general-purpose",
                                 "last_assistant_message": "done"})
        else:
            hooks.post_agent({**call, "tool_response": {"resolvedModel": ran_as, "status": "completed"}})
        if i % 50 == 49:
            hooks.agent_failed({**call, "error": MODEL_ERROR})
        modpath.usage({"session_id": session, "agent_id": f"a{i}", "usage": {**USAGE, "model": ran_as}})

    def sizes() -> dict:
        return {p.name: p.stat().st_size for p in store.data_dir().iterdir()
                if p.is_file() and not p.name.endswith(".lock")}

    def simulate(n: int, per_session: int, offset: int = 0, watch: dict | None = None) -> None:
        """Even sessions take the fallback path, odd ones the mod path."""
        for i in range(offset, offset + n):
            number = i // per_session
            session, mod = f"s{number}", number % 2 == 1
            if i % per_session == 0 or i == offset:
                hooks.session_start({"session_id": session, "cwd": project, "source": "startup",
                                     "model": SESSION_MODEL})
                if mod:
                    modpath.start({"session_id": session})
                    modpath.turn({"session_id": session, "text": BRIEFS[i % 3], "model": SESSION_MODEL})
                else:
                    hooks.user_prompt({"session_id": session, "cwd": project, "prompt": BRIEFS[i % 3]})
            routed_call(session, i, mod)
            if i % 5 == 0:
                hooks.post_edit({"session_id": session, "cwd": project,
                                 "tool_input": {"file_path": f"{project}/src/f{i}.py"}})
            if watch is not None:
                for name, size in sizes().items():
                    watch[name] = max(watch.get(name, 0), size)

    simulate(calls, per_session=20)
    grown = sizes()

    # Bound check: lower every cap, run past them twice, and compare each round's peak size,
    # so a file trimmed by halving isn't judged by where its saw-tooth happens to stand.
    # Once with one call per session, so the per-session files hold far more sessions than
    # their cap, and once with one long session, for the per-session lists.
    caps = {(store, "LEDGER_MAX_BYTES"): 16 * 1024, (store, "KEEP_ENTRIES"): 5, (store, "KEPT_RECORDS"): 10}
    saved = {key: getattr(*key) for key in caps}
    verdict: dict = {}
    for per_session, rounds in ((1, max(40, calls // 5)), (10 ** 9, max(50, calls // 5))):
        os.environ["CLAUDE_PLUGIN_DATA"] = str(base / f"bound{per_session}")
        for (module, name), value in caps.items():
            setattr(module, name, value)
        try:
            first: dict = {}
            simulate(rounds, per_session, watch=first)
            second: dict = {}
            simulate(2 * rounds, per_session, offset=rounds, watch=second)
        finally:
            for (module, name), value in saved.items():
                setattr(module, name, value)
        for name in set(first) | set(second):
            before, after = first.get(name, 0), second.get(name, 0)
            # Slack scaled to the file, so a file of tiny entries still shows its growth.
            verdict[name] = verdict.get(name, True) and after <= before * 1.25 + 256
    os.environ["CLAUDE_PLUGIN_DATA"] = str(box.root / "data")

    state = [{"file": name, "bytes_after": grown.get(name, 0),
              "bytes_per_1000_calls": round(grown.get(name, 0) * 1000 / calls),
              "bounded": verdict.get(name, "not exercised")}
             for name in sorted(set(grown) | set(verdict) | set(STATE_FILES))]
    label = "config.load + spawn.facts + router.decide (in process)"
    return summary(decide, digits=2, command=label, phase="warm"), state


NODE_BENCH = r"""
import { register } from %(router)s
const runs = %(runs)d
const pick = JSON.stringify({ model: 'haiku', effort: 'low', reason: 'lookup subtask', level: 'complex' })
const hooks = {}
const on = (event, ...rest) => { hooks[event] = rest.pop(); return { catch: () => {} } }
const $ = { plugin: { root: '/plugin' }, session: { model: async () => 'claude-opus-5-5' },
  process: { hookEnv: async () => ({ CLAUDE_CODE_SESSION_ID: 'bench' }),
             run: async () => ({ exitCode: 0, stdout: pick, stderr: '' }) } }
register(on, {})
const same = (e) => e
const ms = (f) => async () => { const t = performance.now(); await f(); return performance.now() - t }
async function drain(gen) { let r = await gen.next(); while (!r.done) r = await gen.next() }
async function* model() { yield { kind: 'text', text: 'hi' }; return { done: true } }
const cases = {
  'agent.spawn': ms(() => hooks['agent.spawn']($, { prompt: 'p', subagentType: 'general-purpose' },
                                            (e) => ({ ...e, agentId: 'a1' }))),
  'turn.start': ms(() => hooks['turn.start']($, { turnId: 't1', text: 'fix the auth migration' }, same)),
  'turn.step': ms(() => drain(hooks['turn.step']($, { turnId: 't1', effort: 'medium' }, model))),
}
const out = {}
for (const [name, f] of Object.entries(cases)) {
  for (let i = 0; i < 20; i++) await f()
  const samples = []
  for (let i = 0; i < runs; i++) samples.push(await f())
  out[name] = samples
}
console.log(JSON.stringify(out))
"""


def mod_handlers(base: Path, runs: int) -> list[dict]:
    """The router.ts handlers alone, with the runtime's answer returned at once: the mod's own
    cost on top of the mod-* command it runs."""
    skipped = {"command": "router.ts handlers", "phase": "warm", "runs": 0, "p50_ms": None, "p95_ms": None}
    node = shutil.which("node")
    if not node:
        return [{**skipped, "skipped": "node not on PATH"}]
    script = base / "mod_bench.mjs"
    script.write_text(NODE_BENCH % {"router": json.dumps((PLUGIN / "hooks" / "router.ts").as_uri()),
                                    "runs": max(runs, 200)}, encoding="utf-8")
    run = subprocess.run([node, "--experimental-strip-types", "--no-warnings", str(script)],
                         capture_output=True, text=True, timeout=120)
    if run.returncode != 0:
        lines = run.stderr.strip().splitlines()
        return [{**skipped, "skipped": lines[-1] if lines else "node failed"}]
    return [summary(samples, digits=3, command=f"router.ts {name}", phase="warm")
            for name, samples in json.loads(run.stdout).items()]


def machine() -> dict:
    def version(*argv: str) -> str | None:
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=20).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    return {"platform": platform.platform(), "machine": platform.machine(), "cpus": os.cpu_count(),
            "python": platform.python_version(), "node": version("node", "--version"),
            "claude_code": version("claude", "--version")}


def table(rows: list[dict], columns: tuple) -> str:
    lines = [" | ".join(columns), " | ".join("-" * len(c) for c in columns)]
    lines += [" | ".join(str(row.get(c, "")) for c in columns) for row in rows]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=30, help="runs per hook and phase (default 30)")
    parser.add_argument("--calls", type=int, default=1000, help="routed calls to simulate (default 1000)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of tables")
    args = parser.parse_args()
    if args.runs < 1 or args.calls < 1:
        parser.error("--runs and --calls must be at least 1")
    host = machine()  # before the environment is pointed at throwaway folders
    with tempfile.TemporaryDirectory(prefix="model-router-bench-") as tmp:
        base = Path(tmp)
        floor = python_floor(args.runs)
        hook_rows = hook_latency(base, HOOK_INPUTS, args.runs)
        mod_rows = hook_latency(base, MOD_INPUTS, args.runs)
        decide_row, state = in_process(base, args.calls, args.runs)
        mod_rows += [decide_row, *mod_handlers(base, args.runs)]
    report = {"machine": host, "runs": args.runs, "calls": args.calls, "floor": floor,
              "hooks": hook_rows, "mod": mod_rows, "state": state}
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print("Machine: " + ", ".join(f"{k} {v}" for k, v in host.items()))
    print(f"\nSettings hooks, wall time per call (ms), {args.runs} runs each\n")
    print(table([floor, *hook_rows], ("command", "phase", "p50_ms", "p95_ms", "max_ms")))
    print("\nMod path (ms)\n")
    print(table(mod_rows, ("command", "phase", "p50_ms", "p95_ms", "max_ms")))
    print(f"\nState after {args.calls} routed calls\n")
    print(table(state, ("file", "bytes_after", "bytes_per_1000_calls", "bounded")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
