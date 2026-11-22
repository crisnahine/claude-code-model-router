"""The tables the skills print. Skills run through the Bash tool, so the settings passed in
fall back to what the last SessionStart recorded."""
from __future__ import annotations

from . import agents, ladder, learn, registry, store, upstream
from . import config as cfg
from .profile import profile_call, profile_prompt
from .settings import Settings


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


def models(config: dict, settings: Settings) -> str:
    table = registry.health()
    limit = settings.cap
    usable = _usable(config, settings)
    allowed = registry.permitted(config, cfg.project_dir())
    lines = [f"Cap: **{limit or 'none'}**. Strongest model in use: **{usable[-1] if usable else 'none'}**. "
             f"Priority: **{settings.priority}**.",
             "", "| Model | Last ran as | Status |", "| :- | :- | :- |"]
    for name in registry.by_strength(config):
        info = table.get(name) if isinstance(table.get(name), dict) else {}
        if limit and registry.strength(config, name) > registry.strength(config, limit):
            status = "above your cap"
        elif limit and name not in cfg.trusted_models(config):
            status = "added by this project, so not used while a cap is set"
        elif name not in allowed:
            status = "not permitted by the availableModels or deniedModels setting"
        elif not registry.healthy(name, table):
            why = (f"ran as {info.get('substituted_by')} instead" if info.get("substituted")
                   else str(info.get("last_error") or "calls failed with a model error"))
            status = f"routed around until retried ({why})"
        elif info.get("last_model"):
            status = "working"
        else:
            status = "not used yet"
        lines.append(f"| {name} | {info.get('last_model') or '-'} | {status} |")
    learned = learn.stats()
    rows = [(level, learned[level]) for level in cfg.LEVELS if isinstance(learned.get(level), dict)]
    if rows:
        lines += ["", "| Subtask level | Routed calls | Votes it was too low | Raised |", "| :- | -: | -: | :- |"]
        lines += [f"| {level} | {info.get('calls', 0)} | {info.get('votes', 0)} | "
                  f"{'yes' if info.get('bump') else 'no'} |" for level, info in rows]
    aliases = (upstream.load().get("cache") or {}).get("aliases") or []
    if aliases:
        lines += ["", "Model aliases in the current Claude Code docs: " + ", ".join(aliases) + "."]
        new = [f for f in upstream.model_families(aliases) if f not in registry.models(config)]
        if new:
            lines.append("Not in the registry yet, so not routed to: " + ", ".join(new) + ".")
    aside = store.set_aside()
    if aside:
        lines += ["", "Set aside because it could not be read, so that state started fresh: "
                  + ", ".join(str(p) for p in aside) + ". Look at them, then delete them."]
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


def usage(days: int) -> str:
    rows = store.read_ledger(days)
    spawns = [r for r in rows if r.get("event") == "spawn"]
    results = [r for r in rows if r.get("event") == "result"]
    if not spawns and not results and not any(r.get("event") == "usage" for r in rows):
        return f"No activity recorded in the last {days} days."
    by_agent: dict[str, dict] = {}

    def row(agent: object) -> dict:
        return by_agent.setdefault(str(agent or "?"), {"calls": 0, "routed": 0, "levels": {}, "models": {}})

    for r in spawns:
        a = row(r.get("agent"))
        a["calls"] += 1
        a["routed"] += r.get("action") == "rewrite"
        if r.get("level"):
            a["levels"][r["level"]] = a["levels"].get(r["level"], 0) + 1
    by_model: dict[str, dict] = {}
    for r in results:
        model = str(r.get("resolved_model") or "background (not reported)")
        a = row(r.get("agent"))
        a["models"][model] = a["models"].get(model, 0) + 1
        m = by_model.setdefault(model, {"runs": 0, "tokens": 0})
        m["runs"] += 1
        m["tokens"] += r.get("total_tokens") if isinstance(r.get("total_tokens"), int) else 0
    lines = [f"Subagent usage, last {days} days", "",
             "| Agent | Calls | Model set by router | Levels | Ran on |", "| :- | -: | -: | :- | :- |"]
    for agent, a in sorted(by_agent.items(), key=lambda kv: -kv[1]["calls"]):
        levels = ", ".join(f"{level} x{n}" for level, n in a["levels"].items()) or "-"
        ran = ", ".join(f"{m} x{n}" for m, n in sorted(a["models"].items())) or "-"
        lines.append(f"| {agent} | {a['calls']} | {a['routed']} | {levels} | {ran} |")
    if by_model:
        lines += ["", "| Model | Agent runs | Tokens |", "| :- | -: | -: |"]
        lines += [f"| {m} | {v['runs']} | {v['tokens']:,} |" for m, v in sorted(by_model.items())]
    errors = sum(1 for r in rows if r.get("event") == "model_error")
    if errors:
        lines += ["", f"Agent calls that failed with a model error: {errors}."]
    spend: dict[tuple[str, str], dict] = {}
    unpriced = set()
    for r in rows:
        if r.get("event") != "usage":
            continue
        who = "subagent" if r.get("agent_id") else "main"
        s_row = spend.setdefault((who, str(r.get("model"))), {"turns": 0, "usd": 0.0, "out": 0})
        s_row["turns"] += 1
        s_row["out"] += int(r.get("output_tokens") or 0)
        if isinstance(r.get("cost_usd"), (int, float)):
            s_row["usd"] += r["cost_usd"]
        else:
            unpriced.add(str(r.get("model")))
    if spend:
        total = sum(v["usd"] for v in spend.values())
        lines += ["", f"Spend at list price: ${total:,.2f}", "",
                  "| Loop | Model | Turns | Output tokens | USD |", "| :- | :- | -: | -: | -: |"]
        lines += [f"| {who} | {m} | {v['turns']} | {v['out']:,} | {v['usd']:,.2f} |"
                  for (who, m), v in sorted(spend.items(), key=lambda kv: -kv[1]["usd"])]
        if unpriced:
            lines.append(f"\nNo price in the registry for: {', '.join(sorted(unpriced))}. Add one under `prices` in ~/.claude/model-router.json.")
    else:
        lines += ["", "Dollar totals are in /usage and /cost; this ledger shows what was routed where."]
    return "\n".join(lines)
