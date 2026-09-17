"""The path-free document-set API.

`analitiq.validator._core.validate_document` and `analitiq.validator.connectors
.check_coverage` both read real files off disk: `check_coverage` walks a
connector's sibling type-maps and endpoints via `doc_path.parent`, and the
pipeline-builder plugin's own `_assemble_bundle` (`plugins/
analitiq-pipeline-builder/scripts/validate.py`) globs an entire pipeline
directory the same way. A consumer that never has those files on a local
filesystem — a hosted validator wrapping this package as a remote tool,
registry CI, any caller handed document content directly rather than a
directory to read it from — cannot use either route.

This module fixes the contract such a consumer calls instead. Each entry point
takes the request model that names the unit being submitted
(`analitiq.contracts.validation_requests`) and answers in one
`ValidationEnvelope`. A caller states what it is sending by choosing the
function: nothing here reads content to work out whether it was handed one
document or a package, or which kind of package, and no entry point takes a
parameter that selects between them. One package request model serves every
package entry point precisely because the function carries the kind.

**The request model is the argument gate.** A key outside the document-key
grammar, a value that is not text, a key that is also a directory of another,
a package past the document ceiling, an `entity` outside the published document
schema names — each is a `pydantic.ValidationError` raised at construction, for
an in-process caller and a remote one alike, so there is one gate rather than
one per transport. `bytes` and `bytearray` are what the model does not refuse:
pydantic decodes them to `str` in lax mode, byte-order mark included, so a
caller that read its files as bytes has that document reported as unreadable
*content* rather than as a malformed argument. Nothing below re-checks an
argument the model already refuses, and a malformed argument never becomes a
finding.
Document *content* is the opposite and is what this module exists to judge:
unparseable text, a wrong shape, a contract-model failure or a cross-file
inconsistency is a finding rather than a raised error.

An exception raised by this module's own assembly or scoping is a defect in
this package: it propagates. Turning one into a finding would fail an author's
document for a bug the author cannot fix. `_run_guarded` in
`analitiq.validator._core` is a separate, older mechanism and is untouched: it
contains a crash inside a check bound to one rule, and inside whole-document
dispatch where `validate_document` applies it, as a `check-crashed`
`notApplicable` finding — so a crash inside the path-based route this module
delegates a single document to still comes back as a finding.

The package entry points below still raise `NotImplementedError`; the
behaviour each must satisfy is fixed by
`packages/validator/tests/test_document_set.py`.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    # Annotation-only. `from __future__ import annotations` defers every
    # annotation here to a string, and no body below needs a model at runtime,
    # so nothing imports the contract package when this module loads. That is
    # what keeps the import order safe: this module is imported before
    # `connectors`, whose `try/except ImportError` around its contract-model
    # imports turns a missing `analitiq-contract-models` into the structured
    # "missing dependency" diagnostic, and an unconditional import here would
    # pre-empt that guard with a raw traceback instead. The cost is that these
    # annotations do not resolve at run time: `typing.get_type_hints` on the
    # entry points below needs the request models handed to it as a namespace.
    from analitiq.contracts.validation_requests import (
        ValidatePackageRequest,
        ValidateSingleDocumentRequest,
    )


class _FindingRequired(TypedDict):
    """`finding()` sets every key below unconditionally on every result it
    builds — see `Finding`."""

    message_id: str
    kind: Literal["fail", "notApplicable", "informational"]
    path: str
    message: str


class Finding(_FindingRequired, total=False):
    """One entry of a `ValidationEnvelope`'s `findings` list — the shape
    `rules/SCHEMA.md`'s "Findings" section defines and
    `analitiq.validator.finding` already constructs. Restated here only so this
    module's signatures are checkable; the shape itself stays owned by
    `finding()`, and `test_document_set.py::
    test_finding_matches_the_keys_finding_builder_produces` pins the restated
    keys, and which of them are required, to what `finding()` actually
    produces."""

    rule: str
    severity: Literal["error", "warning"]


class ValidationEnvelope(TypedDict):
    """The result of every entry point below: flat, not a nested per-document
    breakdown — a finding carries the `path` the check that produced it
    reports, so a document validated this way and the same document validated
    through the path-based single-document route report through one shape. The
    one `path` this module decides itself is an embedded connector subtree's,
    scoped under that subtree's key prefix. `passed` is `False` exactly when
    `findings` holds one that `finding_costs_a_pass` accepts, which is not the
    same as "a finding at `severity: error`": an unchecked error-tier rule
    costs a pass too."""

    passed: bool
    findings: list[Finding]


def _envelope(findings: list[Finding]) -> ValidationEnvelope:
    """Wrap `findings` in the one `ValidationEnvelope` shape every entry point
    in this module answers with, via `_core._passed` so `main()` and this
    module answer "did this document pass" identically."""
    from analitiq.validator._core import _passed
    return {"passed": _passed(findings), "findings": findings}


def _detected_entity(document: object) -> str | None:
    """The published document-schema name the core registry's own detection
    would assign `document`, or `None` when no registered kind claims it — the
    same outcome `_core._dispatch` reports as `unrecognized-document`.

    Walks the live `_KIND_REGISTRY` in its real registration order rather than
    a separately hand-copied precedence list, so the order detectors are tried
    is always read from the registry, never duplicated. `_KIND_REGISTRY`'s own
    entries carry no name for either half of the pair; each registration
    idiom keeps a name on a different half — a `register_kind` call site
    passes a named validator, while `register_model_and_schema_kind` builds an
    anonymous validator closure per call and only ever names its detector —
    so the two tables below key off whichever half is actually a stable,
    importable name for that registration.

    `_validate_pipeline_bundle`'s registration is deliberately absent from
    both tables: no published document-schema name denotes an assembled
    bundle, so a document happening to match its detector falls through to
    `None` the same as one no detector recognises at all.
    """
    from analitiq.validator import _core, is_connection_doc, is_pipeline_doc, is_stream_doc
    from analitiq.validator.connectors import (
        _validate_api_endpoint,
        _validate_connector,
        _validate_database_endpoint,
        _validate_kindless_connector,
        _validate_type_map,
        _type_map_direction,
    )

    entity_by_validator = {
        _validate_connector: "connector",
        _validate_api_endpoint: "api-endpoint",
        _validate_database_endpoint: "database-endpoint",
        _validate_kindless_connector: "connector",
    }
    entity_by_detector = {
        is_connection_doc: "connection",
        is_stream_doc: "stream",
        is_pipeline_doc: "pipeline",
    }

    for detector, validator in _core._KIND_REGISTRY:
        if not detector(document):
            continue
        if validator is _validate_type_map:
            return f"type-map-{_type_map_direction(document)}"
        if validator in entity_by_validator:
            return entity_by_validator[validator]
        return entity_by_detector.get(detector)
    return None


def validate_single_document(
        request: ValidateSingleDocumentRequest) -> ValidationEnvelope:
    """Validate one document supplied as its file text.

    `request.entity` names the published document schema the caller says the
    text is written against. It is checked, not trusted: the core registry's
    own detection still runs, and a document whose detected schema is not the
    declared one is reported as a finding rather than validated as whatever it
    resembles. Text the JSON parser cannot read is a finding on the document,
    not a raised error.

    Resolving a detected schema name for a type map means reading the
    document's own `direction` the way `analitiq.validator.connectors`
    already does — the core registry detects a type map by shape and does not
    itself separate the read direction from the write one, while `entity`'s
    vocabulary names them separately. It is that resolution being reused, not a
    second direction rule.

    Wraps the path-based route's bare findings list in a `ValidationEnvelope`,
    so every entry point in this module answers in one shape.
    """
    from analitiq.validator._core import _unreadable_document_finding, finding, validate_document

    try:
        document = json.loads(request.document)
    except (json.JSONDecodeError, RecursionError) as exc:
        # RecursionError is a RuntimeError, so nesting deep enough to exhaust the
        # parser's stack escapes the JSONDecodeError arm — and it is still content
        # a caller sent, not a defect in this package.
        return _envelope([_unreadable_document_finding(exc)])

    detected = _detected_entity(document)
    if detected != request.entity:
        if detected is None:
            message = (
                f"declared entity {request.entity!r}, but this document's own "
                "content matches no published document schema.")
        else:
            message = (
                f"declared entity {request.entity!r} does not match "
                f"{detected!r}, what this document's own content declares.")
        return _envelope([finding(
            message_id="entity-mismatch", kind="fail", path="/", message=message)])

    return _envelope(validate_document(document))


def validate_connector_package(request: ValidatePackageRequest) -> ValidationEnvelope:
    """Validate a connector package supplied as in-memory documents instead of
    files on disk: the connector document, its sibling type maps, and — for an
    api connector — its `endpoints/*.json` files, cross-checked the way
    `analitiq.validator.check_coverage` already does from a filesystem path.

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_connector_package is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")


def validate_pipeline_package(request: ValidatePackageRequest) -> ValidationEnvelope:
    """Validate a pipeline package supplied as in-memory documents instead of
    files on disk: the pipeline document, its sibling `streams/*.json`, and
    every `connections/*/connection.json` (plus their scoped endpoints and type
    maps) — assembled the way `plugins/analitiq-pipeline-builder/scripts/
    validate.py`'s `_assemble_bundle` already does from a filesystem root, then
    checked for referential integrity.

    A `connectors/<slug>/definition/...` subtree is validated by calling
    `validate_connector_package` on it and scoping the returned findings'
    `path` under the subtree's key prefix, so an embedded connector reports its
    own coverage findings — native-type coverage, `transport_ref` resolution,
    duplicate endpoint ids. Today's plugin never reports those at all:
    `_assemble_bundle` reads such a subtree's `connector.json` only for its
    `connector_id`, and `_connector_endpoint_sets` reads the subtree's endpoint
    ids only for stream-ref resolution.

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_pipeline_package is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")
