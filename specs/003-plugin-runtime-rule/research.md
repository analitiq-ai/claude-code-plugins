# Research: Track the plugin-runtime rule

Claude Code facts below were read from its published docs on 2026-10-05: the setup guide, the
hooks reference and the plugin components reference at `code.claude.com/docs`.

## R1: Write the rule body in one pass, from the spec

- **Decision**: The body of `.claude/rules/plugin-runtime.md` is written whole against
  FR-002–FR-006 and FR-010–FR-013. A change to the criterion means another whole-file rewrite,
  never an appended clause.
- **Rationale**: A rule patched one gap at a time ends up built from half-decisions, and each
  patch leaves the earlier wording beside its replacement.
- **Existing tools**: Sibling rules under `.claude/rules/` give the form: an `# Rule:` title, an
  invariant paragraph, short sections.

## R2: Load trigger is the `paths:` frontmatter

- **Decision**: `paths: ["plugins/**"]`. No other trigger map.
- **Rationale**: Claude Code's native rules loader and the `rules-audit` skill both select rules
  from this frontmatter. A second map would be a drift surface (`no-drift-surfaces.md`). A
  diff touching only repo-root `tests/`, `scripts/` or `.github/` does not match, which meets US2
  scenario 2 and FR-006 without any extra wording.
- **Existing tools**: `rules-audit` (it selects rules by `paths:` and says never to add a
  file→rule map); sibling rules such as `plugin-prose.md` use the same form.

## R3: A step is judged by what the user must install

- **Decision**: One test decides every step, wherever it is written: what the user would have to
  install for it to work on any OS Claude Code supports. A step that needs no install passes.
  Separately, the rule bans a script or executable the plugin ships to be run (FR-004). It gives
  examples of each ban, and neither set is written out as a closed list.
- **Rationale**: The defect the test exists to stop is a plugin that fails, or asks for an
  install, on a machine that has only Claude Code. Whether a step runs through a shell, a
  hook or an agent says nothing about that; what it needs on the machine does. A shipped script
  is banned outright because it runs as written and so needs its own interpreter: Python for a
  `.py`, a POSIX shell for a `.sh`. Neither is on every machine Claude Code supports (R10 quotes
  the setup docs on the shell). A list of forbidden
  component types would go stale as soon as Claude Code adds one
  (`no-cardinality-restatements.md`), and it would ban things that need no install. The examples
  follow from the test: a local `stdio` MCP server needs a runtime, an LSP server needs a local
  binary, an `http` hook or server at `localhost` needs a service running there, and a
  hook or a step calling an MCP server the plugin does not declare works only where the user has
  set that server up.
- **Existing tools**: Claude Code hooks reference (handler types); plugin components reference
  (executables, LSP servers, MCP servers).

## R4: Everything that ships with Claude Code is permitted

- **Decision**: The rule permits Claude Code's built-in tools in any tool grant, the shell tool
  among them, its built-in agent types, and agents dispatching agents. It sets no requirement on
  `tools:` or `allowed-tools`.
- **Rationale**: These need no install: they arrive with Claude Code. A ban on shell grants or on
  built-in agents would restrict what a plugin may use without removing a shipped script or an
  install, which are what the rule bans.
- **Existing tools**: Agent `tools:` frontmatter; skill and command `allowed-tools`; Claude
  Code's built-in agent types.

## R5: The escape route splits work between a remote MCP tool and built-in tools

- **Decision**: The ban is absolute. A capability built-in tools cannot provide is split: a remote
  MCP tool returns what they cannot compute, and built-in tools act on it locally. A remote server
  cannot write the user's disk, so "move it to the MCP server" alone is false for a step that
  changes local state. `registry-browser`'s download, which today runs `gh`, is the first case
  for this route: the compliance PR is to read the connector's files through GitHub's hosted MCP
  server at the pinned version and land them with `Write`. A remote
  `http` server is the default. Who operates it does not matter, and a sign-in the server asks
  for is not an install.
- **Rationale**: This is the spec's Clarifications applied directly. It matches ADR-0002, which
  already moved validation to MCP tools behind the backend. Both plugins' `.mcp.json` already
  declare a remote `http` server, so the escape route exists today.
- **Existing tools**: `docs/adr/0002-…` (the decision that already took this route);
  `plugins/*/.mcp.json`; GitHub's hosted MCP server (file-contents tools at a ref).

## R6: The `CLAUDE.md` Rules entry names what sends a contributor to the rule

- **Decision**: One bullet in the sibling form (`` `file` — before <trigger> ``), naming a script
  of the plugin's own, a CLI or runtime the user installs, and a local MCP server.
- **Rationale**: Those are the changes US1 scenario 1 says must reach the rule. Naming every
  example the rule gives would turn the entry into a second copy of it.
- **Existing tools**: The `CLAUDE.md` Rules section.

## R7: No mechanical guard

- **Decision**: No test or CI check is added. The rule is verified in two parts, both recorded in
  quickstart §4: one real `/rules-audit` run over a seeded plugin diff, which exercises path
  selection and the rule's reviewer and is the evidence for US2's Independent Test; and one
  read-only reviewer pass over a matrix with a row on each side of every clause, which grades
  wording `rules-audit` would take one run per case to cover. The branch's own `rules-audit` run
  (SC-003) and the unchanged suite complete it.
- **Rationale**: Whether prose makes a step depend on an installed program is a semantic verdict.
  `guards.md` puts that with a reader, not a regex. A guard would also be a governing check
  landing beside the rule it serves, which Principle VI forbids. A lexical check, such as a
  program file under `plugins/`, is possible later. It would land as its own PR after the
  compliance PR, because today's plugins would fail it.
- **Existing tools**: `rules-audit` (it is a reader and selects this rule by path).

## R8: The local constitution is re-pointed after merge

- **Decision**: Once the PR merges, the untracked constitution's R14 Source line points at
  `.claude/rules/plugin-runtime.md`, and its gate wording is amended to match the rule (no
  shipped script, nothing the user installs). This happens outside the PR.
- **Rationale**: Constitution Governance says the source file wins and the constitution is amended
  after it. The constitution is untracked, so no PR can carry the edit.
- **Existing tools**: the Governance section of the maintainer's local, untracked constitution.

## R9: Data egress is out of scope, and the rule says so

- **Decision**: The rule lists data egress under "Not covered": a permitted `http` hook or
  third-party server is not thereby approved as a data destination.
- **Rationale**: Permitting `http` hooks and any remote server lets a plugin post tool inputs to
  an endpoint. That is a separate question from portability; silence would read as permission.
- **Existing tools**: none — no rule covers egress today.

## R10: The rule does not grade which shell a command is written for

- **Decision**: The rule lists shell dialect under "Not covered". It says the shell behind the
  shell tool differs by machine, that a command needing only a shell and the utilities that come
  with it is not graded on which shell that is, and how an author writes a step that works under
  either (FR-012).
- **Rationale**: Claude Code's setup docs: "Installing Git for Windows is optional. It provides
  Git Bash, which the Bash tool needs. Without it, Claude Code uses PowerShell as the shell tool
  instead." The shell tool itself ships with Claude Code and stays permitted (R4). Grading
  dialect would turn the rule back into a rule about shell use, when what it bans is shipped
  scripts and installs. The note tells an author where the difference is.
- **Existing tools**: Claude Code setup docs.
