# Data model: Track the plugin-runtime rule

No data shapes, persistence or state. The "entities" are the two tracked files the PR changes.

## Rule file — `.claude/rules/plugin-runtime.md`

| Part | Carries | Requirement |
|---|---|---|
| Frontmatter `paths:` | `plugins/**` | FR-001 (R2) |
| Title + invariant | Runs on every OS Claude Code supports, on a machine with Claude Code and nothing else. No step installs a runtime, package manager, CLI or library | FR-002, FR-003 |
| What a plugin may use | Claude Code primitives; other plugins that satisfy the rule; hooks that reach neither a shell nor anything beyond Claude Code on the user's machine (an `http` hook only to a remote URL, an `mcp_tool` hook only to a declared server); built-in file, search and web tools; `.mcp.json` servers that need no local install | FR-002, FR-011 (R3) |
| MCP servers | Remote `http` is the default; a third-party operator is fine; a sign-in is not an install; `stdio` is out | FR-005 (R5) |
| What it may not | Anything that runs a command or a local binary on the user's machine or calls a service that must run there (examples: shipped script, `command` hook, monitor, `bin/` executable, LSP server, `http` hook to `localhost`, `mcp_tool` hook to an undeclared server); a shell command as a step the plugin needs to work, with the platform-shell reason; any agent the plugin defines or dispatches that can run a shell — so every agent declares `tools:`, no grant includes a shell tool, no agent's `tools:` includes a tool that starts another agent, no built-in agent type is dispatched | FR-004, FR-011, FR-012 (R3, R4, R10) |
| No exception path | Absolute: no ADR or README install step admits a dependency; a capability built-in tools cannot provide is split — a remote MCP tool returns what they cannot compute, built-in tools act locally | FR-010 (R5) |
| Not covered | Portability only, not data egress | FR-013 (R9) |
| Scope | Plugin runtime only; repo-root packages, tests, scripts, CI are contributor tooling | FR-006 |
| Why | Users' machines are not controlled by this repo | rationale |

Constraint on every sentence: FR-009 (`resolvable-referents.md`, `no-cardinality-restatements.md`,
`engine-behaviour-claims.md`).

## Index entry — root `CLAUDE.md` Rules section

One bullet, already committed: `` `plugin-runtime.md` — before making a plugin depend on anything
Claude Code does not provide: a script, a shell command, a CLI, a local MCP server. `` (FR-007, R6)

## Relationships

- `rules-audit` and the native loader read the frontmatter → the rule reaches anyone editing
  `plugins/**`.
- The local constitution's R14 cites the rule after merge (R8).
- The follow-up compliance PR is graded by this rule. It is not part of this PR (FR-008).
