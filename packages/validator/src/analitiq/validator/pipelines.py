"""Pipeline validation: the pipeline-side cross-document checks, the documents
graded alone, and the assembled pipeline-run bundle.

Each `_check_*` function takes documents by kind (`_Doc`s) and returns findings
keyed to the document they concern. `document_set` routes the same functions to
a package or a workspace by the kinds they read; `validate_pipeline_bundle`
runs them over a bundle it lays out as documents, so both surfaces apply one
implementation of each rule.

A check does NOT assume each document was already contract-validated: a missing
reference field (a connection naming no connector, a stream slot with no
endpoint_ref) is an unresolved reference, not a skip.

Referential integrity is separate from runnability. The run checks gate the
pipeline on `status='active'` with a runnable stream — what an executor needs;
the bundle applies them under `require_runnable`, and a workspace request to the
pipeline `run_pipeline` names.

Stream and connection refs are matched on their base form: a `{id}_v{n}` ref and
the bare `{id}` it pins resolve to the same document. Connector identities are
matched whole (their version is a separate field, not a ref suffix).

The `pipeline` document is validated against `PipelineInput` plus RULE-SHRD-003,
which reports a `warning` — a severity no `@model_validator` can carry
(`rules/SCHEMA.md`, `validator`). At import this module registers its kinds and
document validators with the `_core` dispatch registry.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Mapping

from ._core import (
    _Doc,
    _missing_schema_url_findings,
    _model_findings,
    contract_model_domain,
    finding,
    register_document_kind,
    register_document_validator,
    register_kind,
)
from .connectors import _check_endpoint_ids_unique

# Import the single-document contract model under the shared DOMAIN guard (the
# model binds the `$schema` host at import; see `contract_model_domain`).
with contract_model_domain():
    from pydantic import TypeAdapter
    from analitiq.contracts.pipelines.config import PipelineInput
    from analitiq.contracts.pipeline_manifest import PipelineManifest

_PIPELINE_ADAPTER = TypeAdapter(PipelineInput)
_PIPELINE_MANIFEST_ADAPTER = TypeAdapter(PipelineManifest)


# A trailing `_v{n}` version suffix selects a revision of an id; the base id is
# the object's identity. Referential matching is on the base, so `{id}_v2` and the
# bare `{id}` resolve to the same document — matching the published versioned-id
# contract (`{id}_v{version}`) and the consumers' own version-suffix stripping.
_VERSION_SUFFIX_RE = re.compile(r"_v\d+$")


def _base_id(ref: Any) -> Any:
    """Strip a trailing `_v{n}` version suffix; pass non-strings through."""
    if not isinstance(ref, str):
        return ref
    return _VERSION_SUFFIX_RE.sub("", ref)


def is_pipeline_bundle(doc: Any) -> bool:
    """A bundle is a mapping carrying a `pipeline` document plus its `streams` and
    `connections` collections — the assembled run inputs. Structurally distinct
    from every single-document kind (connector / endpoint / type-map / pipeline)."""
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("pipeline"), dict)
        and "streams" in doc
        and "connections" in doc
    )


def is_pipeline_doc(doc: Any) -> bool:
    """A single pipeline document declares its source/destination wiring under
    `connections` and, unlike a bundle, carries no nested `pipeline` document. The
    bundle detector (registered first) claims the assembled-run shape, so a
    `connections`-bearing mapping that is not a bundle is a pipeline document."""
    return isinstance(doc, dict) and "connections" in doc and "pipeline" not in doc


# ---------------------------------------------------------------------------
# Field extraction (defensive — documents are already per-document validated)
# ---------------------------------------------------------------------------

def _pipeline_connection_ids(pipeline: dict) -> set[str]:
    """The pipeline's connection references (source + destinations), base-form."""
    conns = pipeline.get("connections")
    if not isinstance(conns, dict):
        return set()
    refs = (conns.get("source"), *(conns.get("destinations") or ()))
    return {_base_id(r) for r in refs if isinstance(r, str) and r}


