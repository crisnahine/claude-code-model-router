<!-- Page head, written by tools/docs_assets.py: edit PAGES in tools/docs_pages.py, then run it. -->
<p align="center"><a href="../README.md">Model Router</a> &nbsp;&middot;&nbsp; <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp; Previous: <a href="TESTING.md">Testing</a> &nbsp;&middot;&nbsp; Next: <a href="../CHANGELOG.md">Changelog</a></p>
<!-- End of page head. -->

# Roadmap

What isn't done yet.

## Next

- Run the judged benchmark round on this release and record it in [BENCHMARKS.md](BENCHMARKS.md).
- Add a benchmark task that the cheapest model fails, so the benchmark can show what the redo step is worth.
- Run a live session on Linux and on Windows. CI runs the unit and mod tests there.
- Run it live under the Agent SDK.

## Later

- Run it live on Amazon Bedrock, Vertex AI and Microsoft Foundry, and with an API-key login.
- Check the Desktop app's window and the VS Code panel by hand, and the other cases [TESTING.md](TESTING.md#the-interactive-features-by-hand) lists as not run by hand.
