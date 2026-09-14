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

This module is the path-free form of that contract: a `DocumentSet` of
already-loaded content, keyed by the relative path each document would have
occupied on disk, in and out. It reuses every existing path-anchored check
unchanged, by handing it a `VirtualPath` (`._virtual_fs`) backed by an
in-memory `_VirtualFS` instead of a real filesystem path — the same duck-typed
`.parent` / `.is_file()` / `.read_text()` / `.rglob("*.json")` surface those
checks already call.
"""
from __future__ import annotations

import difflib
import json
import re
from typing import Any, Literal, TypedDict, Union

from pydantic import ValidationError

from ._core import _KIND_REGISTRY, finding, finding_costs_a_pass, validate_document
from ._virtual_fs import VirtualPath, _VirtualFS
from .connectors import (
    _LEGACY_MAP_FILENAME,
    _READ_MAP_ADAPTER,
    _READ_MAP_FILENAME,
    _WRITE_MAP_ADAPTER,
    _WRITE_MAP_FILENAME,
    _first_match_render,
    _render_arrow_type,
)
from .pipelines import _base_id, _iter_endpoint_refs, validate_pipeline_bundle

#: The value half of a `DocumentSet` entry: text, raw bytes (decoded as UTF-8
#: with a BOM stripped if present — no encoding-guessing), or a value already
#: parsed into the `dict`/`list` shape `analitiq.validator.validate_document`
#: itself expects for `doc`. A value that is none of these is reported as an
#: `invalid-value` finding on its own key, never raised.
DocumentSetValue = Union[str, bytes, dict, list]

#: A path-free bundle of documents: POSIX-relative-path-shaped string keys
#: (e.g. `"connections/foo/connection.json"`, `"endpoints/widgets.json"`) mapped
#: to already-loaded content. A leading `./` is normalized away; an absolute
#: key, a key containing a `.` or `..` segment, or the empty string is
#: reported as an `invalid-key` finding rather than raised. Every function
#: below that takes or produces a document set uses this one shape, so
#: `validate_tree`'s `documents` and `resolve_type_map_gaps`'s `maps` share one
#: value type rather than two independently-typed mappings.
DocumentSet = dict[str, DocumentSetValue]

#: This API's document-kind vocabulary — a literal tuple, not a runtime import
#: of `DOCUMENT_ARTIFACT_KINDS`: a `Literal`'s members must be spelled out for
#: the type checker, which a name resolved at import time is not. Mirrors
#: `plugins/analitiq-pipeline-builder/scripts/validate.py`'s `PIPELINE_ENTITIES`,
#: which is a hand-spelled tuple for a different reason — it must not import
#: `DOCUMENT_ARTIFACT_KINDS` at module level, since argparse evaluates its
#: `--entity` `choices=` before that module's own self-install bootstrap runs
#: — and is pinned the same way against drift.
#: `test_document_set.py::test_entity_matches_document_artifact_kinds`
#: pins this tuple to `DOCUMENT_ARTIFACT_KINDS` so the two cannot drift apart
#: member by member.
Entity = Literal[
    "connector",
    "api-endpoint",
    "database-endpoint",
    "type-map",
    "stream",
    "pipeline",
    "connection",
]


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
    `analitiq.validator.finding` already constructs, plus `direction`, which
    only `resolve_type_map_gaps` below sets (see its docstring for which
    finding kinds carry it). Restated here only so this module's signatures
    are checkable; the shape itself stays owned by `finding()`, and
    `test_document_set.py::
    test_finding_matches_the_keys_finding_builder_produces` pins both the
    restated keys and which of them are required to what `finding()`
    actually produces."""

    rule: str
    severity: Literal["error", "warning"]
    direction: Literal["read", "write"]


class ValidationEnvelope(TypedDict):
    """The result of validating a whole document set: flat, not a nested
    per-document breakdown — each finding's own `path` carries its file key, so
    a document validated this way and the same document validated through the
    existing path-based single-document route report through one shape."""

    passed: bool
    findings: list[Finding]


class FindingsEnvelope(TypedDict):
    """The `{"findings"}`-only result `resolve_type_map_gaps` returns. No
    `passed`: resolving a type map's gaps is not itself a pass/fail verdict on
    a document — a caller wanting one runs the returned findings through
    `analitiq.validator.finding_costs_a_pass`, the same reduction `passed` in
    `ValidationEnvelope` is built from."""

    findings: list[Finding]


# ---------------------------------------------------------------------------
# Key/value normalization — shared by every function that walks a DocumentSet.
# ---------------------------------------------------------------------------

def _normalize_key(raw_key: Any) -> str | None:
    """The relative-path shape a `DocumentSet` key must have, or `None` when it
    is an absolute path, contains a `.` or `..` segment, or is the empty
    string — a leading `./` (however many) is stripped first rather than
    rejected. Rejecting every internal `.` segment (not just a leading one)
    matters beyond tidiness: `"foo/./bar.json"` and `"foo/bar.json"` would
    otherwise coexist as distinct keys denoting the same path, silently
    escaping the `key-path-conflict` check that exists to catch exactly a
    case like it."""
    if not isinstance(raw_key, str):
        return None
    key = raw_key
    while key.startswith("./"):
        key = key[2:]
    if key == "" or key.startswith("/"):
        return None
    parts = key.split("/")
    if "" in parts or "." in parts or ".." in parts:
        return None
    return key