def _connector_ids(connectors: Any) -> set[str]:
    """The connector identities present in the bundle.

    Matched verbatim, not base-form: a connector is referenced by its whole
    identity (slug or id), with the version carried separately — unlike a
    stream/connection ref, `foo_v2` and `foo_v3` are not the same connector.
    Accepts the identity set however the assembler holds it: a mapping keyed by
    connector id, or an iterable of id strings / connector-meta dicts.
    """
    ids: set[str] = set()
    if isinstance(connectors, dict):
        return {k for k in connectors if isinstance(k, str)}
    if isinstance(connectors, (list, tuple, set)):
        for item in connectors:
            if isinstance(item, str) and item:
                ids.add(item)
            elif isinstance(item, dict):
                for key in ("connector_id", "slug", "id"):
                    value = item.get(key)
                    if isinstance(value, str) and value:
                        ids.add(value)
                        break
    return ids


def _content(doc: _Doc) -> dict:
    """The document's content where it is an object; an empty one where the
    document's own model reports it is not."""
    return doc.content if isinstance(doc.content, dict) else {}


def _stream_endpoint_refs(stream: dict) -> Iterator[tuple[str, dict]]:
    """Yield `(pointer, endpoint_ref)` for a stream's source and destinations."""
    source = stream.get("source")
    if isinstance(source, dict) and isinstance(source.get("endpoint_ref"), dict):
        yield "/source/endpoint_ref", source["endpoint_ref"]
    destinations = stream.get("destinations")
    if isinstance(destinations, list):
        for k, dest in enumerate(destinations):
            if isinstance(dest, dict) and isinstance(dest.get("endpoint_ref"), dict):
                yield f"/destinations/{k}/endpoint_ref", dest["endpoint_ref"]


# ---------------------------------------------------------------------------
# Referential checks. Each takes the parsed documents of the kinds it reads,
# keyed by kind, and returns `(key, finding)` pairs whose pointer is into the
# document at `key`. A connection, connector or endpoint is identified by the
# package holding it (`_Doc.package_id`); a pipeline or stream by its own id.
# ---------------------------------------------------------------------------

_Documents = Mapping[str, tuple[_Doc, ...]]


def _check_stream_endpoint_targets(documents: _Documents) -> list[tuple[str, dict]]:
    """Every stream slot carries an endpoint_ref — the stream's read/write target.

    A source (or destination) with no endpoint_ref object references nothing
    resolvable at load, so it is a referential failure rather than a skipped slot.
    (A non-object stream document is reported by `_check_stream_refs`, which cannot
    find its `stream_id`.)
    """
    findings: list[tuple[str, dict]] = []
    for doc in documents["stream"]:
        if not isinstance(doc.content, dict):
            continue
        source = doc.content.get("source")
        if not (isinstance(source, dict) and isinstance(source.get("endpoint_ref"), dict)):
            findings.append((doc.key, finding(
                rule="RULE-STRM-042",
                message_id="stream-source-no-endpoint-ref", kind="fail",
                path="/source/endpoint_ref",
                message="stream source has no endpoint_ref; it references no read target.",
            )))
        destinations = doc.content.get("destinations")
        if isinstance(destinations, list):
            for k, dest in enumerate(destinations):
                if not (isinstance(dest, dict) and isinstance(dest.get("endpoint_ref"), dict)):
                    findings.append((doc.key, finding(
                        rule="RULE-STRM-042",
                        message_id="stream-destination-no-endpoint-ref", kind="fail",
                        path=f"/destinations/{k}/endpoint_ref",
                        message="stream destination has no endpoint_ref; it references no write target.",
                    )))
    return findings


def _check_pipeline_id(documents: _Documents) -> list[tuple[str, dict]]:
    """A pipeline names itself: stream parent refs and pipeline references
    resolve against `pipeline_id`. A REFERENTIAL requirement — always checked,
    independent of whether the pipeline is currently runnable."""
    return [(doc.key, finding(
        rule="RULE-PIPE-018",
        message_id="pipeline-missing-pipeline-id", kind="fail",
        path="/pipeline_id",
        message="pipeline document has no pipeline_id; it cannot be resolved to a pipeline.",
    )) for doc in documents["pipeline"]
        if not isinstance(_content(doc).get("pipeline_id"), str) or not _content(doc)["pipeline_id"]]


