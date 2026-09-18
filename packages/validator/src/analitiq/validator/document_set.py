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
one per transport. `bytes` and `bytearray` holding UTF-8 pass that gate:
pydantic decodes them to `str` in lax mode, so a caller that read its files as
bytes is validated as though it had sent the text — except that a byte-order
mark survives the decoding, and a document starting with one is reported as
unreadable *content*. Nothing below re-checks an argument the model already
refuses, and a malformed argument never becomes a finding.
Document *content* is the opposite and is what this module exists to judge:
unparseable text, a wrong shape, a contract-model failure or a cross-file
inconsistency is a finding rather than a raised error.

An exception raised by this module's own assembly or scoping is a defect in
this package: it propagates. Turning one into a finding would fail an author's
document for a bug the author cannot fix. `_run_guarded` in
`analitiq.validator._core` is a separate mechanism: it contains a crash inside
a check bound to one rule, and inside whole-document
dispatch where `validate_document` applies it, as a `check-crashed`
`notApplicable` finding — so a crash inside the path-based route this module
delegates a single document to still comes back as a finding.

Each entry point taking a `ValidatePackageRequest` raises
`NotImplementedError`; the behaviour it must satisfy is fixed by
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
    through the path-based single-document route report through one shape.
    `passed` is `False` exactly when
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


def _consistent_entities(document: object) -> frozenset[str]:
    """The published document-schema names `document`'s own content is
    consistent with — empty when no registered kind claims it, or when the kind
    that does has no published name (an assembled pipeline bundle, say).

    The core registry claims a type map by shape and leaves its direction to
    the document, while the name vocabulary separates the directions. A type
    map is consistent with the name for the direction it declares, and with
    either name when it declares none, so validation reports the missing
    `direction` rather than a mismatch hiding it.

    Walks the live `_KIND_REGISTRY` in registration order, so which kind claims
    a document is always the registry's own answer. A `register_kind` call
    names its validator, while `register_model_and_schema_kind` builds an
    anonymous validator closure and names only its detector, so each
    registration is looked up by whichever half is a stable importable name.
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
            declared = _type_map_direction(document)
            return frozenset(f"type-map-{direction}" for direction in ("read", "write")
                             if declared in (None, direction))
        entity = entity_by_validator.get(validator) or entity_by_detector.get(detector)
        return frozenset({entity}) if entity else frozenset()
    return frozenset()


def validate_single_document(
        request: ValidateSingleDocumentRequest) -> ValidationEnvelope:
    """Validate one document supplied as its file text.

    `request.entity` names the published document schema the caller says the
    text is written against. It is checked, not trusted: a document whose own
    content is inconsistent with the declared name is reported as an
    `entity-mismatch` finding rather than validated as whatever it resembles.
    Text the JSON parser cannot read is a finding on the document, not a
    raised error.

    A consistent document gets the verdict `validate_document` gives it with no
    path to anchor on: a connector's cross-file coverage check has no siblings
    to read, so it reports `coverage-check-skipped-no-path`, which costs the
    pass. A connector's siblings are validated by `validate_connector_package`.
    """
    from analitiq.validator._core import (
        _JSON_TEXT_REFUSALS, _unreadable_document_finding, finding, validate_document,
    )

    try:
        document = json.loads(request.document)
    except _JSON_TEXT_REFUSALS as exc:
        return _envelope([_unreadable_document_finding(exc)])

    consistent = _consistent_entities(document)
    if request.entity not in consistent:
        if consistent:
            detected = " or ".join(repr(entity) for entity in sorted(consistent))
            message = (f"declared entity {request.entity!r}, but this document's "
                       f"content is detected as {detected}.")
        else:
            message = (f"declared entity {request.entity!r}, but this document's "
                       "content matches no published document schema.")
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
