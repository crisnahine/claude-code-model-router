"""The tables the skill commands print."""
from __future__ import annotations

from . import agents, ladder, ledger, router, settings, spawn, store
from . import config as cfg
from . import registry

# The --json shapes. A new key keeps the version; removing or renaming one bumps it.
USAGE_SCHEMA = "model-router.usage/2"
MODELS_SCHEMA = "model-router.models/3"


def _strongest(reg: registry.Registry, usable: list[str]) -> str | None:
    """The strongest usable model by a rank someone vouches for: a repository's own only when nothing else is usable."""
    ranked = [n for n in usable if reg.models[n].trusted] or usable
    return ranked[-1] if ranked else None


def _effort(snap: settings.Snapshot, pick: ladder.Pick) -> str | None:
    """The pick's effort as it runs, held to any maxEffortLevel cap, and saying so."""
    effort, held = router.capped(pick.effort, snap.allowed.effort_cap(snap.registry, pick.model))
    return f"{effort} ({held.lstrip('; ')})" if held else effort


def policy(snap: settings.Snapshot) -> str:
    reg, options, usable = snap.registry, snap.options, snap.usable
    limit = options.cap
    lines = [f"Routing: **{options.mode}**. Cap: **{limit or 'none'}**. "
             f"Strongest model in use: **{_strongest(reg, usable) or 'none'}**.", "",
             "A model named on a call, by a rule or by the agent stands for the lowest level it runs, and runs at that "
             "level's effort when nobody chose one. A call that names none runs on its agent's own model (the parent's, for "
             "`inherit`), else on "
             "CLAUDE_CODE_SUBAGENT_MODEL when it is set, else at the level its tools put it: lookup for an agent "
             "that can't edit, write or run commands, standard for any other, raised to a rule's level.", "",
             "| Level | Model | Effort |", "| :- | :- | :- |"]
    for level in cfg.LEVELS:
        pick = snap.pick(level)
        lines.append(f"| {level} | {pick.model or 'left as it is'} | {_effort(snap, pick) or 'as the agent or session sets it'} |")
    lines += ["", "| Model | Strength | Cost | Use for |", "| :- | -: | -: | :- |"]
    for name in reg.by_strength():
        info = reg.models[name]
        flag = "" if name in usable else " (not in use)"
        lines.append(f"| {name}{flag} | {info.strength} | {'-' if info.cost is None else info.cost} | {info.use_for} |")
    lines += ["", "Strength and cost are relative ranks, not prices.", "",
              "| Role agent | Model | Effort |", "| :- | :- | :- |"]
    for path in sorted((cfg.PLUGIN_ROOT / "agents").glob("*.md")):
        agent = agents.parse(path, "plugin") or {}
        lines.append(f"| {cfg.PLUGIN_NAME}:{agent.get('name') or path.stem} | {agent.get('model') or 'named on the call'} | "
                     f"{agent.get('effort') or '-'} |")
    return "\n".join(lines)


def status(snap: settings.Snapshot) -> dict:
    """The mod's status line (under 80 characters: Claude Code puts the plugin's name before it) and the
    /model-router reply: the line, the session's setup once known, and how to switch."""
    options, session_id = snap.options, snap.session_id
    mode = f"{options.mode}{' (this session)' if options.switched else ''}"
    rows = [r for r in ledger.read(session=session_id) if r.get("event") == "spawn"] if isinstance(session_id, str) else []
    redos = sum(1 for r in rows if r.get("escalated_from"))
    line = mode if options.mode == "off" else " · ".join(
        [mode, rows[-1].get("via") if rows and rows[-1].get("via") == "fallback" else "mod",
         *([f"cap {options.cap}"] if options.cap else []), f"{sum(r.get('action') == 'rewrite' for r in rows)} routed",
         f"{redos} redo{'' if redos == 1 else 's'}"])
    entry = store.load_entry(settings.SWITCHES, session_id) if isinstance(session_id, str) else {}
    found = entry.get("setup") or entry.get("estimate")
    setup = []
    if isinstance(found, dict) and isinstance(found.get("model"), str):
        at = f" at {found['effort']}" if found.get("effort") else ""
        below = "" if found.get("recommended") else " (below the recommended setups)"
        setup = [f"session: {found['model']}{at}{below}{'' if 'setup' in entry else ', estimated before the first request'}"]
    other = "off" if options.mode == "auto" else "on"
    how = f"Switch it {other} for this session with /model-router {other}, or for good with Routing mode in /config."
    return {"line": line, "text": "\n".join([line, *setup, how])}


