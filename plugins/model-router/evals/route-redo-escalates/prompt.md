---
description: A lookup redone twice for gaps in its report, each call naming Haiku at medium and each redo keeping the first call's description with "Redo:" in front. The first redo runs Haiku one effort step higher, at high, and the second, redoing a run at high, moves to Sonnet. Without the plugin every call runs as named, on Haiku at medium.
expected_outcome: "model haiku, effort medium"
tags: [routing, redo]
model: haiku
max_turns: 8
allowed_tools: [Agent, Read, Glob]
---

Make three Agent calls, one after another, each with subagent_type general-purpose and the description, model and effort given for it, word for word. First call: description "list files", model "haiku", effort "medium" and this exact prompt: "List the files in the current directory and reply with their names only." When its answer is back, take it as a report with a gap: it gives no file sizes. Second call, a redo of the first: description "Redo: list files", model "haiku", effort "medium", and a prompt asking for each file's name and size. Wait for that answer too, and take it as a report with a gap as well: it gives no modification times. Third call, a redo once more: description "Redo: list files", model "haiku", effort "medium", and a prompt asking for each file's name, size and modification time. Then reply with all three answers.
