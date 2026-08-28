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

`census/consumption/reachability.py` compares sets: every unread field has an
entry, no entry names a field the manifest now claims or the tree no longer
declares, no field carries more than one, and a `structural` entry sits on a
`Literal`.
`tests/census/test_contract_consumption.py` and
`scripts/render_contract_consumption.py check` run that comparison. **It never
decides whether the kind is right or the reason honest.** That is a reader's,
and this file is what the reader applies.

## The kind

`DispositionKind` in `census/consumption/disposition.py` owns the names; the
docstring there says what each means. What a reader settles is which one
applies:

- Something off the run-time path consumes the field — a person, plugin prose,
  the validator, the schema renderer → `authoring_only`, and the reason
  **names that consumer**. "Documentation" names nothing.
- Pydantic settles the value before the engine holds the object — a
  discriminator, a schema-pinned constant → `structural`. This is the one kind
  with a mechanical half: the guard refuses it on a field that is not
  `Literal`-typed.
- Nothing consumes it, and the question is **who owes the fix**:
  - the engine should honour what the author declared → `engine_gap`;
  - the contract should stop declaring it → `contract_surplus`, and the reason
    says why removal rather than adoption;
  - the engine does read it, by a means its manifest extractor cannot
    attribute → `manifest_gap`, filed against the manifest generator.

## The reason, and its halves

A reason for a gap kind carries a **manifest half** and a **consequence
half**. The manifest half — "the pinned manifest claims no read of …" — is a
comparison against the published artifact, pinned by the sha256 in
`census/consumption/pin.py` and kept current by the pin guard: the first rung
of `engine-behaviour-claims.md`. The consequence half — what the run does with
the field today — is a reading of the engine as it stands, the second rung.
Write it as what the run does, never as what the engine must always do, and
never rest a check on it: the gate is `claims` membership and nothing else.

A consequence half stays inside the engine. What a provider does with the
request that goes out is not a fact the manifest can carry, and an absence of
a read cannot establish a specific outcome on the far side of the wire.

A reason cites a `RULE-*` id as what **names** the obligation. Whether a
validator applies that rule is the record's `validator:` field, and a reason
that restates it is a second copy of a fact the registry owns.

## A pin bump re-reads every entry it touches

A new manifest version retires entries by claiming their fields, and the guard
finds those. It does not find an entry whose manifest half is still true and
whose consequence half has gone stale because the engine moved without
claiming the field. So a pin bump re-reads every entry on a model whose
`claims` changed, and re-affirms or rewrites the consequence half — the same
obligation `contract-prose.md` places on a prose site whose hash has moved.
