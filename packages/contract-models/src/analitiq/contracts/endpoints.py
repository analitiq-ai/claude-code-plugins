"""
Endpoint schema models.

This module owns the *endpoint document* contract: the JSON shape persisted
to the catalog (public connector endpoints) or materialized as a snapshot
(private database endpoints). Catalog storage paths and ``schema_hash``
canonicalization belong to the runtime layer; they are not fields on the
endpoint document.

Fields typed ``Any`` and described as value-expressions accept the shared
value-expression grammar: refs, templates, literals, and functions.

Endpoint documents have no top-level ``kind`` field. The owning connector's
``kind`` selects the per-kind document class:

  * connector ``kind == "api"`` → :class:`ApiEndpointDoc`
  * connector ``kind in {"database", "nosql", "document"}`` →
    :class:`DatabaseEndpointDoc`

Stream-side endpoint references (``EndpointRef``) live in ``analitiq.contracts.stream``.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from collections.abc import Iterator, Sequence
from typing import Annotated, Any, Literal, Union, get_args
from urllib.parse import unquote

from pydantic import (
    ConfigDict,
    Discriminator,
    Field,
    Strict,
    Tag as UnionTag,
    field_validator,
    model_validator,
)

from analitiq.contracts.arrow_grammar import (
    ARROW_TYPE_PATTERN,
    validate_cross_params,
)
from analitiq.contracts.shared.advisory import AdvisoryValidated
from analitiq.contracts.shared.arrow_shape import (
    ARROW_CONTAINER_SCHEMA_RULES,
    enforce_container_shape,
)
from analitiq.contracts.shared.common import (
    DESCRIPTION_MAX,
    DISPLAY_NAME_MAX,
    DISPLAY_NAME_MIN,
    NO_EDGE_WHITESPACE_PATTERN,
    SLUG_PATTERN,
    StrictModel,
    TAGS_MAX,
    TrimmedTag,
    schema_url_for,
    validate_display_name,
    validate_tags,
)
from analitiq.contracts.value_expression import (
    RESOLUTION_SCOPES,
    iter_expression_strings,
    template_placeholders,
)


# ---------------------------------------------------------------------------
# Constants & regex
# ---------------------------------------------------------------------------

PATH_PLACEHOLDER_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"
# Record field paths preserve segment spelling and casing. The pattern only
# enforces the dotted non-empty-segment shape; identifier chars are
# provider-owned.
RECORD_FIELD_PATH_PATTERN = (
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
METADATA_KEY_PATTERN = r"^[a-z][a-z0-9_]*$"

# Canonical Apache Arrow type vocabulary. `ARROW_TYPE_PATTERN` is GENERATED
# from the engine-published, vendored grammar manifest — see
# `analitiq.contracts.arrow_grammar` (the executable family set is a capability
# surface the engine owns; the contract consumes it, never restates it). It is
# imported above and re-exported here because this module is the
# historical import point for the pattern (stream.py, type_map.py, the schema
# renderer, and external consumers all import it from here).
#
# The pattern accepts exactly the engine-executable canonical spellings:
# scalars, parameterized scalars carrying their parameters (bare `Timestamp` /
# `Decimal128` are unbuildable by PyArrow and must fail at author time, not at
# sync time), and the bare authored-shape JSON container markers:
#   Object — JSON object with declared shape; requires sibling `properties`.
#   List   — JSON array with declared element shape; requires sibling `items`.
#   Json   — opaque JSON object or array; no inner declaration permitted.
# Container recursion + sibling rules are enforced at the model layer
# (analitiq.contracts.stream.ArrowFieldSpec, analitiq.contracts.endpoints.Column)
# and at the JSON Schema walker for API endpoint response/input schemas.
# Cross-parameter bounds the regex cannot express (Decimal scale <= precision)
# are enforced by `validate_cross_params` at every arrow_type acceptance site.

SLUG_RE = re.compile(SLUG_PATTERN)
PATH_PLACEHOLDER_NAME_RE = re.compile(PATH_PLACEHOLDER_NAME_PATTERN)
PATH_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
RECORD_FIELD_PATH_RE = re.compile(RECORD_FIELD_PATH_PATTERN)
METADATA_KEY_RE = re.compile(METADATA_KEY_PATTERN)
ARROW_TYPE_RE = re.compile(ARROW_TYPE_PATTERN)

# Sentinel so the arrow_type walker can distinguish "key absent" from
# "key present with value null"; `null` on either annotation counts as
# "not declared" for pairing purposes.
_MISSING = object()

RESERVED_RESPONSE_SCOPES: frozenset[str] = frozenset(
    {"body", "headers", "status", "records", "record_count", "metadata"}
)

# Declarative mirror of the read/write `response.metadata` key rules: every key
# matches `METADATA_KEY_PATTERN` and none collides with a reserved response
# scope. `propertyNames` validates each key and passes vacuously on the null
# branch of the nullable `metadata` field. Defined once so the read
# (`ResponseExtraction`) and write (`WriteResponse`) contracts cannot drift.
_METADATA_PROPERTY_NAMES: dict[str, Any] = {
    "propertyNames": {
        "pattern": METADATA_KEY_PATTERN,
        "not": {"enum": sorted(RESERVED_RESPONSE_SCOPES)},
    }
}

# Published JSON-Schema pattern for a typed `RefExpression.ref`: the value must
# begin with one of the resolution scopes (imported from the resolver so the
# vocabulary has one home). The `(?:\.|$)` boundary rejects a longer look-alike
# token — `responseX` fails while `response` and `response.body` pass. Only the
# leading scope is contract-checked; sub-path existence and per-phase
# availability are the runtime resolver's concern.
_RESOLUTION_SCOPE_PATTERN = r"^(?:" + "|".join(RESOLUTION_SCOPES) + r")(?:\.|$)"


def _has_known_scope(token: str) -> bool:
    """True when a ref/placeholder token's leading scope is a known resolution
    scope. Stripped like the resolver, which strips before resolving."""
    return token.strip().split(".", 1)[0] in RESOLUTION_SCOPES

# The UNIVERSE of destination write modes — every mode a destination may be
# asked to perform. It keys an API endpoint's `operations.write` map below, and
# it bounds `stream._DB_WRITE_MODES`, which is the (currently equal) subset the
# SQL write path implements. Read that as a bound, not an alias: the two are
# separate facts and `stream.py` says why it does not derive one from the other.
# The tuple is derived from the Literal so those two cannot drift.
#
# `truncate_insert` is the full-refresh mode: empty the destination, then insert
# the run's records. Naming it in this vocabulary is all this contract owes it;
# its delivery semantics (at-least-once by design) and its execution belong to
# the SQL write path, and restating either here would be a copy that rots.
WriteMode = Literal["insert", "upsert", "truncate_insert"]
WRITE_MODES: tuple[str, ...] = get_args(WriteMode)
READ_METHODS: tuple[str, ...] = ("GET", "POST")
WRITE_METHODS: tuple[str, ...] = ("POST", "PUT", "PATCH")
API_ENDPOINT_SCHEMA_URL = schema_url_for("api-endpoint")
DATABASE_ENDPOINT_SCHEMA_URL = schema_url_for("database-endpoint")


# ---------------------------------------------------------------------------
# Shared base — x-* extension policy + frozen instances
# ---------------------------------------------------------------------------


_RESERVED_ENDPOINT_FIELDS: frozenset[str] = frozenset({
    "connector_id",
    "connector_version",
    "connection_id",
    "schema_hash",
})


class _EndpointModel(StrictModel):
    """Endpoint-module base: `StrictModel` plus alias handling and immutability.

    ``frozen=True`` prevents post-construction mutation, so the cross-field
    invariants checked in model validators stay valid for the lifetime of an
    instance.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        frozen=True,
        # Default `model_dump()` to wire-format names. Without this, dumps emit
        # Python attribute names (`schema_url`, `schema_`, `location`,
        # `and_`/`or_`/`not_`) and round-trip via `parse_endpoint(model.model_dump())`
        # would fail because none of those are valid spec keys.
        serialize_by_alias=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_reserved_fields(cls, data: Any) -> Any:
        """Spec-specific error for reserved-field smuggling.

        Reserved names (`connector_id`, `connector_version`, `connection_id`,
        `schema_hash`) are catalog metadata stored alongside the document,
        never in it. `endpoint_id` is the endpoint's own identifier and is
        declared on `_EndpointBase`, so it is allowed. Surface a clear
        message; Pydantic's own `extra="forbid"` would still catch them but
        with a less precise hint.
        """
        if not isinstance(data, dict):
            return data
        declared = _declared_field_names(cls)
        smuggled = sorted(
            k for k in data
            if k in _RESERVED_ENDPOINT_FIELDS and k not in declared
        )
        if smuggled:
            raise ValueError(
                f"reserved field names cannot be authored on endpoint documents: "
                f"{smuggled!r} (spec: §Reserved Fields)"
            )
        return data


# ---------------------------------------------------------------------------
# Value expressions (refs / templates / literals / functions)
# ---------------------------------------------------------------------------


class RefExpression(_EndpointModel):
    """``{"ref": "<scope>.<dotted-path>"}`` value expression."""

    ref: str = Field(
        ...,
        min_length=1,
        pattern=_RESOLUTION_SCOPE_PATTERN,
        description=(
            "Must begin with a known resolution scope: "
            + ", ".join(RESOLUTION_SCOPES)
            + " (spec: §Value Expressions)."
        ),
    )


class TemplateExpression(_EndpointModel):
    """``{"template": "...${scope.path}..."}`` value expression."""

    template: str = Field(..., min_length=1)

    @field_validator("template")
    @classmethod
    def _placeholders_qualified(cls, value: str) -> str:
        # Every `${...}` placeholder must begin with a known resolution scope;
        # an unqualified `${name}` would resolve to "" at runtime (a silent bug).
        # Placeholders are parsed by the shared resolver grammar
        # (`template_placeholders`), so this agrees with the resolver by
        # construction. Model-enforced only — not a published JSON-Schema
        # pattern, so the validator, not `latest.json`, is the complete gate.
        for placeholder in template_placeholders(value):
            if not _has_known_scope(placeholder):
                raise ValueError(
                    f"template placeholder ${{{placeholder}}} must begin with a "
                    "known resolution scope "
                    f"({', '.join(RESOLUTION_SCOPES)}); unqualified placeholders "
                    "are invalid (spec: §Value Expressions)"
                )
        return value


class LiteralExpression(_EndpointModel):
    """``{"literal": <any-json>}`` value expression — opt out of expression interpretation."""

    literal: Any = Field(...)


class FunctionExpression(_EndpointModel):
    """``{"function": <name>, ...}`` registered-function value expression."""

    function: str = Field(..., min_length=1)
    input: Any = Field(default=None)
    map: dict[str, Any] | None = Field(default=None)
    safe: str | None = Field(default=None)


_EXPRESSION_KEYS: tuple[str, ...] = ("ref", "template", "literal", "function")


def _expression_discriminator(v: Any) -> str | None:
    """Pick the expression branch by inspecting which expression key is present."""
    if isinstance(v, dict):
        for k in _EXPRESSION_KEYS:
            if k in v:
                return k
        return None
    if isinstance(v, RefExpression):
        return "ref"
    if isinstance(v, TemplateExpression):
        return "template"
    if isinstance(v, LiteralExpression):
        return "literal"
    if isinstance(v, FunctionExpression):
        return "function"
    return None


Expression = Annotated[
    Union[
        Annotated[RefExpression, UnionTag("ref")],
        Annotated[TemplateExpression, UnionTag("template")],
        Annotated[LiteralExpression, UnionTag("literal")],
        Annotated[FunctionExpression, UnionTag("function")],
    ],
    Discriminator(_expression_discriminator),
]


# ---------------------------------------------------------------------------
# Param contract
# ---------------------------------------------------------------------------


# Declarative mirror of `Param._validate`'s cross-field rules for the published
# schema: a `query` param of `array`/`object` type must declare `style` and
# `explode` (non-null); a `controlled_by` param must not declare `operators`.
# Keyed on the wire name `in` (the `location` alias). `then` pins the required
# fields to non-null types because they render nullable and the runtime demands
# a value.
_PARAM_SCHEMA_RULES: dict[str, Any] = {
    "allOf": [
        {
            "if": {
                "required": ["in", "type"],
                "properties": {
                    "in": {"const": "query"},
                    "type": {"enum": ["array", "object"]},
                },
            },
            "then": {
                "required": ["style", "explode"],
                "properties": {
                    "style": {"type": "string"},
                    "explode": {"type": "boolean"},
                },
            },
        },
        {
            "if": {
                "required": ["controlled_by"],
                "properties": {"controlled_by": {"not": {"type": "null"}}},
            },
            "then": {"properties": {"operators": {"type": "null"}}},
        },
    ],
}


class Param(_EndpointModel):
    """One operation-input contract."""

    model_config = ConfigDict(json_schema_extra=_PARAM_SCHEMA_RULES)

    location: Literal["path", "query", "header", "body"] = Field(
        ..., alias="in", description="Where the param is sent in the request.",
    )
    type: Literal["string", "integer", "number", "boolean", "array", "object"] = Field(
        ..., description="JSON-style validation type for the request input.",
    )
    required: bool = Field(..., description="Whether the param must resolve to a value.")
    description: str | None = Field(default=None)
    default: Any | None = Field(default=None, description="Default value (literal or value expression).")
    enum: list[Any] | None = Field(default=None)
    format: str | None = Field(default=None)
    pattern: str | None = Field(default=None)
    minimum: float | None = Field(default=None)
    maximum: float | None = Field(default=None)
    min_length: int | None = Field(default=None, alias="minLength", ge=0)
    max_length: int | None = Field(default=None, alias="maxLength", ge=0)
    min_items: int | None = Field(default=None, alias="minItems", ge=0)
    max_items: int | None = Field(default=None, alias="maxItems", ge=0)
    operators: list[Literal[
        "eq", "neq", "gt", "gte", "lt", "lte",
        "in", "not_in", "contains", "starts_with", "ends_with",
    ]] | None = Field(
        default=None,
        description=(
            "Subset of the Analitiq operator vocabulary stream filters may use. "
            "Absence means the param is not stream-filterable."
        ),
    )
    controlled_by: Literal["pagination", "replication"] | None = Field(
        default=None,
        description="Marks the param as owned by pagination or replication.",
    )
    style: str | None = Field(default=None, description="OpenAPI query serialization style.")
    explode: bool | None = Field(default=None)

    @model_validator(mode="after")
    def _validate(self) -> "Param":
        # `default` IS an expression tree, but it is swept by the OPERATION, not
        # here: `Param` is shared by reads and writes and cannot tell which it is
        # in, so a sweep here reported a paging consequence to write authors and
        # could not reach `response.schema` for declared-path resolution. See
        # `_sweep_expression_sites`.
        if _collect_singleton_values(self.default, "from_input"):
            raise ValueError(
                "from_input is invalid in params.<name>.default "
                "(spec: §Cross-Field Validation)"
            )
        if self.controlled_by is not None and self.operators is not None:
            raise ValueError(
                "params with `controlled_by` must not declare `operators` "
                "(spec: §Parameter Validation and Operators)"
            )
        if (self.location == "query" and self.type in ("array", "object")
                and (self.style is None or self.explode is None)):
            raise ValueError(
                    f"query params with type={self.type!r} must declare `style` and `explode` "
                    "(spec: §Parameter Validation and Operators)"
                )
        return self


# ---------------------------------------------------------------------------
# Pagination strategies
# ---------------------------------------------------------------------------


class PageSize(_EndpointModel):
    """Optional ``limit`` block shared by paginated strategies that accept page size."""

    param: str | None = Field(default=None)
    # `Strict()` on the int branch is not decoration. Without it pydantic's lax
    # mode coerces `true` -> 1 and `"50"` -> 50, while the rendered schema says
    # `type: integer` and rejects both — so this package would accept documents
    # that every external consumer of the published schema rejects. The premise
    # is that the two agree.
    #
    # `Strict()` costs one deliberate asymmetry in the other direction: JSON
    # Schema's `type: integer` accepts a zero-fraction float, so `50.0` passes
    # the published schema and fails here. Kept, because a document this
    # package accepts must always be one the schema accepts, and that direction
    # still holds. Pinned by
    # `test_float_spelled_integer_is_a_one_directional_gap`.
    #
    # Note the bound reaches the BARE-SCALAR spelling only. `Expression`
    # contains `LiteralExpression`, so `{"literal": 0}` is still a
    # statically-known non-positive page size that validates here. Closing that
    # means bounding the literal expression wherever a positive number is
    # required (`max`, `OffsetCursor.increment_by`, `PageCursor.increment_by`),
    # which is wider than this field's own bound and is not attempted here. Do not
    # describe this field as "the literal branch is bounded"; it is not.
    default: Annotated[int, Field(gt=0), Strict()] | Expression | None = Field(  # type: ignore[valid-type]
        default=None,
        description=(
            "Default page size. Either a positive integer (e.g. `50`) or a "
            "value expression the engine resolves per request — typically "
            "`{ref: runtime.batch_size}`, so the run's configured batch size "
            "flows through. A bare non-positive integer is rejected: it is a "
            "meaningless request rather than one the provider gets to refuse. "
            "The `{literal: N}` expression form carries no such bound."
        ),
    )
    # `Strict()` for the same reason as `default` above: lax coercion would read
    # `"50"` and `true` as integers here while the rendered schema rejects them,
    # so the two halves of the contract would disagree.
    max: Annotated[int, Field(ge=1), Strict()] | None = Field(default=None)


class OffsetCursor(_EndpointModel):
    param: str = Field(..., min_length=1, description="Param that receives the offset/start index.")
    initial: Any = Field(..., description="Initial offset/start index value.")
    increment_by: Annotated[int, Field(gt=0)] | Expression = Field(  # type: ignore[valid-type]
        ...,
        description=(
            "Per-page offset step. Required, with no default: the two offset "
            "families cannot be told apart from the document, so any default "
            "silently breaks one of them. A positive-integer literal is a fixed "
            "step (`1` for page-index-style offsets). A value expression lets the "
            "engine advance by a per-page value: "
            "`{ref: response.record_count}` when `offset` counts records returned "
            "(resolved against that page's response); when it counts the requested "
            "window, step by the *effective* request limit — the page size "
            "actually sent — which is `{ref: runtime.batch_size}` only where no "
            "smaller `limit.max` clamps it; with a cap, use the clamped value so "
            "the step matches the window requested (a raw batch size would "
            "overshoot and skip rows). Spec: §Value Expressions."
        ),
    )


class PageCursor(_EndpointModel):
    param: str = Field(..., min_length=1, description="Param that receives the page number.")
    initial: Any = Field(..., description="Initial page number.")
    increment_by: Any | None = Field(default=None, description="Increment per page (defaults to 1).")


class Cursor(_EndpointModel):
    param: str = Field(..., min_length=1, description="Param that receives the cursor/token.")
    next_cursor: Expression = Field(  # type: ignore[valid-type]
        ...,
        description=(
            "Value expression resolving to the next cursor/token. Spec "
            "§Cross-Field Validation: must be a value expression "
            "(``{ref}``/``{template}``/``{literal}``/``{function}``); "
            "``response_path`` is invalid."
        ),
    )


class Link(_EndpointModel):
    next_url: Expression = Field(  # type: ignore[valid-type]
        ...,
        description=(
            "Value expression resolving to the next absolute URL. Spec "
            "§Cross-Field Validation: must be a value expression; "
            "``response_path`` is invalid."
        ),
    )


class Keyset(_EndpointModel):
    param: str = Field(..., min_length=1, description="Param that receives the last seen key value.")
    order_by_field: str = Field(
        ...,
        pattern=RECORD_FIELD_PATH_PATTERN,
        description=(
            "Dotted record field path used for page ordering. Spec §Cross-Field "
            "Validation requires the dotted-path regex."
        ),
    )
    initial: Any | None = Field(
        default=None,
        description="Initial keyset value. Omit to send no keyset on the first request.",
    )


class OffsetPagination(_EndpointModel):
    """Offset/start-index pagination strategy."""

    type: Literal["offset"] = Field(...)
    offset: OffsetCursor = Field(...)
    limit: PageSize | None = Field(default=None)
    stop_when: "Predicate" = Field(...)


class PagePagination(_EndpointModel):
    """Page-number pagination strategy."""

    type: Literal["page"] = Field(...)
    page: PageCursor = Field(...)
    limit: PageSize | None = Field(default=None)
    stop_when: "Predicate" = Field(...)


class CursorPagination(_EndpointModel):
    """Opaque-cursor pagination strategy."""

    type: Literal["cursor"] = Field(...)
    cursor: Cursor = Field(...)
    limit: PageSize | None = Field(default=None)
    stop_when: "Predicate" = Field(...)


class LinkPagination(_EndpointModel):
    """Next-URL pagination strategy."""

    type: Literal["link"] = Field(...)
    link: Link = Field(...)
    limit: PageSize | None = Field(
        default=None,
        description=(
            "First-request-only page size. Follow-up requests use the "
            "response-supplied `next_url` verbatim — no params traverse — so "
            "`limit` binds into the initial request built from `path` + "
            "params and never modifies a followed link. Wired like every "
            "other strategy: if set, `limit.param` names a declared "
            "`controlled_by: 'pagination'` param bound once in the request. "
            "Spec: §Pagination Strategies."
        ),
    )
    stop_when: "Predicate" = Field(...)


class KeysetPagination(_EndpointModel):
    """Keyset (advance-from-last-key) pagination strategy."""

    type: Literal["keyset"] = Field(...)
    keyset: Keyset = Field(...)
    limit: PageSize | None = Field(default=None)
    stop_when: "Predicate" = Field(...)


Pagination = Annotated[
    Union[
        Annotated[OffsetPagination, UnionTag("offset")],
        Annotated[PagePagination, UnionTag("page")],
        Annotated[CursorPagination, UnionTag("cursor")],
        Annotated[LinkPagination, UnionTag("link")],
        Annotated[KeysetPagination, UnionTag("keyset")],
    ],
    Discriminator("type"),
]


# ---------------------------------------------------------------------------
# Predicate grammar (spec §Stop Conditions)
# ---------------------------------------------------------------------------
#
# Encoded as a discriminated union over the operator key. Per spec §Stop
# Conditions: "A predicate object must contain exactly one operator key." The
# discriminator returns the single non-``x-*`` key and Pydantic dispatches to
# the matching branch — there is no separate "exactly one" validator because
# the type system enforces it.


class PredicateEq(_EndpointModel):
    eq: list[Any] = Field(..., min_length=2, max_length=2)


class PredicateNeq(_EndpointModel):
    neq: list[Any] = Field(..., min_length=2, max_length=2)


class PredicateLt(_EndpointModel):
    lt: list[Any] = Field(..., min_length=2, max_length=2)


class PredicateLte(_EndpointModel):
    lte: list[Any] = Field(..., min_length=2, max_length=2)


class PredicateGt(_EndpointModel):
    gt: list[Any] = Field(..., min_length=2, max_length=2)


class PredicateGte(_EndpointModel):
    gte: list[Any] = Field(..., min_length=2, max_length=2)