def _check_pipeline_active(documents: _Documents) -> list[tuple[str, dict]]:
    """RUNNABILITY: only an `active` pipeline is executable. Run only for a
    pipeline about to run — an authoring tool validating a draft skips it."""
    findings: list[tuple[str, dict]] = []
    for doc in documents["pipeline"]:
        status = _content(doc).get("status")
        if status != "active":
            findings.append((doc.key, finding(
                rule="RULE-PIPE-019",
                message_id="pipeline-not-active", kind="fail", path="/status",
                message=f"pipeline status is {status!r}; only 'active' pipelines are runnable.",
            )))
    return findings


def _check_pipeline_active_gate(documents: _Documents) -> list[tuple[str, dict]]:
    """Per the pipeline contract's cross-field rules, an `active` pipeline must
    reference at least one stream AND at least one referenced stream must be
    runnable (`stream.status == 'active'`). (`draft`/`inactive` may be empty.)"""
    findings: list[tuple[str, dict]] = []
    for doc in documents["pipeline"]:
        pipeline = _content(doc)
        if pipeline.get("status") != "active":
            continue
        refs = pipeline.get("streams")
        referenced = {_base_id(r) for r in refs if isinstance(r, str) and r} if isinstance(refs, list) else set()
        if not referenced:
            findings.append((doc.key, finding(
                rule="RULE-PIPE-014",
                message_id="active-pipeline-no-stream-refs", kind="fail",
                path="/streams",
                message="an active pipeline must reference at least one stream.",
            )))
            continue
        runnable = any(
            stream.get("status") == "active"
            and isinstance(stream.get("stream_id"), str)
            and _base_id(stream["stream_id"]) in referenced
            for stream in map(_content, documents["stream"])
        )
        if not runnable:
            findings.append((doc.key, finding(
                rule="RULE-PIPE-014",
                message_id="active-pipeline-no-runnable-stream", kind="fail", path="/streams",
                message=(
                    "an active pipeline requires at least one runnable stream "
                    "(a referenced stream with status='active')."),
            )))
    return findings


def _check_stream_refs(documents: _Documents) -> list[tuple[str, dict]]:
    """Every `pipeline.streams[]` ref resolves to exactly one stream document
    (by declared id), with no duplicate refs and no duplicate documents."""
    findings: list[tuple[str, dict]] = []
    declared: dict[str, str] = {}
    for doc in documents["stream"]:
        stream_id = _content(doc).get("stream_id")
        if not isinstance(stream_id, str) or not stream_id:
            findings.append((doc.key, finding(
                rule="RULE-PIPE-011",
                message_id="bundled-stream-missing-id", kind="fail",
                path="/stream_id",
                message="stream document has no stream_id; no pipeline ref can resolve to it.",
            )))
            continue
        base = _base_id(stream_id)
        if base in declared:
            findings.append((doc.key, finding(
                rule="RULE-PIPE-011",
                message_id="duplicate-bundled-stream-id", kind="fail",
                path="/stream_id",
                message=f"stream id {base!r} is already declared by {declared[base]!r}.",
            )))
        else:
            declared[base] = doc.key

    for doc in documents["pipeline"]:
        refs = _content(doc).get("streams", [])
        if not isinstance(refs, list):
            findings.append((doc.key, finding(
                rule="RULE-PIPE-011",
                message_id="pipeline-streams-not-a-list", kind="fail", path="/streams",
                message=(
                    f"pipeline.streams must be a list of stream references, got "
                    f"{type(refs).__name__}; its stream refs cannot be resolved."),
            )))
            continue
        seen: dict[str, str] = {}
        for j, ref in enumerate(refs):
            if not isinstance(ref, str) or not ref:
                continue  # ref shape is the pipeline model's job
            base = _base_id(ref)
            if base in seen:
                findings.append((doc.key, finding(
                    rule="RULE-PIPE-011",
                    message_id="duplicate-stream-ref", kind="fail",
                    path=f"/streams/{j}",
                    message=(
                        f"pipeline.streams lists {ref!r} and {seen[base]!r}, which both resolve "
                        f"to stream id {base!r}."),
                )))
            else:
                seen[base] = ref
            if base not in declared:
                findings.append((doc.key, finding(
                    rule="RULE-PIPE-011",
                    message_id="stream-ref-unresolved", kind="fail",
                    path=f"/streams/{j}",
                    message=(
                        f"pipeline.streams references {ref!r} but no stream document "
                        f"declares id {base!r} (known: {sorted(declared)})."),
                )))
    return findings


