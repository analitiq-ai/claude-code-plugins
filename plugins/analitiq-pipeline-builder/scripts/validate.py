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

Every entity is graded by ``analitiq.validator.validate_document`` as the kind
``--entity`` names — the same code the ``analitiq-validate`` CLI runs. A
``pipeline`` with ``--bundle-root`` is additionally graded as a bundle by
``analitiq.validator.validate_pipeline_bundle``, for the cross-document
referential integrity no single document can verify. A draft bundle passes
``require_runnable=False`` (a not-yet-runnable draft is not an authoring
error); an ``active`` pipeline is held to full runnability. The bundle is read
the way each published package locates its documents: every stream the
pipeline's package holds is graded as a stream, and every connection directory
is graded as a connection package by ``analitiq.validator.validate_package_at``.

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


def _parsed(text: str | Exception) -> tuple[object, Exception | None]:
    """The document `read_package` read as `text`, or the error that kept it
    from being one."""
    from analitiq.validator._core import _JSON_TEXT_REFUSALS
    if isinstance(text, Exception):
        return None, text
    try:
        return json.loads(text), None
    except _JSON_TEXT_REFUSALS as exc:
        return None, exc


def _assemble_bundle(pipeline_doc: dict, document_path: Path,
                     root: Path) -> tuple[dict, list[dict], bool, bool]:
    """Gather the on-disk pipeline bundle the way the engine resolves it at load:
    the pipeline plus the streams its package holds, every connection package,
    the connection-scoped endpoint documents (stamped with their owning
    connection's id, which endpoint documents do not carry themselves), and the
    downloaded connector identities. Returns the bundle, the findings grading
    its members, whether every member on disk actually made it into the bundle,
    and whether a containment guard is the reason any didn't — a crash or read
    error that excludes a member leaves the published bundle validator unable
    to tell "genuinely missing" from "excluded here", so a caller must know
    before trusting its referential verdicts, and separately must know whether
    that exclusion came from an actual crash (worth its own labeled finding) or
    an already-reported ordinary read error (which needs no second, misleading
    one)."""
    from analitiq.contracts.connection_package import ConnectionPackage
    from analitiq.contracts.pipeline_package import PipelinePackage
    from analitiq.validator import read_package, validate_document, validate_package_at
    from analitiq.validator._core import _unreadable_document_finding
    findings: list[dict] = []
    complete = True
    crashed = False

    # Each section below is wrapped in its own outer guard too, not just each
    # item within it: reading the directory materializes the whole listing
    # before the loop even starts, so a filesystem failure enumerating it (a
    # vanished directory, a permission error) would otherwise escape every
    # per-item guard and abort this whole function before it could return
    # what earlier sections already decided.

    streams: list[dict] = []
    with _contained(findings, "streams") as section:
        texts = read_package(document_path.parent, "pipeline-package")
        for key in sorted(k for k in texts if PipelinePackage.kind_at(k) == "stream"):
            # One stream is one independently-decidable unit, same as one
            # connection or one connector below: a crash grading it (e.g. a
            # pathologically deep document) must not discard the streams
            # already appended above.
            doc, error = _parsed(texts[key])
            with _contained(findings, key):
                if error is not None:
                    findings.extend(_at_site(key, [_unreadable_document_finding(error)]))
                else:
                    findings.extend(_at_site(key, validate_document(doc, "stream")))
            # Whatever grading did: the referential checks read a stream's
            # refs, never its shape, so a crash grading it must not cost it its
            # place in the bundle.
            if isinstance(doc, dict):
                streams.append(doc)
            else:
                complete = False
    if section.crashed:
        crashed = True
        complete = False

    connections: list[dict] = []
    endpoints: list[dict] = []
    with _contained(findings, "connections") as section:
        for conn_dir in sorted(d for d in (root / "connections").glob("*")
                               if (d / ConnectionPackage.ROOT).is_file()):
            site = f"connections/{conn_dir.name}"
            # One connection is one independently-decidable unit: a directory
            # it cannot be read from must not discard what was decided for
            # connections read earlier in this same loop.
            with _contained(findings, site) as outcome:
                texts = read_package(conn_dir, "connection-package")
            if outcome.crashed:
                crashed = True
                complete = False
                continue
            # Grading is its own unit too: a crash grading a connection package
            # must not take the connection — and every endpoint under it — out
            # of the bundle the referential pass reads. An unreadable member
            # needs no finding below; grading reports it.
            with _contained(findings, site):
                findings.extend(_at_site(f"{site}/{ConnectionPackage.ROOT}",
                                         validate_package_at(conn_dir, "connection-package")["findings"]))
            conn, _ = _parsed(texts[ConnectionPackage.ROOT])
            if not isinstance(conn, dict):
                complete = False
                continue
            connections.append(conn)
            for key in sorted(k for k in texts if ConnectionPackage.kind_at(k) == "database-endpoint"):
                endpoint, _ = _parsed(texts[key])
                if not isinstance(endpoint, dict):
                    complete = False
                    continue
                # Endpoint documents omit connection_id (server-managed); supply the
                # owning connection's id so the bundle's endpoint-ref check can resolve
                # connection-scoped references.
                endpoint.setdefault("connection_id", conn.get("connection_id"))
                endpoint.setdefault("scope", "connection")
                endpoints.append(endpoint)
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
        for connector_dir in _connector_dirs(root):
            connectors.add(connector_dir.name)  # directory slug
            with _contained(findings, f"connectors/{connector_dir.name}") as outcome:
                cid = _connector_id(connector_dir)
                if cid:
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


