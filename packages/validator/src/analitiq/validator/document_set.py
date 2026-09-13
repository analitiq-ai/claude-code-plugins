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
#: `invalid-key` finding rather than raised. Every function below that takes or
#: produces a document set uses this one shape, so `validate_tree`'s
#: `documents` and `resolve_type_map_gaps`'s `maps` share one value type rather
#: than two independently-typed mappings.
DocumentSet = dict[str, DocumentSetValue]

#: This API's document-kind vocabulary — a literal tuple, not a runtime import
#: of `DOCUMENT_ARTIFACT_KINDS`: a `Literal`'s members must be spelled out for
#: the type checker, which a name resolved at import time is not. Mirrors
#: `plugins/analitiq-pipeline-builder/scripts/validate.py`'s `PIPELINE_ENTITIES`,
#: which takes the same approach for the same reason and is pinned the same
#: way. `test_document_set.py::test_entity_matches_document_artifact_kinds`
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
    """`finding()` sets these four unconditionally on every result it builds —
    see `Finding`."""

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


def validate_connector_tree(documents: DocumentSet) -> ValidationEnvelope:
    """Validate a connector package supplied as an in-memory `DocumentSet`
    instead of files on disk: the connector document, its sibling type maps,
    and its `endpoints/*.json` files, cross-checked the way
    `analitiq.validator.check_coverage` already does from a filesystem path.

    This is the connector package-kind's root-shape entry, called directly —
    it never walks the registry `validate_tree` walks, so it can never itself
    report an `ambiguous-layout` or `unrecognized-layout` finding.

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_connector_tree is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")


def validate_pipeline_tree(documents: DocumentSet) -> ValidationEnvelope:
    """Validate a pipeline bundle supplied as an in-memory `DocumentSet`
    instead of files on disk: the pipeline document, its sibling
    `streams/*.json`, every `connections/*/connection.json` (plus their scoped
    endpoints and type maps), and any `connectors/<slug>/definition/...`
    subtree — assembled the way `plugins/analitiq-pipeline-builder/scripts/
    validate.py`'s `_assemble_bundle` already does from a filesystem root, then
    checked for referential integrity.

    A `connectors/<slug>/definition/...` subtree is validated by resolving it
    against the same package-kind registry `validate_tree` walks, scoping its
    findings' `path` under the subtree's key prefix — so an embedded
    connector's own coverage findings (native-type coverage, `transport_ref`
    resolution, duplicate endpoint ids) are reported, not only its endpoint
    ids read for stream-ref resolution the way the plugin's
    `_connector_endpoint_sets` reads them today.

    This is the pipeline package-kind's root-shape entry, called directly — it
    never walks the registry, so it can never itself report an
    `ambiguous-layout` or `unrecognized-layout` finding.

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_pipeline_tree is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")


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

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "validate_tree is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")


def diagnostics(target: Any, entity: Entity | None = None) -> ValidationEnvelope:
    """Auto-detect whether `target` is a single already-parsed document or a
    `DocumentSet`, and dispatch to `analitiq.validator.validate_document` or
    `validate_tree` accordingly — replacing the pipeline-builder plugin's
    private `diagnostics_for()`, which takes an explicit `entity` argument
    naming the document's kind rather than detecting it. `entity` plays no
    part in detecting or validating a document SET, which is identified by
    its shape alone. What it does for the single-document route is not
    defined by this specification: `validate_document` takes no such
    parameter today, and wiring `entity` into that route (or into whichever
    replaces it) is implementation, not contract — out of scope here the same
    way implementing this function's body is.

    Wraps a single-document result in a `ValidationEnvelope` (`{"passed":
    finding_costs_a_pass`-reduction, "findings": ...}`) rather than returning
    `validate_document`'s bare list, so a caller gets one result shape
    regardless of which route this dispatched to — the same wrapping the
    plugin's own `diagnostics_for` already does over `validate_document`'s
    list result today.

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "diagnostics is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")


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

    Not yet implemented — raises `NotImplementedError`. Signature and
    behaviour are fixed by `packages/validator/tests/test_document_set.py`.
    """
    raise NotImplementedError(
        "resolve_type_map_gaps is not yet implemented — see "
        "packages/validator/tests/test_document_set.py for the fixed contract.")