class PredicateExists(_EndpointModel):
    exists: Any = Field(...)


class PredicateMissing(_EndpointModel):
    missing: Any = Field(...)


class PredicateEmpty(_EndpointModel):
    empty: Any = Field(...)


class PredicateNotEmpty(_EndpointModel):
    not_empty: Any = Field(...)


class PredicateAnd(_EndpointModel):
    and_: list["Predicate"] = Field(..., alias="and", min_length=1)  # type: ignore[valid-type]


class PredicateOr(_EndpointModel):
    or_: list["Predicate"] = Field(..., alias="or", min_length=1)  # type: ignore[valid-type]


class PredicateNot(_EndpointModel):
    not_: "Predicate" = Field(..., alias="not")  # type: ignore[valid-type]


# Single source of truth for predicate branches — `_PREDICATE_TAGS` and
# `_PREDICATE_INSTANCE_TAGS` derive from this tuple, and an import-time
# assertion below pins the explicit `Union[...]` member list to the same
# tuple so adding an operator can't drift any of the four structures.
_PRED_BRANCHES: tuple[tuple[str, type], ...] = (
    ("eq", PredicateEq),
    ("neq", PredicateNeq),
    ("lt", PredicateLt),
    ("lte", PredicateLte),
    ("gt", PredicateGt),
    ("gte", PredicateGte),
    ("exists", PredicateExists),
    ("missing", PredicateMissing),
    ("empty", PredicateEmpty),
    ("not_empty", PredicateNotEmpty),
    ("and", PredicateAnd),
    ("or", PredicateOr),
    ("not", PredicateNot),
)
_PREDICATE_TAGS: frozenset[str] = frozenset(t for t, _ in _PRED_BRANCHES)
_PREDICATE_INSTANCE_TAGS: dict[type, str] = {c: t for t, c in _PRED_BRANCHES}


def _predicate_discriminator(v: Any) -> str | None:
    """Pick the predicate branch from the single (non-``x-*``) operator key."""
    if isinstance(v, dict):
        op_keys = [
            k for k in v
            if isinstance(k, str) and not k.startswith("x-") and k in _PREDICATE_TAGS
        ]
        # Exactly one operator key is required by spec; any other count
        # produces an "Unable to extract tag" union error.
        if len(op_keys) == 1:
            return op_keys[0]
        return None
    return _PREDICATE_INSTANCE_TAGS.get(type(v))


Predicate = Annotated[
    Union[
        Annotated[PredicateEq, UnionTag("eq")],
        Annotated[PredicateNeq, UnionTag("neq")],
        Annotated[PredicateLt, UnionTag("lt")],
        Annotated[PredicateLte, UnionTag("lte")],
        Annotated[PredicateGt, UnionTag("gt")],
        Annotated[PredicateGte, UnionTag("gte")],
        Annotated[PredicateExists, UnionTag("exists")],
        Annotated[PredicateMissing, UnionTag("missing")],
        Annotated[PredicateEmpty, UnionTag("empty")],
        Annotated[PredicateNotEmpty, UnionTag("not_empty")],
        Annotated[PredicateAnd, UnionTag("and")],
        Annotated[PredicateOr, UnionTag("or")],
        Annotated[PredicateNot, UnionTag("not")],
    ],
    Discriminator(_predicate_discriminator),
]

def _union_tags(annotated_union: Any) -> frozenset[str]:
    """Extract `Tag(<name>)` values from an `Annotated[Union[Annotated[..., Tag(...)], ...], ...]`.

    Used to introspect the actual published `Union[...]` membership at import
    time so the drift guards detect any divergence between the explicit Union
    list, the `_PRED_BRANCHES`-style source-of-truth tuple, and downstream
    consumers like the discriminator's tag set.
    """
    tags: set[str] = set()
    union_arg, _discr = get_args(annotated_union)
    for member in get_args(union_arg):
        for meta in get_args(member)[1:]:
            if isinstance(meta, UnionTag):
                tags.add(meta.tag)
    return frozenset(tags)


# Drift guards — the actual `Union[...]` membership of each discriminated
# union must equal the source-of-truth tag list it derives from. Asserting
# at import time turns silent dispatch failures (`Unable to extract tag
# using discriminator`) into a clear ImportError when the structures
# diverge — including the case where a maintainer adds a branch to one
# list but not the other.
if _PREDICATE_TAGS != _union_tags(Predicate):
    raise AssertionError(
        f"Predicate Union members {sorted(_union_tags(Predicate))!r} do not match "
        f"_PRED_BRANCHES {sorted(_PREDICATE_TAGS)!r}")
if _union_tags(Expression) != frozenset(_EXPRESSION_KEYS):
    raise AssertionError(
        f"Expression Union members {sorted(_union_tags(Expression))!r} do not match "
        f"_EXPRESSION_KEYS {sorted(_EXPRESSION_KEYS)!r}")
if _union_tags(Pagination) != frozenset({"offset", "page", "cursor", "link", "keyset"}):
    raise AssertionError(
        f"Pagination Union members {sorted(_union_tags(Pagination))!r} do not match "
        "the expected pagination strategy set")


# Resolve forward refs: pagination → Predicate, plus Predicate's recursive
# and/or/not branches.
PredicateAnd.model_rebuild()
PredicateOr.model_rebuild()
PredicateNot.model_rebuild()
OffsetPagination.model_rebuild()
PagePagination.model_rebuild()
CursorPagination.model_rebuild()
LinkPagination.model_rebuild()
KeysetPagination.model_rebuild()


# ---------------------------------------------------------------------------
# Replication (spec §Replication)
# ---------------------------------------------------------------------------
#
# `CursorMapping` is a callable-discriminated union of single-param vs
# bounded-window forms. The wire format carries no tag; the discriminator
# picks the branch by detecting which form's fields are present. Mixed-form
# rejection lives on `Replication.cursor_mappings` as a `mode="before"`
# validator — keeping it there means the published JSON Schema's `oneOf`
# carries only the two real shapes, mirroring the runtime contract.


class SingleCursorMapping(_EndpointModel):
    """Single-param cursor mapping. Spec: §Replication."""

    cursor_field: str = Field(
        ...,
        pattern=RECORD_FIELD_PATH_PATTERN,
        description="Dotted record field path used as the incremental watermark.",
    )
    param: str = Field(..., min_length=1)
    operator: Literal["gt", "gte", "lt", "lte"]
    format: Literal["date-time", "date", "epoch_seconds", "epoch_milliseconds"] | None = Field(default=None)


class WindowCursorMapping(_EndpointModel):
    """Bounded-window cursor mapping (start/end provider params). Spec: §Replication."""

    cursor_field: str = Field(
        ...,
        pattern=RECORD_FIELD_PATH_PATTERN,
        description="Dotted record field path used as the incremental watermark.",
    )
    start_param: str = Field(..., min_length=1)
    end_param: str = Field(..., min_length=1)
    start_operator: Literal["gt", "gte", "lt", "lte"]
    end_operator: Literal["gt", "gte", "lt", "lte"]
    format: Literal["date-time", "date", "epoch_seconds", "epoch_milliseconds"] | None = Field(default=None)


_WINDOW_CM_FIELDS: tuple[str, ...] = ("start_param", "end_param", "start_operator", "end_operator")
_SINGLE_CM_FIELDS: tuple[str, ...] = ("param", "operator")


def _cursor_mapping_discriminator(v: Any) -> str | None:
    """Pick the single or window branch by detecting which form's fields are present.

    Pure-window inputs route to ``window``; everything else (pure-single,
    or input with neither form's fields) routes to ``single`` so common
    typos surface as "missing required field" errors. Mixed-form inputs
    are rejected upstream by ``Replication._reject_mixed_cursor_forms``,
    so the discriminator never has to handle that case.
    """
    if isinstance(v, dict):
        if any(f in v for f in _WINDOW_CM_FIELDS):
            return "window"
        return "single"
    if isinstance(v, WindowCursorMapping):
        return "window"
    if isinstance(v, SingleCursorMapping):
        return "single"
    return None


CursorMapping = Annotated[
    Union[
        Annotated[SingleCursorMapping, UnionTag("single")],
        Annotated[WindowCursorMapping, UnionTag("window")],
    ],
    Discriminator(_cursor_mapping_discriminator),
]


class Replication(_EndpointModel):
    """Replication block for API read operations. Spec: §Replication."""

    supported_methods: list[Literal["full_refresh", "incremental"]] = Field(
        ..., min_length=1,
    )
    cursor_mappings: list[CursorMapping] = Field(  # type: ignore[type-arg]
        ..., min_length=1,
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_mixed_cursor_forms(cls, data: Any) -> Any:
        # Spec §Replication forbids mixing single-param (`param`/`operator`)
        # with bounded-window (`start_param`/`end_param`/`start_operator`/
        # `end_operator`) fields in one cursor mapping. Catching this here
        # (mode="before", on the parent) keeps the published JSON Schema's
        # CursorMapping `oneOf` to two real branches; encoding the rule as a
        # synthetic third union branch would leak a permissively-shaped
        # `$def` that external JSON-Schema validators silently accept.
        if not isinstance(data, dict):
            return data
        cms = data.get("cursor_mappings")
        if not isinstance(cms, list):
            return data
        for i, cm in enumerate(cms):
            if not isinstance(cm, dict):
                continue
            single_keys = sorted(k for k in cm if k in _SINGLE_CM_FIELDS)
            window_keys = sorted(k for k in cm if k in _WINDOW_CM_FIELDS)
            if single_keys and window_keys:
                raise ValueError(
                    f"cursor_mappings[{i}] must not mix single-param and "
                    f"bounded-window forms; got single={single_keys!r} and "
                    f"window={window_keys!r} (spec: §Replication — declare "
                    "exactly one form)"
                )
        return data


# ---------------------------------------------------------------------------
# Request, Response, Operation blocks
# ---------------------------------------------------------------------------


# Declarative mirror of `_RequestBase._validate`'s presence correlation: a
# `path` that declares `{placeholder}`s requires a `path_params` object; a
# `path` with none forbids `path_params` (absent or null). The exact key-set
# equality (path_params keys == placeholder names) is instance-relative set
# logic that stock JSON Schema cannot express — it is enforced by
# `_RequestBase._validate` and catalogued in the advisory registry (ADV-ENDP-001).
_REQUEST_SCHEMA_RULES: dict[str, Any] = {
    "allOf": [
        {
            "if": {"required": ["path"], "properties": {"path": {"pattern": r"\{[^{}]+\}"}}},
            "then": {"required": ["path_params"], "properties": {"path_params": {"type": "object"}}},
        },
        {
            "if": {"required": ["path"], "properties": {"path": {"not": {"pattern": r"\{[^{}]+\}"}}}},
            "then": {"properties": {"path_params": {"type": "null"}}},
        },
    ],
}


#: Request fields that carry author-written value expressions, and so must be
#: swept. Stated once because both operations build their site tables from it:
#: dropping a slot from one table and not the other is how `request.path_params`
#: came to be checked on reads and not on writes. `_EXPRESSION_SLOTS_ARE_COMPLETE`
#: pins it against the models, so a new expression-carrying request field cannot
#: be added without either landing here or failing the suite.
_REQUEST_EXPRESSION_SLOTS: tuple[str, ...] = (
    "path_params",
    "headers",
    "query",
    "body",
)


class _RequestBase(AdvisoryValidated, _EndpointModel):
    """Common request fields shared by read and write operations."""

    model_config = ConfigDict(json_schema_extra=_REQUEST_SCHEMA_RULES)

    transport_ref: str | None = Field(
        default=None,
        description=(
            "Named transport this operation dispatches through; defaults to "
            "`default_transport`. **Containment** (spec: §Transport Selection) "
            "has two halves, and they are guaranteed differently. "
            "(1) NAME: the value must be one the sibling `connector.json` "
            "declares in `transports` — a request dispatches only through a "
            "transport the connector declares. This half is checked at author "
            "time by the validator's `endpoint-transport-ref` check (the "
            "endpoint and the connector are separate documents, so no "
            "single-document model validator can see both sides). It is an "
            "ERROR only when the sibling `connector.json` is in hand; "
            "validating an endpoint file whose connector is not reachable "
            "emits a warning saying the name was not checked, because a check "
            "that cannot see the other document cannot refuse the name. "
            "(2) ORIGIN: the intended rule is that every URL this request "
            "produces, including a next-page link followed from "
            "`pagination.link.next_url`, lands on the origin of a declared "
            "transport. **This half is enforced by nothing today** — not by "
            "this contract, not by `analitiq-validator`, and not by the engine "
            "as it stands: the engine opens ONE session at connect time from "
            "`default_transport` and pins the read path to that single origin, "
            "the write path has no origin guard at all, and no production call "
            "site selects a transport per operation. So declaring a second "
            "transport does NOT today make a second origin reachable — a "
            "next-page link that leaves the connection origin is refused. "
            "Stated here as the contract's intent, not as a guarantee: "
            "closing it takes per-operation transport selection plus a "
            "write-path origin guard in the engine."
        ),
    )
    path: str = Field(
        ...,
        min_length=1,
        description="Path or relative URL on the selected transport.",
        json_schema_extra={"not": {"pattern": r"\$\{"}},
    )
    path_params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Bindings for `{name}` placeholders in `path`. Each value is a "
            "`{from_param: <name>}` expression naming a declared `in: path` "
            "param. `{from_input: ...}` is NOT permitted here on a read "
            "operation — no record is in scope when a read request is built "
            "(the write form is documented on the write request's own "
            "`path_params`). **Encoding is engine-owned**: the engine "
            "percent-encodes each substituted value as ONE path segment, so a "
            "binding must not wrap its value in `url_encode` or "
            "`base64_encode` — that double-encodes (`a b` arrives as "
            "`a%2520b`) and the provider 404s or matches the wrong resource. "
            "Spec: §Request Parameter Binding."
        ),
    )
    headers: dict[str, Any] | None = Field(
        default=None,
        description="Endpoint-declared request headers; values may be literals or `{from_param}`/`{ref}`/`{template}`.",
    )
    headers_remove: list[str] | None = Field(
        default=None,
        description="Header names to delete from inherited transport defaults (case-insensitive).",
    )
    query: dict[str, Any] | None = Field(
        default=None,
        description="Endpoint-declared query parameters; values may be literals or expressions.",
    )

    @model_validator(mode="after")
    def _validate(self) -> "_RequestBase":
        placeholders = PATH_PLACEHOLDER_RE.findall(self.path)
        if len(placeholders) != len(set(placeholders)):
            raise ValueError(
                f"request.path contains duplicate placeholders in {self.path!r} "
                "(spec: §Request Parameter Binding)"
            )
        for ph in placeholders:
            if not PATH_PLACEHOLDER_NAME_RE.match(ph):
                raise ValueError(
                    f"path placeholder {ph!r} must match "
                    f"{PATH_PLACEHOLDER_NAME_PATTERN!r} (spec: §Request Parameter Binding)"
                )
        if "${" in self.path:
            raise ValueError(
                "request.path must not contain ${...} template expressions "
                "(spec: §Request Parameter Binding)"
            )
        placeholder_set = set(placeholders)
        # Use explicit `is None`: `path_params={}` is meaningfully different
        # from omitted, and the falsy-check version treats them the same.
        if placeholder_set and self.path_params is None:
            raise ValueError(
                f"request.path declares placeholders {sorted(placeholder_set)!r} but "
                "request.path_params is missing (spec: §Request Parameter Binding)"
            )
        if not placeholder_set and self.path_params is not None:
            raise ValueError(
                "request.path_params is present but request.path has no placeholders "
                "(spec: §Request Parameter Binding)"
            )
        if self.path_params is not None:
            extra = set(self.path_params) - placeholder_set
            missing = placeholder_set - set(self.path_params)
            if extra or missing:
                raise ValueError(
                    f"request.path_params keys must equal placeholders in path; "
                    f"extra={sorted(extra)!r}, missing={sorted(missing)!r} "
                    "(spec: §Request Parameter Binding)"
                )
        return self


class GetReadRequest(_RequestBase):
    """Provider request for a GET-method API read operation. GET declares no body."""

    method: Literal["GET"] = Field(..., description="Read HTTP method.")


class PostReadRequest(_RequestBase):
    """Provider request for a POST-method API read operation (query-in-body reads)."""

    method: Literal["POST"] = Field(..., description="Read HTTP method.")
    body: Any | None = Field(
        default=None,
        description="JSON request body. May mix literals with `{from_param}`.",
    )


# `method`-discriminated read request: only the POST branch declares `body`, so
# the published JSON Schema structurally forbids a body on a GET read (the rule
# formerly enforced only by a `@model_validator`). Both branches share the
# `_RequestBase` fields.
ReadRequest = Annotated[
    GetReadRequest | PostReadRequest,
    Field(discriminator="method"),
]


class WriteRequest(_RequestBase):
    """Provider request for an API write mode."""

    method: Literal["POST", "PUT", "PATCH"] = Field(
        ..., description="Write HTTP method (closed v1 enum).",
    )
    # Redeclared solely to carry the write-side description. A write is the one
    # place a record IS in scope when the request is built, so `path_params`
    # admits a second binding form here that `_RequestBase` refuses.
    path_params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Bindings for `{name}` placeholders in `path`. A value is either "
            "`{from_param: <name>}`, naming a declared `in: path` param that "
            "must itself carry a `default` (a write param has no other source, "
            "so a sourceless one can never resolve), or "
            '`{from_input: "record.<dotted>"}`, which reads one field of the '
            "record being written (`PATCH /contacts/{id}`) and declares no "
            "param at all. `record` and `records[.<dotted>]` are refused — a "
            "path segment carries exactly one value — and the field a "
            "`record.<dotted>` binding addresses must be declared in this "
            "mode's `input.schema`. A `from_input` path_param is mutually "
            "exclusive with `batching`: a multi-record request has no single "
            "record to take the segment from. **Encoding is engine-owned**: "
            "the engine percent-encodes each substituted value as ONE path "
            "segment, so a binding must not wrap its value in `url_encode` or "
            "`base64_encode` — that double-encodes. Spec: §Request Parameter "
            "Binding."
        ),
    )
    body: Any | None = Field(
        default=None,
        description="JSON request body. May mix literals with `{from_param}` (and `{from_input}` for writes).",
    )


# JSON Schema 2020-12 keywords whose values are themselves schemas (or maps/
# lists of schemas). Used by the arrow_type walker to recurse only through
# structural positions — never through `default`, `examples`, `const`, etc.,
# which can legally carry arbitrary user data shaped like a schema.
JSON_SCHEMA_SUBSCHEMA_KEYS: frozenset[str] = frozenset({
    "properties", "patternProperties", "$defs", "definitions",
    "dependentSchemas",
})
JSON_SCHEMA_LIST_OF_SCHEMA_KEYS: frozenset[str] = frozenset({
    "allOf", "anyOf", "oneOf", "prefixItems",
})
JSON_SCHEMA_SINGLE_SCHEMA_KEYS: frozenset[str] = frozenset({
    "items", "contains", "additionalProperties", "propertyNames",
    "unevaluatedItems", "unevaluatedProperties",
    "not", "if", "then", "else",
    # 2020-12 §8.5. An annotation rather than an assertion, but it IS a schema
    # position: a subtree here would otherwise escape both walkers below, which
    # is the one thing the shared sets exist to prevent. Draft-07 `dependencies`
    # is deliberately absent — 2020-12 does not define it, so a conformant
    # validator never applies its subtree at all (it is inert data, like
    # `default`), and treating its property-name-array form as a schema map
    # would reject legal draft-07 shapes.
    "contentSchema",
})


