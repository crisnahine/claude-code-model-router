"""The tables the skills print. Skills run through the Bash tool, so the settings passed in
fall back to what the last SessionStart recorded."""
from __future__ import annotations

from . import agents, ladder, learn, ledger, registry, router, settings, store, upstream
from . import config as cfg
from .profile import profile_call, profile_prompt

# The --json shapes. A new key keeps the version; removing or renaming one bumps it.
USAGE_SCHEMA = "model-router.usage/1"
MODELS_SCHEMA = "model-router.models/1"


def _effort(snap: settings.Snapshot, pick: ladder.Pick) -> str | None:
    """The pick's effort as it runs, held to any maxEffortLevel cap, and saying so."""
    effort, held = router.capped(pick.effort, snap.allowed.effort_cap(snap.config, pick.model))
    return f"{effort} ({held.lstrip('; ')})" if held else effort


def policy(snap: settings.Snapshot) -> str:
    config, options, usable = snap.config, snap.options, snap.usable
    limit = options.cap
    lines = [f"Routing: **{options.mode}**. Priority: **{options.priority}**. "
             f"Cap: **{limit or 'none'}**. Strongest model in use: **{usable[-1] if usable else 'none'}**.", "",
             "| Subtask level | Model | Effort |", "| :- | :- | :- |"]
    raises = learn.raises()
    for level in cfg.LEVELS:
        pick = ladder.pick(config, level, options.priority, usable, raises)
        note = " (raised by feedback)" if raises[level] else ""
        lines.append(f"| {level} | {pick.model or 'left as it is'}{note} | {_effort(snap, pick) or 'as the agent or session sets it'} |")
    lines += ["", "| Model | Strength | Speed | Cost | Use for |", "| :- | -: | -: | -: | :- |"]
    for name in registry.by_strength(config):
        info = registry.models(config)[name]
        flag = "" if name in usable else " (not in use)"
        lines.append(f"| {name}{flag} | {info.get('strength')} | {info.get('speed', '-')} | "
                     f"{info.get('cost', '-')} | {info.get('use_for', '')} |")
    lines += ["", "Strength, speed and cost are relative ranks, not prices.", "",
              "A role agent with a model keeps it, also where the router doesn't route; the others are "
              "routed by their brief, like any other agent.", "",
              "| Role agent | Model | Role |", "| :- | :- | :- |"]
    for name, agent in (config.get("crew") or {}).items():
        lines.append(f"| {cfg.PLUGIN_NAME}:{name} | {agent.get('model') or 'routed by brief'} | {agent.get('role', '')} |")
    return "\n".join(lines)


def models_data(snap: settings.Snapshot) -> dict:
    """Each registry model's state here, in the `model-router.models/1` shape."""
    config, options, table, usable = snap.config, snap.options, snap.health, snap.usable
    limit = options.cap
    allowed = snap.allowed.permitted(config)
    rows = []
    for name in registry.by_strength(config):
        if limit and registry.strength(config, name) > registry.strength(config, limit):
            status, detail = "above_cap", "above your cap"
        elif limit and name not in snap.trusted:
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
    learned = learn.stats()
    levels = [{"level": level, "routed_calls": info.get("calls", 0), "votes_too_low": info.get("votes", 0),
               "raised": bool(info.get("bump"))}
              for level, info in ((lv, learned.get(lv)) for lv in cfg.LEVELS) if isinstance(info, dict)]
    aliases = (upstream.load().get("cache") or {}).get("aliases")
    aliases = aliases if isinstance(aliases, list) else []
    return {
        "schema": MODELS_SCHEMA,
        "cap": limit or None,
        "strongest_in_use": usable[-1] if usable else None,
        "priority": options.priority,
        "models": rows,
        "levels": levels,
        "doc_aliases": aliases,
        "not_in_registry": [f for f in upstream.model_families(aliases) if f not in registry.models(config)],
        "set_aside": [str(p) for p in store.set_aside()],
        "newer_files": [str(p) for p in store.newer_files()],
        "newer_config": [str(p) for p in cfg.newer_overrides()],
        "workflow_model": options.workflow_model,
    }


