---
description: From a Haiku session, a security-review subagent should run on Opus at high effort. Without the router it inherits Haiku.
expected_outcome: "level complex, model opus, effort high"
tags: [routing, quality]
model: haiku
max_turns: 4
allowed_tools: [Agent, Read]
---

Use the Agent tool once, with subagent_type general-purpose, description "review login" and this exact prompt: "Review this login handler for security vulnerabilities in its authentication check, and say in two sentences what is wrong: def login(user, password): return user == 'admin' or password == stored_password(user)". Don't name a model on the call. Then reply with its answer.
