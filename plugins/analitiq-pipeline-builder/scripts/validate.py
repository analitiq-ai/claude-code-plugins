#!/usr/bin/env python3
"""Validate an authored Analitiq document against the published contract.

This is a thin **adapter**: it dispatches to the published `analitiq-validator`
+ `analitiq-contract-models` packages (the same offline, model-driven contract
the Analitiq services validate against) and reduces every backend into one
Diagnostics envelope: ``{"passed": bool, "findings": [...]}``, `passed` fails
closed over every finding — a locally minted one (`validator`, `severity`,
`path`, `message`) or one forwarded unchanged from `analitiq.validator`
(`rule`, `message_id`, `kind`, `path`, `message`, `severity` only for
`kind: "fail"`) — through the published `analitiq.validator.finding_costs_a_pass`
(`skills/pipeline-builder/references/io-contracts.md`'s `Diagnostics` section
owns the shape and the predicate in full).

The published package exposes one single-document entry point plus one bundle
entry point. This adapter routes each entity as follows:

  * ``database-endpoint`` -> ``analitiq.validator.validate_document`` — the model
    plus the derived-``endpoint_id`` gate and column checks (the same code the
    ``analitiq-validate`` CLI runs).
  * ``connection`` / ``stream`` / ``pipeline`` -> the matching ``*Input`` Pydantic
    model's ``.model_validate`` (the source of truth the published JSON Schemas are
    rendered from). ``validate_document`` would reach the same models, but it
    selects them by *document shape*: its detectors key off ``connector_id``,
    ``destinations`` and ``connections`` respectively. An authored document that
    omits its discriminating key — precisely the broken input this adapter exists
    to diagnose — would match no detector and collapse into a single generic
    "unrecognized artifact" finding. Routing by the caller-supplied ``--entity``,
    which is already known here, guarantees the right model runs and yields
    per-field findings instead.
  * ``type-map`` -> ``analitiq.validator.type_map_findings`` at
    ``scope="connection"``. ``scope`` is how the gap-only nature of a connection
    map (``RULE-TMAP-018``) reaches the published check, which otherwise holds a
    write map to a connector's full vocabulary (``RULE-TMAP-017``).
  * ``pipeline`` with ``--bundle-root`` -> additionally
    ``analitiq.validator.validate_pipeline_bundle`` over the on-disk bundle, for the
    cross-document referential integrity no single document can verify. A draft
    bundle passes ``require_runnable=False`` (a not-yet-runnable draft is not an
    authoring error); an ``active`` pipeline is held to full runnability. The
    published bundle validator receives assembled documents, never a
    connection's directory, so the bundle pass also hands each connection's
    ``definition/`` to ``analitiq.validator.load_type_map`` — the loading a
    connector's map goes through — roots every finding at the entry it
    concerns, and grades the map it read as the ``type-map`` entity.

One check is the adapter's own, because it reads files the published
validator never receives:

  * ``connector-endpoint-ref`` — the published bundle validator receives
    connector *identity* only (slugs), never connector endpoint *contents*, so it
    leaves ``scope='connector'`` endpoint refs unresolved by design
    (``analitiq.validator.pipelines``: "out of scope for this check"). This plugin,
    unlike the service, has the downloaded connector endpoint files on disk, so it
    verifies each ``scope='connector'`` stream ref against the connector's on-disk
    endpoint set and emits a **warning** (with an alignment suggestion) when the
    referenced endpoint is absent. It never errors — connectors are trusted
    registry artifacts pinned by ``connector_version`` at runtime — and it never
    edits the connector; the orchestrator aligns the stream's ref instead.

Validation is offline — no schema is fetched. Usage::

    python3 plugins/analitiq-pipeline-builder/scripts/validate.py --entity pipeline --document path/to/pipeline.json --bundle-root .
    python3 plugins/analitiq-pipeline-builder/scripts/validate.py --entity type-map --document path/to/type-map.json

Exit status is ``0`` iff ``passed`` (``finding_costs_a_pass`` owns the full
predicate — a ``fail`` finding at ``severity: "error"``, or an unchecked
error-tier rule, both cost it), ``1`` on an unreadable document, ``2`` on a CLI
usage error.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import posixpath
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from _bootstrap import ensure_deps_or_reexec

# The plugin's document-authoring vocabulary — a literal, not an import of
# `analitiq.contracts.shared.rule_record.DOCUMENT_ARTIFACT_KINDS`: `--entity`'s
# `choices=` below is evaluated at argparse-parse time, before `main()` calls
# `ensure_deps_or_reexec`, so a module-level import here would raise
# `ImportError` for an end user who has not yet self-installed the pinned
# `analitiq-validator` — before the bootstrap that installs it ever runs.
# `test_pipeline_entities_are_a_document_artifact_kind_subset` pins this tuple
# to `DOCUMENT_ARTIFACT_KINDS` so the two cannot drift apart member by member.
# `connector` and `api-endpoint` are authored by the connector-builder plugin's
# own validator route, not this one.
PIPELINE_ENTITIES = ("connection", "stream", "pipeline", "database-endpoint", "type-map")


# ---------------------------------------------------------------------------
# Finding + Diagnostics shape
# ---------------------------------------------------------------------------

def _finding(validator: str, severity: str, path: str, message: str) -> dict:
    return {"validator": validator, "severity": severity, "path": path, "message": message}


def _diagnostics(findings: list[dict]) -> dict:
    from analitiq.validator import finding_costs_a_pass
    return {"passed": not any(finding_costs_a_pass(f) for f in findings), "findings": findings}


def _crash_finding(path: str, exc: BaseException) -> dict:
    """The shared finding every containment site emits: a guard fired and the
    document was not evaluated for that stage. `str(exc)` is empty for some
    exceptions (a bare `MemoryError()`), so the detail is only appended when
    there is one, never leaving a dangling `: `. A third-party backend may
    raise an exception whose own `__str__` raises — this function must never
    itself become a second, unguarded crash, so that failure is swallowed too."""
    try:
        detail = str(exc)
    except Exception:
        detail = ""
    message = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    return _finding("adapter-crash", "error", path, message)


class _Outcome:
    """Mutable result of one `_contained` block, readable by the caller after
    the `with` exits — the only way to tell a block that ran clean from one a
    crash cut short, since the exception itself never escapes."""

    def __init__(self) -> None:
        self.crashed = False


@contextlib.contextmanager
def _contained(findings: list[dict], path: str):
    """Run one independently-decidable stage. Any exception besides
    `MemoryError` becomes one `adapter-crash` finding and the walk continues
    past it. `MemoryError` re-raises: only the outermost guard in `main()`
    turns it into a finding, so a resource-exhaustion event yields exactly one
    finding rather than one per in-progress unit. Yields a `_Outcome` so a
    caller that must know whether this stage actually completed — e.g. before
    trusting a collection it fed into a downstream referential check — can
    check `.crashed` once the block exits."""
    outcome = _Outcome()
    try:
        yield outcome
    except MemoryError:
        raise
    except Exception as exc:
        outcome.crashed = True
        findings.append(_crash_finding(path, exc))


# ---------------------------------------------------------------------------
# Per-entity validation (importable + unit-testable; analitiq imported lazily so
# importing this module never requires the validator to be installed)
# ---------------------------------------------------------------------------

def _model_findings(entity: str, doc) -> list[dict]:
    """Validate a single connection/stream/pipeline document against its published
    contract model, mapping each Pydantic error to a finding. The message is the
    validator's own rendering of the error, so a document value it echoes is
    clipped here exactly as it is there."""
    if entity == "connection":
        from analitiq.contracts.connection import ConnectionInput as Model
    elif entity == "stream":
        from analitiq.contracts.stream import StreamInput as Model
    elif entity == "pipeline":
        from analitiq.contracts.pipelines.config import PipelineInput as Model
    else:  # pragma: no cover - guarded by the entity choices
        raise ValueError(f"no contract model for entity {entity!r}")
    from pydantic import ValidationError
    from analitiq.validator._core import _model_error_message, document_pointer
    try:
        Model.model_validate(doc)
        return []
    except ValidationError as exc:
        return [
            _finding("contract-model", "error",
                     document_pointer(err["loc"], Model.__pydantic_core_schema__),
                     _model_error_message(err))
            for err in exc.errors()
        ]


def _endpoint_findings(doc, document_path: Path) -> list[dict]:
    from analitiq.validator import validate_document
    return validate_document(doc, doc_path=document_path)


def _type_map_findings(doc) -> list[dict]:
    """Grade a connection-scoped type-map document."""
    from analitiq.validator import type_map_findings
    return type_map_findings(doc, scope="connection")


def _connection_type_map_findings(conn_dir: Path, findings: list[dict]) -> None:
    """The published validator's loading of the type map beside one
    connection.json, each finding rooted at the entry it concerns (the
    `definition` directory itself for one about the directory), and the map it
    read graded at connection scope. The loading cites no rule: the record it
    cites beside a connector binds a connector package.

    Appends to the caller's list rather than returning one so that a crash
    grading the map costs only that map's finding, never the findings already
    decided."""
    from analitiq.validator import TYPE_MAP_FILENAME, load_type_map
    site = f"connections/{conn_dir.name}/definition"
    load = load_type_map(conn_dir / "definition", rule=None)
    for entry, f in load.findings:
        findings.extend(_at_site(str(PurePosixPath(site, entry)), [f]))
    if load.loaded:
        map_site = f"{site}/{TYPE_MAP_FILENAME}"
        with _contained(findings, map_site):
            findings.extend(_at_site(map_site, _type_map_findings(load.document)))


def _at_site(site: str, findings: list[dict]) -> list[dict]:
    """Re-root a member's own findings at the entry they came from.

    A document graded on its own reports a pointer into itself (`/scope`), which
    is the whole address when that document is what was validated. A bundle
    holds many, so the same pointer names none of them — the reader is told
    what is wrong and not which entry to open."""
    return [{**f, "path": _rooted(site, f.get("path", ""))} for f in findings]


def _rooted(site: str, path: str) -> str:
    """`path`, reported by grading the entry at `site`, addressed from the
    bundle root.

    A bare pointer is into the entry itself. Any other path names another
    document, as `<reference>#<pointer>` with the percent-encoded reference
    relative to the entry's directory (`rules/SCHEMA.md`, "Findings")."""
    from analitiq.validator._core import is_bare_pointer
    if is_bare_pointer(path):
        target, pointer = site, path
    else:
        reference, _, pointer = path.partition("#")
        target = posixpath.normpath(
            posixpath.join(posixpath.dirname(site), unquote(reference)))
    return f"{target}{pointer}"


