"""Stream models and validators (schema v1)."""
from __future__ import annotations

from typing import Annotated, Any, Literal, get_args
from pydantic import (
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    field_validator,
    model_validator,
)

from analitiq.contracts.endpoints import (
    ARROW_TYPE_PATTERN,
    WRITE_MODES,
    DatabaseObject,
    WriteMode,
)
from analitiq.contracts.endpoint_identity import derive_db_endpoint_id
from analitiq.contracts.shared.filter_operators import (
    API_FILTER_OPERATORS,
    DB_FILTER_OPERATORS,
    UNARY_FILTER_OPERATORS,
    FilterOperator,
)
from analitiq.contracts.shared.rules import find_duplicates, violation
from analitiq.contracts.shared.arrow_shape import (
    ARROW_CONTAINER_SCHEMA_RULES,
    enforce_container_shape,
)
from analitiq.contracts.shared.common import (
    DESCRIPTION_MAX,
    DISPLAY_NAME_MAX,
    DISPLAY_NAME_MIN,
    NO_EDGE_WHITESPACE_PATTERN,
    BatchSize,
    NonEmptyStr,
    RetryErrorHandlingBase,
    StrictModel,
    TAGS_MAX,
    TrimmedTag,
    schema_url_for,
    set_derived_field,
    validate_display_name,
    validate_tags,
)
from analitiq.contracts.shared.types import (
    UUID_PATTERN,
    StrictNonNegativeInt,
    StrictPositiveInt,
)


STREAM_SCHEMA_URL = schema_url_for("stream")

SCOPE_CONNECTOR = "connector"
SCOPE_CONNECTION = "connection"




def _check_unique_destinations(
    destinations: list["StreamDestination"],
) -> list["StreamDestination"]:
    """Reject duplicate destinations by `(scope, connection_id, endpoint_id)`.

    RULE-STRM-001, applied to the authored contract by
    `StreamAuthored._destinations_unique` and importable here for a downstream
    caller that holds a destination list without the stream around it. One
    definition, so the two callers cannot disagree about what a duplicate is.
    """
    dups = find_duplicates(
        destinations,
        key=lambda d: (
            d.endpoint_ref.scope,
            d.endpoint_ref.connection_id,
            d.endpoint_ref.endpoint_id,
        ),
    )
    if dups:
        raise violation("RULE-STRM-001", f"duplicates={dups!r}")
    return destinations


# ---------------------------------------------------------------------------
# Endpoint reference
# ---------------------------------------------------------------------------


class _EndpointRefBase(StrictModel):
    """Fields shared by both endpoint-reference variants.

    `endpoint_id` lives on each variant, not here: a `connector` ref's id is
    the connector registry key you author, while a `connection` ref's id is a
    handle derived from `database_object`.
    """

    connection_id: NonEmptyStr = Field(
        ...,
        description=(
            "Connection reference selected in the parent pipeline. Typically a "
            "versioned connection ID (e.g. 'uuid_v1'); the schema accepts any "
            "non-empty string — engines resolve the reference at runtime."
        ),
        examples=["00000000-0000-4000-8000-000000000001_v1"],
    )


class ConnectorEndpointRef(_EndpointRefBase):
    """Public connector endpoint reference (`scope='connector'`).

    Pinned by the connection's connector_version. Carries NO `database_object`
    — an API endpoint's locator lives in its endpoint document
    (`operations.*.request.path`), not on the ref.
    """

    scope: Literal["connector"] = Field(
        ..., description="Endpoint reference scope; always 'connector' here."
    )
    endpoint_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Connector endpoint identifier — the registry key selected from "
            "endpoint discovery (e.g. 'transfers'). Client-authored."
        ),
    )


class ConnectionEndpointRef(_EndpointRefBase):
    """Private connection-scoped database endpoint reference (`scope='connection'`).

    Carries the verbatim provider-native object locator in `database_object`.
    `endpoint_id` is an opaque handle DERIVED from that locator by
    `analitiq.contracts.endpoint_identity.derive_db_endpoint_id` — a pure
    function shipped in this package, so you can compute it yourself. It is
    never decoded back to a target. The locator is the identity; hence it is
    REQUIRED and non-null here.
    """

    scope: Literal["connection"] = Field(
        ..., description="Endpoint reference scope; always 'connection' here."
    )
    database_object: DatabaseObject = Field(
        ...,
        description=(
            "Verbatim provider-native object locator (catalog/schema/name), "
            "round-tripped from endpoint discovery. Required — the opaque "
            "`endpoint_id` cannot be parsed for identity."
        ),
    )
    endpoint_id: str | None = Field(
        default=None,
        description=(
            "Opaque handle derived from `database_object` "
            "(`slug(schema)__slug(table)[__slug(catalog)]__<hash8>`). Omit it "
            "and it is derived from the locator; supply it and it must equal "
            "the derived handle."
        ),
    )

    @model_validator(mode="after")
    def _derive_or_verify_endpoint_id(self) -> "ConnectionEndpointRef":
        # `endpoint_id` is a pure function of the verbatim locator via the single
        # shared `derive_db_endpoint_id`, so there is no second implementation to
        # drift. Omitted → derive; supplied → verify it matches, fail loud on a
        # mismatch.
        obj = self.database_object
        canonical = derive_db_endpoint_id(obj.catalog, obj.schema_, obj.name)
        if self.endpoint_id is None:
            set_derived_field(self, "endpoint_id", canonical)
        elif self.endpoint_id != canonical:
            raise ValueError(
                f"endpoint_id {self.endpoint_id!r} does not match the id derived "
                f"from database_object ({canonical!r}); it is derived from the "
                "locator and cannot be chosen independently"
            )
        return self


# Structured endpoint reference shared by source + destination sides, as a
# `scope`-discriminated union. The union structurally enforces "a `connection`
# ref carries a (non-null) `database_object`; a `connector` ref carries none" in
# BOTH the pydantic model and every generated artifact — the published JSON
# Schema renders a `oneOf` with a `scope` discriminator, and the
# @analitiq-ai/contracts Zod codegen preserves discriminated unions (unlike the
# `allOf if/then/else` conditional it used to strip). So external validators
# reject exactly what the service rejects, including the `database_object: null`
# edge (the connection variant has no null branch).
EndpointRef = Annotated[
    ConnectorEndpointRef | ConnectionEndpointRef,
    Field(discriminator="scope"),
]

_ENDPOINT_REF_ADAPTER = TypeAdapter(EndpointRef)