def switched(snap: settings.Snapshot, asked: str) -> str:
    """What a /model-router on or off did, and whether it lasts."""
    options = snap.options
    state = "on" if options.mode == "auto" else "off"
    if options.mode != asked:
        return f"Routing stays {state}: the switch could not be saved."
    if options.switched:
        return f"Routing is {state} for this session. To keep it {state}, set Routing mode in /config."
    return f"Routing is {state}, as Routing mode in /config sets it."


def models_data(snap: settings.Snapshot) -> dict:
    """Each registry model's state here, in the `model-router.models/3` shape."""
    reg, options, table, usable = snap.registry, snap.options, snap.health, snap.usable
    limit = options.cap
    allowed = snap.allowed.permitted(reg, snap.sendable)
    rows = []
    for name in reg.by_strength():
        if limit and reg.strength(name) > reg.strength(limit):
            status, detail = "above_cap", "above your cap"
        elif limit and reg.models[name].project:
            status, detail = "project_model_under_cap", "added by this project, so not used while a cap is set"
        elif name not in allowed:
            status, detail = "not_permitted", "not permitted by the availableModels or deniedModels setting"
        elif not table.healthy(name):
            status, detail = "routed_around", f"routed around until retried ({table.why_routed_around(name)})"
        elif table.last_ran_as(name):
            status, detail = "working", "working"
        else:
            status, detail = "not_used", "not used yet"
        rows.append({"model": name, "last_ran_as": table.last_ran_as(name), "status": status, "detail": detail})
    data = {
        "schema": MODELS_SCHEMA,
        "cap": limit or None,
        "strongest_in_use": _strongest(reg, usable),
        "models": rows,
        "set_aside": [str(p) for p in store.set_aside()],
        "newer_files": [str(p) for p in store.newer_files()],
        "newer_config": [str(p) for p in reg.newer],
        "workflow_model": options.workflow_model,
    }
    # New keys appear only when they have something to say, so a clean setup's output keeps its bytes.
    if reg.problems:
        data["config_problems"] = list(reg.problems)
    held_back = _held_back(reg)
    if held_back:
        data["held_back"] = held_back
    locks = lock_rows()
    if locks:
        data["locks"] = locks
    return data


def _held_back(reg: registry.Registry) -> list[dict]:
    """What a project's override file asked for that was not applied: what the registry held at load, and
    each rule model a spawn in the last week found weaker than what the call would run on without it."""
    found = [{"kind": h.kind, "name": h.name, "reason": h.reason} for h in reg.held]
    seen = {(r.get("agent"), r["held"]) for r in ledger.read(7) if isinstance(r.get("held"), str)}
    return found + [{"kind": "rule", "name": str(agent), "reason": reason} for agent, reason in sorted(seen, key=str)]


def models(snap: settings.Snapshot) -> str:
    data = models_data(snap)
    lines = [f"Cap: **{data['cap'] or 'none'}**. Strongest model in use: **{data['strongest_in_use'] or 'none'}**.",
             "", "| Model | Last ran as | Status |", "| :- | :- | :- |"]
    lines += [f"| {m['model']} | {m['last_ran_as'] or '-'} | {m['detail']} |" for m in data["models"]]
    if data.get("config_problems"):
        lines += ["", "Left out of the override files, so the registry runs without them: "
                  + "; ".join(data["config_problems"]) + "."]
    if data.get("held_back"):
        lines += ["", "A project's model-router.json asked for these and Model Router did not apply them "
                  "(a project's file can raise, never lower):"]
        lines += [f"- {h['kind']} {h['name']}: {h['reason']}" for h in data["held_back"]]
    if data["workflow_model"]:
        pin = data["workflow_model"] if cfg._PLAIN_NAME.fullmatch(data["workflow_model"]) else "one model"
        lines += ["", f"CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL pins every workflow agent whose script names no model to "
                  f"{pin}, so Model Router sets only their effort. /model-router:usage shows the model each one ran on."]
    if data["set_aside"]:
        lines += ["", "Set aside because it could not be read, so that state started fresh: "
                  + ", ".join(data["set_aside"]) + ". Look at them, then delete them."]
    if data["newer_files"]:
        lines += ["", "Written by a newer Model Router, so only read and never updated: "
                  + ", ".join(data["newer_files"]) + ". Update the plugin, or delete them to start fresh."]
    if data["newer_config"]:
        lines += ["", "Written for a newer Model Router, so only the settings this release understands apply: "
                  + ", ".join(data["newer_config"]) + ". Update the plugin to use the rest."]
    lines += lock_table(data.get("locks", []))
    return "\n".join(lines)


