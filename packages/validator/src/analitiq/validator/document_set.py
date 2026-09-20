"""The path-free document-set API.

`analitiq.validator._core.validate_document` reads the files beside a
document from the path it is given: `analitiq.validator.connectors
.check_coverage` walks a connector's sibling type map and endpoints from the
directory holding it, and the pipeline-builder plugin's own `_assemble_bundle`
(`plugins/analitiq-pipeline-builder/scripts/validate.py`) globs an entire
pipeline directory. A consumer that never has those files on a local
filesystem — a hosted validator wrapping this package as a remote tool,
registry CI, any caller handed document content directly rather than a
directory to read it from — has no path to give either one.

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
a check bound to one rule, and inside the grading of a whole document, as a
`check-crashed` `notApplicable` finding — so a crash while grading a single
document still comes back as a finding.

`validate_pipeline_package` raises `NotImplementedError`; the behaviour it
must satisfy is fixed by `packages/validator/tests/test_document_set.py`.
"""
from __future__ import annotations

import json
from pathlib import PurePosixPath
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
    breakdown — each finding's `path` says which document it concerns and
    where in it (`rules/SCHEMA.md`, "Findings"). A single document's finding
    names another document only when it is about one; a package's names every
    document by its key, since a package has no one validated document. The
    key is percent-encoded, so a consumer decodes it before matching it
    against a request key.
    `passed` is `False` exactly when `findings` holds one that
    `finding_costs_a_pass` accepts, which is not the
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
    )

    entity_by_validator = {
        _validate_connector: "connector",
        _validate_api_endpoint: "api-endpoint",
        _validate_database_endpoint: "database-endpoint",
        _validate_type_map: "type-map",
        _validate_kindless_connector: "connector",
    }
    entity_by_detector = {
        is_connection_doc: "connection",
        is_stream_doc: "stream",
        is_pipeline_doc: "pipeline",
    }

    for detector, validator in _core._KIND_REGISTRY:  # skipcq: PYL-W0212 — same-package read of the live kind registry
        if not detector(document):
            continue
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
    A consistent document is graded exactly as `validate_document` grades it,
    since the name it declares is the registration that claims it. Text
    the JSON parser cannot read is a finding on the document, not a raised
    error.

    Nothing anchors the document to a path, so a connector declaring its
    `kind` has no siblings for its cross-file coverage check to read: that
    check reports `coverage-check-skipped-no-path`, which costs the pass. The
    siblings belong in a `validate_connector_package` request.
    """
    from analitiq.validator._core import (
        _JSON_TEXT_REFUSALS, _unreadable_document_finding, validate_document)

    try:
        document = json.loads(request.document)
    except _JSON_TEXT_REFUSALS as exc:
        return _envelope([_unreadable_document_finding(exc)])

    mismatch = _entity_mismatch_findings(document, request.entity)
    return _envelope(mismatch or validate_document(document))


def _entity_mismatch_findings(document: object, entity: str) -> list[Finding]:
    """The finding refusing `document` as `entity` when its own content is
    inconsistent with that name, and none when it is consistent."""
    from analitiq.validator._core import finding

    consistent = _consistent_entities(document)
    if entity in consistent:
        return []
    if consistent:
        detected = " or ".join(repr(name) for name in sorted(consistent))
        message = (f"declared entity {entity!r}, but this document's "
                   f"content is detected as {detected}.")
    else:
        message = (f"declared entity {entity!r}, but this document's "
                   "content matches no published document schema.")
    return [finding(message_id="entity-mismatch", kind="fail", path="", message=message)]


#: The key a connector package's root document sits at. The package root is
#: the directory holding it, and every other key is read relative to that.
_CONNECTOR_KEY = "connector.json"


def validate_connector_package(request: ValidatePackageRequest) -> ValidationEnvelope:
    """Validate a connector package supplied as in-memory documents instead of
    files on disk: the connector document at `connector.json`, its sibling
    type map, and — for an api connector — its `endpoints/*.json` files.

    The connector is graded exactly as it is from a path on disk, by the same
    checks reading the same siblings, so the two routes cannot disagree about
    a package whose `connector.json` holds a connector. One that holds anything
    else is refused as an `entity-mismatch`, where the disk route grades
    whatever it detects. What differs is where a finding says it applies: a package has
    no one validated document, so every finding names the document it is
    about by its percent-encoded key. A document the connector's checks never read is not graded.
    """
    from analitiq.validator._core import (
        _JSON_TEXT_REFUSALS,
        _unreadable_document_finding,
        finding,
        qualified,
        validate_document,
    )
    from analitiq.validator._location import Location, MemoryTree

    if _CONNECTOR_KEY not in request.documents.root:
        return _envelope([qualified(finding(
            message_id="connector-document-missing", kind="fail", path="",
            message=(f"a connector package carries its connector document at "
                     f"{_CONNECTOR_KEY!r}; no document has that key.")), _CONNECTOR_KEY)])
    tree = MemoryTree(request.documents.root)
    anchor = Location(PurePosixPath(_CONNECTOR_KEY), tree)
    try:
        document = json.loads(anchor.read_text())
    except _JSON_TEXT_REFUSALS as exc:
        return _envelope([qualified(_unreadable_document_finding(exc), _CONNECTOR_KEY)])
    mismatch = _entity_mismatch_findings(document, "connector")
    if mismatch:
        return _envelope(_from_package_root(mismatch))
    return _envelope(_from_package_root(validate_document(document, doc_path=anchor)))


def _from_package_root(findings: list[Finding]) -> list[Finding]:
    """`findings` from grading the connector document, each read from the
    package root: a pointer into the connector itself gains its key, and a
    finding about a sibling already names it from the directory both share."""
    from analitiq.validator._core import is_bare_pointer, qualified

    return [qualified(f, _CONNECTOR_KEY) if is_bare_pointer(f["path"]) else f
            for f in findings]


def validate_pipeline_package(request: ValidatePackageRequest) -> ValidationEnvelope:
    """Validate a pipeline package supplied as in-memory documents instead of
    files on disk: the pipeline document, its sibling `streams/*.json`, and
    every `connections/*/connection.json` (plus their scoped endpoints and type
    map) — assembled the way `plugins/analitiq-pipeline-builder/scripts/
    validate.py`'s `_assemble_bundle` already does from a filesystem root, then
    checked for referential integrity.

    A `connectors/<slug>/definition/...` subtree is validated by calling
    `validate_connector_package` on it and scoping the returned findings'
    `path` under the subtree's key prefix, so an embedded connector reports its
    own coverage findings — native-type coverage, `transport_ref` resolution,
    duplicate endpoint ids. The plugin's `_assemble_bundle` reports none of
    those: it reads such a subtree's `connector.json` only for its
    `connector_id`, and `_connector_endpoint_sets` reads the subtree's endpoint
    ids only for stream-ref resolution.

    Raises `NotImplementedError`. Signature and behaviour are fixed by
    `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_pipeline_package is not implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")
