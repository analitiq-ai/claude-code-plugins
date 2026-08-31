---
paths:
  - "census/consumption/**/*.py"
---

# Rule: the disposition of an unread contract field

Governs every `FieldDisposition` and every `RecordAffirmation` under
`census/consumption/` — the entry a
contract field carries when the pinned consumption manifest claims no read of
it, and the entry a rule record carries when its `targets`/`fields` govern
such a field. `contract-prose.md` is the same split on prose sites; this file
is the same split on fields and on the records over them.

## What the guard decides, and what a reader decides

`census/consumption/reachability.py` compares sets, checks an annotation
shape (a `structural` entry must sit on a `Literal`) and holds a
`derivation_input` entry's `derives` to a field the same model declares, the
manifest claims, and a validator the model runs writes — the last taken from
the model's own validator registry, which is what pydantic runs, and read
off each validator's source as a call to the sanctioned derivation helper
naming the field as a literal: a call site and a constant rather than a
sentence; `ConsumptionReport` there carries every finding those can reach.
`tests/census/test_contract_consumption.py` and
`scripts/render_contract_consumption.py check` fail on every finding that
dataclass carries. **Neither decides whether a reason is honest, nor which
kind is right.** That is a reader's, and this file is what the reader
applies.

## The unit

An entry is keyed by the class that carries the field, inherited or not,
because that is how the manifest claims: by the class name it keys
`claims` under. A
base-class field is therefore one entry per variant that reaches it, each a
separate fact — a read on one variant and none on another is exactly the
state `stream.FullRefreshReplication.safety_window_seconds` records — and
variants with the same verdict share a named reason rather than a merged
entry. The prose census keys by declaring owner because its unit is one
description; do not carry that keying here. This section owns the unit;
`census/consumption/disposition.py` and the entries point here rather than
restating it.

## The kind

`DispositionKind` in `census/consumption/disposition.py` owns the names; the
module docstring in `census/consumption/disposition.py` says what each
means. What a reader settles is which one applies:

- Something off the run-time path consumes the field → `authoring_only`, and
  the reason **names that consumer**. "Documentation" names nothing. A
  consumer does not settle it alone: a field the document already states
  elsewhere is `contract_surplus` however many checks read the copy, and
  the reason then names each check and what it re-keys onto.
- Pydantic settles the value before the engine holds the object →
  `structural`.
- The contract computes another field of the same model from it, and the
  engine reads that one → `derivation_input`, and `derives` names it. The
  guard reads the model's validators for one writing that product; what it
  cannot decide, and you must, is that the field the entry sits on is an
  **input** to that derivation — a model with one derivation and several
  unread fields would accept any of them — and that the write is on a branch
  a document reaches. The
  reason says which field is computed from which and why the input is
  therefore not free — a value reaching the run under another name is not
  an ignored value. It does not say that the contract rejects a document
  contradicting the derivation; whether a rule is applied is the record's
  `validator:`, and the reason cites the id instead. Where the derived
  field is itself unread, nothing on the run-time path consumes either end,
  and the question is again what consumes them or who owes the fix.
- Nothing consumes it, and the question is **who owes the fix**:
  - the engine → `engine_gap`;
  - the contract → `contract_surplus`, and the reason says why removal
    rather than adoption;
  - the manifest generator, because the engine does read it by a read its
    extractor cannot attribute to a field → `manifest_gap`.

## The reason, and its halves

This section owns the two-halves rule; the census modules point here.
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

## A record over an unread field is affirmed

A rule record's `rationale` ships to users inside the compiled registry and
routinely explains the rule by what the engine does with the governed field.
`census/consumption/records.py` owns the guard — how records are located
against the unread set and what a `RecordAffirmation` in
`census/consumption/record_affirmations.py` pins. The link is the fields a
record names: a record naming no `fields:` is outside the guard — unlocated,
not affirmed, the record-side analogue of a model no root reaches — so a
record whose rationale leans on a field owes that field to its `fields:`
list, and a reviewer of a record that names none asks whether it should.
The guard summons the reader; this section is what the reader applies
before re-computing the entry:

- The rationale carries no unpinned engine read of a field the manifest
  leaves unread. Its engine claims land on a rung of
  `engine-behaviour-claims.md`, following "The reason, and its halves"
  above as if the rationale were a gap entry's reason.
- The rule still earns its severity with the manifest fact in view: what the
  check grades must be defensible without the engine reading the field — a
  closed vocabulary, an agreement between documents — or the record says
  which way it fails if the reading goes stale.
- A rationale already stating that the field reaches nothing is affirmed as
  it stands; the affirmation records that a reader checked, not that a
  rewrite happened.

Re-affirming is re-reading against the current unread set and then
re-computing the refs and hash — never re-computing alone.

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
4. The record guard reports every affirmation whose refs the bump moved and
   every record newly governing an unread field. Each is a re-read of the
   rationale under the section above — and where the bump claims a field a
   rationale leaned on as unread, a rewrite of the rationale too.
