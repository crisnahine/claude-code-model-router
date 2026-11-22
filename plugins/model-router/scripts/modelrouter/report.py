"""The tables the skills print. Skills run through the Bash tool, so the settings passed in
fall back to what the last SessionStart recorded."""
from __future__ import annotations

from . import agents, ladder, learn, registry, store, upstream
from . import config as cfg
from .profile import profile_call, profile_prompt
from .settings import Settings

# The --json shapes. A new key keeps the version; removing or renaming one bumps it.
USAGE_SCHEMA = "model-router.usage/1"
MODELS_SCHEMA = "model-router.models/1"


def _usable(config: dict, settings: Settings) -> list[str]:
    return registry.available(config, cfg.project_dir(), settings.cap)


def policy(config: dict, settings: Settings) -> str:
    usable = _usable(config, settings)
    limit = settings.cap
    lines = [f"Routing: **{settings.mode}**. Priority: **{settings.priority}**. "
             f"Cap: **{limit or 'none'}**. Strongest model in use: **{usable[-1] if usable else 'none'}**.", "",
             "| Subtask level | Model | Effort |", "| :- | :- | :- |"]
    raises = learn.raises()
    for level in cfg.LEVELS:
        pick = ladder.pick(config, level, settings.priority, usable, raises)
        note = " (raised by feedback)" if raises[level] else ""
        lines.append(f"| {level} | {pick.model}{note} | {pick.effort or 'as the agent or session sets it'} |")
    lines += ["", "| Model | Strength | Speed | Cost | Use for |", "| :- | -: | -: | -: | :- |"]
    for name in registry.by_strength(config):
        info = registry.models(config)[name]
        flag = "" if name in usable else " (not in use)"
        lines.append(f"| {name}{flag} | {info.get('strength')} | {info.get('speed', '-')} | "
                     f"{info.get('cost', '-')} | {info.get('use_for', '')} |")
    lines += ["", "Strength, speed and cost are relative ranks, not prices.", "",
              "| Role agent | Model | Role |", "| :- | :- | :- |"]
    for name, agent in sorted((config.get("crew") or {}).items(),
                              key=lambda kv: registry.strength(config, kv[1].get("model"))):
        model = agent.get("model")
        runs = registry.clamp(config, model, usable) if model in registry.models(config) else model
        shown = model if runs == model else f"{runs} (lowered from {model})"
        lines.append(f"| {cfg.PLUGIN_NAME}:{name} | {shown} | {agent.get('role', '')} |")
    return "\n".join(lines)


def models_data(config: dict, settings: Settings) -> dict:
    """Each registry model's state here, in the `model-router.models/1` shape."""
    table = registry.health()
    limit = settings.cap
    usable = _usable(config, settings)
    allowed = registry.permitted(config, cfg.project_dir())
    rows = []
    for name in registry.by_strength(config):
        info = table.get(name) if isinstance(table.get(name), dict) else {}
        if limit and registry.strength(config, name) > registry.strength(config, limit):
            status, detail = "above_cap", "above your cap"
        elif limit and name not in cfg.trusted_models(config):
            status, detail = "project_model_under_cap", "added by this project, so not used while a cap is set"
        elif name not in allowed:
            status, detail = "not_permitted", "not permitted by the availableModels or deniedModels setting"
        elif not registry.healthy(name, table):
            why = (f"ran as {info.get('substituted_by')} instead" if info.get("substituted")
                   else str(info.get("last_error") or "calls failed with a model error"))
            status, detail = "routed_around", f"routed around until retried ({why})"
        elif info.get("last_model"):
            status, detail = "working", "working"
        else:
            status, detail = "not_used", "not used yet"
        rows.append({"model": name, "last_ran_as": info.get("last_model") or None, "status": status, "detail": detail})
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
        "priority": settings.priority,
        "models": rows,
        "levels": levels,
        "doc_aliases": aliases,
        "not_in_registry": [f for f in upstream.model_families(aliases) if f not in registry.models(config)],
    }


def models(config: dict, settings: Settings) -> str:
    data = models_data(config, settings)
    lines = [f"Cap: **{data['cap'] or 'none'}**. Strongest model in use: **{data['strongest_in_use'] or 'none'}**. "
             f"Priority: **{data['priority']}**.",
             "", "| Model | Last ran as | Status |", "| :- | :- | :- |"]
    lines += [f"| {m['model']} | {m['last_ran_as'] or '-'} | {m['detail']} |" for m in data["models"]]
    if data["levels"]:
        lines += ["", "| Subtask level | Routed calls | Votes it was too low | Raised |", "| :- | -: | -: | :- |"]
        lines += [f"| {lv['level']} | {lv['routed_calls']} | {lv['votes_too_low']} | "
                  f"{'yes' if lv['raised'] else 'no'} |" for lv in data["levels"]]
    if data["doc_aliases"]:
        lines += ["", "Model aliases in the current Claude Code docs: " + ", ".join(data["doc_aliases"]) + "."]
        if data["not_in_registry"]:
            lines.append("Not in the registry yet, so not routed to: " + ", ".join(data["not_in_registry"]) + ".")
    return "\n".join(lines)


def explain(config: dict, settings: Settings, text: str, agent: str | None = None) -> str:
    """What the router would make of this task, and why."""
    usable = _usable(config, settings)
    result = profile_call({"prompt": text}, agent) if agent else profile_prompt(text)
    level = result["level"]
    pick = ladder.pick(config, level, settings.priority, usable, learn.raises())
    why = ", ".join(result["signals"]) or "no risk, scope or ambiguity wording"
    out = [f"Level: **{level}** ({why}).",
           f"Model: **{pick.model}**, effort: {pick.effort or 'unchanged'}.",
           f"Priority {settings.priority}; models in use: {', '.join(usable)}."]
    if agent:
        definition = agents.find(agent, cfg.project_dir())
        pinned = (definition or {}).get("model")
        if definition is None:
            out.append(f"The definition of `{agent}` wasn't found, so its calls are left alone.")
        elif pinned and settings.pins == "respect":
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
    rows = store.read_ledger(days)
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
            m = by_model.setdefault(model, {"model": model, "runs": 0, "tokens": 0})
            m["runs"] += 1
            m["tokens"] += r.get("total_tokens") if isinstance(r.get("total_tokens"), int) else 0
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
        lines += ["", "| Model | Agent runs | Tokens |", "| :- | -: | -: |"]
        lines += [f"| {m['model']} | {m['runs']} | {m['tokens']:,} |" for m in data["models"]]
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
