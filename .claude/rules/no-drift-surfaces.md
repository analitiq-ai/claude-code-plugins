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

The property that follows: when an owning source changes, exactly one place
needs a human edit, and anything that forgets fails loudly.

## What has an owner

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
- a curated human-facing summary (e.g. the README support matrix).

Every other copy is a drift surface. "Nothing enforces it, so it has to be
written down" is not one of these forms: the rule registry carries unenforced
rules too — a record with no `validator`, whose `rationale` says what would have
to be read to catch a violation. An obligation with no id is a missing
`rules/records/` entry, and what stays in prose beside the citation is the craft
the record does not carry.

## The state a permitted copy is in

- **Pinned.** A copy of a contract-owned value is pinned by a drift test that
  reads the contract package — `tests/connector_builder/test_schema_drift.py` is
  the worked example — so divergence fails the build. An unpinned copy of a
  contract value is a defect, not documentation.
- **Minimal.** One canonical restatement plus references, never N parallel
  copies. The number of copies of a value never rises.
- **In lockstep.** Where a pin must appear at several call sites (install
  command, CI, docs), every site is identical and cross-references the others,
  so they move together.
