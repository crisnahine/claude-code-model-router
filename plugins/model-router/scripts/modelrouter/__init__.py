"""Model Router plugin runtime. Standard library only; runs on Python 3.9+.

    store      state files, locking and the ledger
    config     the registry file and user and project overrides
    settings   the plugin's options and the environment facts, resolved once per call
    allowlist  what availableModels, deniedModels and maxEffortLevel leave open
    registry   which models exist, which are usable here, and how to pick one
    health     model errors and substitutions, and which models are routed around
    agents     finds an agent's definition, wherever it comes from
    ladder     the levels: each one's model and effort, and the level a model stands for
    router     decides the model and effort for one agent call
    calls      what happened to each call: routed, waiting for a result, finished
    ledger     one writer per row kind, and reading the rows
    spawn      the one procedure both paths run for a spawn: facts, decision, ledger, records
    mod        the mod path: what hooks/router.ts forwards
    fallback   the Agent call path when no mod routes the spawn
    session    the conversation's own model, and reading its transcript
    intake     whether a prompt is the person's own request
    request    the model a person's prompt names
    gates      the check before a turn ends
    report     the tables the skills print
    hooks      one handler per hook event
    cli        the command line
"""