def _validate_arrow_type_in_json_schema(
    schema: Any, path: str, errors: list[str]
) -> None:
    """Walk a JSON Schema document and enforce arrow_type contract rules.

    Spec §Native and Arrow Types:
      (1) any subschema carrying `arrow_type` must match the canonical Arrow
          type vocabulary — bare parameterized forms like 'Timestamp' or
          'Decimal128' are rejected at author time.
      (2) any subschema declaring `native_type` or `arrow_type` must declare
          both. Pairing is enforced per node; the walker does not distinguish
          leaf and inner subschemas.
    """
    # JSON Schema 2020-12 permits `true` / `false` as a whole-schema short-form
    # ("anything" / "nothing"). Those are valid but carry no arrow_type, so
    # walk past them. Non-bool, non-dict values in a schema position are
    # malformed JSON Schema; surface them rather than silently skipping.
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        errors.append(
            f"{path} is not a JSON Schema object/boolean (got "
            f"{type(schema).__name__}); cannot validate arrow_type "
            "(spec: §Native and Arrow Types)"
        )
        return

    native_value = schema.get("native_type", _MISSING)
    arrow_value = schema.get("arrow_type", _MISSING)
    has_native = native_value is not _MISSING and native_value is not None
    has_arrow = arrow_value is not _MISSING and arrow_value is not None
    if arrow_value is not _MISSING and arrow_value is not None:
        # fullmatch (not match) — `$` in ARROW_TYPE_PATTERN matches before a
        # trailing `\n` under Python re default flags, so `"Utf8\n"` would
        # slip through match() but is correctly rejected by Pydantic's
        # rust-regex field-level pattern. Use fullmatch here for parity.
        if not isinstance(arrow_value, str) or not ARROW_TYPE_RE.fullmatch(arrow_value):
            errors.append(
                f"{path}.arrow_type={arrow_value!r} is not a canonical Arrow "
                "type. Parameterized canonical types must carry their "
                "parameters: e.g. 'Timestamp(MICROSECOND)', "
                "'Decimal128(38, 9)', 'FixedSizeBinary(16)' "
                "(spec: §Native and Arrow Types)"
            )
        else:
            # Cross-parameter bounds the pattern cannot express
            # (Decimal scale <= precision).
            try:
                validate_cross_params(arrow_value)
            except ValueError as exc:
                errors.append(
                    f"{path}.arrow_type: {exc} (spec: §Native and Arrow Types)"
                )
    if has_native ^ has_arrow:
        missing = "arrow_type" if has_native else "native_type"
        errors.append(
            f"{path} declares only one of native_type/arrow_type; typed field "
            f"schemas must carry both (missing {missing!r}; spec: §Native and "
            "Arrow Types)"
        )

    # Authored-shape JSON container markers (Object/List/Json) require
    # specific sibling keys. `properties` and `items` are standard JSON
    # Schema keywords already meaningful at this node, so we enforce
    # presence/absence inline here rather than constructing a model.
    #
    # Why this is not just `enforce_container_shape(...)`: the walker
    # validates response/input JSON-Schema slots — raw dicts — whereas
    # `analitiq.contracts.shared.arrow_shape.enforce_container_shape` runs after
    # Pydantic has coerced sibling keys into typed `ArrowFieldSpec` /
    # `ColumnFieldSpec` instances. Pydantic's type coercion implicitly
    # rejects JSON Schema 2020-12 shorthands (`items: true|false`,
    # tuple-form `items: [...]`) at the model layer, but the walker has
    # no such coercion and must reject them explicitly. The two paths
    # cover the same matrix but in different dialects; do not collapse
    # them without preserving the dialect-specific rejections below.
    if has_arrow and isinstance(arrow_value, str):
        properties_value = schema.get("properties", _MISSING)
        items_value = schema.get("items", _MISSING)
        # `null` siblings count as "not declared" so error messages are
        # precise rather than recursing into None downstream.
        has_properties = (
            properties_value is not _MISSING and properties_value is not None
        )
        has_items = items_value is not _MISSING and items_value is not None
        if arrow_value == "Object":
            if not has_properties:
                errors.append(
                    f"{path}.arrow_type='Object' requires sibling 'properties' "
                    "(spec: §Native and Arrow Types)"
                )
            elif not isinstance(properties_value, dict) or not properties_value:
                # Empty dict or non-dict shape is structurally meaningless for
                # a declared Object.
                errors.append(
                    f"{path}.arrow_type='Object' requires non-empty "
                    "'properties' map (spec: §Native and Arrow Types)"
                )
            if has_items:
                errors.append(
                    f"{path}.arrow_type='Object' must not carry 'items' "
                    "(spec: §Native and Arrow Types)"
                )
        elif arrow_value == "List":
            if not has_items:
                errors.append(
                    f"{path}.arrow_type='List' requires sibling 'items' "
                    "(spec: §Native and Arrow Types)"
                )
            elif not isinstance(items_value, dict):
                # Reject JSON Schema boolean shorthand (`items: true/false`)
                # and tuple-form (`items: [...]`) — both contradict the
                # single-spec contract that Column / ArrowFieldSpec enforce.
                errors.append(
                    f"{path}.arrow_type='List' requires 'items' to be a "
                    "single field spec (object); boolean and tuple forms "
                    "are not permitted (spec: §Native and Arrow Types)"
                )
            if has_properties:
                errors.append(
                    f"{path}.arrow_type='List' must not carry 'properties' "
                    "(spec: §Native and Arrow Types)"
                )
        elif arrow_value == "Json":
            if has_properties or has_items:
                errors.append(
                    f"{path}.arrow_type='Json' is opaque and must not carry "
                    "'properties' or 'items' (spec: §Native and Arrow Types)"
                )
        else:
            # Scalar or parameterized arrow_type (Utf8, Int64,
            # Decimal128(38, 9), etc.): JSON-container siblings are not legal.
            # The Pydantic helper rejects this on the model side; the walker
            # must mirror it on the JSON Schema side per spec §Native and
            # Arrow Types ("must not appear on scalar or parameterized
            # arrow_type values").
            if has_properties or has_items:
                errors.append(
                    f"{path}.arrow_type={arrow_value!r} must not carry "
                    "'properties' or 'items'; those are only valid for the "
                    "bare authored-shape markers 'Object' / 'List' "
                    "(spec: §Native and Arrow Types)"
                )

    # Each traversal always re-enters the walker so its entry-point bool/dict
    # check (above) runs on every visited slot — that's the only place
    # malformed schema positions (e.g. `items: "Int64"`) get surfaced.
    for key in JSON_SCHEMA_SUBSCHEMA_KEYS:
        child = schema.get(key)
        if isinstance(child, dict):
            for sub_key, sub_schema in child.items():
                _validate_arrow_type_in_json_schema(
                    sub_schema, f"{path}.{key}.{sub_key}", errors
                )
    for key in JSON_SCHEMA_LIST_OF_SCHEMA_KEYS:
        child = schema.get(key)
        if isinstance(child, list):
            for idx, sub_schema in enumerate(child):
                _validate_arrow_type_in_json_schema(
                    sub_schema, f"{path}.{key}[{idx}]", errors
                )
    for key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS:
        if key not in schema:
            continue
        child = schema[key]
        # Draft 2019-09 tuple-form `items: [...]` is still authored in
        # parts of the catalog; iterate per position. Draft 2020-12 uses
        # `prefixItems` for the same purpose (handled by the list-keyword
        # block above).
        if isinstance(child, list):
            for idx, sub_schema in enumerate(child):
                _validate_arrow_type_in_json_schema(
                    sub_schema, f"{path}.{key}[{idx}]", errors
                )
        else:
            _validate_arrow_type_in_json_schema(child, f"{path}.{key}", errors)


#: Reference keywords the contract does not author, and why each is refused
#: rather than tolerated. Every one of them would let a subtree escape both
#: structural walkers, which is the single harm ADV-ENDP-026 exists to close.
_REFUSED_REFERENCE_KEYWORDS: dict[str, str] = {
    "$id": (
        "declares a new base URI, which under 2020-12 retargets every `#`-leading "
        "reference in its subtree at another document — so a reference this "
        "contract would resolve locally is external to a conformant resolver, and "
        "the two disagree about which subschema applied. An embedded schema is "
        "not separately addressable, so it has no use for an identity"
    ),
    "$anchor": (
        "names a plain-name fragment target. This contract addresses subschemas "
        "only by JSON Pointer (`#/$defs/<name>`), so an anchor is unreachable "
        "here — declare the shape under `$defs` and point at it"
    ),
    "$dynamicRef": (
        "resolves against the dynamic scope at evaluation time, so its target "
        "cannot be determined by reading this document; nothing offline can "
        "annotation-check it or cover it with a type map. Use `$ref` to "
        "`#/$defs/<name>`"
    ),
    "$dynamicAnchor": (
        "exists only to be a `$dynamicRef` target, and `$dynamicRef` is not "
        "authorable here"
    ),
    "$recursiveRef": (
        "is the draft 2019-09 spelling of `$dynamicRef` and is refused for the "
        "same reason. Use `$ref` to `#/$defs/<name>`"
    ),
    "$recursiveAnchor": (
        "is the draft 2019-09 spelling of `$dynamicAnchor` and is refused for "
        "the same reason"
    ),
}


def _validate_schema_refs(
    schema: Any, path: str, errors: list[str], root: Any = None
) -> None:
    """Every reference in an embedded schema must be IN-DOCUMENT, must resolve,
    and must land on a schema.

    ADV-ENDP-026. `$ref` is authorable — `JsonSchemaPropertyNode` enumerates
    `$defs` as a recursive position and the arrow_type walker below descends
    into it, so a `#/$defs/...` target is annotation-checked like any other
    Several spellings are not, and each fails silently (a count here would rot —
    this list grew by three after it was first written):

    * a NON-LOCAL ref (`https://…`, `common.json#/…`) names a document that is
      not here. Nothing in this contract's path is allowed to fetch it — the
      validator runs offline by design, the engine resolves the endpoint
      document with no network, and the conformance kit executes a read with no
      network. So the target is never annotation-checked, never covered by the
      connector's type map, and never seen by anything that could object. The
      subtree simply is not validated, and the author is told nothing.
    * a DANGLING local ref (`#/$defs/Typo`) points at nothing. It asserts no
      constraint, so every instance satisfies it — an "everything passes" hole
      wearing the shape of a declaration.
    * a local ref into a NON-SCHEMA position (`#/properties/x/default`). Both
      walkers deliberately skip `default`/`examples`/`const`/`enum`, because
      those carry arbitrary user data that can be shaped exactly like a schema.
      A pointer into one reaches a subtree nothing checked — the same hole as a
      non-local ref, spelled locally.
    * the reference keywords in :data:`_REFUSED_REFERENCE_KEYWORDS` (`$id`,
      `$dynamicRef`, anchors), each of which either moves the base URI out from
      under the resolver or defers the target to evaluation time.

    Refusing all of them is what makes it safe for declared-path resolution to
    FOLLOW a `$ref` (see :func:`_property_contributors`): by the time a path is
    resolved, every ref it can meet is local, real, and lands on a node both
    walkers visited — so following one cannot land the resolver on a type
    nothing verified.

    Walks the same structural positions as
    :func:`_validate_arrow_type_in_json_schema` — the shared
    ``_JSON_SCHEMA_*_KEYS`` sets, so the two cannot disagree about what counts
    as a schema position. Never follows a `$ref` itself: the walk is over the
    document's own tree, and every local target is already part of it.
    """
    # `root` is threaded down so a ref deep in the tree still resolves against
    # the WHOLE embedded schema — `$defs` lives at the top, and resolving
    # against the current subtree would call every legitimate ref dangling.
    root = schema if root is None else root
    # `true` / `false` are legal whole-schema short-forms carrying no `$ref`.
    if isinstance(schema, bool) or not isinstance(schema, dict):
        return

    for keyword, why in _REFUSED_REFERENCE_KEYWORDS.items():
        if keyword in schema:
            errors.append(
                f"{path}.{keyword} is not authorable in an embedded "
                f"response/input schema: `{keyword}` {why} "
                "(spec: §API Response Extraction — embedded schema references)"
            )

    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str):
            errors.append(
                f"{path}.$ref must be a string (got {type(ref).__name__}) "
                "(spec: §API Response Extraction — embedded schema references)"
            )
        elif not ref.startswith("#"):
            errors.append(
                f"{path}.$ref={ref!r} is not an in-document reference. Embedded "
                "response/input schemas are resolved offline — by the validator, "
                "by the engine and by the conformance kit — so a reference out of "
                "the document can never be fetched and its target would go "
                "unvalidated. Inline the shape, or put it in this document's "
                "`$defs` and reference it as '#/$defs/<name>' "
                "(spec: §API Response Extraction — embedded schema references)"
            )
        elif ref != "#" and not ref.startswith("#/"):
            # A plain-name fragment (`#name`) is an `$anchor` reference. Saying
            # "dangling" here would be a wrong diagnosis — the anchor may well
            # be declared — and would send the author looking for a typo that
            # is not there.
            errors.append(
                f"{path}.$ref={ref!r} is a plain-name fragment (an `$anchor` "
                "reference). This contract addresses subschemas only by JSON "
                "Pointer: declare the shape under `$defs` and reference it as "
                "'#/$defs/<name>' "
                "(spec: §API Response Extraction — embedded schema references)"
            )
        elif isinstance(resolve_schema_ref(root, ref), bool):
            # `true`/`false` is a legal 2020-12 whole-schema short-form and both
            # structural walkers accept it, so the target IS a schema — it just
            # is not a dict. Without this branch it fell through to the
            # non-schema-position message and told the author to move a shape
            # that was already sitting in `$defs`, sending them hunting for a
            # `default`/`examples` payload that does not exist.
            errors.append(
                f"{path}.$ref={ref!r} resolves to a boolean schema. `true`/`false` "
                "declare nothing about a value's shape, so a path through this "
                "reference can never resolve to a typed declaration — inline the "
                "shape you mean, or point at a subschema that declares one "
                "(spec: §API Response Extraction — embedded schema references)"
            )
        elif not isinstance(resolve_schema_ref(root, ref), dict):
            if resolve_local_pointer(root, ref) is not _MISSING:
                errors.append(
                    f"{path}.$ref={ref!r} points into a non-schema position. "
                    "`default`, `examples`, `const` and `enum` carry arbitrary "
                    "data, so nothing validates what is inside them — a "
                    "reference there is an unchecked subtree wearing the shape "
                    "of a declaration. Move the shape to this document's "
                    "`$defs` and reference it as '#/$defs/<name>' "
                    "(spec: §API Response Extraction — embedded schema references)"
                )
            else:
                errors.append(
                    f"{path}.$ref={ref!r} does not resolve to a schema in this "
                    "document. A dangling reference asserts nothing, so every "
                    "instance satisfies it "
                    "(spec: §API Response Extraction — embedded schema references)"
                )

    for key in JSON_SCHEMA_SUBSCHEMA_KEYS:
        child = schema.get(key)
        if not isinstance(child, dict):
            continue
        for sub_key, sub_schema in child.items():
            _validate_schema_refs(sub_schema, f"{path}.{key}.{sub_key}", errors, root)
    for key in JSON_SCHEMA_LIST_OF_SCHEMA_KEYS:
        child = schema.get(key)
        if not isinstance(child, list):
            continue
        for idx, sub_schema in enumerate(child):
            _validate_schema_refs(sub_schema, f"{path}.{key}[{idx}]", errors, root)
    for key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS:
        if key not in schema:
            continue
        child = schema[key]
        # Draft 2019-09 tuple-form `items: [...]`, as above.
        if isinstance(child, list):
            for idx, sub_schema in enumerate(child):
                _validate_schema_refs(sub_schema, f"{path}.{key}[{idx}]", errors, root)
        else:
            _validate_schema_refs(child, f"{path}.{key}", errors, root)


class ResponseExtraction(_EndpointModel):
    """Read operation ``response`` block."""

    # Mirror of `_validate`: `records.ref` is anchored at `response.body[.<path>]`
    # and `metadata` keys obey the shared key rules. The records anchor is a
    # model-level `allOf` (not a sibling of the `records` `$ref`) so it composes
    # portably across draft-07/2020-12 consumers.
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "properties": {
                        "records": {
                            "properties": {"ref": {"pattern": r"^response\.body(?:\..+)?$"}}
                        },
                        "metadata": _METADATA_PROPERTY_NAMES,
                    }
                }
            ]
        }
    )

    records: RefExpression = Field(
        ...,
        description=(
            "Expression that resolves to the iterable record collection. Must "
            "be a `{ref}` whose path starts with `response.body`. The path "
            "after `response.body` is subject to declared-path resolution "
            "against `schema` (defined there) and must land on a node "
            "declaring `type: array`."
        ),
    )
    schema_: dict[str, Any] = Field(
        ...,
        alias="schema",
        description=(
            "JSON Schema Draft 2020-12 document describing the full response "
            "body.\n"
            "\n"
            "**Declared-path resolution.** Every `response.body[.<path>]` this "
            "endpoint reads — `records` above, each replication "
            "`cursor_field` and `pagination.keyset.order_by_field` (both "
            "resolved relative to the record shape but against this document as "
            "root), and every `{ref}` and `${...}` placeholder inside "
            "`pagination`, `response.metadata`, the `request` "
            "`path_params`/`headers`/`query`/`body` slots and every "
            "`params.<name>.default` — must "
            "resolve against this document by the following algorithm. It is "
            "stated in full because the validator, the engine and the "
            "conformance kit must all reproduce it identically.\n"
            "\n"
            "1. *Declaration.* A segment resolves when the current node "
            "declares it under `properties`, counting the declarations "
            "contributed by every object branch of `allOf` and by the target "
            "of an in-document `$ref`, applied recursively (a cycle "
            "contributes nothing the second time it is met). Those are the "
            "keywords whose contributions always apply.\n"
            "2. *Composition.* Contributors declaring the same name compose "
            "into one declaration: identical declarations collapse, differing "
            "ones fold into an `allOf` (an `allOf` branch narrowing a base "
            "declaration is the idiom `allOf` exists for). Composition fails on "
            "a contradiction that can be PROVED, and only then: when the "
            "contributors' `type` sets are provably disjoint, so no instance "
            "could satisfy all of them, and when an `allOf` branch is the "
            "boolean schema `false`, which no instance satisfies and which "
            "therefore empties the whole intersection. The disjoint-type test "
            "runs for the name "
            "being resolved, and again on the node a path finally lands on "
            "(and on a records array's record shape), because a terminal node "
            "assembled from contradictory `allOf`/`$ref` sources is unsatisfiable "
            "whether or not any further segment is read from it. Folding a node "
            "together with the sources that unconditionally apply — its `$ref` "
            "target and `allOf` branches, that order, itself last — is how any "
            "consumer reads `type`/`items`/`properties` off it.\n"
            "3. *Refusal to guess.* Nothing else declares a segment. If a "
            "segment is absent and the node carries `anyOf`, `oneOf`, "
            "`if`/`then`/`else`, `patternProperties`, `dependentSchemas`, a "
            "schema-valued `additionalProperties`/`unevaluatedProperties`, or "
            "a `$ref` that does not resolve, the path is not statically "
            "resolvable and the document is rejected — declare the segment "
            "under `properties`. With none of those present the segment is "
            "simply undeclared (the typo case). A node declaring BOTH the "
            "segment and one of those keywords resolves through `properties`; "
            "the ambiguity check runs only on a miss.\n"
            "4. *Typedness.* A node any checked `response.body` path resolves "
            "to — in `pagination`, in `response.metadata`, or in a `request` "
            "`headers`/`query`/`body` slot — must declare a `type` (or a "
            "`native_type`/`arrow_type` pair). "
            "A declaration that says nothing leaves whatever plants a value "
            "there to invent its type, which is what decides whether an "
            "ordering comparison in `stop_when` raises.\n"
            "5. *References.* Every `$ref` in this document must be an "
            "in-document JSON Pointer (`#`, or `#/…`) whose EVERY token — the "
            "last one included — is an unconditionally-applied schema position. "
            "Exactly eight keywords qualify: `properties`, `$defs`, "
            "`definitions` as maps; `allOf`, `prefixItems` as lists; `items`, "
            "`propertyNames`, `contentSchema` singly. Every OTHER keyword this "
            "document's `JsonSchemaPropertyNode` constrains — `anyOf`, `oneOf`, "
            "`if`/`then`/`else`, `not`, `dependentSchemas`, `patternProperties`, "
            "`contains`, `additionalProperties`, `unevaluatedProperties`, "
            "`unevaluatedItems` — applies conditionally, and a pointer may "
            "neither cross one NOR END ON one. `#/anyOf/0` names a real schema "
            "position, but one that applies only to instances taking that "
            "branch, so following it would commit the resolver to a branch — "
            "the guess clause 3 refuses when it meets the same keyword on a "
            "node. `allOf` is not in that set: its branches all apply, so "
            "`#/allOf/0` resolves. Pointer tokens are percent-decoded then "
            "RFC 6901 "
            "unescaped, and array indices follow RFC 6901 exactly (`0` or "
            "`[1-9][0-9]*`). Non-local refs, dangling refs, refs into "
            "non-schema positions such as `default`/`examples`/`const`/`enum`, "
            "refs whose target is a boolean schema (`true`/`false` declare "
            "nothing about a value's shape, so no path through them can reach a "
            "typed declaration), and the `$id`, `$anchor`, "
            "`$dynamicRef`/`$dynamicAnchor`, "
            "`$recursiveRef`/`$recursiveAnchor` keywords are all refused: "
            "nothing on the offline validate/author/execute path can fetch a "
            "second document or retarget a base URI, and a subtree reached "
            "that way would escape every annotation check.\n"
            "6. *Reserved scopes.* `response.headers.*`, `response.status`, "
            "`response.record_count`, `response.records` and "
            "`response.metadata.*` are engine-owned. This document describes "
            "the BODY only, so those scopes are not resolved against it. A "
            "`response.*` reference naming any OTHER sub-scope is rejected "
            "rather than skipped: `response.bodyy.next` is not a scope this "
            "rule leaves alone, it is a typo that resolves to nothing on every "
            "page. The leading token must likewise name a real resolution "
            "scope — `responses.body` fails for the same reason."
        ),
    )
    metadata: dict[str, Expression] | None = Field(  # type: ignore[valid-type]
        default=None,
        description=(
            "Optional named metadata extractions; each value is a value "
            "expression. Every `response.body[.<path>]` any of them "
            "references is subject to declared-path resolution against "
            "`schema`."
        ),
    )

    @model_validator(mode="after")
    def _validate(self) -> "ResponseExtraction":
        ref = self.records.ref
        if not isinstance(ref, str) or not (ref == "response.body" or ref.startswith("response.body.")):
            raise ValueError(
                "response.records must be `{ref: response.body[.<path>]}` "
                "(spec: §API Response Extraction)"
            )
        if self.metadata is not None:
            for key in self.metadata:
                if not METADATA_KEY_RE.match(key):
                    raise ValueError(
                        f"response.metadata key {key!r} must match {METADATA_KEY_PATTERN!r} "
                        "(spec: §API Response Extraction)"
                    )
                if key in RESERVED_RESPONSE_SCOPES:
                    raise ValueError(
                        f"response.metadata key {key!r} collides with reserved response-scope name "
                        f"{sorted(RESERVED_RESPONSE_SCOPES)!r} (spec: §API Response Extraction)"
                    )
        errors: list[str] = []
        _validate_arrow_type_in_json_schema(self.schema_, "response.schema", errors)
        _validate_schema_refs(self.schema_, "response.schema", errors)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class WriteInput(_EndpointModel):
    """Write mode input shape (one provider-facing destination record)."""

    schema_: dict[str, Any] = Field(
        ...,
        alias="schema",
        description="JSON Schema Draft 2020-12 for one provider-facing destination record.",
    )

    # Named `_validate` (not `_validate_arrow_types`) because it now enforces two
    # rules — ADV-ENDP-006's arrow_type walk and ADV-ENDP-026's `$ref` walk — and
    # the advisory registry needs ONE enforcer name that exists on both this model
    # and `ResponseExtraction` for the pair of rules they share.
    @model_validator(mode="after")
    def _validate(self) -> "WriteInput":
        errors: list[str] = []
        _validate_arrow_type_in_json_schema(self.schema_, "input.schema", errors)
        _validate_schema_refs(self.schema_, "input.schema", errors)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class WriteError(_EndpointModel):
    """Optional provider-declared write error extraction expressions."""

    code: Expression | None = Field(default=None)  # type: ignore[valid-type]
    message: Expression | None = Field(default=None)  # type: ignore[valid-type]
    details: Expression | None = Field(default=None)  # type: ignore[valid-type]


class Batching(_EndpointModel):
    """Batching declaration for a write mode."""

    max_records: int = Field(
        ..., ge=2,
        description="Provider's maximum records per request. Must be ≥ 2.",
    )


class Idempotency(_EndpointModel):
    """Idempotency-key placement declaration for a write mode.

    The author declares only *where* the provider's idempotency key goes on
    the write request. The key *value* is engine-owned (the content-derived
    per-record id) — no value expression, no template, so engine-computed
    values stay out of the request value-expression grammar.
    """

    location: Literal["header", "body"] = Field(
        ...,
        alias="in",
        description=(
            "Where the engine sends the key: `header` — as HTTP request header "
            "`name` (Stripe `Idempotency-Key`, PayPal `PayPal-Request-Id`); "
            "`body` — injected as top-level JSON body field `name` (Square-style "
            "`idempotency_key`). `body` is only valid when the request body is a "
            "JSON object; the engine rejects non-object bodies at configure time."
        ),
    )
    name: str = Field(
        ...,
        min_length=1,
        description="Header name or top-level body field name that carries the key.",
    )