def validate_endpoint_ref(data: Any) -> ConnectorEndpointRef | ConnectionEndpointRef:
    """Validate a raw endpoint_ref dict into its concrete scope variant."""
    return _ENDPOINT_REF_ADAPTER.validate_python(data)


# ---------------------------------------------------------------------------
# Filters (spec §Filters)
# ---------------------------------------------------------------------------


# The filter-operator vocabulary is owned by `shared.filter_operators` — an
# api-endpoint names a landing site for the same members, and `endpoints` cannot
# import this module. `StreamSource` narrows `FilterOperator` to the subset the
# source's scope can carry.


_FILTER_CONDITIONAL_RULES: dict[str, Any] = {
    "allOf": [
        {
            "if": {
                "properties": {"operator": {"enum": list(UNARY_FILTER_OPERATORS)}},
                "required": ["operator"],
            },
            "then": {"not": {"required": ["value"]}},
            "else": {"required": ["value"]},
        },
    ],
    "additionalProperties": False,
}


class Filter(StrictModel):
    """Stream-owned read predicate.

    Endpoint contracts own which fields are filterable and which operators each
    offers. Per spec §Filters, `value` is required except when `operator` takes
    no operand.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_FILTER_CONDITIONAL_RULES,
    )

    field: str = Field(
        ...,
        min_length=1,
        description=(
            "Record field the predicate narrows on. A database source names a "
            "column of the source endpoint's schema; an API source names a "
            "field the endpoint's read `filters` map offers, and that map is "
            "what turns the comparison into a request. Nothing here resolves "
            "the name: the endpoint document that declares it is not part of "
            "the stream, so a name that matches nothing is caught where the "
            "two documents are held together, not by this document alone."
        ),
    )
    operator: FilterOperator = Field(
        ...,
        description="Operator selected from the applicable source capability.",
    )
    value: Any = Field(
        default=None,
        description="JSON value for the predicate; omit for unary operators.",
    )

    @model_validator(mode="after")
    def _validate_value_presence(self) -> "Filter":
        if self.operator in UNARY_FILTER_OPERATORS and self.value is not None:
            raise ValueError(
                f"filters[].value must be omitted for unary operator {self.operator!r}"
            )
        # Non-unary operators can carry `value=None` here because Pydantic
        # cannot distinguish "omitted" from "explicit None"; cross-field
        # checks land at endpoint resolution.
        return self


# ---------------------------------------------------------------------------
# Replication (spec §Replication)
# ---------------------------------------------------------------------------


class _ReplicationBase(StrictModel):
    """Fields shared by both replication variants; `method` selects the variant."""

    safety_window_seconds: StrictNonNegativeInt | None = Field(
        default=None,
        description="Non-negative late-arrival overlap window.",
    )
    tie_breaker_fields: list[str] | None = Field(
        default=None,
        description="Database-only deterministic cursor tie-breaker fields.",
    )


class FullRefreshReplication(_ReplicationBase):
    """Full-refresh replication: each run re-reads the whole source; no cursor."""

    method: Literal["full_refresh"] = Field(
        ...,
        description="Stream-selected replication method.",
    )


class IncrementalReplication(_ReplicationBase):
    """Incremental replication: resume from the last committed `cursor_field` value."""

    method: Literal["incremental"] = Field(
        ...,
        description="Stream-selected replication method.",
    )
    cursor_field: str = Field(
        ...,
        min_length=1,
        description="Source field reference tracking incremental progress.",
    )


# `method`-discriminated union: the incremental variant REQUIRES `cursor_field`
# and the full_refresh variant FORBIDS it (absent under additionalProperties:false).
# The published JSON Schema renders a `oneOf` with a `method` discriminator, so
# external validators reject exactly what the model does — the cross-field rule
# formerly enforced only in a `@model_validator` is now structural.
Replication = Annotated[
    FullRefreshReplication | IncrementalReplication,
    Field(discriminator="method"),
]


# ---------------------------------------------------------------------------
# Database pagination (spec §Database Pagination)
# ---------------------------------------------------------------------------


class _DatabasePaginationBase(StrictModel):
    """Fields shared by both database-pagination variants; `type` selects the variant."""

    page_size: StrictPositiveInt | None = Field(
        default=None,
        description=(
            "Positive integer read page size. Declaring one does not change how "
            "much a read fetches: the size is the pipeline runtime's batching "
            "value for every stream, and no engine release consumes this field."
        ),
    )


class OffsetDatabasePagination(_DatabasePaginationBase):
    """Offset/limit read paging. `order_by_field` is optional."""

    type: Literal["offset"] = Field(
        ...,
        description=(
            "Database pagination strategy. It selects which variant's shape the "
            "document must satisfy; it does not select a read path — every "
            "database source read is offset-paged, whichever variant is declared."
        ),
    )
    order_by_field: str | None = Field(
        default=None,
        min_length=1,
        description="Source field reference for page ordering; optional for offset.",
    )


class KeysetDatabasePagination(_DatabasePaginationBase):
    """Keyset (seek) read paging. `order_by_field` defines the seek order and is required."""

    type: Literal["keyset"] = Field(
        ...,
        description=(
            "Database pagination strategy. It selects which variant's shape the "
            "document must satisfy; it does not select a read path — every "
            "database source read is offset-paged, whichever variant is declared."
        ),
    )
    order_by_field: str = Field(
        ...,
        min_length=1,
        description="Source field reference for page ordering; required for keyset.",
    )


# `type`-discriminated union: the keyset variant REQUIRES `order_by_field`; the
# offset variant leaves it optional (preserving the model's current semantics).
# The published JSON Schema renders a `oneOf` with a `type` discriminator.
DatabasePagination = Annotated[
    OffsetDatabasePagination | KeysetDatabasePagination,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Source (spec §Source)
# ---------------------------------------------------------------------------


class StreamSource(StrictModel):
    """Source endpoint binding and stream-owned read options."""

    endpoint_ref: EndpointRef = Field(..., description="Structured endpoint reference.")
    selected_columns: list[str] | None = Field(
        default=None,
        description="Ordered source field references; database sources only.",
    )
    filters: list[Filter] | None = Field(
        default=None,
        description="Stream-supplied read predicates.",
    )
    replication: Replication | None = Field(
        default=None,
        description=(
            "Stream-selected replication policy. Omission allowed only when "
            "the source supports full_refresh."
        ),
    )
    database_pagination: DatabasePagination | None = Field(
        default=None,
        description=(
            "Database source read-page configuration; database sources only. "
            "Defaults to offset pagination with page size from "
            "pipeline.runtime.batching.batch_size when omitted for database "
            "sources."
        ),
    )
    primary_keys: list[str] | None = Field(
        default=None,
        description=(
            "Stream-owned source identity hint when the endpoint does not "
            "provide primary-key metadata."
        ),
    )

    @model_validator(mode="after")
    def _validate_filter_operator_scope(self) -> "StreamSource":
        # `Filter.operator` structurally allows the union of both vocabularies;
        # which subset is valid depends on the source scope, which only the
        # binding (endpoint_ref) knows. A database (connection) source may use
        # the database operators; an API (connector) source may use the API
        # operators — its finer per-endpoint subset is resolved at runtime.
        if not self.filters:
            return self
        allowed = (
            DB_FILTER_OPERATORS
            if self.endpoint_ref.scope == SCOPE_CONNECTION
            else API_FILTER_OPERATORS
        )
        for filt in self.filters:
            if filt.operator not in allowed:
                raise violation(
                    "RULE-STRM-012",
                    f"filters[].operator {filt.operator!r} is not valid for a "
                    f"{self.endpoint_ref.scope} source "
                    f"(allowed: {sorted(allowed)})",
                )
        return self

    @model_validator(mode="after")
    def _validate_database_only_read_features(self) -> "StreamSource":
        # `selected_columns`, `replication.tie_breaker_fields` and
        # `database_pagination` describe how a database read is shaped; an API
        # (connector-scope) read has none of them to configure, and the
        # structural types cannot see the source scope — only the binding
        # (endpoint_ref) knows it, like `_validate_filter_operator_scope`
        # above (RULE-STRM-012). This check is RULE-STRM-014. `is not None`
        # rather than truthiness: declaring an empty list is still declaring
        # the feature. Neither check publishes a scope-conditioned `if`/`then`
        # mirror, and the stream schema carries none to copy: every published
        # `if`/`then` selects on a sibling value (`arrow_type`, `operator`,
        # `type`), never on the binding's scope. The destination reached the
        # same end by becoming a scope-tagged union instead, which is the
        # precedent to follow here. Doing that to StreamSource is a restriction,
        # hence a major stream schema bump, and belongs in its own change.
        if self.endpoint_ref.scope == SCOPE_CONNECTION:
            return self
        declared = [
            name
            for name, value in (
                ("selected_columns", self.selected_columns),
                (
                    "replication.tie_breaker_fields",
                    self.replication.tie_breaker_fields if self.replication else None,
                ),
                ("database_pagination", self.database_pagination),
            )
            if value is not None
        ]
        if declared:
            raise violation(
                "RULE-STRM-014",
                f"{self.endpoint_ref.scope} source declares {declared!r}",
            )
        return self


# ---------------------------------------------------------------------------
# Destination — write selection, execution overrides (spec §Destinations, §Write Selection, §Execution)
# ---------------------------------------------------------------------------


class Execution(StrictModel):
    """Per-stream destination execution override for pipeline runtime batching defaults."""

    batch_size: BatchSize | None = Field(
        default=None,
        description="Override pipeline.runtime.batching.batch_size for this binding.",
    )


# How the SQL write path dispositions every member of the write-mode UNIVERSE
# (`endpoints.WriteMode`, which also keys an API endpoint's `operations.write`).
# The two are separate facts and must not be aliased: a mode only an HTTP
# provider can perform belongs in the universe without thereby becoming a legal
# SQL destination mode. So this table is the decision, and every vocabulary
# below is derived from it — a mode is never hand-listed twice.
#
# Each member says what a database destination declaring it must supply:
#   API_ONLY       the SQL write path does not implement it; database
#                  destinations cannot select it at all
#   KEYLESS        the write matches no existing row, so it declares no key
#   CONFLICT_KEYED the write matches existing rows on a stream-declared key set,
#                  which it must therefore declare
#
# Adding a member to `endpoints.WriteMode` fails the equality guard below until
# it is dispositioned here — the decision is forced, never defaulted. Same idiom
# as the `FilterOperator` guard above: a divergence fails at import, not
# silently at runtime.
_API_ONLY = "api_only"
_KEYLESS = "keyless"
_CONFLICT_KEYED = "conflict_keyed"
_DISPOSITIONS = frozenset({_API_ONLY, _KEYLESS, _CONFLICT_KEYED})

_SQL_WRITE_PATH_DISPOSITIONS: dict[str, str] = {
    "insert": _KEYLESS,
    "upsert": _CONFLICT_KEYED,
    "truncate_insert": _KEYLESS,
}

if not set(_SQL_WRITE_PATH_DISPOSITIONS.values()) <= _DISPOSITIONS:
    raise AssertionError(
        "unknown write-mode disposition(s): "
        f"{sorted(set(_SQL_WRITE_PATH_DISPOSITIONS.values()) - _DISPOSITIONS)}; "
        f"expected one of {sorted(_DISPOSITIONS)}"
    )

if set(_SQL_WRITE_PATH_DISPOSITIONS) != set(WRITE_MODES):
    raise AssertionError(
        "_SQL_WRITE_PATH_DISPOSITIONS must disposition exactly "
        "endpoints.WriteMode; undispositioned: "
        f"{sorted(set(WRITE_MODES) - set(_SQL_WRITE_PATH_DISPOSITIONS))}, unknown: "
        f"{sorted(set(_SQL_WRITE_PATH_DISPOSITIONS) - set(WRITE_MODES))}"
    )


def _modes_dispositioned(disposition: str) -> tuple[str, ...]:
    """The write modes carrying one disposition, in universe order."""
    modes = tuple(
        mode
        for mode in WRITE_MODES
        if _SQL_WRITE_PATH_DISPOSITIONS[mode] == disposition
    )
    if not modes:
        # An empty tuple would make `Literal[modes]` a TypeError at import with
        # no hint of the cause, and a database write variant with no mode is not
        # a shape anything can author.
        raise AssertionError(
            f"no write mode is dispositioned {disposition!r}; the database write "
            "variant it types would have an empty mode vocabulary"
        )
    return modes


_KEYLESS_DB_WRITE_MODES = _modes_dispositioned(_KEYLESS)
_CONFLICT_KEYED_DB_WRITE_MODES = _modes_dispositioned(_CONFLICT_KEYED)

#: Every mode a database (connection-scope) destination may select: the
#: universe minus what the SQL write path does not implement. Read by the
#: pipeline plugin's doc generator to render the vocabulary into its prose.
#: Derived from the same table as the variants, and pinned equal to what they
#: declare — a mistyped disposition would drop a mode from both variants while
#: leaving it here.
_DB_WRITE_MODES: frozenset[str] = frozenset(
    mode
    for mode, disposition in _SQL_WRITE_PATH_DISPOSITIONS.items()
    if disposition != _API_ONLY
)


class DatabaseKeylessWrite(StrictModel):
    """A database write that matches no existing row, so it declares no key.

    No `conflict_keys` field exists on this shape: a load that overwrites or
    appends wholesale has nothing to match on, so the key is absent from the
    type rather than rejected on the way in.
    """

    mode: Literal[_KEYLESS_DB_WRITE_MODES] = Field(  # type: ignore[valid-type]
        ...,
        description=(
            "Database write mode for a load that matches no existing row."
        ),
    )


class DatabaseConflictKeyedWrite(StrictModel):
    """A database write that matches existing rows on a stream-declared key set."""

    mode: Literal[_CONFLICT_KEYED_DB_WRITE_MODES] = Field(  # type: ignore[valid-type]
        ...,
        description=(
            "Database write mode for a load that matches existing rows on the "
            "declared conflict key set."
        ),
    )
    conflict_keys: list[NonEmptyStr] = Field(
        ...,
        min_length=1,
        description=(
            "Conflict target — a single composite key set of destination field "
            "names. Multiple alternative key sets are out of scope until a "
            "connector needs them."
        ),
    )


# `mode`-discriminated union: the conflict-keyed variant declares its key set
# and the keyless variant has no field to declare one in. What used to be a
# cross-field rule ("conflict_keys iff upsert") is now the shape itself, in the
# model and in the published `oneOf` alike.
DatabaseWrite = Annotated[
    DatabaseKeylessWrite | DatabaseConflictKeyedWrite,
    Field(discriminator="mode"),
]


class ApiWrite(StrictModel):
    """Stream-selected write behavior for an API (connector-scope) destination.

    Carries no `conflict_keys`: an API upsert's conflict target is
    endpoint-owned (`operations.write.upsert.conflict_keys`), so there is no
    stream-authored key to declare.
    """

    # Bounded by the UNIVERSE, not by the SQL subset. Which operation an API
    # endpoint actually declares is a cross-document fact this model cannot see,
    # but the KEYS of `endpoints.Operations.write` are typed `WriteMode`, so a
    # mode outside the universe names an operation no api-endpoint document is
    # able to declare — an illegal binding, and representable until this closed.
    # Read from `endpoints` rather than restated, so the two move together.
    mode: WriteMode = Field(
        ...,
        description=(
            "Write mode — the selected endpoint's `operations.write` key. "
            "Which key that endpoint declares is a cross-document fact; that "
            "the key belongs to the destination write-mode universe is not."
        ),
    )


class _StreamDestinationBase(StrictModel):
    """Fields shared by the destination variants; `endpoint_ref.scope` selects
    the variant."""

    execution: Execution | None = Field(
        default=None,
        description="Stream-level destination execution override.",
    )


class DatabaseStreamDestination(_StreamDestinationBase):
    """A destination bound to a connection-scope (database) endpoint."""

    endpoint_ref: ConnectionEndpointRef = Field(
        ..., description="Structured endpoint reference."
    )
    write: DatabaseWrite = Field(
        ..., description="Stream-selected write behavior for this destination."
    )


class ApiStreamDestination(_StreamDestinationBase):
    """A destination bound to a connector-scope (API) endpoint."""

    endpoint_ref: ConnectorEndpointRef = Field(
        ..., description="Structured endpoint reference."
    )
    write: ApiWrite = Field(
        ..., description="Stream-selected write behavior for this destination."
    )


def _destination_scope(value: Any) -> str | None:
    """Tag a destination by its endpoint ref's scope — the discriminator.

    The selecting field is nested (`endpoint_ref.scope`), which a plain field
    discriminator cannot reach, so the tag is read by this callable instead. A
    ref that declares no scope returns None and fails at the union, exactly as
    a missing top-level discriminator would.
    """
    ref = (
        value.get("endpoint_ref")
        if isinstance(value, dict)
        else getattr(value, "endpoint_ref", None)
    )
    if isinstance(ref, dict):
        return ref.get("scope")
    return getattr(ref, "scope", None)


def _union_tags(union: Any) -> tuple[str, ...]:
    """The tags a `Tag`-annotated union selects its variants by, in order."""
    tags = tuple(
        meta.tag
        for variant in get_args(union)
        for meta in getattr(variant, "__metadata__", ())
        if isinstance(meta, Tag)
    )
    if not tags:
        raise AssertionError(
            f"{union!r} carries no `Tag` metadata; the diagnostic below would "
            "name no scope at all"
        )
    return tags


_DESTINATION_VARIANTS = (
    Annotated[DatabaseStreamDestination, Tag(SCOPE_CONNECTION)]
    | Annotated[ApiStreamDestination, Tag(SCOPE_CONNECTOR)]
)

# Destination binding as a `endpoint_ref.scope`-tagged union. Which write shapes
# are legal is a function of the destination's scope — an API destination's mode
# is the endpoint's declared write-operation key and its conflict target
# endpoint-owned, a database destination's mode is the closed SQL vocabulary and
# its conflict key stream-declared — so the scope selects the whole destination
# shape rather than being cross-checked against it afterwards. The published JSON
# Schema renders a `oneOf` over the closed variants; because each variant's
# `endpoint_ref` pins `scope` to a `const`, exactly one branch can ever match, so
# an external validator rejects precisely what this model does.
#
# The diagnostic is stated rather than defaulted. Pydantic names the CALLABLE
# ("Unable to extract tag using discriminator _destination_scope()"), and that
# function appears in no document, no published schema and no skill prose, so it
# tells an author nothing — where the sibling source-side union, discriminated
# by a plain field, names `scope`. A custom error type replaces BOTH the
# missing-tag and the unknown-tag message, so this one carries what each of them
# carried: the key that selects the variant, and every scope that selects one.
# The scopes are read off the union's own tags, so a variant added to it appears
# here without anyone remembering to edit the sentence.
StreamDestination = Annotated[
    _DESTINATION_VARIANTS,
    Discriminator(
        _destination_scope,
        custom_error_type="destination_endpoint_ref_scope",
        custom_error_message=(
            "endpoint_ref.scope selects the destination shape; it must be "
            "present and one of "
            + ", ".join(repr(tag) for tag in _union_tags(_DESTINATION_VARIANTS))
        ),
    ),
]


# ---------------------------------------------------------------------------
# Mapping (spec §Mapping, §Assignment, §Mapping Expressions, §Assignment Validation)
# ---------------------------------------------------------------------------


# Why a token array rather than the dotted string this replaced.
#
# A dotted string is not a path — it is a path plus an unstated splitting
# convention, and the contract never stated one. Two questions it left open:
# where the split happens (nothing said, so every consumer had to decide for
# itself, and two that decided differently would read one document as two
# different paths), and how a field name containing a literal dot is spelled
# (unanswerable). Tokens answer both by construction: there is nothing left to
# split, and `["a.b"]` is a field named `a.b` while `["a", "b"]` is nested.
#
# Kept as a comment, not in the docstring: the docstring renders verbatim into
# the published `$defs.GetExpression.description`, where the rationale is noise
# for the external authors reading it.
class GetExpression(StrictModel):
    """`{"op": "get", "path": ["<segment>", ...]}` — read a source field.

    The path is an ordered token array: `["a", "b"]` reads field `b` nested
    under `a`, and `["a.b"]` reads a top-level field whose name contains a dot.
    """

    op: Literal["get"] = Field(...)
    path: list[NonEmptyStr] = Field(
        ...,
        min_length=1,
        description=(
            "Source field reference as an ordered token array — one entry per "
            "path segment, outermost first. `['a', 'b']` reads field `b` "
            "nested under `a`; `['a.b']` reads a top-level field whose name "
            "literally contains a dot."
        ),
        examples=[["id"], ["address", "city"]],
    )


class FnExpression(StrictModel):
    """`{"op": "fn", "name": "<conversion fn>"}` — one conversion stage of a
    `pipe`.

    Mirrors the engine's `fn` AST node. `name` is closed over the conversion
    functions the engine-published conversion matrix declares for `explicit`
    conversions — the matrix cell's `fn` is what an author (the FE mapping
    editor) wires in, so a name the engine would reject at transform build is
    not authorable. The engine's optional `version`/`args` node fields are
    intentionally not published: the engine defaults them (`version=1`,
    `args=[]`) and no declarable conversion takes arguments.
    """

    op: Literal["fn"] = Field(...)
    # Source of truth for the permitted names: the engine's
    # `cdk/cdk/type_map/conversion_matrix.json` (the `fn` of every `explicit`
    # cell). Widen this Literal when the matrix declares a new one.
    name: Literal["to_string"] = Field(
        ...,
        description=(
            "Conversion function name. Closed over the functions the "
            "engine-published conversion matrix declares for `explicit` "
            "conversions."
        ),
    )


def _pipe_args_positional_grammar(schema: dict[str, Any]) -> None:
    """Publish `pipe.args` as `[<get seed>, <fn stage>, ...]` positionally.

    Pydantic renders `list[GetExpression | FnExpression]` as a uniform
    `items.anyOf`, which would let a published-schema-only author put a
    `get` in a stage position or an `fn` in the seed — shapes the model
    validator (and the engine transform build) reject. Restructured into
    `prefixItems` (seed) + `items` (stages) the published grammar is exactly
    the model's rule. The `$ref`s seen here are pydantic's internal defs refs;
    the generator remaps them to the public `#/$defs/...` refs afterwards.
    """
    by_position: dict[str, dict[str, Any]] = {}
    for variant in schema["items"]["anyOf"]:
        ref = variant["$ref"].rsplit("/", 1)[-1]
        if "GetExpression" in ref:
            by_position["seed"] = variant
        elif "FnExpression" in ref:
            by_position["stage"] = variant
    schema["prefixItems"] = [by_position["seed"]]
    schema["items"] = by_position["stage"]


class PipeExpression(StrictModel):
    """`{"op": "pipe", "args": [<get>, <fn>, ...]}` — a source read piped
    through one or more declared conversion functions.

    Mirrors the engine's `pipe` AST node: `args[0]` is the seed expression —
    a `get` in the stream grammar (constants use `value.constant`, never an
    expression node) — and every later entry is an `fn` conversion stage
    applied left to right. This is how an assignment satisfies an `explicit`
    conversion-matrix pair (e.g. `Int64 → Utf8` needs `to_string`): the
    engine rejects a bare `get` for such a pair at both the transform build
    and the destination cast.
    """

    op: Literal["pipe"] = Field(...)
    args: list[GetExpression | FnExpression] = Field(
        ...,
        min_length=2,
        json_schema_extra=_pipe_args_positional_grammar,
        description=(
            "Seed `get` expression followed by one or more `fn` conversion "
            "stages, applied left to right."
        ),
    )

    @model_validator(mode="after")
    def _validate_positional_grammar(self) -> "PipeExpression":
        # Mirrored in the published schema by `_pipe_args_positional_grammar`
        # (prefixItems/items); keep the two in lockstep.
        if not isinstance(self.args[0], GetExpression):
            raise ValueError("pipe args[0] must be a 'get' expression (the seed)")
        if not all(isinstance(arg, FnExpression) for arg in self.args[1:]):
            raise ValueError("pipe args[1:] must all be 'fn' conversion stages")
        return self


class ArrowFieldSpec(StrictModel):
    """Recursive field-shape declaration.

    Used to describe authored-shape JSON containers under `arrow_type` =
    `Object` / `List` / `Json`. Scalar and parameterized Arrow types reuse the
    same model with `properties` and `items` absent.
    """

    # Declarative mirror of `enforce_container_shape` — shared verbatim with the
    # endpoint `Column`/`ColumnFieldSpec` classes (see `arrow_shape.py`).
    model_config = ConfigDict(
        extra="forbid", json_schema_extra=ARROW_CONTAINER_SCHEMA_RULES
    )

    arrow_type: str = Field(
        ...,
        pattern=ARROW_TYPE_PATTERN,
        description=(
            "Arrow canonical type string from the shared type vocabulary. "
            "Parameterized canonical types must preserve the full string, "
            "e.g. 'Decimal128(38, 9)' — not 'Decimal128'. Bare authored-shape "
            "markers 'Object', 'List', and 'Json' declare JSON containers."
        ),
    )
    nullable: bool | None = Field(default=None)
    # Sibling-key rules (Object/List/Json) live in
    # `analitiq.contracts.shared.arrow_shape.enforce_container_shape`; do not duplicate
    # them in field descriptions, or they'll rot when the rules change.
    properties: dict[str, "ArrowFieldSpec"] | None = Field(default=None)
    items: "ArrowFieldSpec | None" = Field(default=None)

    @model_validator(mode="after")
    def _validate_container_shape(self) -> "ArrowFieldSpec":
        enforce_container_shape(
            self.arrow_type, self.properties, self.items
        )
        return self


# ConstantValue carries the shared container matrix PLUS a `value` JSON-kind
# pin against `arrow_type` (constants carry the actual payload). The container
# branches are reused verbatim from `ARROW_CONTAINER_SCHEMA_RULES`. `value:null`
# is the universal "no value" sentinel, so every branch also admits null.
_CONSTANT_VALUE_SCHEMA_RULES: dict[str, Any] = {
    "allOf": [
        *ARROW_CONTAINER_SCHEMA_RULES["allOf"],
        {
            "if": {"required": ["arrow_type"], "properties": {"arrow_type": {"const": "Object"}}},
            "then": {"properties": {"value": {"type": ["object", "null"]}}},
        },
        {
            "if": {"required": ["arrow_type"], "properties": {"arrow_type": {"const": "List"}}},
            "then": {"properties": {"value": {"type": ["array", "null"]}}},
        },
        {
            "if": {"required": ["arrow_type"], "properties": {"arrow_type": {"const": "Json"}}},
            "then": {"properties": {"value": {"type": ["object", "array", "null"]}}},
        },
        {
            "if": {
                "required": ["arrow_type"],
                "properties": {"arrow_type": {"not": {"enum": ["Object", "List", "Json"]}}},
            },
            "then": {"properties": {"value": {"not": {"type": ["object", "array"]}}}},
        },
    ],
}


class ConstantValue(StrictModel):
    """Typed constant — alternative to expression."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra=_CONSTANT_VALUE_SCHEMA_RULES
    )

    arrow_type: str = Field(
        ...,
        pattern=ARROW_TYPE_PATTERN,
        description=(
            "Arrow canonical type string from the shared type vocabulary. "
            "Parameterized canonical types must preserve the full string, "
            "e.g. 'Decimal128(38, 9)' — not 'Decimal128'. Bare authored-shape "
            "markers 'Object', 'List', and 'Json' declare JSON containers."
        ),
    )
    value: Any = Field(
        ...,
        description=(
            "JSON literal value to assign. May be a JSON object when "
            "arrow_type is 'Object' or 'Json', a JSON array when arrow_type "
            "is 'List' or 'Json', or a JSON scalar for scalar Arrow types."
        ),
    )
    properties: dict[str, ArrowFieldSpec] | None = Field(default=None)
    items: ArrowFieldSpec | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_container_shape(self) -> "ConstantValue":
        enforce_container_shape(
            self.arrow_type, self.properties, self.items
        )
        # Constants carry the actual payload, so we additionally pin the
        # JSON kind of `value` against the declared `arrow_type`. Object →
        # dict; List → list; Json → dict or list; everything else → scalar
        # (anything but dict/list).
        #
        # `value: null` is permitted for any arrow_type as the universal
        # "no value" sentinel — destination nullability is enforced at write
        # time against the destination field, not here. Skip the kind check
        # for null so e.g. `{arrow_type: "Int64", value: null}` and
        # `{arrow_type: "Object", value: null, properties: {...}}` both pass.
        # Sibling-key requirements (`properties` for Object, `items` for List)
        # are still enforced above by `enforce_container_shape` regardless
        # of `value`, so a null Object without `properties` still fails.
        if self.value is None:
            return self
        if self.arrow_type == "Object" and not isinstance(self.value, dict):
            raise ValueError(
                "constant.value must be a JSON object when arrow_type is 'Object'"
            )
        if self.arrow_type == "List" and not isinstance(self.value, list):
            raise ValueError(
                "constant.value must be a JSON array when arrow_type is 'List'"
            )
        if self.arrow_type == "Json" and not isinstance(self.value, (dict, list)):
            raise ValueError(
                "constant.value must be a JSON object or array when "
                "arrow_type is 'Json'"
            )
        if self.arrow_type not in ("Object", "List", "Json") and isinstance(
            self.value, (dict, list)
        ):
            raise ValueError(
                f"constant.value must be a JSON scalar when arrow_type is "
                f"{self.arrow_type!r}; got {type(self.value).__name__}"
            )
        return self