def _key_and_value_findings(raw_key: Any, value: Any) -> tuple[str | None, list[dict]]:
    """Validate one `DocumentSet` entry's key and value shape — shared by every
    caller that walks a `DocumentSet` (`_normalize_documents`,
    `resolve_type_map_gaps`) so they never grade a malformed entry
    differently. Returns the normalized key, or `None` plus the one finding
    (`invalid-key` or `invalid-value`) naming why the entry could not be used;
    the raw key is what an `invalid-key` finding's `path` names, since the
    normalized form does not exist for it."""
    key = _normalize_key(raw_key)
    if key is None:
        return None, [finding(
            message_id="invalid-key", kind="fail", path=raw_key,
            message=(
                f"key {raw_key!r} is not a usable relative document path: it must "
                "be a non-empty relative path (no leading '/') with no '.' or "
                "'..' path segment."))]
    if not isinstance(value, (str, bytes, dict, list)):
        return None, [finding(
            message_id="invalid-value", kind="fail", path=key,
            message=(
                f"value for {key!r} is a {type(value).__name__}, not one of "
                "str / bytes / dict / list."))]
    return key, []


def _normalize_documents(documents: DocumentSet) -> tuple[_VirtualFS, list[dict]]:
    """Normalize a raw `DocumentSet` into a `_VirtualFS` plus the findings the
    normalization pass itself produces — `invalid-key`/`invalid-value` (via
    `_key_and_value_findings`), `internal-error` for a value whose
    materialization into text crashed (a cyclic structure, say),
    `key-path-conflict` for a key that is simultaneously a document and a
    directory prefix of another key, and `duplicate-key` for two distinct raw
    keys that normalize to the same key. None of these describes a document's
    own content, so none names a rule (`rules/SCHEMA.md`'s "no detector
    recognised the document" case, generalized one level up to the document
    set's own key/value shape).

    Keys are processed in sorted order (by string form, so a raw key of any
    type still sorts rather than crashing `_key_and_value_findings` never got
    to grade), not the caller's own insertion order, so this pass's own
    findings never depend on how the caller happened to build the mapping —
    the same guarantee `check_coverage`'s own `sorted(...)` endpoint scan
    already gives the checks built on top of it.
    """
    findings: list[dict] = []
    texts: dict[str, str] = {}
    objects: dict[str, Any] = {}
    known_keys: set[str] = set()
    for raw_key in sorted(documents, key=str):
        value = documents[raw_key]
        key, kv_findings = _key_and_value_findings(raw_key, value)
        if key is None:
            findings.extend(kv_findings)
            continue
        if key in known_keys:
            findings.append(finding(
                message_id="duplicate-key", kind="fail", path=key,
                message=(
                    f"{raw_key!r} normalizes to {key!r}, which another key in "
                    "this document set already denotes; two keys for one path "
                    "leave which document is validated undefined.")))
            continue
        known_keys.add(key)
        try:
            if isinstance(value, bytes):
                text = value.decode("utf-8-sig")
            elif isinstance(value, str):
                text = value
            else:
                text = json.dumps(value)
        except Exception as exc:  # noqa: BLE001 - isolate one key's crash
            findings.append(finding(
                message_id="internal-error", kind="fail", path=key,
                message=(
                    f"{key!r} could not be materialized into JSON text "
                    f"({type(exc).__name__}: {exc}).")))
            continue
        texts[key] = text
        if isinstance(value, (dict, list)):
            objects[key] = value

    for key in sorted(known_keys):
        if any(other.startswith(f"{key}/") for other in known_keys):
            findings.append(finding(
                message_id="key-path-conflict", kind="fail", path=key,
                message=(
                    f"{key!r} is both a document key and a directory prefix of "
                    "another key in this document set.")))

    return _VirtualFS(texts, objects, known_keys), findings


def _at_site(site: str, findings: list[dict]) -> list[dict]:
    """Re-root a member's own findings at the site (key or key prefix) they
    came from.

    A finding `finding()` built for a document validated on its own carries a
    leading-slash pointer into that document (`/`, `/endpoint_id`); a finding
    this module builds directly for one of its own `DocumentSet` keys
    (`internal-error`, `invalid-key`, ...) already carries a bare relative key.
    These conventions can appear together in the same findings list being
    rerooted here — an embedded connector subtree's own findings mix them —
    so each is joined the way that makes it a path under `site` rather than a
    sibling of it. A bare `/` is the whole-document case of the leading-slash
    convention and joins to `site` itself, not `site` plus a trailing
    slash."""
    def _joined(path: str) -> str:
        if path == "/":
            return site
        if path.startswith("/"):
            return f"{site}{path}"
        if path:
            return f"{site}/{path}"
        return site
    return [{**f, "path": _joined(f["path"])} for f in findings]