class WriteResponse(_EndpointModel):
    """Optional write-result extraction block."""

    # Mirror of `_metadata_keys`: write `metadata` follows the same key rules as
    # the read side (`§API Write Response Contract` defers to
    # `§API Response Extraction`). Same fragment as `ResponseExtraction.metadata`.
    model_config = ConfigDict(
        json_schema_extra={"allOf": [{"properties": {"metadata": _METADATA_PROPERTY_NAMES}}]}
    )

    success_when: Predicate | None = Field(default=None)  # type: ignore[valid-type]
    error: WriteError | None = Field(
        default=None,
        description="Optional `code`/`message`/`details` value expressions for failure parsing.",
    )
    affected_records: Expression | None = Field(default=None)  # type: ignore[valid-type]
    generated_keys: Expression | None = Field(default=None)  # type: ignore[valid-type]
    metadata: dict[str, Expression] | None = Field(default=None)  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _metadata_keys(self) -> "WriteResponse":
        # Spec §API Write Response Contract delegates metadata key rules to
        # the read-side §API Response Extraction rules; same patterns apply.
        if self.metadata is None:
            return self
        for key in self.metadata:
            if not METADATA_KEY_RE.match(key):
                raise ValueError(
                    f"response.metadata key {key!r} must match {METADATA_KEY_PATTERN!r} "
                    "(spec: §API Write Response Contract — follows §API Response Extraction)"
                )
            if key in RESERVED_RESPONSE_SCOPES:
                raise ValueError(
                    f"response.metadata key {key!r} collides with reserved response-scope name "
                    "(spec: §API Write Response Contract — follows §API Response Extraction)"
                )
        return self

    @model_validator(mode="after")
    def _reject_record_count(self) -> "WriteResponse":
        # `response.record_count` is available only for read operations
        # (§API Write Response Contract); write-response expressions must not
        # reference it. `iter_expression_strings` (the shared resolver grammar)
        # reaches the `Any`-typed `success_when` operands and function inputs a
        # typed walk would miss — including bare-string templates — while skipping
        # `literal` subtrees (a `{"literal": {...}}` payload is protected data,
        # not an executable ref). Tokens are stripped like the resolver.
        def _is_record_count(token: str) -> bool:
            t = token.strip()
            return t == "response.record_count" or t.startswith("response.record_count.")

        for kind, s in iter_expression_strings(self.model_dump(by_alias=True)):
            hits = [s] if kind == "ref" else template_placeholders(s)
            if any(_is_record_count(h) for h in hits):
                raise ValueError(
                    "write-response expressions must not reference "
                    "`response.record_count` (read-only response scope; spec: "
                    "§API Write Response Contract)"
                )
        return self


class ReadOperation(_EndpointModel):
    """Read operation block."""

    # Mirror of `_wiring`'s GET check: a GET read must not declare any param with
    # `in: "body"`. Cross-block (`request.method` vs `params.*.in`), so it reads
    # the discriminated request's `method` const; the T1 request-union split
    # cannot reach across to the sibling `params` map.
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "required": ["request"],
                        "properties": {
                            "request": {
                                "required": ["method"],
                                "properties": {"method": {"const": "GET"}},
                            }
                        },
                    },
                    "then": {
                        "properties": {
                            "params": {
                                "additionalProperties": {
                                    "properties": {"in": {"not": {"const": "body"}}}
                                }
                            }
                        }
                    },
                }
            ]
        }
    )

    request: ReadRequest = Field(...)
    params: dict[str, Param] = Field(default_factory=dict)
    response: ResponseExtraction = Field(...)
    pagination: Pagination | None = Field(  # type: ignore[type-arg]
        default=None,
        description=(
            "Pagination strategy. Every `response.body[.<path>]` this block "
            "references — anywhere in it, including `stop_when` predicates, "
            "`cursor.next_cursor`, `link.next_url`, `offset.increment_by`, "
            "`page.initial`/`increment_by` and `keyset.initial`, and including "
            "refs inside `${...}` templates — is subject to declared-path "
            "resolution against `response.schema` (defined there). A path that "
            "does not resolve is a typo that would silently stop paging after "
            "one page."
        ),
    )
    replication: Replication | None = Field(default=None)

    @model_validator(mode="after")
    def _wiring(self) -> "ReadOperation":
        _validate_param_wiring(self.request, self.params, allow_from_input=False)
        _validate_param_binding_uniqueness(self.request, self.params)

        if self.request.method == "GET":
            for name, param in self.params.items():
                if param.location == "body":
                    raise ValueError(
                        f"read GET operation must not declare params with in='body' "
                        f"(found {name!r}; spec: §Request Bodies)"
                    )

        if self.pagination is not None:
            _validate_pagination_wiring(self.pagination, self.params)

        if self.replication is not None:
            _validate_replication_wiring(self.replication, self.params)

        # response.records → response.schema traversal raises directly.
        # When replication is declared, the same traversal feeds cursor-field
        # validation (avoiding a second walk of the same JSON Schema).
        records_array_node = _validate_records_in_response_schema(self.response)
        if self.replication is not None:
            _validate_cursor_fields_in_record_shape(
                self.replication, records_array_node, self.response.schema_
            )

        # `keyset.order_by_field` is a RECORD path, not a `response.body` ref, so
        # the sweep below never sees it — `_response_body_segments` returns None
        # and it is skipped. It needs the record-shape walk instead, the same one
        # replication `cursor_field` gets: both name a field the engine reads off
        # a record to advance from, and an undeclared one truncates or repeats
        # pages silently while the run still reports success. Until this it was
        # guarded by nothing but `RECORD_FIELD_PATH_PATTERN` — a shape check,
        # not an existence one.
        if isinstance(self.pagination, KeysetPagination):
            _validate_record_field_path(
                self.pagination.keyset.order_by_field,
                records_array_node,
                self.response.schema_,
                where="pagination.keyset.order_by_field",
            )

        # Last: the records anchor and the cursor fields are the paths an author
        # is most likely to have got right, so reporting them first keeps the
        # broader pagination/metadata sweep from masking a simpler error.
        _validate_response_body_paths(
            self.response, self.pagination, self.request, self.params
        )

        return self


def _json_schema_top_level_fields(
    schema: dict[str, Any], root: Any = None
) -> set[str] | None:
    """Top-level object field names declared by a JSON Schema record shape.

    The names a write mode's `conflict_keys` can target. Returns `None` when the
    schema declares no object `properties` map even after composition —
    "unknowable, skip the check" — distinct from an explicit empty `properties:
    {}`, which returns an empty set ("zero declared fields", so any conflict key
    is invalid).

    Composed with :func:`materialize_node` against ``root`` (the whole
    `input.schema`), so a record assembled from `allOf` branches or reached
    through an in-document `$ref` enumerates the fields it actually declares.
    Reading `properties` raw made this return `None` for exactly the
    `$defs` + `$ref` shape ADV-ENDP-026's rejection message tells authors to
    write — silently disabling both this check and ADV-ENDP-024's membership
    rule for every document that followed the advice.
    """
    materialized = materialize_node(schema, root if root is not None else schema)
    props = materialized.get("properties") if isinstance(materialized, dict) else None
    return set(props) if isinstance(props, dict) else None


def _walk_input_schema_path(
    schema: dict[str, Any], from_input: str, root: Any = None
) -> dict[str, Any] | None:
    """Resolve the ``input.schema`` subschema a write-body `from_input` addresses.

    ``record`` → the schema itself; ``record.<a>.<b>`` → a composed `properties`
    walk (:func:`materialize_node` per step, see
    :func:`_json_schema_top_level_fields`). Returns ``None`` when the expression
    is not record-addressed or a segment is not declared — statically
    unknowable, per the contract's unknowable→skip convention (the engine owns
    the resolved shape at configure time).
    """
    root = schema if root is None else root
    if from_input == "record":
        return materialize_node(schema, root)
    if not from_input.startswith("record."):
        return None
    node = materialize_node(schema, root)
    for seg in from_input.removeprefix("record.").split("."):
        props = node.get("properties") if isinstance(node, dict) else None
        if not isinstance(props, dict) or not isinstance(props.get(seg), dict):
            return None
        node = materialize_node(props[seg], root)
    return node if isinstance(node, dict) else None


def _undeclared_from_input_field(schema: dict[str, Any], from_input: str) -> str | None:
    """The first ``record.<dotted>`` segment the declared ``input.schema``
    provably does not contain, or ``None`` when the path is not checkable.

    Walks COMPOSED ``properties`` maps segment by segment
    (:func:`_json_schema_top_level_fields`, so `allOf` branches and in-document
    `$ref` targets count). A segment is a violation only when its parent
    declares an object ``properties`` map that omits it — a genuinely absent
    field. A parent that declares no ``properties`` map even after composition
    (an unconstrained object) is unknowable, so the walk stops and the path is
    accepted, per the contract's unknowable→skip convention (the engine owns the
    resolved shape at configure time). Whole-``record`` and ``records``
    expressions carry no field path and are always ``None``.
    """
    if not from_input.startswith("record."):
        return None
    root = schema
    node: Any = materialize_node(schema, root)
    walked = "record"
    for seg in from_input.removeprefix("record.").split("."):
        if not isinstance(node, dict):
            return None  # a non-object subschema (e.g. a boolean) is not walkable
        fields = _json_schema_top_level_fields(node, root)
        if fields is None:
            return None
        if seg not in fields:
            return f"{walked}.{seg}"
        node = materialize_node(node["properties"][seg], root)
        walked = f"{walked}.{seg}"
    return None


def is_valid_conflict_keys(value: Any) -> bool:
    """A raw ``conflict_keys`` value is well-formed iff it is a non-empty list of
    non-empty strings — the shape the Pydantic field enforces on parsed
    documents. The untyped read (discovery) and ingest (DIP webhook)
    paths never run the model, so they share this predicate rather than each
    re-spelling the rule (define once)."""
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(k, str) and k for k in value)
    )


class WriteOperation(_EndpointModel):
    """One write-mode block, keyed in `operations.write` by the mode it serves."""

    model_config = ConfigDict(
        json_schema_extra={
            # Published-schema mirror of the `_wiring` idempotency×batching
            # rule: the key value is per-record, so a multi-record request
            # cannot carry one. `anyOf` over null-or-absent — not
            # `not: {required: [...]}` — so an explicit null (either field's
            # nullable default) still authors, matching the model's is-None check.
            "anyOf": [
                {"properties": {"idempotency": {"type": "null"}}},
                {"properties": {"batching": {"type": "null"}}},
            ],
            # Published-schema mirror of the `_wiring` body-placement guard:
            # `idempotency.in: "body"` needs an object request body to inject
            # into, so a literal non-object `request.body` template is
            # unauthorable. Template-level only — an expression body
            # (`{"from_input": ...}`) is an object template whose resolved
            # shape JSON Schema cannot see; the model's static resolution and
            # the engine's configure gate own those cases. A sibling `allOf`
            # (not `if`/`then`, and not folded into the anyOf above, which
            # would loosen it): the guard binds only documents using the new
            # 9.1.0 field, so it is semantically additive, and this is the
            # conjunction form the version classifier also reads as additive.
            "allOf": [
                {
                    "anyOf": [
                        {"properties": {"idempotency": {"anyOf": [
                            {"type": "null"},
                            {"properties": {"in": {"const": "header"}}},
                        ]}}},
                        {"properties": {"request": {
                            "properties": {"body": {"type": "object"}},
                            "required": ["body"],
                        }}},
                    ],
                },
            ],
        },
    )

    request: WriteRequest = Field(...)
    params: dict[str, Param] = Field(default_factory=dict)
    input: WriteInput = Field(...)
    conflict_keys: list[Annotated[str, Field(min_length=1)]] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Upsert conflict-target fields — the provider-defined natural key "
            "the upsert matches on (e.g. Salesforce `ExternalIdField`, HubSpot "
            "`idProperty`, Airtable `fieldsToMergeOn`, Elasticsearch `_id`, "
            "Algolia `objectID`). Each entry is a top-level field name in "
            "`input.schema`. A single composite key set — every listed field "
            "participates. Required on the `upsert` write mode; forbidden on "
            "every other write mode (enforced by `operations`, which knows the "
            "mode key)."
        ),
    )
    batching: Batching | None = Field(default=None)
    idempotency: Idempotency | None = Field(
        default=None,
        description=(
            "Provider idempotency-key placement (e.g. Stripe `Idempotency-Key` "
            "header, Square `idempotency_key` body field). Allowed on any write "
            "mode, required on none — some providers require the key even on "
            "upsert (Square `UpsertCatalogObject`). Forbidden "
            "together with `batching`: the "
            "key value is per-record, and a resumed cursor re-batches different "
            "row compositions, so a multi-record request cannot carry a key "
            "that survives an engine restart."
        ),
    )
    response: WriteResponse | None = Field(default=None)

    @model_validator(mode="after")
    def _wiring(self) -> "WriteOperation":
        _validate_param_wiring(self.request, self.params, allow_from_input=True)
        _validate_param_binding_uniqueness(self.request, self.params)

        # ADV-ENDP-025. Held here rather than in `_validate_param_wiring`
        # because `batching` is a property of the write MODE, not of the request,
        # so this is the innermost scope that can see both.
        # A write has no `response.schema`, so declared-path resolution has
        # nothing to resolve against — but both SCOPE checks apply, and they are
        # what catches this class. `success_when` is the predicate that decides
        # whether a write SUCCEEDED: `{"empty": {"ref": "response.bodyy.errors"}}`
        # resolves to nothing on every response, so `empty` holds unconditionally
        # and every write reports success, including the ones whose rejected rows
        # the provider listed in `body.errors`. Partial data loss, green run —
        # strictly worse than the silent paging truncation the read side's
        # declared-path sweep prevents. The request slots are swept for the
        # same reason the read side is.
        write_sites: list[_ExpressionSite] = [
            _ExpressionSite(
                where=f"operations.write.request.{slot}",
                payload=getattr(self.request, slot, None),
                operation=_OperationKind.WRITE,
                can_read_response=False,
            )
            for slot in _REQUEST_EXPRESSION_SLOTS
        ]
        write_sites.append(_ExpressionSite(
            where="operations.write.response",
            payload=self.response.model_dump() if self.response is not None else None,
            operation=_OperationKind.WRITE_RESPONSE,
            can_read_response=True,
        ))
        write_sites += [
            _ExpressionSite(
                where=f"operations.write.params[{name!r}].default",
                payload=param.default,
                operation=_OperationKind.WRITE,
                # A param default feeds the REQUEST, so it is built before the
                # response too.
                can_read_response=False,
            )
            for name, param in self.params.items()
        ]
        # `metadata_keys` too: `WriteResponse.metadata` has the identical closed,
        # author-declared key set, and the harm string for an undeclared key here
        # is the worst one in the contract — a `success_when` predicate over a ref
        # that resolves to nothing holds unconditionally, so every write reports
        # success including the ones whose rejected rows the provider listed.
        # That message was already written and already selected by
        # WRITE_RESPONSE; only the check that emits it was wired to the read
        # sweep alone.
        _sweep_expression_sites(
            [s for s in write_sites if s.payload is not None],
            metadata_keys=frozenset(
                (self.response.metadata or {}) if self.response is not None else {}
            ),
        )

        path_from_inputs = _collect_singleton_values(self.request.path_params, "from_input")
        if path_from_inputs and self.batching is not None:
            raise ValueError(
                "from_input in request.path_params cannot be combined with batching — "
                "a path segment takes one record's value and a multi-record request "
                "has no single record to take it from (spec: §Write Modes)"
            )

        if self.conflict_keys is not None:
            known = _json_schema_top_level_fields(self.input.schema_)
            # Enforce membership whenever the input schema declares an object
            # `properties` map — including an explicit empty one (`properties:
            # {}` means zero fields, so any conflict key is invalid). Only a
            # record that models its fields some other way (a bare `$ref`, no
            # `properties` map → `None`) is unknowable and skipped.
            if known is not None:
                unknown = sorted(set(self.conflict_keys) - known)
                if unknown:
                    raise ValueError(
                        f"conflict_keys reference unknown input.schema fields {unknown!r} "
                        "(spec: §Cross-Field Validation)"
                    )

        if self.idempotency is not None:
            if self.batching is not None:
                raise ValueError(
                    "idempotency cannot be combined with batching — the key value is "
                    "per-record and a multi-record request cannot carry one "
                    "(spec: §Write Modes)"
                )
            if self.idempotency.location == "header":
                declared = {h.lower() for h in (self.request.headers or {})}
                if self.idempotency.name.lower() in declared:
                    raise ValueError(
                        f"idempotency header {self.idempotency.name!r} is also declared "
                        "in request.headers — the key value is engine-owned, so the "
                        "header must not carry an authored value "
                        "(spec: §Cross-Field Validation)"
                    )
            else:  # location == "body"
                # Only the statically-provable cases are rejected here; where
                # the resolved shape is unknowable, the engine rejects a
                # resolved non-object body at configure time.
                body = self.request.body
                if not isinstance(body, dict):
                    raise ValueError(
                        "idempotency.in='body' requires the write request body "
                        "template to be a JSON object — the engine injects the key "
                        "as a top-level body field (spec: §Cross-Field Validation)"
                    )
                if _matches_singleton(body, "from_input"):
                    # Expression body: the request body IS the addressed record
                    # (or record field). Apply the same two rules to the
                    # input.schema shape it resolves to, when declared.
                    node = _walk_input_schema_path(self.input.schema_, body["from_input"])
                    declared_type = node.get("type") if node is not None else None
                    if declared_type is not None and "object" not in (
                        declared_type if isinstance(declared_type, list) else [declared_type]
                    ):
                        raise ValueError(
                            "idempotency.in='body' requires the write request body to "
                            f"resolve to a JSON object — `from_input: {body['from_input']!r}` "
                            f"resolves to input.schema type {declared_type!r} "
                            "(spec: §Cross-Field Validation)"
                        )
                    fields = _json_schema_top_level_fields(node) if node is not None else None
                    if fields is not None and self.idempotency.name in fields:
                        raise ValueError(
                            f"idempotency body field {self.idempotency.name!r} is also a "
                            "declared field of the record the body resolves to — the key "
                            "value is engine-owned, so the field must not carry an "
                            "authored value (spec: §Cross-Field Validation)"
                        )
                elif self.idempotency.name in body:
                    raise ValueError(
                        f"idempotency body field {self.idempotency.name!r} is also a "
                        "top-level key of the request body template — the key value "
                        "is engine-owned, so the field must not carry an authored "
                        "value (spec: §Cross-Field Validation)"
                    )

        from_inputs = _collect_singleton_values(self.request.body, "from_input")
        if self.batching is None:
            if not from_inputs:
                raise ValueError(
                    "non-batched write request body must reference `from_input: 'record'` "
                    "or `record.<field>` (spec: §Cross-Field Validation)"
                )
            for fi in from_inputs:
                if fi == "records":
                    raise ValueError(
                        "non-batched write must not use `from_input: 'records'` "
                        "(spec: §Cross-Field Validation)"
                    )
        else:
            if not from_inputs:
                raise ValueError(
                    "batched write request body must reference `from_input: 'records'` "
                    "(spec: §Cross-Field Validation)"
                )
            for fi in from_inputs:
                if fi == "record" or fi.startswith("record."):
                    raise ValueError(
                        "batched write must not use `from_input: 'record'` or `record.<field>` "
                        "(spec: §Cross-Field Validation)"
                    )

        # A `record.<field>` path must address a field the declared `input.schema`
        # actually contains, where the shape is knowable — the same membership
        # rule `conflict_keys` enforces. Batched writes never reach here with a
        # `record.<field>` path (rejected above), so this only bites the
        # non-batched per-field placement form.
        for fi in from_inputs:
            missing = _undeclared_from_input_field(self.input.schema_, fi)
            if missing is not None:
                raise ValueError(
                    f"from_input {fi!r} references undeclared input.schema field "
                    f"{missing!r} (spec: §Cross-Field Validation)"
                )

        # ADV-ENDP-024: the same membership rule for path_params, reported
        # against its own site so the author is sent to the binding that is
        # actually wrong rather than to the body.
        for fi in path_from_inputs:
            missing = _undeclared_from_input_field(self.input.schema_, fi)
            if missing is not None:
                raise ValueError(
                    f"request.path_params from_input {fi!r} references undeclared "
                    f"input.schema field {missing!r} (spec: §Cross-Field Validation)"
                )
        return self


