---
paths:
  - "*.md"
  - "plugins/**/*.md"
  - "packages/**/*.py"
  - "census/**/*.py"
  - "scripts/**/*.py"
  - "tests/**/*.py"
  - "rules/records/*.yaml"
  - ".github/workflows/*.yml"
---

# Rule: no hand-maintained second copy

**The invariant:** every value has one owner, and every other place it appears
either references that owner or is pinned to it by a test that fails when the
two diverge. A hand-maintained copy is a *drift surface* — it rots silently when
the owner changes.

The property that follows: a change to an owning source reaches every place
that carries the value, and any place it does not reach fails loudly.

## What has an owner

A value has an owner when some source is authoritative for it. At least
these do:

- schema-owned enums and vocabularies (kinds, auth types, transports,
  encodings, pagination styles, …) — owned by the published contract and
  `analitiq-contract-models`;
- canonical type vocabularies — owned by the contract models;
- dependency version pins (e.g. `analitiq-validator==…`) — one intended value,
  repeated across install, CI and docs;
- anything already stated once in `CLAUDE.md`, a `SKILL.md`, or a reference doc.

A value with an owner is referenced, not pasted: the file or section is linked,
or the value is read at runtime.

## Permitted copies

A copy is permitted in exactly these forms:

- decision or mapping logic (e.g. `enum-mappers.md`);
- a test's assertion target;
- a curated human-facing summary — a table written for a person weighing
  options, not for an agent authoring a document.

Every other copy is a drift surface. "Nothing enforces it, so it has to be
written down" is not one of these forms: the rule registry carries unenforced
rules too — a record with no `validator`, whose `rationale` says what would have
to be read to catch a violation. An obligation with no id is a missing
`rules/records/` entry, and what stays in prose beside the citation is the craft
the record does not carry.

## The state a permitted copy is in

- **Pinned.** A copy is pinned by a test that reads the owner and fails on
  divergence, whichever source the owner is —
  `tests/connector_builder/test_schema_drift.py`, reading the contract package,
  is the worked example. An unpinned copy of an owned value is a defect, not
  documentation.
- **Minimal.** One canonical restatement plus references, never N parallel
  copies. An edit touching a value already copied to several places reduces
  that number where it can, and never raises it.
- **In lockstep.** Where a pin must appear at several call sites (install
  command, CI, docs), every site is identical and cross-references the others,
  so they move together.