def _read_bundle_member(path: Path, findings: list[dict]) -> dict | None:
    """Read one sibling bundle document. On an unreadable/invalid file or a
    non-object payload, append an error finding and return None — so a malformed
    sibling becomes a clear diagnostic instead of an uncaught traceback or a
    silently dropped document."""
    from analitiq.validator._core import _JSON_READ_ERRORS
    try:
        doc = _read_json(path)
    except _JSON_READ_ERRORS as exc:
        findings.append(_finding("document", "error", "", f"Cannot read {path.name}: {exc}"))
        return None
    if not isinstance(doc, dict):
        findings.append(_finding("document", "error", "", f"{path.name} is not a JSON object"))
        return None
    return doc


def _assemble_bundle(pipeline_doc: dict, document_path: Path,
                     root: Path) -> tuple[dict, list[dict], bool, bool]:
    """Gather the on-disk pipeline bundle the way the engine resolves it at load:
    the pipeline plus its sibling stream documents, every connection, the
    connection-scoped endpoint documents (stamped with their owning connection's
    id, which endpoint documents do not carry themselves), and the downloaded
    connector identities. Returns the bundle, any read-error findings for
    malformed siblings, whether every member on disk actually made it into the
    bundle, and whether a containment guard is the reason any didn't — a crash
    or read error that excludes a member leaves the published bundle validator
    unable to tell "genuinely missing" from "excluded here", so a caller must
    know before trusting its referential verdicts, and separately must know
    whether that exclusion came from an actual crash (worth its own labeled
    finding) or an already-reported ordinary read error (which needs no second,
    misleading one)."""
    from analitiq.validator._core import _JSON_READ_ERRORS
    # `validate_pipeline_bundle` takes filename-less dicts, so every check that
    # needs a name — RULE-PKG-031, on where an endpoint document ships — is run
    # here, per file, where the names are known.
    findings: list[dict] = []
    complete = True
    crashed = False

    # Each section below is wrapped in its own outer guard too, not just each
    # item within it: `sorted(...glob(...))` itself materializes the whole
    # listing before the loop even starts, so a filesystem failure enumerating
    # it (a vanished directory, a permission error) would otherwise escape
    # every per-item guard and abort this whole function before it could
    # return what earlier sections already decided.

    streams: list[dict] = []
    with _contained(findings, "streams") as section:
        for p in sorted((document_path.parent / "streams").glob("*.json")):
            # One stream is one independently-decidable unit, same as one
            # connection or one connector below: a crash reading it (e.g. a
            # pathologically deep document) must not discard the streams
            # already appended above.
            doc = None
            with _contained(findings, f"streams/{p.name}") as outcome:
                doc = _read_bundle_member(p, findings)
                if doc is not None:
                    streams.append(doc)
                    # Grade it as the document it is, not only as a member of
                    # the bundle: the referential checks below read a stream's
                    # refs and never its shape, so an unbundled member would
                    # otherwise be graded on this route and a bundled one not.
                    # Its own guard, like the endpoint branch below: a crash
                    # here costs these findings, never the stream's place in
                    # the bundle, which the append above already gave it.
                    with _contained(findings, f"streams/{p.name}"):
                        findings.extend(_at_site(f"streams/{p.name}",
                                                 _model_findings("stream", doc)))
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
        for conn_json in sorted((root / "connections").glob("*/connection.json")):
            # One connection is one independently-decidable unit: a crash
            # processing it must not discard the findings already decided for
            # connections processed earlier in this same loop. Reading the
            # connection is the part that can exclude a bundle member, so only
            # a crash here (or one from a per-endpoint guard below, which
            # reports through its own site) marks assembly incomplete.
            conn = None
            with _contained(findings, f"connections/{conn_json.parent.name}") as outcome:
                conn = _read_bundle_member(conn_json, findings)
                if conn is not None:
                    connections.append(conn)
                    conn_site = f"connections/{conn_json.parent.name}/connection.json"
                    # Own guard, same reason as the stream and endpoint
                    # branches: a crash grading this connection's shape must
                    # not take the connection — and every endpoint under it —
                    # out of the bundle the referential pass reads.
                    with _contained(findings, conn_site):
                        findings.extend(_at_site(conn_site,
                                                 _model_findings("connection", conn)))
                    connection_id = conn.get("connection_id")
                    for ep_json in sorted((conn_json.parent / "definition" / "endpoints").glob("*.json")):
                        # One endpoint is its own independently-decidable unit, same
                        # as one stream or connection above: a crash reading it
                        # (e.g. a pathologically deep document) must not abort the
                        # loop and cost its siblings their place in the bundle —
                        # each gets its own guard rather than sharing the
                        # connection-level one above.
                        ep_site = f"connections/{conn_json.parent.name}/definition/endpoints/{ep_json.name}"
                        endpoint = None
                        with _contained(findings, ep_site) as ep_outcome:
                            endpoint = _read_bundle_member(ep_json, findings)
                        if ep_outcome.crashed:
                            crashed = True
                        if ep_outcome.crashed or endpoint is None:
                            complete = False
                            continue
                        # Grade the document the same way validating this one
                        # file on its own does — every rule it settles alone,
                        # not just the filename. It runs BEFORE the bundle keys
                        # below are set on it: the endpoint models forbid
                        # unknown keys, so a `connection_id`/`scope` supplied
                        # here would come back as the author's error. Files
                        # here are stem-addressed by construction (globbed from
                        # definition/endpoints/), so the filename gate inside
                        # applies directly and is not called separately. Its own
                        # guard: a crash costs these findings, never the
                        # endpoint's place in the bundle, which the lines below
                        # still give it.
                        with _contained(findings, ep_site):
                            findings.extend(_at_site(
                                ep_site, _endpoint_findings(endpoint, ep_json)))
                        # Endpoint documents omit connection_id (server-managed); supply the
                        # owning connection's id so the bundle's endpoint-ref check can resolve
                        # connection-scoped references. Assigned rather than defaulted: where
                        # the document carries one of these the grading above has just refused
                        # it, and letting a refused value stand would attach the endpoint
                        # under whatever it names — the bundle would then report the stream
                        # referencing it as unresolved, blaming a document whose reference is
                        # correct. The connection an endpoint sits under is what says which
                        # one it belongs to.
                        endpoint["connection_id"] = connection_id
                        endpoint["scope"] = "connection"
                        endpoints.append(endpoint)
            if outcome.crashed:
                crashed = True
            if outcome.crashed or conn is None:
                complete = False
            # A connection-scoped type map is a file beside the connection,
            # invisible to the assembled-document bundle, and depends
            # only on conn_json.parent — never on whether connection.json itself
            # parsed — so it is checked unconditionally, and a crash inside
            # never costs the bundle's completeness (which would otherwise
            # misreport a live connection as unresolved).
            with _contained(findings, f"connections/{conn_json.parent.name}"):
                _connection_type_map_findings(conn_json.parent, findings)
    if section.crashed:
        crashed = True
        complete = False

    # Connectors supply identity only, and the directory slug already is that
    # identity — so a malformed connector.json is best-effort skipped (its slug
    # still counts), not a bundle error. A crash beyond the read errors already
    # handled below (e.g. a pathologically deep document) is its own unit too,
    # and it costs only the connector_id alias below (the slug is already
    # recorded) — but a connection naming that id rather than the slug would
    # then wrongly read as unresolved, so it still marks the bundle incomplete.
    connectors: set[str] = set()
    with _contained(findings, "connectors") as section:
        for conn_json in sorted((root / "connectors").glob("*/definition/connector.json")):
            connectors.add(conn_json.parent.parent.name)  # directory slug
            with _contained(findings, f"connectors/{conn_json.parent.parent.name}") as outcome:
                try:
                    cid = _read_json(conn_json).get("connector_id")
                except (*_JSON_READ_ERRORS, AttributeError):
                    cid = None
                if isinstance(cid, str) and cid:
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


