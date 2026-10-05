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

A primitive is judged by everything it reaches at run time, not only by what it
runs itself: one that can reach a shell or a local service breaks the invariant
as surely as one that runs it.

## What a plugin may use

- Claude Code's own primitives: agents, skills, commands, rules, and other
  plugins that themselves satisfy this rule.
- Hooks whose handler reaches neither a shell nor anything beyond Claude Code on
  the user's machine, such as a `prompt` hook, an `agent` hook (Claude Code
  documents its verifier as using tools like Read, Grep and Glob), an `http`
  hook whose URL is a remote endpoint, and an `mcp_tool` hook calling a server
  declared in the plugin's own `.mcp.json` that needs no local install.
- Claude Code's built-in file, search and web tools.
- MCP servers declared in the plugin's `.mcp.json` that need no local install.
  A remote `http` server is the default. Who operates it does not matter, and a
  sign-in the server asks for is not an install.

## What it may not

- **Anything that runs a command or a local binary on the user's machine, or
  calls a service that must be running there.** A shipped script an agent is
  told to execute, a `command` hook, a monitor, an executable under `bin/`, an
  LSP server, a local `stdio` MCP server, an `http` hook or server pointed at
  `localhost` — each needs something on the machine beyond Claude Code. An
  `mcp_tool` hook calling a server the plugin does not declare is out too: it
  works only where the user has set that server up, a step the plugin never
  states.
- **A shell command as a step the plugin needs in order to work**, whether an
  agent runs it or the user is asked to. Claude Code's setup documentation
  (<https://code.claude.com/docs/en/setup>) makes Git for Windows optional and
  falls back to PowerShell without it, so even a command naming only POSIX
  utilities fails on a supported platform. A generator's "run … to regenerate"
  comment and optional advice to the user, such as reviewing a result with
  `git diff`, are not such steps.
- **An agent that can reach a shell**, among those the plugin defines or
  dispatches. A granted shell lets the model improvise the command the prose is
  barred from writing, and a grant that can start another agent hands over that
  agent's tools. So every plugin agent declares `tools:`, since an agent without
  it inherits every tool; no tool grant — an agent's `tools:`, a skill's or
  command's `allowed-tools` — includes a tool that runs a shell command, such as
  `Bash`, `PowerShell` or `Monitor`; no plugin agent's `tools:` includes a tool
  that starts another agent, such as `Agent` or `Task`, because Claude Code
  documents that listing `Agent` in a subagent's `tools` allows spawning any
  subagent type; and plugin prose dispatches only agents the plugin defines,
  never a built-in agent type that has a shell, such as `general-purpose`. A
  skill's `allowed-tools` pre-approves tools rather than restricting them, so
  keeping a shell out of it is not a sandbox; the ban on shell steps carries the
  rest.

## No exception path

The ban is absolute: no ADR, README or install step admits a runtime dependency.
A capability built-in tools cannot provide is split — a remote MCP tool returns
what they cannot compute, and built-in tools act on it locally. A remote server
cannot write the user's disk; it returns a file's contents and `Write` lands
them.

## Not covered

Portability only. Where data is sent is a separate question: a permitted `http`
hook or third-party server is not thereby approved as a destination for the
user's data.

## Scope

The plugin runtime only. The packages, tests, scripts and CI at the repo root are
contributor tooling; they may require Python and stay out of `plugins/<name>/`
under the root `CLAUDE.md` ("Trees nothing here may author").

## Why

A plugin is installed by users on machines this repo does not control. Every
runtime it assumes is a failure on the machine that lacks it, and one more thing
to install before the plugin does anything. A requirement nobody sees until it
fails is worse than one stated up front, and both are worse than none.
