#!/usr/bin/env python3
"""Validate an authored Analitiq document against the published contract.

This is a thin **adapter**: it dispatches to the published `analitiq-validator`
+ `analitiq-contract-models` packages (the same offline, model-driven contract
the Analitiq services validate against) and reduces every backend into one
Diagnostics envelope: ``{"passed": bool, "findings": [...]}``, `passed` fails
closed over every finding — a locally minted one (`validator`, `severity`,
`path`, `message`) or one forwarded unchanged from `analitiq.validator`
(`rule`, `message_id`, `kind`, `path`, `message`, `severity` only for
`kind: "fail"`) — through `_finding_costs_a_pass` (`skills/pipeline-builder/references/io-contracts.md`'s
`Diagnostics` section owns the shape and the predicate in full).

The published package exposes one single-document entry point plus one bundle
entry point. This adapter routes each entity as follows:

  * ``database_endpoint`` -> ``analitiq.validator.validate_document`` — the model
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
  * ``type_map_read`` / ``type_map_write`` -> ``analitiq.validator.validate_document``
    over the connection-scoped type-map rule array, after an adapter filename gate:
    the engine loads ``connections/<slug>/definition/type-map-{read,write}.json`` by
    exactly those names (and the published validator derives rule direction from
    them, defaulting an unknown name to read), so a misnamed file gets the rename
    finding alone rather than findings that could be graded in the wrong direction.
    The published ``type-map-write-coverage`` warning is filtered out here: it
    presumes a connector's full-vocabulary write map, which a gap-only connection
    map deliberately is not (see ``_type_map_findings``).
  * ``pipeline`` with ``--bundle-root`` -> additionally
    ``analitiq.validator.validate_pipeline_bundle`` over the on-disk bundle, for the
    cross-document referential integrity no single document can verify. A draft
    bundle passes ``require_runnable=False`` (a not-yet-runnable draft is not an
    authoring error); an ``active`` pipeline is held to full runnability.

Each check below is the adapter's own: it reads something on disk — a
connection's directory, a downloaded connector's endpoint files — that the
published validator never receives, so the published contract structurally
cannot make it:

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
  * ``connection-type-map`` — the published bundle validator receives assembled
    documents, never a connection's directory, so it cannot see the type-map
    files the engine loads beside ``connection.json``. The bundle pass therefore
    validates each connection's present ``type-map-{read,write}.json`` in full
    (via the published validator) and rejects the dead pre-split ``type-map.json``
    filename with a migration finding, mirroring the published connector-side
    check at connection scope.

Validation is offline — no schema is fetched. Usage::

    python3 plugins/analitiq-pipeline-builder/scripts/validate.py --entity pipeline --document path/to/pipeline.json --bundle-root .

Exit status is ``0`` iff ``passed`` (``_finding_costs_a_pass`` owns the full
predicate — a ``fail`` finding at ``severity: "error"``, or an unchecked
error-tier rule, both cost it), ``1`` on an unreadable document, ``2`` on a CLI
usage error.
"""
from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path

from _bootstrap import ensure_deps_or_reexec

ENTITIES = ("pipeline", "stream", "connection", "database_endpoint",
            "type_map_read", "type_map_write")

# The engine loads connection-scoped type maps by these exact filenames under
# connections/<slug>/definition/ — a differently-named file is silently ignored
# at runtime, so the adapter gates the name like the endpoint filename gate does.
_TYPE_MAP_FILENAMES = {"type_map_read": "type-map-read.json",
                       "type_map_write": "type-map-write.json"}
# The pre-split filename: the engine never reads it, at either scope. The
# published validator rejects it beside a connector; the adapter mirrors that
# for connections, where the published bundle validator cannot see files.
_LEGACY_TYPE_MAP_FILENAME = "type-map.json"


# ---------------------------------------------------------------------------
# Finding + Diagnostics shape
# ---------------------------------------------------------------------------

def _finding(validator: str, severity: str, path: str, message: str) -> dict:
    return {"validator": validator, "severity": severity, "path": path, "message": message}


def _finding_costs_a_pass(f: dict) -> bool:
    """A local copy of ``analitiq.validator.finding_costs_a_pass``.

    Not imported: this adapter self-installs ``analitiq-validator`` at
    ``VALIDATOR_PIN`` (``_bootstrap.py``), a released version that predates
    this predicate's addition, so importing it would raise ``ImportError`` in
    every normal (non-source) run. ``test_finding_costs_a_pass_matches_the_published_predicate``
    holds this copy to the source one so the two cannot silently diverge; fold
    this back into an import once the pin reaches a release that carries it.
    """
    kind = f.get("kind", "fail")
    if kind == "fail":
        return f.get("severity") == "error"
    if kind == "notApplicable":
        rule = f.get("rule")
        if rule is None:
            return True
        from analitiq.contracts.shared.rules import rule_by_id
        return rule_by_id(rule).severity == "error"
    return False


