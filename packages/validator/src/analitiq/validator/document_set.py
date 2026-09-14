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
from ._virtual_fs import VirtualPath, _VirtualFS, path_parts_key
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
    """Validate one `DocumentSet` entry's key and value shape. `_normalize_documents`
    is the one caller — every other function that walks a `DocumentSet`
    (`resolve_type_map_gaps` included) reaches this through that function
    rather than calling it directly, so none of them can grade a malformed
    entry differently. Returns `(None, [invalid-key finding])` when `raw_key` itself
    is not a usable relative path — the raw key is what that finding's `path`
    names, since the normalized form does not exist for it. Returns
    `(key, [invalid-value finding])` when the key is fine but `value` is not
    one of the usable value types: the key is a real path identity in this
    document set regardless of whether its content is usable, so a caller
    must still be able to discover it (e.g. a connection slug whose sibling
    endpoints and type maps are independently checkable) rather than treating
    an unusable value the same as an unusable key. Returns `(key, [])` when
    both are fine."""
    key = _normalize_key(raw_key)
    if key is None:
        return None, [finding(
            # `path` is a public string field (`rules/SCHEMA.md`'s Findings
            # shape); `raw_key` is exactly the value that failed to normalize
            # into one, so it is stringified here rather than passed through —
            # an int, tuple, or bytes key would otherwise produce a
            # schema-invalid finding, or break `json.dumps` on the envelope.
            message_id="invalid-key", kind="fail", path=str(raw_key),
            message=(
                f"key {raw_key!r} is not a usable relative document path: it must "
                "be a non-empty relative path (no leading '/') with no '.' or "
                "'..' path segment."))]
    if not isinstance(value, (str, bytes, dict, list)):
        return key, [finding(
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
    type still sorts against another of a different type, rather than
    crashing on a comparison before `_key_and_value_findings` ever gets a
    chance to grade it as an invalid key), not the caller's own insertion
    order, so this pass's own
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
        if kv_findings:
            # invalid-value: the key is still a real path identity in this
            # document set (kept in known_keys for discovery, per
            # _key_and_value_findings), but there is no usable content to
            # materialize into texts/objects.
            findings.extend(kv_findings)
            continue
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
    here.

    Two raw keys colliding on the same subtree-relative key (e.g. a
    `./`-prefixed duplicate) are resolved the same way `_normalize_documents`
    already resolves the identical collision at the whole tree's own top
    level — keep the first in sorted key order — by walking `documents` in
    that same order and keeping only the first value `setdefault` sees for
    each relative key, rather than raw insertion order silently picking
    whichever raw key happened to come last."""
    subtree: DocumentSet = {}
    for raw_key in sorted(documents, key=str):
        key = _normalize_key(raw_key)
        if key is None or not key.startswith(prefix):
            continue
        subtree.setdefault(key[len(prefix):], documents[raw_key])
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
        # entity="connector": the root key's kind is already known from its
        # position; auto-detecting would validate a wrong-shaped document
        # (say, a valid pipeline object) as whatever kind it happens to
        # match, skipping connector validation and coverage entirely.
        _, doc_findings = _validate_tree_document(fs, "connector.json", entity="connector")
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

    This walk runs over every embedded connector's `endpoints/*.json` files
    regardless of `kind`, and for a `database`/`storage`-kind connector it is
    the only one: `check_coverage` returns before reaching that kind's
    endpoint files at all. So a crash reading one (materialization or parse)
    is reported here (`internal-error`, parse-crash only — a materialization
    crash is `_normalize_documents`'s own to name) rather than swallowed, and
    a key that never resolves is
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
    # Sorted by `path_parts_key`, matching `_VirtualFS.glob_json`'s ordering —
    # a plain string sort would disagree with it whenever one slug is another
    # plus a hyphenated suffix (`pipelines/a/...` vs `pipelines/a-b/...`).
    pipeline_keys = sorted(
        (key for key in fs.known_keys if _PIPELINE_DOC_RE.match(key)), key=path_parts_key)
    if pipeline_keys:
        primary_key = pipeline_keys[0]
        slug = _PIPELINE_DOC_RE.match(primary_key).group(1)
        # entity="pipeline": this key's position already says what it must be;
        # auto-detecting would validate a wrong-shaped document (say, a valid
        # type-map array) as whatever kind it happens to match instead of
        # rejecting it as the pipeline document it was supposed to be.
        pipeline_doc, doc_findings = _resolved_member(fs, primary_key, entity="pipeline")
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
            # entity="stream": same reasoning as the pipeline document above.
            doc, doc_findings = _resolved_member(fs, key, entity="stream")
            findings.extend(_at_site(f"streams/{suffix}", doc_findings))
            if doc is None:
                bundle_is_incomplete = True
            else:
                streams.append(doc)

    connections: list[Any] = []
    endpoints: list[Any] = []
    # Sorted by `path_parts_key`, matching `_VirtualFS.glob_json`'s ordering —
    # see `pipeline_keys` above for why a plain string sort would diverge.
    for key in sorted((k for k in fs.known_keys if _CONNECTION_DOC_RE.match(k)), key=path_parts_key):
        # `conn_slug` comes from the KEY alone, never the connection document's
        # own content, so a connection-scoped endpoint or type map is still
        # locatable and independently checked even when the connection itself
        # fails to resolve.
        conn_slug = _CONNECTION_DOC_RE.match(key).group(1)
        # entity="connection": same reasoning as the pipeline document above.
        doc, doc_findings = _resolved_member(fs, key, entity="connection")
        findings.extend(_at_site(key, doc_findings))
        if doc is None:
            bundle_is_incomplete = True
            connection_id_value = None
        else:
            connections.append(doc)
            connection_id_value = doc.get("connection_id")

        ep_prefix = f"connections/{conn_slug}/definition/endpoints/"
        for ep_key in fs.known_json_children(ep_prefix):
            # entity="database-endpoint": a connection-scoped endpoint is
            # always a database endpoint by its position in the tree — unlike
            # an embedded connector's own endpoints (api-vs-database is
            # genuinely ambiguous there, and is left to shape auto-detection),
            # so pinning here rejects a wrong-shaped document the same way the
            # pipeline/stream/connection members above already do.
            ep_doc, ep_findings = _resolved_member(fs, ep_key, entity="database-endpoint")
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
        # `_resolved_member`'s gate (materializes, parses, is a dict) admits a
        # member that is dict-shaped but fails its own model validation —
        # deliberately, mirroring the plugin's `complete`/`crashed` distinction
        # documented above. The published bundle validator assumes referenced
        # collections hold the shapes their model promises, so a member this
        # gate let through with a wrong-shaped field (e.g. a non-iterable
        # `destinations`) can still crash it. `_contained` in `plugins/
        # analitiq-pipeline-builder/scripts/validate.py` isolates this same
        # call for the same reason; mirror it here rather than tightening the
        # gate, which would report those documents' own model-validation
        # findings twice.
        require_runnable = isinstance(pipeline_doc, dict) and pipeline_doc.get("status") == "active"
        try:
            findings.extend(validate_pipeline_bundle(bundle, require_runnable=require_runnable))
        except Exception as exc:  # noqa: BLE001 - isolate the referential pass's own crash
            findings.append(finding(
                message_id="internal-error", kind="fail", path="pipeline",
                message=(
                    "cross-document referential validation crashed "
                    f"({type(exc).__name__}: {exc}); a bundle member can materialize, parse, "
                    "and still fail its own model validation, which this pass does not "
                    "re-check before reading the shapes it assumes.")))

    endpoint_sets, endpoint_set_findings = _connector_endpoint_sets(fs, connector_slugs)
    findings.extend(endpoint_set_findings)
    findings.extend(_connector_endpoint_ref_findings(streams, connections, endpoint_sets))
    return _envelope(findings)


def _tree_root_signals(documents: DocumentSet) -> tuple[bool, bool]:
    """Whether `documents`' own key *names* carry a connector-package root
    (`connector.json`) or a pipeline-bundle root
    (`pipelines/<slug>/pipeline.json`) — read from the normalized key set
    alone, without materializing any document's content. Shared by
    `validate_tree` (which root shape a document set forms) and
    `_is_single_document` (whether it is a document set at all): a key that
    happens to also be a single-document field name (`"connections"`, say)
    must not fall through to single-document detection once one of these
    tree roots is actually present."""
    keys = {key for key in (_normalize_key(raw_key) for raw_key in documents) if key is not None}
    is_connector = "connector.json" in keys
    is_pipeline = any(_PIPELINE_DOC_RE.match(key) for key in keys)
    return is_connector, is_pipeline


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

    The root-shape detectors (`_tree_root_signals`) need only a document set's
    key *names*, so shape detection reads the normalized key set alone,
    without materializing any document's content; a document set matching
    exactly one shape then delegates its whole normalization and validation to
    that shape's own entry point rather than doing either twice.
    """
    is_connector, is_pipeline = _tree_root_signals(documents)
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


def _document_set_shaped_keys(target: dict) -> bool:
    """Whether any of `target`'s own keys, once normalized, is shaped like a
    `DocumentSet` member key — containing `/` or ending in `.json` — rather
    than a single document's own field name. A document set is not restricted
    to keys carrying an explicit tree root (`connector.json`,
    `pipelines/<slug>/pipeline.json`; each already matches this broader test):
    a sibling key like `connections/pg/connection.json` marks the whole
    mapping as a document set even when another one of its keys happens to
    coincide with a single-document field name (a root-level key literally
    named `"connections"`, say)."""
    return any(
        "/" in key or key.endswith(".json")
        for key in (_normalize_key(raw_key) for raw_key in target)
        if key is not None
    )


def _is_single_document(target: Any) -> bool:
    """`target` is a single already-parsed document, as opposed to a
    `DocumentSet`, when it is not a mapping at all, or when some registered
    kind's own detector claims it — the same test `_core._dispatch` applies
    once it is committed to treating something as a single document. A
    `DocumentSet`'s own top-level keys are relative-path-shaped strings, which
    match no registered kind's detector, so it falls through to being treated
    as a document set rather than an unrecognized single document.

    `_document_set_shaped_keys` is checked first, ahead of the kind registry:
    once any key of `target` is itself path-shaped, `target` is a document set
    regardless of what a field-shape detector would have said about it as a
    whole."""
    if not isinstance(target, dict):
        return True
    if _document_set_shaped_keys(target):
        return False
    return any(detector(target) for detector, _ in _KIND_REGISTRY)


def diagnostics(
        target: Any, entity: Entity | None = None,
        schema_url: str | None = None) -> ValidationEnvelope:
    """Auto-detect whether `target` is a single already-parsed document or a
    `DocumentSet`, and dispatch to `analitiq.validator.validate_document` or
    `validate_tree` accordingly — replacing the pipeline-builder plugin's
    private `diagnostics_for()`, which takes an explicit `entity` argument
    naming the document's kind rather than detecting it. `entity` and
    `schema_url` play no part in detecting or validating a document SET, which
    is identified by its shape alone; on the single-document route both are
    passed straight through to `validate_document`, letting a caller that
    already knows a document's kind name it directly instead of relying on
    shape auto-detection, and disambiguate a type-map array's direction the
    same way a real `doc_path`'s filename would — this path-free route has no
    filename for `validate_document`'s own type-map validator to read one
    from, so without `schema_url` an ambiguous-direction type-map document
    always defaults to read.

    Wraps a single-document result in a `ValidationEnvelope` (`{"passed":
    finding_costs_a_pass`-reduction, "findings": ...}`) rather than returning
    `validate_document`'s bare list, so a caller gets one result shape
    regardless of which route this dispatched to — the same wrapping the
    plugin's own `diagnostics_for` already does over `validate_document`'s
    list result today.
    """
    if _is_single_document(target):
        return _envelope(validate_document(target, entity=entity, schema_url=schema_url))
    return validate_tree(target)


def resolve_type_map_gaps(
        maps: DocumentSet,
        direction: Literal["read", "write"],
        probes: list[str]) -> FindingsEnvelope:
    """The path-free form of the pipeline-builder plugin's private
    `type_map_gaps.py`: given one or more type-map documents (`maps`, a
    `DocumentSet` like any other) and a list of native-type `probes` to
    resolve in `direction`, report every probe that no map in `maps` resolves.

    Never raises. Returns `{"findings"}` only — no `resolved`, no top-level
    `direction`. Finding kinds sharing that list: `invalid-key`/`invalid-value`,
    `duplicate-key`/`key-path-conflict`, and `internal-error` (a map's own
    value crashed while being turned into text, e.g. bytes that are not valid
    UTF-8) — the same collection-level hazards `_normalize_documents` guards
    against, produced by delegating `maps`'s own key/value validation and
    materialization to that same function rather than a second, hand-rolled
    walk over it, since two raw keys normalizing to one map (or one key that
    is also a directory prefix of another) could otherwise spread
    complementary rules across both and make every probe appear covered when
    only one of them could actually exist at runtime; `type-map-unreadable`
    (`fail`/`error`, no `direction` — a map that failed to parse as JSON,
    crashed for any other reason while parsing (a `RecursionError` from
    pathologically deep nesting, say — isolated the same way
    `_validate_tree_document`'s own parse step is), or parsed to something
    other than a list, a failure prior to any direction-specific check);
    `type-map-wrong-direction` (`fail`/`error`, `direction` = this call's
    `direction` — a map whose canonical filename
    (`type-map-read.json`/`type-map-write.json`) names the other direction:
    both directions' exact-rule shapes share the same keys, so nothing in
    `TypeMapReadDoc`/`TypeMapWriteDoc` model validation alone can catch a map
    loaded under the wrong direction, mirroring `type_map_gaps.py`'s own
    filename/direction check); `invalid-type-map` (`fail`/`error`, `direction`
    = this call's `direction` — a map that fails its
    `TypeMapReadDoc`/`TypeMapWriteDoc` model, `analitiq.contracts.type_map`);
    `invalid-probes` (`fail`/`error`, `direction` = this call's `direction`,
    no `path` — `probes` itself is not a `list`, short-circuiting the walk
    below entirely the same way a map defect does: a non-list `probes` is
    not "zero probes to check" and iterating it directly would either raise
    (`None`) or silently probe its characters as if they were the intended
    values (a bare `str`)); `invalid-probe` (`fail`/`error`, `direction` =
    this call's `direction` — one element of `probes` that is not a `str`.
    The read route's `_render_arrow_type` normalizes a probe by calling
    string methods on it directly and crashes on anything else; the write
    route's `_first_match_render` matches a probe against each rule's
    `arrow_type` with no such normalization, so on that route a non-string
    probe would not crash at all — it would instead be silently graded
    resolved-or-gapped by whatever `==`/regex comparison its type happens to
    support, which is the wrong failure mode for the same underlying defect.
    This finding replaces both outcomes with one explicit report, on either
    route, rather than letting only one of them announce itself by crashing);
    `type-map-gap` (`informational`, no severity, no rule, `direction` = this
    call's `direction` — a probe none of `maps` resolved; never costs a
    pass). A run in which every probe resolved reports an empty `findings`
    list.

    `probes` is de-duplicated before resolution, order-preserving — a
    repeated probe (valid or invalid) is one verdict, not one finding per
    occurrence — mirroring the outcome `type_map_gaps.py`'s own `resolve`
    already gives its CLI probes.

    This is a deliberate divergence from `type_map_gaps.py`'s own
    `_load_rules`, which raises `ValueError` on exactly the same two
    conditions (an unreadable map, a map failing its model) rather than
    reporting them: a path-free caller has no filename to name in a raised
    message the way the plugin's CLI does, and folding both conditions into
    the same findings list this function already returns means a caller
    checks one place for every way a probe run can come back incomplete.

    A map's own key/value, materialization, read/parse, or direction defect
    (`invalid-key`, `invalid-value`, `internal-error`, `type-map-unreadable`,
    `type-map-wrong-direction`, `invalid-type-map`) short-circuits the probe
    walk below it: a probe
    reported as a gap against an incomplete set of maps would be
    indistinguishable from a genuine gap against the maps the caller actually
    intended, so this function reports the map defect(s) alone and leaves
    every probe unjudged rather than guessing.

    `maps` is walked in sorted key order, not the caller's own insertion
    order — the same precedent `_normalize_documents` already sets — so this
    function's own findings never depend on how the caller happened to build
    the mapping.
    """
    fs, findings = _normalize_documents(maps)
    rendered_maps: list[list] = []
    load_bearing_filename = {_READ_MAP_FILENAME: "read", _WRITE_MAP_FILENAME: "write"}
    for key in sorted(fs.known_keys, key=path_parts_key):
        if not fs.materialized(key):
            # Already reported by `_normalize_documents` (`internal-error`) or
            # `_key_and_value_findings` (`invalid-value`) when it first tried
            # to turn this key's value into usable content — a second finding
            # here would only double-report the same crash.
            continue
        try:
            parsed = fs.parsed(key)
        except Exception as exc:  # noqa: BLE001 - isolate one map's parse crash
            findings.append(finding(
                message_id="type-map-unreadable", kind="fail", path=key,
                message=(
                    f"{key!r} is not readable as a JSON array of type-map rules "
                    f"({type(exc).__name__}: {exc}).")))
            continue
        if not isinstance(parsed, list):
            findings.append(finding(
                message_id="type-map-unreadable", kind="fail", path=key,
                message=f"{key!r} is not readable as a JSON array of type-map rules."))
            continue
        implied_direction = load_bearing_filename.get(key.rsplit("/", 1)[-1])
        if implied_direction is not None and implied_direction != direction:
            findings.append({
                **finding(
                    message_id="type-map-wrong-direction", kind="fail", path=key,
                    message=(
                        f"{key!r} is a {implied_direction}-direction map by its canonical "
                        f"filename, but direction={direction!r} was requested; the read and "
                        "write exact-rule shapes share the same keys, so model validation "
                        "alone cannot catch a map loaded under the wrong direction.")),
                "direction": direction,
            })
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

    if not isinstance(probes, list):
        return {"findings": [{
            **finding(
                message_id="invalid-probes", kind="fail", path="",
                message=f"probes must be a list of strings; got {type(probes).__name__}."),
            "direction": direction,
        }]}

    # Order-preserving de-duplication via list membership, not a `set` /
    # `dict.fromkeys` — a probe may be any type until the `isinstance` check
    # just below runs, and a `set` would itself crash on an unhashable one
    # (e.g. a `list` mistakenly passed as a probe), the exact "never raises"
    # violation this whole block exists to close.
    deduped_probes: list = []
    for probe in probes:
        if probe not in deduped_probes:
            deduped_probes.append(probe)

    for probe in deduped_probes:
        if not isinstance(probe, str):
            expected = "a native-type" if direction == "read" else "an Arrow-type"
            findings.append({
                **finding(
                    message_id="invalid-probe", kind="fail", path="",
                    message=f"probe {probe!r} is not a string; every {direction} probe must be {expected} name."),
                "direction": direction,
            })
            continue
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
