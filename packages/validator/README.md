# analitiq-validator

The Analitiq **artifact validator**. It validates Analitiq JSON documents against
the **contract models** — the same Pydantic models the published JSON Schemas are
generated from — plus the cross-document checks a single-document model cannot
express, so authoring, the connector-builder plugin, and downstream
consumers all enforce one contract with no drift. It is structured so per-kind
validators slot in without touching each other.

It covers the authored documents — connector, endpoint, type-map,
`connection`, `stream` and `pipeline` — one at a time or as the packages and
workspace that hold them.

Single-document validity (structure **and** every cross-field rule) is delegated
to `TypeAdapter(...).validate_python` from `analitiq-contract-models`
(`analitiq.contracts`). It runs **offline** — no schema fetch, no network. On top
of the models it adds only what a single-document model cannot express — the
connection / stream / pipeline kinds are pure model validation (the model IS the
whole contract). A caller holding files reads them and builds the request.

**Connector-package cross-document checks:**

- **coverage** — a connector package's type map carries the sections its
  connector's kind calls for, and an API connector's read map covers every
  `(native_type, arrow_type)` its endpoint documents declare;
- **filename ↔ id** — an endpoint document sits at `{endpoint_id}.json`;
- **advisory warnings** the contract tolerates — duplicate type-map rules, read
  patterns spelling a lowercase literal, regex natives spelling a container that
  renders a scalar, write-map vocabulary gaps.

## In-memory requests

`analitiq.validator.document_set` grades documents handed over as text, never
read from a filesystem. `validate_single_document`, `validate_package` and
`validate_workspace` each take the request model naming that unit, from
`analitiq.contracts.validation_requests`; the model owns the request shape and
is where a malformed argument is refused. A document is graded as the kind the
request names, or the kind its location gives it inside a package or
workspace. Checks spanning documents run where every document they read is
held. That module's docstring is the contract; it is not restated here.

## Rule cases

Some rules enforced by a cross-document check ship example document sets with
the package, each one a directory holding a workspace, graded by `validate_workspace`. A rule is not required to carry any, so the corpus
grades only the rules it holds cases for. `rule_cases()` loads them and `case_mismatch(case)`
grades one against the installed validator, returning `None` when the
validator agrees:

```python
from analitiq.validator.rule_cases import case_mismatch, rule_cases

mismatches = [m for m in map(case_mismatch, rule_cases()) if m]
```

## Install

```bash
pip install analitiq-validator
```

This pulls `analitiq-contract-models` (and pydantic) transitively.

## Use

```python
from analitiq.contracts.validation_requests import ValidatePackageRequest
from analitiq.validator import validate_package

verdict = validate_package(ValidatePackageRequest(
    package_kind="connector",
    documents={"definition/connector.json": connector_text, ...},
))
```

Every entry point answers `{"passed": bool, "findings": [...]}`; `passed` is
false exactly when some finding costs it — a `fail` finding at
`severity: "error"`, or a `notApplicable` finding naming a rule that is (or,
naming none, might as well be) error-tier, since a check that could not run
gets no benefit of the doubt.

## Source of truth

The canonical source is
[**`analitiq-ai/claude-code-plugins`**](https://github.com/analitiq-ai/claude-code-plugins),
under `packages/validator/` — with the contract models beside it at
`packages/contract-models/`, and the public JSON Schemas rendered from those same
models by `scripts/render_schemas.py`.

The package is authored directly in the public `analitiq.validator` namespace;
it is staged verbatim at build time, not rendered from a private tree. Edit the
source there, not the installed copy.
