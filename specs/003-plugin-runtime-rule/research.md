# Research: Track the plugin-runtime rule

Claude Code facts below were read from its published docs on 2026-10-05: the tools reference,
the hooks reference and the plugin components reference at `code.claude.com/docs`.

## R1: Rewrite the committed rule once, from the spec

- **Decision**: Replace the body of `.claude/rules/plugin-runtime.md` (commit `45c34f20`) in one
  rewrite against FR-002–FR-013. Keep its frontmatter, title, invariant and "Why".
- **Rationale**: The committed file came before the spec's Clarifications and misses several of
  them: no absolute-ban clause and no MCP-tool escape route (FR-010), no hook clause (FR-011), a
  `Bash` clause conditional on "an agent that needs no shell command", where FR-012 says no grant
  at all and also covers skills' and commands' `allowed-tools`, nothing on third-party servers or
  sign-in (FR-005), and no search tools in the permitted list (FR-002). Patching each gap one at a
  time is the accretion `pr-review-loop` forbids. One rewrite from the settled spec is cheaper.
- **Existing tools**: The committed rule is the base; its invariant, scope and "Why" sections
  already meet FR-003, FR-006 and the rationale, so they are kept word for word wherever they
  still hold.

## R2: Load trigger is the `paths:` frontmatter

- **Decision**: `paths: ["plugins/**"]`, already in the committed file. No other trigger map.
- **Rationale**: Claude Code's native rules loader and the `rules-audit` skill both select rules
  from this frontmatter. A second map would be a drift surface (`no-drift-surfaces.md`). A
  diff touching only repo-root `tests/`, `scripts/` or `.github/` does not match, which meets US2
  scenario 2 and FR-006 without any extra wording.
- **Existing tools**: `rules-audit` (it selects rules by `paths:` and says never to add a
  file→rule map); sibling rules such as `plugin-prose.md` use the same form.

## R3: State what is forbidden by mechanism, not by listing component types

- **Decision**: The rule forbids any plugin component that **runs a command or a local binary on
  the user's machine**, and gives examples. It permits any hook whose handler **runs no shell**.
  Neither set is written out as a closed list.
- **Rationale**: Claude Code has more shell-running plugin components than the issue names.
  Command hooks, monitors (`monitors/monitors.json`, a background shell command), executables in
  `bin/` (put on the Bash tool's `PATH`), shipped scripts, LSP servers (`.lsp.json`, a local
  binary) and `stdio` MCP servers all run something locally. Hook handler types that run no shell
  are `prompt`, `agent`, `http` and `mcp_tool`. The spec's "(`prompt`, `agent`)" is a set of
  examples, not a closed list, and `http`/`mcp_tool` meet the same mechanism, so the rule permits
  them. A closed list of hook types or components would go stale as soon as Claude Code adds one
  (`no-cardinality-restatements.md`). The mechanism is what decides membership, and it stays true.
  Monitors, `bin/` and LSP follow from FR-002 ("only what Claude Code provides") and FR-004, so
  naming them as examples adds no new requirement (spec Assumptions).
- **Existing tools**: Claude Code hooks reference (handler types); plugin components reference
  (monitors, executables, LSP servers). None of them gives a portable way to run a command.

## R4: Shell-tool grants are a class, with `Bash` named

- **Decision**: No `tools:`, `allowed-tools` or other tool grant a plugin declares may include a
  tool that runs a shell command. The rule names `Bash` and gives `PowerShell` and `Monitor` as
  examples of the same class. The ban is stated by mechanism — no agent a plugin defines or
  dispatches can run a shell — so it also covers an agent with no `tools:` line (it inherits every
  tool) and a dispatched built-in agent type such as `general-purpose`. Every plugin agent
  therefore declares `tools:`, and prose dispatches only plugin-defined agents.
