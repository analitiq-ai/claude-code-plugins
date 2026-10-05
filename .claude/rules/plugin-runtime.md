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
`npx`, `gh`, `jq`, or anything like them — and a plugin may ship no script or
executable for an agent, a hook or the user to run.

The test for a step, wherever it is written — agent or skill prose, a hook,
`.mcp.json` — is what the user would have to install for it to work on any OS
Claude Code supports. If the answer is nothing, the step is fine.

## What a plugin may use

Whatever ships with Claude Code:

- Its primitives: agents, skills, commands, rules, hooks, and other plugins
  that themselves satisfy this rule.
- Its built-in tools, the shell tool among them, in any tool grant.
- Its built-in agent types, such as `general-purpose`, dispatched by plugin
  prose or by a plugin agent.

And MCP servers declared in the plugin's `.mcp.json` that need no local
install. A remote `http` server is the default. Who operates it does not
matter, and a sign-in the server asks for is not an install.

## What it may not

- **Ship a script or an executable.** A `.py`, `.sh` or other program file
  under `plugins/<name>/` that an agent, a hook or the user is told to run, or
  an executable under `bin/`. What it did moves into agent prose and built-in
  tools, or behind a remote MCP tool.
- **Need anything the user installs.** A step that invokes a program the user
  has to install, such as `python3`, `gh` or `jq`; a local `stdio` MCP server,
  which needs a runtime on the machine; an LSP server; an `http` hook or server
  pointed at `localhost`; a hook or a step calling an MCP server the plugin
  does not declare, which works only where the user has set that server up.

A generator's "run … to regenerate" comment and optional advice to the user,
such as reviewing a result with `git diff`, are not steps the plugin needs.

## No exception path

The ban is absolute: no ADR, README or install step admits a runtime dependency.
A capability built-in tools cannot provide is split — a remote MCP tool returns
what they cannot compute, and built-in tools act on it locally. A remote server
cannot write the user's disk; it returns a file's contents and `Write` lands
them.

## Not covered

- **Which shell a command is written for.** The shell tool ships with Claude
  Code, but the shell behind it differs by machine: Claude Code's setup
  documentation (<https://code.claude.com/docs/en/setup>) makes Git for Windows
  optional and uses PowerShell without it. A command that needs only a shell
  and the utilities that come with it is not graded on which shell that is. An
  author who wants a step to work under either states what the step must
  achieve, or uses the built-in file and search tools.
- **Where data is sent.** A permitted `http` hook or third-party server is not
  thereby approved as a destination for the user's data.

## Scope

The plugin runtime only. The packages, tests, scripts and CI at the repo root are
contributor tooling; they may require Python and stay out of `plugins/<name>/`
under the root `CLAUDE.md` ("Trees nothing here may author").

## Why

A plugin is installed by users on machines this repo does not control. Every
runtime it assumes is a failure on the machine that lacks it, and one more thing
to install before the plugin does anything. A requirement nobody sees until it
fails is worse than one stated up front, and both are worse than none.
