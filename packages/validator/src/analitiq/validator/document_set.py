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

This module fixes the contract such a consumer calls instead: a `DocumentSet`
of already-loaded content, keyed by the relative path each document would
have occupied on disk, in and out. `validate_doc`'s type-map gap-resolution
mode resolves probes; `validate_connector_tree`, `validate_pipeline_tree` and
`validate_doc`'s ordinary document mode raise `NotImplementedError`. The
behaviour every route must satisfy is fixed by
`packages/validator/tests/test_document_set.py`, whose cases for a route that
raises carry an `xfail` marker — `strict`, so a marker cannot outlive the body
it stands in for.
"""
from __future__ import annotations

import codecs
import json
import posixpath
from typing import Any, Literal, TypedDict, Union

from ._core import _bounded, _passed, crash_finding, finding, finding_costs_a_pass

#: The value half of a `DocumentSet` entry: text, raw bytes (decoded as UTF-8,
#: with a BOM stripped from either text form — no encoding-guessing), or a value already
#: parsed into the `dict`/`list` shape `analitiq.validator.validate_document`
#: itself expects for `doc`. A value that is none of these is reported as an
#: `invalid-value` finding on its own key, never raised.
DocumentSetValue = Union[str, bytes, dict, list]

#: A path-free bundle of documents: POSIX-relative-path-shaped string keys
#: (e.g. `"connections/foo/connection.json"`, `"endpoints/widgets.json"`) mapped
#: to already-loaded content. A key is canonicalized the way a relative POSIX
#: path is, so redundant `.` and `/` spellings of one path are one key; an
#: absolute key, a key that escapes the set, a key naming the set root rather
#: than a document in it, and the empty string are reported as an
#: `invalid-key` finding rather than raised; two keys where one names both a
#: document and a directory prefix of another (e.g. `"a/b.json"` alongside
#: `"a/b.json/c.json"`) are reported as `key-path-conflict` instead; distinct
#: raw keys that canonicalize together — e.g. `"connector.json"` alongside
#: `"./connector.json"` — are reported as `normalized-key-collision` rather
#: than one silently overwriting the other by iteration order. Every
#: function below that takes or produces a document set uses this one shape,
#: so `validate_connector_tree`'s and `validate_pipeline_tree`'s `documents`
#: and `validate_doc`'s `doc` (in its type-map gap-resolution mode) share one
#: value type rather than independently-typed mappings.
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


class _Omitted:
    """Sentinel default for `validate_doc`'s `probes` parameter, distinguishing
    the argument being omitted entirely from a caller explicitly passing
    `probes=None` — a plain `None` default cannot: both bind `probes` to the
    same value. The two must differ here: passing `probes` at all, `None`
    included, selects type-map gap-resolution mode below and is then rejected
    as an invalid `probes` value (`None` is not a `list`), while omitting it
    selects ordinary document mode."""

    def __repr__(self) -> str:
        return "<omitted>"


_OMITTED: Any = _Omitted()


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
    only `validate_doc`'s type-map gap-resolution mode below sets (see its
    docstring for which finding kinds carry it). Restated here only so this
    module's signatures are checkable; the shape itself stays owned by
    `finding()`, and
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


# ---------------------------------------------------------------------------
# Shared `DocumentSet` handling — one definition of how a key is canonicalized
# and how a value is decoded, so the routes that take a `DocumentSet` cannot
# answer the same question differently.
# ---------------------------------------------------------------------------

#: A UTF-8 byte-order mark, decoded — derived from the `codecs` constant rather
#: than typed out, since the character itself is invisible in a source file and
#: survives no editor that normalizes one. Stripped from the leading edge of
#: decoded text before it is parsed; `json` rejects it as a stray character.
_BOM = codecs.BOM_UTF8.decode("utf-8")

#: How many colliding spellings a `normalized-key-collision` names before it
#: counts the rest. Enough that a caller reading the finding sees the mistake
#: they actually made — two or three spellings of one path is what a collision
#: normally is — without the message growing with the input.
_MAX_SPELLINGS = 5


def _envelope(findings: list[Finding]) -> ValidationEnvelope:
    """Wrap a findings list in the envelope shape this module's routes return,
    taking the verdict from `_core._passed` — the same reduction the path-based
    route answers "did this document pass" with, rather than a second predicate
    beside it."""
    return {"passed": _passed(findings), "findings": list(findings)}


def _directed(f: Finding, direction: str) -> Finding:
    """Tag a finding with the direction it was produced under — a declared
    `Finding` key `finding()` itself never sets."""
    return {**f, "direction": direction}


#: Where caller-supplied text is allowed to reach a finding whole, and where it
#: is not. A `path` identifies the document a finding is about, so it carries
#: the caller's key verbatim: clipping it would let two keys sharing a prefix
#: arrive as one, and the envelope cannot say which document it meant. A
#: `message` explains, and every piece of caller text in one is borrowed
#: diagnostic — a `repr`, a type name, a parser's complaint, an exception —
#: clipped to `_core._bounded`'s width. Unclipped, a finding's size is a
#: multiple of the input it complains about, and the hosted consumer this
#: module exists for lets the caller choose how much comes back.
#:
#: Echoing a key in `path` is bounded by the request: each document draws a
#: small, fixed number of findings, so the response stays proportional to what
#: was sent. It is the message interpolations that multiply, which is why they
#: are the half that clips. `_repr` and `_type_name` are the two ways a caller
#: value enters a message, so between them they are where that is enforced.


def _repr(value: Any) -> str:
    """`repr`, for a value this module is about to name in a finding about how
    unusable it is — bounded on both paths out, since the fallback names a
    caller-chosen type too.

    The caller owns the object, so its `__repr__` is the caller's code: it may
    raise, and a message explaining why an input was rejected must not become
    the second failure.
    """
    try:
        return _bounded(repr(value))
    except Exception:  # noqa: BLE001 - see the docstring
        return _bounded(f"<unrepresentable {type(value).__name__}>")


def _type_name(value: Any) -> str:
    """The name of `value`'s type, for a message that reports having been
    handed the wrong kind of thing. A caller names its own classes, so this is
    borrowed text like any other."""
    return _bounded(type(value).__name__)


def _canonical_key(raw: str) -> str:
    """The one spelling of the relative path `raw` names, via `posixpath`
    rather than a strip of its own: `"./a.json"`, `"././a.json"` and
    `"a/./b.json"` name the documents `"a.json"` and `"a/b.json"`, and a
    hand-written canonicalizer that misses one of those spellings lets two keys
    for one document both survive — which is the collision
    `normalized-key-collision` exists to report."""
    return posixpath.normpath(raw)


def _key_rejection(raw: object) -> str | None:
    """Why `raw` cannot name a document in a `DocumentSet`, or `None` when it
    can. A key stands in for the relative path the document would occupy on
    disk, so it must be a non-empty relative string naming a document inside
    the set.

    Every test is applied to the canonical form, not the raw key, because a key
    can satisfy all of them as written and none of them as resolved:
    `"a/../../b.json"` does not begin with `"../"` and resolves to
    `"../b.json"`, which leaves the set, and `"a/.."` names no document while
    looking like one.
    """
    if not isinstance(raw, str) or not raw:
        return "a document set key must be a non-empty string"
    key = _canonical_key(raw)
    if key.startswith("/"):
        return "a document set key is relative, never absolute"
    if key == ".." or key.startswith("../"):
        return "a document set key names a document inside the set, and this one leaves it"
    if key == ".":
        return "a document set key names a document, not the root of the set"
    return None


def _normalized_documents(documents: dict) -> tuple[dict[str, Any], list[Finding], list[str]]:
    """Canonicalize a `DocumentSet`'s keys, reporting every key it cannot use
    rather than raising on one.

    Returns the usable entries — canonical key to raw value, in the input's own
    iteration order, which is what carries precedence for a route that has any
    — alongside the findings for the keys left out, and the name each dropped
    key went by. A route that consults only part of a set reads that last list
    to tell whether what it lost was something it would have consulted; a key
    too malformed to have a name contributes the empty string, which no route
    can match and every route must therefore treat as possibly its own.

    The name is whichever spelling that route would have tested. For a collided
    or conflicting key that is the canonical one, which is what a route
    selecting documents would have matched against had the key survived. A
    rejected key has no such form to offer: it was rejected *for* what it
    canonicalizes to — `"a/../../b.json"` resolves outside the set, `"a/.."`
    names no document — so its canonical form is not a name any route could
    have matched, and the raw spelling, which is also what the caller wrote, is
    the only name it ever had.

    A key that cannot name a document at all is `invalid-key`, reported against
    the raw key the caller wrote rather than a canonical form they would not
    recognize. Distinct raw keys that canonicalize together are
    `normalized-key-collision`: which of them applies could only be settled by
    iteration order, so neither is used. A key nested under another key's own
    document is `key-path-conflict` — no filesystem holds both, and it is the
    nested key that cannot exist, so that is the one dropped and named.
    """
    findings: list[Finding] = []
    dropped: list[str] = []
    grouped: dict[str, list[tuple[str, Any]]] = {}
    for raw, value in documents.items():
        rejection = _key_rejection(raw)
        if rejection is not None:
            findings.append(finding(
                message_id="invalid-key", kind="fail",
                path=raw if isinstance(raw, str) else _repr(raw),
                message=f"{_repr(raw)} cannot be used as a document set key: {rejection}."))
            dropped.append(raw if isinstance(raw, str) else "")
            continue
        grouped.setdefault(_canonical_key(raw), []).append((raw, value))

    usable: dict[str, Any] = {}
    for key, entries in grouped.items():
        if len(entries) > 1:
            # Each spelling is bounded, and so is how many are named: a caller
            # can collide unboundedly many raw keys onto one canonical name,
            # and a finding that lists every one of them stops being a sentence
            # long before it stops being accurate.
            shown = [_repr(raw) for raw, _ in entries[:_MAX_SPELLINGS]]
            rest = len(entries) - len(shown)
            spellings = ", ".join(shown) + (f", and {rest} more" if rest else "")
            findings.append(finding(
                message_id="normalized-key-collision", kind="fail", path=key,
                message=(f"{spellings} all name the document {_repr(key)}; which of them "
                         "applies would be decided by iteration order.")))
            dropped.append(key)
            continue
        usable[key] = entries[0][1]

    # Sorted, so which conflict is reported first does not follow the caller's
    # iteration order. Deleting a parent mid-walk cannot hide a conflict from
    # the keys under it: a parent is only ever deleted for being nested under a
    # shallower document itself, and that one — nested under nothing, so never
    # deleted — is what the search finds instead.
    for key in sorted(usable):
        segments = key.split("/")
        parent = next(
            (p for p in ("/".join(segments[:i]) for i in reversed(range(1, len(segments))))
             if p in usable),
            None)
        if parent is not None:
            findings.append(finding(
                message_id="key-path-conflict", kind="fail", path=key,
                message=(f"{_repr(key)} is nested under {_repr(parent)}, which this set "
                         "also carries as a document of its own.")))
            del usable[key]
            dropped.append(key)
    return usable, findings, dropped


def _document_content(value: Any) -> tuple[Any, str | None, str]:
    """Decode one `DocumentSet` value into the parsed shape a validator takes.

    A `dict`/`list` is already parsed and passes through untouched; `str` and
    `bytes` are JSON-parsed, `bytes` decoded as UTF-8 with no encoding-guessing,
    and a leading BOM stripped from either text form — a caller that read a
    BOM-bearing file with `str` semantics hands over the mark just as one
    handing over the raw bytes does.

    Returns `(content, None, "")`, or `(None, reason, detail)` where `reason` is
    `"invalid-type"` for a value of a type this contract does not hold,
    `"not-utf8"` for bytes that are not UTF-8, `"unparseable"` for text that is
    not JSON, and `"over-limit"` for JSON this interpreter refuses to build a
    value from. `detail` says what was found: the type name for `invalid-type`,
    and otherwise what the underlying failure said — the decode position, the
    JSON syntax error with its line and column, the limit the interpreter
    refused to exceed. The caller interpolates it, because a type map reported
    only as "unreadable" leaves its author bisecting a file the parser could
    already point into. It is bounded here rather than at each of those call
    sites, so no route can interpolate it unclipped.

    The reason is a code rather than a finding: what an unreadable value means
    is each route's own vocabulary, and this function is shared by all of them.
    """
    if isinstance(value, (dict, list)):
        return value, None, ""
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            return None, "not-utf8", _bounded(str(exc))
    elif isinstance(value, str):
        text = value
    else:
        return None, "invalid-type", _type_name(value)
    # Stripped outside the guard below, which names the two ways `json.loads`
    # declines well-formed JSON. `text` is a caller-supplied `str` subclass as
    # readily as a `str`, so `removeprefix` can be the caller's own code; a
    # crash in it is a crash, and folding it in here would report it as a
    # property of the document instead.
    body = text.removeprefix(_BOM)
    try:
        return json.loads(body), None, ""
    except json.JSONDecodeError as exc:
        return None, "unparseable", _bounded(str(exc))
    except (ValueError, RecursionError) as exc:
        # Separated from `unparseable` because the document is not malformed:
        # an integer literal past `int_max_str_digits` and nesting past the
        # recursion limit are both well-formed JSON that this interpreter
        # declines to build a value from, and another reader of the same bytes
        # would accept them. Calling either a syntax error sends the author
        # hunting for a typo that is not there, and letting it reach a crash
        # guard blames the validator for a property of the input.
        return None, "over-limit", _bounded(f"{type(exc).__name__}: {exc}")


def validate_connector_tree(documents: DocumentSet) -> ValidationEnvelope:
    """Validate a connector package supplied as an in-memory `DocumentSet`
    instead of files on disk: the connector document, its sibling type maps,
    and its `endpoints/*.json` files, cross-checked the way
    `analitiq.validator.check_coverage` already does from a filesystem path.

    Called directly by a caller that already knows `documents` is a
    connector package — this module never inspects a document set's shape to
    decide what kind of thing it is; the caller (or the wire-schema wrapper
    in front of it) declares that before this function is ever reached. That
    declaration is not a promise this function takes on faith: a `documents`
    with no `"connector.json"` key — the one document every other check here
    needs to run at all — is reported as a `missing-package-root` finding
    (`fail`/`error`, path `"connector.json"`) rather than validated as an
    empty, trivially-passing package. A `"connector.json"` that is present but
    does not itself validate as a connector document is rejected the same
    way, not silently accepted as a trivially-passing package: `check_coverage`
    itself returns no findings for a document missing a connector's sentinel
    keys, so package-root validity cannot rest on coverage findings alone —
    this route validates `"connector.json"`'s own content, not just its
    presence.

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_connector_tree is not implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")


