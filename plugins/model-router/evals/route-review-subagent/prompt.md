---
description: From a Haiku session, Claude names Opus for a security-review subagent, as the session note gives a review. Without the plugin it inherits Haiku unless Claude names a model on its own.
expected_outcome: "model opus, effort high"
tags: [routing, quality]
model: haiku
max_turns: 4
allowed_tools: [Agent, Read]
---

Use the Agent tool once, with subagent_type general-purpose, description "review login" and this exact prompt: "Review this login handler for security vulnerabilities in its authentication check, and say in two sentences what is wrong: def login(user, password): return user == 'admin' or password == stored_password(user)". Then reply with its answer.