def _validate_tree_document(fs: _VirtualFS, key: str, *, entity: str | None = None) -> tuple[Any, list[dict]]:
    """Parse `key`'s content and validate it via the single-document route,
    isolating a materialization or parse crash to this one key. Returns
    `(doc, findings)` — `doc` is `None` when `key` never materialized or when
    parsing it crashed, so a caller that also needs the parsed value for
    bundle assembly reads it off this return instead of parsing `key` a
    second time, and can call this function unconditionally rather than
    pre-guarding with `fs.materialized(key)` itself.

    A key that never materialized produces no NEW finding here —
    `_normalize_documents` already reported that crash (`internal-error`,
    `path=key`) when it first tried to turn the value into text, and a second
    finding for the same crash would only double-report it. A key that
    materializes but fails to parse as JSON gets its own `internal-error`
    here instead, since nothing upstream could have caught that failure.

    `entity`, when given, is passed straight through to `validate_document`'s
    own explicit-kind override, for a key whose kind this module already
    knows from where it sits in the tree (a connection-scoped type-map file,
    say) rather than from the document's own shape.

    `analitiq.validator.validate_document` already contains a dispatch-time
    crash (`check-crashed`, `notApplicable`) once a document is in hand, but
    turning a `DocumentSet` entry's raw text into that document happens before
    dispatch ever runs, so a crash there needs its own containment —
    `internal-error` (`fail`/`error`), scoped to this key rather than the
    framework's generic no-rule case, and distinct from `check-crashed`.

    The parse-crash finding's own `path` is `"/"` — the same whole-document
    pointer convention `validate_document` itself uses (e.g.
    `unrecognized-document`) — never `key`: every caller re-roots this
    function's findings at a site with `_at_site`, which already turns a
    leading-slash path into `site` + that path; a bare `key` here would
    instead be appended AGAIN as a relative suffix, double-prefixing the
    finding's path.
    """
    if not fs.materialized(key):
        return None, []
    try:
        doc = fs.parsed(key)
    except Exception as exc:  # noqa: BLE001 - isolate one key's crash
        return None, [finding(
            message_id="internal-error", kind="fail", path="/",
            message=f"{key!r} could not be parsed as JSON ({type(exc).__name__}: {exc}).")]
    return doc, validate_document(doc, doc_path=VirtualPath(fs, key), entity=entity)


def _resolved_member(
        fs: _VirtualFS, key: str, *, entity: str | None = None, validate: bool = True,
) -> tuple[dict | None, list[dict]]:
    """The one gate a document-set member — the pipeline document, a stream,
    a connection, a connection- or connector-scoped endpoint, an embedded
    connector's own identity document — must clear to be usable for
    referential checking: `key` must materialize, parse without crashing,
    and — every member this gate is used for is expected to be an object —
    be a `dict`. Returns `(None, findings)` on any of those four ways to fail
    (missing key, materialization crash, parse crash, wrong shape); a caller
    collapses all four into its own single "this member is excluded" signal
    rather than re-deriving which one happened.

    `validate=True` (the default) resolves `key` through
    `_validate_tree_document`, so a member this call is itself the one place
    that checks its content also earns its model-validation findings.
    `validate=False` resolves it with the same materialize/parse isolation
    but skips `validate_document` entirely, for two different reasons
    depending on the caller:

    - The connector-scoped endpoint lookup in `_connector_endpoint_sets`
      keeps this function's returned findings, so its reason has to hold on
      its own: running `validate_document` there would either double-report
      an API-kind connector's endpoint (already validated by the
      sibling-endpoint scan `check_coverage` runs over it) or grade a
      database/storage-kind connector's endpoint against a model shape
      nothing else in this call's scope checks — `_connector_endpoint_sets`'s
      own docstring carries the full argument.
    - The two embedded-connector identity lookups (`_connector_endpoint_sets`
      pulling a connector's own `connector_id`, and `validate_pipeline_tree`
      doing the same) discard this function's returned findings outright
      (`connector_doc, _ = _resolved_member(...)`), so nothing either call
      does could ever double-report regardless of `validate`. Their reason is
      simpler: the same `connector.json` this call would re-validate is
      already validated (or its crash already reported) by the recursive
      `validate_connector_tree` call over that connector's own subtree, so
      validating it again here would only be redundant work whose result is
      thrown away immediately.

    A materialization crash is never reported by this function either way:
    `_normalize_documents` already named it.
    """
    if validate:
        doc, findings = _validate_tree_document(fs, key, entity=entity)
    elif not fs.materialized(key):
        doc, findings = None, []
    else:
        try:
            doc, findings = fs.parsed(key), []
        except Exception as exc:  # noqa: BLE001 - isolate one key's crash
            doc = None
            findings = [finding(
                message_id="internal-error", kind="fail", path="/",
                message=f"{key!r} could not be parsed as JSON ({type(exc).__name__}: {exc}).")]
    if not isinstance(doc, dict):
        return None, findings
    return doc, findings


def _extract_subtree(documents: DocumentSet, prefix: str) -> DocumentSet:
    """Re-relativize an embedded package's own documents out of the whole
    tree's raw `documents` — not out of an already-normalized `_VirtualFS` —
    so a value whose materialization crashes is handed to the recursive
    `validate_connector_tree` call exactly as the caller supplied it, letting
    that call discover and report the crash itself, scoped to its own
    subtree-relative key rather than being reported (or silently dropped)
    here."""
    subtree: DocumentSet = {}
    for raw_key, value in documents.items():
        key = _normalize_key(raw_key)
        if key is None or not key.startswith(prefix):
            continue
        subtree[key[len(prefix):]] = value
    return subtree