- **Rationale**: The tools reference marks `Bash`, `PowerShell` and `Monitor` as the tools that
  execute commands. Banning only `Bash` would leave the same hole open under another tool name, and banning only
  declared grants would leave it open through inheritance. The ban takes no search capability
  away: the tools reference documents that a subagent listing `Glob`/`Grep` and leaving out `Bash`
  has both. That is craft for writing a compliant agent, not part of the rule — it is a Claude Code
  default that can change without any sentence here going red — so it goes into the agent
  definitions and `contributing/<plugin>.md` the compliance PR touches. For skills the effect is weaker. A skill's `allowed-tools` pre-approves tools;
  it does not restrict them. Keeping a shell out of that grant stops pre-approved improvisation,
  and the prose ban (FR-004) carries the rest. The rule states this split so nobody reads
  `allowed-tools` as a sandbox.
- **Existing tools**: Agent `tools:` frontmatter (restricts); skill/command `allowed-tools`
  (pre-approves). No existing rule covers either one.

## R5: The escape route splits work between a remote MCP tool and built-in tools

- **Decision**: The ban is absolute. A capability built-in tools cannot provide is split: a remote
  MCP tool returns what they cannot compute, and built-in tools act on it locally. A remote server
  cannot write the user's disk, so "move it to the MCP server" alone is false for a step that
  changes local state. `registry-browser`'s download is the first instance: GitHub's hosted MCP
  server returns the connector's files at its pinned version and `Write` lands them. A remote `http` server is the default. Who
  operates it does not matter, and a sign-in the server asks for is not an install.
- **Rationale**: This is the spec's Clarifications applied directly. It matches ADR-0002, which
  already moved validation to MCP tools behind the backend. Both plugins' `.mcp.json` already
  declare a remote `http` server, so the escape route exists today.
- **Existing tools**: `docs/adr/0002-…` (the decision that already took this route);
  `plugins/*/.mcp.json`; GitHub's hosted MCP server (file-contents tools at a ref).

## R9: Data egress is out of scope, and the rule says so

- **Decision**: The rule states it governs portability only; a permitted `http` hook or
  third-party server is not thereby approved as a data destination.
- **Rationale**: Permitting `http` hooks and any remote server lets a plugin post tool inputs to
  an endpoint. That is a separate question from portability; silence would read as permission.
- **Existing tools**: none — no rule covers egress today.

## R6: Keep the `CLAUDE.md` Rules entry as committed

- **Decision**: No change to the entry added in `45c34f20`.
- **Rationale**: It already has the sibling form (`` `file` — before <trigger> ``) and names a
  script, a shell command, a CLI and a local MCP server, which is what US1 scenario 1 asks for. If
  it named the rest of R3's class, it would turn into the enumeration R3 avoids.
- **Existing tools**: The `CLAUDE.md` Rules section.

## R7: No mechanical guard

- **Decision**: No test or CI check is added. Verification is `rules-audit` and the existing
  suite.
- **Rationale**: Whether prose *instructs* a shell command is a semantic verdict. `guards.md` puts
  that with a reader, not a regex. A guard would also be a governing check landing beside the
  rule it serves, which Principle VI forbids. A lexical check, such as `Bash` in a `tools:` line or
  `"type": "command"` in a `hooks.json`, is possible later. It would land as its own PR after the
  compliance PR, because today's plugins would fail it.
- **Existing tools**: `rules-audit` (it is a reader and selects this rule by path).

## R8: The local constitution is re-pointed after merge

- **Decision**: Once the PR merges, the untracked constitution's R14 Source line points at
  `.claude/rules/plugin-runtime.md`, and its gate wording is amended to match the rule (no CLI, no
  shell-running hooks or tool grants). This happens outside the PR.

## R10: Shell stays banned although bash is common

- **Decision**: Keep the shell ban (FR-004, FR-012).
- **Rationale**: Claude Code's setup docs: "Installing Git for Windows is optional. It provides
  Git Bash, which the Bash tool needs. Without it, Claude Code uses PowerShell as the shell tool
  instead." Allowing shell would make Git for Windows an install for some Windows users, which
  FR-010 forbids; every shell use the plugins have today also needs `python3`, `gh` or `jq`.
- **Existing tools**: Claude Code setup docs.
- **Rationale**: Constitution Governance says the source file wins and the constitution is amended
  after it. The constitution is untracked, so no PR can carry the edit.
- **Existing tools**: `.specify/memory/constitution.md` Governance section.