def _diagnostics(findings: list[dict]) -> dict:
    # A published finding can now be notApplicable against a rule this
    # predicate looks up, which a bare `severity == "error"` check reads past
    # silently. This adapter's own locally-minted findings (no `kind` key) are
    # unaffected — `_finding_costs_a_pass` grades those exactly as this line
    # always did.
    passed = not any(_finding_costs_a_pass(f) for f in findings)
    return {"passed": passed, "findings": findings}


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
    contract model, mapping each Pydantic error to a finding (the same mapping the
    validator itself uses internally)."""
    if entity == "connection":
        from analitiq.contracts.connection import ConnectionInput as Model
    elif entity == "stream":
        from analitiq.contracts.stream import StreamInput as Model
    elif entity == "pipeline":
        from analitiq.contracts.pipelines.config import PipelineInput as Model
    else:  # pragma: no cover - guarded by the entity choices
        raise ValueError(f"no contract model for entity {entity!r}")
    from pydantic import ValidationError
    try:
        Model.model_validate(doc)
        return []
    except ValidationError as exc:
        return [
            _finding("contract-model", "error",
                     "/" + "/".join(str(p) for p in err["loc"]), err["msg"])
            for err in exc.errors()
        ]


def _endpoint_findings(doc, document_path: Path) -> list[dict]:
    from analitiq.validator import validate_document
    return validate_document(doc, doc_path=document_path.resolve())


def _type_map_findings(entity: str, doc, document_path: Path) -> list[dict]:
    """Validate a connection-scoped type-map file. The filename gate runs first
    and alone on a mismatch: the published validator derives rule direction from
    the filename, so validating a misnamed file's content could grade it in the
    wrong direction (an unknown filename defaults to read) and bury the one
    actionable finding (rename it) in noise. A non-list document is likewise
    gated here — the published dispatch detects by *shape*, so a stray dict
    under a type-map filename would be graded as some other artifact (a
    connection document would even pass clean) instead of failing as the
    non-array the engine's loader will choke on."""
    expected = _TYPE_MAP_FILENAMES[entity]
    if document_path.name != expected:
        return [_finding(
            "connection-type-map", "error", "",
            f"file is named {document_path.name!r} but entity {entity!r} requires "
            f"{expected!r} — the engine loads each direction only from its exact "
            f"filename (connections/<slug>/definition/{expected}).")]
    if not isinstance(doc, list):
        return [_finding(
            "connection-type-map", "error", "",
            f"{expected} must be a top-level JSON array of rules, got "
            f"{type(doc).__name__}.")]
    from analitiq.validator import validate_document
    # Resolve the parent but keep the authored basename: the published validator
    # derives direction from `doc_path.name`, and a full resolve() would follow a
    # symlinked map to a differently-named target and silently re-grade it.
    findings = validate_document(doc, doc_path=document_path.parent.resolve() / document_path.name)
    if entity == "type_map_write":
        # The published write-vocabulary coverage warning presumes a CONNECTOR
        # write map, which must cover the full canonical vocabulary. A connection
        # map is gap-only by rule (spec-type-map-gaps.md) — the warning would fire
        # on every authored connection write map forever, and its remedy ("add
        # rules") is exactly the shadowing the gap-only rule forbids. Filtering it
        # is the same adapter-adapts-published-behavior move as require_runnable.
        # Checked both ways: the currently-pinned release predates the `rule`
        # axis and still names this check `validator="type-map-write-coverage"`;
        # a release carrying the `rule` axis names it `rule="RULE-TMAP-017"`
        # instead and drops `validator` entirely.
        findings = [
            f for f in findings
            if f.get("validator") != "type-map-write-coverage"
            and f.get("rule") != "RULE-TMAP-017"
        ]
    return findings


def _connection_type_map_findings(conn_dir: Path, findings: list[dict]) -> None:
    """Validate the connection-scoped type maps beside one connection.json —
    file-level checks the published bundle validator structurally cannot make
    (it receives assembled documents, never the connection's directory). A
    present map is validated in full via the published validator; the dead
    pre-split filename is rejected with a migration finding, mirroring the
    published connector-side check.

    Appends directly to the caller's shared `findings` list rather than
    building a local one to return: the legacy check and each type-map
    direction are independently-decidable units, each in its own `_contained`
    guard, so a crash in one costs only its own finding — never a list of
    already-decided results a crash partway through would otherwise discard
    before this function got the chance to return it."""
    definition = conn_dir / "definition"
    site = f"connections/{conn_dir.name}/definition"
    legacy = definition / _LEGACY_TYPE_MAP_FILENAME
    with _contained(findings, f"{site}/{_LEGACY_TYPE_MAP_FILENAME}"):
        if legacy.exists() or legacy.is_symlink():
            findings.append(_finding(
                "connection-type-map", "error", f"{site}/{_LEGACY_TYPE_MAP_FILENAME}",
                f"{_LEGACY_TYPE_MAP_FILENAME} is the pre-split filename; the engine never "
                "reads it. Split it into type-map-read.json (native → Arrow) and, for the "
                "write direction, type-map-write.json (Arrow → native)."))
    for entity, fname in _TYPE_MAP_FILENAMES.items():
        path = definition / fname
        with _contained(findings, f"{site}/{fname}"):
            if not (path.exists() or path.is_symlink()):
                continue
            if not path.is_file():
                # A directory or dangling symlink under a load-bearing name would
                # pass silently here and fail at the engine's loader — the most
                # expensive place to find out.
                findings.append(_finding(
                    "connection-type-map", "error", f"{site}/{fname}",
                    f"{fname} exists but is not a readable file (directory or dangling "
                    "symlink); the engine's loader will fail to open it."))
                continue
            try:
                doc = _read_json(path)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                findings.append(_finding("connection-type-map", "error", f"{site}/{fname}",
                                         f"Cannot read {fname}: {exc}"))
                continue
            findings.extend({**f, "path": f"{site}/{fname}{f.get('path', '')}"}
                            for f in _type_map_findings(entity, doc, path))