class ExpressionAssignmentValue(StrictModel):
    """`{"kind": "expression", "expression": {...}}` — assign from a source read."""

    kind: Literal["expression"] = Field(
        ..., description="Assignment value kind; always 'expression' here."
    )
    expression: GetExpression | PipeExpression = Field(
        ..., description="Source read, optionally piped through conversion stages."
    )


class ConstantAssignmentValue(StrictModel):
    """`{"kind": "constant", "constant": {...}}` — assign a typed literal."""

    kind: Literal["constant"] = Field(
        ..., description="Assignment value kind; always 'constant' here."
    )
    constant: ConstantValue = Field(..., description="Typed constant to assign.")


# `kind`-discriminated union, replacing a single model with two nullable fields
# and a `_validate_one_of` (retired RULE-STRM-008).
#
# This is the BREAKING half of the release: `kind` is required, so every
# document written against the two-nullable-fields shape — which had no such
# key — is now rejected. What the two shapes agree on is the illegal
# *combination*; the new one additionally demands the discriminator.
#
# The old model DECLARED both fields, so an instance could hold both and a
# validator had to say no. Here each variant declares only its own payload key,
# so `ExpressionAssignmentValue` has no `constant` attribute to hold — the
# illegal combination is gone from the type rather than caught on the way in.
# (`extra="forbid"` on each variant is what rejects it in the input now; before,
# both fields being declared meant only `_validate_one_of` could.) A third value
# kind is then additive: append a variant, touch neither of these two.
#
# On errors, precisely: a MISSING `kind` fails at the discriminator with
# `union_tag_not_found`, naming no variant; an UNKNOWN one fails with
# `union_tag_invalid`, which does name the expected tags. A payload that carries
# a valid `kind` but the wrong body is reported against that variant — which is
# the improvement over "exactly one of 'expression' or 'constant'", where every
# malformed value produced the same sentence.
#
# The published JSON Schema renders `oneOf` + a `kind` discriminator (pinned by
# test_stream_mapping_shapes.py), so external validators reject what this does.
AssignmentValue = Annotated[
    ExpressionAssignmentValue | ConstantAssignmentValue,
    Field(discriminator="kind"),
]


