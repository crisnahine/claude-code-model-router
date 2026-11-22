---
description: A high-risk, multi-service design question should route to an opus or fable agent.
tags: [routing]
max_turns: 6
allowed_tools: [Skill, Read, Glob, Grep]
---

Which model should handle this, and how would you split it up? We need to migrate session-based auth to OAuth2 across our API gateway, billing service and mobile backend, without logging anyone out, and the billing service stores tokens in Postgres.
