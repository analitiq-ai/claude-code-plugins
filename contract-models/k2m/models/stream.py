"""Stream models and validators.

Aligned with the published Analitiq schema documentation (schema v1).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from k2m.models.endpoints import ARROW_TYPE_PATTERN, DatabaseObject
from k2m.models.endpoint_identity import derive_db_endpoint_id
from k2m.models.shared.arrow_shape import enforce_container_shape
from k2m.models.shared.common import (
    CorruptedPlaceholderBase,
    DESCRIPTION_MAX,
    DISPLAY_NAME_MAX,
    DISPLAY_NAME_MIN,
    NonEmptyStr as _NonEmptyStr,
    StrictModel,
    TAGS_MAX,
    Tag as _Tag,
    make_authored_top_level_check,
    make_strip_managed_fields,
    schema_url_for,
    schema_url_pattern,
    validate_display_name as _validate_display_name,
    validate_tags as _validate_tags,
)
from k2m.models.shared.types import UUID_PATTERN


STREAM_SCHEMA_URL = schema_url_for("stream")

SCOPE_CONNECTOR = "connector"
SCOPE_CONNECTION = "connection"

# Re-export under historical names for callers that import via this module.
_DESCRIPTION_MAX = DESCRIPTION_MAX
_DISPLAY_NAME_MIN = DISPLAY_NAME_MIN
_DISPLAY_NAME_MAX = DISPLAY_NAME_MAX
_TAGS_MAX = TAGS_MAX

# Rejected on `StreamInput` so a misconfigured caller fails loud rather than
# silently overwriting server state. Sub-model server-managed fields
# (`source.schema_hash`, `destinations[].schema_hash`, `mapping.assignments_hash`)
# are declared as readOnly fields on `StreamSource`/`StreamDestination`/`StreamMapping`
# so persisted records round-trip through `StreamConfig`; they are rejected on
# `StreamInput` by `_reject_sub_model_server_managed`.
# `stream_id` is intentionally excluded so externally-authored stream documents
# (publicly managed artifacts) can supply their own UUID through the published
# validation schema.
SERVER_MANAGED_FIELDS: frozenset[str] = frozenset({
    "version",
    "org_id",
    "created_at",
    "updated_at",
})

_check_authored_top_level = make_authored_top_level_check(
    SERVER_MANAGED_FIELDS,
    spec_doc="the published Analitiq schema documentation",
)

strip_server_managed_fields = make_strip_managed_fields(SERVER_MANAGED_FIELDS)

_XModel = StrictModel


# Sub-model readOnly fields. Declared as accepted (readOnly) on the relevant
# sub-models so persisted records round-trip; rejected on `StreamInput` /
# `StreamPatch` by `_reject_sub_model_server_managed`.
_SUB_MODEL_SERVER_MANAGED_FIELDS: dict[str, frozenset[str]] = {
    "source": frozenset({"schema_hash"}),
    "destinations": frozenset({"schema_hash"}),
    "mapping": frozenset({"assignments_hash"}),
}


def _reject_sub_model_server_managed(data: Any) -> Any:
    """Reject readOnly sub-model fields on input (StreamInput / StreamPatch)."""
    if not isinstance(data, dict):
        return data
    violations: list[str] = []
    for parent, banned in _SUB_MODEL_SERVER_MANAGED_FIELDS.items():
        node = data.get(parent)
        if isinstance(node, dict):
            present = sorted(banned & node.keys())
            violations.extend(f"{parent}.{k}" for k in present)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, dict):
                    present = sorted(banned & item.keys())
                    violations.extend(f"{parent}[{i}].{k}" for k in present)
    if violations:
        raise ValueError(
            f"server-managed fields {violations!r} must not be sent by clients"
        )
    return data


def _check_unique_destinations(
    destinations: list["StreamDestination"],
) -> list["StreamDestination"]:
    """Per spec §Destinations: unique by `(scope, connection_id, endpoint_id)`."""
    seen: set[tuple] = set()
    duplicates: list[tuple] = []
    for dest in destinations:
        ref = dest.endpoint_ref
        key = (ref.scope, ref.connection_id, ref.endpoint_id)
        if key in seen:
            duplicates.append(key)
        else:
            seen.add(key)
    if duplicates:
        raise ValueError(
            "destinations[].endpoint_ref must be unique by "
            f"(scope, connection_id, endpoint_id); duplicates: {sorted(set(duplicates))!r}"
        )
    return destinations


# ---------------------------------------------------------------------------
# Endpoint reference
# ---------------------------------------------------------------------------


class _EndpointRefBase(_XModel):
    """Fields shared by both endpoint-reference variants.

    `endpoint_id` lives on each variant, not here: a `connector` ref's id is
    client-authored (the connector registry key), while a `connection` ref's id
    is a server-derived handle over `database_object` (never client-authored).
    """

    connection_id: _NonEmptyStr = Field(
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

    Carries the verbatim provider-native object locator in `database_object`;
    the `endpoint_id` is an opaque **server-derived** handle over that locator
    (see the endpoint identity contract) — never client-authored and never
    decoded back to a target. The locator is what the engine dialect-quotes to
    build the SQL identifier and what the backend introspects to materialize the
    schema snapshot — so it is REQUIRED and non-null here.
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
        json_schema_extra={"readOnly": True},
        description=(
            "Server-derived opaque handle over `database_object` "
            "(`slug(schema)__slug(table)[__slug(catalog)]__<hash8>`). Clients "
            "must not author it — omit it and the server derives it from the "
            "locator; a supplied value must equal the derived handle."
        ),
    )

    @model_validator(mode="after")
    def _derive_or_verify_endpoint_id(self) -> "ConnectionEndpointRef":
        # `endpoint_id` is a pure function of the verbatim locator via the single
        # shared `derive_db_endpoint_id` (the same helper the discovery mint site
        # uses), so there is no second implementation to drift. Omitted → derive
        # (the new-table destination case, where no discovery descriptor exists);
        # supplied → verify it matches, fail loud on a client-authored mismatch.
        obj = self.database_object
        canonical = derive_db_endpoint_id(obj.catalog, obj.schema_, obj.name)
        if self.endpoint_id is None:
            self.endpoint_id = canonical
        elif self.endpoint_id != canonical:
            raise ValueError(
                f"endpoint_id {self.endpoint_id!r} does not match the id derived "
                f"from database_object ({canonical!r}); it is a server-derived "
                "handle and must not be authored independently"
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
# edge (the connection variant has no null branch). Spec:
# the published Analitiq schema documentation
# §Shared Metadata; §Endpoint Identity Derivation.
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


_FILTER_CONDITIONAL_RULES: dict[str, Any] = {
    "allOf": [
        {
            "if": {
                "properties": {"operator": {"enum": ["is_null", "is_not_null"]}},
                "required": ["operator"],
            },
            "then": {"not": {"required": ["value"]}},
            "else": {"required": ["value"]},
        },
    ],
    "additionalProperties": False,
}


class Filter(_XModel):
    """Stream-owned read predicate.

    Endpoint contracts own which fields/params are filterable and which
    operators are allowed. Per spec §Filters, `value` is required except
    when `operator` is a unary operator (`is_null` / `is_not_null`).
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_FILTER_CONDITIONAL_RULES,
    )

    field: str = Field(
        ...,
        min_length=1,
        description="Database field reference or API endpoint read parameter key.",
    )
    operator: str = Field(
        ...,
        min_length=1,
        description="Operator selected from the applicable source capability.",
    )
    value: Any = Field(
        default=None,
        description="JSON value for the predicate; omit for unary operators.",
    )

    @model_validator(mode="after")
    def _validate_value_presence(self) -> "Filter":
        unary = {"is_null", "is_not_null"}
        if self.operator in unary and self.value is not None:
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