# An assignment target addresses exactly one field on the destination record
# root, so its `path` is a single segment. Nesting beneath that root is declared
# by `arrow_type: "Object"` plus `properties` (or `List` plus `items`), and a
# dotted target path would be a second, contradictory spelling of the same
# thing. Constraining it here makes that document unparseable rather
# than leaving each consumer to reject it later.
#
# Two constraints. "At least one non-whitespace character" is the parity one —
# it is what `NonEmptyStr` already guarantees for a SOURCE segment
# (`GetExpression.path` items), and a destination field name must not be laxer
# than the source name it is fed from. "No `.` anywhere" is target-only and
# deliberately TIGHTER than the source: `["a.b"]` is a legal source token,
# because there the dot is inside one segment rather than separating two.
# Written without lookaround on purpose:
# pydantic's default rust-regex engine has none, and this pattern is published
# into the JSON Schema.
SINGLE_SEGMENT_PATH_PATTERN = r"^[^.]*[^.\s][^.]*$"


class AssignmentTarget(StrictModel):
    """Destination field specification."""

    # Declarative mirror of `enforce_container_shape` — shared with the other
    # authored-shape classes (see `arrow_shape.py`).
    model_config = ConfigDict(
        extra="forbid", json_schema_extra=ARROW_CONTAINER_SCHEMA_RULES
    )

    path: str = Field(
        ...,
        min_length=1,
        pattern=SINGLE_SEGMENT_PATH_PATTERN,
        description=(
            "Destination field reference — a single segment naming one field "
            "on the destination record root. Nesting is declared with "
            "`arrow_type: 'Object'` + `properties` (or `'List'` + `items`), "
            "never with a dotted path."
        ),
        examples=["id", "address"],
    )
    arrow_type: str = Field(
        ...,
        pattern=ARROW_TYPE_PATTERN,
        description=(
            "Arrow canonical type string from the shared type vocabulary. "
            "Parameterized canonical types must preserve the full string, "
            "e.g. 'Decimal128(38, 9)' — not 'Decimal128'. Bare authored-shape "
            "markers 'Object', 'List', and 'Json' declare JSON containers."
        ),
    )
    native_type: str | None = Field(
        default=None,
        description="Destination-native type override (e.g., 'NUMERIC(12,2)').",
    )
    nullable: bool = Field(default=True)
    # See ArrowFieldSpec for the recursive child shape and
    # enforce_container_shape for the sibling-key rules.
    properties: dict[str, ArrowFieldSpec] | None = Field(default=None)
    items: ArrowFieldSpec | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_container_shape(self) -> "AssignmentTarget":
        enforce_container_shape(
            self.arrow_type, self.properties, self.items
        )
        return self


