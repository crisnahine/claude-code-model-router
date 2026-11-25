<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="TESTING.md">Testing</a> &nbsp;&middot;&nbsp; Next: <a href="../CHANGELOG.md">Changelog</a></p>
<!-- End of page head. -->

# Roadmap

What isn't done yet.

## Next

- Run benchmark rounds on this release and publish them in [BENCHMARKS.md](BENCHMARKS.md). None is recorded yet.
- Run the plugin live on a Linux install and a Windows install. CI runs the unit and mod tests there, but no live session has.
- Run it live on Amazon Bedrock, Vertex AI and Microsoft Foundry, and with an API-key login.

## Later

- Run it live in the Desktop app, VS Code, JetBrains and the Agent SDK.
- Run the benchmark's `workflow` shape (`workflow-two-stage-report`), which has no run yet, and read how the stream shows a workflow's agents. An agent-team shape can't run headless, since `claude -p` and the Agent SDK never start teammates.