class Operations(_EndpointModel):
    """``operations`` block for API endpoints. At least one of ``read``/``write`` is required."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        json_schema_extra={
            "additionalProperties": False,
            "anyOf": [{"required": ["read"]}, {"required": ["write"]}],
        },
    )

    read: ReadOperation | None = Field(default=None)
    write: dict[WriteMode, WriteOperation] | None = Field(
        default=None,
        json_schema_extra={"minProperties": 1},
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> "Operations":
        if self.read is None and not self.write:
            raise ValueError(
                "operations must declare at least one of `read` or `write` "
                "(spec: §API Endpoint Shape)"
            )
        if self.write is not None and len(self.write) == 0:
            raise ValueError(
                "operations.write must contain at least one mode when present "
                "(spec: §API Endpoint Shape)"
            )
        return self

    @model_validator(mode="after")
    def _conflict_keys_by_mode(self) -> "Operations":
        # The conflict key lives on the endpoint because it is provider-defined,
        # but whether it is required is a property of the *mode* — and the mode
        # is the dict key, visible only here. `upsert` must declare it (there is
        # nothing to match on otherwise); every other mode must not (the concept
        # does not apply). Spec: §Write Modes.
        for mode, op in (self.write or {}).items():
            if mode == "upsert":
                if not op.conflict_keys:
                    raise ValueError(
                        "operations.write.upsert.conflict_keys is required — an "
                        "upsert must declare the provider's conflict target(s) "
                        "(spec: §Write Modes)"
                    )
            elif op.conflict_keys:
                raise ValueError(
                    f"operations.write.{mode}.conflict_keys is not allowed — "
                    "conflict_keys applies only to the upsert write mode "
                    "(spec: §Write Modes)"
                )
        return self


# ---------------------------------------------------------------------------
# Endpoint root models
# ---------------------------------------------------------------------------


class _EndpointBase(_EndpointModel):
    """Shared identity and metadata fields. Spec: §Top-Level Fields, §Shared Metadata."""

    endpoint_id: str = Field(
        ...,
        min_length=1,
        pattern=SLUG_PATTERN,
        description=(
            "Stable endpoint identifier within the owner. "
            f"Matches `{SLUG_PATTERN}`."
        ),
    )
    display_name: str | None = Field(
        default=None,
        min_length=DISPLAY_NAME_MIN,
        max_length=DISPLAY_NAME_MAX,
        pattern=NO_EDGE_WHITESPACE_PATTERN,
        description="User-facing label (1-120 chars trimmed).",
    )
    description: str | None = Field(
        default=None,
        max_length=DESCRIPTION_MAX,
        description="User-facing summary (≤2000 chars).",
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


class ApiEndpointDoc(_EndpointBase):
    """API endpoint schema document."""

    schema_url: Literal[API_ENDPOINT_SCHEMA_URL] = Field(
        ...,
        alias="$schema",
        description=(
            "Per-kind schema URL declared by every persisted API endpoint "
            "document. Per spec §Schema URLs."
        ),
    )
    operations: Operations = Field(...)


class DatabaseObject(_EndpointModel):
    """Provider-native database object identity.

    Identifier strings are stored verbatim from introspection — no
    case-folding, quoting, or normalization.
    """

    catalog: str | None = Field(default=None, min_length=1)
    schema_: str | None = Field(default=None, alias="schema", min_length=1)
    name: str = Field(..., min_length=1, description="Provider-native object name.")
    object_type: str | None = Field(
        default=None,
        description=(
            "Open-string descriptive type (table, view, materialized_view, "
            "external_table, collection, …). Read execution must not branch on it."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null_namespaces(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in ("catalog", "schema"):
                if key in data and data[key] is None:
                    raise ValueError(
                        f"database_object.{key} must be omitted when not applicable; "
                        "explicit null is invalid (spec: §Database Endpoint Shape)"
                    )
        return data


# The authored-shape container matrix — Object ⇒ non-empty `properties`, no
# `items`; List ⇒ `items`, no `properties`; anything else ⇒ neither. The shared
# `ARROW_CONTAINER_SCHEMA_RULES` (defined next to the runtime
# `enforce_container_shape` helper) is the declarative mirror, reused verbatim by
# the stream `ArrowFieldSpec`/`AssignmentTarget` classes (and its `allOf`
# branches by stream `ConstantValue`) so the two contracts cannot drift.
# `test_column_container_matrix` guards the fragment and the runtime validator.


class ColumnFieldSpec(_EndpointModel):
    """Recursive child field-shape declaration for declared-shape JSON
    containers under database columns.
    """

    model_config = ConfigDict(json_schema_extra=ARROW_CONTAINER_SCHEMA_RULES)

    arrow_type: str = Field(
        ...,
        pattern=ARROW_TYPE_PATTERN,
        description=(
            "Apache Arrow canonical transport type string. Bare authored-shape "
            "markers 'Object', 'List', and 'Json' declare JSON containers."
        ),
    )
    nullable: bool | None = Field(default=None)
    properties: dict[str, "ColumnFieldSpec"] | None = Field(default=None)
    items: ColumnFieldSpec | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_container_shape(self) -> "ColumnFieldSpec":
        enforce_container_shape(self.arrow_type, self.properties, self.items)
        return self


class Column(_EndpointModel):
    """Database column metadata."""

    model_config = ConfigDict(json_schema_extra=ARROW_CONTAINER_SCHEMA_RULES)

    name: str = Field(..., min_length=1)
    native_type: str = Field(
        ...,
        min_length=1,
        description="Provider-native database type label. Use 'unknown' when unavailable.",
    )
    arrow_type: str = Field(
        ...,
        pattern=ARROW_TYPE_PATTERN,
        description=(
            "Apache Arrow canonical transport type string. PascalCase base name "
            "plus parameters when the canonical type requires them — bare "
            "parameterized forms such as 'Timestamp' or 'Decimal128' "
            "are rejected. Examples: 'Utf8', 'Int64', 'Timestamp(MICROSECOND)' "
            "(zone-naive), 'Timestamp(MICROSECOND, UTC)' (zoned source; prefer "
            "UTC unless source-specific), 'Decimal128(38, 9)', "
            "'FixedSizeBinary(16)'. Bare markers "
            "'Object' / 'List' / 'Json' declare JSON containers; see spec "
            "§Native and Arrow Types."
        ),
    )
    nullable: bool | None = Field(default=None)
    default: Any | None = Field(default=None)
    comment: str | None = Field(default=None)
    ordinal_position: int | None = Field(default=None, ge=1)
    # `properties` here is a field-spec map (recursive ColumnFieldSpec),
    # distinct from JSON Schema `properties` blocks used by API endpoints.
    # Both are enforced by `enforce_container_shape` via the validator below.
    properties: dict[str, ColumnFieldSpec] | None = Field(default=None)
    items: ColumnFieldSpec | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_container_shape(self) -> "Column":
        enforce_container_shape(self.arrow_type, self.properties, self.items)
        return self


class DatabaseEndpointDoc(AdvisoryValidated, _EndpointBase):
    """Database endpoint schema document."""

    schema_url: Literal[DATABASE_ENDPOINT_SCHEMA_URL] = Field(
        ...,
        alias="$schema",
        description=(
            "Per-kind schema URL declared by every persisted database endpoint "
            "document. Per spec §Schema URLs."
        ),
    )
    database_object: DatabaseObject = Field(...)
    columns: list[Column] = Field(..., min_length=1)
    primary_keys: list[str] | None = Field(default=None, min_length=1)


def parse_endpoint(payload: Any) -> "ApiEndpointDoc | DatabaseEndpointDoc":
    """Dispatch an endpoint payload to the kind-specific validator.

    Endpoint documents carry no top-level ``kind``; the owning connector
    determines kind at runtime. This helper picks the right Pydantic model
    using (1) the ``$schema`` URL when present, then (2) a structural fall-
    back on the presence of database-only fields (``database_object`` /
    ``columns``). API is the default when neither hint is present.

    Raises:
        TypeError: payload is not a dict.
        ValueError: payload carries a ``$schema`` URL that is neither the
            api-endpoint nor the database-endpoint URL. Routing on an
            unrecognized schema would silently dispatch to ApiEndpointDoc and
            surface a misleading "extra field" error against the wrong model.
    """
    if not isinstance(payload, dict):
        raise TypeError(
            f"endpoint payload must be a dict, got {type(payload).__name__}"
        )
    schema = payload.get("$schema")
    if schema == DATABASE_ENDPOINT_SCHEMA_URL:
        return DatabaseEndpointDoc.model_validate(payload)
    if schema == API_ENDPOINT_SCHEMA_URL:
        return ApiEndpointDoc.model_validate(payload)
    if schema is not None:
        raise ValueError(
            f"unknown $schema {schema!r}; expected "
            f"{API_ENDPOINT_SCHEMA_URL!r} or {DATABASE_ENDPOINT_SCHEMA_URL!r}"
        )
    if "database_object" in payload or "columns" in payload:
        return DatabaseEndpointDoc.model_validate(payload)
    return ApiEndpointDoc.model_validate(payload)


# ---------------------------------------------------------------------------
# Internal validators
# ---------------------------------------------------------------------------


_DECLARED_FIELDS_CACHE: dict[type, frozenset[str]] = {}


def _declared_field_names(cls: type) -> frozenset[str]:
    """Per-class set of declared field names + aliases. Cached.

    Used by the ``_reject_non_x_extras`` mode-``before`` validator on every
    model construction; caching keeps the hot path cheap.
    """
    cached = _DECLARED_FIELDS_CACHE.get(cls)
    if cached is not None:
        return cached
    names: set[str] = set()
    for name, info in cls.model_fields.items():
        names.add(name)
        if info.alias:
            names.add(info.alias)
    frozen = frozenset(names)
    _DECLARED_FIELDS_CACHE[cls] = frozen
    return frozen


_BINDING_KEYS: frozenset[str] = frozenset({"from_param", "from_input"})
_VALUE_EXPRESSION_KEYS: frozenset[str] = frozenset(_EXPRESSION_KEYS)
_ALL_EXPRESSION_KEYS: frozenset[str] = _BINDING_KEYS | _VALUE_EXPRESSION_KEYS

# `function` expressions are *not* singletons — per the `FunctionExpression` model,
# they declare a `function` name plus optional argument fields. The shape validator at `_validate_expression_shapes`
# permits exactly the field set declared on `FunctionExpression` itself, so
# extending the model (e.g. adding a future `jwt_sign`-specific field) only
# touches one place — the validator follows automatically.
_FUNCTION_EXPRESSION_FIELDS: frozenset[str] = frozenset(FunctionExpression.model_fields.keys())

# Callable functions whose whole job is to escape a value for the wire. Naming
# one inside a `path_params` binding double-encodes, because the engine already
# percent-encodes each substituted path segment (ADV-ENDP-027). This is a
# judgement about what each function DOES, not a mechanical subset of the
# callable catalog — `basic_auth` and `lookup` are equally callable and neither
# escapes anything — so it is stated here and pinned against the catalog by
# `test_the_refused_encoders_are_real_catalog_functions`.
_WIRE_ENCODING_FUNCTIONS: frozenset[str] = frozenset({"url_encode", "base64_encode"})


def _collect_function_names(value: Any) -> list[str]:
    """Every `function` name declared anywhere inside ``value``.

    A function expression can be nested as another expression's `input`, so the
    check has to reach the whole subtree rather than only its head.

    One subtree is deliberately not reached: a `literal` payload.
    `resolve_value_expression` returns a literal's contents verbatim
    (`value_expression.py` — `if "literal" in value: return value["literal"]`),
    so `{"literal": {"function": "url_encode"}}` is data and nothing is called.

    `x-*` siblings ARE reached, and an earlier revision of this function was
    wrong to skip them. `resolve_template_deep` walks every key of a request
    slot, extension keys included: `{"x-why": {"function": "url_encode",
    "input": "a b"}}` really does resolve to `{"x-why": "a%20b"}`. Whatever the
    extension namespace is for, the engine executes what is in it, so a rule
    about what the engine executes must look there too. This matches
    :func:`_expression_tokens` / ``iter_expression_strings``, which also recurse
    into `x-*` — a validator should see exactly what the resolver will resolve,
    no more and no less.
    """
    found: list[str] = []
    if isinstance(value, dict):
        if "literal" in value:
            return found
        name = value.get("function")
        if isinstance(name, str):
            found.append(name)
        for child in value.values():
            found.extend(_collect_function_names(child))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_function_names(item))
    return found


def _validate_expression_shapes(value: Any, where: str) -> None:
    """Walk ``value``; raise when a dict has expression-like keys but is structurally malformed.

    Spec §Extension Policy + §Value Expressions: an expression dict declares
    exactly one of ``ref``/``template``/``literal``/``function``/``from_param``/
    ``from_input``. ``ref``/``template``/``literal``/``from_param``/``from_input``
    are singleton-shaped — only the named key plus ``x-*`` siblings are
    permitted. ``function`` carries documented argument fields
    (``input``/``map``/``safe``) plus ``x-*``.

    Surfacing malformed expression dicts here gives the author a precise
    pointer to the bad fragment instead of a downstream "param not
    referenced" error from the param-binding walk that runs after.
    """
    if isinstance(value, dict):
        present_expr_keys = [k for k in value if k in _ALL_EXPRESSION_KEYS]
        if present_expr_keys:
            if len(present_expr_keys) > 1:
                raise ValueError(
                    f"{where}: expression dict declares multiple expression keys "
                    f"{sorted(present_expr_keys)!r}; spec requires exactly one "
                    "(spec: §Value Expressions)"
                )
            primary = present_expr_keys[0]
            allowed_siblings = (
                _FUNCTION_EXPRESSION_FIELDS if primary == "function" else {primary}
            )
            non_x_others = sorted(
                k for k in value
                if k not in allowed_siblings and not (
                    isinstance(k, str) and k.startswith("x-")
                )
            )
            if non_x_others:
                raise ValueError(
                    f"{where}: {primary!r} expression has unexpected siblings "
                    f"{non_x_others!r}; expressions must be the documented shape "
                    "(spec: §Value Expressions)"
                )
            if primary == "literal":
                # A `literal` payload is opaque data — `resolve_value_expression`
                # returns it verbatim, and `_collect_singleton_values`,
                # `_collect_function_names` and `_expression_tokens` all skip it.
                # So this walker must not RECURSE into it (a provider-shaped
                # default carrying a field named `template`/`function`/`ref` was
                # otherwise unauthorable with no working escape) — but it must
                # still CHECK the dict that carries it. Skipping the dict too
                # accepted `{"ref": "totally.bogus", "literal": 5}`: the resolver
                # dispatches `literal` before `ref`, so the value went out as 5
                # and the author's ref was silently inert.
                return
            # Recurse into the expression's argument(s). For `function`, this
            # walks `input` (which may itself be an expression) and `map`'s
            # values; for `template`/`ref` the inner is leaf data.
            for v_inner in value.values():
                _validate_expression_shapes(v_inner, f"{where}.<{primary}>")
            return
        for k, v_inner in value.items():
            _validate_expression_shapes(v_inner, f"{where}.{k}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _validate_expression_shapes(item, f"{where}[{i}]")


def _matches_singleton(value: Any, key: str) -> bool:
    """True when ``value`` is a ``{key: <str>}`` dict, with optional ``x-*`` siblings.

    Tolerating ``x-*`` siblings (spec §Extension Policy) is required so an
    extension key on a binding expression does not hide it from the
    dangling-param walk that runs against the param-wiring.
    """
    if not isinstance(value, dict):
        return False
    if key not in value or not isinstance(value[key], str):
        return False
    for k in value:
        if k == key:
            continue
        if isinstance(k, str) and k.startswith("x-"):
            continue
        return False
    return True


def _collect_singleton_values(value: Any, key: str) -> list[str]:
    """Walk ``value``; return every string from a ``{key: str}`` singleton dict (x-* tolerant).

    A `literal` payload is NOT walked. `resolve_value_expression` returns a
    literal's contents verbatim, so a `from_param`/`from_input` inside one is
    inert data, not a binding — the engine never resolves it. Walking in made
    `{"literal": {"from_input": "record.id"}}` satisfy the "this placeholder has
    a binding" test in `_validate_param_wiring`, pass the `record.<dotted>`
    shape check, pass the `input.schema` membership check, and then put the
    literal dict itself on the wire as the path segment. A wrong URL with no
    error anywhere — the silently mis-bound path segment the write
    `path_params` rules exist to eliminate, re-entering through the door the
    `from_input` binding opened.
    """
    found: list[str] = []
    if isinstance(value, dict):
        if _matches_singleton(value, key):
            found.append(value[key])
            return found
        if "literal" in value:
            return found
        for v in value.values():
            found.extend(_collect_singleton_values(v, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_singleton_values(item, key))
    return found


def _has_disallowed_dynamic_refs(value: Any) -> str | None:
    """Return the first ``stream.*``/``state.*``/``runtime.*`` ref encountered, or ``None``."""
    disallowed_prefixes = ("stream.", "state.", "runtime.")
    if isinstance(value, dict):
        if _matches_singleton(value, "ref"):
            ref = value["ref"]
            for prefix in disallowed_prefixes:
                if ref == prefix.rstrip(".") or ref.startswith(prefix):
                    return ref
            return None
        for v in value.values():
            r = _has_disallowed_dynamic_refs(v)
            if r is not None:
                return r
    elif isinstance(value, list):
        for item in value:
            r = _has_disallowed_dynamic_refs(item)
            if r is not None:
                return r
    return None


def _expression_tokens(value: Any) -> Iterator[str]:
    """Yield every resolution token reachable in ``value``, stripped like the
    resolver strips before lookup.

    A token is a whole `{ref}` path or one `${...}` placeholder key inside a
    template — the two things that address the resolution scopes. Parsed with
    the shared resolver grammar (``iter_expression_strings``), so refs nested in
    ``Any``-typed slots and bare-string templates are reached while protected
    ``literal`` subtrees are skipped: the validators see exactly what the
    resolver will resolve, no more and no less.
    """
    for kind, s in iter_expression_strings(value):
        if kind == "ref":
            yield s.strip()
        else:
            yield from template_placeholders(s)


def _first_unscoped_expression(value: Any) -> str | None:
    """Return the first ref or `${...}` template placeholder whose leading token
    is not a known resolution scope, or ``None``. Complements ``RefExpression``'s
    published ``pattern`` (typed nodes) by reaching refs *and* templates buried in
    ``Any``-typed request slots, parsed via the shared resolver grammar
    (``iter_expression_strings`` skips protected ``literal`` subtrees)."""
    for token in _expression_tokens(value):
        if not _has_known_scope(token):
            return token
    return None


def _validate_param_wiring(
    request: _RequestBase,
    params: dict[str, Param],
    *,
    allow_from_input: bool,
) -> None:
    """Validate from_param/from_input usage and request-binding location rules."""
    # Reject malformed expression dicts (e.g. `{from_param: "x", "rogue": 1}`)
    # at their actual location before the per-binding walks. Without this,
    # the singleton check would fall through to recursion and the user would
    # only see a misleading "param not referenced" error rooted at the
    # param-binding-uniqueness validator — pointing at the wrong failure site.
    _validate_expression_shapes(request.path_params, "request.path_params")
    _validate_expression_shapes(request.headers, "request.headers")
    _validate_expression_shapes(request.query, "request.query")
    _validate_expression_shapes(getattr(request, "body", None), "request.body")

    # `path_params` is a from_input site on WRITE operations only: a REST
    # write addresses one record by its own key (`PATCH /contacts/{id}`), and the
    # id lives in the record, not in a declared param. On a read there is no
    # record in scope at request time, so the original ban stands unchanged —
    # same message, same site.
    banned_from_input_sites: list[tuple[str, Any]] = []
    if not allow_from_input:
        banned_from_input_sites.append(("request.path_params", request.path_params))
    banned_from_input_sites += [
        ("request.headers", request.headers),
        ("request.query", request.query),
    ]
    for where, value in banned_from_input_sites:
        if _collect_singleton_values(value, "from_input"):
            raise ValueError(
                f"from_input is invalid in {where}; on a write it is allowed in "
                "operations.write.<mode>.request.body and, as "
                "`record.<field>`, in operations.write.<mode>.request.path_params "
                "— nowhere on a read (spec: §Cross-Field Validation)"
            )

    for placeholder, expr in (request.path_params or {}).items():
        # ADV-ENDP-027. Percent-encoding a path segment is the ENGINE's job, and
        # it does it unconditionally. An author reaching for `url_encode` here
        # is not adding safety, they are adding a second pass: a record id
        # containing `/` or a space goes on the wire as `a%2520b`, and the
        # provider 404s or matches the wrong resource. Nothing downstream can
        # tell that apart from a value that genuinely contained `%25`, so it is
        # refused at authoring time. Same shape as the idempotency refusals
        # above: the value is engine-owned, so the document does not get to
        # produce it.
        for function_name in _collect_function_names(expr):
            if function_name in _WIRE_ENCODING_FUNCTIONS:
                raise ValueError(
                    f"request.path_params[{placeholder!r}] must not apply "
                    f"{function_name!r}: the engine percent-encodes each "
                    "substituted path segment, so encoding it here sends the "
                    "value double-escaped. Bind the raw value "
                    "(spec: §Request Parameter Binding)"
                )

        from_inputs = (
            _collect_singleton_values(expr, "from_input") if allow_from_input else []
        )
        for from_input in from_inputs:
            # A path segment carries ONE value. Only `record.<dotted>` addresses
            # one; the batch forms address many and the bare record addresses a
            # structure. Each is rejected with the reason it cannot work, so the
            # author is not left guessing which spelling was meant.
            if from_input == "record":
                raise ValueError(
                    f"request.path_params[{placeholder!r}] cannot bind "
                    "`from_input: 'record'` — a whole record is not a single path "
                    "segment; address one field as `record.<dotted>` "
                    "(spec: §Request Parameter Binding)"
                )
            if from_input == "records" or from_input.startswith("records."):
                raise ValueError(
                    f"request.path_params[{placeholder!r}] cannot bind `from_input: "
                    f"{from_input!r}` — a batch has no single value for a path "
                    "segment (spec: §Request Parameter Binding)"
                )
            if not from_input.startswith("record.") or not RECORD_FIELD_PATH_RE.match(
                from_input.removeprefix("record.")
            ):
                # The dotted REMAINDER must be a real field path, not merely
                # start with `record.`. Without the pattern check `"record."`
                # and `"record..id"` passed here, then passed the input.schema
                # membership check too (an empty segment is vacuously "not
                # provably absent"), and bound a URL segment to no field at
                # all — the same silently wrong URL this binding exists to
                # prevent, re-entering through the door this rule opened. Same
                # regex the contract already uses for every other dotted
                # record path.
                raise ValueError(
                    f"request.path_params[{placeholder!r}] from_input value "
                    f"{from_input!r} must be `record.<dotted>` "
                    "(spec: §Request Parameter Binding)"
                )

        names = _collect_singleton_values(expr, "from_param")
        # A `from_input` path_param binds the record directly and declares no
        # param, so it satisfies the "must be a binding" requirement on its own.
        if not names and not from_inputs:
            raise ValueError(
                f"request.path_params[{placeholder!r}] must be a `{{from_param: <name>}}` expression "
                "(spec: §Request Parameter Binding)"
            )
        for name in names:
            param = params.get(name)
            if param is None:
                raise ValueError(
                    f"request.path_params[{placeholder!r}] references unknown param {name!r} "
                    "(spec: §Cross-Field Validation)"
                )
            if param.location != "path":
                raise ValueError(
                    f"request.path_params[{placeholder!r}] binds to param {name!r} which has "
                    f"in={param.location!r}; expected in='path' (spec: §Parameter Validation and Operators)"
                )
            # ADV-ENDP-028, on WRITES only. A write param has exactly one
            # source: its own `default`. `operators` makes a param
            # stream-filterable and `controlled_by` hands it to
            # pagination/replication — both read-side, neither reachable from a
            # write. So a write path param with no `default` provably cannot
            # resolve, and the placeholder it fills can never be substituted.
            # That is the headline broken write document: contract-valid, and
            # dead at the engine handshake. It is refused here, naming the
            # binding that replaces it. Reads keep the old latitude: a read
            # path param can be supplied by a stream filter.
            if allow_from_input and param.default is None:
                raise ValueError(
                    f"request.path_params[{placeholder!r}] binds to param {name!r}, "
                    "which declares no `default` — on a write operation a param "
                    "has no other source, so the placeholder can never be "
                    "substituted. Give the param a `default`, or bind the "
                    'placeholder to the record with `{"from_input": '
                    '"record.<field>"}` (spec: §Request Parameter Binding)'
                )

    for header_name, value in (request.headers or {}).items():
        for name in _collect_singleton_values(value, "from_param"):
            param = params.get(name)
            if param is None:
                raise ValueError(
                    f"request.headers[{header_name!r}] references unknown param {name!r}"
                )
            if param.location != "header":
                raise ValueError(
                    f"request.headers[{header_name!r}] binds to param {name!r} with "
                    f"in={param.location!r}; expected in='header'"
                )
        if _has_disallowed_dynamic_refs(value) is not None:
            raise ValueError(
                f"request.headers[{header_name!r}] uses a direct stream/state/runtime ref; "
                "route dynamic values through declared params (spec: §Request Parameter Binding)"
            )
        bad_scope = _first_unscoped_expression(value)
        if bad_scope is not None:
            raise ValueError(
                f"request.headers[{header_name!r}] uses {bad_scope!r} (a ref or template placeholder) whose leading token is "
                f"not a known resolution scope ({', '.join(RESOLUTION_SCOPES)}) (spec: §Value Expressions)"
            )

    for q_name, value in (request.query or {}).items():
        for name in _collect_singleton_values(value, "from_param"):
            param = params.get(name)
            if param is None:
                raise ValueError(
                    f"request.query[{q_name!r}] references unknown param {name!r}"
                )
            if param.location != "query":
                raise ValueError(
                    f"request.query[{q_name!r}] binds to param {name!r} with "
                    f"in={param.location!r}; expected in='query'"
                )
        if _has_disallowed_dynamic_refs(value) is not None:
            raise ValueError(
                f"request.query[{q_name!r}] uses a direct stream/state/runtime ref; "
                "route dynamic values through declared params"
            )
        bad_scope = _first_unscoped_expression(value)
        if bad_scope is not None:
            raise ValueError(
                f"request.query[{q_name!r}] uses {bad_scope!r} (a ref or template placeholder) whose leading token is "
                f"not a known resolution scope ({', '.join(RESOLUTION_SCOPES)}) (spec: §Value Expressions)"
            )

    body = getattr(request, "body", None)
    if body is not None:
        for name in _collect_singleton_values(body, "from_param"):
            param = params.get(name)
            if param is None:
                raise ValueError(
                    f"request.body references unknown param {name!r}"
                )
            if param.location != "body":
                raise ValueError(
                    f"request.body binds to param {name!r} with in={param.location!r}; expected in='body'"
                )
        from_inputs = _collect_singleton_values(body, "from_input")
        if not allow_from_input and from_inputs:
            raise ValueError(
                "from_input is allowed only on write operations — in the request "
                "body, or as `record.<field>` in request.path_params "
                "(spec: §Cross-Field Validation)"
            )
        # Disjoint cases: 'record', 'records', or 'record.<dotted>'. Anything
        # else — including 'records.<dotted>' (dotted paths through batch
        # arrays) — is invalid in v1.
        for fi in from_inputs:
            if fi in ("record", "records"):
                continue
            if fi.startswith("record.") and not fi.startswith("records."):
                continue
            if fi.startswith("records."):
                raise ValueError(
                    f"from_input value {fi!r} is invalid; dotted paths through batch arrays "
                    "are unsupported in v1 (spec: §Cross-Field Validation)"
                )
            raise ValueError(
                f"from_input value {fi!r} must be 'record', 'records', or 'record.<dotted>' "
                "(spec: §Cross-Field Validation)"
            )
        if _has_disallowed_dynamic_refs(body) is not None:
            raise ValueError(
                "request.body uses a direct stream/state/runtime ref; "
                "route dynamic values through declared params"
            )
        bad_scope = _first_unscoped_expression(body)
        if bad_scope is not None:
            raise ValueError(
                f"request.body uses {bad_scope!r} (a ref or template placeholder) whose leading token is not a known "
                f"resolution scope ({', '.join(RESOLUTION_SCOPES)}) (spec: §Value Expressions)"
            )


def _validate_pagination_wiring(pagination: Any, params: dict[str, Param]) -> None:
    """Validate pagination param references and ``controlled_by`` markers."""
    referenced: list[str] = []
    if isinstance(pagination, OffsetPagination):
        referenced.append(pagination.offset.param)
    elif isinstance(pagination, PagePagination):
        referenced.append(pagination.page.param)
    elif isinstance(pagination, CursorPagination):
        referenced.append(pagination.cursor.param)
    elif isinstance(pagination, KeysetPagination):
        referenced.append(pagination.keyset.param)
    # LinkPagination declares no cursor param (spec: §Pagination Strategies —
    # link replaces the entire URL, no params traverse to follow-up requests).
    # Every strategy carries an optional `limit`.
    if pagination.limit and pagination.limit.param:
        referenced.append(pagination.limit.param)

    for name in referenced:
        param = params.get(name)
        if param is None:
            raise ValueError(
                f"pagination references unknown param {name!r} (spec: §Cross-Field Validation)"
            )
        if param.controlled_by != "pagination":
            raise ValueError(
                f"param {name!r} is referenced by pagination but does not declare "
                "controlled_by='pagination' (spec: §Cross-Field Validation)"
            )


def _declares_a_type(node: Any) -> bool:
    """Whether a resolved node says what kind of value lives there.

    `type` is the JSON Schema statement; the `native_type`/`arrow_type` pair is
    the contract's own, and either answers the question ADV-ENDP-023 asks.
    """
    if not isinstance(node, dict):
        return False
    if _declared_types(node):
        return True
    return node.get("native_type") is not None and node.get("arrow_type") is not None


def _validate_response_body_paths(
    response: ResponseExtraction,
    pagination: Any,
    request: Any = None,
    params: dict[str, "Param"] | None = None,
) -> None:
    """ADV-ENDP-023: every `response.body[.<path>]` a read operation reads
    OUTSIDE `response.records` must resolve against `response.schema`.

    `response.records` was already anchored to the declared schema; pagination
    and `response.metadata` were not, so a typo'd `response.body.nextt_page`
    silently resolved to nothing at run time and paging stopped after one page —
    a wrong-data bug the document could have been rejected for. The same
    *declared-path resolution* the records anchor uses answers it here, so
    `response.schema` is the one thing an author has to keep honest.

    Scope, deliberately:

    * The WHOLE `pagination` block is walked rather than the known ref sites
      (`stop_when` predicates, `cursor.next_cursor`, `link.next_url`,
      `offset.increment_by`, `page.initial`/`increment_by`, `keyset.initial`).
      Enumerating sites would mean a new strategy field silently escapes the
      rule; walking the block cannot.
    * Every `response.metadata` value, likewise.
    * Tokens are collected with the shared resolver grammar, so a ref buried in
      a `${...}` template counts and a `{literal}` payload does not.
    * Only `response.body[.<path>]` is resolved against `response.schema`.
      `headers`, `status`, `record_count` and `records` are reserved and
      engine-owned — the schema describes the BODY, so it has no opinion about
      them (see :func:`_response_body_segments`).
    * `metadata` is the exception, and is checked by KEY rather than by path:
      its key set is closed and declared in this same document, so
      `response.metadata.<key>` is exactly as checkable as `response.body`.
      Lumping it in with the genuinely engine-owned scopes let
      `response.metadata.nope` through — paging stops after page one, run
      reports success.
    * The addressed node must declare a `type` (or the `native_type`/
      `arrow_type` pair). Resolving a path to `{}` would satisfy the letter of
      the rule and none of its purpose: a conformance kit planting a value at
      that path still has to invent its type, and the invented type is what
      decides whether an ordering comparison in `stop_when` raises. A
      declaration that says nothing is not a declaration.
    """
    sites: list[_ExpressionSite] = []
    if pagination is not None:
        # `model_dump()` rather than the model: predicates nest arbitrarily deep
        # inside `stop_when`, and a plain dict is what the shared token walker
        # knows how to traverse.
        sites.append(_ExpressionSite(
            where="pagination",
            payload=pagination.model_dump(),
            operation=_OperationKind.READ,
            # Pagination is the one request-shaping block that legitimately
            # reads the PREVIOUS page's response.
            can_read_response=True,
        ))
    for key, expression in (response.metadata or {}).items():
        sites.append(_ExpressionSite(
            where=f"response.metadata[{key!r}]",
            payload=expression.model_dump(),
            operation=_OperationKind.READ,
            can_read_response=True,
        ))
    # The REQUEST slots too. A request is built before the response exists, so a
    # `response.*` ref there is refused outright — and it was accepted:
    # `request.query = {"c": {"ref": "response.body.nope"}}` interpolated
    # nothing, the provider answered 200, and the run went green. The same
    # unresolved-path failure this rule catches elsewhere, at the site where
    # the value actually goes onto the wire.
    for slot in _REQUEST_EXPRESSION_SLOTS:
        value = getattr(request, slot, None)
        if value is not None:
            sites.append(_ExpressionSite(
                where=f"request.{slot}",
                payload=value,
                operation=_OperationKind.READ,
                can_read_response=False,
            ))
    for name, param in (params or {}).items():
        if param.default is not None:
            sites.append(_ExpressionSite(
                where=f"params[{name!r}].default",
                payload=param.default,
                operation=_OperationKind.READ,
                can_read_response=False,
            ))

    _sweep_expression_sites(
        sites, response.schema_, frozenset(response.metadata or {})
    )


def _validate_replication_wiring(replication: Replication, params: dict[str, Param]) -> None:
    """Validate replication param references and ``controlled_by`` markers."""
    referenced: list[str] = []
    for cm in replication.cursor_mappings:
        if isinstance(cm, SingleCursorMapping):
            referenced.append(cm.param)
        elif isinstance(cm, WindowCursorMapping):
            referenced.append(cm.start_param)
            referenced.append(cm.end_param)
    for name in referenced:
        param = params.get(name)
        if param is None:
            raise ValueError(
                f"replication references unknown param {name!r} (spec: §Cross-Field Validation)"
            )
        if param.controlled_by != "replication":
            raise ValueError(
                f"param {name!r} is referenced by replication but does not declare "
                "controlled_by='replication' (spec: §Cross-Field Validation)"
            )


def _validate_param_binding_uniqueness(
    request: _RequestBase, params: dict[str, Param]
) -> None:
    """Every declared param must be referenced by exactly one request binding.

    Spec: §Cross-Field Validation — "Every declared param must be referenced
    by exactly one request binding" + "If a provider requires the same
    resolved value in two request locations, declare two params with the
    same default/source and bind each param once."
    """
    refs: list[str] = []
    refs.extend(_collect_singleton_values(request.path_params, "from_param"))
    refs.extend(_collect_singleton_values(request.headers, "from_param"))
    refs.extend(_collect_singleton_values(request.query, "from_param"))
    refs.extend(_collect_singleton_values(getattr(request, "body", None), "from_param"))
    counts = Counter(refs)

    for name in params:
        n = counts.get(name, 0)
        if n == 0:
            raise ValueError(
                f"declared param {name!r} is not referenced by any request binding "
                "(spec: §Cross-Field Validation — every declared param must be "
                "referenced by exactly one request binding)"
            )
        if n > 1:
            raise ValueError(
                f"declared param {name!r} is referenced by {n} request bindings; "
                "every declared param must be referenced exactly once "
                "(spec: §Cross-Field Validation — declare two params if the "
                "same value is needed in two request locations)"
            )


# ---------------------------------------------------------------------------
# Declared-path resolution
#
# ONE algorithm answers "does this dotted path address something the document
# declares?" for every site that asks: `response.records`, replication
# `cursor_field`, and (since ADV-ENDP-023) every `response.body` path
# pagination and `response.metadata` read. Before this there were two
# half-answers — a `properties`-only walk for records/cursor_field and nothing
# at all for pagination — which is why a typo in a pagination ref could ship.
#
# The rule, in one sentence: a segment resolves when the current node declares
# it under `properties`, counting the declarations `allOf` branches and an
# in-document `$ref` target contribute, because those always apply.
#
# What it deliberately does NOT do is guess. `anyOf` / `oneOf` / `if`-`then`-
# `else` / `patternProperties` / a dict-valued `additionalProperties` may or may
# not declare a field depending on the instance; picking one branch would make
# the contract's answer depend on authoring order. So when a segment is absent
# and one of those keywords is present, the document is reported as not
# statically resolvable, with the fix named ("declare it under `properties`")
# rather than silently accepted or silently rejected as a typo.
#
# MONOTONICITY is the load-bearing property, and the ambiguity check is ordered
# to preserve it: it runs ONLY when the segment was not found. A node that
# declares BOTH `properties.next` and a `oneOf` still resolves `next` through
# `properties`, exactly as before. Every path the old walk resolved still
# resolves, to the same node — so turning this rule on cannot invalidate a
# document that was honest about its schema. The deliberate exceptions are the
# two contradictions composition can PROVE, and it refuses only on those:
# disjoint `type` sets contributed for one name (`_compose_declarations`,
# `_refuse_disjoint_types`), and an `allOf` branch that is the boolean schema
# `false` (`_reject_unsatisfiable_branch`, reached from BOTH walkers). `allOf`
# refinement — a branch narrowing a base declaration — is the idiom `allOf`
# exists for, so it composes rather than failing.
# ---------------------------------------------------------------------------


class SchemaResolutionError(ValueError):
    """Base for the two ways declared-path resolution refuses to answer.

    A caller that only needs "this did not resolve, and here is why" catches
    this; one that needs the failing position catches
    :class:`DeclaredPathError` specifically. Both carry ``reason`` — the
    resolver's half of the message, kept free of site framing so every call
    site reports the same diagnosis in its own words.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        #: The diagnosis, without any site framing.
        self.reason = reason