class ReplicationConfig(_XModel):
    """Replication policy. Cursor mapping is endpoint-owned."""

    method: Literal["full_refresh", "incremental"] = Field(
        ...,
        description="Stream-selected replication method.",
    )
    cursor_field: str | None = Field(
        default=None,
        min_length=1,
        description="Source field reference; required for incremental, omitted for full_refresh.",
    )
    safety_window_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Non-negative late-arrival overlap window.",
    )
    tie_breaker_fields: list[str] | None = Field(
        default=None,
        description="Database-only deterministic cursor tie-breaker fields.",
    )

    @model_validator(mode="after")
    def _validate_cursor(self) -> "ReplicationConfig":
        if self.method == "incremental" and not self.cursor_field:
            raise ValueError("cursor_field is required when replication.method is 'incremental'")
        if self.method == "full_refresh" and self.cursor_field is not None:
            raise ValueError("cursor_field must be omitted when replication.method is 'full_refresh'")
        return self


# ---------------------------------------------------------------------------
# Database pagination (spec §Database Pagination)
# ---------------------------------------------------------------------------


class DatabasePagination(_XModel):
    """Database read-page configuration. API pagination is endpoint-owned."""

    type: Literal["offset", "keyset"] = Field(
        ...,
        description="Database pagination strategy.",
    )
    page_size: int | None = Field(
        default=None,
        ge=1,
        description="Positive integer read page size; pipeline batch-size default applies when omitted.",
    )
    order_by_field: str | None = Field(
        default=None,
        min_length=1,
        description="Source field reference for page ordering; required for keyset, omitted for offset.",
    )

    @model_validator(mode="after")
    def _validate_keyset(self) -> "DatabasePagination":
        if self.type == "keyset" and not self.order_by_field:
            raise ValueError("order_by_field is required when database_pagination.type is 'keyset'")
        return self


