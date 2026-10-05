# Implementation Plan: Track the plugin-runtime rule

**Branch**: `docs/504-plugin-runtime-rule` | **Date**: 2026-10-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-plugin-runtime-rule/spec.md`

## Summary

Track the requirement that a plugin needs nothing Claude Code does not provide, as
`.claude/rules/plugin-runtime.md` scoped to `plugins/**` and indexed in the root `CLAUDE.md`.
The rule body is written whole against the spec (R1). It carries two bans: a plugin may ship no
script or executable to be run (FR-004), and no step may need anything the user installs (R3).
Everything that ships with Claude Code is permitted (R4). The PR changes no plugin file.

## Technical Context

**Language/Version**: Markdown (rule prose); no code.

**Primary Dependencies**: Claude Code rules loader and the `rules-audit` skill, both of which
read `paths:` frontmatter.

**Storage**: N/A

**Testing**: research R7.

**Target Platform**: Contributors' Claude Code sessions in this repo. The rule governs plugins
on every OS Claude Code supports.

**Project Type**: Contributor rule (documentation).

**Performance Goals**: N/A

**Constraints**: FR-009 prose rules; Principle VI (no `plugins/` change); no mechanical guard (R7).

**Scale/Scope**: One rule file and its `CLAUDE.md` Rules entry (R6).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Result |
|---|---|
| I Contract-first | Pass. No data shape, validator or type list. |
| II Ownership & trust | Pass. Nothing internal or cloud-specific; the rule names no host. No cross-link to a private repo. |
| III Fail loud, no workarounds | Pass. The ban is absolute, with no exception path (FR-010). |
| IV Test-first & verified | Pass with a note. No mechanical guard; a reader verifies the rule against seeded violations, per R7, recorded in quickstart §4. |
| V Smallest complete change | Pass. One rule file and its index entry. The class is stated by one test (R3), which covers every instance. |
| VI Governing rule apart | Pass. No file under `plugins/` changes (FR-008). The compliance PR follows separately. |
| R1 Verdict stability | Pass. No validator or model change. |
| R2 Generated trees | Pass. Nothing under `schemas/` or the vendored grammar. |
| R3 Store of record | Pass. A contributor obligation whose verdict needs a reader belongs in `.claude/rules/*.md`. |
| R4 No drift surface | Pass. Tool, hook and program names are examples of a stated test, not a closed copy of Claude Code's lists (R3). |
| R5 Plugin-prose ladder | N/A. No plugin prose is written. |
| R6 Contract prose | N/A |
| R7 Guards locate | Pass. No guard is added (R7). |
| R8 Engine facts by pin | Pass. The rule makes no engine claim. What it says of Claude Code comes from Claude Code's published docs (research.md names the pages). The Windows-shell fact cites its page inline and sits under "Not covered", so no ban depends on it (R10). |
| R9 Unread fields | N/A |
| R10 Distribution artifacts | Pass. Nothing is added under `plugins/`. |
| R11 Install and pin nothing | Pass. The rule generalises it. |
| R12 Releases & credentials | N/A |
| R13 Close against the class | Pass. The class is stated by one test. Known instances (the shipped `validation_request.py`, steps that need `python3`, `gh` or `jq`) are fixed by the compliance PR. |
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
CLAUDE.md                         # Rules entry (R6)
```

**Structure Decision**: This is a documentation change in two tracked files. `plugins/` is not
touched.

## Complexity Tracking

None.

## Mechanisms

- Rule body written whole against the spec: R1
- Load and audit trigger via `paths: ["plugins/**"]` frontmatter: R2
- A ban on shipped scripts, and one test for every step, what the user would have to install: R3
- Everything that ships with Claude Code permitted, the shell tool and built-in agents included: R4
- Absolute ban with a split escape route; third-party remote servers and sign-in allowed: R5
- `CLAUDE.md` Rules index entry: R6
- Verification without a new guard: R7
- Local constitution R14 re-pointed after merge: R8
- Egress stated as not covered: R9
- Shell dialect stated as not covered, with a note for authors: R10
