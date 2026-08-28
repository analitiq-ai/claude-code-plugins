---
paths:
  - "census/consumption/**/*.py"
---

# Rule: the disposition of an unread contract field

Governs every `FieldDisposition` under `census/consumption/` — the entry a
contract field carries when the pinned consumption manifest claims no read of
it. `contract-prose.md` is the same split on prose sites; this file is the
same split on fields.

## What the guard decides, and what a reader decides

`census/consumption/reachability.py` compares sets, and `ConsumptionReport`
there carries every finding that comparison can reach.
`tests/census/test_contract_consumption.py` and
`scripts/render_contract_consumption.py check` fail on every finding that
dataclass carries. **Neither decides whether the kind is right or the reason
honest.** That is a reader's,
and this file is what the reader applies.

## The unit

An entry is keyed by the class that carries the field, inherited or not,
because that is how the manifest claims: by the class the engine holds. A
base-class field is therefore one entry per variant that reaches it, each a
separate fact — a read on one variant and none on another is exactly the
state `stream.FullRefreshReplication.safety_window_seconds` records — and
variants with the same verdict share a named reason rather than a merged
entry. The prose census keys by declaring owner because its unit is one
description; do not carry that keying here.

## The kind

`DispositionKind` in `census/consumption/disposition.py` owns the names; the
module docstring in `census/consumption/disposition.py` says what each
means. What a reader settles is which one applies:

- Something off the run-time path consumes the field → `authoring_only`, and
  the reason **names that consumer**. "Documentation" names nothing.
- Pydantic settles the value before the engine holds the object →
  `structural`.
- Nothing consumes it, and the question is **who owes the fix**:
  - the engine → `engine_gap`;
  - the contract → `contract_surplus`, and the reason says why removal
    rather than adoption;
  - the manifest generator, because the engine does read it by a read its
    extractor cannot attribute to a field → `manifest_gap`.

## The reason, and its halves

A reason for a gap kind carries a **manifest half** and a **consequence
half**. The manifest half — "the pinned manifest claims no read of …" — is a
comparison against the published artifact, pinned by the sha256 in
`census/consumption/pin.py` and kept current by the pin guard: the first rung
of `engine-behaviour-claims.md`. The consequence half — what the run does with
the field today — is a reading of the engine as it stands, the second rung.
Write it as what the run does, never as what the engine must always do, and
never rest a check on it: the gate is a non-empty `claims` entry and nothing
else.

A consequence half stays inside the engine. What a provider does with the
request that goes out is not a fact the manifest can carry, and an absence of
a read cannot establish a specific outcome on the far side of the wire.

A reason cites a `RULE-*` id as what **names** the obligation. Whether a
validator applies that rule is the record's `validator:` field, and a reason
that restates it is a second copy of a fact the registry owns.

## A pin bump re-reads every entry it touches

This section owns the pin-bump procedure; `census/consumption/pin.py`, the
pin guard and the root `CLAUDE.md` point here rather than restating it.

1. Replace the vendored manifest with the newly published object, byte for
   byte, and move the version and sha256 constants in
   `census/consumption/pin.py` together (the sha is `sha256` of the published
   bytes). The manifest
   itself is never edited.
2. Run `scripts/render_contract_consumption.py check`. It reports the entries
   whose fields the new manifest now claims — delete those — and the fields
   it leaves unread with no entry — write a disposition for each, by hand,
   under this file.
3. Re-read every entry on a model whose `claims` changed. A new manifest
   version retires entries by claiming their fields, and the guard finds
   those. It does not find an entry whose manifest half is still true and
   whose consequence half has gone stale because the engine moved without
   claiming the field. So re-affirm or rewrite the consequence half — the
   same obligation `contract-prose.md` places on a prose site whose hash has
   moved.
