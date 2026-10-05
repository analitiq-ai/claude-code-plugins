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

- Q: Can a plugin ever ask the user to install something when a capability cannot be built from Claude Code's tools plus a remote MCP server? → A: No. The ban is absolute, with no exception path.
- Q: Which plugin hooks does the rule allow? → A: Any hook that runs no shell (`prompt`, `agent`, `http`, `mcp_tool` as examples); a `command` hook is a violation.
- Q: (PR review) Is a primitive judged by what it runs or by what it reaches? → A: By what it reaches. An `http` hook to `localhost`, an `mcp_tool` hook calling an undeclared server, a dependency plugin that breaks the rule, and `Agent` in a plugin agent's `tools:` (Claude Code documents it as allowing any subagent type) are violations.
- Q: May a plugin agent list `Bash` in its `tools:`? → A: No. No tool grant a plugin declares includes `Bash`.
- Q: May a plugin declare a remote MCP server Analitiq does not operate? → A: Yes, if it is remote and needs no local install; a sign-in is not an install.
- Q: Since bash is widely available, should plugin agents be allowed shell access? → A: No, keep the ban. Claude Code's setup docs make Git for Windows optional and fall back to PowerShell without it, so bash is not on every supported machine.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A contributor learns the constraint before adding a runtime dependency (Priority: P1)

A contributor edits a file under `plugins/` and is about to add a script, a shell command or a local MCP server. The rule reaches them before the change does: it loads for `plugins/**` paths, and the root `CLAUDE.md` Rules section names it with the trigger that should send them to it.

**Why this priority**: The constraint is invisible today; that is the whole defect.

**Independent Test**: From a fresh clone, open the root `CLAUDE.md` Rules section and follow the entry to `.claude/rules/plugin-runtime.md`; the file states every requirement below.

**Acceptance Scenarios**:

1. **Given** a fresh clone, **When** a contributor reads the root `CLAUDE.md` Rules section, **Then** it lists `plugin-runtime.md` with a trigger naming scripts, shell commands, CLIs and local MCP servers.
2. **Given** an edit under `plugins/`, **When** Claude Code loads path-scoped rules, **Then** `plugin-runtime.md` applies (its `paths:` matches `plugins/**`).

---

### User Story 2 - A reviewer can rule on a plugin change by citing the rule (Priority: P1)

A reviewer sees a PR that has a plugin agent run `python`, `uv`, `npx` or a shell command, or declares a `stdio` MCP server. They cite the tracked rule instead of a closed issue or a local file.

**Why this priority**: A rule that cannot be cited cannot be enforced in review, and `rules-audit` selects rules by path.

**Independent Test**: Run `rules-audit` over a diff adding a shell command to a plugin agent; the plugin-runtime reviewer flags it.

**Acceptance Scenarios**:

1. **Given** a diff adding a shipped script a plugin executes, **When** it is audited, **Then** the rule names it a violation.
2. **Given** a diff changing only repo-root tests, scripts or CI, **When** it is audited, **Then** the rule does not apply.

### Edge Cases