def _check_stream_parent_pipeline(documents: _Documents) -> list[tuple[str, dict]]:
    """Every stream declares the pipeline it is handed with as its parent.

    A stream carries an immutable parent `pipeline_id`; one placed under a
    different pipeline would run under the wrong pipeline context, so a mismatch
    is a referential defect. (A stream that omits `pipeline_id` is a per-document
    shape defect the stream model reports.)
    """
    findings: list[tuple[str, dict]] = []
    for pipeline in map(_content, documents["pipeline"]):
        pipeline_id = pipeline.get("pipeline_id")
        if not isinstance(pipeline_id, str) or not pipeline_id:
            continue  # a missing pipeline id is already flagged by _check_pipeline_id
        parent = _base_id(pipeline_id)
        for doc in documents["stream"]:
            stream_parent = _content(doc).get("pipeline_id")
            if not isinstance(stream_parent, str) or not stream_parent:
                continue  # shape is the stream model's job
            if _base_id(stream_parent) != parent:
                findings.append((doc.key, finding(
                    rule="RULE-STRM-032",
                    message_id="stream-wrong-parent-pipeline", kind="fail",
                    path="/pipeline_id",
                    message=(
                        f"stream declares pipeline_id {stream_parent!r} but sits under "
                        f"pipeline {parent!r}; it belongs to a different pipeline."),
                )))
    return findings


def _check_connection_version_conflicts(documents: _Documents) -> list[tuple[str, dict]]:
    """A pipeline must not pin two different versions of one connection base.

    Connections resolve by base id, so `source={X}_v1` with `destination={X}_v2`
    would collapse to a single connection at load and cannot represent both — an
    ambiguous run.
    """
    findings: list[tuple[str, dict]] = []
    for doc in documents["pipeline"]:
        conns = _content(doc).get("connections")
        if not isinstance(conns, dict):
            continue
        by_base: dict[str, str] = {}
        for ref in (conns.get("source"), *(conns.get("destinations") or ())):
            if not isinstance(ref, str) or not ref:
                continue
            base = _base_id(ref)
            prior = by_base.get(base)
            if prior is not None and prior != ref:
                findings.append((doc.key, finding(
                    rule="RULE-PIPE-013",
                    message_id="connection-version-conflict", kind="fail",
                    path="/connections",
                    message=(
                        f"pipeline pins two versions of connection {base!r} ({prior!r} and "
                        f"{ref!r}); connections resolve by base id and cannot represent both."),
                )))
            else:
                by_base[base] = ref
    return findings


def _check_connections_present(documents: _Documents) -> list[tuple[str, dict]]:
    """Every connection a pipeline references is present exactly once.

    Two connections whose ids collapse to the same base are ambiguous — the
    run layout keys one document per base connection — so a duplicate is flagged.
    """
    findings: list[tuple[str, dict]] = []
    present: dict[str, str] = {}
    for doc in documents["connection"]:
        if not doc.package_id:
            continue
        base = _base_id(doc.package_id)
        if base in present:
            findings.append((doc.key, finding(
                rule="RULE-PIPE-012",
                message_id="duplicate-bundled-connection", kind="fail",
                path="",
                message=(
                    f"connection {base!r} is also declared by {present[base]!r}; "
                    "the run layout keys one per base."),
            )))
        else:
            present[base] = doc.key
    for doc in documents["pipeline"]:
        for cid in sorted(_pipeline_connection_ids(_content(doc))):
            if cid not in present:
                findings.append((doc.key, finding(
                    rule="RULE-PIPE-012",
                    message_id="connection-ref-unresolved", kind="fail",
                    path="/connections",
                    message=(
                        f"pipeline references connection {cid!r} but no connection document "
                        f"for it is present (known: {sorted(present)})."),
                )))
    return findings


