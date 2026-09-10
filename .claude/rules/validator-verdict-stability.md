---
paths:
  - "packages/*/src/**/*.py"
  - "packages/*/pyproject.toml"
  - "rules/records/*.yaml"
---

# Rule: within a major, a passing document keeps passing

Governs `analitiq-validator` and `analitiq-contract-models` from their first
stable release onward — every enforcer under `packages/*/src` (a `finding(…)`
call in `analitiq.validator`, a `@model_validator` or a field constraint under
`analitiq.contracts`), every record's `severity` under `rules/records/`, and
the `version` each package's `pyproject.toml` carries.

**The invariant:** the major version is a promise about verdicts. Within a
major, a document that passes keeps passing. A pass is the validator's
verdict — no finding at severity `error` — and, for the contract models on
their own, the model accepting the document. Nothing a minor or patch release
ships turns either into a rejection.

## Why the major carries it

A consumer that addresses the validator by major version, not by exact
version, takes every compatible release without a release of its own. That is
what a major is for, and it holds only while "compatible" means the verdict
holds: a check that turns a passing document into a failing one under the same
major is a document broken in the field by a release nobody chose. A
pre-release promises nothing, so the promise starts at the first stable
release.

## What moves the major

Any change after which the validator rejects a document the current release
accepts, whatever the change is called:

- **A check promoted to `error`.** A new check ships at severity `warning`;
  its promotion is the major bump, not its arrival. A rule whose violation
  breaks a run still arrives as a warning — the promise forbids the rejection,
  not the finding — and the record's `rationale` says what the run does with
  the violation.
- **A model tightened.** A field made required, a `Literal` narrowed, a bound
  or pattern added, a `@model_validator` raising where none did. A pydantic
  `ValidationError` reaches the validator as an `error` finding under the
  `contract-model` id and has no severity to be downgraded to, so a model
  change that rejects an accepted document moves the major on its own.
- **A hole closed.** A fix after which the validator rejects what it wrongly
  accepted is a tightening. The policy keys on the verdict, not the intent: a
  wrong pass is a pass a consumer relied on.

## What does not

- **A new finding id.** A new `validator` id (the `VALIDATOR_IDS` registry in
  `analitiq.validator`) arriving at severity `warning` is compatible: a
  consumer that does not know an id handles it as it handles any warning,
  which is why a new check may arrive only as one.
- **A loosening.** Accepting a document the current release rejects.
- **A reworded message, a moved `path`.** The verdict is the promise; the
  finding's wording is not.

## The packages move together

`analitiq-validator` pins `analitiq-contract-models` exactly and carries the
same version (`packages/validator/tests/test_contract_models_pin.py` holds
both), so a major bump in one is a major bump in both, and which side a change
falls on is decided once, for the pair.

## Applying it while editing

- **A record's `severity`.** `warning` → `error` is a major bump whether the
  enforcer is code or a reader: the compiled registry ships in the wheel and
  the rendered references print the severity, so the promotion reaches every
  consumer whichever applies the rule. A demotion, and `info` → `warning`, are
  compatible.
- **A new check.** It lands as a `finding(…, "warning", …)` in
  `analitiq.validator` with a record at `severity: warning` — even a rule one
  document settles alone, which would otherwise be a `@model_validator`: the
  model layer has nothing below `error`, so a warning has one home. Promotion
  is when it moves into the model, where `rules/SCHEMA.md` places a rule one
  document settles.
- **A model constraint.** Ask whether a document the current release accepts
  now fails. `render_schemas.py write` classifies the rendered schema's diff
  and errs toward `major`; that classification is evidence, not the verdict —
  it grades the schema alone, and a `@model_validator` renders into no keyword
  it can see.
- **A change on the breaking side.** Say so in the PR description, and the
  release that carries it is a major of both packages — the one coordinated
  release the root `CLAUDE.md` describes under "Releases and credentials". The
  first stable release is `1.0.0`.
