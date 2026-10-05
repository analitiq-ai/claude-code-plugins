---

description: "Task list for tracking the plugin-runtime rule"
---

# Tasks: Track the plugin-runtime rule

**Input**: Design documents from `specs/003-plugin-runtime-rule/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rule-file.md, quickstart.md, critiques/critique-2026-10-05.md

**Tests**: No test tasks are generated. The spec puts any mechanical guard out of scope (Assumptions; research R7), so a prose rule has no red test. Verification is the reader's mapping check (quickstart §2), seeded `rules-audit` runs (quickstart §4–5) and the unchanged `pytest` suite (quickstart §6).

**Organization**: Tasks are grouped by user story. Both stories are P1; US1 produces the rule, US2 proves it grades diffs.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2)

## Path Conventions

All paths are relative to the worktree root `/mnt/nvme/projects/analitiq/cto/claude-code-plugins/.claude/worktrees/504`. The only tracked files this feature may change are `.claude/rules/plugin-runtime.md`, `CLAUDE.md` and files under `specs/` (SC-002). No file under `plugins/` changes (FR-008).

---

## Phase 1: Setup

**Purpose**: Confirm the starting state the plan assumes.

- [ ] T001 Run `git branch --show-current` and `pwd`; confirm branch `docs/504-plugin-runtime-rule` and the worktree root above. Run `git diff --name-only origin/main...HEAD` and confirm it lists only `.claude/rules/plugin-runtime.md` and `CLAUDE.md` (commit `45c34f20`).
- [ ] T002 Run `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT`; both must be `Analitiq-bot <analitiq@analitiq.ai>`. If not, export `GIT_AUTHOR_NAME`, `GIT_COMMITTER_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_EMAIL` with those values before any commit.
- [ ] T003 Commit the untracked `specs/003-plugin-runtime-rule/` tree (spec, plan, research, data-model, contracts, quickstart, critiques, this tasks.md) as one `docs(#504): …` commit. The message ends with the `alq-session-id: <current session uuid>` trailer and the `Co-Authored-By` line in one contiguous trailer block (no blank line between them).

---

## Phase 2: Foundational

**Purpose**: Load the prose constraints every sentence of the rule must meet (FR-009).

- [ ] T004 Read `.claude/rules/resolvable-referents.md`, `.claude/rules/no-cardinality-restatements.md`, `.claude/rules/engine-behaviour-claims.md` and the sibling rules `.claude/rules/plugin-prose.md` and `.claude/rules/guards.md` for house style (`# Rule:` title, `**The invariant:**` paragraph, short sections). Note the constraints that bind the rewrite: no ticket/PR numbers (no `#264`, `#402`, `#505`); no count of any set ("both", "two", digits) — state the mechanism that decides membership; no path the clone does not contain (no `.specify/…`, no `docs/` outside `docs/adr/`); Claude Code facts phrased as Claude Code's documented behaviour; no function or symbol named as a behaviour claim.

**Checkpoint**: Constraints known — the rewrite can start.

---

## Phase 3: User Story 1 — A contributor learns the constraint before adding a runtime dependency (Priority: P1) 🎯 MVP

**Goal**: `.claude/rules/plugin-runtime.md` loads for `plugins/**`, is indexed in the root `CLAUDE.md`, and states every requirement FR-002–FR-006 and FR-010–FR-013.

**Independent Test**: From a fresh clone, follow the root `CLAUDE.md` Rules entry to `.claude/rules/plugin-runtime.md`; for each of FR-002–FR-006 and FR-010–FR-013 a reader can point to the sentence stating it (quickstart §2–3).

### Implementation for User Story 1

- [ ] T005 [US1] Rewrite the body of `.claude/rules/plugin-runtime.md` in one pass (research R1 — one rewrite, not section-by-section patches; T005 is a single edit producing the whole file). Keep the frontmatter exactly `---\npaths:\n  - "plugins/**"\n---` (contracts/rule-file.md), the `# Rule: a plugin needs nothing Claude Code does not provide` title, and the existing invariant, Scope and Why wording wherever it still holds. The body must contain, in this order:
  1. **Governs line**: everything under `plugins/<name>/` — agents, skills, commands, hooks, `.mcp.json`, and any file shipped beside them.
  2. **Invariant** (FR-002, FR-003): a plugin runs on every OS Claude Code supports, on a machine with Claude Code and nothing else; no step needs the user to install a language runtime, package manager, CLI or library — "Python, `uv`, `pip`, Node, `npx`, `gh`, `jq`, or anything like them".
  3. **What a plugin may use** (FR-002, FR-005, FR-011): Claude Code's primitives (agents, skills, commands, rules, other plugins); hooks whose handler runs no shell, with `prompt`, `agent`, `http`, `mcp_tool` given as examples, not a closed list; Claude Code's built-in file, search and web tools; MCP servers declared in the plugin's `.mcp.json` that need no local install — remote `http` is the default, who operates the server does not matter (a third-party remote server is permitted), and a sign-in the server asks for is not an install.
  4. **What it may not** (FR-004, FR-011, FR-012; research R3, R4, R10), each stated by mechanism:
     - Anything that runs a command or a local binary on the user's machine. Examples, explicitly not a closed list: a shipped script a plugin tells an agent to execute, a `command` hook, a monitor, an executable under `bin/`, an LSP server, a local `stdio` MCP server.
     - A shell command as a step the plugin needs in order to work, whether an agent runs it or the user is asked to. Reason, phrased as Claude Code's documented behaviour: on Windows its shell is Git Bash when Git for Windows is installed and PowerShell when it is not, so even a command naming only POSIX utilities fails on a supported platform. State that a generator's provenance comment ("run … to regenerate") and optional advice to the user (e.g. reviewing a result with `git diff`) are not such steps.
     - Any agent the plugin defines or dispatches that can run a shell, because a granted shell lets the model improvise the command the prose is barred from writing. Consequences: every plugin agent declares `tools:` (an agent without it inherits every tool); no tool grant — an agent's `tools:`, a skill's or command's `allowed-tools` — includes a tool that runs a shell command, `Bash` among them (`PowerShell`, `Monitor` as further examples of the class); plugin prose dispatches only plugin-defined agents, never a built-in agent type that has a shell (e.g. `general-purpose`). Add one sentence that a skill's `allowed-tools` pre-approves rather than restricts, so keeping a shell out of it is not a sandbox and the prose ban carries the rest (R4).
     - Do NOT include the `Glob`/`Grep` default-tool-set detail (critique E3, research R4) — it belongs in the compliance PR's agent craft.
  5. **No exception path** (FR-010; research R5): the ban is absolute — no ADR, README or install step admits a runtime dependency. A capability built-in tools cannot provide is split: a remote MCP tool returns what they cannot compute, and built-in tools act on it locally (a remote server cannot write the user's disk). Do not cite an issue number; an example may describe the shape (a remote tool returns a file's contents, `Write` lands it) without naming a ticket.
  6. **Not covered** (FR-013; research R9): the rule governs portability only, not where data is sent; a permitted `http` hook or third-party server is not thereby approved as a data destination.
  7. **Scope** (FR-006): the plugin runtime only; repo-root packages, tests, scripts and CI are contributor tooling, may require Python, and stay out of `plugins/<name>/`.
  8. **Why**: keep the committed paragraph (users' machines are not controlled by this repo; every assumed runtime is a failure on the machine that lacks it).
- [ ] T006 [US1] Self-check `.claude/rules/plugin-runtime.md` against FR-009 (depends on T005): grep the file for `#[0-9]`, ` both `, ` two `, ` three `, and digits; read every hit and remove any ticket reference or set cardinality (counts of things no model or tool list owns are allowed per `no-cardinality-restatements.md`). Confirm every backticked path in the file exists in the clone (`git ls-files` / `ls`), and that no sentence names a Claude Code or engine function as evidence of behaviour.
- [ ] T007 [US1] Confirm the root `CLAUDE.md` Rules section still holds, unchanged from `45c34f20`, the entry `` `plugin-runtime.md` — before making a plugin depend on anything Claude Code does not provide: a script, a shell command, a CLI, a local MCP server. `` (FR-007, research R6). Make no edit to `CLAUDE.md`.
- [ ] T008 [US1] Perform quickstart §2: read `.claude/rules/plugin-runtime.md` against the table in `specs/003-plugin-runtime-rule/data-model.md` and write down, for each of FR-002, FR-003, FR-004, FR-005, FR-006, FR-010, FR-011, FR-012, FR-013, the sentence that states it (SC-001). Any FR without a sentence → return to T005 and rewrite the file whole, not by appended patch.
- [ ] T009 [US1] Commit `.claude/rules/plugin-runtime.md` as `docs(#504): rewrite the plugin-runtime rule against the spec`, with the `alq-session-id` and `Co-Authored-By` trailers in one contiguous block (identity per T002).

**Checkpoint**: US1 complete — the rule is reachable and states every requirement.

---

## Phase 4: User Story 2 — A reviewer can rule on a plugin change by citing the rule (Priority: P1)

**Goal**: `rules-audit` selects `plugin-runtime.md` for `plugins/**` diffs, flags each forbidden mechanism, passes each permitted case, and does not select the rule for repo-root-only diffs.

**Independent Test**: Quickstart §4 table — every row records the expected FLAG / PASS / not-selected result.

### Implementation for User Story 2

All seeds run in a throwaway copy, never in this worktree and never committed (subagents must not touch the tree). Depends on T009.

- [ ] T010 [US2] Create the throwaway copy: `git worktree add /tmp/504-audit-seeds HEAD --detach` from the worktree root. Every T011–T019 seed is made there as an uncommitted working-tree change on top of HEAD, audited, then reverted with `git -C /tmp/504-audit-seeds checkout -- . && git -C /tmp/504-audit-seeds clean -fd` before the next seed.
- [ ] T011 [P] [US2] Seed: add a step "run `python3 scripts/foo.py`" to a plugin agent's prose, e.g. `/tmp/504-audit-seeds/plugins/connector-builder/agents/<any agent>.md`; run `/rules-audit` on the working tree; expect FLAG from the plugin-runtime reviewer.
- [ ] T012 [P] [US2] Seed: add a `{"type": "command", "command": "echo hi"}` hook in `/tmp/504-audit-seeds/plugins/connector-builder/hooks/hooks.json`; expect FLAG.
- [ ] T013 [P] [US2] Seed: add a `stdio` server (`"command": "npx", "args": ["some-mcp"]`) to `/tmp/504-audit-seeds/plugins/connector-builder/.mcp.json`; expect FLAG.
- [ ] T014 [P] [US2] Seed: add an executable file `/tmp/504-audit-seeds/plugins/connector-builder/bin/tool`; expect FLAG.
- [ ] T015 [P] [US2] Seed: add a `{"type": "prompt", …}` hook in `/tmp/504-audit-seeds/plugins/connector-builder/hooks/hooks.json`; expect PASS (no plugin-runtime finding).
- [ ] T016 [P] [US2] Seed: add a third-party remote `http` server needing sign-in to `/tmp/504-audit-seeds/plugins/pipeline-builder/.mcp.json`; expect PASS.
- [ ] T017 [P] [US2] Seed: add a generated-style `<!-- Run \`python scripts/render_rule_reference.py write\` to regenerate. -->` comment to a plugin reference `.md`; expect PASS.
- [ ] T018 [P] [US2] Seed: add optional "review the result with `git diff`" advice to `/tmp/504-audit-seeds/plugins/connector-builder/README.md`; expect PASS.
- [ ] T019 [P] [US2] Seed: change only a file under `/tmp/504-audit-seeds/tests/`; expect the plugin-runtime rule not selected.
- [ ] T020 [US2] Record each T011–T019 outcome (FLAG / PASS / not selected) in `specs/003-plugin-runtime-rule/quickstart.md` §4 as a result column. Any mismatch → the rule's wording is the defect; return to T005 for a whole-file rewrite, not an appended sentence. Then remove the copy: `git worktree remove --force /tmp/504-audit-seeds`.

**Checkpoint**: US2 complete — the rule grades seeded violations and permitted cases as specified.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [ ] T021 Quickstart §1: `git diff --name-only origin/main...HEAD` lists only `.claude/rules/plugin-runtime.md`, `CLAUDE.md` and paths under `specs/` (SC-002, FR-008).
- [ ] T022 Quickstart §5: run `/rules-audit` over the branch diff; expect no finding against any rule in `.claude/rules/` (SC-003). Triage any finding as FIX / PUSH_BACK / DEFER before editing.
- [ ] T023 [P] Quickstart §6: run `python -m pytest -q`; expect green, same result as on `main` (SC-004).
- [ ] T024 Tripwire: `git log --name-only origin/main..HEAD | sort | uniq -c | sort -rn`; `.claude/rules/plugin-runtime.md` must not exceed ~3 commits — if it does, collapse to one rewrite before pushing.
- [ ] T025 Commit T020's quickstart results (trailers per T003), push `docs/504-plugin-runtime-rule`, and open a DRAFT PR against `main` titled `docs(#504): track the plugin-runtime rule`, body stating scope (rule only; compliance in a separate PR) and ending with the Claude Code attribution line.
- [ ] T026 Run the internal review loop: `Workflow(name='alq-review:review-round', args={worktree: '<worktree root>', stage: 'internal'})`, then `alq-review:assess-round` on findings, until clean or CONVERGED_PUSHBACK; then mark the PR ready and drive the Codex / DeepSource loop to the pre-merge checklist.
- [ ] T027 After merge (outside the PR): re-point R14 in the untracked `.specify/memory/constitution.md` to `.claude/rules/plugin-runtime.md` and amend its gate wording to match (no CLI, no shell-running hooks or tool grants) — research R8.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001–T003)**: no dependencies.
- **Foundational (T004)**: after Setup; blocks T005.
- **US1 (T005–T009)**: sequential — one file, each step depends on the previous.
- **US2 (T010–T020)**: depends on T009 (the committed rewrite is what the copy audits). T011–T019 are independent seeds.
- **Polish (T021–T027)**: after US2. T027 only after merge.

### User Story Dependencies

- **US1**: independent; it is the MVP.
- **US2**: grades US1's output, so it depends on US1. A US2 mismatch loops back to T005.

### Parallel Opportunities

- T011–T019: each seed can run in its own throwaway copy (one copy per seed if run concurrently, not the shared `/tmp/504-audit-seeds`).
- T023 can run alongside T021–T022.

---

## Parallel Example: User Story 2

```text
# One throwaway worktree per seed, audits run concurrently:
Task: "T011 python3 step in plugin agent prose → expect FLAG"
Task: "T012 command hook → expect FLAG"
Task: "T013 stdio MCP server → expect FLAG"
Task: "T015 prompt hook → expect PASS"
Task: "T019 tests/-only change → rule not selected"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. T001–T004.
2. T005–T009: the rewritten, reachable rule.
3. **Stop and validate** with quickstart §2–3.

### Incremental Delivery

1. US1 → rule tracked and complete.
2. US2 → seeded audits prove it grades diffs.
3. Polish → confined diff, clean audit, green suite, draft PR, review loop.

---

## Notes

- One rewrite of the rule file (R1). A failed check sends you back to T005 to rewrite the whole file, never to append a patch.
- Nothing under `plugins/` changes in this PR; known violations are fixed by the separate compliance PR.
- Every commit carries the `alq-session-id` trailer contiguous with `Co-Authored-By`.