LOCK_EVENTS = ("waits", "other_session", "skipped")


def lock_rows() -> list[dict]:
    """Each state file's lock counts, for `models`: waits for the lock, how many of those were
    on another session's lock, writes skipped because it stayed held."""
    rows = []
    for name, counts in sorted(store.lock_counts().items()):
        if isinstance(counts, dict):
            rows.append({"file": name, **{e: counts.get(e) if isinstance(counts.get(e), int) else 0
                                          for e in LOCK_EVENTS},
                         "last_at": counts.get("at") if isinstance(counts.get("at"), (int, float)) else None})
    return rows


def lock_table(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    return ["", "| State file | Lock waits | On another session's lock | Writes skipped |",
            "| :- | -: | -: | -: |",
            *(f"| {r['file']} | {r['waits']} | {r['other_session']} | {r['skipped']} |" for r in rows),
            "", "A skipped write lost one update to that file, since the lock stayed held past "
            f"{store.LOCK_WAIT_SECONDS:g} seconds."]


def explain(snap: settings.Snapshot, text: str, agent: str | None = None, model: str | None = None,
            effort: str | None = None) -> str:
    """What an agent call naming `model` and `effort` would run on here, and why: a dry run of spawn.decide,
    which writes nothing. `text` is the call's prompt."""
    tool_input = {"prompt": text, "subagent_type": agent or "general-purpose",
                  **({"model": model} if model else {}), **({"effort": effort} if effort else {})}
    decision = spawn.decide({"session_id": snap.session_id, "tool_input": tool_input}, snap).decision
    runs_on = decision["model"] or decision["was"]
    out = [f"Level: **{decision['level']}**." if decision["level"] else "Level: none, the model it runs on isn't known.",
           f"Model: **{runs_on or 'left as it is'}**, effort: {decision['effort'] or 'unchanged'}"
           + (" (the call is changed)." if decision["action"] == "rewrite" else " (the call is left as it is)."),
           f"Why: {decision['reason']}.",
           f"Models in use: {', '.join(snap.usable) or 'none: availableModels and deniedModels leave no registry model open'}."]
    return "\n".join(out)


def _runs(rows: list[dict]) -> dict[str, dict]:
    """Each subagent's usage, by agent id: a listed agent's newest row holds all of its turns so far, and a
    row of one turn's tokens, a workflow agent's or one the engine stopped listing, adds on top."""
    runs: dict[str, dict] = {}
    for r in rows:
        if r.get("event") != "usage" or not r.get("agent_id") or not isinstance(r.get("models"), list):
            continue
        key = str(r["agent_id"])
        if r.get("running") or key not in runs:
            runs[key] = {**r, "models": list(r["models"])}
        else:
            runs[key]["models"] += r["models"]
    return runs


def usage_data(days: int, exact: bool = False) -> dict:
    """What the ledger shows for the last `days` days, in the `model-router.usage/2` shape.
    Dollars are rounded to six places unless `exact`, which the table uses."""
    rows = ledger.read(days)
    by_agent: dict[str, dict] = {}

    def row(agent: object) -> dict:
        name = str(agent or "?")
        return by_agent.setdefault(name, {"agent": name, "calls": 0, "routed": 0, "levels": {}, "ran_on": {}})

    redos: dict[str, int] = {}
    for r in rows:
        if r.get("event") == "spawn":
            a = row(r.get("agent"))
            a["calls"] += 1
            a["routed"] += r.get("action") == "rewrite"
            if r.get("level"):
                a["levels"][r["level"]] = a["levels"].get(r["level"], 0) + 1
            # A marked redo with no run on record climbed from a level nothing shows ran.
            if r.get("escalated_from") and r.get("on_record") is not False:
                redos[r["escalated_from"]] = redos.get(r["escalated_from"], 0) + 1
    redos = {level: redos[level] for level in cfg.LEVELS if level in redos}
    by_model: dict[str, dict] = {}
    for r in rows:
        if r.get("event") == "result":
            model = str(r.get("resolved_model") or "background (not reported)")
            a = row(r.get("agent"))
            a["ran_on"][model] = a["ran_on"].get(model, 0) + 1
            m = by_model.setdefault(model, {"model": model, "runs": 0, "run_tokens": None})
            m["runs"] += 1
    runs = _runs(rows)
    for run in runs.values():
        used = [m for m in run["models"] if isinstance(m, dict)]
        # A workflow agent has no result row, so its first turn says what it ran on.
        if not run.get("running") and run.get("agent") and used:
            a = row(run["agent"])
            model = str(used[0].get("model"))
            a["ran_on"][model] = a["ran_on"].get(model, 0) + 1
        for m in used:
            found = by_model.get(str(m.get("model")))
            if found is not None:
                found["run_tokens"] = (found["run_tokens"] or 0) + sum(int(m.get(k) or 0) for k in ledger.TOKENS)
    spend: dict[str, dict] = {}
    unknown, no_estimate, subscription = set(), set(), False
    for run in runs.values():
        agent = str(run.get("agent") or "?")
        if not isinstance(run.get("cost_usd"), (int, float)):
            no_estimate.add(agent)
            continue
        s_row = spend.setdefault(agent, {"agent": agent, "runs": 0, "usd": 0.0})
        s_row["runs"] += 1
        s_row["usd"] += run["cost_usd"]
        if run.get("unknown_price"):
            unknown.add(agent)
        subscription = subscription or run.get("subscription") is True
    spend_rows = sorted(spend.values(), key=lambda v: -v["usd"])
    total = sum((v["usd"] for v in spend_rows), 0.0)
    if not exact:
        total = round(total, 6)
        for s_row in spend_rows:
            s_row["usd"] = round(s_row["usd"], 6)
    since = ledger.since(days)
    return {
        "schema": USAGE_SCHEMA,
        "days": days,
        **({"since": since} if since else {}),
        "agents": sorted(by_agent.values(), key=lambda a: -a["calls"]),
        **({"redos": redos} if redos else {}),
        "models": [by_model[m] for m in sorted(by_model)],
        "model_errors": sum(1 for r in rows if r.get("event") == "model_error"),
        "spend": {"total_usd": total, "rows": spend_rows, "unknown_price": sorted(unknown),
                  "no_estimate": sorted(no_estimate - set(spend)), "subscription": subscription},
    }


def usage(days: int) -> str:
    data = usage_data(days, exact=True)
    spend = data["spend"]
    if not data["agents"] and not spend["rows"]:
        return f"No activity recorded in the last {days} days."
    since = f" (the ledger starts {data['since'][:10]})" if data.get("since") else ""
    lines = [f"Subagent usage, last {days} days{since}", "",
             "| Agent | Calls | Rewritten by router | Levels | Ran on |", "| :- | -: | -: | :- | :- |"]
    for a in data["agents"]:
        levels = ", ".join(f"{level} x{n}" for level, n in a["levels"].items()) or "-"
        ran = ", ".join(f"{m} x{n}" for m, n in sorted(a["ran_on"].items())) or "-"
        lines.append(f"| {a['agent']} | {a['calls']} | {a['routed']} | {levels} | {ran} |")
    if data.get("redos"):
        redone = ", ".join(f"{level} x{n}" for level, n in data["redos"].items())
        lines += ["", f"Redos by the level they climbed from: {redone}. A level that keeps coming back can be raised "
                  "under `levels` in ~/.claude/model-router.json."]
    if data["models"]:
        lines += ["", "| Model | Agent runs | Subagent tokens |", "| :- | -: | -: |"]
        lines += [f"| {m['model']} | {m['runs']} | {'-' if m['run_tokens'] is None else format(m['run_tokens'], ',')} |"
                  for m in data["models"]]
    if data["model_errors"]:
        lines += ["", f"Agent calls that failed with a model error: {data['model_errors']}."]
    if spend["rows"]:
        lines += ["", f"Subagent spend, Claude Code's estimate and not the bill: ${spend['total_usd']:,.2f}"]
        if spend["subscription"]:
            lines.append("A session in this window was on a subscription: usage inside the plan isn't billed in dollars.")
        lines += ["", "| Agent | Runs | USD |", "| :- | -: | -: |"]
        lines += [f"| {v['agent']} | {v['runs']} | {v['usd']:,.2f} |" for v in spend["rows"]]
        if spend["unknown_price"]:
            lines.append("\nClaude Code priced a model with no known price at another model's rates for: "
                         f"{', '.join(spend['unknown_price'])}.")
    if spend["no_estimate"]:
        lines += ["", f"Claude Code gives no estimate for these agents, such as workflow agents: {', '.join(spend['no_estimate'])}."]
    lines += ["", "The session's own cost and plan usage are in /usage."]
    return "\n".join(lines)
