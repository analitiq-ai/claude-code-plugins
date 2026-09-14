"""The path-free document-set API — types and signatures, not yet implemented.

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
have occupied on disk, in and out. Every function below raises
`NotImplementedError` — the behaviour they must satisfy is fixed by
`packages/validator/tests/test_document_set.py`, whose fixture cases are
`xfail` until an implementation PR replaces these bodies and removes the
markers one case at a time.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict, Union

#: The value half of a `DocumentSet` entry: text, raw bytes (decoded as UTF-8
#: with a BOM stripped if present — no encoding-guessing), or a value already
#: parsed into the `dict`/`list` shape `analitiq.validator.validate_document`
#: itself expects for `doc`. A value that is none of these is reported as an
#: `invalid-value` finding on its own key, never raised.
DocumentSetValue = Union[str, bytes, dict, list]

#: A path-free bundle of documents: POSIX-relative-path-shaped string keys
#: (e.g. `"connections/foo/connection.json"`, `"endpoints/widgets.json"`) mapped
#: to already-loaded content. A leading `./` is normalized away; an absolute
#: key, a key containing `..`, or the empty string is reported as an
#: `invalid-key` finding rather than raised; two keys where one names both a
#: document and a directory prefix of another (e.g. `"a/b.json"` alongside
#: `"a/b.json/c.json"`) are reported as `key-path-conflict` instead; two
#: distinct raw keys that normalize to the same canonical key — the collision
#: leading-`./`-stripping can itself create, e.g. `"connector.json"` alongside
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
        "validate_connector_tree is not yet implemented — see "
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
        "validate_pipeline_tree is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")


def validate_doc(
        doc: Any,
        entity: Entity | None = None,
        schema_url: str | None = None,
        *,
        direction: Literal["read", "write"] | None = None,
        probes: list[str] | None = _OMITTED) -> ValidationEnvelope:
    """Validate one document — replacing the pipeline-builder plugin's private
    `diagnostics_for()`, whose explicit `entity` argument this keeps, and
    folding in that plugin's separate `type_map_gaps.py` CLI, since a type map
    is a document like any other this module validates rather than a third
    kind of thing alongside "document" and "package". A package is validated
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
    before `doc` is touched — mirroring the same check `type_map_gaps.py`'s
    own CLI argument parsing runs before it ever resolves a probe.
    `entity`/`schema_url` play no part in this mode.

    A `doc` with no key at all is rejected too, before any probe is resolved:
    `type_map_gaps.py`'s own `--map` is `required=True`, and an empty `doc`
    would otherwise report every probe as an informational `type-map-gap` and
    still report `passed: True` — silently treating "no map was supplied" the
    same as "the supplied maps have this gap". This mode reports a single
    `missing-type-map` finding instead (`fail`/`error`, no `direction`) and
    resolves no probes. A `doc` that is not even a mapping — a `list`, a
    `str`, anything with no keys to hold a map under — supplies no keyed maps
    either, and is rejected the same way: one `missing-type-map` finding, no
    probes resolved, checked before any key is read.

    Beyond the `invalid-direction`/`invalid-probes`/`missing-type-map`
    rejections above and the key/value hazards every `DocumentSet` route
    shares (`DocumentSet`/`DocumentSetValue` state them), this mode's own
    finding kinds are: `direction-filename-mismatch` (`fail`/`error`,
    `direction` = this call's `direction` — a map keyed under a load-bearing
    filename for the *other* direction, present under a call `direction` that
    contradicts it; checked before that map's content is read, and that map
    excluded from resolution the same way `type-map-unreadable` below is). A
    key's *load-bearing filename* is its final `/`-separated segment (a bare
    key with no `/` is its own segment — this mode's own fixture keys, all
    top-level, are), matched against `analitiq.validator.connectors`'s
    `_READ_MAP_FILENAME`/`_WRITE_MAP_FILENAME`
    (`"type-map-read.json"`/`"type-map-write.json"`) — the same two constants
    `_validate_type_map` there already holds a sibling file's direction to,
    so this mode names them rather than retyping the filenames as a second,
    independently-spelled pair. A key whose final segment is neither constant
    carries no such expectation and cannot mismatch.

    This mode's own check diverges from `_validate_type_map`'s in one
    respect, deliberately: `_validate_type_map` treats a name/direction
    mismatch as ambiguity, not error — it defaults to `"read"` and reports an
    informational `type-map-direction-defaulted` finding, never failing the
    document. This mode instead mirrors `type_map_gaps.py`'s own CLI, whose
    `load_bearing` check hard-fails the same mismatch before any map is even
    read. The two are answering different questions:
    `_validate_type_map` asks "what direction is this lone sibling file most
    plausibly in", with no caller-declared direction to check it against;
    this mode asks "does this caller-declared `direction` hold for every map
    it was handed" — `type_map_gaps.py`'s own `--direction` question, which
    has a declared direction to contradict and so has no ambiguous case to
    default.

    `type-map-unreadable` (`fail`/`error`, no `direction` — a map that is
    invalid JSON or not a list, a failure prior to any direction-specific
    check); `invalid-type-map` (`fail`/`error`, `direction` = this call's
    `direction` — a map that fails its type-map model); `type-map-gap`
    (`informational`, no severity, no rule, `direction` = this call's
    `direction` — a probe none of `doc` resolved; never costs a pass);
    `internal-error` (`fail`/`error`, no `direction` — one map's resolution
    crashed; isolated to that map's key the same way
    `validate_connector_tree`'s and `validate_pipeline_tree`'s own per-key
    crash isolation works, so one bad map does not stop the rest of `doc`
    from being resolved). A run in which every probe resolved reports a
    `ValidationEnvelope` with `passed: True` and an empty `findings` list —
    this mode reports through the same envelope shape as every other call to
    this function, never a bare `{"findings"}` shape with no `passed` key.
    This mode never raises: every failure above, `internal-error` included,
    is a reported finding, the same guarantee
    `validate_connector_tree`'s and `validate_pipeline_tree`'s own per-key
    isolation make for a `DocumentSet`.

    Reporting rather than raising is itself a deliberate divergence from
    `type_map_gaps.py`'s own `_load_rules`, which raises `ValueError` on
    `type-map-unreadable`'s and `invalid-type-map`'s two conditions (an
    unreadable map, a map failing its model): a path-free caller has no
    filename to name in a raised message the way the plugin's CLI does, and
    folding both conditions into the same findings list this function already
    returns means a caller checks one place for every way a probe run can
    come back incomplete.

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_doc is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")
