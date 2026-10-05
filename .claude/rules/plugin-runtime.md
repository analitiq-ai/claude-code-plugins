---
paths:
  - "plugins/**"
---

# Rule: a plugin needs nothing Claude Code does not provide

Governs everything under `plugins/<name>/` — agents, skills, commands, hooks,
`.mcp.json`, and any file shipped beside them.

**The invariant:** a plugin runs on every OS Claude Code supports, on a machine
that has Claude Code and nothing else. No step asks the user to install a
language runtime, package manager, CLI or library — Python, `uv`, `pip`, Node,
`npx`, `gh`, `jq`, or anything like them.

## What a plugin may use

- Claude Code's own primitives: agents, skills, commands, rules, other plugins.
- Claude Code's built-in tools that do not hand off to a shell: file read, write,
  edit, glob, grep, and web fetch.
- MCP servers declared in the plugin's `.mcp.json` that need no local install.
  A remote `http` server is the default; a local `stdio` server needs a runtime
  on the user's machine and is therefore out.

## What it may not

- **A shipped script.** A file a plugin tells an agent to execute needs an
  interpreter Claude Code does not provide. Logic a script would carry belongs in
  agent or skill prose, a built-in tool, or a tool on the plugin's MCP server.
- **A shell command in prose.** The shell itself is not portable: on Windows
  Claude Code runs Git Bash when it is installed and PowerShell when it is not,
  so even a command naming only POSIX utilities fails on a supported platform.
  An agent that needs no shell command does not list `Bash` in its `tools:`.

## Scope

The plugin runtime only. The packages, tests, scripts and CI at the repo root are
contributor tooling; they may require Python and stay out of `plugins/<name>/`
under the root `CLAUDE.md` ("Trees nothing here may author").

## Why

A plugin is installed by users on machines this repo does not control. Every
runtime it assumes is a failure on the machine that lacks it, and one more thing
to install before the plugin does anything. A requirement nobody sees until it
fails is worse than one stated up front, and both are worse than none.
