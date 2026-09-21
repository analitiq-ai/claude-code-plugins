# analitiq-validator

The Analitiq **artifact validator**. It validates Analitiq JSON documents against
the **contract models** — the same Pydantic models the published JSON Schemas are
generated from — plus the cross-file and cross-document checks a single-document
model cannot express, so authoring, the connector-builder plugin, and downstream
consumers all enforce one contract with no drift. It is structured so per-kind
validators slot in without touching each other.

Today it covers:

- **single documents** — one document graded as the kind its caller names;
- **packages** — the connector, connection and pipeline packages, each graded as
  the published package schema locates its documents, plus the checks across
  them;
- **pipeline bundles** — the cross-document referential integrity of an assembled
  run (pipeline + streams + connections + connectors + endpoints).

Single-document validity (structure **and** every cross-field rule) is delegated
to `TypeAdapter(...).validate_python` from `analitiq-contract-models`
(`analitiq.contracts`). It runs **offline** — no schema fetch, no network. On top
of the models it adds only what a single-document model cannot express, and
every kind emits the same uniform `{passed, findings[]}`.

**Package checks:**

- **coverage** — a connector package's type map carries the sections its kind
  calls for, and an API connector's read map covers every
  `(native_type, arrow_type)` its endpoint documents declare;
- **filename ↔ id** — an endpoint document is named `{endpoint_id}.json`;
- **advisory warnings** the contract tolerates — duplicate type-map rules, read
  patterns spelling a lowercase literal, regex natives spelling a container that
  renders a scalar, write-map vocabulary gaps.

**Pipeline-bundle referential integrity** (`validate_pipeline_bundle(bundle)`) —
a bundle is a mapping of already-parsed `{pipeline, streams, connections,
connectors, endpoints}` documents, checked for internal consistency independent of
any storage or on-disk layout:

- every `pipeline.streams[]` ref resolves to exactly one bundled stream document;
- every referenced connection is present in the bundle;
- every stream `endpoint_ref.connection_id` is one of the pipeline's connections;
- every connection's `connector_id` is present among the bundled connectors;
- every `scope='connection'` endpoint_ref resolves to a bundled endpoint document;
- the bundle names a pipeline (has a `pipeline_id`).

Referential integrity is separate from **runnability**. `require_runnable=True`
(the default) additionally gates the pipeline on `status='active'` with at least
one runnable stream — the check an executor needs. An authoring tool validating a
**draft** bundle passes `require_runnable=False` to get the referential checks
without the active-status gate.

It does not assume the documents were already contract-validated, so a missing
reference field (a connection naming no connector, a stream slot with no
`endpoint_ref`) is reported as an unresolved reference — while per-document *shape*
(field types, lengths, enums) remains each document model's own job. Stream and
connection refs match on their base form, so a `{id}_v{n}` versioned ref resolves
the document that declares the bare `{id}` (connector identities match whole).

## Packages

`validate_package` takes a `ValidatePackageRequest` from
`analitiq.contracts.validation_requests`: the package it names and its documents'
text, keyed by path within the package. `validate_package_at` reads the same
package from a directory. The package model owns which key is its root and
which kind each located key holds; a key it does not locate is not part of the
package and is not graded. A request never carries a directory to read from,
and its keys are never resolved against a filesystem.

## Rule cases

Some rules enforced by a cross-document check ship example document sets with
the package, each one a directory holding a `bundle.json`. A rule is not required to carry any, so the corpus
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

```bash
analitiq-validate --document connection.json --kind connection
analitiq-validate --package path/to/connector --kind connector-package
```

`--kind` names what the document or package is and is required; naming a kind
outside the published ones is a usage error that lists them. Validation is
always model-driven and offline.

Output is a JSON report (`{"passed": bool, "findings": [...]}`) on stdout; the
process exits non-zero exactly when `passed` is `false` — a `fail` finding at
`severity: "error"`, or a `notApplicable` finding naming a rule that is (or,
naming none, might as well be) error-tier, since a check that could not run
gets no benefit of the doubt.

A pipeline bundle is assembled from many documents, so it is validated as a
library call rather than from a single file:

```python
from analitiq.validator import validate_pipeline_bundle

findings = validate_pipeline_bundle(
    {
        "pipeline": pipeline_doc,
        "streams": stream_docs,
        "connections": connection_docs,
        "connectors": connector_ids,          # the connector identities present
        "endpoints": connection_endpoint_docs,  # scope='connection', connection_id, endpoint_id
    },
    require_runnable=False,  # authoring a DRAFT bundle: referential checks, no active-status gate
)
if any(f["severity"] == "error" for f in findings):
    raise SystemExit(findings)
```

## Source of truth

The canonical source is
[**`analitiq-ai/claude-code-plugins`**](https://github.com/analitiq-ai/claude-code-plugins),
under `packages/validator/` — with the contract models beside it at
`packages/contract-models/`, and the public JSON Schemas rendered from those same
models by `scripts/render_schemas.py`.

The package is authored directly in the public `analitiq.validator` namespace;
it is staged verbatim at build time, not rendered from a private tree. Edit the
source there, not the installed copy.
