# Data model: Track the plugin-runtime rule

No data shapes, persistence or state. The "entities" are the two tracked files the PR changes.

## Rule file — `.claude/rules/plugin-runtime.md`

The spec's requirements own what each part says; this table only maps part to requirement.

| Part | Requirement |
|---|---|
| Frontmatter `paths:` | FR-001 (R2) |
| Title + invariant | FR-002, FR-003, FR-004 |
| The test for a step | FR-011 (R3) |
| What a plugin may use | FR-002, FR-005 (R4, R5) |
| What it may not | FR-003, FR-004, FR-011, FR-012 (R3) |
| No exception path | FR-010 (R5) |
| Not covered | FR-012, FR-013 (R9, R10) |
| Scope | FR-006 |
| Why | rationale |

Constraint on every sentence: FR-009 (`resolvable-referents.md`, `no-cardinality-restatements.md`,
`engine-behaviour-claims.md`).

## Index entry — root `CLAUDE.md` Rules section

One bullet naming `plugin-runtime.md` and its trigger (FR-007, R6).

## Relationships

- `rules-audit` and the native loader read the frontmatter → the rule reaches anyone editing
  `plugins/**`.
- The local constitution's R14 cites the rule after merge (R8).
- The follow-up compliance PR is graded by this rule. It is not part of this PR (FR-008).
