"""Model Router plugin runtime. Standard library only; runs on Python 3.9+.

    store      state files, locking and the ledger
    config     the registry file and user and project overrides
    settings   the plugin's options and the environment facts, resolved once per call
    registry   which models exist, which are usable here, and how to pick one
    agents     finds an agent's definition, wherever it comes from
    profile    scores a request or a subtask from its wording and its agent
    ladder     turns a level, the priority and learned raises into a model and an effort
    router     decides the model and effort for one agent call
    calls      what happened to each call: routed, waiting for a result, finished
    ledger     one append per row kind, the turn Spend rule, and reading the rows
    spawn      the one procedure both paths run for a spawn: facts, decision, ledger, records
    learn      feedback: raises a level when routed work keeps being redone stronger
    gates      checks before a turn ends
    upstream   Claude Code releases, docs and model aliases
    report     the tables the skills print
    hooks      one handler per hook event
    cli        the command line
"""