def validate_pipeline_tree(documents: DocumentSet) -> ValidationEnvelope:
    """Validate a pipeline bundle supplied as an in-memory `DocumentSet`
    instead of files on disk: the pipeline document, its sibling
    `streams/*.json`, and every `connections/*/connection.json` (plus their
    scoped endpoints and type maps) — assembled the way `plugins/
    analitiq-pipeline-builder/scripts/validate.py`'s `_assemble_bundle`
    already does from a filesystem root, then checked for referential
    integrity.

    Any `connectors/<slug>/definition/...` subtree is validated separately, by
    resolving it through `validate_connector_tree` and scoping its findings'
    `path` under the subtree's key prefix — so an embedded connector's own
    coverage findings (native-type coverage, `transport_ref` resolution,
    duplicate endpoint ids) are reported. Today's plugin never resolves this
    subtree that way at all: `_assemble_bundle` itself reads only that
    subtree's `connector.json`, and only for its `connector_id` (the
    referential check that the identity is bundled); a separate function,
    `_connector_endpoint_sets`, reads the subtree's endpoint ids for
    stream-ref resolution — neither reports the subtree's own coverage
    findings the way this function must.

    Called directly by a caller that already knows `documents` is a pipeline
    bundle — this module never inspects a document set's shape to decide what
    kind of thing it is; the caller (or the wire-schema wrapper in front of
    it) declares that before this function is ever reached. Neither this
    function nor `validate_connector_tree` guesses which package kind it was
    handed, so there is no detecting entry point over them. That declaration
    is not a promise this function takes on faith either: unlike
    `validate_connector_tree`'s fixed `"connector.json"` key, this route's
    root is found by pattern — any key matching
    `pipelines/<slug>/pipeline.json` — so it must also define what a
    *pattern* root needs that a fixed key does not. No key matching that
    pattern — the one document every other check here needs to run at all —
    is reported as a `missing-package-root` finding (`fail`/`error`, path the
    literal pattern `"pipelines/*/pipeline.json"`, mirroring how the
    sibling's fixed key names itself) rather than validated as an empty,
    trivially-passing bundle. More than one key matching the pattern is
    rejected too, as its own `ambiguous-package-root` finding
    (`fail`/`error`, path a sorted, comma-joined list of every matching key)
    rather than one being picked by iteration order — the same silent-pick
    hazard `normalized-key-collision` above exists to prevent, here extended
    to a root a fixed key has no equivalent hazard for. Exactly one match is
    required to reach any further check: a pipeline-root document that is
    present but does not itself validate as a pipeline document is rejected
    the same way as a missing or ambiguous root — its presence under that key
    is not, on its own, enough to call the bundle valid. Because the root is
    found by matching rather than looked up directly, which key resolves it
    must be independent of `documents`' iteration order the same way
    `validate_connector_tree`'s own finding output already is.

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_pipeline_tree is not implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")


def validate_doc(
        doc: Any,
        # Named and typed here because they are ordinary document mode's
        # contract — the keywords a caller passes — and that mode raises below
        # rather than reading them.
        entity: Entity | None = None,  # skipcq: PYL-W0613
        schema_url: str | None = None,  # skipcq: PYL-W0613
        *,
        direction: Literal["read", "write"] | None = None,
        probes: list[str] | None = _OMITTED) -> ValidationEnvelope:
    """Validate one document — replacing the pipeline-builder plugin's private
    `diagnostics_for()`, whose explicit `entity` argument this keeps, and
    covering what that plugin's separate `type_map_gaps.py` CLI does, since a
    type map is a document like any other this module validates rather than a
    third kind of thing alongside "document" and "package". A package is validated
    by `validate_connector_tree` or `validate_pipeline_tree`, whichever the
    caller declares it is sending; everything else, type maps included,
    comes through here.

    Ordinary document mode (`probes` omitted, the default): dispatches `doc`
    to `analitiq.validator.validate_document`, passing `schema_url` through,
    and wraps the returned findings list in a `ValidationEnvelope`
    (`{"passed": finding_costs_a_pass`-reduction, "findings": ...}`), the
    same wrapping the plugin's own `diagnostics_for` already does over
    `validate_document`'s list result today. `validate_document` takes no
    `entity` parameter today — how `entity` reaches its own document-kind
    detection through this route, if at all, is implementation, not fixed by
    this signature. Unlike `direction` and `probes` below, `entity` is
    deliberately not given a dedicated runtime-rejection finding here: this
    signature does not fix what `entity` does downstream at all, so it has no
    basis of its own for calling any particular value "invalid" — that
    verdict belongs wherever `entity` is actually consumed, not to this
    parameter's type hint.

    `direction` still pairs with `entity` the way `diagnostics_for` already
    required in this mode: `entity == "type-map"` needs a `direction` to know
    which of `TypeMapReadDoc`/`TypeMapWriteDoc` to check `doc` against, and
    no other `entity` value takes one. `diagnostics_for` enforced that
    pairing by raising `ValueError`; this function reports it instead — the
    same `invalid-direction` finding the gap-resolution mode below reports —
    for a `direction` that is missing or invalid while `entity == "type-map"`,
    or given at all while `entity` is anything else. How an accepted
    `direction` then reaches `validate_document`'s own read/write selection
    (today keyed off a filename this path-free route has none of, with
    `schema_url` only a fallback hint) is implementation, the same way
    `entity`'s routing above is.

    This route never inspects `doc`'s shape to decide anything about
    `probes`/`direction` — the two modes below are selected only by whether
    the caller passed `probes` at all, never by sniffing `doc` itself, so
    there is no shape-guessing anywhere in this function. Mode selection
    reads whether the argument was passed, not what value it was passed:
    `probes`'s default is a private sentinel, not `None`, so a caller passing
    `probes=None` explicitly has already selected gap-resolution mode and
    reaches the `invalid-probes` rejection below the same as any other
    non-list value — only an omitted `probes` argument selects ordinary mode.
    (What a wire caller sends to select a mode, once this is wrapped by a
    published schema, is that wrapper's concern, not this function's.)

    Type-map gap-resolution mode (`probes` given): the path-free form of
    `type_map_gaps.py`'s own probe resolution. `doc` is then one or more
    type-map documents in precedence order — the same `DocumentSet` shape
    `validate_connector_tree`/`validate_pipeline_tree` take, keyed and
    ordered the same way (the first key whose map renders a probe wins) —
    and `direction` selects which of
    `TypeMapReadDoc`/`TypeMapWriteDoc` (`analitiq.contracts.type_map`) each
    map is checked against. `direction` is validated first, before `doc` is
    even normalized: `Literal["read", "write"]` is a type-checker-only
    promise, and a runtime caller passing anything else (including omitting
    it while still passing `probes`) gets a single `invalid-direction`
    finding (`fail`/`error`, no `direction` field of its own — the value that
    would go there is the very thing rejected) rather than every non-`"read"`
    value being silently treated as `"write"`. `probes` gets the same
    treatment right after: `list[str]` is a type-checker-only promise too, and
    a runtime `probes` that is not a list — `None` included, since only an
    omitted `probes` argument selects ordinary mode instead of ever reaching
    this check — or a list holding anything other than a `str`, gets a single
    `invalid-probes` finding (`fail`/`error`, no `direction` field), checked
    before `doc` is touched. `type_map_gaps.py` rejects the same shape, also
    before it resolves anything, though it reads its probes from stdin rather
    than from an argument. `entity`/`schema_url` play no part in this mode.

    A `doc` that is not a mapping — a `list`, a `str`, anything with no keys
    to hold a map under — supplies no keyed maps at all, and is rejected with
    a single `missing-type-map` finding (`fail`/`error`, no `direction`)
    before any key is read. A mapping that supplies no map this `direction`
    would consult reaches the same finding from the other end, after the keys
    have been read; `missing-type-map` is stated once, below, for both.

    Beyond the `invalid-direction`/`invalid-probes`/`missing-type-map`
    rejections above and the key/value hazards every `DocumentSet` route
    shares (`DocumentSet`/`DocumentSetValue` state them), this mode's own
    finding kinds are: `direction-filename-mismatch` (`fail`/`error`,
    `direction` = this call's `direction` — a map whose load-bearing filename
    refuses it for this call; checked before that map's content is read, and
    that map not used to resolve anything). A key's *load-bearing filename* is
    its final `/`-separated segment, so a scoped key like
    `"connections/foo/type-map-read.json"` is recognized as the read map it is
    rather than passing unchecked. It is matched against the filename
    `analitiq.validator.connectors`'s `_DIRECTIONS` records for each
    direction, which is where the filenames themselves live — this mode names
    that table rather than spelling either filename again.

    A filename refuses a map two ways. It names the *other* direction, under a
    call `direction` that contradicts it. Or it is the single-map filename
    predating the read/write split, which names no direction at all: that one
    is refused whichever direction is asked for. `RULE-PKG-030` rejects that
    filename outright rather than reading it as a read map, because a package
    still carrying it has a write direction nobody has separated out; a
    path-free caller is owed that verdict too, not a quieter one reached by
    handing the documents over without a directory. Any other final segment
    carries no expectation, so the caller's declared `direction` is the only
    claim in hand and the map is consulted under it.

    `_validate_type_map` in that module answers a different question and so
    reaches a different verdict: it has no caller-declared direction, only a
    lone sibling file, so it asks which direction the file is most plausibly
    in and reports an informational `type-map-direction-defaulted` when the
    filename settles nothing. This mode has a declared `direction` for the
    filename to contradict, which is a mismatch rather than an ambiguity —
    the same question `type_map_gaps.py`'s `--direction` asks, and it too
    refuses the map rather than guessing.

    `type-map-unreadable` (`fail`/`error`, no `direction`) — a map whose text
    is not JSON, whose content is not an array of rules, or which is JSON this
    interpreter will not build a value from (an integer literal past
    `int_max_str_digits`, nesting past the recursion limit). Each is its own
    message: the parser's own complaint, position included, for the first,
    what was found instead for the second, and what the interpreter refused
    for the third — which is a limit of the reader rather than a defect in the
    document, so it is neither called a syntax error nor blamed on the
    validator. A map that parses into
    an array is then validated by `connectors._type_map_findings`, the
    definition every other type-map check in this package already goes
    through, so a map's model errors arrive here attributed to the rule
    records that claim them rather than flattened into one finding of this
    mode's own. Those findings keep their own `path` within the document,
    prefixed by the key the map was supplied under, since more than one map
    can be in hand. A map whose model findings cost a pass contributes no
    rules; one whose findings are all advisory still does.

    `type-map-gap` (`informational`, no severity, no rule, `direction` = this
    call's `direction`, `path` the empty string — a probe nothing in `doc`
    resolved; never costs a pass). A gap is a fact about a probe against the
    whole supplied set rather than about any one map, so there is no key to
    name: attributing it to one would implicate a map doing nothing wrong.
    The probe itself is named in the message.

    A map its filename refuses is never consulted, and says so with
    `direction-filename-mismatch`. Refusing rather than trying it is what keeps
    a wrong answer from looking like a right one: read and write rules share
    one key set and the resolvers only choose which key is matched and which is
    rendered, so a map authored for the other mapping does not fail to resolve
    a probe — it resolves it, plausibly and wrongly, which is the one outcome a
    caller cannot detect. A map whose direction was never declared is refused
    for the same reason rather than the same evidence: its rules may well be
    authored for the direction asked for, but nothing in hand says so, and a
    probe answered on that basis is right by luck. Either way the filename is
    the declaration of intent available, and it is taken at its word. The same
    holds for a key lost before anything could read it: a lost
    `type-map-write.json` costs a `read` call nothing, because that call would
    not have consulted it.

    Gaps are reported only when the set could answer the question — no map
    this `direction` would have consulted was lost, and at least one was
    actually consulted:

    - A map that would have been consulted and was not usable — a rejected or
      colliding key, an unreadable map, one whose model errors cost a pass, a
      crash — withholds every gap, and a `gap-resolution-skipped` finding
      (`notApplicable`, no severity, no rule, `path` the empty string) says so.
      A probe called unresolved against what is left may be covered by exactly
      the map that went missing, and `type_map_gaps.py`'s `_load_rules` raises
      for that reason: a false gap is indistinguishable from a real one and
      sends an author to add a rule their map already had.
    - A set that supplies no map for this `direction` at all — empty, or
      holding only maps their filenames refuse — reports
      `missing-type-map` and no gaps. Every probe would otherwise come back
      unresolved against nothing, which is the same false report in its
      starkest form.

    Both say what happened because an absence of `type-map-gap` findings is
    what full coverage looks like too, and an envelope that cannot be told
    apart from a clean run is the failure this mode exists to prevent.

    `internal-error` (`notApplicable`, no `severity`, no `rule` — something
    this mode did crashed). A crash settles nothing about whether the map or
    probe it was reading is valid, which is what `notApplicable` reports and
    what `fail` would misstate; `analitiq.validator._core`'s `_run_guarded`
    classifies a crashing check the same way and for the same reason. Naming
    no rule, it still costs a pass. Each site carries what it can name:
    canonicalizing the keys (`path` empty), reading one map (`path` that map's
    key, so the rest of `doc` is still read), resolving one probe (`path`
    empty, `direction` set, so the remaining probes are still resolved), and
    the guard around the whole mode (`path` empty), which reports what the
    narrower ones did not reach. A crash reading a map means a map was lost,
    so it withholds gaps the way any other lost map does.

    Every map `doc` carries that this `direction` admits is read and checked
    whether or not a probe needed it, matching `type_map_gaps.py`'s
    `_load_rules`, which runs over every `--map` before a single probe
    resolves. So a malformed map is reported even where an earlier map
    already covers every probe, and a call whose `probes` list is empty is
    still a check of the maps rather than a trivially-passing no-op. A run
    whose maps raise nothing advisory and whose every probe resolved reports a
    `ValidationEnvelope` with `passed: True` and an empty `findings` list; one
    whose maps are usable but carry warnings reports those and still passes.
    Either way it is the same envelope shape as every other call to this
    function, never a bare `{"findings"}` shape with no `passed` key.

    Once this function is entered, every `Exception` it reaches is a reported
    finding rather than a raise — `internal-error` is the last-resort form of
    that, and the mode's body runs inside a guard of its own, so it covers the
    arguments nobody anticipated as well as the ones the rejections above name.
    The rule is `Exception`, which is what bounds it: a `BaseException` is
    caught nowhere here and propagates, whether it is an interrupt or one a
    caller's own `__str__` or `__hash__` chose to raise. So does a
    `RecursionError` arriving with no stack left to build the finding on, since
    a caller already close to the limit has spent the frames the handler needs.
    Reporting also says nothing about importing this package: a missing
    `analitiq-contract-models` is reported by `analitiq.validator.connectors`
    at import time, before any call here.

    Reporting rather than raising is a deliberate divergence from
    `type_map_gaps.py`'s `_load_rules`, which raises `ValueError` on an
    unreadable or model-invalid map: a path-free caller has no filename to
    name in a raised message the way the plugin's CLI does, and folding those
    conditions into the findings list this function already returns means a
    caller checks one place for every way a probe run can come back
    incomplete.

    Ordinary document mode raises `NotImplementedError`; gap-resolution mode
    is implemented. Both modes' behaviour is fixed by
    `packages/validator/tests/test_document_set.py`.
    """
    if probes is _OMITTED:
        raise NotImplementedError(
            "validate_doc's ordinary document mode is not implemented — see "
            "packages/validator/tests/test_document_set.py for the fixed contract.")
    try:
        return _type_map_gap_mode(doc, direction, probes)
    except Exception as exc:  # noqa: BLE001 - the closure guarantee
        # The inner guards cover the work; this one covers the reporting around
        # it, so the guarantee holds for every argument rather than for every
        # argument someone thought of.
        return _envelope([_crashed("type-map gap resolution", "", exc)])


def _crashed(doing: str, path: str, exc: BaseException) -> Finding:
    """This module's crash finding, built by `_core.crash_finding` so it reads
    identically to the one the path-based route reports — one wording, which is
    what the claim probes that locate a crash finding match on."""
    return crash_finding(message_id="internal-error", doing=doing, path=path, exc=exc)


def _at_key(f: Finding, key: str) -> Finding:
    """Re-path a finding raised about a document's own content onto the key
    that document was supplied under, so a set holding several maps says which
    one a model error came from."""
    within = f.get("path", "")
    return {**f, "path": key if within in ("", "/") else f"{key}{within}"}


def _map_rules(key: str, value: Any, direction: str) -> tuple[list | None, list[Finding]]:
    """The rules one supplied map contributes, or `None` where it contributes
    none, alongside everything to report about it."""
    from .connectors import _type_map_findings

    content, unread, detail = _document_content(value)
    if unread == "invalid-type":
        return None, [finding(
            message_id="invalid-value", kind="fail", path=key,
            message=(f"{_repr(key)} holds a value of type {detail}; a document set value "
                     "is the parsed document as a dict or list, or the JSON text as str "
                     "or bytes."))]
    if unread == "not-utf8":
        return None, [finding(
            message_id="invalid-value", kind="fail", path=key,
            message=f"{_repr(key)} holds bytes that are not UTF-8: {detail}.")]
    if unread == "unparseable":
        return None, [finding(
            message_id="type-map-unreadable", kind="fail", path=key,
            message=f"{_repr(key)} is not JSON: {detail}.")]
    if unread == "over-limit":
        return None, [finding(
            message_id="type-map-unreadable", kind="fail", path=key,
            message=(f"{_repr(key)} is JSON this interpreter will not build a value "
                     f"from: {detail}."))]
    if not isinstance(content, list):
        return None, [finding(
            message_id="type-map-unreadable", kind="fail", path=key,
            message=(f"{_repr(key)} is not an array of type-map rules; it holds a value "
                     f"of type {_type_name(content)}."))]
    model = [_at_key(f, key) for f in _type_map_findings(content, direction)]
    if any(finding_costs_a_pass(f) for f in model):
        return None, model
    return content, model


def _type_map_gap_mode(doc: Any, direction: Any, probes: Any) -> ValidationEnvelope:
    """`validate_doc`'s type-map gap-resolution mode — see its docstring for
    the contract this implements.

    The matcher is whatever `connectors._DIRECTIONS` records for the
    direction, so this holds no resolution logic of its own; `type_map_gaps.py`
    reaches the same matchers by naming them directly, which is why a probe
    resolves identically either way. What this adds over that CLI is reporting
    in place of raising, and one envelope in place of its
    `{"direction", "resolved", "gaps"}` result.
    """
    from .connectors import _DIRECTIONS, _LEGACY_MAP_FILENAME

    if not isinstance(direction, str) or direction not in _DIRECTIONS:
        return _envelope([finding(
            message_id="invalid-direction", kind="fail", path="",
            message=("type-map gap resolution needs a direction of 'read' or 'write'; "
                     f"got {_repr(direction)}."))])
    if not isinstance(probes, list) or not all(isinstance(p, str) for p in probes):
        return _envelope([finding(
            message_id="invalid-probes", kind="fail", path="",
            message=f"probes must be a list of native-type strings; got {_repr(probes)}.")])
    if not isinstance(doc, dict):
        return _envelope([finding(
            message_id="missing-type-map", kind="fail", path="",
            message=("type-map gap resolution reads its maps from the keys of `doc`, "
                     f"which must be a dict; got a {_type_name(doc)}."))])

    resolve = _DIRECTIONS[direction].resolve
    declared_by_filename = {ops.filename: name for name, ops in _DIRECTIONS.items()}

    def filename_refusal(name: str) -> str | None:
        """Why `name`'s load-bearing filename disqualifies it from resolving a
        probe in this direction — the rest of a sentence that opens with the
        key — or `None` where nothing does."""
        by_name = name.rsplit("/", 1)[-1]
        declared = declared_by_filename.get(by_name)
        if declared is not None and declared != direction:
            return (f"is a {declared} map by its load-bearing filename, but this call "
                    f"resolves in the {direction} direction; it is not used to resolve "
                    "any probe.")
        if by_name == _LEGACY_MAP_FILENAME:
            return ("carries the single-map filename that predates the read/write split, "
                    "so nothing declares which direction its rules are authored for and "
                    "it is not used to resolve any probe; key it under the filename for "
                    "the direction it renders.")
        return None

    try:
        maps, findings, dropped = _normalized_documents(doc)
    except Exception as exc:  # noqa: BLE001 - the caller's own mapping and keys
        return _envelope([_crashed("canonicalizing the document set's keys", "", exc)])

    # A key this set could not use may have been the very map a probe needs,
    # and nothing read it, so nothing can say otherwise — unless its filename
    # refuses it anyway, in which case it would not have been consulted even
    # had it survived.
    lost = any(filename_refusal(name) is None for name in dropped)

    rules: list = []
    consulted = 0
    for key, value in maps.items():
        refusal = filename_refusal(key)
        if refusal is not None:
            findings.append(_directed(finding(
                message_id="direction-filename-mismatch", kind="fail", path=key,
                message=f"{_repr(key)} {refusal}"),
                direction))
            continue
        try:
            contributed, reported = _map_rules(key, value, direction)
        except Exception as exc:  # noqa: BLE001 - last-resort per-map guard
            findings.append(_crashed(f"reading {_repr(key)}", key, exc))
            lost = True
            continue
        findings.extend(reported)
        if contributed is None:
            lost = True
        else:
            rules.extend(contributed)
            consulted += 1

    if lost:
        findings.append(finding(
            message_id="gap-resolution-skipped", kind="notApplicable", path="",
            message=("no probe was resolved: a map this set was handed could not be used, "
                     "and a probe unresolved without it may be one that map covers.")))
        return _envelope(findings)
    if not consulted:
        findings.append(finding(
            message_id="missing-type-map", kind="fail", path="",
            message=(f"type-map gap resolution needs at least one {direction} map keyed "
                     "in `doc`; this set supplies none.")))
        return _envelope(findings)

    # Deduped so a probe asked for twice is answered once; the CLI dedupes its
    # own probe list the same way.
    for probe in dict.fromkeys(probes):
        try:
            # Guarded separately from the map loop because this is the one step
            # validating the maps did not already perform: running a matcher.
            # Every value the matcher reads was read while the map was checked,
            # so a malformed map crashes there instead — what is left here is
            # the matching itself, a regex run against a probe.
            rendered = resolve(probe, rules)
        except Exception as exc:  # noqa: BLE001 - last-resort per-probe guard
            findings.append(_directed(
                _crashed(f"resolving probe {_repr(probe)}", "", exc), direction))
            continue
        if rendered is None:
            findings.append(_directed(finding(
                message_id="type-map-gap", kind="informational", path="",
                message=f"no supplied {direction} map resolves {_repr(probe)}."), direction))
    return _envelope(findings)