def _check_stream_connection_roles(documents: _Documents) -> list[tuple[str, dict]]:
    """Per the pipeline contract's cross-field rules, a stream's SOURCE connection
    must equal `pipeline.connections.source`, and each DESTINATION connection must
    be one of `pipeline.connections.destinations` (compared base-form). Wiring a
    read/write side to the wrong role is a referential defect even though the
    connection is present in the pipeline."""
    findings: list[tuple[str, dict]] = []

    def _connection_id(ref: dict) -> str | None:
        cid = ref.get("connection_id")
        return cid if isinstance(cid, str) and cid else None

    for pipeline in map(_content, documents["pipeline"]):
        conns = pipeline.get("connections")
        conns = conns if isinstance(conns, dict) else {}
        src = conns.get("source")
        source_base = _base_id(src) if isinstance(src, str) and src else None
        dest_bases = {_base_id(d) for d in (conns.get("destinations") or []) if isinstance(d, str) and d}
        for doc in documents["stream"]:
            for pointer, ref in _stream_endpoint_refs(_content(doc)):
                cid = _connection_id(ref)
                path = f"{pointer}/connection_id"
                if pointer.startswith("/source/"):
                    if cid is None:
                        findings.append((doc.key, finding(
                            rule="RULE-STRM-033",
                            message_id="stream-source-no-connection-id", kind="fail", path=path,
                            message="source endpoint_ref names no connection_id.")))
                    elif source_base is None or _base_id(cid) != source_base:
                        findings.append((doc.key, finding(
                            rule="RULE-STRM-033",
                            message_id="stream-source-wrong-connection", kind="fail", path=path,
                            message=(
                                f"stream source connection {cid!r} must match the pipeline's "
                                f"connections.source ({src!r})."),
                        )))
                elif cid is None:
                    findings.append((doc.key, finding(
                        rule="RULE-STRM-033",
                        message_id="stream-destination-no-connection-id", kind="fail", path=path,
                        message="destination endpoint_ref names no connection_id.")))
                elif _base_id(cid) not in dest_bases:
                    findings.append((doc.key, finding(
                        rule="RULE-STRM-033",
                        message_id="stream-destination-wrong-connection", kind="fail", path=path,
                        message=(
                            f"stream destination connection {cid!r} is not one of the pipeline's "
                            f"connections.destinations ({sorted(dest_bases)})."),
                    )))
    return findings


def _check_connection_connector_refs(documents: _Documents) -> list[tuple[str, dict]]:
    """Every connection's `connector_id` names a connector that is present."""
    present = {doc.package_id for doc in documents["connector"]}
    findings: list[tuple[str, dict]] = []
    for doc in documents["connection"]:
        if not isinstance(doc.content, dict):
            continue
        connection_id = doc.content.get("connection_id")
        connector_id = doc.content.get("connector_id")
        if not isinstance(connector_id, str) or not connector_id:
            findings.append((doc.key, finding(
                rule="RULE-CONN-011",
                message_id="connection-no-connector-id", kind="fail",
                path="/connector_id",
                message=(
                    f"connection {connection_id!r} names no connector_id; "
                    "its connector cannot be resolved."),
            )))
        elif connector_id not in present:
            findings.append((doc.key, finding(
                rule="RULE-CONN-011",
                message_id="connector-ref-unresolved", kind="fail",
                path="/connector_id",
                message=(
                    f"connection {connection_id!r} references connector "
                    f"{connector_id!r} but it is not among the connectors present "
                    f"({sorted(present)})."),
            )))
    return findings