def models(snap: settings.Snapshot) -> str:
    data = models_data(snap)
    lines = [f"Cap: **{data['cap'] or 'none'}**. Strongest model in use: **{data['strongest_in_use'] or 'none'}**. "
             f"Priority: **{data['priority']}**.",
             "", "| Model | Last ran as | Status |", "| :- | :- | :- |"]
    lines += [f"| {m['model']} | {m['last_ran_as'] or '-'} | {m['detail']} |" for m in data["models"]]
    if data["levels"]:
        lines += ["", "| Subtask level | Routed calls | Votes it was too low | Raised |", "| :- | -: | -: | :- |"]
        lines += [f"| {lv['level']} | {lv['routed_calls']} | {lv['votes_too_low']} | "
                  f"{'yes' if lv['raised'] else 'no'} |" for lv in data["levels"]]
    if data["workflow_model"]:
        lines += ["", f"CLAUDE_CODE_WORKFLOW_SUBAGENT_MODEL pins every workflow agent to {data['workflow_model']}. "
                  "Model Router still sets a workflow agent's model, and the docs don't say which of the two wins; "
                  "/model-router:usage shows the model each one ran on."]
    if data["doc_aliases"]:
        lines += ["", "Model aliases in the current Claude Code docs: " + ", ".join(data["doc_aliases"]) + "."]
        if data["not_in_registry"]:
            lines.append("Not in the registry yet, so not routed to: " + ", ".join(data["not_in_registry"]) + ".")
    if data["set_aside"]:
        lines += ["", "Set aside because it could not be read, so that state started fresh: "
                  + ", ".join(data["set_aside"]) + ". Look at them, then delete them."]
    if data["newer_files"]:
        lines += ["", "Written by a newer Model Router, so only read and never updated: "
                  + ", ".join(data["newer_files"]) + ". Update the plugin, or delete them to start fresh."]
    if data["newer_config"]:
        lines += ["", "Written for a newer Model Router, so only the settings this release understands apply: "
                  + ", ".join(data["newer_config"]) + ". Update the plugin to use the rest."]
    return "\n".join(lines)


def explain(snap: settings.Snapshot, text: str, agent: str | None = None) -> str:
    """What the router would make of this task, and why."""
    config, options, usable = snap.config, snap.options, snap.usable
    result = profile_call({"prompt": text}, agent) if agent else profile_prompt(text)
    level = result["level"]
    pick = ladder.pick(config, level, options.priority, usable, learn.raises())
    why = ", ".join(result["signals"]) or "no risk, scope or ambiguity wording"
    out = [f"Level: **{level}** ({why}).",
           f"Model: **{pick.model or 'left as it is'}**, effort: {_effort(snap, pick) or 'unchanged'}.",
           f"Priority {options.priority}; models in use: {', '.join(usable) or 'none: availableModels and deniedModels leave no registry model open'}."]
    if agent:
        definition = agents.find(agent, snap.project)
        pinned = (definition or {}).get("model")
        if definition is None:
            out.append(f"The definition of `{agent}` wasn't found, so its calls are left alone.")
        elif pinned and options.pins == "respect":
            out.append(f"`{agent}` names its own model ({pinned}), which is kept within the cap.")
    return "\n".join(out)


def status(changes: dict | None) -> str:
    if changes is None:
        return f"No upstream data cached yet. Run `/{cfg.PLUGIN_NAME}:refresh`."
    out = [f"Last reviewed Claude Code version: {changes['reviewed_version']}"]
    if changes["new_releases"]:
        out.append(f"\nNew releases since then ({len(changes['new_releases'])}):")
        out += [f"- {r.get('version')} ({r.get('date', '')}): {r.get('link', '')}" for r in changes["new_releases"]]
    else:
        out.append("No new releases.")
    for label, key in (("Docs pages added", "pages_added"), ("Docs pages removed", "pages_removed"),
                       ("Docs pages whose summary changed", "pages_changed")):
        if changes[key]:
            out.append(f"\n{label} ({len(changes[key])}):")
            out += [f"- {u}" for u in changes[key][:25]]
    if changes.get("new_model_families"):
        out.append("\nNew model aliases in the docs, not in the registry yet: "
                   + ", ".join(changes["new_model_families"]))
    if changes.get("removed_model_families"):
        out.append("\nRegistry models the docs no longer list: " + ", ".join(changes["removed_model_families"]))
    for label, key in (("Models routed calls ran on that are deprecated or retired", "deprecated_in_use"),
                       ("Models deprecated or retired since the last review", "newly_deprecated")):
        if changes.get(key):
            out.append(f"\n{label}:")
            out += [f"- {m['model']}: {m['state']}, retires {m['retires']}"
                    + (f", replacement {m['replacement']}" if m.get("replacement") else "") for m in changes[key]]
    if changes["watched_pages_touched"]:
        out.append("\nHigh impact (pages this plugin is built on):")
        out += [f"- {u}" for u in changes["watched_pages_touched"]]
    return "\n".join(out)


