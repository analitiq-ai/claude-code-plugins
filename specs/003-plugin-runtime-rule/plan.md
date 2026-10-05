# Implementation Plan: Track the plugin-runtime rule

**Branch**: `docs/504-plugin-runtime-rule` | **Date**: 2026-10-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-plugin-runtime-rule/spec.md`

## Summary

Track the requirement that a plugin needs nothing Claude Code does not provide, as
`.claude/rules/plugin-runtime.md` scoped to `plugins/**` and indexed in the root `CLAUDE.md`.
Commit `45c34f20` already lands a first version and its index entry. That version came before the
spec's Clarifications and misses FR-005 (third-party servers, sign-in), FR-010 (absolute ban plus
the MCP-tool escape route), FR-011 (hooks) and FR-012 (shell tool grants). The work is to rewrite
the rule body once against the spec, including the critique's amendments: the shell ban by
mechanism (no agent a plugin defines or dispatches can run a shell), the split escape route
(remote MCP tool computes, built-in tools act locally), FR-004 scoped to steps the plugin needs,
and FR-013 (portability, not egress). Every forbidden thing is stated by its mechanism, "runs a
command or a local binary on the user's machine", so that monitors, `bin/` executables and LSP
servers are covered without a component list that goes stale (R3). The PR changes no plugin
file.

## Technical Context

**Language/Version**: Markdown (rule prose); no code.

**Primary Dependencies**: Claude Code rules loader and the `rules-audit` skill, both of which
read `paths:` frontmatter.

**Storage**: N/A

**Testing**: `rules-audit` over the branch diff; existing `pytest` suite unchanged.

**Target Platform**: Contributors' Claude Code sessions in this repo. The rule governs plugins
on every OS Claude Code supports.

**Project Type**: Contributor rule (documentation).

**Performance Goals**: N/A

**Constraints**: FR-009 prose rules; Principle VI (no `plugins/` change); no mechanical guard (R7).

**Scale/Scope**: One rule file is rewritten. The `CLAUDE.md` entry is unchanged (R6).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Result |
|---|---|
| I Contract-first | Pass. No data shape, validator or type list. |
| II Ownership & trust | Pass. Nothing internal or cloud-specific; the rule names no host. No cross-link to a private repo. |
| III Fail loud, no workarounds | Pass. The ban is absolute, with no exception path (FR-010). |
| IV Test-first & verified | Pass with a note. A prose rule has no red test. Per R7, verification is `rules-audit` grading a seeded violation (quickstart §4) plus the unchanged suite. |
| V Smallest complete change | Pass. One file rewritten. The class is stated by mechanism, which covers every instance. |
| VI Governing rule apart | Pass. No file under `plugins/` changes (FR-008). The compliance PR follows separately. |
| R1 Verdict stability | Pass. No validator or model change. |
| R2 Generated trees | Pass. Nothing under `schemas/` or the vendored grammar. |
| R3 Store of record | Pass. A contributor obligation whose verdict needs a reader belongs in `.claude/rules/*.md`. |
| R4 No drift surface | Pass. Tool and hook names are examples of a stated mechanism, not a closed copy of Claude Code's lists (R3). |
| R5 Plugin-prose ladder | N/A. No plugin prose is written. |
| R6 Contract prose | N/A |
| R7 Guards locate | Pass. No guard is added (R7). |
| R8 Engine facts by pin | Pass. The rule makes no engine claim. Its one Claude Code claim (the Windows shell) comes from Claude Code's setup docs and is written as documented behaviour; every ban holds without it. The `Glob`/`Grep` default-set detail stays out of the rule (R4). |
| R9 Unread fields | N/A |
| R10 Distribution artifacts | Pass. Nothing is added under `plugins/`. |
| R11 Install and pin nothing | Pass. The rule generalises it. |
| R12 Releases & credentials | N/A |
| R13 Close against the class | Pass. The class is stated by mechanism. Known instances (shipped `validation_request.py`, `Bash` in agent `tools:`) are fixed by the compliance PR. |
| R14 Run wherever Claude Code runs | Pass. This PR is R14's missing source file (research R8 re-points it). |

ADR alignment: ADR-0002 (validation through MCP) is the precedent for FR-010's escape route.
ADR-0001 is not affected.

Post-design re-check: unchanged. All gates pass, and Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/003-plugin-runtime-rule/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R10
├── data-model.md        # rule sections → FRs
├── quickstart.md        # validation run
└── contracts/
    └── rule-file.md     # frontmatter read by the loader and rules-audit
```

### Source Code (repository root)

```text
.claude/rules/plugin-runtime.md   # rewritten (R1)
CLAUDE.md                         # Rules entry, unchanged since 45c34f20 (R6)
```

**Structure Decision**: This is a documentation change in two tracked files. `plugins/` is not
touched.

## Complexity Tracking

None.

## Mechanisms

- Rule body rewritten once against the spec: R1
- Load and audit trigger via `paths: ["plugins/**"]` frontmatter: R2
- Every primitive judged by what it reaches; forbidden components and permitted hooks stated by that mechanism (reaches a shell or a local service / reaches neither): R3
- Shell ban by mechanism: every agent declares `tools:`, no grant includes a shell tool or a tool that starts another agent, no built-in agent type dispatched: R4
- Absolute ban with a split escape route (remote MCP tool computes, built-in tools act locally); third-party servers and sign-in allowed: R5
- Portability-only scope, egress stated as not covered: R9
- Shell ban kept despite common bash, from Claude Code's setup docs: R10
- `CLAUDE.md` Rules index entry kept as committed: R6
- Verification by `rules-audit` and the existing suite, with no new guard: R7
- Local constitution R14 re-pointed after merge: R8
