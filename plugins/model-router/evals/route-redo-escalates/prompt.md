---
description: A lookup redone for a gap in its report keeps its description with "Redo:" in front, and the redo runs one step higher, on Sonnet. Without the plugin nothing marks the redo and both runs stay on the Haiku session's model.
expected_outcome: "model haiku, effort medium"
tags: [routing, redo]
model: haiku
max_turns: 6
allowed_tools: [Agent, Read, Glob]
---

Use the Agent tool with subagent_type general-purpose, description "list files" and this exact prompt: "List the files in the current directory and reply with their names only." When its answer is back, take it as a report with a gap: it gives no file sizes. Redo that call so the report gives each file's size, and wait for that answer too. Then reply with both answers.
