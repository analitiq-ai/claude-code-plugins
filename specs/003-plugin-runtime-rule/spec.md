# Feature Specification: Track the plugin-runtime rule

**Feature Branch**: `docs/504-plugin-runtime-rule`

**Created**: 2026-10-05

**Status**: Draft

**Input**: Issue #504 — "Track the plugin-runtime rule: plugins need nothing Claude Code does not provide"

**Scope**: Rule only. The plugin changes that bring `plugins/` into compliance ship in a separate PR, because a governing rule and the code it governs never move in the same PR.

## Why this spec exists

Plugins are installed on machines this repo does not control. The requirement that they need nothing beyond Claude Code exists only as a decision on a closed issue (#264) and in an untracked local constitution. No tracked file a contributor reads states it, and #402 reintroduced a Python script the plugins invoke.

## Clarifications

### Session 2026-10-05

- Q: What decides whether a plugin step is allowed? → A: Only what the user must have installed for it to work. Anything that ships with Claude Code is allowed: its built-in tools, the shell tool included, in any tool grant; its built-in agent types, such as `general-purpose`; agents dispatching agents; hooks. Banned: a hand-rolled script or executable the plugin ships, and anything the user installs separately.
- Q: Can a plugin ever ask the user to install something when a capability cannot be built from Claude Code's tools plus a remote MCP server? → A: No. The ban is absolute, with no exception path.
- Q: May a plugin declare a remote MCP server Analitiq does not operate? → A: Yes, if it is remote and needs no local install; a sign-in is not an install.
- Q: Does the rule grade which shell a command is written for? → A: No. Claude Code's setup docs make Git for Windows optional and use PowerShell without it, so the shell differs by machine. The rule says so as a note for authors; a command that needs only a shell and the utilities that come with it is not graded on which shell that is.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A contributor learns the constraint before adding a runtime dependency (Priority: P1)

A contributor edits a file under `plugins/` and is about to add a script, a call to an installed CLI or a local MCP server. The rule reaches them before the change does: it loads for `plugins/**` paths, and the root `CLAUDE.md` Rules section names it with the trigger that should send them to it.

**Why this priority**: The constraint is invisible today; that is the whole defect.

**Independent Test**: From a fresh clone, open the root `CLAUDE.md` Rules section and follow the entry to `.claude/rules/plugin-runtime.md`; the file states every requirement below.

**Acceptance Scenarios**:

1. **Given** a fresh clone, **When** a contributor reads the root `CLAUDE.md` Rules section, **Then** it lists `plugin-runtime.md` with a trigger naming a shipped script, an installed CLI or runtime, and a local MCP server.
2. **Given** an edit under `plugins/`, **When** Claude Code loads path-scoped rules, **Then** `plugin-runtime.md` applies (its `paths:` matches `plugins/**`).

---

### User Story 2 - A reviewer can rule on a plugin change by citing the rule (Priority: P1)

A reviewer sees a PR that ships a script in a plugin, has a plugin agent run `python`, `uv`, `npx` or `gh`, or declares a `stdio` MCP server. They cite the tracked rule instead of a closed issue or a local file.

**Why this priority**: A rule that cannot be cited cannot be enforced in review, and `rules-audit` selects rules by path.

**Independent Test**: Run `rules-audit` over a diff that has a plugin agent run a shipped script; the plugin-runtime reviewer flags it.

**Acceptance Scenarios**:

1. **Given** a diff adding a shipped script a plugin executes, **When** it is audited, **Then** the rule names it a violation.
2. **Given** a diff changing only repo-root tests, scripts or CI, **When** it is audited, **Then** the rule does not apply.

### Edge Cases

- `Bash` in a plugin agent's `tools:` or a skill's `allowed-tools`, a plugin agent with no `tools:` line, or plugin prose dispatching `general-purpose` → permitted: each ships with Claude Code (FR-002).
- A step that invokes a program the user has to install (`python3`, `gh`, `jq`) → a violation, whatever tool runs it (FR-003, FR-011).
- A `.py` or `.sh` file under `plugins/<name>/` that an agent, a hook or the user is told to run, or an executable under `bin/` → a violation. A program file the plugin only copies into what the user is building, and never runs, is not (FR-004).
- A local `stdio` MCP server → a violation: it needs a runtime on the user's machine. A remote `http` server is the default (FR-005).
- An `http` hook or server pointed at `localhost`, or a hook or a step calling an MCP server the plugin does not declare → a violation: each works only where the user has set something up (FR-011).
- A step whose shell command needs only a shell and the utilities that come with it (`mktemp`, `tar`) → permitted. The rule does not grade which shell the command is written for; it tells an author how to write a step that works under either (FR-012).
- A generator's provenance comment ("run `python scripts/…` to regenerate") or optional advice to the user ("review the result with `git diff`") → not a step the plugin needs (FR-012).
- Python under repo-root `packages/`, `scripts/`, `tests/` → out of scope; contributor tooling (FR-006).
- A step that changes the user's disk with input no built-in tool can compute (a registry download) → a remote MCP tool returns the content and the built-in `Write` lands it (FR-010).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The repo MUST track `.claude/rules/plugin-runtime.md`, path-scoped to `plugins/**`.
- **FR-002**: The rule MUST state that everything under `plugins/<name>/` runs on every OS Claude Code supports, using only what ships with Claude Code: its primitives (agents, skills, commands, rules, hooks, other plugins that satisfy the rule), its built-in tools, the shell tool among them, in any tool grant, its built-in agent types, and MCP servers declared in `.mcp.json` that need no local install.
- **FR-003**: The rule MUST state that no step needs the user to install a language runtime, package manager, CLI or library (Python, `uv`, `pip`, Node, `npx`, `gh`, `jq`, …).
- **FR-004**: The rule MUST state that a plugin ships no script or executable for an agent, a hook or the user to run: no such program file under `plugins/<name>/`, and no executable under `bin/`.
- **FR-005**: The rule MUST state that a declared MCP server needs no local install, and that remote `http` is the default. Who operates it does not matter: a third-party remote server is permitted, and a sign-in the server asks for is not an install.
- **FR-006**: The rule MUST scope itself to the plugin runtime only; repo-root packages, tests, scripts and CI are contributor tooling.
- **FR-007**: The root `CLAUDE.md` Rules section MUST list `plugin-runtime.md` with a one-line trigger, in the same form as its sibling entries.
- **FR-008**: The PR MUST change no file under `plugins/`.
- **FR-009**: The rule MUST satisfy the repo's prose rules: no ticket or PR references (`resolvable-referents.md`), no cardinality restatements, no named-symbol claims about engine behaviour.
- **FR-010**: The rule MUST state the ban is absolute: no exception path, no ADR or README install step that admits a runtime dependency. A capability built-in tools cannot provide is split: a remote MCP tool returns what they cannot compute, and built-in tools act on it locally.
- **FR-011**: The rule MUST give one test for any step, wherever it is written — prose, a hook, `.mcp.json`: what the user would have to install for it to work on any OS Claude Code supports. It names, as examples and not a closed list, steps that fail the test: a program the user has to install, a local `stdio` MCP server, an LSP server, an `http` hook or server pointed at `localhost`, a hook or a step calling an MCP server the plugin does not declare.
- **FR-012**: The rule MUST state that it does not grade which shell a command is written for: the shell behind the shell tool differs by machine (Git Bash when Git for Windows is installed, PowerShell when it is not), a command that needs only a shell and the utilities that come with it is not graded on which shell that is, and an author who wants a step to work under either states what it must achieve or uses built-in file and search tools. It MUST also state that a generator's provenance comment and optional advice to the user are not steps the plugin needs.
- **FR-013**: The rule MUST state that it does not govern where data is sent: a permitted `http` hook or third-party server is not thereby approved as a data destination.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Each of FR-002 through FR-006 and FR-010 through FR-013 maps to a sentence in the rule file.
- **SC-002**: The PR diff is confined to `.claude/rules/plugin-runtime.md`, `CLAUDE.md` and `specs/`.
- **SC-003**: `rules-audit` over the PR diff reports no violation of any rule in `.claude/rules/`.
- **SC-004**: The test suite passes unchanged.

## Assumptions

- The local constitution, #264's decision and this spec's Clarifications are the intent being tracked; the rule adds no requirement beyond them.
- Existing violations in `plugins/` (the shipped request builder, steps that need `python3`, `gh` or `jq`) are known and fixed in the follow-up compliance PR (#505), not here. #505 owns its own completion check — a full read of `plugins/**` against the rule — because `rules-audit` judges only added and modified lines.
- No mechanical guard (a test failing on a script shipped under `plugins/`) is in scope; adding one would be a governing check landing beside the code it grades.