def _envelope(findings: list[dict]) -> ValidationEnvelope:
    return {"passed": not any(finding_costs_a_pass(f) for f in findings), "findings": findings}


# ---------------------------------------------------------------------------
# Package-kind entry points.
# ---------------------------------------------------------------------------

def validate_connector_tree(documents: DocumentSet) -> ValidationEnvelope:
    """Validate a connector package supplied as an in-memory `DocumentSet`
    instead of files on disk: the connector document, its sibling type maps,
    and its `endpoints/*.json` files, cross-checked the way
    `analitiq.validator.check_coverage` already does from a filesystem path.

    This is the connector package-kind's root-shape entry, called directly —
    it never walks the registry `validate_tree` walks, so it can never itself
    report an `ambiguous-layout` or `unrecognized-layout` finding.

    A `VirtualPath` backed by the normalized `DocumentSet` stands in for the
    real `doc_path` `check_coverage` reads sibling type-maps and endpoints
    through, so that check runs unchanged; a root `connector.json` whose own
    content crashed during normalization is reported by that pass alone
    (`internal-error`), and is never handed to the single-document route on
    top of it. A document set with no `connector.json` key at all — not even
    one that crashed — is not a connector package missing nothing to report;
    it is reported as `missing-connector-document` rather than silently
    passing on whatever findings (often none) `_normalize_documents` happened
    to produce.
    """
    fs, findings = _normalize_documents(documents)
    if "connector.json" not in fs.known_keys:
        findings.append(finding(
            message_id="missing-connector-document", kind="fail", path="connector.json",
            message="this document set has no root connector.json; a connector package must ship one."))
    else:
        _, doc_findings = _validate_tree_document(fs, "connector.json")
        findings.extend(doc_findings)
    return _envelope(findings)


_CONNECTOR_SUBTREE_RE = re.compile(r"^connectors/([^/]+)/definition/")
_PIPELINE_DOC_RE = re.compile(r"^pipelines/([^/]+)/pipeline\.json$")
_CONNECTION_DOC_RE = re.compile(r"^connections/([^/]+)/connection\.json$")


def _connection_type_map_findings(fs: _VirtualFS, slug: str) -> list[dict]:
    """RULE-CONN-012: a connection's own scoped type maps, checked the way a
    connector's sibling type maps already are — the pre-split filename is
    rejected outright, and each present direction is validated via the
    single-document route (`_validate_tree_document`, entity-pinned to
    `"type-map"` — the direction is already known from which of the two
    filenames this loop is on, so shape auto-detection is bypassed rather
    than relied on to notice a stray non-list value), so a parse crash here is
    contained by that same mechanism rather than needing its own.

    The write direction's coverage warning (RULE-TMAP-017) is filtered here
    the same way it is beside a connector: that warning presumes a connector's
    write map, which must cover the canonical vocabulary in full, while a
    connection's own write map is gap-only by rule and would otherwise be
    warned at for every connection ever authored.
    """
    site = f"connections/{slug}/definition"
    findings: list[dict] = []
    legacy_key = f"{site}/{_LEGACY_MAP_FILENAME}"
    if legacy_key in fs.known_keys:
        findings.append(finding(
            rule="RULE-CONN-012",
            message_id="legacy-type-map-filename", kind="fail", path=legacy_key,
            message=(
                f"sibling {_LEGACY_MAP_FILENAME} is the pre-split name; rename the "
                f"read direction to {_READ_MAP_FILENAME} (and add {_WRITE_MAP_FILENAME} "
                "for a connection whose connector kind renders a write direction).")))
    for direction, filename in (("read", _READ_MAP_FILENAME), ("write", _WRITE_MAP_FILENAME)):
        key = f"{site}/{filename}"
        _, doc_findings = _validate_tree_document(fs, key, entity="type-map")
        if direction == "write":
            doc_findings = [f for f in doc_findings if f.get("rule") != "RULE-TMAP-017"]
        findings.extend(_at_site(key, doc_findings))
    return findings


