---
paths:
  - "packages/*/src/**/*"
  - "packages/*/scripts/**/*"
  - "packages/*/pyproject.toml"
  - "rules/records/*.yaml"
  - ".github/workflows/contract-models-release.yml"
  - ".github/workflows/validator-release.yml"
---

# Rule: within a major, a passing document keeps passing

Governs every change that can alter what an installed `analitiq-validator` or
`analitiq-contract-models` accepts: an enforcer under `packages/*/src`, the
data shipped beside it that an enforcer derives from — the vendored Arrow type
grammar the accepted `arrow_type` spellings come from, the compiled rule
registry — a record's `severity` under `rules/records/`, the packaging under
`packages/*/scripts` that decides what the built wheel enforces, and the
`version` each `pyproject.toml` carries. What puts a change here is that it can
reach a released wheel's verdict, not which directory it sits in and not what
kind of file it is.

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

- **Reject.** A call to the validator rejects when its `passed` is false, or,
  for the contract models used on their own, the model refuses the document.
  Which findings keep `passed` from being true is owned by `rules/SCHEMA.md`,
  "Findings", and is not restated here: read the verdict, never a finding's
  `severity`, since a finding can cost the pass without carrying one. A
  pydantic `ValidationError` reaches the validator as an `error`-severity
  `fail` finding, so a model's refusal reaches the validator as a call that
  does not pass. A finding that carries a `severity` carries `error` or
  `warning` and nothing else, while a record may also declare `info` — so
  what answers the question is whether a document is rejected, never the
  label a record declares. A record reaching any severity is a bump only when
  an enforcer rejects a document with it.
- **The last stable release.** The newest release of the packages carrying no
  pre-release suffix. A pre-release is never the reference point: before the
  first stable release there is nothing to preserve, and a pre-release
  prepared after one is measured against that stable release like any other
  change. Asking the question of a release candidate therefore needs no
  special case — one preparing a later version within the major answers it
  the same way that version's release must, and one preparing the next major
  answers yes, which is the bump it is already carrying.
- **A document that release accepted.** One it could have been handed and did
  not reject — where what a call is handed is its whole input: the document,
  the files available to the call beside it, what each is named and where it
  sits relative to the others, and the value of every option the call is
  given. The invocation fixes the input, not what a given release reads from
  it: a file a later release starts reading was already part of the input the
  last stable release passed. A verdict is a function of all of it, so the
  question is asked of all of it: a change answers yes when it rejects any
  input the last stable release passed, never only when it rejects the
  document's bytes on their own. The promise is not keyed on a path. Which
  location a call is pointed at changes without any release, so the input is
  what is found there — the content, names and layout — not where it was
  found. The validator does not pass a document it cannot identify, so
  nothing of a kind no detector claimed was ever accepted.

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

- **A check promoted to `error`.** Ask which inputs the check fires on, and
  which it cannot evaluate. It is the arrival that is free and the promotion
  that costs, where the check reports on an input the release accepts; where
  it can only fire on an input already rejected for another reason, promoting
  it changes no verdict. The promotion can reach past the check's `fail`
  findings — a finding saying the check could not evaluate the rule is one —
  so ask `rules/SCHEMA.md`, "Findings", what each finding naming the rule now
  costs: an input the check skipped can stop passing with nothing in it newly
  detected. A rule whose violation breaks a run still arrives as a warning —
  the promise forbids the rejection, not the finding — and the record's
  `rationale` says what the run does with the violation.
- **A record gaining a rejecting enforcer.** A record one document settles
  alone is applied by a `@model_validator`, which can only refuse, so the
  record's declared severity does not soften what the enforcer does
  (`rules/SCHEMA.md` owns that placement); ask which documents it refuses. A
  record edited with no enforcer behind it refuses nothing.
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
- **A new finding id.** An id arrives with the check that emits it: a
  registered id with no call site is refused by
  `packages/validator/tests/test_check_registry_census.py`. So the question is
  asked of that check and the inputs it fires on. The id carries no verdict
  of its own.
- **A new finding with no `severity`.** A change whose only effect is a
  finding that carries no `severity` is not free on that account. Whether it
  costs the pass is `rules/SCHEMA.md`'s answer, not the missing label's; where
  it turns an input the last stable release passed into one that does not
  pass, the change answers yes.
- **A new check reading beside the document.** Say a check starts reading a
  file the document names and failing when that file disagrees with it. Asked
  of the document alone, the change can look free: handed without the files
  beside it, that document may never have passed. Asked of the whole input, a
  document together with files the last stable release passed it with, and
  which this change now fails, answers yes.
- **An option that relaxes a check.** Say an option lets a draft pass a check
  it would otherwise fail. Adding the option accepts more and answers no.
  Narrowing what it relaxes fails an input — the same document with the same
  option set — that the last stable release passed, and answers yes.
- **A loosening, a reworded message, a moved `path`.** Accepting more, or
  saying the same verdict differently, answers no.
