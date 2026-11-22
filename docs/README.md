# Documentation index

Every delivery item the project keeps, where it lives, and whether it exists yet. Checked 2026-11-22.

| # | Item | Where | Exists |
| -: | :- | :- | :- |
| 1 | Source code and architecture | [ARCHITECTURE.md](ARCHITECTURE.md), [GLOSSARY.md](../GLOSSARY.md) | Yes |
| 2 | Capabilities and commands | [README.md](../README.md) "Commands", "Configuration" | Yes |
| 3 | Primary-source research | [research/model-control.md](research/model-control.md) | Yes |
| 4 | Supported Claude Code versions, surfaces, platforms and providers | [README.md](../README.md) "Compatibility", [ARCHITECTURE.md](ARCHITECTURE.md) "Verified live" | Yes |
| 5 | Install and usage | [README.md](../README.md) "Install" | Yes |
| 6 | Automated test results | [TEST_RESULTS.md](TEST_RESULTS.md) | Yes |
| 7 | Security assessment | [SECURITY.md](SECURITY.md) | Yes |
| 8 | Reproducible cost and quality results, with raw data | [BENCHMARKS.md](BENCHMARKS.md), raw data under [bench/results/](../bench/results/) | Yes, a pilot only |
| 9 | Hook latency and state-size measurements | [PERFORMANCE.md](PERFORMANCE.md) | Yes |
| 10 | Remaining defects and incomplete requirements | [RELEASE_READINESS.md](RELEASE_READINESS.md) "Remaining defects and incomplete requirements" | Yes |
| 11 | Release-readiness assessment | [RELEASE_READINESS.md](RELEASE_READINESS.md) | Yes |
| 12 | Roadmap | [ROADMAP.md](ROADMAP.md) "Next" | Yes |
| 13 | Comparison against the baselines | [BENCHMARKS.md](BENCHMARKS.md) | Yes, a pilot only |
| 14 | Release checklist and changelog | [RELEASING.md](../RELEASING.md), [CHANGELOG.md](../CHANGELOG.md) | Yes |

Also kept:

- [TRACEABILITY.md](TRACEABILITY.md): one row per requirement with its implementation, source, verification, evidence and status. `python3 tools/traceability.py` counts it.
- [CONTRIBUTING.md](../CONTRIBUTING.md): gates, commit rules and where to work.
- [compat/baseline.json](../compat/baseline.json): the Claude Code version, docs index, model aliases and model status last reviewed.
