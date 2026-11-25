---
description: A lookup redone for a gap in its report keeps its description with "Redo:" in front. The first redo runs Haiku one effort step higher, at high, and the second, redoing a run at high, moves to Sonnet. Without the plugin nothing marks the redos and every run stays on the Haiku session's model.
expected_outcome: "model haiku, effort medium"
tags: [routing, redo]
model: haiku
max_turns: 8
allowed_tools: [Agent, Read, Glob]
---

Use the Agent tool with subagent_type general-purpose, description "list files" and this exact prompt: "List the files in the current directory and reply with their names only." When its answer is back, take it as a report with a gap: it gives no file sizes. Redo that call so the report gives each file's size, and wait for that answer too. Take that answer as a report with a gap as well: it gives no modification times. Redo the call once more so the report gives each file's size and modification time, and wait for that answer. Then reply with all three answers.