# Declarative mirror of `_validate_value_for_rule`. It resembles the
# `_FILTER_CONDITIONAL_RULES` precedent but must be null-aware, because
# `ValidationRule`'s runtime is stricter than `Filter`'s: `Filter` defers the
# value check (a null `value` is allowed for binary operators), so key-presence
# suffices there; `ValidationRule` requires a NON-NULL `value` for the
# value-taking rules and forbids a non-null `value` for the unary ones. So each
# branch pins the null-ness, not just key presence:
#   type in {required, not_null}  ⇒ value absent or null
#   otherwise                     ⇒ value present and non-null
_VALIDATION_RULE_CONDITIONAL_RULES: dict[str, Any] = {
    "allOf": [
        {
            "if": {
                "properties": {"type": {"enum": ["required", "not_null"]}},
                "required": ["type"],
            },
            "then": {"properties": {"value": {"type": "null"}}},
            "else": {
                "required": ["value"],
                "properties": {"value": {"not": {"type": "null"}}},
            },
        },
    ],
    "additionalProperties": False,
}


class ValidationRule(StrictModel):
    """Stream record validation rule — see §Assignment Validation."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra=_VALIDATION_RULE_CONDITIONAL_RULES
    )

    type: Literal[
        "required", "not_null", "min_length", "max_length", "pattern", "range", "in_list"
    ] = Field(...)
    # `NonEmptyStr` tokens, matching a source `get` segment and NOT the tighter
    # `AssignmentTarget.path` rule. The tight rule exists because a dotted
    # STRING is ambiguous — it could be read as a path — and a token array
    # removes that ambiguity by construction, so the reason does not carry over.
    # It also cannot be applied here without breaking addressing: nested field
    # names are `properties` keys, which are unconstrained, so a declared field
    # named `user.id` would be addressable by no rule at all. `["a.b"]` is one
    # field called `a.b`; nesting is `["a", "b"]`.
    field: list[NonEmptyStr] = Field(
        ...,
        min_length=1,
        description=(
            "Mapped output field addressed by this rule, as an ordered token "
            "array — one entry per path segment, outermost first, spelled like "
            "a source `get` path. The first token names an "
            "`assignments[].target.path` declared in the same mapping; each "
            "later token names a field declared under that target's "
            "`properties` (descending through `items` for a `List`), so a rule "
            "can address a field nested inside an `Object` target. A token is "
            "one field name, so a `.` inside it is part of that name: nesting "
            "is a further token, never a dotted string."
        ),
        examples=[["email"], ["address", "city"]],
    )
    value: Any = Field(
        default=None,
        description=(
            "Rule parameter. Required for min_length/max_length/pattern/range/in_list; "
            "must be omitted for required/not_null."
        ),
    )
    message: str | None = Field(default=None, description="Custom validation error message.")

    @model_validator(mode="after")
    def _validate_value_for_rule(self) -> "ValidationRule":
        unary = {"required", "not_null"}
        needs_value = {"min_length", "max_length", "pattern", "range", "in_list"}
        if self.type in unary and self.value is not None:
            raise ValueError(
                f"validation rule {self.type!r} must omit 'value'"
            )
        if self.type in needs_value and self.value is None:
            raise ValueError(
                f"validation rule {self.type!r} requires 'value'"
            )
        return self


class StreamValidationErrorHandling(RetryErrorHandlingBase):
    """Mirror of the shared error-handling shape (`pipeline.ErrorHandling` in `analitiq.contracts.pipelines`)."""

    # Inherits the full error-handling contract — fields, bounds, defaulting, the
    # cross-field rule, and the JSON-Schema conditional rules — from
    # `RetryErrorHandlingBase`, shared with the pipeline block so the two cannot
    # drift. The stream block adds no field descriptions, so it re-declares
    # nothing; this subclass exists to name the stream schema `$def`.


class Validation(StrictModel):
    """Per-assignment validation block."""

    rules: list[ValidationRule] = Field(default_factory=list)
    error_handling: StreamValidationErrorHandling | None = Field(
        default=None,
        description=(
            "Validation failure handling override. When omitted, the pipeline "
            "runtime.error_handling default applies."
        ),
    )


class Assignment(StrictModel):
    """Single field assignment — writes one target field from expression or constant."""

    target: AssignmentTarget = Field(...)
    value: AssignmentValue = Field(...)
    # Field name is `validate` per spec; aliased to avoid shadowing Pydantic's
    # `BaseModel.validate` legacy attribute.
    validation: Validation | None = Field(
        default=None,
        alias="validate",
        description="Assignment validation rules.",
    )


def _declared_child(
    node: AssignmentTarget | ArrowFieldSpec, token: str
) -> ArrowFieldSpec | None:
    """The field `token` names under `node`, or None when `node` declares none.

    A `List` node declares its element shape in `items` rather than naming it,
    so the element is stepped through transparently: a rule on a list of
    objects addresses the object's fields. `enforce_container_shape` keeps
    `properties` and `items` mutually exclusive, so the walk is unambiguous;
    the loop is bounded by the (finite) declared nesting depth.
    """
    while node.properties is None and node.items is not None:
        node = node.items
    return (node.properties or {}).get(token)


class StreamMapping(StrictModel):
    """Source-to-destination assignment rules. Optional — omit for default mapping."""

    assignments: list[Assignment] = Field(
        default_factory=list,
        description="Ordered list of field assignments. Order is significant.",
    )

    @model_validator(mode="after")
    def _assignment_targets_unique(self) -> "StreamMapping":
        """RULE-STRM-002: array position never decides a destination field's value."""
        dups = find_duplicates(self.assignments, key=lambda a: a.target.path)
        if dups:
            raise violation("RULE-STRM-002", f"duplicates={dups!r}")
        return self

    @model_validator(mode="after")
    def _validate_rule_fields_resolve(self) -> "StreamMapping":
        # A validation rule grades the MAPPED OUTPUT, so its `field` addresses a
        # target this mapping declares — and only this model has both the
        # assignments and their validation blocks in scope. Unchecked, a typo
        # named nothing and the rule silently graded no value at all, which
        # reads exactly like a passing rule. Any target in the mapping is
        # addressable, not just the enclosing assignment's: rules are authored
        # per assignment but grade the record the assignments build together.
        declared = {a.target.path: a.target for a in self.assignments}
        for i, assignment in enumerate(self.assignments):
            if assignment.validation is None:
                continue
            for j, rule in enumerate(assignment.validation.rules):
                where = f"assignments[{i}].validate.rules[{j}].field"
                head, *rest = rule.field
                node: AssignmentTarget | ArrowFieldSpec | None = declared.get(head)
                if node is None:
                    raise ValueError(
                        f"{where} {rule.field!r} names no assignment target: "
                        f"{head!r} is not a declared assignments[].target.path "
                        f"(declared: {sorted(declared)})"
                    )
                walked = [head]
                for token in rest:
                    child = _declared_child(node, token)
                    if child is None:
                        # Report the prefix as tokens, not joined by a dot: this
                        # contract spells nesting one token at a time, and an
                        # error message is a place authors copy from.
                        raise ValueError(
                            f"{where} {rule.field!r} does not resolve: "
                            f"{walked!r} declares no field {token!r} under its "
                            "`properties`"
                        )
                    node = child
                    walked.append(token)
        return self


