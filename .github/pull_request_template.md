<!--
Ticket numbers, PR links and issue references belong HERE — the tracker is the
medium in this box, not a dangling pointer out of a file. Keep them out of code
and prose.
-->

## What changed, and why

<!-- The mechanism, not the symptom. A reader with no context should be able to
     tell what is now true that was not true before. -->

## Closes

<!-- Closes #N. Per CONTRIBUTING.md, close against the CLASS, not the
     instances: if this fixes one leak of a mechanism, say what still leaks. -->

## Checks

Tick what you did. These are attestations: you are stating that you applied
the rule, not that a tool agreed.

- [ ] **Referents resolve.** Applied `.claude/rules/resolvable-referents.md` to
      every comment, docstring and description this change touched: each pointer
      opened and confirmed, each count re-counted, no history asserted that the
      repo does not record, and no "this PR" / "this commit" left in a file that
      will outlive it — and no ticket number, tracker URL or citation of a
      path a fresh clone does not contain.
- [ ] **No new drift surface.** Applied `.claude/rules/no-drift-surfaces.md` —
      nothing restates a value another source owns, or the copy is pinned by a
      test that reads the restating file at that site.
- [ ] **No cardinalities in prose.** Applied
      `.claude/rules/no-cardinality-restatements.md` to any sentence describing
      a declared shape.
- [ ] **Plugin prose is on a rung.** If this touches `plugins/**/*.md`, applied
      `.claude/rules/plugin-prose.md` — every fact cited, generated or pinned.
- [ ] **Contract change is rendered.** If `packages/contract-models` moved,
      `render_schemas.py check` and `render_prose_census.py check` are clean and
      the re-rendered pins are in the diff. No `schemas/` file hand-edited.
- [ ] **Tests grade the new behaviour**, and a deliberate mutation of it goes
      red. A test that passes against broken code is worse than no test.

## Verification

<!-- What you ran and what it said. "Tests pass" is not a result; paste the
     counts, the check names, or the mutation verdicts. -->
