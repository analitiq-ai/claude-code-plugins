# Rule: don't create drift surfaces

**Purpose:** avoid adding new places where a fact must be kept in sync by hand.
Every hand-maintained copy of something a single source already owns is a *drift
surface* — it silently rots when the source changes.

This rule is the *how-to-behave* checklist. The *why* and the canonical policy
live in `CLAUDE.md` → **"## Single source of truth (drift policy)"** — read it,
don't restate it here.

## The rule

Before you hardcode a value that some other source already owns — a
schema/contract enum, a vocabulary, a `$schema` URL, a field list, a package
version pin — **stop and reference the owner instead of copying it.**

A "value some other source owns" includes at least:
- schema-owned enums/vocabularies (kinds, auth types, transports, encodings,
  pagination styles, …) — owned by the published contract / `analitiq-contract-models`;
- canonical type vocabularies — owned by the contract models;
- dependency version pins (e.g. `analitiq-validator==…`) — one intended value,
  repeated across install/CI/docs;
- anything already stated once in `CLAUDE.md`, a `SKILL.md`, or a reference doc.

## Checklist when an edit adds a value list or a pinned value

1. **Does a single source already own this?** If yes → reference it (link the
   file/section, or read it at runtime). Do **not** paste a second copy.
2. **Is a copy genuinely unavoidable?** Only three reasons qualify:
   decision/mapping logic (e.g. `enum-mappers.md`), a test's assertion target,
   or a curated human-facing summary (e.g. the README support matrix). If it's
   none of those, you're making a drift surface — reference instead.
3. **If the copy is unavoidable, is it pinned?** An unavoidable restatement of a
   contract-owned value **must** be pinned by a drift test that reads the
   contract package — `tests/connector_builder/test_schema_drift.py` is the
   worked example — so a divergence fails the build. An *unpinned* copy of a
   contract value is a defect, not documentation.
4. **Collapse parallel copies.** Prefer one canonical restatement + references
   to it over N copies. When you touch a value that appears in several places,
   reduce the count if you can; never increase it.
5. **Repeated pins stay in lockstep.** When a version/const must appear at
   several call sites (install command, CI, docs), keep every site identical and
   cross-reference them so they move together.

## Quick test

> "If the owning source changes tomorrow, how many places must a human remember
> to edit — and will anything fail loudly if they forget?"

Answer should be **as few as possible**, and **each of them pinned by a test**.
If editing here adds an unpinned place, reference the owner instead.
