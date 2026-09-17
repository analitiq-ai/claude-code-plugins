"""The path-free document-set API — types and signatures, not yet implemented.

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
parameter that selects between them. One package request model serves both
package entry points precisely because the function carries the kind.

**The request model is the argument gate.** A key outside the document-key
grammar, a non-string value, a key that is also a directory of another, a
package past the document ceiling, an `entity` outside the published document
schema names — each is a `pydantic.ValidationError` raised at construction, for
an in-process caller and a remote one alike, so there is one gate rather than
one per transport. Nothing below re-checks an argument the model already
refuses, and a malformed argument never becomes a finding. Document *content*
is the opposite and is what this module exists to judge: unparseable text, a
wrong shape, a contract-model failure or a cross-file inconsistency is a
finding on that document's key.

An exception raised while validating is a defect in this package: it
propagates. Turning one into a finding would fail an author's document for a
bug the author cannot fix. The per-check `check-crashed` containment of the
path-based route is a different mechanism and is untouched.

Every function below raises `NotImplementedError` — the behaviour they must
satisfy is fixed by `packages/validator/tests/test_document_set.py`, whose
fixture cases are `xfail` until an implementation replaces these bodies and
removes the markers one case at a time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    # Annotation-only. `from __future__ import annotations` defers every
    # annotation here to a string, and no body below needs a model at runtime,
    # so nothing imports the contract package when this module loads. That is
    # what keeps the import order safe: this module is imported before the
    # per-kind modules whose `try/except ImportError` turns a missing
    # `analitiq-contract-models` into the structured "missing dependency"
    # diagnostic, and an unconditional import here would pre-empt that guard
    # with a raw traceback instead.
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
    breakdown — each finding's own `path` carries its document key, so a
    document validated this way and the same document validated through the
    path-based single-document route report through one shape. `passed` is the
    `finding_costs_a_pass` reduction over `findings`, which is not the same as
    "no finding at `severity: error`": an unchecked error-tier rule costs a
    pass too."""

    passed: bool
    findings: list[Finding]


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

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_single_document is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")


def validate_connector_package(request: ValidatePackageRequest) -> ValidationEnvelope:
    """Validate a connector package supplied as in-memory documents instead of
    files on disk: the connector document, its sibling type maps, and its
    `endpoints/*.json` files, cross-checked the way
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
