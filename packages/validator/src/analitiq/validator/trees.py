"""`validate_tree` — a keyed document tree in, the `diagnostics` envelope out.

The path-based entry points read a document's siblings from disk. This one
reads them from a mapping of POSIX relative keys to file text (parsed here) or
already-parsed documents, so a consumer that does not run on the author's
filesystem — a remote validator handed a tree over the wire — grades exactly
what the local reader grades, through the same checks. Two layouts are
recognised from the keys:

- **connector tree** — `definition/connector.json` with its type maps and
  `endpoints/` beside it. The findings are those of validating that document
  by path: the kind dispatch, then `check_coverage` over the tree.
- **pipeline tree** — `pipeline.json`, `streams/*.json`,
  `connections/<slug>/connection.json` with that connection's
  `definition/endpoints/*.json` and `definition/type-map-{read,write}.json`,
  and `connectors/<slug>/definition/connector.json` with that connector's
  `definition/endpoints/*.json`. The pipeline document is graded by its model,
  then the bundle is assembled and handed to `validate_pipeline_bundle`
  (`require_runnable` iff the pipeline is `active`), each connection's type
  maps are graded in their own direction, and every `scope='connector'`
  endpoint ref is checked against the endpoints its connector publishes.

Every pipeline-tree stage runs under `_contained`: a crash in one is one
`adapter-crash` finding naming the stage, and the others still report. A stream,
connection or endpoint the bundle could not take — a crash reading it, text that
does not parse, an `Unreadable` entry, a non-object payload — withholds the
referential pass, since the published checks could not then tell "excluded here"
from "genuinely missing"; the finding at the member's key is the report until it
is fixed. A connector is the exception, and the loop that gathers them says why:
its directory slug already is its identity, so a malformed `connector.json`
costs only the `connector_id` alias.
"""
from __future__ import annotations

import difflib
from typing import Any, Mapping

from ._core import (
    diagnostics,
    finding,
    register_validator_ids,
    validate_document,
    _contained,
    _validate_at,
)
from ._tree import MemoryTree, Tree, key_problem
from .connectors import (
    endpoint_filename_findings,
    _LEGACY_MAP_FILENAME,
    _TYPE_MAP_FILENAMES,
    _validate_connection_type_map,
)
from .pipelines import validate_pipeline_bundle, _base_id, _iter_endpoint_refs

register_validator_ids({"connector-endpoint-ref"})

CONNECTOR_ROOT = "definition/connector.json"
PIPELINE_ROOT = "pipeline.json"


def validate_tree(documents: Mapping[str, Any]) -> dict:
    """Validate one connector or one pipeline handed over as a tree.

    `documents` maps POSIX relative keys to the file's text (`str` or `bytes`,
    parsed here — a parse failure is a finding, never an exception), an
    `Unreadable` standing for a file a reader found but could not read, or the
    already-parsed document. Which layout the tree is comes from its keys; a
    tree matching neither, or both, is one `document` error, as is a key that
    is empty, absolute, or steps through `.` / `..`.
    """
    bad = [key for key in documents if key_problem(key) is not None]
    if bad:
        return diagnostics([finding(
            "document", "error", "",
            "tree keys must be POSIX relative paths with no '.', '..' or empty "
            f"segment: {sorted(bad, key=repr)!r}")])
    tree = MemoryTree(documents)
    has_connector, has_pipeline = tree.occupied(CONNECTOR_ROOT), tree.occupied(PIPELINE_ROOT)
    if has_connector and has_pipeline:
        return diagnostics([finding(
            "document", "error", "",
            f"tree carries both {CONNECTOR_ROOT} and {PIPELINE_ROOT}; a tree is one "
            "connector or one pipeline.")])
    if has_connector:
        return diagnostics(_connector_tree_findings(tree))
    if has_pipeline:
        return diagnostics(_pipeline_tree_findings(tree))
    return diagnostics([finding(
        "document", "error", "",
        f"tree matches no known layout: a connector tree carries {CONNECTOR_ROOT}, "
        f"a pipeline tree {PIPELINE_ROOT}.")])


def _root_document(tree: Tree, key: str) -> tuple[Any, list[dict]]:
    doc, error = tree.read(key)
    if error is not None:
        return None, [finding("document", "error", key, f"Cannot read {key}: {error}")]
    return doc, []