def _check_connection_scoped_endpoints(documents: _Documents) -> list[tuple[str, dict]]:
    """Every `scope='connection'` endpoint_ref resolves to an endpoint document
    in the package of the connection it names. `scope='connector'` refs resolve
    from the connector's endpoints: `_check_connector_scoped_endpoints`."""
    present = {(_base_id(doc.package_id), _content(doc).get("endpoint_id"))
               for doc in documents["database-endpoint"]}
    findings: list[tuple[str, dict]] = []
    for doc in documents["stream"]:
        for path, ref in _stream_endpoint_refs(_content(doc)):
            if ref.get("scope") != "connection":
                continue
            cid, eid = ref.get("connection_id"), ref.get("endpoint_id")
            if not isinstance(cid, str) or not cid:
                continue  # a missing connection_id is already flagged by the connection check
            if not isinstance(eid, str) or not eid:
                findings.append((doc.key, finding(
                    rule="RULE-STRM-034",
                    message_id="endpoint-ref-no-endpoint-id", kind="fail", path=path,
                    message=(
                        "connection-scoped endpoint_ref names no endpoint_id; its endpoint "
                        "cannot be resolved."),
                )))
            elif (_base_id(cid), eid) not in present:
                findings.append((doc.key, finding(
                    rule="RULE-STRM-034",
                    message_id="endpoint-ref-unresolved", kind="fail", path=path,
                    message=(
                        f"connection-scoped endpoint_ref (connection {_base_id(cid)!r}, endpoint "
                        f"{eid!r}) has no matching endpoint document."),
                )))
    return findings


def _check_connector_scoped_endpoints(documents: _Documents) -> list[tuple[str, dict]]:
    """Every `scope='connector'` endpoint_ref resolves to an endpoint document
    in the package of the connector its connection names. A connection that is
    not present is `_check_connections_present`'s to report."""
    connector_of = {_base_id(doc.package_id): _content(doc).get("connector_id")
                    for doc in documents["connection"]}
    present = {(doc.package_id, _content(doc).get("endpoint_id")) for doc in documents["api-endpoint"]}
    findings: list[tuple[str, dict]] = []
    for doc in documents["stream"]:
        for path, ref in _stream_endpoint_refs(_content(doc)):
            cid, eid = ref.get("connection_id"), ref.get("endpoint_id")
            if ref.get("scope") != "connector" or not isinstance(cid, str) or not isinstance(eid, str):
                continue
            connector_id = connector_of.get(_base_id(cid))
            if isinstance(connector_id, str) and (connector_id, eid) not in present:
                findings.append((doc.key, finding(
                    rule="RULE-STRM-044",
                    message_id="connector-endpoint-ref-unresolved", kind="fail", path=path,
                    message=(
                        f"connector-scoped endpoint_ref (connection {_base_id(cid)!r}, endpoint "
                        f"{eid!r}) has no endpoint document in connector {connector_id!r}."),
                )))
    return findings


# ---------------------------------------------------------------------------
# Bundle adapter + registration
# ---------------------------------------------------------------------------

def _bundle_documents(bundle: dict, pipeline: dict) -> dict[str, tuple[_Doc, ...]]:
    """The bundle's documents as the referential checks read them, each keyed
    by its pointer into the bundle. A connection or endpoint sits in the
    package its `connection_id` names, and a connector in the one its id names;
    a bundled endpoint entry is a connection-scoped endpoint unless it says
    otherwise."""
    def _package(cid: Any) -> str:
        return f"connections/{_base_id(cid)}/" if isinstance(cid, str) and cid else ""

    def _listed(value: Any) -> list:
        return value if isinstance(value, list) else []

    endpoints = [
        _Doc(f"/endpoints/{j}", _package(e.get("connection_id")), e)
        for j, e in enumerate(_listed(bundle.get("endpoints")))
        if isinstance(e, dict) and e.get("scope", "connection") == "connection"
        and isinstance(e.get("connection_id"), str) and isinstance(e.get("endpoint_id"), str)]
    return {
        "pipeline": (_Doc("/pipeline", "", pipeline),),
        "stream": tuple(_Doc(f"/streams/{i}", "", s) for i, s in enumerate(_listed(bundle.get("streams")))),
        "connection": tuple(
            _Doc(f"/connections/{i}", _package(c.get("connection_id") if isinstance(c, dict) else None), c)
            for i, c in enumerate(_listed(bundle.get("connections")))),
        "connector": tuple(_Doc("/connectors", f"connectors/{cid}/", None)
                           for cid in sorted(_connector_ids(bundle.get("connectors")))),
        "database-endpoint": tuple(endpoints),
    }


