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
"""
from __future__ import annotations

import json
import posixpath
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal, TypedDict
from urllib.parse import quote, unquote

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def _claimed_entities(document: object) -> frozenset[str] | None:
    """The published document-schema names of the kind that claims `document`
    — `None` where no registered kind claims it at all, and an *empty set*
    where one does and that kind has no published name (an assembled pipeline
    bundle, say).

    The two empty-looking answers must stay apart, because a declared kind
    treats them oppositely: a claimed document is refused, an unclaimed one is
    graded. A bundle is claimed, so collapsing the two would let one pass as
    the `pipeline` its core carries.

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
    return None


def validate_single_document(
        request: ValidateSingleDocumentRequest) -> ValidationEnvelope:
    """Validate one document supplied as its file text.

    `request.entity` names the published document schema the caller says the
    text is written against. It is checked, not trusted: a document some
    detector claims for another kind is reported as an `entity-mismatch`
    finding rather than validated as whatever it resembles. A document no
    detector claims is graded rather than refused, which answers with the
    discriminating field of every published kind instead of with the
    declaration alone. A document claimed by the declared kind is graded
    exactly as `validate_document` grades it, since the name it declares is
    the registration that claims it. Text the JSON parser cannot read is a
    finding on the document, not a raised error.

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
    """The finding refusing `document` as `entity`, and none where the
    declaration stands or where nothing claimed the document.

    A declared kind — a caller's `entity`, or the key a package member is filed
    at — exists so a broken document is answered with the fields that make it
    broken instead of one generic refusal. That is what decides how far it may
    reach. A document some detector claims is refused unless the kind claimed
    is the declared one: the claim is positive evidence the document is
    something else, and it covers a kind with no published name too, since an
    assembled bundle is claimed and would otherwise grade as the `pipeline` its
    core carries. A document no detector claims carries no such evidence, and
    refusing it is the collapse the declaration exists to avoid — grading it
    answers with the fields it lacks, or with the discriminating field of every
    published kind, where refusing names neither. So it is graded, by whatever
    grader the site holds.
    """
    from analitiq.validator._core import finding

    claimed = _claimed_entities(document)
    if claimed is None or entity in claimed:
        return []
    if claimed:
        detected = " or ".join(repr(name) for name in sorted(claimed))
        message = (f"declared entity {entity!r}, but this document's "
                   f"content is detected as {detected}.")
    else:
        message = (f"declared entity {entity!r}, but this document's content is "
                   "detected as a kind no published document schema names.")
    return [finding(message_id="entity-mismatch", kind="fail", path="", message=message)]


def validate_connector_package(request: ValidatePackageRequest) -> ValidationEnvelope:
    """Validate a connector package supplied as in-memory documents instead of
    files on disk, keyed from the package's own directory: the connector
    document where `analitiq.contracts.connector_package.DOCUMENT_LOCATIONS`
    places it, its sibling type map, and — for an api connector — its endpoint
    files.

    The connector is graded exactly as it is from a path on disk, by the same
    checks reading the same siblings, so the two routes cannot disagree about
    a package whose connector document holds a connector. One holding a
    document some detector claims for another kind is refused as an
    `entity-mismatch`, where the disk route grades whatever it detects; one
    holding content no detector claims is graded, as it is there. A document
    the connector's checks never read is not graded.

    What differs is where a finding says it applies, which `ValidationEnvelope`
    states for every package route: a directory named like a type map is
    reported at the directory, since that is the entry the author has to
    rename. That the package carries no connector document at all is the
    empty-path kind — nothing was submitted for it to name.
    """
    return _envelope(_graded_connector_package(request.documents.root)[0])


def _graded_connector_package(
        texts: Mapping[str, str]) -> tuple[list[Finding], object | None]:
    """The findings for the connector package `texts` holds, and the connector
    document they were produced from — `None` where that document is absent,
    did not parse, or was refused as an `entity-mismatch`.

    Returning the document is what lets a caller that needs something off it —
    the identity a pipeline package resolves connector references by — read
    that from the grade rather than from a parse of its own, which would let a
    document this route refused supply it anyway.
    """
    from analitiq.validator._core import (
        _JSON_TEXT_REFUSALS,
        _unreadable_document_finding,
        finding,
        qualified,
        validate_document,
    )
    from analitiq.validator._location import Location, MemoryTree

    key = _connector_key(texts)
    if key is None:
        return ([finding(
            message_id="connector-document-missing", kind="fail", path="",
            message="a connector package carries one connector document; no submitted key is one.")],
            None)
    anchor = Location(PurePosixPath(key), MemoryTree(texts))
    try:
        document = json.loads(anchor.read_text())
    except _JSON_TEXT_REFUSALS as exc:
        return ([qualified(_unreadable_document_finding(exc), quote(key))], None)
    mismatch = _entity_mismatch_findings(document, "connector")
    if mismatch:
        return (_from_package_root(mismatch, key), None)
    return (_from_package_root(validate_document(document, doc_path=anchor), key), document)


def _connector_key(texts: Mapping[str, str]) -> str | None:
    """The key in the connector package `texts` holds at which
    `connector_package.DOCUMENT_LOCATIONS` places the connector document, or
    `None` where the package carries none."""
    from analitiq.validator.workspace import document_kind

    keys = [key for key in texts if document_kind("connector-package", key) == "connector"]
    if len(keys) > 1:
        raise ValueError(f"the published connector location matches more than one key: {keys}")
    return keys[0] if keys else None


def _read_from(findings: list[Finding], directory: str) -> list[Finding]:
    """`findings` whose references are read from `directory`, each re-read from
    the package root. The empty path — a finding about the package that
    `directory` is — becomes that directory."""
    rooted = []
    for f in findings:
        reference, _, pointer = f["path"].partition("#")
        joined = posixpath.normpath(posixpath.join(directory, unquote(reference)))
        rooted.append({**f, "path": f"{quote(joined)}#{pointer}"})
    return rooted


def _from_package_root(findings: list[Finding], key: str) -> list[Finding]:
    """`findings` from grading the document at `key`, each read from the
    package root instead: a pointer into that document gains its key, and a
    finding about a sibling is re-read from the directory the two share."""
    from analitiq.validator._core import is_bare_pointer, qualified

    return [qualified(f, quote(key)) if is_bare_pointer(f["path"])
            else _read_from([f], posixpath.dirname(key))[0]
            for f in findings]


def _directory_mismatch_findings(
        declared: str, field: str, key: str, package_root: str) -> list[Finding]:
    """The finding refusing the document at `key` when the identity `declared`
    in its `field` is not the name of the package directory `package_root`,
    and none when it is."""
    from analitiq.validator._core import finding

    directory = PurePosixPath(package_root).name
    if declared == directory:
        return []
    return [finding(
        rule="RULE-PKG-036", message_id="package-directory-mismatch", kind="fail",
        path=f"{quote(key)}#/{field}",
        message=(f"this document declares {field} {declared!r}, but its package sits in "
                 f"a directory named {directory!r}; a workspace reads a package's "
                 f"identity off its directory name, so the two must be one value."))]


def _about_bundle(findings: list[Finding], *, pipeline_key: str,
                  member_keys: dict[str, list[str]]) -> list[Finding]:
    """`findings` from the assembled bundle, each read from the workspace root.

    A bundle pointer addresses a member by the position assembly gave it
    (`/connections/1/connector_id`), which names nothing the caller sent, so
    each one is translated back to the key that member was read from. A pointer
    at a whole member collection — the set of streams an active pipeline
    lacks a runnable one in — is about the pipeline document that declares it.
    """
    rooted = []
    for f in findings:
        head, _, rest = f["path"].lstrip("/").partition("/")
        index, _, within = rest.partition("/")
        if head == "pipeline":
            key, pointer = pipeline_key, f"/{rest}" if rest else ""
        elif head in member_keys and not rest:
            key, pointer = pipeline_key, f["path"]
        elif head in member_keys and index.isdigit():
            key, pointer = member_keys[head][int(index)], f"/{within}" if within else ""
        else:
            raise ValueError(f"bundle finding at {f['path']!r} names no assembled member")
        rooted.append({**f, "path": f"{quote(key)}#{pointer}"})
    return rooted


def _by_document(findings: list[Finding]) -> list[Finding]:
    """`findings` ordered by the document each is about, then by where in it.

    A pipeline package merges several traversals and one bundle pass, so the
    order is imposed here rather than left to emerge from which traversal ran
    first. Stable, so two findings at one pointer keep the order the checks
    produced them in.
    """
    return sorted(findings, key=lambda f: (unquote(f["path"].partition("#")[0]),
                                           f["path"].partition("#")[2]))


def validate_pipeline_package(request: ValidatePackageRequest) -> ValidationEnvelope:
    """Validate a workspace holding one pipeline package, supplied as in-memory
    documents keyed from the workspace root: the pipeline document and its
    streams, each connection package with its endpoints, type map and
    credentials, each embedded connector package, and the pipeline manifest.

    Every key is read through `analitiq.validator.workspace.locate`, which
    reads the published location tables, so the key alone declares what kind
    of document it holds and which package carries it. A key no published
    location reaches is not graded.

    What the declaration does with a document is `_entity_mismatch_findings`:
    a document some detector claims for another kind is refused as an
    `entity-mismatch`, never graded as whatever it resembles, and a document
    no detector claims is graded at the declared kind rather than refused. An
    embedded connector package is graded by the connector package route, so it
    reports its own coverage findings, read from the workspace root. A
    connection or connector package whose document declares an identity other
    than its directory name is refused under `RULE-PKG-036`.

    The graded documents are then assembled into the bundle
    `analitiq.validator.pipelines.validate_pipeline_bundle` checks for
    referential integrity, with the pipeline's own `status` deciding whether it
    is held to runnability. A member is assembled only when it graded and the
    document giving it a place resolved, and each reason one is not leaves a
    `fail` at the document responsible for it:

    - the member's own text did not parse, a detector claimed it for another
      kind, or grading it identified no kind at all — reported at the member,
      as `unreadable-document`, `entity-mismatch` or `unrecognized-document`;
    - the workspace carries no document it is authored under — reported at the
      member, as `owning-document-missing`;
    - that owning document is carried and did not grade — reported at the
      owner, by whichever of those its own content earned;
    - that owning document graded and declares no identity, or one its
      directory does not carry — reported at the owner, as
      `owning-document-unidentified` or `package-directory-mismatch`.

    While anything is missing, the referential pass is skipped rather than run
    half-blind: a reference into a document that was withheld is
    indistinguishable from one that resolves to nothing.

    What this route reports with the empty path is that the workspace does not
    settle on one pipeline document for the rest of it to be read under.
    """
    from pydantic import TypeAdapter

    from analitiq.validator._core import (
        _JSON_TEXT_REFUSALS,
        _model_findings,
        _unreadable_document_finding,
        contract_model_domain,
        finding,
        validate_document,
    )
    from analitiq.validator._location import Location, MemoryTree
    from analitiq.validator.connectors import load_type_map, type_map_findings
    from analitiq.validator.pipelines import is_runnable_required, validate_pipeline_bundle
    from analitiq.validator.workspace import locate

    with contract_model_domain():
        from analitiq.contracts.credentials_file import CredentialsFile
        from analitiq.contracts.pipeline_manifest import PipelineManifest

    texts = request.documents.root
    tree = MemoryTree(texts)

    # Each located key, filed by the package directory holding it and the kind
    # its location declares. Every group is taken off this map as it is read,
    # so a location the route never reads raises instead of grading nothing.
    authored: dict[tuple[str | None, str], dict[str, list[str]]] = {}
    for key in sorted(texts):
        where = locate(key)
        if where is not None:
            roots = authored.setdefault((where.package, where.kind), {})
            roots.setdefault(where.package_root, []).append(key)

    def routed(package: str | None, kind: str) -> dict[str, list[str]]:
        return authored.pop((package, kind), {})

    pipelines_authored = routed("pipeline-package", "pipeline")
    streams_authored = routed("pipeline-package", "stream")
    manifests_authored = routed(None, "pipeline-manifest")
    connections_authored = routed("connection-package", "connection")
    endpoints_authored = routed("connection-package", "database-endpoint")
    type_maps_authored = routed("connection-package", "type-map")
    credentials_authored = routed("connection-package", "credentials")
    connector_roots = sorted({root for (package, kind) in list(authored)
                              if package == "connector-package"
                              for root in authored.pop((package, kind))})
    if authored:
        raise ValueError(
            f"published locations {sorted(authored, key=str)} are read by no grader here; "
            f"every document filed there would be graded by nothing.")

    if not pipelines_authored:
        return _envelope([finding(
            message_id="pipeline-document-missing", kind="fail", path="",
            message="a pipeline package carries one pipeline document; no submitted key is one.")])
    pipeline_keys = [key for keys in pipelines_authored.values() for key in keys]
    if len(pipeline_keys) > 1:
        return _envelope([finding(
            message_id="pipeline-document-ambiguous", kind="fail", path="",
            message=(f"a pipeline package carries one pipeline document; these keys "
                     f"each hold one: {', '.join(repr(key) for key in pipeline_keys)}."))])
    [(pipeline_root, [pipeline_key])] = pipelines_authored.items()

    findings: list[Finding] = []
    withheld: list[str] = []

    def parsed(key: str, entity: str) -> tuple[bool, object]:
        """The document at `key`, and whether it may be graded as `entity`: its
        text parsed and no detector claimed it for another kind. A refusal's
        finding is appended."""
        try:
            document = json.loads(texts[key])
        except _JSON_TEXT_REFUSALS as exc:
            findings.extend(_from_package_root([_unreadable_document_finding(exc)], key))
            return False, None
        mismatch = _entity_mismatch_findings(document, entity)
        findings.extend(_from_package_root(mismatch, key))
        return not mismatch, document

    def graded(key: str, entity: str) -> object | None:
        """The document at `key`, graded as `entity`, with its findings
        appended; `None` where nothing left a document the bundle can place —
        a refusal, or a grade that identified no kind at all."""
        ok, document = parsed(key, entity)
        if not ok:
            return None
        graded_findings = validate_document(document, doc_path=Location(PurePosixPath(key), tree))
        findings.extend(_from_package_root(graded_findings, key))
        unidentified = any(f["message_id"] == "unrecognized-document" for f in graded_findings)
        return None if unidentified else document

    def graded_by_model(key: str, entity: str, adapter: TypeAdapter) -> None:
        ok, document = parsed(key, entity)
        if ok:
            findings.extend(_from_package_root(_model_findings(document, adapter), key))

    def report_unowned(key: str, owner: str) -> None:
        """A document a published location reaches whose owning document the
        workspace does not carry. It is graded on its own terms; what it cannot
        have is a place."""
        findings.extend(_from_package_root([finding(
            message_id="owning-document-missing", kind="fail", path="",
            message=(f"this document is authored under a {owner} the workspace does not "
                     f"carry: its package directory holds no {owner} document."))], key))

    pipeline_doc = graded(pipeline_key, "pipeline")
    if pipeline_doc is None:
        withheld.append(pipeline_key)

    streams, stream_keys = [], []
    for root, keys in streams_authored.items():
        for key in keys:
            if root != pipeline_root:
                report_unowned(key, "pipeline")
            stream = graded(key, "stream")
            if stream is not None and root == pipeline_root and pipeline_doc is not None:
                streams.append(stream)
                stream_keys.append(key)
            else:
                withheld.append(key)

    manifest_adapter = TypeAdapter(PipelineManifest)
    for keys in manifests_authored.values():
        for key in keys:
            graded_by_model(key, "pipeline-manifest", manifest_adapter)

    credentials_adapter = TypeAdapter(CredentialsFile)
    connections, connection_keys = [], []
    endpoints, endpoint_keys = [], []
    connection_roots = sorted({*connections_authored, *endpoints_authored,
                               *type_maps_authored, *credentials_authored})
    for root in connection_roots:
        [connection_key] = connections_authored.get(root, [None])
        members = [key for group in (endpoints_authored, type_maps_authored, credentials_authored)
                   for key in group.get(root, [])]
        connection_id = None
        if connection_key is None:
            for key in members:
                report_unowned(key, "connection")
        else:
            connection = graded(connection_key, "connection")
            declared = connection.get("connection_id") if isinstance(connection, dict) else None
            if connection is not None and not (isinstance(declared, str) and declared):
                findings.extend(_from_package_root([finding(
                    message_id="owning-document-unidentified", kind="fail", path="",
                    message=("this document declares no `connection_id`, so nothing can "
                             "reference it or what is authored under it."))], connection_key))
            elif connection is not None:
                mismatch = _directory_mismatch_findings(
                    declared, "connection_id", connection_key, root)
                findings.extend(mismatch)
                if not mismatch:
                    connection_id = declared
            if connection_id is None:
                withheld.append(connection_key)
            else:
                connections.append(connection)
                connection_keys.append(connection_key)

        for key in endpoints_authored.get(root, []):
            endpoint = graded(key, "database-endpoint")
            if endpoint is None or connection_id is None:
                withheld.append(key)
                continue
            # After grading, never before: the endpoint models forbid these
            # keys, so one supplied first comes back as the author's error.
            # Assigned rather than defaulted, because where the document
            # carries one the model has just refused it, and letting the
            # refused value stand would attach the endpoint somewhere the
            # workspace did not put it.
            endpoint["connection_id"] = connection_id
            endpoint["scope"] = "connection"
            endpoints.append(endpoint)
            endpoint_keys.append(key)

        # Read through the published loader, which also refuses every other
        # type-map name beside it — a directory property no key-by-key walk can
        # see. A connection's map is gap-only, so it is graded at connection
        # scope rather than held to a connector's write vocabulary.
        for key in type_maps_authored.get(root, []):
            directory = posixpath.dirname(key)
            load = load_type_map(Location(PurePosixPath(directory), tree), rule=None)
            for entry, f in load.findings:
                findings.extend(_from_package_root(
                    [f], posixpath.normpath(posixpath.join(directory, entry))))
            if load.loaded:
                mismatch = _entity_mismatch_findings(load.document, "type-map")
                findings.extend(_from_package_root(
                    mismatch or type_map_findings(load.document, scope="connection"), key))

        for key in credentials_authored.get(root, []):
            graded_by_model(key, "credentials", credentials_adapter)

    connector_ids: set[str] = set()
    for root in connector_roots:
        subtree = {key[len(root):]: text for key, text in texts.items() if key.startswith(root)}
        inner, connector = _graded_connector_package(subtree)
        findings.extend(_read_from(inner, root.rstrip("/")))
        connector_key = _connector_key(subtree)
        declared = connector.get("connector_id") if isinstance(connector, dict) else None
        if isinstance(declared, str) and declared:
            mismatch = _directory_mismatch_findings(
                declared, "connector_id", root + connector_key, root)
            findings.extend(mismatch)
            if not mismatch:
                connector_ids.add(declared)
                continue
        # A connector declaring no identity is already failed where it sits, by
        # its own model or by the connector route; what is lost is the
        # reference it would have resolved.
        withheld.append(root + connector_key if connector_key else root.rstrip("/"))

    if withheld:
        findings.extend(_from_package_root([finding(
            message_id="referential-check-skipped", kind="notApplicable", path="",
            message=(f"referential integrity was not checked: the workspace carries "
                     f"{', '.join(repr(key) for key in sorted(withheld))}, which this run "
                     f"could not place in the bundle, so a reference into one cannot be "
                     f"told apart from a reference that resolves to nothing."))], pipeline_key))
    else:
        bundle = {"pipeline": pipeline_doc, "streams": streams, "connections": connections,
                  "connectors": sorted(connector_ids), "endpoints": endpoints}
        findings.extend(_about_bundle(
            validate_pipeline_bundle(bundle, require_runnable=is_runnable_required(pipeline_doc)),
            pipeline_key=pipeline_key,
            member_keys={"streams": stream_keys, "connections": connection_keys,
                         "endpoints": endpoint_keys}))
    return _envelope(_by_document(findings))