def _connector_endpoint_sets(
        fs: _VirtualFS, connector_slugs: list[str]) -> tuple[dict[str, set[str]], list[dict]]:
    """Map each embedded connector — by its subtree slug and its own
    `connector_id` — to the endpoint ids it publishes, mirroring the plugin's
    own `_connector_endpoint_sets`. Returns `(sets, findings)`: a connector
    whose `definition/endpoints/` holds no usable `*.json`, OR whose
    `definition/endpoints/` holds at least one KNOWN key (a file that
    genuinely exists, as opposed to an `endpoint_ref` simply naming an id no
    file ever claimed) that fails to materialize, parse, or shape correctly,
    is omitted from `sets` in full rather than recorded with a partial set —
    an incomplete set is exactly as unusable for the "does this connector
    publish this id" question as an empty one, since either could be hiding
    the very id a ref names. So its set reads as *unknown* rather than *no
    endpoints* or *these endpoints only* — a ref against it is skipped rather
    than warned at. A `*.json` that was never authored at all does not put its
    connector in this state: `fs.known_json_children` never enumerates a key
    that isn't there, so there is nothing for this loop to have failed to
    resolve, and the connector's set is still recorded — correctly missing
    that one id, the same as it would be missing any other id no file ever
    claimed.

    This is the only walk over a pipeline tree's embedded-connector
    `endpoints/*.json` files for connectors whose `kind` never routes
    `check_coverage` through them at all (`database`/`storage`), so a crash
    reading one (materialization or parse) is reported here (`internal-error`,
    parse-crash only — a materialization crash is `_normalize_documents`'s
    own to name) rather than swallowed, and a key that never resolves is
    excluded from `ids` outright — the only alternative would be treating a
    broken file's name as a published endpoint id regardless, which could
    wrongly resolve a stream's `endpoint_ref` against a document that never
    actually validated. `_resolved_member(..., validate=False)` is used
    rather than the full single-document route: nothing here validates a
    connector-scoped endpoint's own model shape (that is out of scope for
    the `kind`s this walk exists for), only whether it resolves to a `dict`
    an id can be read off."""
    sets: dict[str, set[str]] = {}
    findings: list[dict] = []
    for slug in connector_slugs:
        ep_prefix = f"connectors/{slug}/definition/endpoints/"
        ids: set[str] = set()
        slug_is_incomplete = False
        for key in fs.known_json_children(ep_prefix):
            ep_doc, ep_findings = _resolved_member(fs, key, validate=False)
            findings.extend(_at_site(key, ep_findings))
            if ep_doc is None:
                slug_is_incomplete = True
                continue
            suffix = key[len(ep_prefix):]
            ids.add(suffix[:-len(".json")])
            eid = ep_doc.get("endpoint_id")
            if isinstance(eid, str) and eid:
                ids.add(eid)
        if slug_is_incomplete or not ids:
            continue
        keys = {slug}
        connector_doc, _ = _resolved_member(
            fs, f"connectors/{slug}/definition/connector.json", validate=False)
        if connector_doc is not None:
            cid = connector_doc.get("connector_id")
            if isinstance(cid, str) and cid:
                keys.add(cid)
        for key in keys:
            sets[key] = ids
    return sets, findings


def _connector_endpoint_ref_findings(streams: list, connections: list,
                                     connector_endpoint_sets: dict[str, set[str]]) -> list[dict]:
    """RULE-STRM-043: every connector-scoped stream endpoint_ref names an
    endpoint its resolved connector actually publishes, mirroring the
    plugin's own `_check_connector_endpoint_refs` — a warning, with a
    closest-match alignment suggestion, never an error: a connector is a
    trusted, version-pinned registry artifact, so a stale ref is realigned
    rather than blocked on. Skipped when the connector's endpoint set is
    unknown (an unresolved connection, or a connector whose own endpoints
    could not be walked), so absence never reads as a false positive."""
    conn_to_connector: dict[str, str] = {}
    for conn in connections:
        if not isinstance(conn, dict):
            continue
        cid, connector = conn.get("connection_id"), conn.get("connector_id")
        if isinstance(cid, str) and isinstance(connector, str):
            conn_to_connector[_base_id(cid)] = connector

    findings: list[dict] = []
    for path, ref in _iter_endpoint_refs(streams):
        if ref.get("scope") != "connector":
            continue
        cid, eid = ref.get("connection_id"), ref.get("endpoint_id")
        if not (isinstance(cid, str) and cid and isinstance(eid, str) and eid):
            continue
        connector = conn_to_connector.get(_base_id(cid))
        if connector is None:
            continue
        endpoint_ids = connector_endpoint_sets.get(connector)
        if not endpoint_ids:
            continue
        if eid in endpoint_ids:
            continue
        available = sorted(endpoint_ids)
        case_match = next((e for e in available if e.lower() == eid.lower()), None)
        close = difflib.get_close_matches(eid, available, n=1, cutoff=0.6)
        suggestion = case_match or (close[0] if close else None)
        hint = f" Did you mean {suggestion!r}?" if suggestion else ""
        findings.append(finding(
            rule="RULE-STRM-043",
            message_id="connector-endpoint-ref-unresolved", kind="fail", path=path,
            message=(
                f"endpoint_id {eid!r} is not among connector {connector!r}'s published "
                f"endpoints {available}.{hint} Align the stream's endpoint_ref to the "
                "connector's endpoint name; the connector itself is not edited by this "
                "check.")))
    return findings


