---
description: From a Sonnet session, a file-listing subagent should run on Haiku. Without the router it inherits Sonnet.
expected_outcome: "level lookup, model haiku, effort medium"
tags: [routing, cost]
model: sonnet
max_turns: 4
allowed_tools: [Agent, Read, Glob]
---

Use the Agent tool once, with subagent_type general-purpose, description "list files" and this exact prompt: "List the files in the current directory and reply with their names only." Don't name a model on the call. Then reply with what it found.
