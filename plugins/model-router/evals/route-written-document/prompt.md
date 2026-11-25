---
description: From a Sonnet session, Claude names Haiku at effort high for a memo written to a brief, the session note's row for writing documents. Without the plugin the subagent runs on the session's Sonnet.
expected_outcome: "model haiku, effort high"
tags: [routing, quality, any-work]
model: sonnet
max_turns: 4
allowed_tools: [Agent]
---

Use the Agent tool once, with subagent_type general-purpose, description "write contract memo" and this exact prompt: "Write a memo for the Customer's counsel on this services agreement, to this brief: three short paragraphs, one each on the term, termination and liability clauses, in plain words, each ending with what to ask the Supplier to change. 1. Term: twelve months from the start date, renewing for another twelve months unless either party gives 90 days' written notice. 2. Termination: the Supplier may terminate at any time without notice; the Customer may terminate only for a material breach the Supplier leaves uncured for 60 days. 3. Liability: the Supplier's total liability under this agreement is limited to $10, and the Customer's liability is unlimited. 4. Fees: payable monthly in advance." Then reply with its answer.
