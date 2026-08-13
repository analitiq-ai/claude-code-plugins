---
paths:
  - "packages/contract-models/src/**/*.py"
  - "census/**/*.py"
---

# Rule: prose on the contract's own surface

Governs every pydantic field description, model docstring and `Enum` docstring
under `analitiq.contracts` — the prose the contract publishes about itself.
These render into the JSON Schemas at `schemas.analitiq.ai`, and a published
`X.Y.Z.json` is immutable, so a sentence here outlives the change that wrote it
by more than any other prose in this repo. `plugin-prose.md` is the same split
on the prose that ships inside `plugins/`.

## What the census decides, and what you decide

`census/` binds every prose site to a disposition — a `RULE-*` rule, a
structural mechanism, a waiver, or `descriptive=True` — with a `prose_hash`
pinning the wording. `tests/census/test_prose_census.py` and
`scripts/render_prose_census.py check` compare it to the live tree. It sits
outside `packages/contract-models/src/` because it keeps this repo's own wording
honest rather than being part of the contract, so it does not ship in the wheel.

Those comparisons are set membership and a hash: a site is catalogued, no entry
is dead, the wording has not moved since the disposition was affirmed. **They
never decide whether the disposition is right.** That question — does this
sentence state an obligation an instance could violate, and does the thing you
named carry it — is yours.

A hash mismatch is the summons: re-read the sentence before restamping, because
`render_prose_census.py write` restamps without re-judging.

### Choosing the disposition

Ask what would happen to a document that ignored the sentence.

- Some rule rejects it → `rule_ids`, naming that `RULE-*` rule.
- The model's own shape rejects it (a `Literal`, a pattern, a bound, a
  discriminated union, `extra='forbid'`, a field validator) → `structural`,
  naming the mechanism.
- Nothing rejects it and nothing here could → `waiver`, saying **why** it is not
  mechanisable: engine-owned at configure or run time, cross-document, authoring
  judgment. Prefer the shared reasons in `census/obligation.py`
  (`UNKNOWABLE_SKIP`, `ENGINE_OWNED_DEFAULTING`, `ENGINE_CONDUCT`) so the census
  stays countable by category.
- Nothing could ignore it, because it asks for nothing → `descriptive=True`.

A description carrying several obligations may combine `rule_ids`, `structural`
and `waiver`, the waiver naming the unenforced remainder.

Be suspicious of `descriptive=True`: it is the default in the skeletons `write`
prints, and the disposition an unenforced obligation hides under. "Must",
"every", "only" and "defaults to" earn a second look — but a sentence can state
an obligation without any of them, and carry all of them while stating none,
which is why no check makes this call.

## Enum member docstrings

Pydantic publishes an `Enum`'s **class** docstring into the schema description
and never a member's, so member docstrings sit outside the census — safe only
while they state no obligation. Keep member lines descriptive; a real
requirement goes in the class docstring, which is censused, or binds to a rule
there.

## Declaring an unenforced half

When the contract enforces part of a rule and leaves part open, the description
says so and the waiver says the same in the form a reviewer greps: the
description is what an author reads, the waiver is what an auditor counts.

`_RequestBase.transport_ref` is the worked example. Its NAME half —
`transport_ref` resolving to a declared transport — is enforced cross-document
by the validator's `endpoint-transport-ref` check. Its ORIGIN half — every URL
a request produces landing on that transport's origin — is enforced nowhere,
and the description says so rather than implying a guarantee.

Softening such a disclaimer trips the hash pin, so a reviewer re-affirms the
entry. Read the sentence then: the pin says the words moved, not that the
promise is still honest. `test_endpoint_transport_ref.py` pins the unenforced
*behaviour*, so it is what tells you the day the gap closes and the disclaimer
must go.

## Never restate a value

Bounds, patterns, defaults and member lists live in the model. A description
repeating one is a drift surface the census cannot see: the hash pins the
wording, and the wording holds still while the model moves. Say what the
constraint is for; let the field carry what it is. `no-drift-surfaces.md` and
`no-cardinality-restatements.md` are the general cases.

## Quick test

> If someone authored a document that ignored this sentence, what tells them?

Name it. That name is the disposition. If the honest answer is "nothing", the
entry takes a waiver saying why — not `descriptive=True`, and not silence.
