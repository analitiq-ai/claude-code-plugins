"""The in-memory request API.

Each entry point takes the request model naming the unit submitted
(`analitiq.contracts.validation_requests`) — one document, one package, or a
workspace of packages — and answers in one `ValidationEnvelope`. Nothing here
reads a file: a caller holding files reads them and builds the request. A caller
states what it is sending by choosing the function, so nothing reads content to
work out what it was handed.

**Kinds come from the request, never from content.** A single document is graded
as the kind the caller names. A package or workspace key is graded as the kind
its location table (`kind_at`) gives it, and a key no location matches is not
graded. A package's root document is required.

**Cross-document checks are routed by what they read.** Each entry of `_CHECKS`
declares the document kinds it reads. A check reading only kinds one package
holds is a package check, run on each package alone; any other is a workspace
check; a check that gates running a pipeline runs only for the pipeline a
workspace request names to run. A check runs only over documents that all parsed,
and in a unit whose every package has its root: it cannot tell a missing document
from one it could not read.

**The request model is the argument gate.** A key outside the document-key
grammar, a value that is not text, a key at a secret location, an unknown kind —
each is a `pydantic.ValidationError` raised at construction, for an in-process
caller and a remote one alike. `bytes` holding UTF-8 pass that gate, decoded to
`str` in lax mode, except that a byte-order mark survives the decoding and a
document starting with one is reported as unreadable *content*. Document content
is what this module judges: unparseable text, a wrong shape, a contract-model
failure or a cross-document inconsistency is a finding, never a raised error.

An exception raised by this module's own assembly is a defect in this package
and propagates. A crash inside grading one document, or inside one check, is
contained by `_run_guarded` in `analitiq.validator._core` as a `check-crashed`
finding, so the other checks still report.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable, Literal, Mapping, NamedTuple, TypedDict

from analitiq.contracts.validation_requests import PACKAGE_KINDS
from analitiq.contracts.workspace import PACKAGE_MODELS, Workspace

from ._core import (
    _JSON_TEXT_REFUSALS,
    _Doc,
    _passed,
    _run_guarded,
    _unreadable_document_finding,
    finding,
    qualified,
    validate_document_as,
)
from ._location import relative_reference
from .connectors import (
    _check_connector_endpoints,
    _check_endpoint_filenames,
    _check_endpoint_ids_unique,
    _check_type_map_relation,
)
from .pipelines import (
    _check_connection_connector_refs,
    _check_connection_scoped_endpoints,
    _check_connection_version_conflicts,
    _check_connections_present,
    _check_connector_scoped_endpoints,
    _check_pipeline_active,
    _check_pipeline_active_gate,
    _check_pipeline_id,
    _check_stream_connection_roles,
    _check_stream_endpoint_targets,
    _check_stream_parent_pipeline,
    _check_stream_refs,
)

if TYPE_CHECKING:
    # Annotation-only: no body below needs a request model at run time.
    from analitiq.contracts.shared.common import DocumentPackage
    from analitiq.contracts.validation_requests import (
        ValidatePackageRequest,
        ValidateSingleDocumentRequest,
        ValidateWorkspaceRequest,
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
    return {"passed": _passed(findings), "findings": findings}


_Documents = Mapping[str, tuple[_Doc, ...]]


class _Check(NamedTuple):
    """A cross-document check and the document kinds it reads."""

    run: Callable[[_Documents], list[tuple[str, dict]]]
    reads: frozenset[str]
    #: Whether it decides only that a pipeline may run, not that it is sound.
    gates_run: bool = False


def _reads(*kinds: str) -> frozenset[str]:
    return frozenset(kinds)


_CHECKS: tuple[_Check, ...] = (
    _Check(_check_type_map_relation, _reads("connector", "type-map")),
    _Check(_check_connector_endpoints, _reads("connector", "type-map", "api-endpoint")),
    _Check(_check_endpoint_ids_unique, _reads("api-endpoint")),
    _Check(_check_endpoint_ids_unique, _reads("database-endpoint")),
    _Check(_check_endpoint_filenames, _reads("api-endpoint")),
    _Check(_check_endpoint_filenames, _reads("database-endpoint")),
    _Check(_check_pipeline_id, _reads("pipeline")),
    _Check(_check_connection_version_conflicts, _reads("pipeline")),
    _Check(_check_stream_endpoint_targets, _reads("stream")),
    _Check(_check_stream_refs, _reads("pipeline", "stream")),
    _Check(_check_stream_parent_pipeline, _reads("pipeline", "stream")),
    _Check(_check_stream_connection_roles, _reads("pipeline", "stream")),
    _Check(_check_pipeline_active, _reads("pipeline"), gates_run=True),
    _Check(_check_pipeline_active_gate, _reads("pipeline", "stream"), gates_run=True),
    _Check(_check_connections_present, _reads("pipeline", "connection")),
    _Check(_check_connection_connector_refs, _reads("connection", "connector")),
    _Check(_check_connection_scoped_endpoints, _reads("stream", "database-endpoint")),
    _Check(_check_connector_scoped_endpoints, _reads("stream", "connection", "api-endpoint")),
)


def _package_kinds(model: type[DocumentPackage]) -> frozenset[str]:
    """The kinds a request can carry in a package of `model`: a secret
    location's document is never carried."""
    secret = {model.LOCATIONS[pattern] for pattern in model.SECRET_LOCATIONS}
    return frozenset(model.LOCATIONS.values()) - secret