def validate_pipeline_tree(documents: DocumentSet) -> ValidationEnvelope:
    """Validate a pipeline bundle supplied as an in-memory `DocumentSet`
    instead of files on disk: the pipeline document, its sibling
    `streams/*.json`, and every `connections/*/connection.json` (plus their
    scoped endpoints and type maps) — assembled the way `plugins/
    analitiq-pipeline-builder/scripts/validate.py`'s `_assemble_bundle`
    already does from a filesystem root, then checked for referential
    integrity.

    Any `connectors/<slug>/definition/...` subtree is validated separately, by
    resolving it against the same package-kind registry `validate_tree`
    walks and scoping its findings' `path` under the subtree's key prefix —
    so an embedded connector's own coverage findings (native-type coverage,
    `transport_ref` resolution, duplicate endpoint ids) are reported. Today's
    plugin never resolves this subtree that way at all: `_assemble_bundle`
    itself reads only that subtree's `connector.json`, and only for its
    `connector_id` (the referential check that the identity is bundled); a
    separate function, `_connector_endpoint_sets`, reads the subtree's
    endpoint ids for stream-ref resolution — neither reports the subtree's
    own coverage findings the way this function must.

    This is the pipeline package-kind's root-shape entry, called directly — it
    never walks the registry, so it can never itself report an
    `ambiguous-layout` or `unrecognized-layout` finding.

    `require_runnable` for the bundle-referential pass is derived from the
    pipeline document's own `status` field (`status == "active"`), the same
    reading the plugin's `is_runnable_required` makes from a filesystem-loaded
    pipeline document — never a caller-supplied parameter, since the document
    itself already says whether it claims to be runnable.

    A materialization crash inside an embedded connector subtree is reported
    once, by that subtree's own recursive resolution, scoped under its own
    key prefix — not a second time by this function's own top-level
    normalization pass over the whole tree, which structurally cannot avoid
    walking those same keys (their presence is how a subtree is discovered at
    all).

    Every bundle member this function collects — the pipeline document, each
    stream, each connection, each connection-scoped endpoint, and each
    embedded connector's own identity (its `connector_id`, read for the
    referential check that it is bundled) — is resolved through one gate,
    `_resolved_member`: usable only when its key materializes, parses, and is
    a `dict`. When any bundle member is excluded for any of those reasons, the
    referential pass (`validate_pipeline_bundle`) is skipped entirely for this
    call, keeping only the `internal-error` finding(s) (if any — a
    materialization crash is already named by `_normalize_documents`, never
    reported twice) already collected for the member(s) responsible —
    mirroring `plugins/analitiq-pipeline-builder/scripts/validate.py`'s own
    `complete`/`crashed` gate: the published bundle validator has no way to
    tell "excluded here because it's broken" from "genuinely missing", so
    running it against a bundle this incomplete risks reporting a reference as
    broken that the missing/broken member, not the author, made unresolvable.
    A connection-scoped endpoint's own resolution does not depend on its
    parent connection resolving — the two are independently gated bundle
    members, sharing only the `conn_slug` this function derives from the
    connection's own KEY (never its content) to locate them — so a connection
    that fails to resolve still leaves its sibling endpoints and type maps
    checked on their own terms.
    `_connector_endpoint_ref_findings` (RULE-STRM-043) is a plugin-local aid
    rather than a referential check the published bundle validator owns, and
    runs regardless, the same way the plugin's own connector-endpoint-ref
    check does.
    """
    fs, findings = _normalize_documents(documents)

    connector_slugs = sorted({
        m.group(1) for key in fs.known_keys
        for m in (_CONNECTOR_SUBTREE_RE.match(key),) if m
    })
    _deduped_under_connector_subtrees = ("internal-error", "key-path-conflict", "invalid-value")
    findings = [
        f for f in findings
        if not (f["message_id"] in _deduped_under_connector_subtrees
                and any(f["path"].startswith(f"connectors/{slug}/definition/") for slug in connector_slugs))
    ]

    bundle_is_incomplete = False

    pipeline_doc: Any = None
    slug = None
    pipeline_keys = sorted(key for key in fs.known_keys if _PIPELINE_DOC_RE.match(key))
    if pipeline_keys:
        primary_key = pipeline_keys[0]
        slug = _PIPELINE_DOC_RE.match(primary_key).group(1)
        pipeline_doc, doc_findings = _resolved_member(fs, primary_key)
        findings.extend(doc_findings)
        if pipeline_doc is None:
            bundle_is_incomplete = True
        # A second (or further) pipelines/<slug>/pipeline.json is not silently
        # ignored: only `primary_key` (the first in sorted key order) becomes
        # this tree's pipeline, and each other match is named so its author
        # learns it was never validated as one, rather than reading no
        # finding as "this one passed too".
        for ignored_key in pipeline_keys[1:]:
            findings.append(finding(
                message_id="ignored-pipeline-document", kind="fail", path=ignored_key,
                message=(
                    f"{ignored_key!r} is a second pipelines/<slug>/pipeline.json in this "
                    f"document set; only {primary_key!r} (the first in sorted key order) is "
                    "validated as this tree's pipeline. Lay out one pipeline per document set.")))

    streams: list[Any] = []
    if slug is not None:
        stream_prefix = f"pipelines/{slug}/streams/"
        for key in fs.known_json_children(stream_prefix):
            suffix = key[len(stream_prefix):]
            doc, doc_findings = _resolved_member(fs, key)
            findings.extend(_at_site(f"streams/{suffix}", doc_findings))
            if doc is None:
                bundle_is_incomplete = True
            else:
                streams.append(doc)

    connections: list[Any] = []
    endpoints: list[Any] = []
    for key in sorted(k for k in fs.known_keys if _CONNECTION_DOC_RE.match(k)):
        # `conn_slug` comes from the KEY alone, never the connection document's
        # own content, so a connection-scoped endpoint or type map is still
        # locatable and independently checked even when the connection itself
        # fails to resolve.
        conn_slug = _CONNECTION_DOC_RE.match(key).group(1)
        doc, doc_findings = _resolved_member(fs, key)
        findings.extend(_at_site(key, doc_findings))
        if doc is None:
            bundle_is_incomplete = True
            connection_id_value = None
        else:
            connections.append(doc)
            connection_id_value = doc.get("connection_id")

        ep_prefix = f"connections/{conn_slug}/definition/endpoints/"
        for ep_key in fs.known_json_children(ep_prefix):
            ep_doc, ep_findings = _resolved_member(fs, ep_key)
            findings.extend(_at_site(ep_key, ep_findings))
            if ep_doc is None:
                bundle_is_incomplete = True
                continue
            entry = {**ep_doc}
            entry.setdefault("connection_id", connection_id_value)
            entry.setdefault("scope", "connection")
            endpoints.append(entry)

        findings.extend(_connection_type_map_findings(fs, conn_slug))

    connector_identities = set(connector_slugs)
    for conn_slug in connector_slugs:
        prefix = f"connectors/{conn_slug}/definition/"
        subtree = _extract_subtree(documents, prefix)
        sub_findings = validate_connector_tree(subtree)["findings"]
        findings.extend(_at_site(prefix.rstrip("/"), sub_findings))
        # validate=False: the recursive `validate_connector_tree` call just
        # above already validated this same connector.json (or reported why
        # it could not), scoped under `prefix` — this lookup exists only to
        # pull `connector_id` off it, not to check it a second time.
        connector_doc, _ = _resolved_member(
            fs, f"{prefix}connector.json", validate=False)
        if connector_doc is None:
            bundle_is_incomplete = True
        else:
            cid = connector_doc.get("connector_id")
            if isinstance(cid, str) and cid:
                connector_identities.add(cid)

    bundle = {
        "pipeline": pipeline_doc,
        "streams": streams,
        "connections": connections,
        "connectors": sorted(connector_identities),
        "endpoints": endpoints,
    }
    if not bundle_is_incomplete:
        require_runnable = isinstance(pipeline_doc, dict) and pipeline_doc.get("status") == "active"
        findings.extend(validate_pipeline_bundle(bundle, require_runnable=require_runnable))

    endpoint_sets, endpoint_set_findings = _connector_endpoint_sets(fs, connector_slugs)
    findings.extend(endpoint_set_findings)
    findings.extend(_connector_endpoint_ref_findings(streams, connections, endpoint_sets))
    return _envelope(findings)