def usage_data(days: int, exact: bool = False) -> dict:
    """What the ledger shows for the last `days` days, in the `model-router.usage/1` shape.
    Dollars are rounded to six places unless `exact`, which the table uses."""
    rows = ledger.read(days)
    by_agent: dict[str, dict] = {}

    def row(agent: object) -> dict:
        name = str(agent or "?")
        return by_agent.setdefault(name, {"agent": name, "calls": 0, "routed": 0, "levels": {}, "ran_on": {}})

    for r in rows:
        if r.get("event") == "spawn":
            a = row(r.get("agent"))
            a["calls"] += 1
            a["routed"] += r.get("action") == "rewrite"
            if r.get("level"):
                a["levels"][r["level"]] = a["levels"].get(r["level"], 0) + 1
    by_model: dict[str, dict] = {}
    for r in rows:
        if r.get("event") == "result":
            model = str(r.get("resolved_model") or "background (not reported)")
            a = row(r.get("agent"))
            a["ran_on"][model] = a["ran_on"].get(model, 0) + 1
            m = by_model.setdefault(model, {"model": model, "runs": 0, "tokens": 0, "run_tokens": None})
            m["runs"] += 1
            # totalTokens is the run's final request only (https://code.claude.com/docs/en/hooks.md).
            m["tokens"] += r.get("total_tokens") if isinstance(r.get("total_tokens"), int) else 0
    for r in rows:
        m = by_model.get(str(r.get("model"))) if r.get("event") == "usage" and r.get("agent_id") else None
        if m is not None:
            m["run_tokens"] = (m["run_tokens"] or 0) + sum(int(r.get(k) or 0) for k in (
                "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    spend: dict[tuple[str, str], dict] = {}
    unpriced = set()
    for r in rows:
        if r.get("event") != "usage":
            continue
        loop, model = "subagent" if r.get("agent_id") else "main", str(r.get("model"))
        s_row = spend.setdefault((loop, model), {"loop": loop, "model": model, "turns": 0,
                                                  "output_tokens": 0, "usd": 0.0})
        s_row["turns"] += 1
        s_row["output_tokens"] += int(r.get("output_tokens") or 0)
        if isinstance(r.get("cost_usd"), (int, float)):
            s_row["usd"] += r["cost_usd"]
        else:
            unpriced.add(model)
    spend_rows = sorted(spend.values(), key=lambda v: -v["usd"])
    total = sum((v["usd"] for v in spend_rows), 0.0)
    if not exact:
        total = round(total, 6)
        for s_row in spend_rows:
            s_row["usd"] = round(s_row["usd"], 6)
    return {
        "schema": USAGE_SCHEMA,
        "days": days,
        "agents": sorted(by_agent.values(), key=lambda a: -a["calls"]),
        "models": [by_model[m] for m in sorted(by_model)],
        "model_errors": sum(1 for r in rows if r.get("event") == "model_error"),
        "spend": {"total_usd": total, "rows": spend_rows, "unpriced": sorted(unpriced)},
    }


def usage(days: int) -> str:
    data = usage_data(days, exact=True)
    spend = data["spend"]
    if not data["agents"] and not spend["rows"]:
        return f"No activity recorded in the last {days} days."
    lines = [f"Subagent usage, last {days} days", "",
             "| Agent | Calls | Model set by router | Levels | Ran on |", "| :- | -: | -: | :- | :- |"]
    for a in data["agents"]:
        levels = ", ".join(f"{level} x{n}" for level, n in a["levels"].items()) or "-"
        ran = ", ".join(f"{m} x{n}" for m, n in sorted(a["ran_on"].items())) or "-"
        lines.append(f"| {a['agent']} | {a['calls']} | {a['routed']} | {levels} | {ran} |")
    if data["models"]:
        lines += ["", "| Model | Agent runs | Final-request tokens | Tokens over the runs |", "| :- | -: | -: | -: |"]
        lines += [f"| {m['model']} | {m['runs']} | {m['tokens']:,} | "
                  f"{'-' if m['run_tokens'] is None else format(m['run_tokens'], ',')} |" for m in data["models"]]
    if data["model_errors"]:
        lines += ["", f"Agent calls that failed with a model error: {data['model_errors']}."]
    if spend["rows"]:
        lines += ["", f"Spend at list price: ${spend['total_usd']:,.2f}", "",
                  "| Loop | Model | Turns | Output tokens | USD |", "| :- | :- | -: | -: | -: |"]
        lines += [f"| {v['loop']} | {v['model']} | {v['turns']} | {v['output_tokens']:,} | {v['usd']:,.2f} |"
                  for v in spend["rows"]]
        if spend["unpriced"]:
            lines.append(f"\nNo price in the registry for: {', '.join(spend['unpriced'])}. "
                         "Add one under `prices` in ~/.claude/model-router.json.")
    else:
        lines += ["", "Dollar totals are in /usage and /cost; this ledger shows what was routed where."]
    return "\n".join(lines)