class DeclarationConflictError(SchemaResolutionError):
    """Contributors for one name cannot all hold. Carries NO path coordinates.

    Raised wherever composition proves a contradiction — `_refuse_disjoint_types`
    (from :func:`_compose_declarations`, :func:`_combine_schema_values` and
    :func:`_materialize`) and `_reject_unsatisfiable_branch` (from BOTH walkers).
    Each inspects a node or a name; none knows where it sits in anyone's path.
    :func:`resolve_declared_path` catches every one of them and re-raises a
    :class:`DeclaredPathError` carrying the segment it was resolving, so a
    caller may catch only the narrow class.

    A separate class, rather than a :class:`DeclaredPathError` with placeholder
    coordinates: the placeholder (`segment=None, index=-1`) was constructible,
    reachable through the public :func:`effective_properties`, and actively
    misleading — `segments[-1]` silently yields the LAST segment rather than
    "no segment", so a consumer following the in-repo idiom reported the failure
    at the wrong end of the path.
    """

class DeclaredPathError(SchemaResolutionError):
    """A declared path did not resolve AT A KNOWN POSITION.

    Call sites frame the failure with their own site name (which block the path
    came from) and re-raise a plain ``ValueError``; the ``reason`` is the
    resolver's half of the message, kept separate so every site reports the
    same diagnosis in its own words.

    ``segment`` and ``index`` always locate a real position in the path that was
    requested — ``index`` is a valid subscript of the caller's ``segments``, so
    ``".".join(segments[: exc.index + 1])`` is the walked prefix. A failure with
    no position is a :class:`DeclarationConflictError` instead.
    """

    def __init__(self, reason: str, segment: str, index: int) -> None:
        if index < 0:
            raise ValueError(
                f"DeclaredPathError.index must be a position in the path, got {index!r}; "
                "a failure with no position is a DeclarationConflictError"
            )
        super().__init__(reason)
        #: The segment that failed.
        self.segment = segment
        #: Position of ``segment`` in the requested path.
        self.index = index


def _unescape_pointer_token(token: str) -> str:
    """One JSON Pointer reference token, decoded.

    Two decodings, in the order the specs stack them:

    1. **Percent-decoding** (RFC 6901 §6). A `$ref` is a URI-reference and its
       fragment is the URI-fragment form of a pointer, so `%XX` escapes are the
       URI layer and are decoded FIRST. This is not cosmetic: a stock
       JSON-Pointer library decodes them, so skipping it would make this
       resolver disagree with the engine and the conformance kit about which
       refs are dangling (`#/$defs/my%20def` would resolve there and not here).
       A literal `%` in a key must therefore be written `%25`, exactly as the
       spec requires.
    2. **Pointer unescaping** (RFC 6901 §3): `~1` is `/` and `~0` is `~`, in
       that order — reversing them would turn an encoded `~1` into `/` twice.
    """
    return unquote(token).replace("~1", "/").replace("~0", "~")


def _pointer_array_index(token: str, array: list[Any]) -> int | None:
    """RFC 6901 §4 array index, or ``None`` when the token is not one.

    The index production is `0 | [1-9][0-9]*` — ASCII digits only, no leading
    zero, no sign. `str.isdigit()` is NOT that test: it accepts superscripts and
    other Unicode digit forms that `int()` then rejects with a raw `ValueError`,
    which would escape a resolver whose callers only catch
    :class:`DeclaredPathError`. It also accepts `00`, which the spec forbids and
    which would otherwise resolve while `01` did not — two spellings, two
    answers.
    """
    if token != "0" and not (token[:1] in "123456789" and token.isascii() and token.isdigit()):
        return None
    index = int(token)
    return index if index < len(array) else None


def resolve_local_pointer(root: Any, ref: str) -> Any:
    """Resolve an in-document `$ref` (`#`, `#/a/b`) to its node, or ``_MISSING``.

    The raw RFC 6901 walk over the document. Returns the sentinel — not
    ``None`` — because ``None`` is a legal thing to find at a JSON pointer and
    "found null" must not read as "not found".

    Three rules a re-implementation must match, stated because a stock library
    settles them differently or not at all:

    * tokens are percent-decoded then pointer-unescaped
      (:func:`_unescape_pointer_token`);
    * array indices follow RFC 6901 §4 exactly (:func:`_pointer_array_index`);
    * a plain-name fragment (`#name`) resolves to nothing here. It is an
      `$anchor` reference, and this contract does not author anchors — the
      guard rejects both the anchor declaration and the reference to it with
      their own message, so an author is never told a working anchor is
      "dangling".

    A non-local `ref` (anything not starting with `#`) is never resolved here:
    the contract refuses those outright (:func:`_validate_schema_refs`), because
    nothing in the offline validate/author/execute path can fetch them.

    This walk is position-blind: it will happily land inside a `default` or an
    `examples` payload. Everything that *follows* a ref goes through
    :func:`resolve_schema_ref`, which does not.
    """
    if not isinstance(ref, str) or not ref.startswith("#"):
        return _MISSING
    node: Any = root
    fragment = ref[1:]
    if not fragment:
        return node
    if not fragment.startswith("/"):
        return _MISSING
    for token in fragment[1:].split("/"):
        key = _unescape_pointer_token(token)
        if isinstance(node, dict):
            if key not in node:
                return _MISSING
            node = node[key]
        elif isinstance(node, list):
            index = _pointer_array_index(key, node)
            if index is None:
                return _MISSING
            node = node[index]
        else:
            return _MISSING
    return node


#: Schema positions a JSON Pointer must not CROSS. Each holds a real subschema,
#: but one that applies only to some instances — a branch of a choice, an arm of
#: a condition, a rule for names or extras the document did not name outright.
#: A pointer that lands inside one addresses a conditional declaration, and
#: declared-path resolution reports only unconditional ones, so following it
#: would smuggle in exactly the guess the algorithm refuses to make when it
#: meets the same keyword on a node directly.
#:
#: `allOf` is absent on purpose: its branches all apply, so a pointer through
#: one addresses something unconditional. `properties`, `items`, `prefixItems`,
#: `$defs` and `definitions` are likewise unconditional positions.
_CONDITIONAL_POINTER_KEYWORDS: frozenset[str] = frozenset({
    "anyOf", "oneOf", "if", "then", "else", "not",
    "dependentSchemas", "patternProperties", "contains",
    "additionalProperties", "unevaluatedProperties", "unevaluatedItems",
})


def resolve_schema_ref(root: Any, ref: str) -> Any:
    """Resolve an in-document `$ref` that lands on a SCHEMA, or ``_MISSING``.

    :func:`resolve_local_pointer` restricted to pointers that stay inside
    schema positions — the same ``_JSON_SCHEMA_*_KEYS`` inventory both
    structural walkers recurse through, plus the `$defs`/`definitions` maps.

    Why the restriction is load-bearing: the walkers deliberately do NOT descend
    into `default`, `examples`, `const` or `enum`, because those carry arbitrary
    user data that may be shaped exactly like a schema. A pointer such as
    `#/properties/x/default` would therefore reach a subtree that no walker ever
    annotation-checked and no type map ever covered — and declared-path
    resolution would then hand that unvalidated node to its callers as if it
    were a declaration. Refusing to resolve there is what makes
    :func:`_validate_schema_refs`' guarantee true rather than aspirational.

    A pointer whose path CROSSES a conditional keyword is refused for the same
    reason, and it is the subtler half. `#/anyOf/0` names a perfectly real
    schema position — but that subschema applies only to instances that take
    that branch, so following the pointer would let the resolver commit to one
    branch of an `anyOf` and report its fields as unconditionally declared.
    That is precisely the guess the whole algorithm refuses to make when it
    meets `anyOf` on a node directly; reaching the same branch through a `$ref`
    must not be a way around it. Refused with the rest, so "never picks a
    branch" holds however the branch is addressed.

    Note this deliberately ignores `$id`. Under 2020-12 an `$id` retargets the
    base URI, which would make a `#`-leading ref external; the contract refuses
    `$id` in an embedded schema outright (:func:`_validate_schema_refs`) rather
    than tracking base URIs, so by the time anything resolves a ref there is
    exactly one base — the embedded document.
    """
    if not isinstance(ref, str) or not ref.startswith("#"):
        return _MISSING
    fragment = ref[1:]
    if not fragment:
        return root
    if not fragment.startswith("/"):
        return _MISSING
    tokens = [_unescape_pointer_token(t) for t in fragment[1:].split("/")]
    node: Any = root
    index = 0
    while index < len(tokens):
        if not isinstance(node, dict):
            return _MISSING
        key = tokens[index]
        if key in _CONDITIONAL_POINTER_KEYWORDS:
            return _MISSING
        child = node.get(key, _MISSING)
        if child is _MISSING:
            return _MISSING
        if key in JSON_SCHEMA_SUBSCHEMA_KEYS:
            # A map of schemas: the next token names one of them.
            if not isinstance(child, dict) or index + 1 >= len(tokens):
                return _MISSING
            name = tokens[index + 1]
            if name not in child:
                return _MISSING
            node = child[name]
            index += 2
            continue
        if key in JSON_SCHEMA_LIST_OF_SCHEMA_KEYS or key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS:
            if isinstance(child, list):
                # A list of schemas (including draft-07 tuple-form `items`):
                # the next token is the position.
                if index + 1 >= len(tokens):
                    return _MISSING
                position = _pointer_array_index(tokens[index + 1], child)
                if position is None:
                    return _MISSING
                node = child[position]
                index += 2
                continue
            if key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS:
                node = child
                index += 1
                continue
            return _MISSING
        # Any other key is not a schema position — see the docstring.
        return _MISSING
    return node


# Composition (`_property_contributors` / `materialize_node`) walks a schema that
# is a DAG with back-edges, not a tree: `$defs` entries are routinely reached
# from several places, `allOf` multiplies the routes, and a recursive `$defs` is
# legal and common. Expanding each route separately is exponential in depth.
#
# An earlier attempt memoized per node and refused to cache any result computed
# under a cycle. That fixed the acyclic case and did nothing for the real one:
# the "computed under a cycle" flag propagates to every ancestor, so a single
# back-edge anywhere left the whole walk uncached and exponential again — and
# slower than no memo at all, because it also paid for the bookkeeping.
#
# What both walkers do instead: ONE recursive fold, memoized on the RESULT, with
# a separate `on_path` set for cycles. Those two sets answer different questions
# and conflating them was the bug — a node already VISITED returns its cached
# result, while a node on the CURRENT path contributes nothing (the contract's
# "a cycle contributes nothing the second time it is met"). Memoizing the result
# reproduces the fold exactly on any acyclic document and visits each node once,
# so cyclic and acyclic cost the same.
#
# ORDER IS PART OF THE ANSWER, not an implementation detail. Flattening the fold
# into a single pre-order and reversing it looked equivalent and was not: it
# inverted precedence between a DIRECT later branch and a transitively-reached
# contributor of an earlier one, so a nearby `allOf` override silently lost to a
# distant base and a destination column changed type with no error anywhere.
#
# Both walkers therefore use the SAME source order — `$ref` target, then `allOf`
# branches in document order, then the node itself — with the last contributor
# winning. They are one fold seen from two angles: `_contributors` keeps each
# name's declarations as a list, `_materialize` folds them into a value. Every
# time the two were computed differently they disagreed, silently, about which
# declaration wins.