def _connector_endpoint_sets(root: Path, findings: list[dict]) -> dict[str, set[str]]:
    """Map each downloaded connector — by directory slug **and** its authored
    `connector_id` — to the set of endpoint ids it publishes on disk (each
    `connectors/<slug>/definition/endpoints/*.json` contributes both its filename
    stem and its `endpoint_id` field, which the connector's own filename gate keeps
    equal for well-formed registry connectors — this adapter records both to stay
    correct even if a malformed connector let them diverge).

    A connector whose `definition/endpoints/` directory is absent or empty is
    **omitted**, not recorded as an empty set: its endpoint set is *unknown* here
    (the plugin may not have downloaded endpoints for it), and an unknown set must
    not read as "no endpoints", which would warn on every ref. Callers treat a
    missing key as "cannot verify — skip" — the same treatment a crash reading
    one connector's endpoints gets here (contained per connector, so it costs
    only that connector's set, never every other connector's).

    The enumeration itself (`sorted(root.glob(...))`, which materializes the
    full listing before the loop runs) is wrapped in its own outer guard too:
    a filesystem failure there would otherwise escape every per-connector
    guard below and return nothing at all, rather than whatever connectors
    were already found before it."""
    from analitiq.validator._core import _JSON_READ_ERRORS
    sets: dict[str, set[str]] = {}
    with _contained(findings, "connectors"):
        for ep_dir in sorted(root.glob("connectors/*/definition/endpoints")):
            slug_dir = ep_dir.parent.parent  # connectors/<slug>
            with _contained(findings, f"connectors/{slug_dir.name}/definition/endpoints"):
                if not ep_dir.is_dir():
                    continue
                ids: set[str] = set()
                for ep_json in sorted(ep_dir.glob("*.json")):
                    ids.add(ep_json.stem)
                    try:
                        eid = _read_json(ep_json).get("endpoint_id")
                    except (*_JSON_READ_ERRORS, AttributeError):
                        eid = None
                    if isinstance(eid, str) and eid:
                        ids.add(eid)
                if ids:
                    keys = {slug_dir.name}
                    try:
                        cid = _read_json(slug_dir / "definition" / "connector.json").get("connector_id")
                    except (*_JSON_READ_ERRORS, AttributeError):
                        cid = None
                    if isinstance(cid, str) and cid:
                        keys.add(cid)
                    for key in keys:
                        sets[key] = ids
    return sets