def _connector_dirs(root: Path) -> list[Path]:
    """Every downloaded connector package under `root`: a directory holding a
    connector package's root document."""
    from analitiq.contracts.connector_package import ConnectorPackage
    return sorted(d for d in (root / "connectors").glob("*")
                  if (d / ConnectorPackage.ROOT).is_file())


def _connector_id(connector_dir: Path) -> str | None:
    """The `connector_id` a downloaded connector's root document declares, or
    `None` when it declares none it can be read for."""
    from analitiq.contracts.connector_package import ConnectorPackage
    from analitiq.validator._core import _JSON_READ_ERRORS
    try:
        cid = _read_json(connector_dir / ConnectorPackage.ROOT).get("connector_id")
    except (*_JSON_READ_ERRORS, AttributeError):
        return None
    return cid if isinstance(cid, str) and cid else None


def _connector_endpoint_sets(root: Path, findings: list[dict]) -> dict[str, set[str]]:
    """Map each downloaded connector — by directory slug **and** its authored
    `connector_id` — to the set of endpoint ids it publishes on disk (each
    endpoint document its package holds contributes both its filename stem and
    its `endpoint_id` field, which the connector's own filename gate keeps
    equal for well-formed registry connectors — this adapter records both to
    stay correct even if a malformed connector let them diverge).

    A connector whose package holds no endpoint document is **omitted**, not
    recorded as an empty set: its endpoint set is *unknown* here (the plugin
    may not have downloaded endpoints for it), and an unknown set must not read
    as "no endpoints", which would warn on every ref. Callers treat a missing
    key as "cannot verify — skip" — the same treatment a crash reading one
    connector's endpoints gets here (contained per connector, so it costs only
    that connector's set, never every other connector's).

    The enumeration itself is wrapped in its own outer guard too: a filesystem
    failure there would otherwise escape every per-connector guard below and
    return nothing at all, rather than whatever connectors were already found
    before it."""
    from analitiq.contracts.connector_package import ConnectorPackage
    from analitiq.validator import read_package
    sets: dict[str, set[str]] = {}
    with _contained(findings, "connectors"):
        for connector_dir in _connector_dirs(root):
            with _contained(findings, f"connectors/{connector_dir.name}"):
                texts = read_package(connector_dir, "connector-package")
                ids: set[str] = set()
                for key in sorted(k for k in texts
                                  if ConnectorPackage.kind_at(k) == "api-endpoint"):
                    ids.add(PurePosixPath(key).stem)
                    endpoint, _ = _parsed(texts[key])
                    eid = endpoint.get("endpoint_id") if isinstance(endpoint, dict) else None
                    if isinstance(eid, str) and eid:
                        ids.add(eid)
                if ids:
                    for key in {connector_dir.name, _connector_id(connector_dir)} - {None}:
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
    """This plugin authors draft bundles by design: a draft pipeline is not yet
    runnable, so its runnability verdicts are an author-time expectation, not a
    defect. Ask the bundle validator for referential integrity only
    (require_runnable=False) while the pipeline is a draft, and enforce runnability
    once it is authored 'active'. A non-dict pipeline_doc already earned its own
    contract-model finding at the single-document stage (see diagnostics_for) —
    treat it as not-yet-active here rather than raising.

    Public (no leading underscore): `scripts/gen_pipeline_docs.py` calls this
    directly to measure which `require_runnable`-gated rules this adapter can
    actually surface, rather than reasoning about the gate from outside it."""
    return isinstance(pipeline_doc, dict) and pipeline_doc.get("status") == "active"


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
    from analitiq.validator import validate_document
    from analitiq.validator._core import _JSON_READ_ERRORS, _unreadable_document_finding
    try:
        doc = _read_json(document_path)
    except _JSON_READ_ERRORS as exc:
        return _diagnostics([_unreadable_document_finding(exc)])

    findings = validate_document(doc, entity)
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
