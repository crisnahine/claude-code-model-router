# Claude Code changelog

> Release notes for Claude Code, including new features, improvements, and bug fixes by version.

This page is generated from the [CHANGELOG.md on GitHub](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md). Longer releases are grouped by type of change, with long lists of improvements and bug fixes collapsed.

Run `claude --version` to check your installed version.

**2.1.331**

November 23, 2026

**Added**

* Added Code Mode support in terminal sessions on Amazon Bedrock, Google Cloud's Agent Platform, Microsoft Foundry and Claude Platform on AWS
* Added `oidc.groups_jsonpath` to the Claude apps gateway, to read groups from the matching entries of a list of objects with a small subset of JSONPath (RFC 9535)
* Added an API connectivity section to `claude doctor` that reports whether the API can be reached, with the HTTP status or the error, or says why it wasn't tested

**Changed**


**18 improvements**


**95 bug fixes**

**2.1.330**

November 20, 2026

**Added**

* Added sign-in to organizations managed in Anthropic's Enterprise Admin Console: after `claude auth login`, Claude Code applies the organization's settings and uses its cloud provider or gateway
* Added easel, a built-in mod that draws charts when asked and under MCP metrics results, on Pro, Max and, where available, Team and Enterprise on macOS and Linux; turn automatic charts off in `/config`

**Changed**


**14 improvements**


**98 bug fixes**

**2.1.329**

November 19, 2026

**Added**

* Added a trust dialog warning that says where a folder's hooks are declared
* Added a warning in the conversation, in Claude Desktop and other non-interactive hosts, when hooks in managed settings fail to load

**Changed**


**10 improvements**


**91 bug fixes**