def _check_connector_endpoint_refs(streams, connections,
                                   connector_endpoint_sets: dict[str, set[str]],
                                   findings: list[dict]) -> None:
    """Verify every `scope='connector'` stream endpoint_ref names an endpoint that
    actually exists in the referenced connector's on-disk endpoint set. Emits a
    `connector-endpoint-ref` **warning** (never an error) per unresolved ref, with a
    closest-match alignment suggestion so the orchestrator can retarget the stream to
    the connector's real endpoint name (it never edits the connector).

    Skipped silently when the endpoint set is unknown (connector not downloaded), so
    absence never produces a false positive. Reuses the published ref iterator and
    version-suffix normaliser so ref paths and connection-id matching stay identical
    to the bundle validator's own resolution.

    Appends directly to the caller's shared `findings` list: each ref is its own
    independently-decidable unit, contained on its own, so a crash checking one
    ref (a validator regression in `_base_id`, say) costs only that ref's
    warning — never the warnings already decided for refs checked earlier in
    this same loop, which building a local list and returning it once at the
    end would risk losing entirely."""
    import difflib
    from analitiq.validator.pipelines import _base_id, _iter_endpoint_refs

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
                continue  # missing ids are the contract model's concern, or RULE-STRM-033/034's
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
            findings.append(_finding(
                "connector-endpoint-ref", "warning", path,
                f"endpoint_id {eid!r} is not among connector {connector!r}'s published "
                f"endpoints {available}.{hint} Align the stream's endpoint_ref to the "
                f"connector's endpoint name; the plugin never edits the connector.",
            ))