def _connector_tree_findings(tree: Tree) -> list[dict]:
    doc, problems = _root_document(tree, CONNECTOR_ROOT)
    if problems:
        return problems
    return _validate_at(doc, tree.at(CONNECTOR_ROOT))


def _pipeline_tree_findings(tree: Tree) -> list[dict]:
    doc, problems = _root_document(tree, PIPELINE_ROOT)
    if problems:
        return problems
    findings = validate_document(doc, entity="pipeline")
    # Its own guarded unit: a crash assembling the bundle must not discard the
    # single-document findings above — the precise contract-model error a
    # malformed pipeline document already earned stays in the result alongside
    # the adapter-crash finding, instead of being replaced by it.
    with _contained(findings, "pipeline-bundle"):
        findings.extend(_bundle_findings(tree, doc))
    return findings


# ---------------------------------------------------------------------------
# Pipeline tree: layout helpers
# ---------------------------------------------------------------------------

def _slugs(tree: Tree, directory: str, *tail: str) -> list[str]:
    """Every `<slug>` with a file at `directory/<slug>/tail…`, sorted. A `*`
    segment in `tail` matches any one name."""
    found: set[str] = set()
    for key in tree.files(directory, recursive=True):
        parts = key.split("/")
        if len(parts) == 2 + len(tail) and all(
                want in ("*", have) for want, have in zip(tail, parts[2:])):
            found.add(parts[1])
    return sorted(found)


def _read_member(tree: Tree, key: str, findings: list[dict]) -> dict | None:
    """Read one bundle document. On text that does not parse or a non-object
    payload, append an error finding naming the key and return None — so a
    malformed member becomes a clear diagnostic instead of a silently dropped
    document."""
    doc, error = tree.read(key)
    name = key.rpartition("/")[2]
    if error is not None:
        findings.append(finding("document", "error", key, f"Cannot read {name}: {error}"))
        return None
    if not isinstance(doc, dict):
        findings.append(finding("document", "error", key, f"{name} is not a JSON object"))
        return None
    return doc


def _connector_id(tree: Tree, key: str) -> str | None:
    """The `connector_id` a connector document declares, best-effort: the
    directory slug already identifies the connector, so a document that does
    not parse or is not an object costs only the alias."""
    doc, _ = tree.read(key)
    cid = doc.get("connector_id") if isinstance(doc, dict) else None
    return cid if isinstance(cid, str) and cid else None


# ---------------------------------------------------------------------------
# Pipeline tree: stages
# ---------------------------------------------------------------------------

def _connection_type_map_findings(tree: Tree, slug: str, findings: list[dict]) -> None:
    """Grade the connection-scoped type maps beside one connection.json
    (`RULE-TMAP-023`) — files the assembled bundle never carries. A present map
    is graded in its own direction; the pre-split filename is rejected with a
    migration finding.

    Appends directly to the caller's shared `findings` list rather than
    building a local one to return: the legacy check and each type-map
    direction are independently-decidable units, each in its own `_contained`
    guard, so a crash in one costs only its own finding — never a list of
    already-decided results a crash partway through would otherwise discard
    before this function got the chance to return it."""
    site = f"connections/{slug}/definition"
    legacy = f"{site}/{_LEGACY_MAP_FILENAME}"
    with _contained(findings, legacy):
        if tree.occupied(legacy):
            findings.append(finding(
                "connection-type-map", "error", legacy,
                f"{_LEGACY_MAP_FILENAME} is the pre-split filename. Split it into "
                f"{_TYPE_MAP_FILENAMES['read']} (native → Arrow) and, for the write "
                f"direction, {_TYPE_MAP_FILENAMES['write']} (Arrow → native)."))
    for direction, fname in _TYPE_MAP_FILENAMES.items():
        key = f"{site}/{fname}"
        with _contained(findings, key):
            if not tree.occupied(key):
                continue
            doc, error = tree.read(key)
            if error is not None:
                findings.append(finding("connection-type-map", "error", key,
                                        f"Cannot read {fname}: {error}"))
                continue
            findings.extend({**f, "path": f"{key}{f['path']}"}
                            for f in _validate_connection_type_map(direction, doc, None))