def _property_contributors(node: dict[str, Any], root: Any) -> dict[str, list[Any]]:
    """Every UNCONDITIONAL declaration of each property name, LOWEST precedence
    first.

    Structurally identical to :func:`_materialize` — same recursion, same source
    order (`$ref` target, `allOf` branches in document order, then the node
    itself), same memo. That is not a coincidence to be optimised away: the two
    are the same fold seen from two angles, and every time they were computed
    differently they disagreed, silently, about which declaration wins.

    The ordering contract is the whole point. `_compose_declarations` wraps these
    into `{"allOf": [...]}` and every consumer materializes that LAST-WINS, so
    the list must run lowest-precedence first: a node's own `properties.<name>`
    beats its `$ref` base's, and a later `allOf` branch beats an earlier one.
    Three previous attempts got this wrong in three different ways — a reversed
    pre-order inverted sibling branches, an un-reversed one let the base beat the
    refinement, a flattened post-order emitted a shared node at its first
    (lowest) position — and all three shipped green, because the failure is a
    different `arrow_type` on a derived column, not an error.

    Declarations are deduplicated by equality, and a declaration re-contributed
    by a LATER source moves to the end — its highest-precedence position. Keeping
    the first occurrence is NOT safe and was one of the three bugs above; the
    inline comment at the dedup states the failure.
    """
    return _contributors(node, root, {}, set())


def _contributors(
    node: dict[str, Any],
    root: Any,
    memo: dict[int, tuple[Any, dict[str, list[Any]]]],
    on_path: set[int],
) -> dict[str, list[Any]]:
    """Memoized worker. See :func:`_materialize` for the memo/cycle scheme."""
    key = id(node)
    cached = memo.get(key)
    if cached is not None:
        return cached[1]
    if key in on_path:
        return {}
    on_path.add(key)

    sources: list[dict[str, list[Any]]] = []
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = resolve_schema_ref(root, ref)
        if isinstance(target, dict):
            sources.append(_contributors(target, root, memo, on_path))
    branches = node.get("allOf")
    if isinstance(branches, list):
        for branch in branches:
            _reject_unsatisfiable_branch(branch)
            if isinstance(branch, dict):
                sources.append(_contributors(branch, root, memo, on_path))
    own = node.get("properties")
    if isinstance(own, dict):
        sources.append({name: [declaration] for name, declaration in own.items()})

    contributors: dict[str, list[Any]] = {}
    for source in sources:
        for name, declarations in source.items():
            bucket = contributors.setdefault(name, [])
            for declaration in declarations:
                # A declaration contributed again by a LATER source moves to the
                # end — its highest-precedence position. Keeping the first
                # occurrence and skipping the rest was the bug: an identical
                # declaration reached early through a base pinned itself below a
                # later sibling that restated it, so the sibling lost. Dedup is
                # about not stacking the same shape twice, not about which
                # position it holds.
                for index, seen in enumerate(bucket):
                    if declaration == seen:
                        bucket.pop(index)
                        break
                bucket.append(declaration)

    on_path.discard(key)
    memo[key] = (node, contributors)
    return contributors


def _reject_unsatisfiable_branch(branch: Any) -> None:
    """`allOf: [false, …]` is an empty intersection.

    Checked in BOTH walkers rather than only the materializing one: a rule
    enforced by one view and not the other is how `effective_properties` came to
    answer `{}` where `materialize_node` raised, which crashed a public helper on
    a document the gate had accepted. `true` is vacuous and is simply skipped.
    """
    if branch is False:
        raise DeclarationConflictError(
            "an `allOf` branch is the boolean schema `false`, which no instance "
            "satisfies, so the whole intersection is empty"
        )


def _declared_types(declaration: Any) -> set[str] | None:
    """The `type` values a declaration allows, or ``None`` when it declares none."""
    if not isinstance(declaration, dict):
        return None
    declared = declaration.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list) and all(isinstance(t, str) for t in declared):
        return set(declared)
    return None


def _compose_declarations(key: str, declarations: list[Any]) -> Any:
    """The single declaration a name resolves to, given its contributors.

    One contributor resolves to itself. Several compose as `allOf` — which is
    exactly what the document said: `allOf` and `$ref` are intersections, so a
    branch that NARROWS a base declaration (`{type: string}` refined to
    `{type: string, format: date-time}`) is the dominant real-world idiom and
    must resolve, not fail. The resolver's job is to LOCATE a declaration, not
    to decide its content, so it composes rather than picks.

    The one refusal is a contradiction it can prove: contributors whose `type`
    sets are disjoint cannot both hold, so nothing satisfies the intersection
    and no answer about the field is honest. Proof is required — mere
    inequality is not a contradiction — because rejecting on difference alone
    would break monotonicity with the `properties`-only walk this replaced.
    """
    if not declarations:
        # Unreachable via `_property_contributors`, which only creates a bucket
        # when it has something to put in it. Asserted rather than assumed: the
        # natural "empty" answer here is `{"allOf": []}`, a vacuous schema
        # meaning "anything" — the one answer a resolver must never invent.
        raise DeclarationConflictError(
            f"no contributor declares {key!r}; refusing to compose a vacuous declaration"
        )
    if len(declarations) == 1:
        return declarations[0]
    _refuse_disjoint_types(key, declarations)
    # NOT reversed. `_contributors` already emits lowest-precedence first, in
    # the fold's own source order, which is exactly what a last-wins `allOf`
    # needs. Reversing here was an attempt to compensate for a pre-order walk
    # and it inverted sibling `allOf` branches instead.
    return {"allOf": list(declarations)}


#: Stand-in "name" for the whole-node case. `_refuse_disjoint_types`' message is
#: written for a property name; passing the literal "node" made it read as a
#: field called `node`, which no document has.
_MATERIALIZED_NODE = "<this node>"


def _refuse_disjoint_types(where: str, sources: list[Any]) -> None:
    """Raise when ``sources`` declare `type` sets whose intersection is empty.

    The one contradiction declared-path resolution can PROVE. Shared by
    :func:`_compose_declarations` (the contributors of one property name) and
    :func:`materialize_node` (a node folded with its `$ref`/`allOf` sources) so
    that the two agree: composing and materializing are the same intersection
    seen from two directions, and a rule enforced by only one of them is a gate
    the other walks past. Proof is required — mere inequality is not a
    contradiction — or monotonicity with the `properties`-only walk breaks.

    Raises :class:`DeclarationConflictError`, which carries no path coordinates:
    both callers inspect a node or a name and neither knows where it sits in
    anyone's path. :func:`resolve_declared_path` re-raises it with the segment
    it was resolving.
    """
    type_sets = [t for t in (_declared_types(s) for s in sources) if t is not None]
    if len(type_sets) > 1 and not set.intersection(*type_sets):
        raise DeclarationConflictError(
            f"conflicting redeclaration of {where!r} across allOf/$ref branches: "
            f"the declared types {sorted({t for s in type_sets for t in s})!r} "
            "are disjoint, so no instance can satisfy all of them"
        )


def effective_properties(
    node: dict[str, Any], root: Any = None
) -> dict[str, Any]:
    """The property declarations that UNCONDITIONALLY apply to ``node``.

    :func:`_property_contributors` composed per name
    (:func:`_compose_declarations`).

    ``root`` is the document `$ref` pointers resolve against; it defaults to
    ``node``, which is correct when ``node`` IS the whole embedded schema.
    Callers walking INTO a schema must pass the original root, or a `$ref`
    would resolve against a subtree and silently miss `$defs`.

    This composes EVERY name, so it reports a provable contradiction anywhere in
    the node — it is the whole-node view. :func:`resolve_declared_path` composes
    only the segment it is looking for, so a contradiction on an unrelated key
    never blocks an unrelated path; that difference is deliberate and is the
    reason the resolver does not simply call this.
    """
    root = node if root is None else root
    return {
        key: _compose_declarations(key, declarations)
        for key, declarations in _property_contributors(node, root).items()
    }


#: How a key's value should be merged. Only a SCHEMA position can hold a
#: contradiction worth proving; `default`, `const`, `examples` and `x-*` carry
#: arbitrary user data that may be shaped exactly like a schema, and running the
#: `type` check there proved two `default` objects "contradictory" because both
#: happened to have a field named `type`.
class _Position(str, Enum):
    """What a key's value IS, which decides how it merges and whether a
    contradiction in it is worth proving."""

    SCHEMA = "schema"
    SCHEMA_MAP = "schema_map"
    DATA = "data"


_SCHEMA_POSITION = _Position.SCHEMA
_SCHEMA_MAP_POSITION = _Position.SCHEMA_MAP
_DATA_POSITION = _Position.DATA


def _position_kind(key: str) -> _Position:
    if key in JSON_SCHEMA_SUBSCHEMA_KEYS:
        return _SCHEMA_MAP_POSITION
    if key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS:
        return _SCHEMA_POSITION
    return _DATA_POSITION


def _combine_schema_values(
    existing: Any, incoming: Any, *, kind: _Position = _Position.DATA
) -> Any:
    """Fold ``incoming`` into ``existing`` for one key of a materialized node.

    Objects merge recursively — `allOf` and `$ref` are intersections, so BOTH
    declarations apply and an `allOf` branch that refines `items` must ADD to
    the base rather than replace it. Overwriting was a real defect: a record
    shape whose fields came from an inline `properties` map lost them to an
    `allOf` branch, silently changing the enumerated column set.

    Everything else — scalars AND lists — is last-wins. That is the pre-existing
    behaviour and it is genuinely lossy for `required` and `enum`, whose true
    intersection this does not compute. The one lossy case that could produce a
    confidently-wrong answer, mutually exclusive `type` sets, is refused
    outright by :func:`_refuse_disjoint_types` before any merging happens.
    """
    if isinstance(existing, dict) and isinstance(incoming, dict):
        if kind == _SCHEMA_POSITION:
            # Only here. This is a real subschema, so two contributors declaring
            # disjoint `type`s cannot both hold — the same proof
            # `_compose_declarations` applies to a property name's contributors,
            # so the two views of one document cannot disagree. Applying it at
            # every depth instead read `default`/`const`/`x-*` payloads as
            # schemas and refused documents that were merely carrying data.
            _refuse_disjoint_types(_MATERIALIZED_NODE, [existing, incoming])
        child_kind = {
            _SCHEMA_MAP_POSITION: _SCHEMA_POSITION,
            _SCHEMA_POSITION: None,
            _DATA_POSITION: _DATA_POSITION,
        }[kind]
        merged = dict(existing)
        for key, value in incoming.items():
            merged[key] = (
                _combine_schema_values(
                    merged[key], value,
                    kind=_position_kind(key) if child_kind is None else child_kind,
                )
                if key in merged
                else value
            )
        return merged
    return incoming


def materialize_node(node: Any, root: Any = None) -> Any:
    """``node`` folded together with the contributors that unconditionally apply.

    The inspection counterpart of :func:`effective_properties`: it answers
    "what does this node say about `type` / `items` / `properties`?" when the
    answer is spread across a `$ref` target and `allOf` branches. Without it a
    consumer reading `node["type"]` off a `{"$ref": "#/$defs/Coll"}` sees
    nothing — which is how a document following ADV-ENDP-026's own advice
    ("put it in this document's `$defs`") could validate and then yield zero
    record fields.

    `$ref` and `allOf` are CONSUMED, so a materialized node never carries them.
    Sources apply in the order `$ref` target, `allOf` branches, then the node
    itself, each already materialized — so the node's own statements win, and
    among its contributors the LAST one stated wins. That is what makes the
    canonical `allOf: [{$ref: Base}, {refinement}]` idiom mean what it says.
    Object values (`items`, a nested `properties` map) merge recursively rather
    than replace, because `allOf` is an intersection and both declarations
    apply.

    ``root`` is the document `$ref` pointers resolve against; it defaults to
    ``node``, which is correct only when ``node`` IS the whole embedded schema.
    Pass the real root when materializing a subtree, or `$defs` is unreachable.
    """
    if not isinstance(node, dict):
        return node
    return _materialize(node, node if root is None else root, {}, set())


def _materialize(
    node: dict[str, Any],
    root: Any,
    memo: dict[int, tuple[Any, Any]],
    on_path: set[int],
) -> Any:
    """Memoized fold. Each node is materialized ONCE and its RESULT reused.

    Two earlier shapes of this were wrong, both silently, and the reason is
    worth keeping:

    * Refusing to cache anything computed under a cycle made a single `$ref`
      back-edge disable the cache for every ancestor, so the walk stayed
      exponential on exactly the schemas that motivated the cache.
    * Flattening the fold into one ordered chain with a global visited set
      cannot express the fold at all. A node reached from two places was
      emitted at its FIRST position — the LOWEST precedence in a last-wins
      merge — while the fold re-merged it at every position, giving it the
      precedence of its LAST. On 4000 acyclic shared-`$defs` schemas the two
      disagreed 1502 times: a nearby refinement lost to a distant base, and a
      destination column was created `Utf8` instead of `Timestamp` with no
      error anywhere.

    Memoizing the RESULT is what the fold did, so it reproduces the fold
    exactly on any acyclic document, and it visits each node once. A node
    already on the current path is a cycle: it contributes nothing the second
    time it is met, which is the rule the contract states. The memo holds the
    node beside its result so a freed node's `id()` cannot be reused mid-walk.
    """
    key = id(node)
    cached = memo.get(key)
    if cached is not None:
        return cached[1]
    if key in on_path:
        return {}
    on_path.add(key)

    sources: list[Any] = []
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = resolve_schema_ref(root, ref)
        if isinstance(target, dict):
            sources.append(_materialize(target, root, memo, on_path))
    branches = node.get("allOf")
    if isinstance(branches, list):
        for branch in branches:
            _reject_unsatisfiable_branch(branch)
            if isinstance(branch, dict):
                sources.append(_materialize(branch, root, memo, on_path))
    sources.append({k: v for k, v in node.items() if k not in ("$ref", "allOf")})

    # The same refusal `_compose_declarations` applies to a property name's
    # contributors. Without it the two disagree, and the PERMISSIVE one guards
    # the gate: `allOf: [{type: object}, {type: array, items: …}]` merged
    # last-wins yields a tidy `{type: "array"}` that
    # `_validate_records_in_response_schema` accepts, so a record collection no
    # instance can satisfy validates as a good array.
    _refuse_disjoint_types(_MATERIALIZED_NODE, sources)

    merged: dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for name, value in source.items():
            if name == "properties":
                continue  # composed below, from the shared contributor walk
            merged[name] = (
                _combine_schema_values(merged[name], value, kind=_position_kind(name))
                if name in merged
                else value
            )

    # `properties` is composed per NAME here, and the satisfiability proof below
    # reads the same raw contributor list `_compose_declarations` proves, so the
    # two views cannot disagree about whether a name is self-contradictory.
    # Merging the maps as an ordinary dict key instead was how `materialize_node`
    # came to answer where `effective_properties` refused — and the permissive
    # one is what derives the destination column.
    #
    # Each declaration is FOLDED but NOT recursively materialized: a caller
    # reading an annotation off a child descriptor must materialize that
    # descriptor itself. `find_record_field_properties` and
    # `_walk_input_schema_path` both do exactly that, and the comment at the
    # former explains why — materializing only the walk left a field declared as
    # `{"$ref": "#/$defs/Addr"}` coming back raw, with no `type` and no
    # `arrow_type`.
    #
    # The emptiness test is on DECLARATION, not on the composed result: an
    # explicit `properties: {}` means "zero fields", which
    # `_json_schema_top_level_fields` must be able to tell apart from "no
    # `properties` map anywhere", its unknowable→skip case. Dropping the key
    # when the map is empty collapsed the two and turned `conflict_keys` and the
    # `from_input` membership check back off.
    own_properties = [
        source["properties"] for source in sources
        if isinstance(source, dict) and isinstance(source.get("properties"), dict)
    ]
    if own_properties:
        # The proof reads the RAW contributors — the same list
        # `_compose_declarations` proves — and NOT the maps hanging off
        # `sources`. Those sources have each already been materialized, and
        # `type` is a data position that merges last-wins, so a nested level
        # collapses its own contributors before this one can see them:
        # `{string,integer}` then `{integer,boolean}` folds to
        # `{integer,boolean}`, and the three-way emptiness against
        # `{string,boolean}` becomes a two-way overlap on `boolean`. Proving
        # from the folded maps therefore refused single-level contradictions
        # and accepted nested ones, which is `effective_properties` refusing a
        # record shape `materialize_node` was happy to derive columns from.
        #
        # The walk starts FRESH, exactly as `effective_properties(node, root)`
        # would run it. No second cache: `_materialize`'s own memo already
        # guarantees each node's body runs once per materialization, so a
        # proved-nodes cache here never gets a hit (measured: 0 hits across the
        # full suite and 40k random cyclic documents) — and the SHARED variant
        # that would get hits was the bug. `_contributors` caches `{}` for a
        # node it meets on the CURRENT path, so a shared entry computed while
        # some ancestor was mid-walk carries that truncation, and a later node
        # reads a weaker contributor set than `effective_properties` would
        # compute from a standing start:
        #
        #   A: {$ref: C, properties: {x: {type: [string, boolean]}}}
        #   B: {$ref: A, properties: {x: {type: [integer, boolean]}}}
        #   C: {$ref: A, properties: {x: {type: [string, integer]}}}
        #
        # gave `materialize_node(B)` an accepted `[integer, boolean]` while
        # `effective_properties(B)` refused. Truncation-tainting a shared cache
        # instead was tried earlier in this PR and is exponential — the taint
        # propagates to every ancestor, so one back-edge uncaches the whole walk.
        #
        # THE PRICE, stated because nothing else in this file will: one full
        # contributor walk per node that declares `properties`, which is cubic
        # on a deep UNSHARED `allOf` chain (measured ~3s at depth 400, ~44ms at
        # depth 100). Real record shapes nest < 10 deep, where this is ~0.2ms;
        # the linearity pins bound the shared/cyclic shapes that actually occur.
        raw = _property_contributors(node, root)
        by_name: dict[str, list[Any]] = {}
        for source_map in own_properties:
            for name, declaration in source_map.items():
                by_name.setdefault(name, []).append(declaration)
        properties: dict[str, Any] = {}
        for name, declarations in by_name.items():
            # Fall back to the folded declarations only for a name the raw walk
            # cannot see (a cycle truncated it); never prove on the weaker set
            # when the stronger one exists.
            contributors = raw.get(name) or declarations
            if len(contributors) > 1:
                _refuse_disjoint_types(name, contributors)
            folded = declarations[0]
            for declaration in declarations[1:]:
                folded = _combine_schema_values(
                    folded, declaration, kind=_SCHEMA_POSITION
                )
            properties[name] = folded
        merged["properties"] = properties

    on_path.discard(key)
    memo[key] = (node, merged)
    return merged


#: Keywords whose presence means "this node MIGHT declare more fields, subject to
#: the instance". Their mere presence is enough to make a missing segment
#: ambiguous rather than absent.
_CONDITIONAL_DECLARATION_KEYWORDS: tuple[str, ...] = (
    "anyOf", "oneOf", "if", "then", "else", "patternProperties", "dependentSchemas",
)
#: Catch-alls that only widen the declared set when they carry a SCHEMA. As
#: `true`/`false` (or absent) they say nothing about names, so they must not
#: turn a plain typo into an "ambiguous" report.
_SCHEMA_VALUED_CATCHALL_KEYWORDS: tuple[str, ...] = (
    "additionalProperties", "unevaluatedProperties",
)


def _conditional_declaration_keywords(node: dict[str, Any], root: Any) -> list[str]:
    """Keywords on ``node`` that could conditionally declare an absent segment.

    Returned in a fixed order so error messages are stable and greppable.
    """
    found = [k for k in _CONDITIONAL_DECLARATION_KEYWORDS if k in node]
    found += [
        k for k in _SCHEMA_VALUED_CATCHALL_KEYWORDS if isinstance(node.get(k), dict)
    ]
    ref = node.get("$ref")
    # A `$ref` that DID resolve has already contributed everything it declares,
    # so it cannot be the reason a segment is missing. One that did not resolve
    # can be — and `_validate_schema_refs` will have rejected it in its own
    # right.
    if ref is not None and not isinstance(resolve_schema_ref(root, ref), dict):
        found.append("$ref")
    return found


def resolve_declared_path(
    start_node: Any, segments: Sequence[str], *, root: Any = None
) -> Any:
    """Resolve ``segments`` against ``start_node`` by declared-path resolution.

    THE contract's path-resolution rule (see the module comment above). For each
    segment: the current node must be an object schema; the segment must be
    declared by one of that node's unconditional contributors
    (:func:`_property_contributors`); resolution moves to the composition of
    that name's declarations (:func:`_compose_declarations`). An empty
    ``segments`` resolves to ``start_node`` itself.

    ``root`` is the document `$ref` pointers resolve against, and defaults to
    ``start_node``. A caller that starts the walk at a SUBTREE — the record
    shape under `items`, say — must pass the whole embedded schema as ``root``,
    or a `#/$defs/...` ref inside that subtree resolves against the subtree,
    finds no `$defs`, and the path is misreported as undeclared.

    Only the segment being looked for is composed, so a provable contradiction
    on some unrelated property of the same node does not block this path;
    :func:`effective_properties` is the whole-node view that does report those.

    Raises :class:`DeclaredPathError` — never returns a sentinel — so no caller
    can accidentally treat "unresolvable" as "resolved to nothing". The
    exception's ``reason`` distinguishes the failure modes an author fixes
    differently: a non-object intermediate, a conditionally-declared
    (untightened) schema, a contradiction, and a plain undeclared segment (the
    typo case).
    """
    node: Any = start_node
    document: Any = start_node if root is None else root
    for index, segment in enumerate(segments):
        if not isinstance(node, dict):
            raise DeclaredPathError(
                "intermediate node is not an object schema",
                segment=segment,
                index=index,
            )
        # `_property_contributors` raises `DeclarationConflictError` too (an
        # `allOf` branch that is boolean `false`), so it sits INSIDE the same
        # try as the compose: a conflict raised while collecting contributors is
        # every bit as positioned as one raised while folding them, and leaving
        # it outside sent the bare conflict past the three narrow
        # `except DeclaredPathError` handlers — the document was still refused,
        # but the author lost the field name and the walked prefix.
        try:
            contributors = _property_contributors(node, document)
            if segment in contributors:
                node = _compose_declarations(segment, contributors[segment])
        except DeclarationConflictError as exc:
            # Neither callee has path context of its own (one inspects a node,
            # the other a name); re-raise with the segment/index this walk is at
            # so the caller can say where in the path the contradiction sits.
            raise DeclaredPathError(
                exc.reason, segment=segment, index=index
            ) from None
        if segment in contributors:
            continue

        # Not declared. Only NOW does ambiguity matter — checking it earlier
        # would reject paths that resolve perfectly well through `properties`.
        conditional = _conditional_declaration_keywords(node, document)
        if conditional:
            raise DeclaredPathError(
                "path is not statically resolvable: this node declares "
                f"{', '.join(conditional)}; declare {segment!r} under 'properties'",
                segment=segment,
                index=index,
            )
        raise DeclaredPathError(
            f"{segment!r} is not declared", segment=segment, index=index
        )
    return node