def validate_tree(documents: DocumentSet) -> ValidationEnvelope:
    """Detect which package kind `documents` forms, and validate it.

    Walks a package-kind registry mirroring `analitiq.validator`'s own
    single-document `_KIND_REGISTRY`: each entry pairs a root-shape detector
    with the function that validates a document set matching it
    (`validate_connector_tree`, `validate_pipeline_tree`). No entry's detector
    matches reports an `unrecognized-layout` finding; more than one matching
    reports `ambiguous-layout`; exactly one dispatches to it — the same
    fallthrough shape the single-document dispatcher already uses when no
    registered kind claims a document (`unrecognized-document`).

    The root-shape detectors themselves need only a document set's key
    *names* — a root-level `connector.json`, a `pipelines/<slug>/pipeline.json`
    — so shape detection reads the normalized key set alone, without
    materializing any document's content; a document set matching exactly one
    shape then delegates its whole normalization and validation to that
    shape's own entry point rather than doing either twice.
    """
    keys = {key for key in (_normalize_key(raw_key) for raw_key in documents) if key is not None}
    is_connector = "connector.json" in keys
    is_pipeline = any(_PIPELINE_DOC_RE.match(key) for key in keys)
    if is_connector and is_pipeline:
        _, findings = _normalize_documents(documents)
        findings.append(finding(
            message_id="ambiguous-layout", kind="fail", path="",
            message=(
                "this document set matches both the connector-package root shape "
                "(a root-level connector.json) and the pipeline-bundle root shape "
                "(a pipelines/<slug>/pipeline.json) at once; lay it out as one or "
                "the other.")))
        return _envelope(findings)
    if not is_connector and not is_pipeline:
        _, findings = _normalize_documents(documents)
        findings.append(finding(
            message_id="unrecognized-layout", kind="fail", path="",
            message=(
                "this document set matches neither the connector-package root "
                "shape (a root-level connector.json) nor the pipeline-bundle "
                "root shape (a pipelines/<slug>/pipeline.json).")))
        return _envelope(findings)
    if is_connector:
        return validate_connector_tree(documents)
    return validate_pipeline_tree(documents)


def _is_single_document(target: Any) -> bool:
    """`target` is a single already-parsed document, as opposed to a
    `DocumentSet`, when it is not a mapping at all, or when some registered
    kind's own detector claims it — the same test `_core._dispatch` applies
    once it is committed to treating something as a single document. A
    `DocumentSet`'s own top-level keys are relative-path-shaped strings, which
    match no registered kind's detector, so it falls through to being treated
    as a document set rather than an unrecognized single document."""
    if not isinstance(target, dict):
        return True
    return any(detector(target) for detector, _ in _KIND_REGISTRY)