def _assemble_bundle(tree: Tree, pipeline_doc: Any) -> tuple[dict, list[dict], bool, bool]:
    """Gather the pipeline bundle `validate_pipeline_bundle` grades: the
    pipeline plus its stream documents, every connection, the connection-scoped
    endpoint documents (stamped with their owning connection's id, which
    endpoint documents do not carry themselves), and the connector identities.
    Returns the bundle, any read-error findings for malformed members, whether
    every member in the tree actually made it into the bundle, and whether a
    containment guard is the reason any didn't — a crash or read error that
    excludes a member leaves the published bundle validator unable to tell
    "genuinely missing" from "excluded here", so a caller must know before
    trusting its referential verdicts, and separately must know whether that
    exclusion came from an actual crash (worth its own labeled finding) or an
    already-reported ordinary read error (which needs no second, misleading
    one)."""
    findings: list[dict] = []
    complete = True
    crashed = False

    # Each section below is wrapped in its own outer guard too, not just each
    # item within it: the listing is materialised before the loop starts, so a
    # failure enumerating it would otherwise escape every per-item guard and
    # abort this whole function before it could return what earlier sections
    # already decided.

    streams: list[dict] = []
    with _contained(findings, "streams") as section:
        for key in tree.files("streams"):
            # One stream is one independently-decidable unit, same as one
            # connection or one connector below: a crash reading it (e.g. a
            # pathologically deep document) must not discard the streams
            # already appended above.
            doc = None
            with _contained(findings, key) as outcome:
                doc = _read_member(tree, key, findings)
                if doc is not None:
                    streams.append(doc)
            if outcome.crashed:
                crashed = True
            if outcome.crashed or doc is None:
                complete = False
    if section.crashed:
        crashed = True
        complete = False

    connections: list[dict] = []
    endpoints: list[dict] = []
    with _contained(findings, "connections") as section:
        for slug in _slugs(tree, "connections", "connection.json"):
            # One connection is one independently-decidable unit: a crash
            # processing it must not discard the findings already decided for
            # connections processed earlier in this same loop. Reading the
            # connection is the part that can exclude a bundle member, so only
            # a crash here (or one from a per-endpoint guard below, which
            # reports through its own site) marks assembly incomplete.
            site = f"connections/{slug}"
            conn = None
            with _contained(findings, site) as outcome:
                conn = _read_member(tree, f"{site}/connection.json", findings)
                if conn is not None:
                    connections.append(conn)
                    connection_id = conn.get("connection_id")
                    for ep_key in tree.files(f"{site}/definition/endpoints"):
                        # One endpoint is its own independently-decidable unit, same
                        # as one stream or connection above: a crash reading it
                        # must not abort the loop and cost its siblings their
                        # place in the bundle — each gets its own guard rather
                        # than sharing the connection-level one above.
                        endpoint = None
                        with _contained(findings, ep_key) as ep_outcome:
                            endpoint = _read_member(tree, ep_key, findings)
                        if ep_outcome.crashed:
                            crashed = True
                        if ep_outcome.crashed or endpoint is None:
                            complete = False
                            continue
                        # Endpoint documents omit connection_id (server-managed); supply the
                        # owning connection's id so the bundle's endpoint-ref check can resolve
                        # connection-scoped references. Stamped into a copy: an
                        # in-memory tree hands back the caller's own object, and
                        # validating a tree must not write into what the caller
                        # still holds. What the document itself declares wins.
                        endpoint = {"connection_id": connection_id, "scope": "connection",
                                    **endpoint}
                        endpoints.append(endpoint)
                        # Files here are stem-addressed by construction (listed from
                        # definition/endpoints/), so the filename gate applies
                        # directly. The gate is its own guard too, run LAST: the
                        # endpoint is already in the bundle by this point, so a crash
                        # here costs only this one finding, not the bundle's completeness.
                        with _contained(findings, ep_key):
                            findings.extend(endpoint_filename_findings(
                                endpoint, ep_key.rpartition("/")[2]))
            if outcome.crashed:
                crashed = True
            if outcome.crashed or conn is None:
                complete = False
            # Connection-scoped type maps sit beside the connection, invisible
            # to the assembled-document bundle, and depend
            # only on the connection's directory — never on whether
            # connection.json itself parsed — so they are checked unconditionally:
            # a crash inside is contained per-direction by
            # _connection_type_map_findings itself, so this outer guard is a
            # backstop, never costs the bundle's completeness (which would
            # otherwise misreport a live connection as unresolved), and a
            # genuinely malformed or legacy type-map file is still reported even
            # when connection.json itself is unreadable.
            with _contained(findings, site):
                _connection_type_map_findings(tree, slug, findings)
    if section.crashed:
        crashed = True
        complete = False

    # Connectors supply identity only, and the directory slug already is that
    # identity — so a malformed connector.json is best-effort skipped (its slug
    # still counts), not a bundle error. A crash beyond the read errors already
    # handled (e.g. a pathologically deep document) is its own unit too, and it
    # costs only the connector_id alias (the slug is already recorded) — but a
    # connection naming that id rather than the slug would then wrongly read as
    # unresolved, so it still marks the bundle incomplete.
    connectors: set[str] = set()
    with _contained(findings, "connectors") as section:
        for slug in _slugs(tree, "connectors", "definition", "connector.json"):
            connectors.add(slug)  # directory slug
            with _contained(findings, f"connectors/{slug}") as outcome:
                cid = _connector_id(tree, f"connectors/{slug}/definition/connector.json")
                if cid is not None:
                    connectors.add(cid)
            if outcome.crashed:
                crashed = True
                complete = False
    if section.crashed:
        crashed = True
        complete = False

    bundle = {
        "pipeline": pipeline_doc,
        "streams": streams,
        "connections": connections,
        "connectors": sorted(connectors),
        "endpoints": endpoints,
    }
    return bundle, findings, complete, crashed