def is_runnable_required(pipeline_doc: object) -> bool:
    """Whether this run's bundle is held to runnability — the published
    derivation, called rather than restated, so this adapter and the bundle
    validator it hands the flag to cannot answer differently.

    Public (no leading underscore): `scripts/gen_pipeline_docs.py` calls this
    directly to measure which `require_runnable`-gated rules this adapter can
    actually surface, rather than reasoning about the gate from outside it."""
    from analitiq.validator import is_runnable_required as published

    return published(pipeline_doc)


def _bundle_findings(pipeline_doc: dict, document_path: Path, root: Path) -> list[dict]:
    from analitiq.validator import validate_pipeline_bundle
    bundle, findings, complete, crashed = _assemble_bundle(pipeline_doc, document_path, root)
    # Every referential finding stays blocking whether or not runnability is
    # enforced too — see is_runnable_required for what the flag itself decides.
    require_runnable = is_runnable_required(pipeline_doc)
    if complete:
        # Each of these two is its own unit: a crash in one must not discard the
        # per-connection findings _assemble_bundle already decided above, nor the
        # other unit's result.
        with _contained(findings, "pipeline"):
            findings.extend(validate_pipeline_bundle(bundle, require_runnable=require_runnable))
    elif crashed:
        # A containment guard above actually fired and excluded an on-disk member
        # (that finding already names which one and why). The published validator
        # has no way to tell "excluded here" from "genuinely missing", so running
        # it against a bundle this ambiguous risks reporting a reference as broken
        # that the crash, not the author, made unresolvable. Telling which specific
        # references an exclusion could taint would mean re-deriving the published
        # validator's own reference-resolution logic locally, so the whole
        # referential pass is skipped instead of risking that drift.
        findings.append(_finding(
            "adapter-crash", "error", "pipeline",
            "a containment guard excluded at least one on-disk document from the "
            "bundle; cross-document referential integrity was not evaluated against "
            "a bundle this incomplete."))
    # else: assembly is incomplete only from ordinary, already-reported read errors
    # (no guard fired) — those findings (validator "document") already name the
    # defect precisely. Skipping the referential pass here is the same caution as
    # the crash case, but adding a second, adapter-crash-labeled finding would
    # claim a containment guard fired when nothing actually crashed.
    # Plugin-local aid the published bundle can't make: it receives connector identity
    # only, so scope='connector' endpoint refs go unresolved. The plugin has the
    # downloaded connector endpoint files, so verify those refs here and warn (with an
    # alignment suggestion) rather than error — connectors are trusted, pinned at
    # runtime. _check_connector_endpoint_refs contains each ref on its own; this
    # outer guard is a backstop, e.g. against _connector_endpoint_sets itself.
    with _contained(findings, "connector-endpoint-refs"):
        _check_connector_endpoint_refs(
            bundle["streams"], bundle["connections"], _connector_endpoint_sets(root, findings), findings)
    return findings