class _OperationKind(str, Enum):
    """Which operation a swept site belongs to.

    Passed explicitly rather than inferred from the site label. Prefix-matching
    a caller-supplied string already produced one wrong message: `Param` is
    shared by reads and writes, so its label matched neither operation prefix
    and write authors were told "paging stops after the first page" about an
    operation that does not page.
    """

    READ = "read"
    WRITE = "write"
    WRITE_RESPONSE = "write_response"


@dataclass(frozen=True, slots=True)
class _ExpressionSite:
    """One swept slot, with everything the checks need STATED by its producer.

    `operation` used to be a per-call default and `WRITE_RESPONSE` was then
    re-derived by prefix-matching `where` — the very inference this enum's
    docstring says it replaced, one field rename away from firing again. A
    producer knows which operation it is and whether its slot is built before
    the response exists; neither is recoverable from a display label, so
    neither is inferred from one.
    """

    where: str
    payload: Any
    operation: _OperationKind
    #: False for a slot built BEFORE the response exists — every `request.*`
    #: slot. A `response.*` ref there resolves to nothing at request-build time
    #: whatever it names, so it is refused on scope alone.
    can_read_response: bool


def _sweep_expression_sites(
    sites: "list[_ExpressionSite]",
    response_schema: Any = None,
    metadata_keys: "frozenset[str] | None" = None,
) -> None:
    """Run EVERY expression check over every site, from one table.

    The four checks — expression shape, leading scope, response sub-scope, and
    (where a `response.schema` exists) declared-path resolution with typedness —
    were wired per CALL SITE rather than per slot, and every hole this PR closed
    after the first was the same shape: a site that was not on somebody's list.
    `request.path_params` was missing from two tables; `pagination` and the write
    `response` never reached the shape walk; `params.<name>.default` was added to
    three walks and not the fourth. `_validate_response_body_paths`' own
    docstring rejects exactly this reasoning for the INSIDE of `pagination`
    ("enumerating sites would mean a new strategy field silently escapes the
    rule; walking the block cannot") — it just was not applied to the
    enumeration of the blocks themselves.

    So: one function, all four checks, and each operation states its slots once.
    A slot added to the table gets every check by construction.
    """
    for site in sites:
        where, payload = site.where, site.payload
        _validate_expression_shapes(payload, where)
        for token in _expression_tokens(payload):
            _reject_unknown_scope(where, token, site.operation)
            _reject_unknown_response_scope(where, token, site.operation)
            if not site.can_read_response and token.split(".")[0] == "response":
                # Scope-level, not path-level. Checking only whether the path
                # RESOLVES let `request.query = {"ref":
                # "response.body.next_cursor"}` through whenever the response
                # schema happened to declare that path — and every `response.*`
                # ref in a write request slot through unconditionally, since a
                # write has no `response.schema` to resolve against. The request
                # is built before the response exists, so the ref interpolates
                # nothing regardless of what it names: the provider is called
                # with the value missing and answers 200.
                raise ValueError(
                    f"{where} references {token!r}, but a request is built "
                    "before the response exists, so no `response.*` value is "
                    "available here — it would interpolate to nothing and the "
                    "request would go out with the value missing. Use a "
                    "`params` entry or a literal "
                    "(spec: §Value Expressions — scopes)"
                )
            if metadata_keys is not None and token.startswith("response.metadata."):
                # `metadata` is the ONE reserved response sub-scope whose key set
                # is closed and author-declared, in this same document. The other
                # reserved scopes (`headers`, `status`, `record_count`,
                # `records`) really are engine-owned and unknowable here, but
                # lumping `metadata` in with them let `response.metadata.nope`
                # through — which resolves to nothing on every page, so paging
                # stops after page one and the run reports success. That is the
                # ADV-ENDP-023 failure verbatim, one segment to the right of
                # where it was fixed.
                key = token.split(".", 2)[2].split(".")[0]
                if key not in metadata_keys:
                    raise ValueError(
                        f"{where} references {token!r}, but "
                        f"{key!r} is not a declared `response.metadata` key "
                        f"(declared: {sorted(metadata_keys)!r}). "
                        f"{_unresolved_harm(site.operation)} "
                        "(spec: §API Response Extraction)"
                    )
            segments = _response_body_segments(token)
            if segments is None or response_schema is None:
                continue
            try:
                node = resolve_declared_path(response_schema, segments)
            except SchemaResolutionError as exc:
                raise ValueError(
                    f"{where} references {token!r}, which does not resolve in "
                    f"response.schema: {exc.reason} "
                    "(spec: §API Response Extraction — declared-path resolution)"
                ) from None
            try:
                materialized = materialize_node(node, response_schema)
            except SchemaResolutionError as exc:
                raise ValueError(
                    f"{where} references {token!r}, which resolves in "
                    f"response.schema to a self-contradictory node: {exc.reason} "
                    "(spec: §API Response Extraction — declared-path resolution)"
                ) from None
            if not _declares_a_type(materialized):
                raise ValueError(
                    f"{where} references {token!r}, which resolves in "
                    "response.schema to a node that declares no `type` (and no "
                    "`native_type`/`arrow_type` pair). Declare the type of the "
                    "value the engine reads there, or nothing can tell what it "
                    "planted (spec: §API Response Extraction — declared-path "
                    "resolution)"
                )



def _unresolved_harm(operation: _OperationKind) -> str:
    """What actually goes wrong when a token resolves to nothing.

    The consequence is the actionable half of these messages, and it differs by
    operation: a success predicate reading nothing holds unconditionally, which
    is not the same failure as paging stopping after one page.
    """
    if operation is _OperationKind.WRITE_RESPONSE:
        return (
            "It resolves to nothing on every response, so a `success_when` "
            "predicate over it holds unconditionally and every write reports "
            "success — including the ones whose rejected rows the provider "
            "listed."
        )
    if operation is _OperationKind.WRITE:
        return (
            "It resolves to nothing at run time, so the request goes out with "
            "that value missing and the provider answers whatever it answers."
        )
    if operation is _OperationKind.READ:
        return (
            "It resolves to nothing at run time, so paging stops after the "
            "first page and the run still reports success."
        )
    # Exhaustive on purpose. With the read text as the fall-through, a raw
    # `"write"` string — `==`-equal to the member but not `is`-identical — got
    # paging advice, which is the wrong-message bug this enum replaced; and a
    # member added later would inherit it silently.
    raise AssertionError(f"unhandled operation kind {operation!r}")


def _reject_unknown_scope(where: str, token: str, operation: _OperationKind) -> None:
    """The LEADING token must name a real resolution scope.

    :func:`_reject_unknown_response_scope` catches a bad SUB-scope
    (`response.bodyy`); this catches a bad scope (`responses.body`,
    `respons.body`, `Response.body`). Both halves are needed — a typo lands
    either side of the dot and the run-time failure is identical.

    The typed `Expression` fields are already covered by the published
    `_RESOLUTION_SCOPE_PATTERN`, but the `Any`-typed paging slots
    (`keyset.initial`, `offset.initial`, `page.initial`, every `Predicate`
    operand) are covered by nothing, and those are the most load-bearing paging
    sites in the contract.
    """
    if _has_known_scope(token):
        return
    raise ValueError(
        f"{where} references {token!r}, whose leading token is not a known "
        f"resolution scope ({', '.join(RESOLUTION_SCOPES)}). {_unresolved_harm(operation)} "
        "(spec: §Value Expressions)"
    )


def _reject_unknown_response_scope(
    where: str, token: str, operation: _OperationKind
) -> None:
    """A `response.*` token must name a real response sub-scope.

    The hole this closes is the one ADV-ENDP-023 exists to close, one segment
    to the left. `_response_body_segments` returns ``None`` for anything that is
    not `response.body[.…]`, and the caller skips it — on the stated grounds
    that every OTHER `response.*` scope is reserved and engine-owned. Nothing
    checked that the token actually named one of them, so
    `response.bodyy.next_cursor` was not "a reserved scope this rule leaves
    alone", it was a typo that resolved to nothing at run time. Paging stopped
    after page one and the sync reported success — the identical silent
    truncation ADV-ENDP-023 exists to catch, reachable by misspelling `body`
    instead of the field after it.

    `_has_known_scope` cannot catch it: it inspects only the LEADING token, and
    `response` is a real scope. The sub-scope needs its own check.
    """
    stripped = token.strip()
    if stripped != "response" and not stripped.startswith("response."):
        return
    sub_scope = stripped[len("response."):].split(".", 1)[0] if "." in stripped else ""
    if sub_scope in RESERVED_RESPONSE_SCOPES:
        return
    raise ValueError(
        f"{where} references {token!r}, whose response sub-scope "
        f"{sub_scope or '(none)'!r} is not one of "
        f"{sorted(RESERVED_RESPONSE_SCOPES)!r}. {_unresolved_harm(operation)} "
        "(spec: §API Response Extraction)"
    )


def _response_body_segments(token: str) -> list[str] | None:
    """Segments a `response.body[.<path>]` token addresses, or ``None``.

    ``None`` means "not a `response.body` token" — every other response scope
    (`response.headers.*`, `response.status`, `response.record_count`,
    `response.records`, `response.metadata`) is RESERVED and engine-owned, so it
    has nothing to resolve against `response.schema` and is not checked here.
    A bare `response.body` addresses the schema root and yields ``[]``.
    """
    stripped = token.strip()
    if stripped == "response.body":
        return []
    if stripped.startswith("response.body."):
        return stripped[len("response.body."):].split(".")
    return None


def resolve_read_record_schema(response: Any, response_schema: Any) -> Any:
    """Resolve ``operations.read.response.records`` to the record-shape subschema.

    ``response.schema`` describes the FULL provider response body, not just the
    records; ``records`` is a ``{ref}`` selecting the record collection inside
    it. Per the api-endpoint contract the ref is anchored at ``response.body``
    (the schema root), and the dotted remainder resolves by *declared-path
    resolution* (:func:`resolve_declared_path`). The addressed
    node's ``items`` (when it is an ``array``) is the record shape — so a
    nested collection like ``response.body.objects`` yields the real record
    columns, not the wrapper key ``objects``.

    Returns ``None`` when the ref does not resolve, is not `response.body`-
    anchored, or lands on something that is not a schema node. ``None`` means
    "this document does not say" — never a fallback to ``response_schema``,
    which is the ENVELOPE and whose keys a caller would then enumerate as the
    record's fields. Callers must branch on it; the gate
    (:func:`_validate_records_in_response_schema`) reports the real reason.

    The single record-locator shared by every consumer of the read contract
    (field extraction and arrow_type stamping): they MUST target the same
    fields, so they call one function.
    """
    if not isinstance(response_schema, dict):
        return response_schema
    records = response.get("records") if isinstance(response, dict) else response
    ref = records.get("ref") if isinstance(records, dict) else records
    if not isinstance(ref, str):
        # Absent, null or non-string `records`. `None` for the same reason every
        # other unresolvable case returns it: `response_schema` is the ENVELOPE,
        # and an ungated caller enumerates its keys as the record's fields. An
        # absent `records` is exactly what ungated input looks like.
        return None
    segments = _response_body_segments(ref)
    if segments is None:
        # Non-spec ref (e.g. a bare JSONPath). The gate rejects this exactly as
        # it rejects a typo, so returning the envelope here reproduces the bug
        # the typo branch was just fixed for: `find_record_field_properties`
        # would enumerate `data`/`has_more` as the table's columns.
        return None
    try:
        node: Any = resolve_declared_path(response_schema, segments)
    except SchemaResolutionError:
        # Do NOT fall back to `response_schema` here. That returned the response
        # ENVELOPE, so a one-character typo in `records.ref` silently enumerated
        # `data`/`has_more`/`next_cursor` as the destination table's columns —
        # a confident wrong answer handed to the one caller the fallback existed
        # to serve (the pipeline plugin's prose calls this without running the
        # gate first). `None` says "I could not resolve this", which is the only
        # honest answer; the gate still reports the ref properly for anyone who
        # does run it.
        return None
    # Materialize before reading `type`/`items`, and again on the record shape:
    # the collection may be reached through a `$ref` (`{"$ref": "#/$defs/Coll"}`)
    # and the record shape is very often `items: {"$ref": "#/$defs/Record"}` —
    # the exact shape ADV-ENDP-026's rejection message tells authors to write.
    # Reading the raw node there would return a bare `{"$ref": …}` and every
    # consumer would enumerate zero fields.
    node = materialize_node(node, response_schema)
    if isinstance(node, dict) and node.get("type") == "array" and isinstance(node.get("items"), dict):
        return materialize_node(node["items"], response_schema)
    # Not a schema node (e.g. `properties.data` declared as boolean `true`).
    # `None`, for the same reason the other branches return it: handing back
    # `response_schema` is handing back the ENVELOPE, whose keys a caller then
    # enumerates as the record's fields.
    return node if isinstance(node, dict) else None


def find_record_field_properties(
    record_schema: Any, root: Any = None
) -> dict[str, Any] | None:
    """Return a record schema's top-level field-descriptor map, or ``None``.

    Walks the ``items`` chain (an array-of-records envelope may nest the record
    object under ``items``) until the first ``properties`` map — the record's
    mappable fields, one level deep. The single field-enumerator shared by the
    read contract's consumers (field extraction and column derivation) so
    they enumerate identical fields. Consumers read type/annotations off
    these top-level descriptors
    only; nested sub-properties, ``items`` elements and composition branches
    are not separately mappable.

    Each step is materialized (:func:`materialize_node`), so a record shape
    assembled from `allOf` branches or reached through an in-document `$ref`
    enumerates the same fields an inline one would. Pass ``root`` (the whole
    embedded schema) whenever ``record_schema`` is a subtree, or its `$ref`s
    have no `$defs` to resolve against.
    """
    document = record_schema if root is None else root
    current = materialize_node(record_schema, document)
    while isinstance(current, dict):
        props = current.get("properties")
        if isinstance(props, dict):
            # Materialize each DESCRIPTOR too. Materializing only the walk left
            # a field declared as `{"$ref": "#/$defs/Addr"}` coming back raw —
            # no `type`, no `arrow_type` — and the documented consumer
            # (plugins/analitiq-pipeline-builder/skills/endpoint-spec/
            # spec-new-table.md) reads exactly those annotations off exactly
            # this map. With nothing to read it invents a column type, which is
            # the silent wrong-data outcome this whole PR exists to remove, one
            # level below where it was fixed.
            return {
                name: materialize_node(declaration, document)
                for name, declaration in props.items()
            }
        items = current.get("items")
        if not isinstance(items, dict):
            return None
        current = materialize_node(items, document)
    return None


def _validate_records_in_response_schema(
    response: ResponseExtraction,
) -> dict[str, Any]:
    """Validate ``response.records`` resolves to an array node in ``response.schema``.

    Returns the array subschema (caller drills into ``items.*``). Always
    raises on failure — never returns ``None``. Spec: §Cross-Field Validation
    — ``response.records`` must resolve to a path represented in
    ``response.schema``, and that schema location must be an array.
    """
    ref: str = response.records.ref  # validated upstream to start with response.body
    segments = _response_body_segments(ref) or []
    try:
        node = resolve_declared_path(response.schema_, segments)
    except DeclaredPathError as exc:
        raise ValueError(
            f"response.records ref {ref!r} traversal failed at segment "
            f"{exc.segment!r}: {exc.reason} "
            "(spec: §API Response Extraction — declared-path resolution)"
        ) from None
    # `resolve_declared_path` returns whatever the path addressed; a non-object
    # there means the declaration itself is not a schema (e.g. `properties.x`
    # holding a string), which no later check would catch.
    if not isinstance(node, dict):
        raise ValueError(
            f"response.records ref {ref!r} resolved to a non-object schema location "
            "(spec: §API Response Extraction — declared-path resolution)"
        )
    # `type`/`items` may be contributed by a `$ref` target or an `allOf` branch
    # rather than stated inline, so read them off the materialized node — the
    # raw one would report `type=None` for a perfectly good `{"$ref": …}`
    # collection. The materialized node is what the caller drills into, so the
    # record shape it sees is composed the same way.
    try:
        node = materialize_node(node, response.schema_)
    except SchemaResolutionError as exc:
        raise ValueError(
            f"response.records ref {ref!r} resolves to a self-contradictory node "
            f"in response.schema: {exc.reason} "
            "(spec: §API Response Extraction — declared-path resolution)"
        ) from None
    if node.get("type") != "array":
        raise ValueError(
            f"response.records ref {ref!r} resolves to a non-array node in "
            f"response.schema (got type={node.get('type')!r}); spec requires "
            "the schema location to be an array (spec: §Cross-Field Validation)"
        )
    # Gate the RECORD SHAPE too, not just the array node. Without this a
    # contradictory `items` (or a `$defs` entry it references) validated here and
    # then raised out of `resolve_read_record_schema` /
    # `find_record_field_properties` — public helpers the pipeline plugin's
    # prose calls directly — as a bare traceback with no endpoint, no ref and no
    # `$defs` name. A gate that lets the bad document through and lets a
    # downstream script crash on it is not a gate.
    items = node.get("items")
    if isinstance(items, dict):
        try:
            materialized_items = materialize_node(items, response.schema_)
        except SchemaResolutionError as exc:
            raise ValueError(
                f"response.records ref {ref!r} resolves to an array whose record "
                f"shape is self-contradictory: {exc.reason} "
                "(spec: §API Response Extraction — declared-path resolution)"
            ) from None
        # And the shape must declare SOMETHING. Materializing purely to catch an
        # exception inferred validity from the absence of one, so a record shape
        # that composes down to nothing at all passed: `items: {}`, and a `$defs`
        # entry that only `$ref`s itself, which the cycle rule collapses to `{}`
        # without raising. The second is newly reachable because this PR made
        # `$ref` following legal in the record locator.
        #
        # Deliberately NOT "must declare fields": `{"type": "object"}` with no
        # `properties` is the documented unknowable-shape case the corpus uses,
        # and `_json_schema_top_level_fields` returns None for it too. Emptiness
        # is what separates "I am not telling you the fields" from "I am not
        # telling you anything", and only the latter is unauthorable.
        if not materialized_items:
            raise ValueError(
                f"response.records ref {ref!r} resolves to an array whose record "
                "shape declares nothing at all, so nothing downstream can tell "
                "what a record is. Declare at least the record's `type` (a "
                "`$defs` entry that only references itself composes to nothing) "
                "(spec: §Cross-Field Validation)"
            )
    return node


def _validate_cursor_fields_in_record_shape(
    replication: Replication, array_node: dict[str, Any], root: Any
) -> None:
    """Each ``cursor_field`` path must exist under the array's ``items`` subschema.

    Spec: §Cross-Field Validation — "Each replication ``cursor_field`` must
    correspond to a field path in ``response.schema`` under the extracted
    record-shape branch."

    ``root`` is the whole ``response.schema``. The record shape is a SUBTREE of
    it, and `items: {"$ref": "#/$defs/Record"}` is the ordinary way to write one
    — so the walk must keep resolving pointers against the document, not against
    the subtree it starts at. Rooting at the subtree would find no `$defs` and
    report a field that IS declared as undeclared.
    """
    items = array_node.get("items")
    cursor_fields = [_cursor_field_of(cm) for cm in replication.cursor_mappings]

    if items is None or items is True:
        raise ValueError(
            "replication is declared but response.schema array node has no "
            f"`items` subschema; cursor_fields {cursor_fields!r} cannot be "
            "verified — tighten the response schema "
            "(spec: §Cross-Field Validation)"
        )
    if items is False:
        raise ValueError(
            "response.schema array node disallows items (`items: false`) but "
            "replication is declared (spec: §Cross-Field Validation)"
        )
    if isinstance(items, list):
        # Tuple validation: every cursor_field must exist in every position.
        for idx, sub in enumerate(items):
            if not isinstance(sub, dict):
                raise ValueError(
                    f"replication is declared but response.schema array `items[{idx}]` "
                    f"is {type(sub).__name__}, not an object schema; cursor_fields "
                    f"{cursor_fields!r} cannot be verified at that position "
                    "(spec: §Cross-Field Validation)"
                )
            for cf in cursor_fields:
                _check_cursor_field_in_node(cf, sub, where=f"items[{idx}]", root=root)
        return
    if not isinstance(items, dict):
        raise ValueError(
            f"response.schema array node `items` has unexpected type "
            f"{type(items).__name__}; cannot validate cursor fields"
        )

    for cf in cursor_fields:
        _check_cursor_field_in_node(cf, items, where="items", root=root)


def _validate_record_field_path(
    field_path: str, array_node: dict[str, Any], root: Any, *, where: str
) -> None:
    """A dotted RECORD field path must resolve under the records array's ``items``.

    The generic form of the `cursor_field` check, for any site that names a
    field the engine reads off a record rather than off the response body.
    `pagination.keyset.order_by_field` is the other one: the seek order is
    defined over it, so a path the record shape does not declare means pages
    advance from a value the engine cannot read — silently truncating or
    repeating, which is the same wrong-data-on-a-green-run failure
    ADV-ENDP-023 catches on the response-body side, with a different cause.

    Unknowable shapes are reported, not skipped: this is `response.schema`,
    which the contract holds to the strict standard (see
    :func:`_validate_cursor_fields_in_record_shape`).
    """
    items = array_node.get("items")
    if not isinstance(items, dict):
        raise ValueError(
            f"{where} is declared but the response.schema records array has no "
            f"object `items` subschema, so {field_path!r} cannot be verified — "
            "tighten the response schema (spec: §Cross-Field Validation)"
        )
    segments = field_path.split(".")
    try:
        resolve_declared_path(items, segments, root=root)
    except DeclaredPathError as exc:
        walked = ".".join(segments[: exc.index + 1])
        raise ValueError(
            f"{where} {field_path!r} is not declared in the response.schema "
            f"record shape at {walked!r}: {exc.reason} "
            "(spec: §Cross-Field Validation)"
        ) from None


def _cursor_field_of(cm: Any) -> str:
    if isinstance(cm, (SingleCursorMapping, WindowCursorMapping)):
        return cm.cursor_field
    raise TypeError(
        f"unsupported cursor mapping type {type(cm).__name__}; expected "
        "SingleCursorMapping or WindowCursorMapping"
    )


def _check_cursor_field_in_node(
    cursor_field: str, items_node: dict[str, Any], *, where: str, root: Any
) -> None:
    """A ``cursor_field`` must resolve under the record shape by declared-path
    resolution — the same algorithm `response.records` and the pagination /
    metadata refs use, so an author never has to hold two traversal rules.

    The walk STARTS at the record shape but resolves `$ref`s against ``root``
    (the whole ``response.schema``) — see
    :func:`_validate_cursor_fields_in_record_shape`."""
    segments = cursor_field.split(".")
    try:
        resolve_declared_path(items_node, segments, root=root)
    except DeclaredPathError as exc:
        # Name the prefix that WAS walked, up to and including the failing
        # segment: for a dotted path, "which hop broke" is the whole diagnosis.
        walked = ".".join(segments[: exc.index + 1])
        raise ValueError(
            f"replication cursor_field {cursor_field!r} not declared in "
            f"response.schema record-shape branch at {walked!r} (under {where!r}): "
            f"{exc.reason} (spec: §Cross-Field Validation)"
        ) from None