# ---------------------------------------------------------------------------
# Source (spec §Source)
# ---------------------------------------------------------------------------


class StreamSource(_XModel):
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
    replication: ReplicationConfig | None = Field(
        default=None,
        description=(
            "Stream-selected replication policy. Omission allowed only when "
            "the source supports full_refresh."
        ),
    )
    database_pagination: DatabasePagination | None = Field(
        default=None,
        description=(
            "Database source read-page configuration. Defaults to offset "
            "pagination with page size from pipeline.runtime.batching.batch_size "
            "when omitted for database sources."
        ),
    )
    primary_keys: list[str] | None = Field(
        default=None,
        description=(
            "Stream-owned source identity hint when the endpoint does not "
            "provide primary-key metadata."
        ),
    )
    schema_hash: str | None = Field(
        default=None,
        json_schema_extra={"readOnly": True},
        description=(
            "Server-assigned schema snapshot hash for `scope=connection` refs. "
            "Clients must not author this field."
        ),
    )


# ---------------------------------------------------------------------------
# Destination — write selection, execution overrides (spec §Destinations, §Write Selection, §Execution)
# ---------------------------------------------------------------------------


class WriteConfig(_XModel):
    """Stream-selected write behavior for one destination."""

    mode: str = Field(
        ...,
        min_length=1,
        description=(
            "Write mode. API: selected endpoint operations.write key. "
            "Database: 'insert' or 'upsert'."
        ),
    )
    conflict_keys: list[Annotated[str, Field(min_length=1)]] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Database upsert conflict target — a single composite key set "
            "(non-empty list of destination field names). Required for a "
            "database (`scope=connection`) upsert; forbidden for an API "
            "(`scope=connector`) destination, whose conflict key is "
            "endpoint-owned (`operations.write.upsert.conflict_keys`). Presence "
            "is enforced by `StreamDestination`, which knows the destination "
            "scope. Multiple alternative key sets are out of scope until a "
            "connector needs them."
        ),
    )


class ExecutionConfig(_XModel):
    """Per-stream destination execution override for pipeline runtime batching defaults."""

    batch_size: int | None = Field(
        default=None,
        ge=1,
        le=100_000,
        description="Override pipeline.runtime.batching.batch_size for this binding.",
    )
    max_concurrent_batches: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Override pipeline.runtime.batching.max_concurrent_batches for this binding.",
    )


