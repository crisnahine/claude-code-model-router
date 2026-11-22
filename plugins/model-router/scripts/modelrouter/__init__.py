"""Model Router plugin runtime. Standard library only; runs on Python 3.9+.

    store      state files, locking and the ledger
    config     the registry file, user and project overrides, and the plugin's options
    registry   which models exist, which are usable here, and how to pick one
    agents     finds an agent's definition, wherever it comes from
    profile    scores a request or a subtask from its wording and its agent
    ladder     turns a level, the priority and learned raises into a model and an effort
    router     decides the model and effort for one agent call
    learn      feedback: raises a level when routed work keeps being redone stronger
    gates      checks before a turn ends
    upstream   Claude Code releases, docs and model aliases
    report     the tables the skills print
    hooks      one handler per hook event
    cli        the command line
"""