- A command naming only POSIX utilities → still a violation: on Windows the shell is Git Bash or PowerShell, so no shell command is portable.
- A local `stdio` MCP server → a violation: it needs a runtime on the user's machine. A remote `http` server is the default.
- Python under repo-root `packages/`, `scripts/`, `tests/` → out of scope; contributor tooling.
- `Bash`, or any tool that runs a shell command, in an agent's `tools:` or a skill's `allowed-tools`, even with no shell command in the prose → a violation (FR-012).
- A `command` hook → a violation: it runs through the shell. A hook that reaches neither a shell nor anything local (`prompt`, `agent`, an `http` hook to a remote URL, an `mcp_tool` hook calling a declared server) is permitted; an `http` hook to `localhost` is a violation (FR-011).
- `Agent` in a plugin agent's `tools:` → a violation: it can start a built-in agent that has a shell (FR-012).
- A plugin agent with no `tools:` line, or plugin prose dispatching a built-in agent type such as `general-purpose` → a violation: both inherit a shell tool without declaring it (FR-012).
- A generator's provenance comment ("run `python scripts/…` to regenerate") or optional advice to the user ("review the result with `git diff`") → outside FR-004: neither is a step the plugin needs in order to work.
- A step that changes the user's disk with input no built-in tool can compute (a registry download) → a remote MCP tool returns the content and the built-in `Write` lands it (FR-010).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The repo MUST track `.claude/rules/plugin-runtime.md`, path-scoped to `plugins/**`.
- **FR-002**: The rule MUST state that everything under `plugins/<name>/` runs on every OS Claude Code supports, using only what Claude Code provides: agents, skills, commands, rules, hooks that run no shell, its built-in file, search and web tools, other plugins, and MCP servers declared in `.mcp.json`.
- **FR-003**: The rule MUST state that no step needs the user to install a language runtime, package manager, CLI or library (Python, `uv`, `pip`, Node, `npx`, `gh`, `jq`, …).
- **FR-004**: The rule MUST state that no step the plugin needs in order to work — run by an agent or asked of the user — is a shell command, and why: Claude Code's documented shell differs by platform (Git Bash when Git for Windows is installed, PowerShell when it is not). A generator's provenance comment and optional advice to the user are not such steps.
- **FR-005**: The rule MUST state that a declared MCP server needs no local install, and that remote `http` is the default. Who operates it does not matter: a third-party remote server is permitted, and a sign-in the server asks for is not an install.
- **FR-006**: The rule MUST scope itself to the plugin runtime only; repo-root packages, tests, scripts and CI are contributor tooling.
- **FR-007**: The root `CLAUDE.md` Rules section MUST list `plugin-runtime.md` with a one-line trigger, in the same form as its sibling entries.
- **FR-008**: The PR MUST change no file under `plugins/`.
- **FR-009**: The rule MUST satisfy the repo's prose rules: no ticket or PR references (`resolvable-referents.md`), no cardinality restatements, no named-symbol claims about engine behaviour.
- **FR-010**: The rule MUST state the ban is absolute: no exception path, no ADR or README install step that admits a runtime dependency. A capability built-in tools cannot provide is split: a remote MCP tool returns what they cannot compute, and built-in tools act on it locally.
- **FR-011**: The rule MUST judge every primitive by what it reaches at run time. It permits a hook whose handler reaches neither a shell nor a local service (`prompt`, `agent`, an `http` hook to a remote URL, an `mcp_tool` hook calling a server the plugin may declare — examples, not a closed list), permits other plugins only when they satisfy the rule, and forbids `command` hooks and anything pointed at a local service.
- **FR-012**: The rule MUST state that no agent a plugin defines or dispatches can run a shell, because a granted shell lets the model improvise the command the prose is barred from writing. Consequences it states: every plugin agent declares `tools:` (an agent without it inherits every tool), no tool grant — an agent's `tools:`, a skill's or command's `allowed-tools` — includes a tool that runs a shell command, `Bash` among them, no plugin agent's `tools:` includes `Agent`, and plugin prose dispatches only plugin-defined agents, never a built-in agent type that has a shell.
- **FR-013**: The rule MUST state that it governs portability only, not where data is sent: a permitted `http` hook or third-party server is not thereby approved as a data destination.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Each of FR-002 through FR-006 and FR-010 through FR-013 maps to a sentence in the rule file.
- **SC-002**: The PR diff is confined to `.claude/rules/plugin-runtime.md`, `CLAUDE.md` and `specs/`.
- **SC-003**: `rules-audit` over the PR diff reports no violation of any rule in `.claude/rules/`.
- **SC-004**: The test suite passes unchanged.

## Assumptions

- The local constitution, #264's decision and this spec's Clarifications are the intent being tracked; the rule adds no requirement beyond them.
- Existing violations in `plugins/` (the shipped request builder, `Bash` in agent tool lists) are known and fixed in the follow-up compliance PR (#505), not here. #505 owns its own completion check — a full read of `plugins/**` against the rule — because `rules-audit` judges only added and modified lines.
- No mechanical guard (a test failing on a shell command in plugin prose) is in scope; adding one would be a governing check landing beside the code it grades.