def validate_pipeline_bundle(bundle: Any, *, require_runnable: bool = True) -> list[dict]:
    """Validate referential integrity across an assembled pipeline bundle.

    `bundle` is a mapping of the already-parsed documents:
    `{pipeline, streams, connections, connectors, endpoints}`. `connectors` and
    `endpoints` supply only identity — the connector ids present, and the
    connection-scoped endpoint documents (`connection_id` + `endpoint_id`). Returns
    a list of findings (empty == referentially sound); every referential defect is
    error severity.

    `require_runnable` (default True) additionally gates RUNNABILITY: the pipeline
    must be `status='active'` with at least one referenced stream that is itself
    runnable — the check an executor needs. An authoring tool validating a **draft**
    bundle passes `require_runnable=False` to get the referential checks WITHOUT the
    active-status gate (a draft is expected not to be active, not a defect); the
    always-checked referential rules — including that the bundle names a
    `pipeline_id` — still apply.
    """
    if not isinstance(bundle, dict):
        # No rule to name: this rejects before any referential check — the ones
        # rule records bind — could even begin (rules/SCHEMA.md's generalized
        # first ruleless-fail case).
        return [finding(
            message_id="bundle-not-a-mapping", kind="fail", path="",
            message=(
                "pipeline bundle must be a mapping of pipeline/streams/connections/"
                "connectors/endpoints."),
        )]
    pipeline = bundle.get("pipeline")
    if not isinstance(pipeline, dict):
        return [finding(
            message_id="bundle-missing-pipeline-document",
            kind="fail", path="/pipeline",
            message="pipeline bundle is missing its 'pipeline' document.",
        )]
    documents = _bundle_documents(bundle, pipeline)
    checks = [
        _check_pipeline_id,
        _check_stream_refs,
        _check_stream_parent_pipeline,
        _check_stream_endpoint_targets,
        _check_connection_version_conflicts,
        _check_connections_present,
        _check_stream_connection_roles,
        _check_connection_connector_refs,
        _check_connection_scoped_endpoints,
        _check_endpoint_ids_unique,
    ]
    if require_runnable:
        checks += [_check_pipeline_active, _check_pipeline_active_gate]
    return [{**f, "path": key + f["path"]} for check in checks for key, f in check(documents)]


def _validate_pipeline_bundle(doc: Any, location: Any = None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature; a bundle reads nothing beside itself
    """Kind entry point: dispatch a bundle document to the referential validator.

    A bundle carries every document it references, so it reads nothing
    beside its `location` (the registry's per-kind signature), unused here.
    """
    return validate_pipeline_bundle(doc)


def _validate_pipeline_document(doc: Any) -> list[dict]:
    return _model_findings(doc, _PIPELINE_ADAPTER) + _missing_schema_url_findings(doc)


def _validate_pipeline_manifest_document(doc: Any) -> list[dict]:
    return _model_findings(doc, _PIPELINE_MANIFEST_ADAPTER)


# The bundle is registered BEFORE the single-pipeline document so the bundle
# detector claims an assembled-run mapping first; `is_pipeline_doc` then only sees
# a `connections`-bearing mapping with no nested `pipeline` document.
register_kind(is_pipeline_bundle, _validate_pipeline_bundle)
register_document_kind("pipeline", is_pipeline_doc, _validate_pipeline_document)
register_document_validator("pipeline-manifest", _validate_pipeline_manifest_document)