class StreamDestination(_XModel):
    """Destination endpoint binding and stream-owned destination options."""

    endpoint_ref: EndpointRef = Field(..., description="Structured endpoint reference.")
    write: WriteConfig = Field(
        ..., description="Stream-selected write behavior for this destination."
    )
    execution: ExecutionConfig | None = Field(
        default=None,
        description="Stream-level destination execution override.",
    )
    schema_hash: str | None = Field(
        default=None,
        json_schema_extra={"readOnly": True},
        description=(
            "Server-assigned schema snapshot hash for `scope=connection` refs. "
            "Clients must not author this field."
        ),
    )

    @model_validator(mode="after")
    def _validate_write_conflict_keys(self) -> "StreamDestination":
        # Who owns the upsert conflict key differs by destination type, and the
        # type is the endpoint scope: `connector` is an API endpoint (the key is
        # provider-defined and declared on the endpoint —
        # `operations.write.upsert.conflict_keys`), `connection` is a database
        # endpoint (the key is the stream-selected `primary_keys` subset). So an
        # API destination must NOT carry stream-authored conflict_keys, and a
        # database upsert MUST. Spec: §Write Selection.
        if self.endpoint_ref.scope == "connector":
            if self.write.conflict_keys is not None:
                raise ValueError(
                    "destinations[].write.conflict_keys must not be set for an API "
                    "destination (endpoint_ref.scope='connector'); the upsert conflict "
                    "key is endpoint-owned (operations.write.upsert.conflict_keys)"
                )
        elif self.write.mode == "upsert":
            if not self.write.conflict_keys:
                raise ValueError(
                    "destinations[].write.conflict_keys is required for a database upsert "
                    "(endpoint_ref.scope='connection', write.mode='upsert')"
                )
        elif self.write.conflict_keys is not None:
            # conflict_keys are an upsert concept; a non-upsert database mode
            # (insert) must not carry them.
            raise ValueError(
                "destinations[].write.conflict_keys is only valid for a database upsert "
                f"(endpoint_ref.scope='connection', write.mode='upsert'); write.mode="
                f"{self.write.mode!r} must not declare it"
            )
        return self


# ---------------------------------------------------------------------------
# Mapping (spec §Mapping, §Assignment, §Mapping Expressions, §Assignment Validation)
# ---------------------------------------------------------------------------


class GetExpression(_XModel):
    """`{"op": "get", "path": "<source field reference>"}` — read a source
    field."""

    op: Literal["get"] = Field(...)
    path: str = Field(
        ..., min_length=1, description="Source field reference."
    )