def _package_checks(model: type[DocumentPackage]) -> list[_Check]:
    return [c for c in _CHECKS if not c.gates_run and c.reads <= _package_kinds(model)]


def _workspace_checks() -> list[_Check]:
    return [c for c in _CHECKS if not c.gates_run
            and not any(c.reads <= _package_kinds(m) for m in PACKAGE_MODELS.values())]


def _run_checks() -> list[_Check]:
    return [c for c in _CHECKS if c.gates_run]


def _documents_for(check: _Check, documents: _Documents) -> _Documents:
    return {kind: documents.get(kind, ()) for kind in check.reads}


def _check_findings(checks: list[_Check], documents: _Documents) -> list[Finding]:
    """Every finding `checks` report over `documents`, each naming its document
    by key. A crash costs only the check that crashed."""
    def _run(check: _Check) -> Callable[[], list[dict]]:
        return lambda: [qualified(f, relative_reference(key)) for key, f in check.run(_documents_for(check, documents))]
    return [f for check in checks for f in _run_guarded(_run(check), crash_label=check.run.__name__)]


def _parsed(text: str) -> tuple[object, list[Finding]]:
    """`text` parsed, or the finding saying it could not be."""
    try:
        return json.loads(text), []
    except _JSON_TEXT_REFUSALS as exc:
        return None, [_unreadable_document_finding(exc)]


def _graded(kind: str, key: str, package: str, text: str) -> tuple[_Doc | None, list[Finding]]:
    """The document at `key` parsed and graded as `kind`, its findings named by
    key; no document when its text could not be parsed."""
    content, unreadable = _parsed(text)
    findings = unreadable or validate_document_as(kind, content)
    named = [qualified(f, relative_reference(key)) for f in findings]
    return (None if unreadable else _Doc(key, package, content)), named


class _GradedPackage(NamedTuple):
    findings: list[Finding]
    documents: dict[str, list[_Doc]]
    #: Whether its root is present and every located document parsed.
    complete: bool


def _graded_package(model: type[DocumentPackage], directory: str,
                    texts: Mapping[str, str]) -> _GradedPackage:
    """Each located document of one package, keyed within it, graded as the kind
    its location gives it; `directory` is where the package sits in the request."""
    findings: list[Finding] = []
    documents: dict[str, list[_Doc]] = {}
    complete = model.ROOT in texts
    if not complete:
        findings.append(qualified(finding(
            message_id="package-root-missing", kind="fail", path="",
            message=f"a {model.ROOT_KIND} package carries its root document at {model.ROOT!r}; none is there."),
            relative_reference(directory + model.ROOT)))
    for inner in sorted(texts):
        kind = model.kind_at(inner)
        if kind is None:
            continue
        doc, graded = _graded(kind, directory + inner, directory, texts[inner])
        findings += graded
        if doc is None:
            complete = False
        else:
            documents.setdefault(kind, []).append(doc)
    return _GradedPackage(findings, documents, complete)


def _frozen(documents: Mapping[str, list[_Doc]]) -> dict[str, tuple[_Doc, ...]]:
    return {kind: tuple(docs) for kind, docs in documents.items()}


def validate_single_document(
        request: ValidateSingleDocumentRequest) -> ValidationEnvelope:
    """Validate one document, graded as the kind `request.document_kind`
    names. Checks needing another document belong to a package or workspace
    request, so none runs here."""
    content, unreadable = _parsed(request.document)
    return _envelope(unreadable or validate_document_as(request.document_kind, content))


def validate_package(request: ValidatePackageRequest) -> ValidationEnvelope:
    """Validate one package: each located document, then — where the root is
    present and every located document parsed — the package checks."""
    model = PACKAGE_KINDS[request.package_kind]
    graded = _graded_package(model, "", request.documents.root)
    findings = list(graded.findings)
    if graded.complete:
        findings += _check_findings(_package_checks(model), _frozen(graded.documents))
    return _envelope(findings)


def validate_workspace(request: ValidateWorkspaceRequest) -> ValidationEnvelope:
    """Validate a workspace: each package as `validate_package` does, each
    document the workspace holds directly, then — where every package has its
    root and every located document parsed — the workspace checks, and the run
    checks over the pipeline `request.run_pipeline` names."""
    packages: dict[str, tuple[type[DocumentPackage], dict[str, str]]] = {}
    direct: list[str] = []
    for key, text in request.documents.root.items():
        located = Workspace.package_at(key)
        if located is None:
            direct.append(key)
            continue
        directory, model, inner = located
        packages.setdefault(directory, (model, {}))[1][inner] = text

    findings: list[Finding] = []
    documents: dict[str, list[_Doc]] = {}
    complete = True
    for directory in sorted(packages):
        model, texts = packages[directory]
        graded = _graded_package(model, directory, texts)
        findings += graded.findings
        if graded.complete:
            findings += _check_findings(_package_checks(model), _frozen(graded.documents))
        complete = complete and graded.complete
        for kind, docs in graded.documents.items():
            documents.setdefault(kind, []).extend(docs)
    for key in sorted(direct):
        kind = Workspace.kind_at(key)
        if kind is None:
            continue
        doc, graded = _graded(kind, key, "", request.documents.root[key])
        findings += graded
        complete = complete and doc is not None

    if complete:
        findings += _check_findings(_workspace_checks(), _frozen(documents))
        if request.run_pipeline is not None:
            running = {kind: tuple(d for d in docs if d.package == request.run_pipeline)
                       for kind, docs in documents.items()}
            findings += _check_findings(_run_checks(), running)
    return _envelope(findings)
