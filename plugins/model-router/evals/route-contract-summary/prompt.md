---
description: From a Haiku session, Claude names Opus for a contract summary, as the session note gives a contract. Without the plugin it inherits Haiku unless Claude names a model on its own.
expected_outcome: "model opus, effort high"
tags: [routing, quality, any-work]
model: haiku
max_turns: 4
allowed_tools: [Agent]
---

Use the Agent tool once, with subagent_type general-purpose, description "summarize contract" and this exact prompt: "Summarize the term, termination and liability clauses of this services agreement in three bullets, and flag anything unusual. 1. Term: twelve months from the start date, renewing for another twelve months unless either party gives 90 days' written notice. 2. Termination: the Supplier may terminate at any time without notice; the Customer may terminate only for a material breach the Supplier leaves uncured for 60 days. 3. Liability: the Supplier's total liability under this agreement is limited to $10, and the Customer's liability is unlimited. 4. Fees: payable monthly in advance." Then reply with its answer.
