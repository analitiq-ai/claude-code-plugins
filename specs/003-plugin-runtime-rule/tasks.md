---

description: "Task list for tracking the plugin-runtime rule"
---

# Tasks: Track the plugin-runtime rule

**Input**: Design documents from `specs/003-plugin-runtime-rule/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rule-file.md, quickstart.md, critiques/critique-2026-10-05.md

**Tests**: No test tasks are generated. The spec puts any mechanical guard out of scope (Assumptions; research R7). Verification is research R7, run as quickstart §2 and §4–6.

**Organization**: Tasks are grouped by user story. Both stories are P1; US1 produces the rule, US2 proves it grades diffs.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2)

## Path Conventions

All paths are relative to the repo root. The only tracked files this feature may change are `.claude/rules/plugin-runtime.md`, `CLAUDE.md` and files under `specs/` (SC-002). No file under `plugins/` changes (FR-008).

---

## Phase 1: Setup

**Purpose**: Confirm the starting state the plan assumes.

- [ ] T001 Run `git branch --show-current`; confirm branch `docs/504-plugin-runtime-rule`. Run `git diff --name-only origin/main...HEAD` and confirm it lists only `.claude/rules/plugin-runtime.md` and `CLAUDE.md` (commit `45c34f20`).
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

- [ ] T005 [US1] Write the body of `.claude/rules/plugin-runtime.md` in one pass (research R1: one rewrite, never section-by-section patches). Keep the frontmatter exactly as contracts/rule-file.md gives it. The parts, their order and the requirement each one states are the table in data-model.md; the wording comes from the spec's FRs and Edge Cases. Cite no issue number.
- [ ] T006 [US1] Self-check `.claude/rules/plugin-runtime.md` against FR-009 (depends on T005): grep the file for `#[0-9]`, ` both `, ` two `, ` three `, and digits; read every hit and remove any ticket reference or set cardinality (counts of things no model or tool list owns are allowed per `no-cardinality-restatements.md`). Confirm every backticked path in the file exists in the clone (`git ls-files` / `ls`), and that no sentence names a Claude Code or engine function as evidence of behaviour.
- [ ] T007 [US1] Confirm the root `CLAUDE.md` Rules section lists `plugin-runtime.md` in the sibling form, with the trigger research R6 gives (FR-007).
- [ ] T008 [US1] Perform quickstart §2: read `.claude/rules/plugin-runtime.md` against the table in `specs/003-plugin-runtime-rule/data-model.md` and write down, for each of FR-002, FR-003, FR-004, FR-005, FR-006, FR-010, FR-011, FR-012, FR-013, the sentence that states it (SC-001). Any FR without a sentence → return to T005 and rewrite the file whole, not by appended patch.
- [ ] T009 [US1] Commit `.claude/rules/plugin-runtime.md` and `CLAUDE.md` as `docs(#504): rewrite the plugin-runtime rule against the spec`, with the `alq-session-id` and `Co-Authored-By` trailers in one contiguous block (identity per T002).

**Checkpoint**: US1 complete — the rule is reachable and states every requirement.

---

## Phase 4: User Story 2 — A reviewer can rule on a plugin change by citing the rule (Priority: P1)

**Goal**: A reviewer citing the rule reaches the verdict the spec expects for every clause, on both sides of each condition.

**Independent Test**: Quickstart §4 table — every row records the expected FLAG / PASS / not-selected result.

### Implementation for User Story 2

All seeds run in a throwaway copy, never in this worktree and never committed (subagents must not touch the tree). Depends on T009.

- [ ] T010 [US2] Create the throwaway copy: `git worktree add <scratch dir>/504-audit-seeds HEAD --detach` from the worktree root.
- [ ] T011 [US2] Run both parts of quickstart §4 in the copy, as its method says.
- [ ] T012 [US2] Record each returned verdict in quickstart §4's Result column. Any mismatch → the rule's wording is the defect; return to T005 for a whole-file rewrite, not an appended sentence. Then remove the copy: `git worktree remove --force <scratch dir>/504-audit-seeds`.

**Checkpoint**: US2 complete — the rule grades seeded violations and permitted cases as specified.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [ ] T013 Quickstart §1: `git diff --name-only origin/main...HEAD` lists only `.claude/rules/plugin-runtime.md`, `CLAUDE.md` and paths under `specs/` (SC-002, FR-008).
- [ ] T014 Quickstart §5: run `/rules-audit` over the branch diff; expect no finding against any rule in `.claude/rules/` (SC-003). Triage any finding as FIX / PUSH_BACK / DEFER before editing.
- [ ] T015 [P] Quickstart §6: run `python -m pytest -q`; expect green, same result as on `main` (SC-004).
- [ ] T016 Tripwire: `git log --name-only origin/main..HEAD | sort | uniq -c | sort -rn`; `.claude/rules/plugin-runtime.md` must not exceed ~3 commits — if it does, collapse to one rewrite before pushing.
- [ ] T017 Commit T012's quickstart results (trailers per T003), push `docs/504-plugin-runtime-rule`, and open a DRAFT PR against `main` titled `docs(#504): track the plugin-runtime rule`, body stating scope (rule only; compliance in a separate PR) and ending with the Claude Code attribution line.
- [ ] T018 Run the internal review loop: `Workflow(name='alq-review:review-round', args={worktree: '<worktree root>', stage: 'internal'})`, then `alq-review:assess-round` on findings, until clean or CONVERGED_PUSHBACK; then mark the PR ready and drive the Codex / DeepSource loop to the pre-merge checklist.
- [ ] T019 After merge (outside the PR): re-point R14 in the maintainer's local, untracked constitution to `.claude/rules/plugin-runtime.md` and amend its gate wording to match (no shipped script; nothing the user installs) — research R8.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001–T003)**: no dependencies.
- **Foundational (T004)**: after Setup; blocks T005.
- **US1 (T005–T009)**: sequential — one file, each step depends on the previous.
- **US2 (T010–T012)**: depends on T009 (the committed rewrite is what the copy audits); sequential.
- **Polish (T013–T019)**: after US2. T019 only after merge.

### User Story Dependencies

- **US1**: independent; it is the MVP.
- **US2**: grades US1's output, so it depends on US1. A US2 mismatch loops back to T005.

### Parallel Opportunities

- T015 can run alongside T013–T014.

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