def _read_bundle_member(path: Path, findings: list[dict]) -> dict | None:
    """Read one sibling bundle document. On an unreadable/invalid file or a
    non-object payload, append an error finding and return None — so a malformed
    sibling becomes a clear diagnostic instead of an uncaught traceback or a
    silently dropped document."""
    try:
        doc = _read_json(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
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
    # The engine locates a connection-scoped endpoint by its filename stem, so a file
    # named other than <endpoint_id>.json won't resolve at runtime. validate_document
    # gates this for a stem-addressed file, but validate_pipeline_bundle takes a
    # filename-less dict — so run the published gate here, where the names are known.
    from analitiq.validator import endpoint_filename_findings

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
                        # Endpoint documents omit connection_id (server-managed); supply the
                        # owning connection's id so the bundle's endpoint-ref check can resolve
                        # connection-scoped references.
                        endpoint.setdefault("connection_id", connection_id)
                        endpoint.setdefault("scope", "connection")
                        endpoints.append(endpoint)
                        # files here are stem-addressed by construction (globbed from
                        # definition/endpoints/), so the published filename gate applies
                        # directly. The gate is its own guard too, run LAST: the
                        # endpoint is already in the bundle by this point, so a crash
                        # here costs only this one finding, not the bundle's completeness.
                        with _contained(findings, ep_site):
                            findings.extend(endpoint_filename_findings(endpoint, ep_json.name))
            if outcome.crashed:
                crashed = True
            if outcome.crashed or conn is None:
                complete = False
            # Connection-scoped type maps are files the engine loads beside the
            # connection, invisible to the assembled-document bundle, and depend
            # only on conn_json.parent — never on whether connection.json itself
            # parsed — so they are checked unconditionally: a crash inside is
            # contained per-direction by _connection_type_map_findings itself, so
            # this outer guard is a backstop, never costs the bundle's completeness
            # (which would otherwise misreport a live connection as unresolved),
            # and a genuinely malformed or legacy type-map file is still reported
            # even when connection.json itself is unreadable.
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
                except (OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
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
                    except (OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                        eid = None
                    if isinstance(eid, str) and eid:
                        ids.add(eid)
                if ids:
                    keys = {slug_dir.name}
                    try:
                        cid = _read_json(slug_dir / "definition" / "connector.json").get("connector_id")
                    except (OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
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
            findings.append(_finding(
                "connector-endpoint-ref", "warning", path,
                f"endpoint_id {eid!r} is not among connector {connector!r}'s published "
                f"endpoints {available}.{hint} Align the stream's endpoint_ref to the "
                f"connector's endpoint name; the plugin never edits the connector.",
            ))


def _bundle_findings(pipeline_doc: dict, document_path: Path, root: Path) -> list[dict]:
    from analitiq.validator import validate_pipeline_bundle
    bundle, findings, complete, crashed = _assemble_bundle(pipeline_doc, document_path, root)
    # This plugin authors draft bundles by design: a draft pipeline is not yet
    # runnable, so its runnability verdicts are an author-time expectation, not a
    # defect. Ask the bundle validator for referential integrity only
    # (require_runnable=False) while the pipeline is a draft, and enforce runnability
    # once it is authored 'active'. Every referential finding stays blocking either
    # way. A non-dict pipeline_doc already earned its own contract-model finding at
    # the single-document stage (see diagnostics_for) — treat it as not-yet-active
    # here rather than raising and discarding what _assemble_bundle just decided.
    require_runnable = isinstance(pipeline_doc, dict) and pipeline_doc.get("status") == "active"
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
    try:
        doc = _read_json(document_path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _diagnostics([_finding("document", "error", "", f"Cannot read document: {exc}")])

    if entity == "database_endpoint":
        findings = _endpoint_findings(doc, document_path)
    elif entity in _TYPE_MAP_FILENAMES:
        findings = _type_map_findings(entity, doc, document_path)
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
    parser.add_argument("--entity", required=True, choices=ENTITIES,
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
        print(json.dumps(_diagnostics([_crash_finding("", exc)]), indent=2))
        return 1

    print(output)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
