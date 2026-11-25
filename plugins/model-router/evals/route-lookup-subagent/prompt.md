---
description: From a Sonnet session, Claude names Haiku for a file-listing subagent, as the session note gives finding and listing. Without the plugin it inherits Sonnet unless Claude names a model on its own.
expected_outcome: "model haiku, effort medium"
tags: [routing, cost]
model: sonnet
max_turns: 4
allowed_tools: [Agent, Read, Glob]
---

Use the Agent tool once, with subagent_type general-purpose, description "list files" and this exact prompt: "List the files in the current directory and reply with their names only." Then reply with what it found.