def diagnostics_for(entity: str, document_path: Path, bundle_root: Path | None = None) -> dict:
    """Validate one document and return the Diagnostics envelope. Raises nothing
    for validation failures — those become findings; only a genuinely unreadable
    document short-circuits."""
    from analitiq.validator._core import _JSON_READ_ERRORS
    try:
        doc = _read_json(document_path)
    except _JSON_READ_ERRORS as exc:
        return _diagnostics([_finding("document", "error", "", f"Cannot read document: {exc}")])

    if entity == "database-endpoint":
        findings = _endpoint_findings(doc, document_path)
    elif entity == "type-map":
        findings = _type_map_findings(doc)
    else:
        findings = _model_findings(entity, doc)
        if entity == "pipeline" and bundle_root is not None:
            # Its own guarded unit: a crash enriching the bundle (e.g. `doc` is
            # not an object, so `_bundle_findings`'s own field access on it
            # raises) must not discard the single-document findings above —
            # the precise contract-model error a malformed pipeline document
            # already earned stays in the result alongside the adapter-crash
            # finding, instead of being replaced by it.
            with _contained(findings, "pipeline-bundle"):
                findings.extend(_bundle_findings(doc, document_path, bundle_root))
    return _diagnostics(findings)


def _read_json(path: Path):
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entity", required=True, choices=PIPELINE_ENTITIES,
                        help="Which published contract the document is authored against.")
    parser.add_argument("--document", required=True, help="Path to the JSON document to validate.")
    parser.add_argument("--bundle-root",
                        help="Project root for cross-document validation of a stitched pipeline "
                             "(walks connections/, connectors/, and the pipeline's streams/). "
                             "Only meaningful with --entity pipeline.")
    args = parser.parse_args(argv)

    # The one guard that must contain everything Python exception handling can
    # contain, MemoryError included — it is reached however deep the failing
    # call is, so it is what keeps such a crash anywhere in the dispatch below
    # from ever reaching the interpreter's own uncaught-exception handling (a
    # traceback on stderr, nothing on stdout). A SystemExit raised below this
    # point (or anything else `except Exception` does not catch) still escapes
    # uncontained — the driving agent's stderr-excerpt fallback is for exactly
    # that case.
    try:
        ensure_deps_or_reexec(__file__)
        bundle_root = Path(args.bundle_root) if args.bundle_root else None
        diagnostics = diagnostics_for(args.entity, Path(args.document), bundle_root)
        # Serialized inside the guard: a backend finding carrying a
        # JSON-incompatible value (a malformed message from a validator
        # regression) must itself become an adapter-crash result, not a
        # TypeError escaping after the guard has already exited clean.
        output = json.dumps(diagnostics, indent=2)
        passed = diagnostics["passed"]
    except Exception as exc:
        # Not through `_diagnostics`: whatever crashed here may leave
        # `analitiq.validator` unimportable and its `finding_costs_a_pass` out
        # of reach. A crash finding costs a pass under that predicate anyway,
        # so this states the verdict it would reach rather than risking a
        # second, uncontained failure reporting it.
        print(json.dumps({"passed": False, "findings": [_crash_finding("", exc)]}, indent=2))
        return 1

    print(output)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
