---
description: From a Haiku session, a design question that names no agent goes to the plugin's architect on Sonnet, as the agent's description and the session note give a design. Without the plugin there is no architect, and the subagent inherits Haiku unless Claude names a model on its own.
expected_outcome: "agent model-router:architect, model sonnet, effort high"
tags: [routing, delegation]
model: haiku
max_turns: 4
allowed_tools: [Agent, Read]
---

Have a subagent compare two designs for rate limiting requests across three web services that share one Redis instance, and recommend one in five bullets. Then reply with its recommendation.