def diagnostics(target: Any, entity: Entity | None = None) -> ValidationEnvelope:
    """Auto-detect whether `target` is a single already-parsed document or a
    `DocumentSet`, and dispatch to `analitiq.validator.validate_document` or
    `validate_tree` accordingly — replacing the pipeline-builder plugin's
    private `diagnostics_for()`, which takes an explicit `entity` argument
    naming the document's kind rather than detecting it. `entity` plays no
    part in detecting or validating a document SET, which is identified by
    its shape alone; on the single-document route it is passed straight
    through to `validate_document`'s own explicit-kind override, letting a
    caller that already knows a document's kind name it directly instead of
    relying on shape auto-detection.

    Wraps a single-document result in a `ValidationEnvelope` (`{"passed":
    finding_costs_a_pass`-reduction, "findings": ...}`) rather than returning
    `validate_document`'s bare list, so a caller gets one result shape
    regardless of which route this dispatched to — the same wrapping the
    plugin's own `diagnostics_for` already does over `validate_document`'s
    list result today.
    """
    if _is_single_document(target):
        return _envelope(validate_document(target, entity=entity))
    return validate_tree(target)


def resolve_type_map_gaps(
        maps: DocumentSet,
        direction: Literal["read", "write"],
        probes: list[str]) -> FindingsEnvelope:
    """The path-free form of the pipeline-builder plugin's private
    `type_map_gaps.py`: given one or more type-map documents in precedence
    order (`maps`, keyed and ordered the same way a `DocumentSet` is — the
    first key whose map renders a probe wins) and a list of native-type
    `probes` to resolve in `direction`, report what each probe resolved to.

    Never raises. Returns `{"findings"}` only — no `resolved`, no top-level
    `direction`. Finding kinds sharing that list: `type-map-unreadable`
    (`fail`/`error`, no `direction` — a map that is invalid JSON or not a
    list, a failure prior to any direction-specific check); `invalid-type-map`
    (`fail`/`error`, `direction` = this call's `direction` — a map that fails
    its `TypeMapReadDoc`/`TypeMapWriteDoc` model,
    `analitiq.contracts.type_map`); `type-map-gap` (`informational`, no
    severity, no rule, `direction` = this call's `direction` — a probe none of
    `maps` resolved; never costs a pass). A run in which every probe resolved
    reports an empty `findings` list.

    This is a deliberate divergence from `type_map_gaps.py`'s own
    `_load_rules`, which raises `ValueError` on exactly the same two
    conditions (an unreadable map, a map failing its model) rather than
    reporting them: a path-free caller has no filename to name in a raised
    message the way the plugin's CLI does, and folding both conditions into
    the same findings list this function already returns means a caller
    checks one place for every way a probe run can come back incomplete.

    A map's own key/value or read/parse defect (`invalid-key`, `invalid-value`,
    `type-map-unreadable`, `invalid-type-map`) short-circuits the probe walk
    below it: a probe reported as a gap against an incomplete set of maps
    would be indistinguishable from a genuine gap against the maps the caller
    actually intended, so this function reports the map defect(s) alone and
    leaves every probe unjudged rather than guessing.

    `maps` is walked in sorted key order (by string form, so a raw key of any
    type still sorts rather than crashing `_key_and_value_findings` never got
    to grade), not the caller's own insertion order — the same precedent
    `_normalize_documents` already sets — so this function's own findings
    never depend on how the caller happened to build the mapping.
    """
    findings: list[dict] = []
    rendered_maps: list[list] = []
    for raw_key in sorted(maps, key=str):
        value = maps[raw_key]
        key, kv_findings = _key_and_value_findings(raw_key, value)
        if key is None:
            findings.extend(kv_findings)
            continue
        if isinstance(value, (dict, list)):
            parsed: Any = value
        else:
            try:
                text = value.decode("utf-8-sig") if isinstance(value, bytes) else value
                parsed = json.loads(text)
            except ValueError:
                parsed = None
        if not isinstance(parsed, list):
            findings.append(finding(
                message_id="type-map-unreadable", kind="fail", path=key,
                message=f"{key!r} is not readable as a JSON array of type-map rules."))
            continue
        adapter = _READ_MAP_ADAPTER if direction == "read" else _WRITE_MAP_ADAPTER
        try:
            adapter.validate_python(parsed)
        except ValidationError:
            findings.append({
                **finding(
                    message_id="invalid-type-map", kind="fail", path=key,
                    message=f"{key!r} does not satisfy the {direction} type-map model."),
                "direction": direction,
            })
            continue
        rendered_maps.append(parsed)

    if findings:
        return {"findings": findings}

    for probe in probes:
        if direction == "read":
            resolved = any(_render_arrow_type(probe, rules) is not None for rules in rendered_maps)
        else:
            resolved = any(
                _first_match_render(probe, rules, "arrow_type", "native_type") is not None
                for rules in rendered_maps)
        if not resolved:
            findings.append({
                **finding(
                    message_id="type-map-gap", kind="informational", path="",
                    message=f"no bundled {direction} type map resolves {probe!r}."),
                "direction": direction,
            })
    return {"findings": findings}
