---
description: The same lookup issued twice in one request should run the second time one level higher, on Sonnet. Without the router both runs stay on the Haiku session's model.
expected_outcome: "level lookup, model haiku, effort medium"
tags: [routing, redo]
model: haiku
max_turns: 6
allowed_tools: [Agent, Read, Glob]
---

Use the Agent tool with subagent_type general-purpose, description "list files" and this exact prompt: "List the files in the current directory and reply with their names only." Don't name a model on the call. When its answer is back, make exactly the same Agent call again, with the same description and prompt, and wait for that answer too. Then reply with both answers.