def _connector_endpoint_sets(tree: Tree, findings: list[dict]) -> dict[str, set[str]]:
    """Map each connector in the tree — by directory slug **and** its authored
    `connector_id` — to the set of endpoint ids it publishes (each
    `connectors/<slug>/definition/endpoints/*.json` contributes both its
    filename stem and its `endpoint_id` field, which the connector's own
    filename gate keeps equal for well-formed registry connectors — both are
    recorded to stay correct even if a malformed connector let them diverge).

    A connector with no endpoint file under `definition/endpoints/` is
    **omitted**, not recorded as an empty set: its endpoint set is *unknown*
    here (the author may not have downloaded endpoints for it), and an unknown
    set must not read as "no endpoints", which would warn on every ref. Callers
    treat a missing key as "cannot verify — skip" — the same treatment a crash
    reading one connector's endpoints gets here (contained per connector, so it
    costs only that connector's set, never every other connector's).

    The enumeration itself is wrapped in its own outer guard too: a failure
    there would otherwise escape every per-connector guard below and return
    nothing at all, rather than whatever connectors were already found."""
    sets: dict[str, set[str]] = {}
    with _contained(findings, "connectors"):
        for slug in _slugs(tree, "connectors", "definition", "endpoints", "*"):
            endpoint_dir = f"connectors/{slug}/definition/endpoints"
            with _contained(findings, endpoint_dir):
                ids: set[str] = set()
                for ep_key in tree.files(endpoint_dir):
                    ids.add(ep_key.rpartition("/")[2].removesuffix(".json"))
                    doc, _ = tree.read(ep_key)
                    eid = doc.get("endpoint_id") if isinstance(doc, dict) else None
                    if isinstance(eid, str) and eid:
                        ids.add(eid)
                if ids:
                    keys = {slug}
                    cid = _connector_id(tree, f"connectors/{slug}/definition/connector.json")
                    if cid is not None:
                        keys.add(cid)
                    for key in keys:
                        sets[key] = ids
    return sets


