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
from collections.abc import Iterator
from typing import Annotated, Any, Literal, NamedTuple, Union, get_args

from pydantic import (
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag as UnionTag,
    field_validator,
    model_validator,
)

from analitiq.contracts.arrow_grammar import (
    ARROW_TYPE_PATTERN,
    validate_cross_params,
)
from analitiq.contracts.shared.rules import (
    DeclaredHeaderNames,
    HeaderMergeRules,
    find_duplicates,
    violation,
)
from analitiq.contracts.shared.arrow_shape import (
    ARROW_CONTAINER_SCHEMA_RULES,
    enforce_container_shape,
)
from analitiq.contracts.shared.common import (
    DESCRIPTION_MAX,
    HEADER_NAME_PROPERTY_NAMES,
    HeaderName,
    MediaType,
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
from analitiq.contracts.shared.json_schema import (
    _MISSING,
    JSON_SCHEMA_LIST_OF_SCHEMA_KEYS,
    JSON_SCHEMA_SINGLE_SCHEMA_KEYS,
    JSON_SCHEMA_SUBSCHEMA_KEYS,
    DeclaredPathError,
    SchemaResolutionError,
    _declares_a_type,
    materialize_node,
    resolve_declared_path,
    resolve_local_pointer,
    resolve_schema_ref,
)
from analitiq.contracts.shared.types import (
    StrictFloat,
    StrictInt,
    StrictNonNegativeInt,
    StrictPositiveInt,
)
from analitiq.contracts.value_expression import (
    RESOLUTION_SCOPE_PATTERN,
    RESOLUTION_SCOPES,
    header_name_key,
    _EXPRESSION_KEYS as _RESOLVER_EXPRESSION_KEYS,
    has_known_scope,
    iter_expression_strings,
    template_placeholders,
    validate_expression_shapes,
)


# ---------------------------------------------------------------------------
# Constants & regex
# ---------------------------------------------------------------------------

#: The name half of a `{name}` path placeholder. Stated once: the anchored
#: pattern the model applies and the whole-string pattern the published schema
#: carries are both derived from it, so a JSON Schema consumer and this model
#: refuse the same spellings.
PATH_PLACEHOLDER_NAME_INNER = r"[a-z][a-z0-9_]*"
PATH_PLACEHOLDER_NAME_PATTERN = rf"^{PATH_PLACEHOLDER_NAME_INNER}$"
#: A path is a sequence of characters that are not braces and placeholders that
#: are well formed — which says the same thing about `{` and `}`, so the schema
#: refuses `{}`, `{{name}}`, an unclosed `{` and an unopened `}` alike, as
#: `_RequestBase._validate` does. Stated as an alternation rather than a
#: lookaround so it compiles wherever a JSON Schema consumer reads it.
PATH_BRACES_WELL_FORMED_PATTERN = (
    rf"^(?:[^{{}}]|\{{{PATH_PLACEHOLDER_NAME_INNER}\}})*$"
)
#: The sigil that opens a value-expression template. Stated once: the runtime
#: refusal and the pattern the schema carries are the same characters, so a
#: change to the grammar's opener cannot reach one and miss the other.
TEMPLATE_SIGIL = "${"
PATH_TEMPLATE_SIGIL_PATTERN = re.escape(TEMPLATE_SIGIL)
#: A placeholder name appearing twice, as a backreference — the uniqueness half
#: of the same field, so a consumer reading only the published document refuses
#: the repeat the model refuses. Negated in the schema, since what it matches
#: is the defect.
PATH_PLACEHOLDER_REPEATED_PATTERN = (
    rf"\{{({PATH_PLACEHOLDER_NAME_INNER})\}}[\s\S]*\{{\1\}}"
)
# Record field paths preserve segment spelling and casing. "." is the
# segment separator, so segments exclude it; a response property is
# otherwise a legal JSON key however a provider spells it (`created-at`,
# `@timestamp`), and stream.Filter.field (unconstrained on the database
# branch) already carries that latitude — this is the API branch's
# equivalent. A `filters` map key additionally has to be embeddable inside a
# `${stream.filters.<field>.value}` placeholder (RULE-ENDP-072), whose
# extraction regex stops at the first `}` — a field name carrying one would
# truncate every placeholder built from it, so `}` is excluded too.
RECORD_FIELD_PATH_PATTERN = (
    r"^[^.}]+(\.[^.}]+)*$"
)
METADATA_KEY_PATTERN = r"^[a-z][a-z0-9_]*$"

#: A `filters` map's outer key — the record field a predicate targets.
#: Pydantic renders a constrained dict key as `patternProperties`, which says
#: what a MATCHING key holds and forbids nothing, so a schema-only consumer
#: would accept the names the models reject. `_RECORD_FIELD_PATH_PROPERTY_NAMES`
#: is the `propertyNames` companion that refuses them — the same split
#: `HeaderName`/`HEADER_NAME_PROPERTY_NAMES` uses.
RecordFieldPathKey = Annotated[str, StringConstraints(pattern=RECORD_FIELD_PATH_PATTERN)]
_RECORD_FIELD_PATH_PROPERTY_NAMES: dict[str, Any] = {
    "propertyNames": {"pattern": RECORD_FIELD_PATH_PATTERN}
}

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

# The UNIVERSE of destination write modes — every mode a destination may be
# asked to perform. It keys an API endpoint's `operations.write` map below, and
# `stream.py` dispositions each member for the SQL write path: which of them a
# database destination may select, and what it must then declare. Read that as a
# disposition, not an alias — the two are separate facts and `stream.py` says
# why. Adding a member here fails that table's import-time guard until it is
# dispositioned. The tuple is derived from the Literal so those two cannot drift.
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
# Shared base — x-* extension policy + alias handling
# ---------------------------------------------------------------------------


_RESERVED_ENDPOINT_FIELDS: frozenset[str] = frozenset({
    "connector_id",
    "connector_version",
    "connection_id",
    "schema_hash",
})


class _EndpointModel(StrictModel):
    """Endpoint-module base: `StrictModel` plus alias handling.

    Immutability is not set here — `StrictModel` freezes every contract model,
    and stating it a second time would be a copy to keep in sync.
    """

    model_config = ConfigDict(
        # Default `model_dump()` to wire-format names. Without this, dumps emit
        # Python attribute names (`schema_url`, `schema_`, `location`,
        # `and_`/`or_`/`not_`) and round-trip via `parse_endpoint(model.model_dump())`
        # would fail because none of those are valid spec keys.
        serialize_by_alias=True,
        # `populate_by_name` is deliberately absent, here and on every contract
        # model. It would make an aliased field accept its Python attribute name
        # too, while the published schema admits only the alias under
        # `additionalProperties: false` — so a document written that way passes
        # here and is refused by every consumer of the published schema.
        # `tests/unit/test_wire_name_policy.py` keeps it absent.
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
        pattern=RESOLUTION_SCOPE_PATTERN,
        description=(
            "Must begin with a known resolution scope: "
            + ", ".join(RESOLUTION_SCOPES)
            + " (spec: §Value Expressions)."
        ),
    )


def _validate_template_placeholders(value: str) -> str:
    # Every `${...}` placeholder must begin with a known resolution scope.
    # An unqualified `${name}` is not refused by every runtime that reads
    # this document: one takes the bare name as a top-level context key and
    # falls back to `secrets`, substituting whatever is stored under it,
    # while the other raises on a scope it does not know. Neither names the
    # placeholder to the author of the endpoint.
    # Placeholders are parsed by the shared resolver grammar
    # (`template_placeholders`), so this agrees with the resolver by
    # construction. Model-enforced only — not a published JSON-Schema
    # pattern, so the validator, not `latest.json`, is the complete gate.
    for placeholder in template_placeholders(value):
        if not has_known_scope(placeholder):
            raise ValueError(
                f"template placeholder ${{{placeholder}}} must begin with a "
                "known resolution scope "
                f"({', '.join(RESOLUTION_SCOPES)}); unqualified placeholders "
                "are invalid (spec: §Value Expressions)"
            )
    return value


class TemplateExpression(_EndpointModel):
    """``{"template": "...${scope.path}..."}`` value expression."""

    template: str = Field(..., min_length=1)

    @field_validator("template")
    @classmethod
    def _placeholders_qualified(cls, value: str) -> str:
        return _validate_template_placeholders(value)


# `path_params` / `headers` / `query` recognize `from_param` as an untyped
# procedural convention; `filters` is closed to the members `FilterLanding`
# declares, so it gets a typed member instead.
class FromParamExpression(_EndpointModel):
    """``{"from_param": "<name>"}`` — a `filters` map entry landing on a param,
    verbatim: the filter's own value is what reaches the param.
    """

    from_param: str = Field(
        ...,
        min_length=1,
        description="Name of a param this operation declares under `params`.",
    )


class TemplateFilterLanding(_EndpointModel):
    """``{"param": "<name>", "template": "...${scope.path}..."}`` — a
    `filters` map entry landing on a param via a computed value.

    Unlike `FromParamExpression`, the value the param carries is not the
    filter's own value but this template rendered — for a provider that
    spells ONE comparison inside the value rather than in a distinct param
    (``amount=<>0``, ``q=created>2020-01-01``). `param` is still the
    destination; only what reaches it differs, so it is checked the same way
    `from_param` is (RULE-ENDP-070, RULE-ENDP-002). A provider needing two
    comparisons on one field composed into one value (a bounded range inside
    a single `$filter`) has no landing site here: RULE-ENDP-071 refuses two
    entries sharing a param, and this model carries no way to compose two
    templates into one rendered value.
    """

    param: str = Field(
        ...,
        min_length=1,
        description="Name of a param this operation declares under `params`.",
    )
    template: str = Field(..., min_length=1)

    @field_validator("template")
    @classmethod
    def _placeholders_qualified(cls, value: str) -> str:
        return _validate_template_placeholders(value)


def _filter_landing_discriminator(v: Any) -> str | None:
    """Pick the `filters` landing-site branch by inspecting which key is present."""
    if isinstance(v, dict):
        if "from_param" in v:
            return "from_param"
        if "template" in v:
            return "template"
        return None
    if isinstance(v, FromParamExpression):
        return "from_param"
    if isinstance(v, TemplateFilterLanding):
        return "template"
    return None


#: Where a `filters` map entry's operator lands: an already-declared param
#: carrying the filter's own value, or the same param carrying a computed
#: value rendered from a template — the value-expression `${scope.path}`
#: syntax reused, not a second grammar.
FilterLanding = Annotated[
    Union[
        Annotated[FromParamExpression, UnionTag("from_param")],
        Annotated[TemplateFilterLanding, UnionTag("template")],
    ],
    Discriminator(_filter_landing_discriminator),
]

#: The operator vocabulary a `filters` map entry may key on — the API-only
#: half of `stream.FilterOperator`. A database-only member (`is_null`,
#: `like`, ...) never reaches a request as a bound value, only as a compiled
#: predicate, so it has no landing site here at all.
FilterableOperator = Literal[
    "eq", "neq", "gt", "gte", "lt", "lte",
    "in", "not_in", "contains", "starts_with", "ends_with",
]


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


# The expression forms admissible on a slot that ALSO accepts a bounded number.
# Which slots those are is not listed here — a field is one by carrying this
# alias, and a slot that takes only the bare scalar (a provider fact such as
# `PageSize.max`) carries `StrictPositiveInt` alone and admits no expression.
#
# `LiteralExpression` is excluded, and that exclusion is the whole point. Its
# payload is `Any` and its documented purpose is to opt OUT of expression
# interpretation — so on a numeric slot it is a second spelling of the bare
# scalar that carries none of the bare scalar's bound. `{"literal": 0}` on
# `offset.increment_by` is a pagination step of zero: a loop that never
# advances, accepted by a model whose sibling spelling rejects `0` outright.
# Dropping the branch makes that unrepresentable rather than merely rejected,
# and costs nothing an author needs: a statically-known page size IS the bare
# integer spelling.
#
# Every form that remains resolves at request time, so no bound can be checked
# here and none is applied.
NumericExpression = Annotated[
    Union[
        Annotated[RefExpression, UnionTag("ref")],
        Annotated[TemplateExpression, UnionTag("template")],
        Annotated[FunctionExpression, UnionTag("function")],
    ],
    Discriminator(_expression_discriminator),
]

# The literal key, named once, so the drift guard below and the exclusion above
# cannot disagree about which form was dropped.
_UNBOUNDABLE_EXPRESSION_KEY = "literal"


# ---------------------------------------------------------------------------
# Param contract
# ---------------------------------------------------------------------------


# Declarative mirror of `Param._validate`'s cross-field rule for the published
# schema: a `query` param of `array`/`object` type must declare `style` and
# `explode` (non-null). Keyed on the wire name `in` (the `location` alias).
# `then` pins the required fields to non-null types because they render
# nullable and the runtime demands a value.
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
    minimum: StrictFloat | None = Field(default=None)
    maximum: StrictFloat | None = Field(default=None)
    min_length: StrictNonNegativeInt | None = Field(default=None, alias="minLength")
    max_length: StrictNonNegativeInt | None = Field(default=None, alias="maxLength")
    min_items: StrictNonNegativeInt | None = Field(default=None, alias="minItems")
    max_items: StrictNonNegativeInt | None = Field(default=None, alias="maxItems")
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
    # `StrictPositiveInt` on the scalar branch, and `NumericExpression` (which
    # drops `{literal}`) on the other, are what make the bound reach EVERY
    # spelling of this field. Both rationales are stated where the names are
    # defined — the strict-numeric policy in `analitiq.contracts.shared.types`,
    # the literal exclusion beside `NumericExpression` above.
    default: StrictPositiveInt | NumericExpression | None = Field(  # type: ignore[valid-type]
        default=None,
        description=(
            "Default page size. Either a positive integer (e.g. `50`) or a "
            "value expression the engine resolves per request — typically "
            "`{ref: runtime.batch_size}`, so the run's configured batch size "
            "flows through. A non-positive integer is rejected: it is a "
            "meaningless request rather than one the provider gets to refuse."
        ),
    )
    max: StrictPositiveInt | None = Field(default=None)


class OffsetCursor(_EndpointModel):
    param: str = Field(..., min_length=1, description="Param that receives the offset/start index.")
    initial: Any = Field(..., description="Initial offset/start index value.")
    increment_by: StrictPositiveInt | NumericExpression = Field(  # type: ignore[valid-type]
        ...,
        description=(
            "Per-page offset step. Required, with no default: the two offset "
            "families cannot be told apart from the document, so any default "
            "silently breaks one of them. A bare positive integer is a fixed "
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
    increment_by: StrictPositiveInt | NumericExpression | None = Field(  # type: ignore[valid-type]
        default=None,
        description=(
            "Increment per page (defaults to 1). Either a positive integer or a "
            "value expression the engine resolves per page. A page step of zero "
            "is a loop that never advances, so it is rejected in every spelling."
        ),
    )


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
# The resolver's dispatch keys own this vocabulary; this module's tuple is a
# second copy, pinned to its Expression union just above. Membership must
# also agree with the owner, or the documents' shape walks diverge inside the
# shared walker: it defaults to the resolver's set on a connector document
# and takes this module's widened set on an endpoint one, so a form added to
# one copy alone would be graded wherever that copy feeds the walk and pass
# as structural JSON everywhere else.
if frozenset(_EXPRESSION_KEYS) != frozenset(_RESOLVER_EXPRESSION_KEYS):
    raise AssertionError(
        f"endpoint _EXPRESSION_KEYS {sorted(_EXPRESSION_KEYS)!r} do not match "
        f"the resolver's expression keys {sorted(_RESOLVER_EXPRESSION_KEYS)!r}")
# `NumericExpression` is `Expression` minus exactly the unboundable form,
# checked against the same tag list so the two cannot drift apart silently.
# This is an equality, not a widening: adding an expression form FAILS this
# import until `NumericExpression` is updated to carry it or the exclusion is
# widened deliberately. Nothing joins the numeric slots by omission.
if _union_tags(NumericExpression) != frozenset(_EXPRESSION_KEYS) - {
    _UNBOUNDABLE_EXPRESSION_KEY
}:
    raise AssertionError(
        f"NumericExpression Union members {sorted(_union_tags(NumericExpression))!r} "
        f"must be _EXPRESSION_KEYS minus {_UNBOUNDABLE_EXPRESSION_KEY!r}")
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
# `_RequestBase._validate` and catalogued in the rule registry (RULE-ENDP-001).
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
#: came to be checked on reads and not on writes.
#: `test_the_slot_tuple_still_covers_every_expression_carrying_field` in
#: packages/contract-models/tests/unit/test_response_path_resolution.py pins
#: this tuple against that file's own `FREE_SLOTS` list, so a slot dropped from
#: one and not the other fails the suite; a genuinely new expression-carrying
#: model field is caught by neither list until an author adds it to both.
_REQUEST_EXPRESSION_SLOTS: tuple[str, ...] = (
    "path_params",
    "headers",
    "query",
    "body",
)


class _RequestBase(HeaderMergeRules, DeclaredHeaderNames, _EndpointModel):
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
        # Bounded because the published uniqueness pattern is a backreference
        # over `[\s\S]*`: a consumer applying it to an unbounded string
        # backtracks from every placeholder, so an oversized document costs a
        # validator quadratic time. No real provider path approaches this, and
        # the runtime scan does not backtrack at all.
        max_length=2048,
        description=(
            "Path or relative URL on the selected transport. A `{name}` "
            "placeholder in it is a substitution slot this document names: "
            "spelled in the contract's placeholder-name form (RULE-ENDP-060), "
            "never repeated within one path (RULE-ENDP-059), and bound in "
            "`path_params` (RULE-ENDP-001). A `${...}` template expression is "
            "refused here (RULE-ENDP-061). That the path resolves against the "
            "selected transport's origin rather than carrying a host of its "
            "own (RULE-ENDP-045) is **enforced by nothing today**: an absolute "
            "URL here satisfies every constraint this field declares."
        ),
        json_schema_extra={
            # `allOf` because a schema object carries one `pattern` and one
            # `not`, and this field is graded by several patterns — each the
            # published half of a rule the model applies.
            "allOf": [
                {"not": {"pattern": PATH_TEMPLATE_SIGIL_PATTERN}},
                {"pattern": PATH_BRACES_WELL_FORMED_PATTERN},
                {"not": {"pattern": PATH_PLACEHOLDER_REPEATED_PATTERN}},
            ],
        },
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
    headers: dict[HeaderName, Any] | None = Field(
        default=None,
        description="Endpoint-declared request headers; values may be literals or `{from_param}`/`{ref}`/`{template}`.",
        json_schema_extra=HEADER_NAME_PROPERTY_NAMES,
    )
    headers_remove: list[HeaderName] | None = Field(
        default=None,
        description="Header names to delete from inherited transport defaults (case-insensitive).",
    )
    query: dict[str, Any] | None = Field(
        default=None,
        description="Endpoint-declared query parameters; values may be literals or expressions.",
    )

    @model_validator(mode="after")
    def _validate(self) -> "_RequestBase":
        # Before the placeholder sweep: `${scope.name}` matches the `{name}`
        # shape too, so refusing the template second reports it as a malformed
        # placeholder name and sends the author to respell something that must
        # not be in the path at all.
        if TEMPLATE_SIGIL in self.path:
            raise violation("RULE-ENDP-061", f"path={self.path!r}")
        placeholders = PATH_PLACEHOLDER_RE.findall(self.path)
        # A brace the placeholder pattern did not consume is a brace that
        # reaches the URL as itself: `{}`, `{{name}}`, an unclosed `{`, a
        # stray `}`. Each leaves `placeholders` empty or short, so every later
        # check agrees the path is fine and the braces ship.
        if set("{}") & set(PATH_PLACEHOLDER_RE.sub("", self.path)):
            raise violation(
                "RULE-ENDP-060", f"path={self.path!r}; a brace delimits no placeholder"
            )
        for ph in placeholders:
            # `fullmatch`, not `match`: `$` also matches before a trailing
            # newline, so `{name\n}` would pass a pattern an ECMA reader of
            # the same string rejects.
            if not PATH_PLACEHOLDER_NAME_RE.fullmatch(ph):
                raise violation(
                    "RULE-ENDP-060",
                    f"placeholder {ph!r} does not match "
                    f"{PATH_PLACEHOLDER_NAME_PATTERN!r}",
                )
        repeated = find_duplicates(placeholders)
        if repeated:
            raise violation(
                "RULE-ENDP-059", f"path={self.path!r}; repeated={repeated!r}"
            )
        placeholder_set = set(placeholders)
        # Use explicit `is None`: `path_params={}` is meaningfully different
        # from omitted, and the falsy-check version treats them the same.
        if placeholder_set and self.path_params is None:
            raise violation(
                "RULE-ENDP-001",
                f"path declares {sorted(placeholder_set)!r}; path_params missing",
            )
        if not placeholder_set and self.path_params is not None:
            raise violation("RULE-ENDP-001", "path_params present; path declares none")
        if self.path_params is not None:
            extra = set(self.path_params) - placeholder_set
            missing = placeholder_set - set(self.path_params)
            if extra or missing:
                raise violation(
                    "RULE-ENDP-001",
                    f"extra={sorted(extra)!r}; missing={sorted(missing)!r}",
                )
        return self


class GetReadRequest(_RequestBase):
    """Provider request for a GET-method API read operation. GET declares no body."""

    method: Literal["GET"] = Field(..., description="Read HTTP method.")


class _BodyBearingRequest(_RequestBase):
    """A request branch that declares a body, and the media type describing it.

    Both sit here rather than on `_RequestBase` because a branch with no body
    has nothing for a media type to describe: the GET read is that branch, and
    one construction keeps the pair off it. A body is still optional on the
    branches that may have one — a POST that sends none is an ordinary
    request, and a media type declared beside no body is a statement about
    what would be sent, which the engine settles rather than this document.
    """

    body: Any | None = Field(
        default=None,
        description="Request body. May mix literals with `{from_param}`.",
    )
    content_type: MediaType | None = Field(
        default=None,
        description=(
            "Media type of `body`, sent as the request's `Content-Type` and "
            "selecting how the body is encoded — the field to declare when a "
            "provider takes a form-encoded or otherwise non-JSON body. "
            "Omitted, the engine sends JSON. The header map is not a second "
            "way to say this (RULE-HTTP-003)."
        ),
    )


class PostReadRequest(_BodyBearingRequest):
    """Provider request for a POST-method API read operation (query-in-body reads)."""

    method: Literal["POST"] = Field(..., description="Read HTTP method.")


# `method`-discriminated read request: only the POST branch declares `body`, so
# the published JSON Schema structurally forbids a body on a GET read (the rule
# formerly enforced only by a `@model_validator`). Both branches share the
# `_RequestBase` fields.
ReadRequest = Annotated[
    GetReadRequest | PostReadRequest,
    Field(discriminator="method"),
]


class WriteRequest(_BodyBearingRequest):
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
        description="Request body. May mix literals with `{from_param}` (and `{from_input}` for writes).",
    )


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
#: structural walkers, which is the single harm RULE-ENDP-026 exists to close.
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
    and must land on a schema; and the schema declares its dialect at its root
    or nowhere.

    RULE-ENDP-026. `$ref` is authorable — `JsonSchemaPropertyNode` enumerates
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
    FOLLOW a `$ref` (see
    :func:`analitiq.contracts.shared.json_schema._property_contributors`): by
    the time a path is resolved, every ref it can meet is local, real, and lands
    on a node both walkers visited — so following one cannot land the resolver
    on a type nothing verified.

    RULE-ENDP-064 rides this walk too, because it reaches the same positions:
    `$schema` names the dialect a schema RESOURCE is written in, an embedded
    schema is one resource, and so the keyword is authorable at this document's
    root and refused at every node the walk reaches below it. Its verdict turns
    on WHERE the keyword sits rather than only on its presence, which is why the
    walk carries a root marker.

    Walks the same structural positions as
    :func:`_validate_arrow_type_in_json_schema` — the shared
    ``_JSON_SCHEMA_*_KEYS`` sets, so the two cannot disagree about what counts
    as a schema position. Never follows a `$ref` itself: the walk is over the
    document's own tree, and every local target is already part of it.
    """
    # `root` is threaded down so a ref deep in the tree still resolves against
    # the WHOLE embedded schema — `$defs` lives at the top, and resolving
    # against the current subtree would call every legitimate ref dangling.
    # Its absence also marks the entry call: every recursive one passes on the
    # root it was given, so no root in hand means this node IS the root — the
    # one position a dialect declaration belongs in.
    is_root = root is None
    root = schema if is_root else root
    # `true` / `false` are legal whole-schema short-forms carrying no `$ref`.
    if isinstance(schema, bool) or not isinstance(schema, dict):
        return

    if not is_root and "$schema" in schema:
        errors.append(
            f"{path}.$schema is not authorable below the root of an embedded "
            "response/input schema: `$schema` declares the dialect a schema "
            "resource is written in, and this document is one resource. A "
            "reader grading a subschema honours the declaration that subschema "
            "carries, so a node naming another draft takes its whole subtree "
            "out of the dialect the rest of the document is read in, and the "
            "keywords underneath keep their spelling while losing their "
            "meaning. Declare the dialect on the schema itself, or omit it "
            "(spec: §API Response Extraction — embedded schema references)"
        )

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

    # Named `_validate` (not `_validate_arrow_types`) because it enforces every
    # rule one document settles alone over an embedded schema, whichever walk
    # reaches it, and the rule registry needs ONE enforcer name that exists on
    # this model and on `ResponseExtraction` for the rules they share.
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

    max_records: Annotated[StrictInt, Field(ge=2)] = Field(
        ...,
        description="Provider's maximum records per request. Must be ≥ 2.",
    )


class Idempotency(DeclaredHeaderNames, _EndpointModel):
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
    name: Annotated[str, StringConstraints(pattern=NO_EDGE_WHITESPACE_PATTERN)] = Field(
        ...,
        description=(
            "Header name or top-level body field name that carries the key. "
            "Not constrained to a header name's token shape, because `in: "
            "body` makes it a JSON field name, which admits more; what both "
            "share is that the space around a name is not part of it."
        ),
    )

    def declared_header_names(self) -> list[tuple[str, str]]:
        """`name` is a header name, and only when `in` says it is."""
        if self.location != "header":
            return []
        return [(self.name, "idempotency.name")]


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
                },
                {"properties": {"filters": _RECORD_FIELD_PATH_PROPERTY_NAMES}},
            ]
        }
    )

    request: ReadRequest = Field(...)
    params: dict[str, Param] = Field(default_factory=dict)
    filters: dict[RecordFieldPathKey, dict[FilterableOperator, FilterLanding]] | None = Field(
        default=None,
        description=(
            "How a stream filter's operator reaches this operation's request, "
            "keyed by the record field a filter targets and then by operator. "
            "Each entry names a param declared under `params` as its landing "
            "site: `from_param` carries the filter's own value there "
            "verbatim; `param` plus `template` carries a value rendered "
            "through the value-expression grammar instead, for a provider "
            "that spells the comparison inside the value rather than in a "
            "distinct param. RULE-STRM-026 is what a stream-side filter on "
            "this operation must agree with."
        ),
    )
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

        named: frozenset[str] = frozenset()
        controlled: frozenset[str] = frozenset()
        if self.pagination is not None:
            block = _validate_pagination_wiring(self.pagination, self.params)
            named |= block.named
            controlled |= block.filled

        if self.replication is not None:
            block = _validate_replication_wiring(self.replication, self.params)
            named |= block.named
            controlled |= block.filled
        _validate_required_params_have_a_source(
            self.params,
            allow_from_input=False,
            controlled=controlled,
            named=named,
            filters_landed=_filters_landed_params(self.filters),
        )

        # response.records → response.schema traversal raises directly.
        # When replication is declared, the same traversal feeds cursor-field
        # validation (avoiding a second walk of the same JSON Schema).
        records_array_node = _validate_records_in_response_schema(self.response)
        if self.replication is not None:
            _validate_cursor_fields_in_record_shape(
                self.replication, records_array_node, self.response.schema_
            )

        if self.filters:
            _validate_filters_wiring(self.filters, self.params)
            # A `filters` key is a RECORD field, not a `response.body` ref —
            # `RECORD_FIELD_PATH_PATTERN` on the field type is a shape check,
            # not an existence one, the same gap `keyset.order_by_field` closes
            # below. Without this, `filters: {"updatedAt": …}` against a record
            # declaring `updated_at` validates clean and a stream filtering on
            # `updated_at` lands nowhere — the wrong-rows failure this map
            # exists to close, relocated from the operator to the field name.
            for field in self.filters:
                _validate_record_field_path(
                    field, records_array_node, self.response.schema_,
                    where="filters",
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
            self.response, self.pagination, self.request, self.params, self.filters
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
    `$defs` + `$ref` shape RULE-ENDP-026's rejection message tells authors to
    write — silently disabling both this check and RULE-ENDP-024's membership
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
        # A write declares no pagination or replication block, so nothing can
        # back a `controlled_by` marker and the `default` is the whole set.
        _validate_required_params_have_a_source(
            self.params, allow_from_input=True,
            controlled=frozenset(), named=frozenset(),
        )

        # RULE-ENDP-025. Held here rather than in `_validate_param_wiring`
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
                declared = {
                    header_name_key(h) for h in (self.request.headers or {})
                }
                if header_name_key(self.idempotency.name) in declared:
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

        # RULE-ENDP-024: the same membership rule for path_params, reported
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
    ordinal_position: StrictPositiveInt | None = Field(default=None)
    # `properties` here is a field-spec map (recursive ColumnFieldSpec),
    # distinct from JSON Schema `properties` blocks used by API endpoints.
    # Both are enforced by `enforce_container_shape` via the validator below.
    properties: dict[str, ColumnFieldSpec] | None = Field(default=None)
    items: ColumnFieldSpec | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_container_shape(self) -> "Column":
        enforce_container_shape(self.arrow_type, self.properties, self.items)
        return self


class DatabaseEndpointDoc(_EndpointBase):
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

    @model_validator(mode="after")
    def _column_names_unique(self) -> "DatabaseEndpointDoc":
        """RULE-DBEP-001: the name every downstream lookup addresses is one column."""
        dups = find_duplicates(self.columns, key=lambda c: c.name)
        if dups:
            raise violation("RULE-DBEP-001", f"duplicates={dups!r}")
        return self

    @model_validator(mode="after")
    def _ordinal_positions_unique(self) -> "DatabaseEndpointDoc":
        """RULE-DBEP-002: declared ordinals canonicalise column order unambiguously.

        Schemaless sources expose no ordinal, so a column that omits one is not
        competing for a position and is left out of the comparison.
        """
        declared = [c.ordinal_position for c in self.columns if c.ordinal_position is not None]
        dups = find_duplicates(declared)
        if dups:
            raise violation("RULE-DBEP-002", f"duplicates={dups!r}")
        return self

    @model_validator(mode="after")
    def _primary_keys_name_columns(self) -> "DatabaseEndpointDoc":
        """RULE-DBEP-003: every conflict key an upsert draws is a column here."""
        if not self.primary_keys:
            return self
        declared = {c.name for c in self.columns}
        extra = sorted(set(self.primary_keys) - declared)
        if extra:
            raise violation("RULE-DBEP-003", f"not declared: {extra!r}")
        return self


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
# percent-encodes each substituted path segment (RULE-ENDP-027). This is a
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

    The walk itself is the shared grammar's — `validate_expression_shapes`,
    handed this document's key set and extension policy, each stated above.
    """
    validate_expression_shapes(
        value, where,
        expression_keys=_ALL_EXPRESSION_KEYS,
        function_fields=_FUNCTION_EXPRESSION_FIELDS,
        extension_siblings=True,
    )


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
        if not has_known_scope(token):
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
        # RULE-ENDP-027. Percent-encoding a path segment is the ENGINE's job, and
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
            # RULE-ENDP-028, on WRITES only. A write param has exactly one
            # source: its own `default`. A `filters` map entry makes a param
            # a stream's landing site and `controlled_by` hands it to
            # pagination/replication — both read-side, neither reachable from a
            # write. So a write path param with no `default` provably cannot
            # resolve, and the placeholder it fills can never be substituted.
            # Such a document is contract-valid and dead at the engine
            # handshake. It is refused here, naming the binding that replaces
            # it. Reads keep the old latitude: a read path param can be
            # supplied by a stream filter.
            if allow_from_input and param.default is None:
                raise violation(
                    "RULE-ENDP-028",
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


def _declares_a_value(value: Any) -> bool:
    """Whether an authored slot carries something that can resolve to a value.

    A source is only a source if what the document put there can arrive. Three
    shapes cannot, and all three are non-`None` so a bare null check reads them
    as present: an authored `null`; `{"literal": null}`, which the resolver
    unwraps back to nothing; and an object that is not a value expression at
    all — `{}`, or a binding form like `{"from_param": ...}`, which the
    resolver does not implement and returns nothing for.

    Used for a param's `default` and for a pagination strategy's starting
    value, which are the same question asked in two places.
    """
    if value is None:
        return False
    if not isinstance(value, dict):
        return True
    keys = set(_RESOLVER_EXPRESSION_KEYS) & set(value)
    if len(keys) != 1:
        return False
    if "literal" in keys:
        return value["literal"] is not None
    return True


class _BlockParams(NamedTuple):
    """What a pagination or replication block names, and what it fills.

    The two differ, and RULE-ENDP-066 needs both: `filled` is what counts as a
    source, and `named` is what lets the finding tell a param no block mentions
    from one a block mentions and gives nothing to. Those are different author
    mistakes with different fixes.
    """

    named: frozenset[str]
    filled: frozenset[str]


def _validate_pagination_wiring(
    pagination: Any, params: dict[str, Param]
) -> _BlockParams:
    """Validate pagination param references and ``controlled_by`` markers.

    Naming a param says which slot the strategy drives; it does not say the
    document put a value there for the first request, and a param the block
    never gives one to is as empty as a param no block names at all — so
    counting a mention as a source would let an author satisfy RULE-ENDP-066 by
    adding `limit: {"param": <name>}` and change nothing about the request.

    Which params a strategy fills is a property of its own shape, and the test
    is `_declares_a_value` rather than the key's presence everywhere: an
    offset, a page cursor and a keyset each fill their param when `initial`
    carries something that can arrive (the first two require the key and still
    admit a nothing under it), a `limit` fills its param when `default` does,
    an opaque `Cursor` has no field for one because there is no cursor before
    the first page, and a link strategy names no param but the `limit`.
    """
    referenced: list[str] = []
    filled: list[str] = []
    if isinstance(pagination, OffsetPagination):
        referenced.append(pagination.offset.param)
        if _declares_a_value(pagination.offset.initial):
            filled.append(pagination.offset.param)
    elif isinstance(pagination, PagePagination):
        referenced.append(pagination.page.param)
        if _declares_a_value(pagination.page.initial):
            filled.append(pagination.page.param)
    elif isinstance(pagination, CursorPagination):
        referenced.append(pagination.cursor.param)
    elif isinstance(pagination, KeysetPagination):
        referenced.append(pagination.keyset.param)
        if _declares_a_value(pagination.keyset.initial):
            filled.append(pagination.keyset.param)
    # LinkPagination declares no cursor param (spec: §Pagination Strategies —
    # link replaces the entire URL, no params traverse to follow-up requests).
    # Every strategy carries an optional `limit`.
    if pagination.limit and pagination.limit.param:
        referenced.append(pagination.limit.param)
        if _declares_a_value(pagination.limit.default):
            filled.append(pagination.limit.param)

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
    return _BlockParams(frozenset(referenced), frozenset(filled))


def _validate_response_body_paths(
    response: ResponseExtraction,
    pagination: Any,
    request: Any = None,
    params: dict[str, "Param"] | None = None,
    filters: dict[str, dict[str, Any]] | None = None,
) -> None:
    """RULE-ENDP-023: every `response.body[.<path>]` a read operation reads
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
    # `filters` is request-shaping too — its `template` landing renders
    # before any request goes out, so a `${response...}` ref there is the
    # identical never-has-a-value defect the request slots above are swept
    # for, just reached through a newer field. Only `template` is scanned:
    # `TemplateFilterLanding` also carries `param`, and dumping both together
    # reads as an `Expression` dict with an unexpected sibling to the shared
    # walker's own shape check (RULE-ENDP-022), which this compound landing
    # is not subject to.
    for field, landings in (filters or {}).items():
        for operator, landing in landings.items():
            if isinstance(landing, TemplateFilterLanding):
                sites.append(_ExpressionSite(
                    where=f"filters[{field!r}][{operator!r}].template",
                    payload={"template": landing.template},
                    operation=_OperationKind.READ,
                    can_read_response=False,
                ))

    _sweep_expression_sites(
        sites, response.schema_, frozenset(response.metadata or {})
    )


def _validate_replication_wiring(
    replication: Replication, params: dict[str, Param]
) -> _BlockParams:
    """Validate replication param references and ``controlled_by`` markers.

    Splits the same two sets `_validate_pagination_wiring` does, on a different
    test — replication declares no starting value anywhere, so what decides
    whether it fills a param is `supported_methods`. A block that never runs
    incrementally names its cursor params and leaves them with nothing, which
    is a fact the document settles; a block that does leaves them empty only
    until a run has stored a cursor, which is run state and belongs to the
    residue RULE-ENDP-067 covers rather than to a refusal here.
    """
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
    named = frozenset(referenced)
    if "incremental" not in replication.supported_methods:
        return _BlockParams(named, frozenset())
    return _BlockParams(named, named)


def _filters_landed_params(filters: dict[str, dict[str, Any]] | None) -> frozenset[str]:
    """Param names any `filters` entry names as a landing site.

    Reads names only — whether a named param exists or collides with another
    entry is `_validate_filters_wiring`'s separate, later concern. Called from
    `_validate_required_params_have_a_source`, which runs before that wiring
    check, so a required param naming itself as a landing site already counts
    as sourced even though the map as a whole has not been graded yet.
    """
    if not filters:
        return frozenset()
    names: set[str] = set()
    for landings in filters.values():
        for landing in landings.values():
            names.add(
                landing.from_param
                if isinstance(landing, FromParamExpression)
                else landing.param
            )
    return frozenset(names)


def _validate_required_params_have_a_source(
    params: dict[str, Param],
    *,
    allow_from_input: bool,
    controlled: frozenset[str],
    named: frozenset[str],
    filters_landed: frozenset[str] = frozenset(),
) -> None:
    """RULE-ENDP-066 — a required param the document can never fill.

    Runs from `_wiring` after the binding checks, whose diagnostics name the
    real defect when a param is unbound or bound twice, and after the
    pagination and replication wiring, which is what produces `controlled`;
    before the response-side resolution checks, which grade a different half of
    the document. Not from `_validate_param_wiring`, which sees neither.

    The sources are the ones RULE-ENDP-066 names. `filters_landed` — any param
    a `filters` entry names as its landing site, from `_filters_landed_params`
    — and the blocks behind `controlled` are read-side, so on a write the
    `default` is the whole set. RULE-ENDP-028 makes that argument about a
    write path_param whether or not it is required, so neither rule contains
    the other.

    `controlled` is read instead of `param.controlled_by` because the marker is
    self-declared and only the block-to-param direction is checked anywhere: a
    marker no block backs, and a block that names a param and fills nothing,
    both leave the param with nothing behind it. `named` separates those two,
    so the finding can name which one the author is looking at — the fixes are
    different, and neither is the one a param with no marker at all needs.

    Whether a `default` is a source is `_declares_a_value`, which is also what
    grades a strategy's starting value: an authored `null`, a
    `{"literal": null}` the resolver unwraps back to nothing, and an object
    that is no value expression at all each leave the slot as empty as no
    `default` at all. An authored `null` and an absent key stay distinguishable
    in `model_fields_set`; reading them alike is the deliberate choice, since
    keying on the set would let the rule be walked around by typing four
    characters.

    What this proves is that a source is DECLARED, never that it resolves. A
    `default` reffing a connection parameter the connection leaves unset, and a
    param `filters` names but no stream filters, both pass here; what the
    request path then does with the empty value depends on the slot, and
    RULE-ENDP-067 is the obligation over that remainder.
    """
    findings: list[tuple[str, str]] = []
    for name, param in params.items():
        if not param.required or _declares_a_value(param.default):
            continue
        if not allow_from_input and (name in filters_landed or name in controlled):
            continue
        if allow_from_input and param.location == "body":
            # The value a write puts in the body comes from the record, and the
            # param is what stands between them: a body slot takes
            # `{"from_input": "record.<field>"}` with no param declared at all.
            # `path` is not offered the same way out because it never reaches
            # here — RULE-ENDP-028 refuses a write path_param with no `default`
            # first, and names the record binding itself.
            ways_out = (
                "give it a `default`, or delete the param and bind the slot to "
                'the record with `{"from_input": "record.<field>"}`'
            )
        elif allow_from_input:
            ways_out = "give it a `default`"
        elif param.controlled_by is None:
            ways_out = (
                "give it a `default`, name it as a `filters` entry's landing "
                "site, or have a pagination block give it a starting value or "
                "a replication block support `incremental`"
            )
        else:
            # The author already reached for a block, so the fix is on that
            # side. `filters` is not offered here: a param naming
            # `controlled_by` is refused as a `filters` landing site
            # (`_validate_filters_wiring`), so taking that advice would cost a
            # round trip to a different refusal.
            if name not in named:
                cause = "no block names it"
            elif param.controlled_by == "replication":
                cause = (
                    "the replication block naming it does not support "
                    "`incremental`, so no run leaves it a cursor"
                )
            else:
                cause = (
                    "the pagination block naming it gives it no starting value"
                )
            ways_out = (
                f"it declares `controlled_by: {param.controlled_by}` and "
                f"{cause} — fix that, or give it a `default`"
            )
        findings.append((name, ways_out))

    if findings:
        raise violation(
            "RULE-ENDP-066",
            "required and given no source, so the value each binds resolves "
            "to nothing on every run — "
            + "; ".join(f"params[{n!r}]: {w}" for n, w in findings)
            + ". Or declare the param not required, where the operation is "
            "correct without the value",
        )


def _validate_filters_wiring(
    filters: dict[str, dict[str, Any]], params: dict[str, Param]
) -> None:
    """Every `filters` entry lands on a declared, non-`controlled_by` param,
    no two entries anywhere in the map land on the same one, and a
    `template` landing interpolates the entry's own filter value.

    Both landing forms name a destination param (`from_param` / `param`), so
    one param existence/`controlled_by` check and one uniqueness check cover
    both — uniqueness spans the whole map, not one field: two DIFFERENT
    fields landing on the same param is the identical ambiguity as two
    operators on one field doing so, since the param still carries only one
    value and a run can honour only one of the two predicates.

    `filters`' landing is a routing declaration, not a request binding —
    whether the named param is itself bound into a request location at all is
    `_validate_param_binding_uniqueness`'s separate, already-covered concern.

    RULE-ENDP-072 is checked here, not on `TemplateFilterLanding` itself: a
    single-model `field_validator` sees the template string alone and cannot
    know which `filters` key/operator pair it is nested under, so it cannot
    tell a constant or a wrong-field reference from the entry's own value.
    """
    seen: dict[str, tuple[str, str]] = {}
    for field, landings in filters.items():
        for operator, landing in landings.items():
            name = (
                landing.from_param
                if isinstance(landing, FromParamExpression)
                else landing.param
            )
            param = params.get(name)
            if param is None:
                raise violation(
                    "RULE-ENDP-070",
                    f"filters.{field}.{operator} names undeclared param {name!r}",
                )
            if param.controlled_by is not None:
                raise violation(
                    "RULE-ENDP-002",
                    f"filters.{field}.{operator} names param {name!r}, which "
                    f"declares controlled_by={param.controlled_by!r}",
                )
            if name in seen:
                other_field, other_operator = seen[name]
                raise violation(
                    "RULE-ENDP-071",
                    f"filters.{other_field}.{other_operator} and "
                    f"filters.{field}.{operator} both land on param {name!r}",
                )
            seen[name] = (field, operator)
            if isinstance(landing, TemplateFilterLanding):
                current_value = f"stream.filters.{field}.value"
                placeholders = template_placeholders(landing.template)
                if current_value not in placeholders:
                    raise violation(
                        "RULE-ENDP-072",
                        f"filters.{field}.{operator}.template {landing.template!r} "
                        f"does not interpolate ${{{current_value}}} — every value "
                        "for this field/operator renders the identical request",
                    )
                extra = [
                    p for p in placeholders
                    if p != current_value and p.startswith("stream.filters.")
                ]
                if extra:
                    raise violation(
                        "RULE-ENDP-072",
                        f"filters.{field}.{operator}.template {landing.template!r} "
                        f"also interpolates {extra!r} — a filters template's only "
                        "dependency is the field/operator entry it is declared on",
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
    were wired per CALL SITE rather than per slot, and every hole found after
    the first was the same shape: a site that was not on somebody's list.
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
                # RULE-ENDP-023 failure verbatim, one segment to the right of
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
            if not _declares_a_type(materialized, response_schema):
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
    `RESOLUTION_SCOPE_PATTERN`, but the `Any`-typed paging slots
    (`keyset.initial`, `offset.initial`, `page.initial`, every `Predicate`
    operand) are covered by nothing, and those are the most load-bearing paging
    sites in the contract.
    """
    if has_known_scope(token):
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

    The hole this closes is the one RULE-ENDP-023 exists to close, one segment
    to the left. `_response_body_segments` returns ``None`` for anything that is
    not `response.body[.…]`, and the caller skips it — on the stated grounds
    that every OTHER `response.*` scope is reserved and engine-owned. Nothing
    checked that the token actually named one of them, so
    `response.bodyy.next_cursor` was not "a reserved scope this rule leaves
    alone", it was a typo that resolved to nothing at run time. Paging stopped
    after page one and the sync reported success — the identical silent
    truncation RULE-ENDP-023 exists to catch, reachable by misspelling `body`
    instead of the field after it.

    `has_known_scope` cannot catch it: it inspects only the LEADING token, and
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
    # the exact shape RULE-ENDP-026's rejection message tells authors to write.
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
    # A records array is a page of rows — every row the same shape — so a
    # positional/tuple shape (the legacy `items: [...]`, or its Draft 2020-12
    # replacement `prefixItems`) is refused here, unconditionally: this gate
    # runs for every read with a `response.records` ref, whether or not
    # replication/filters/keyset ever calls `_require_record_shape_items` to
    # ask about a specific field. Without this an endpoint declaring no such
    # feature could carry a positional records array straight past every
    # check, and `resolve_read_record_schema` (below) only unwraps a
    # dictionary-valued `items`, so it could not even see the record's
    # fields for downstream mapping/type derivation.
    if isinstance(node.get("items"), list) or node.get("prefixItems") is not None:
        raise ValueError(
            f"response.records ref {ref!r} resolves to an array declaring a "
            "positional/tuple shape (`items: [...]` or `prefixItems`) — a "
            "records array is a page of rows and every row must be the same "
            "shape; declare one `items` object schema "
            "(spec: §Cross-Field Validation)"
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
        # without raising. The second is reachable at all only because `$ref`
        # following is legal in the record locator.
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


def _require_record_shape_items(array_node: dict[str, Any], *, subject: str) -> dict[str, Any]:
    """Return the records array's `items` object subschema, or raise.

    `items` must be a single object subschema. A records array is a page
    of API rows — every row the same shape — so there is no positional
    ("row 0 looks different from row 1") case to support. Both the
    pre-2020-12 tuple form (`items: [...]`) and Draft 2020-12's replacement
    (`prefixItems`) are refused outright: no accommodation for an authoring
    style the contract has no real use for.
    """
    if isinstance(array_node.get("items"), list) or array_node.get("prefixItems") is not None:
        raise ValueError(
            f"{subject} is declared but the response.schema records array "
            "declares a positional/tuple shape (`items: [...]` or "
            "`prefixItems`) — a records array is a page of rows and every "
            "row must be the same shape; declare one `items` object schema "
            "(spec: §Cross-Field Validation)"
        )
    items = array_node.get("items")
    if items is None or items is True:
        raise ValueError(
            f"{subject} is declared but the response.schema records array has "
            "no `items` subschema, so it cannot be verified — tighten the "
            "response schema (spec: §Cross-Field Validation)"
        )
    if items is False:
        raise ValueError(
            f"response.schema records array disallows items (`items: false`) "
            f"but {subject} is declared (spec: §Cross-Field Validation)"
        )
    if not isinstance(items, dict):
        raise ValueError(
            f"{subject} is declared but the response.schema records array "
            f"`items` is {type(items).__name__}, not a single object schema "
            "(spec: §Cross-Field Validation)"
        )
    return items


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
    items = _require_record_shape_items(array_node, subject="replication")
    for cm in replication.cursor_mappings:
        _check_cursor_field_in_node(_cursor_field_of(cm), items, where="items", root=root)


def _validate_record_field_path(
    field_path: str, array_node: dict[str, Any], root: Any, *, where: str
) -> None:
    """A dotted RECORD field path must resolve under the records array's ``items``.

    The generic form of the `cursor_field` check, reused at every site that
    names a field the engine reads off a record rather than off the response
    body — `pagination.keyset.order_by_field` and each `filters` map key
    among them: a path the record shape does not declare means the field it
    names is read from a value the engine cannot resolve — silently
    truncating or repeating pages, or silently filtering nothing — which is
    the same wrong-data-on-a-green-run failure RULE-ENDP-023 catches on the
    response-body side, with a different cause.

    Unknowable shapes are reported, not skipped: this is `response.schema`,
    which the contract holds to the strict standard (see
    :func:`_validate_cursor_fields_in_record_shape`). The resolved node must
    also declare a type — the same requirement `_validate_response_body_paths`
    holds response-body refs to — since a comparison built over an untyped
    node (a keyset ordering, a `filters` value match) has nothing to tell it
    what a valid value looks like.
    """
    items = _require_record_shape_items(array_node, subject=where)
    segments = field_path.split(".")
    try:
        node = resolve_declared_path(items, segments, root=root)
    except DeclaredPathError as exc:
        walked = ".".join(segments[: exc.index + 1])
        raise ValueError(
            f"{where} {field_path!r} is not declared in the response.schema "
            f"record shape at {walked!r}: {exc.reason} "
            "(spec: §Cross-Field Validation)"
        ) from None
    try:
        materialized = materialize_node(node, root)
    except SchemaResolutionError as exc:
        raise ValueError(
            f"{where} {field_path!r} resolves in the response.schema record "
            f"shape to a self-contradictory node: {exc.reason} "
            "(spec: §Cross-Field Validation)"
        ) from None
    if not _declares_a_type(materialized, root):
        raise ValueError(
            f"{where} {field_path!r} resolves in the response.schema record "
            "shape to a node that declares no `type` (and no "
            "`native_type`/`arrow_type` pair). Declare the type of the value "
            "read there, or nothing can tell what a valid comparison looks "
            "like (spec: §Cross-Field Validation)"
        )


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
    :func:`_validate_cursor_fields_in_record_shape`. The resolved node must
    also declare a type, the same requirement `_validate_record_field_path`
    holds `filters`/`order_by_field` to: an incremental comparison built over
    an untyped node has nothing to tell it what a valid watermark looks like.
    """
    segments = cursor_field.split(".")
    try:
        node = resolve_declared_path(items_node, segments, root=root)
    except DeclaredPathError as exc:
        # Name the prefix that WAS walked, up to and including the failing
        # segment: for a dotted path, "which hop broke" is the whole diagnosis.
        walked = ".".join(segments[: exc.index + 1])
        raise ValueError(
            f"replication cursor_field {cursor_field!r} not declared in "
            f"response.schema record-shape branch at {walked!r} (under {where!r}): "
            f"{exc.reason} (spec: §Cross-Field Validation)"
        ) from None
    try:
        materialized = materialize_node(node, root)
    except SchemaResolutionError as exc:
        raise ValueError(
            f"replication cursor_field {cursor_field!r} resolves in the "
            f"response.schema record-shape branch (under {where!r}) to a "
            f"self-contradictory node: {exc.reason} (spec: §Cross-Field Validation)"
        ) from None
    if not _declares_a_type(materialized, root):
        raise ValueError(
            f"replication cursor_field {cursor_field!r} resolves in the "
            f"response.schema record-shape branch (under {where!r}) to a node "
            "that declares no `type` (and no `native_type`/`arrow_type` pair). "
            "Declare the type of the watermark value read there, or nothing "
            "can tell what a valid comparison looks like "
            "(spec: §Cross-Field Validation)"
        )
