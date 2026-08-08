---
paths: packages/contract-models/**/*.py
---

# Rule: prose on the contract's own surface

Applies to the prose the contract publishes about itself — every pydantic field
description, model docstring and `Enum` docstring under `analitiq.contracts`.
These render into the JSON Schemas at `schemas.analitiq.ai`, and a published
`X.Y.Z.json` is immutable, so a sentence here outlives the change that wrote it
by more than any other prose in this repo.

Companion to `plugin-prose.md`, which governs the prose that ships to users
inside `plugins/`. Same split, different surface.

## What the census decides, and what you decide

`analitiq.contracts.shared.prose_census` catalogues every prose site and binds
it to a disposition — an `RULE-*` rule, a structural mechanism, a waiver, or
`descriptive=True` — with a `prose_hash` pinning the exact wording.
`test_prose_census.py` and `scripts/render_prose_census.py check` compare the
census to the live tree.

Those comparisons are set membership and a hash. They decide that a site is
catalogued, that no entry is dead, and that the wording has not moved since the
disposition was affirmed. **They do not decide whether the disposition is
right.** That question — does this sentence state an obligation an instance
could violate, and is the thing you named actually what carries it — is yours.

A hash mismatch is the summons. When one fires, re-read the sentence before
restamping; `render_prose_census.py write` restamps, it does not re-judge.

### Choosing the disposition

Ask what would happen to a document that ignored the sentence.

- Some rule rejects it → `rule_ids`, naming that `RULE-*` rule.
- The model's own shape rejects it (a `Literal`, a pattern, a bound, a
  discriminated union, `extra='forbid'`, a field validator) → `structural`,
  naming the mechanism.
- Nothing rejects it, and nothing here could → `waiver`, saying **why** it is
  not mechanisable: engine-owned at configure or run time, cross-document,
  authoring judgment. Prefer the shared reasons in `prose_obligation.py`
  (`UNKNOWABLE_SKIP`, `ENGINE_OWNED_DEFAULTING`, `ENGINE_CONDUCT`) so the census
  stays countable by category.
- Nothing could ignore it, because it asks for nothing → `descriptive=True`.

A description carrying several obligations may combine `rule_ids`,
`structural` and `waiver`; the waiver then names the unenforced remainder.

`descriptive=True` is the one to be suspicious of. It is the default in the
skeletons `write` prints, and it is the disposition under which an unenforced
obligation hides: read the sentence and ask whether an instance could contradict
it before accepting the default. Words like "must", "every", "only" and
"defaults to" are worth a second look — but a sentence can state an obligation
without any of them, and can carry all of them while stating none, which is
exactly why no check makes this call.

## Enum member docstrings

Pydantic publishes an `Enum`'s **class** docstring into the schema description
and never a member's, so member docstrings are outside the census. That is safe
only while they state no obligation. Keep a member line descriptive; when a
member carries a real requirement, state it in the class docstring — a censused
site — or bind it to a rule there.

## Declaring an unenforced half

When the contract enforces part of a rule and leaves part of it open, the
description says so, and the census entry's waiver says the same thing in the
form a reviewer greps. Both halves matter: the description is what an author
reads, the waiver is what an auditor counts.

`_RequestBase.transport_ref` is the worked example. Its NAME half —
`transport_ref` resolving to a declared transport — is enforced cross-document
by the validator's `endpoint-transport-ref` check. Its ORIGIN half — every URL
a request produces landing on that transport's origin — is enforced nowhere,
and the description says so rather than implying a guarantee.

Softening such a disclaimer is a wording change, so the hash pin fires and a
reviewer has to re-affirm the entry. Read the sentence then — the pin says the
words moved, not that the promise is still honest.
`test_endpoint_transport_ref.py` pins the unenforced *behaviour*, so it is what
tells you the day the gap closes and the disclaimer must go.

## Never restate a value

Constraint values — bounds, patterns, defaults, member lists — live in the
model, and a description that repeats one is a drift surface the census cannot
see (the hash pins the *wording*, and the wording is what stays fixed while the
model moves). Say what the constraint is for; let the field carry what it is.
`no-drift-surfaces.md` and `no-cardinality-restatements.md` are the general
cases.

## Quick test

> If someone authored a document that ignored this sentence, what tells them?

Name it. That name is the disposition. If the honest answer is "nothing", the
entry takes a waiver saying why — not `descriptive=True`, and not silence.