def _check_connector_endpoint_refs(streams: Any, connections: Any,
                                   connector_endpoint_sets: dict[str, set[str]],
                                   findings: list[dict]) -> None:
    """Verify every `scope='connector'` stream endpoint_ref names an endpoint that
    actually exists in the referenced connector's endpoint set. Emits a
    `connector-endpoint-ref` **warning** (never an error) per unresolved ref, with a
    closest-match alignment suggestion so the author can retarget the stream to
    the connector's real endpoint name (the connector is never edited).

    Skipped silently when the endpoint set is unknown (connector not downloaded), so
    absence never produces a false positive. Reuses the bundle validator's ref
    iterator and version-suffix normaliser so ref paths and connection-id matching
    stay identical to its own resolution.

    Appends directly to the caller's shared `findings` list: each ref is its own
    independently-decidable unit, contained on its own, so a crash checking one
    ref costs only that ref's warning — never the warnings already decided for
    refs checked earlier in this same loop, which building a local list and
    returning it once at the end would risk losing entirely."""
    conn_to_connector: dict = {}
    with _contained(findings, "connector-endpoint-refs"):
        for conn in connections if isinstance(connections, list) else []:
            if not isinstance(conn, dict):
                continue
            cid, connector = conn.get("connection_id"), conn.get("connector_id")
            if isinstance(cid, str) and isinstance(connector, str):
                conn_to_connector[_base_id(cid)] = connector

    for path, ref in _iter_endpoint_refs(streams):
        with _contained(findings, path):
            if ref.get("scope") != "connector":
                continue
            cid, eid = ref.get("connection_id"), ref.get("endpoint_id")
            if not (isinstance(cid, str) and cid and isinstance(eid, str) and eid):
                continue  # missing ids are contract-model / bundle-connection-ref concerns
            connector = conn_to_connector.get(_base_id(cid))
            if connector is None:
                continue  # unresolved connection — already flagged by the connection check
            endpoint_ids = connector_endpoint_sets.get(connector)
            if not endpoint_ids:
                continue  # endpoint set unknown (connector not downloaded) — cannot verify
            if eid in endpoint_ids:
                continue
            available = sorted(endpoint_ids)
            case_match = next((e for e in available if e.lower() == eid.lower()), None)
            close = difflib.get_close_matches(eid, available, n=1, cutoff=0.6)
            suggestion = case_match or (close[0] if close else None)
            hint = f" Did you mean {suggestion!r}?" if suggestion else ""
            findings.append(finding(
                "connector-endpoint-ref", "warning", path,
                f"endpoint_id {eid!r} is not among connector {connector!r}'s published "
                f"endpoints {available}.{hint} Align the stream's endpoint_ref to the "
                f"connector's endpoint name; the plugin never edits the connector.",
            ))


def _bundle_findings(tree: Tree, pipeline_doc: Any) -> list[dict]:
    bundle, findings, complete, crashed = _assemble_bundle(tree, pipeline_doc)
    # A draft pipeline is not yet runnable by design: its runnability verdicts
    # are an author-time expectation, not a defect. Ask the bundle validator for
    # referential integrity only (require_runnable=False) while the pipeline is
    # a draft, and enforce runnability once it is authored 'active'. Every
    # referential finding stays blocking either way. A non-dict pipeline
    # document already earned its own contract-model finding at the
    # single-document stage — treat it as not-yet-active here rather than
    # raising and discarding what _assemble_bundle just decided.
    require_runnable = isinstance(pipeline_doc, dict) and pipeline_doc.get("status") == "active"
    if complete:
        # Each of these two is its own unit: a crash in one must not discard the
        # per-connection findings _assemble_bundle already decided above, nor the
        # other unit's result.
        with _contained(findings, "pipeline"):
            findings.extend(validate_pipeline_bundle(bundle, require_runnable=require_runnable))
    elif crashed:
        # A containment guard above actually fired and excluded a member (that
        # finding already names which one and why). The published validator has
        # no way to tell "excluded here" from "genuinely missing", so running it
        # against a bundle this ambiguous risks reporting a reference as broken
        # that the crash, not the author, made unresolvable. Telling which
        # specific references an exclusion could taint would mean re-deriving
        # the validator's own reference-resolution logic here, so the whole
        # referential pass is skipped instead of risking that drift.
        findings.append(finding(
            "adapter-crash", "error", "pipeline",
            "a containment guard excluded at least one document from the bundle; "
            "cross-document referential integrity was not evaluated against a "
            "bundle this incomplete."))
    # else: assembly is incomplete only from ordinary, already-reported read errors
    # (no guard fired) — those findings (validator "document") already name the
    # defect precisely. Skipping the referential pass here is the same caution as
    # the crash case, but adding a second, adapter-crash-labeled finding would
    # claim a containment guard fired when nothing actually crashed.
    # The bundle validator receives connector identity only, so scope='connector'
    # endpoint refs go unresolved there. The tree has the connector endpoint
    # documents, so verify those refs here and warn (with an alignment
    # suggestion) rather than error: the documents on disk are the author's copy
    # of the connector, not proof of what it publishes (`RULE-STRM-042`).
    # _check_connector_endpoint_refs contains each ref on its own; this outer
    # guard is a backstop, e.g. against _connector_endpoint_sets itself.
    with _contained(findings, "connector-endpoint-refs"):
        _check_connector_endpoint_refs(
            bundle["streams"], bundle["connections"], _connector_endpoint_sets(tree, findings),
            findings)
    return findings