# ---------------------------------------------------------------------------
# Authored shared base + read/write split
# ---------------------------------------------------------------------------


class StreamAuthored(StrictModel):
    """Authored stream fields shared between input and persisted models."""

    schema_url: Literal[STREAM_SCHEMA_URL] | None = Field(
        default=None,
        alias="$schema",
        description="Stream schema URL (optional in API payloads).",
    )

    display_name: str | None = Field(
        default=None,
        min_length=DISPLAY_NAME_MIN,
        max_length=DISPLAY_NAME_MAX,
        pattern=NO_EDGE_WHITESPACE_PATTERN,
        description="User-facing stream label.",
    )
    description: str | None = Field(
        default=None, max_length=DESCRIPTION_MAX, description="User-facing summary."
    )
    pipeline_id: NonEmptyStr = Field(
        ...,
        description=(
            "Parent pipeline reference. Typically the base pipeline UUID; the "
            "schema accepts any non-empty string — engines resolve the "
            "reference at runtime. Immutable after creation."
        ),
        examples=["b4904c77-0a4a-4a8d-a768-4a8b5f2f2414"],
    )

    source: StreamSource = Field(...)
    destinations: list[StreamDestination] = Field(
        ..., min_length=1, description="Non-empty array of destination bindings."
    )
    mapping: StreamMapping | None = Field(
        default=None,
        description=(
            "Explicit source-to-destination field mapping. Omit for runtime "
            "default mapping."
        ),
    )

    status: Literal["draft", "active", "inactive"] = Field(
        default="draft",
        description=(
            "Lifecycle status."
        ),
    )
    tags: list[TrimmedTag] | None = Field(
        default=None,
        max_length=TAGS_MAX,
        json_schema_extra={"uniqueItems": True},
        description="Grouping/search labels (max 50, each 1-64 chars, unique, trimmed).",
    )

    @field_validator("display_name")
    @classmethod
    def _validate_display_name_field(cls, v: str | None) -> str | None:
        return validate_display_name(v)

    @field_validator("tags")
    @classmethod
    def _validate_tags_field(cls, v: list[str] | None) -> list[str] | None:
        return validate_tags(v)

    @model_validator(mode="after")
    def _destinations_unique(self) -> "StreamAuthored":
        """RULE-STRM-001: no endpoint receives the same records twice."""
        _check_unique_destinations(self.destinations)
        return self


class StreamInput(StreamAuthored):
    """Strict API input variant — the source of truth for the `stream/latest.json` published JSON Schema.

    This model declares exactly what an author may write, so anything Analitiq
    assigns is not merely rejected here — it is unrepresentable. `extra="forbid"`
    does the rest.

    `stream_id` is an optional authored identifier (RFC-4122 UUID) so
    externally-authored stream definitions can supply their own UUID; the
    service assigns one when the create payload omits it.
    """


    stream_id: str | None = Field(
        default=None,
        pattern=UUID_PATTERN,
        description=(
            "Stream UUID (RFC-4122). Optional in authored definitions so "
            "external publishers can assign their own identifier; "
            "service-assigned on ingest when omitted."
        ),
    )

