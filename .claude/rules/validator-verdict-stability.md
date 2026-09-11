---
paths:
  - "packages/*/src/**/*.py"
  - "packages/*/scripts/**/*.py"
  - "packages/*/pyproject.toml"
  - "rules/records/*.yaml"
---

# Rule: within a major, a passing document keeps passing

Governs every change that can alter what an installed `analitiq-validator` or
`analitiq-contract-models` accepts: an enforcer under `packages/*/src`, a
record's `severity` under `rules/records/`, the packaging under
`packages/*/scripts` that decides what the built wheel enforces, and the
`version` each `pyproject.toml` carries. What puts a change here is that it can
reach a released wheel's verdict, not which directory it sits in.

**The invariant:** the major version is a promise about verdicts. From the
first stable release onward, no release within a major rejects a document the
last stable release accepted.

## The question every change answers

> Does this change make the validator reject a document the last stable
> release accepted?

Yes is a major bump of both packages. No is not. Nothing else decides it: not
what the change is called, not which package it lands in, not whether the
verdict it replaces was right.

The question carries the whole rule, so each term in it is defined here rather
than left to the reader.

- **Reject.** The validator answers with a finding at severity `error`, or,
  for the contract models used on their own, the model refuses the document.
  A pydantic `ValidationError` reaches the validator as an `error` finding
  under the `contract-model` id, so a model that refuses and a check that
  reports `error` are one event here. A finding carries `error` or `warning`
  and nothing else, while a record may also declare `info` — so what answers
  the question is whether a document is rejected, never the label a record
  declares. A record reaching any severity is a bump only when an enforcer
  rejects a document with it.
- **The last stable release.** The newest release of the packages carrying no
  pre-release suffix. A pre-release is never the reference point. Before the
  first stable release there is nothing to preserve, and a pre-release
  prepared after one is measured against that stable release like any other
  change — so a narrowing merged during a release candidate still binds,
  because the stable release it is heading for inherits the promise.
- **A document that release accepted.** One it could have been handed and did
  not reject. A document of a kind no detector claimed was not accepted: the
  validator answers anything it cannot identify with an `error`. So the
  contract model that first claims a kind may reject a malformed one at
  `error` in the release that introduces it.

## Why the major carries it

A consumer that addresses the validator by major version, not by exact
version, takes every compatible release without a release of its own. That is
what a major is for, and it holds only while "compatible" means the verdict
holds: a release that turns a passing document into a failing one under the
same major is a document broken in the field by a release nobody chose.

## The packages move together

`analitiq-validator` pins `analitiq-contract-models` exactly and carries the
same version (`packages/validator/tests/test_contract_models_pin.py` holds
both), so a major bump in one is a major bump in both, and which side a change
falls on is decided once, for the pair.

## Worked examples

These apply the question; they are not a list to check a change against. A
change absent here is decided by the question, not by its absence.

- **A check promoted to `error`.** It rejects what it used to report. A check
  arrives at `warning` where the document it grades is one the release
  accepts, and the promotion is the bump, not the arrival. A rule whose
  violation breaks a run still arrives as a warning — the promise forbids the
  rejection, not the finding — and the record's `rationale` says what the run
  does with the violation.
- **A record gaining a rejecting enforcer.** A record one document settles
  alone is applied by a `@model_validator`, which can only refuse, so binding
  one to a record is a rejection whatever severity the record declared before
  (`rules/SCHEMA.md` owns that placement). A record edited with no enforcer
  behind it rejects nothing and answers the question no.
- **A model tightened.** A field made required, a `Literal` narrowed, a bound
  or pattern added. Ask the question of a document the last stable release
  accepted. `render_schemas.py write` classifies the rendered schema's diff
  and errs toward `major`; that is evidence, not the verdict — it grades the
  schema alone, and a `@model_validator` renders into no keyword it can see.
- **A hole closed.** A fix after which the validator rejects what it wrongly
  accepted still answers yes. The question keys on the verdict, not the
  intent: a wrong pass is a pass a consumer relied on.
- **What the wheel pins.** The generated `analitiq/contracts/__init__.py` fixes
  the host the `$schema` `Literal` fields are built for. Changing it makes the
  installed models reject documents naming the old host, which answers yes
  from a file outside `packages/*/src`.
- **A new finding id.** It changes no verdict on a document that already
  validated, so the question answers no.
- **A loosening, a reworded message, a moved `path`.** Accepting more, or
  saying the same verdict differently, answers no.
