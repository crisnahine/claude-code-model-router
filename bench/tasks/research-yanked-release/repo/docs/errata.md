# Errata

- The 2.4.0 notes list `timeout=` for `Client.stream()`. That change was reverted before the release and shipped in 2.4.2 instead; in 2.4.0 and 2.4.1, `Client.stream()` raises TypeError for `timeout=`.