class FnExpression(_XModel):
    """`{"op": "fn", "name": "<conversion fn>"}` — one conversion stage of a
    `pipe` (#887).

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


class PipeExpression(_XModel):
    """`{"op": "pipe", "args": [<get>, <fn>, ...]}` — a source read piped
    through one or more declared conversion functions (#887).

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


class ArrowFieldSpec(_XModel):
    """Recursive field-shape declaration.

    Used to describe authored-shape JSON containers under `arrow_type` =
    `Object` / `List` / `Json`. Scalar and parameterized Arrow types reuse the
    same model with `properties` and `items` absent.

    Spec: the published Analitiq schema documentation §Assignment and
    the published Analitiq schema documentation
    §Native and Arrow Types.
    """

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
    # `k2m.models.shared.arrow_shape.enforce_container_shape`; do not duplicate
    # them in field descriptions, or they'll rot when the rules change.
    properties: dict[str, "ArrowFieldSpec"] | None = Field(default=None)
    items: "ArrowFieldSpec | None" = Field(default=None)

    @model_validator(mode="after")
    def _validate_container_shape(self) -> "ArrowFieldSpec":
        enforce_container_shape(
            self.arrow_type, self.properties, self.items
        )
        return self


class ConstantValue(_XModel):
    """Typed constant — alternative to expression."""

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


class AssignmentValue(_XModel):
    """Exactly one of `expression` or `constant` per spec §Assignment."""

    expression: GetExpression | PipeExpression | None = Field(default=None)
    constant: ConstantValue | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_one_of(self) -> "AssignmentValue":
        if (self.expression is None) == (self.constant is None):
            raise ValueError(
                "value must contain exactly one of 'expression' or 'constant'"
            )
        return self


class AssignmentTarget(_XModel):
    """Destination field specification."""

    path: str = Field(
        ..., min_length=1, description="Destination field reference."
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


class ValidationRule(_XModel):
    """Stream record validation rule — see §Assignment Validation."""

    type: Literal[
        "required", "not_null", "min_length", "max_length", "pattern", "range", "in_list"
    ] = Field(...)
    field: str = Field(
        ..., min_length=1, description="Mapped output field path validated by this rule."
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


_STREAM_ERROR_HANDLING_CONDITIONAL_RULES: dict[str, Any] = {
    "allOf": [
        {
            "if": {
                "properties": {"max_retries": {"const": 0}},
                "required": ["max_retries"],
            },
            "then": {
                "properties": {
                    "retry_delay_seconds": {"oneOf": [{"const": 0}, {"type": "null"}]}
                }
            },
        },
    ],
    "additionalProperties": False,
}


class StreamValidationErrorHandling(_XModel):
    """Mirror of `pipeline.ErrorHandlingConfig`. See pipeline-schema §Error Handling."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_STREAM_ERROR_HANDLING_CONDITIONAL_RULES,
    )

    strategy: Literal["fail", "dlq", "skip"] = Field(default="dlq")
    max_retries: int = Field(default=3, ge=0, le=5)
    retry_delay_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _default_retry_delay(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        max_retries = data.get("max_retries", 3)
        if "retry_delay_seconds" in data and data["retry_delay_seconds"] is not None:
            return data
        data["retry_delay_seconds"] = 0 if max_retries == 0 else 5
        return data

    @model_validator(mode="after")
    def _validate_retry_fields(self) -> "StreamValidationErrorHandling":
        if self.max_retries == 0 and self.retry_delay_seconds not in (None, 0):
            raise ValueError(
                "retry_delay_seconds must be omitted or 0 when max_retries is 0"
            )
        return self


class ValidationConfig(_XModel):
    """Per-assignment validation block."""

    rules: list[ValidationRule] = Field(default_factory=list)
    error_handling: StreamValidationErrorHandling | None = Field(
        default=None,
        description=(
            "Validation failure handling override. When omitted, the pipeline "
            "runtime.error_handling default applies."
        ),
    )


class Assignment(_XModel):
    """Single field assignment — writes one target field from expression or constant."""

    target: AssignmentTarget = Field(...)
    value: AssignmentValue = Field(...)
    # Field name is `validate` per spec; aliased to avoid shadowing Pydantic's
    # `BaseModel.validate` legacy attribute.
    validation: ValidationConfig | None = Field(
        default=None,
        alias="validate",
        description="Assignment validation rules.",
    )


class StreamMapping(_XModel):
    """Source-to-destination assignment rules. Optional — omit for default mapping."""

    assignments: list[Assignment] = Field(
        default_factory=list,
        description="Ordered list of field assignments. Order is significant.",
    )
    assignments_hash: str | None = Field(
        default=None,
        json_schema_extra={"readOnly": True},
        description=(
            "Server-computed content hash over `assignments`. "
            "Clients must not author this field."
        ),
    )

    @field_validator("assignments")
    @classmethod
    def _unique_target_paths(cls, v: list[Assignment]) -> list[Assignment]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for a in v:
            if a.target.path in seen:
                duplicates.append(a.target.path)
            seen.add(a.target.path)
        if duplicates:
            raise ValueError(
                f"mapping.assignments target paths must be unique: "
                f"{sorted(set(duplicates))!r}"
            )
        return v


# ---------------------------------------------------------------------------
# Authored shared base + read/write split
# ---------------------------------------------------------------------------


class _StreamAuthored(BaseModel):
    """Authored stream fields shared between input and persisted models."""

    schema_url: Literal[STREAM_SCHEMA_URL] | None = Field(
        default=None,
        alias="$schema",
        description="Stream schema URL (optional in API payloads).",
    )

    display_name: str | None = Field(
        default=None,
        min_length=_DISPLAY_NAME_MIN,
        max_length=_DISPLAY_NAME_MAX,
        description="User-facing stream label.",
    )
    description: str | None = Field(
        default=None, max_length=_DESCRIPTION_MAX, description="User-facing summary."
    )
    pipeline_id: _NonEmptyStr = Field(
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
            "Lifecycle status authored by clients. The terminal `error` state "
            "is backend-managed and surfaces only on the read-side model."
        ),
    )
    tags: list[_Tag] | None = Field(
        default=None,
        max_length=_TAGS_MAX,
        description="Grouping/search labels (max 50, each 1-64 chars, unique, trimmed).",
    )

    @field_validator("display_name")
    @classmethod
    def _validate_display_name_field(cls, v: str | None) -> str | None:
        return _validate_display_name(v)

    @field_validator("tags")
    @classmethod
    def _validate_tags_field(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)

    @field_validator("destinations")
    @classmethod
    def _unique_destination_endpoint_refs(
        cls, v: list[StreamDestination]
    ) -> list[StreamDestination]:
        return _check_unique_destinations(v)


class StreamConfig(_StreamAuthored):
    """Read-side model. Round-trips persisted DDB documents.

    `extra="allow"` at top level passes DDB-internal attributes (`pk`, `sk`,
    GSI keys) through; sub-model server-managed metadata (`schema_hash`,
    `assignments_hash`) is declared on the relevant sub-models as readOnly.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # Read-side widens the input enum to include backend-managed terminal `error`.
    status: Literal["draft", "active", "inactive", "error"] = Field(  # type: ignore[assignment]
        default="draft", description="Lifecycle status (read side; includes backend-managed `error`)."
    )

    stream_id: str | None = Field(
        default=None, description="Versioned stream ID (server-managed)."
    )
    version: int | None = Field(
        default=None, ge=1, description="Stream configuration version (server-managed)."
    )
    org_id: str | None = Field(
        default=None, description="Tenant identifier (server-managed)."
    )
    created_at: datetime | None = Field(default=None, description="Creation timestamp (server-managed).")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp (server-managed).")


class StreamInput(_StreamAuthored):
    """Strict API input variant — the source of truth for the `stream/latest.json` published JSON Schema.

    Server-managed fields are not declared here, so the published artifact
    does not advertise them as accepted properties. `_check_authored_top_level`
    rejects them on input by name (alongside unknown top-level keys).
    `_reject_sub_model_server_managed` rejects readOnly sub-model fields
    (`source.schema_hash`, `destinations[].schema_hash`, `mapping.assignments_hash`).

    `stream_id` is an optional authored identifier (RFC-4122 UUID) so
    externally-authored stream definitions can supply their own UUID; the
    service assigns one when the create payload omits it.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    stream_id: str | None = Field(
        default=None,
        pattern=UUID_PATTERN,
        description=(
            "Stream UUID (RFC-4122). Optional in authored definitions so "
            "external publishers can assign their own identifier; "
            "service-assigned on ingest when omitted."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_top_level(cls, data: Any) -> Any:
        data = _check_authored_top_level(cls, data)
        return _reject_sub_model_server_managed(data)


class StreamDocument(_StreamAuthored):
    """Persisted-record variant — internal read-side validator for `GET /streams/{id}`.

    Same authored shape as `StreamInput` plus the server-managed fields the
    service stamps on at create time. NOT published as a JSON Schema; the
    public `stream/latest.json` is rendered from `StreamInput`.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Read-side widens the input enum to include backend-managed terminal `error`.
    status: Literal["draft", "active", "inactive", "error"] = Field(  # type: ignore[assignment]
        default="draft",
        description="Lifecycle status (read side; includes backend-managed `error`).",
    )

    stream_id: str = Field(..., description="Versioned stream ID (server-managed).")
    version: int = Field(..., ge=1, description="Stream configuration version (server-managed).")
    org_id: str = Field(..., description="Tenant identifier (server-managed).")
    created_at: datetime = Field(..., description="Creation timestamp (server-managed).")
    updated_at: datetime = Field(..., description="Last update timestamp (server-managed).")


class StreamRecord(_StreamAuthored):
    """Wire projection of one stored stream — PRIVATE `stream-read` contract.

    The shape of every stream record the streams API returns: `GET
    /streams/{id}`, items of `GET /streams` (the list projection omits
    `display_name`/`description`/`mapping`/`created_at` — hence optional),
    and the `_resolved_streams` sidecar on pipeline reads.

    `extra="ignore"` makes the model a projection: dumping a validated
    record carries exactly the declared fields, so stray legacy attributes
    on old rows are dropped from the wire instead of leaking. Distinct from
    `StreamConfig` (DDB round-trip, `extra="allow"`) and `StreamDocument`
    (persisted-record spec pin, `extra="forbid"`).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Any-host pattern (not the env-pinned Literal the WRITE models use):
    # the published contract already accepts any `schemas.analitiq.<tld>`
    # host, and a stored document imported across environments must read
    # back rather than degrade — `$schema` is an informational pointer.
    schema_url: str | None = Field(  # type: ignore[assignment]
        default=None,
        alias="$schema",
        pattern=schema_url_pattern("stream"),
        description="Stream schema URL when stored (any environment host).",
    )

    # Read-side widens the input enum to include backend-managed terminal
    # `error`. Required (no input default): the store stamps `status` at
    # create time, so a row without one is corrupt — a read default would
    # mask it as a healthy draft instead of degrading.
    status: Literal["draft", "active", "inactive", "error"] = Field(  # type: ignore[assignment]
        ...,
        description="Lifecycle status (read side; includes backend-managed `error`).",
    )

    stream_id: str = Field(..., description="Versioned stream ID (server-managed).")
    version: int = Field(..., ge=1, description="Stream configuration version (server-managed).")
    org_id: str | None = Field(default=None, description="Tenant identifier (server-managed).")
    created_at: datetime | None = Field(
        default=None,
        description="Creation timestamp (server-managed; absent in list projections).",
    )
    updated_at: datetime | None = Field(
        default=None, description="Last update timestamp (server-managed)."
    )


class CorruptedStreamPlaceholder(CorruptedPlaceholderBase):
    """Per-row degrade marker for nonconforming stored streams.

    A list response replaces a row that fails `StreamRecord` validation with
    this closed shape (and logs the defect server-side) so one corrupt row
    cannot take down the whole listing. Also substituted per-entry inside a
    pipeline's `_resolved_streams` sidecar. Single-resource GETs never
    return it — they fail loud with a 500 instead.
    """

    stream_id: str | None = Field(
        default=None, description="Versioned stream ID when readable, else omitted."
    )
    pipeline_id: str | None = Field(
        default=None, description="Parent pipeline reference when readable, else omitted."
    )


# Published union for the PRIVATE `stream-read` contract. The closed,
# discriminated placeholder variant comes first so tolerant-union consumers
# match it before the open record shape.
StreamReadRecord = CorruptedStreamPlaceholder | StreamRecord


class StreamPatch(BaseModel):
    """Partial-update model for PATCH/PUT /streams/{id}.

    `pipeline_id` is immutable per the spec and not patchable.
    Server-managed fields are also rejected.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    display_name: str | None = Field(
        default=None, min_length=_DISPLAY_NAME_MIN, max_length=_DISPLAY_NAME_MAX
    )
    description: str | None = Field(default=None, max_length=_DESCRIPTION_MAX)
    status: Literal["draft", "active", "inactive"] | None = Field(default=None)
    tags: list[_Tag] | None = Field(default=None, max_length=_TAGS_MAX)
    source: StreamSource | None = Field(default=None)
    destinations: list[StreamDestination] | None = Field(default=None, min_length=1)
    mapping: StreamMapping | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _validate_top_level(cls, data: Any) -> Any:
        data = _check_authored_top_level(cls, data)
        return _reject_sub_model_server_managed(data)

    @field_validator("display_name")
    @classmethod
    def _validate_display_name_field(cls, v: str | None) -> str | None:
        return _validate_display_name(v)

    @field_validator("tags")
    @classmethod
    def _validate_tags_field(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)

    @field_validator("destinations")
    @classmethod
    def _unique_destination_endpoint_refs(
        cls, v: list[StreamDestination] | None
    ) -> list[StreamDestination] | None:
        if v is None:
            return v
        return _check_unique_destinations(v)


# ---------------------------------------------------------------------------
# Public validators
# ---------------------------------------------------------------------------


def validate_stream_input(payload: dict) -> dict:
    """Validate a stream create/save payload.

    Used by the streams Lambda's create/save routes. Returns the validated
    JSON-serializable dict using public alias names (e.g. `$schema`).
    """
    config = StreamInput.model_validate(payload)
    return config.model_dump(exclude_none=True, mode="json", by_alias=True)


def validate_stream_patch(payload: dict) -> dict:
    """Validate a partial stream update payload.

    Used by the streams Lambda's PATCH/PUT routes. Returns only the keys
    the caller supplied so omitted fields are not silently nulled out.
    """
    patch = StreamPatch.model_validate(payload)
    return patch.model_dump(exclude_unset=True, mode="json", by_alias=True)
