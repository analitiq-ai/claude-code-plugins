"""Census entries for ``analitiq.contracts.endpoints``."""
from __future__ import annotations

from analitiq.contracts.shared.advisory_prose import (
    ENGINE_CONDUCT,
    ENGINE_OWNED_DEFAULTING,
    ProseObligation,
    UNKNOWABLE_SKIP,
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    # === api-endpoint: params, request binding ===============================
    ProseObligation(
        model="Param", field="required", waiver=ENGINE_CONDUCT,
        prose_hash="4ad7a10fde50",
    ),
    ProseObligation(
        model="endpoints.RefExpression", field="ref",
        prose_hash="44646d257e20",
        structural=(
            "Field(pattern=_RESOLUTION_SCOPE_PATTERN), built from the "
            "RESOLUTION_SCOPES tuple the description itself enumerates"
        ),
    ),
    ProseObligation(
        model="_RequestBase", field="transport_ref",
        prose_hash="b35c466c3e3d",
        waiver=(
            "the NAME half is cross-document — enforced by analitiq-validator's "
            "endpoint-transport-ref check, which a single-document model cannot "
            "run; the ORIGIN half is enforced by nothing today, as the prose "
            "itself states; the defaulting to default_transport is engine-owned"
        ),
    ),
    ProseObligation(
        model="_RequestBase", field="path_params",
        prose_hash="18b30a3b6dcb",
        rule_ids=("ADV-ENDP-024", "ADV-ENDP-027"),
    ),
    ProseObligation(
        model="WriteRequest", field="path_params",
        prose_hash="48153a4015ce",
        rule_ids=("ADV-ENDP-024", "ADV-ENDP-025", "ADV-ENDP-027", "ADV-ENDP-028"),
        waiver=UNKNOWABLE_SKIP,
    ),
    # === api-endpoint: pagination ============================================
    ProseObligation(
        model="ReadOperation", field="pagination", rule_ids=("ADV-ENDP-023",),
        prose_hash="b6cbe51e7ce7",
    ),
    ProseObligation(
        model="Cursor", field="next_cursor",
        prose_hash="620b8e0f2840",
        structural=(
            "typed as the `Expression` discriminated union; a bare string or "
            "a response_path shape selects no branch"
        ),
    ),
    ProseObligation(
        model="Link", field="next_url",
        prose_hash="09c07d9da104",
        structural=(
            "typed as the `Expression` discriminated union; a bare string or "
            "a response_path shape selects no branch"
        ),
    ),
    ProseObligation(
        model="LinkPagination", field="limit",
        prose_hash="1ea90bda27f5",
        rule_ids=("ADV-ENDP-009", "ADV-ENDP-010"),
    ),
    ProseObligation(
        model="OffsetCursor", field="increment_by",
        prose_hash="faf4ca370494",
        structural="required, with no default (the leading ... sentinel)",
        waiver=(
            "which step value is correct (records-returned vs requested-window "
            "offsets) depends on provider semantics the document cannot state — "
            "authoring judgment"
        ),
    ),
    ProseObligation(
        model="PageCursor", field="increment_by", waiver=ENGINE_OWNED_DEFAULTING,
        prose_hash="d17d2ce2c053",
    ),
    ProseObligation(
        model="Keyset", field="order_by_field",
        prose_hash="88fd131e2aea",
        structural="Field(pattern=RECORD_FIELD_PATH_PATTERN)",
    ),
    # === api-endpoint: response ==============================================
    ProseObligation(
        model="ResponseExtraction", field="records",
        prose_hash="edb6f6f3bd17",
        rule_ids=("ADV-ENDP-012",),
        structural=(
            "typed RefExpression (never template/literal/function); the "
            "response.body anchor is enforced by ResponseExtraction._validate "
            "and mirrored as a pattern in the published schema"
        ),
    ),
    ProseObligation(
        model="ResponseExtraction", field="schema_",
        prose_hash="4fdb3a003c8c",
        rule_ids=(
            "ADV-ENDP-012", "ADV-ENDP-013", "ADV-ENDP-023", "ADV-ENDP-026",
        ),
    ),
    ProseObligation(
        model="ResponseExtraction", field="metadata", rule_ids=("ADV-ENDP-023",),
        prose_hash="f499ea237375",
    ),
    # === api-endpoint: write =================================================
    ProseObligation(
        model="WriteOperation", field="conflict_keys",
        prose_hash="0f620ea3b1a7",
        rule_ids=("ADV-ENDP-014", "ADV-ENDP-019"),
    ),
    ProseObligation(
        model="endpoints.Batching", field="max_records",
        prose_hash="25bf4976766d",
        structural="a Field ge lower bound",
    ),
    ProseObligation(
        model="Idempotency",
        prose_hash="b0adc4f0fe7c",
        structural=(
            "the closed model declares no value slot at all — `location` and "
            "`name` only, extra='forbid'"
        ),
    ),
    ProseObligation(
        model="Idempotency", field="location",
        prose_hash="94f399e042e2",
        waiver=(
            "body-objectness is known only when the request body is assembled; "
            "the prose states the engine rejects non-object bodies at "
            "configure time"
        ),
    ),
    # === api-endpoint: columns + documents ===================================
    ProseObligation(
        model="Column", field="arrow_type",
        prose_hash="0bfd7c2025c1",
        structural=(
            "Field(pattern=ARROW_TYPE_PATTERN), generated from the vendored "
            "engine grammar — parameterized families require their parameters"
        ),
    ),
    ProseObligation(
        model="ApiEndpointDoc", field="schema_url",
        prose_hash="787f4aa676e2",
        structural="required Literal pinned to schema_url_for('api-endpoint')",
    ),
    ProseObligation(
        model="DatabaseEndpointDoc", field="schema_url",
        prose_hash="f269dd217641",
        structural="required Literal pinned to schema_url_for('database-endpoint')",
    ),
    ProseObligation(
        model="DatabaseObject", field="object_type", waiver=ENGINE_CONDUCT,
        prose_hash="9689f5766a6e",
    ),
    # === full-census migration (worklist order) ==============================
    ProseObligation(model="ApiEndpointDoc", prose_hash="dc8a00ef4ac2", descriptive=True),
    ProseObligation(model="Column", prose_hash="2bd694ca67b8", descriptive=True),
    ProseObligation(
        model="Column", field="native_type",
        prose_hash="4b9192deb686",
        structural="required, with a Field length lower bound, so a label is always present",
        waiver=(
            "the fallback-label convention is authoring judgment: whether a "
            "provider type was genuinely unavailable at introspection time "
            "cannot be read from the document"
        ),
    ),
    ProseObligation(model="ColumnFieldSpec", prose_hash="0dde04175263", descriptive=True),
    ProseObligation(
        model="ColumnFieldSpec", field="arrow_type",
        prose_hash="bba63a6a84da",
        rule_ids=("ADV-ENDP-020",),
        structural=(
            "Field(pattern=ARROW_TYPE_PATTERN), generated from the vendored "
            "engine grammar; the container-marker sentence is the rule's "
            "container-shape check"
        ),
    ),
    ProseObligation(model="Cursor", field="param", prose_hash="17bfad8d4c5d", descriptive=True),
    ProseObligation(model="CursorPagination", prose_hash="253647c1fc9f", descriptive=True),
    ProseObligation(model="DatabaseEndpointDoc", prose_hash="33fd8eb4fb3c", descriptive=True),
    ProseObligation(
        model="DatabaseObject", waiver=ENGINE_CONDUCT,
        prose_hash="e72cf4e9227b",
    ),
    ProseObligation(model="DatabaseObject", field="name", prose_hash="ba4aa8eaf86d", descriptive=True),
    ProseObligation(
        model="FunctionExpression",
        prose_hash="bfc558a4d975",
        waiver=(
            "the registered-function catalog is engine-owned at configure "
            "time; the contract carries no catalog to resolve the `function` "
            "name against, so membership is not offline-checkable"
        ),
    ),
    ProseObligation(
        model="GetReadRequest",
        prose_hash="adc760b77ba3",
        structural=(
            "the model declares no `body` field and `StrictModel` forbids "
            "extras; the method-discriminated `ReadRequest` union routes GET "
            "reads to this bodyless branch"
        ),
    ),
    ProseObligation(model="GetReadRequest", field="method", prose_hash="ea42a27602c8", descriptive=True),
    ProseObligation(model="Idempotency", field="name", prose_hash="0254c1003efd", descriptive=True),
    ProseObligation(
        model="Keyset", field="initial", waiver=ENGINE_CONDUCT,
        prose_hash="9302943f9f24",
    ),
    ProseObligation(model="Keyset", field="param", prose_hash="2d66021b6236", descriptive=True),
    ProseObligation(model="KeysetPagination", prose_hash="bc461b98a480", descriptive=True),
    ProseObligation(model="LinkPagination", prose_hash="0cefda08ca88", descriptive=True),
    ProseObligation(model="LiteralExpression", prose_hash="ea43709ce09c", descriptive=True),
    ProseObligation(model="OffsetCursor", field="initial", prose_hash="a8e904c54f87", descriptive=True),
    ProseObligation(model="OffsetCursor", field="param", prose_hash="5c1a0248aafa", descriptive=True),
    ProseObligation(model="OffsetPagination", prose_hash="b0101e20267e", descriptive=True),
    ProseObligation(
        model="Operations", rule_ids=("ADV-ENDP-018",),
        prose_hash="57393ed00d58",
    ),
    ProseObligation(model="PageCursor", field="initial", prose_hash="896fe0e59108", descriptive=True),
    ProseObligation(model="PageCursor", field="param", prose_hash="4387bbb23f71", descriptive=True),
    ProseObligation(model="PagePagination", prose_hash="c0fdf3982fc5", descriptive=True),
    ProseObligation(model="PageSize", prose_hash="3cf85819e8cc", descriptive=True),
    ProseObligation(
        model="PageSize", field="default",
        prose_hash="cd3567d289ee",
        structural=(
            "a Field gt lower bound with Strict() on the bare-integer "
            "branch; the expression spellings are the typed `Expression` "
            "union, whose literal branch the prose itself states is "
            "unbounded"
        ),
    ),
    ProseObligation(model="Param", prose_hash="ac26d8a264f8", descriptive=True),
    ProseObligation(model="Param", field="controlled_by", prose_hash="d826ba713998", descriptive=True),
    ProseObligation(model="Param", field="default", prose_hash="95b46ea4340e", descriptive=True),
    ProseObligation(model="Param", field="location", prose_hash="4ad56f39fa37", descriptive=True),
    ProseObligation(
        model="Param", field="operators",
        prose_hash="c3ef12030ac5",
        structural="typed as a list of `Literal` members — the operator vocabulary is the type",
        waiver=(
            "the consequence of absence binds the stream document that "
            "filters on this param, not a checkable shape of this endpoint"
        ),
    ),
    ProseObligation(model="Param", field="style", prose_hash="491d84aaf9f9", descriptive=True),
    ProseObligation(model="Param", field="type", prose_hash="e716f55ea092", descriptive=True),
    ProseObligation(model="PostReadRequest", prose_hash="ec73f959a39b", descriptive=True),
    ProseObligation(
        model="PostReadRequest", field="body",
        prose_hash="958783250f0b",
        rule_ids=("ADV-ENDP-022",),
    ),
    ProseObligation(model="PostReadRequest", field="method", prose_hash="ea42a27602c8", descriptive=True),
    ProseObligation(model="ReadOperation", prose_hash="ec0e00d340de", descriptive=True),
    ProseObligation(model="Replication", prose_hash="4c297fc4a9ce", descriptive=True),
    ProseObligation(model="ResponseExtraction", prose_hash="a28b53384cfe", descriptive=True),
    ProseObligation(model="SingleCursorMapping", prose_hash="47d7dccff621", descriptive=True),
    ProseObligation(
        model="SingleCursorMapping", field="cursor_field",
        prose_hash="b8fff47c6f3f",
        structural="Field(pattern=RECORD_FIELD_PATH_PATTERN)",
    ),
    ProseObligation(model="WindowCursorMapping", prose_hash="d68aaa0695ba", descriptive=True),
    ProseObligation(
        model="WindowCursorMapping", field="cursor_field",
        prose_hash="b8fff47c6f3f",
        structural="Field(pattern=RECORD_FIELD_PATH_PATTERN)",
    ),
    ProseObligation(model="WriteError", prose_hash="4126f384e766", descriptive=True),
    ProseObligation(model="WriteInput", prose_hash="2221b86c9fac", descriptive=True),
    ProseObligation(
        model="WriteInput", field="schema_",
        prose_hash="dff7e3bde5fe",
        rule_ids=("ADV-ENDP-006", "ADV-ENDP-026"),
    ),
    ProseObligation(model="WriteOperation", prose_hash="494e5ab7f5db", descriptive=True),
    ProseObligation(
        model="WriteOperation", field="idempotency",
        prose_hash="f689d018db5b",
        rule_ids=("ADV-ENDP-015",),
    ),
    ProseObligation(model="WriteRequest", prose_hash="c2059bccd496", descriptive=True),
    ProseObligation(
        model="WriteRequest", field="body",
        prose_hash="191d279412ec",
        rule_ids=("ADV-ENDP-017", "ADV-ENDP-022"),
    ),
    ProseObligation(
        model="WriteRequest", field="method",
        prose_hash="c6efca5d851f",
        structural="the field's `Literal` member set is the closed enum",
    ),
    ProseObligation(model="WriteResponse", prose_hash="d757c8d295dd", descriptive=True),
    ProseObligation(model="WriteResponse", field="error", prose_hash="5e176d97f807", descriptive=True),
    ProseObligation(model="_EndpointBase", prose_hash="134249583cbe", descriptive=True),
    ProseObligation(
        model="_EndpointBase", field="description",
        prose_hash="0e2a699e0fec",
        structural="Field(max_length=DESCRIPTION_MAX)",
    ),
    ProseObligation(
        model="_EndpointBase", field="display_name",
        prose_hash="451b5a346d0e",
        structural=(
            "Field length bounds (DISPLAY_NAME_MIN/DISPLAY_NAME_MAX) and "
            "NO_EDGE_WHITESPACE_PATTERN, plus the `validate_display_name` "
            "field validator"
        ),
    ),
    ProseObligation(
        model="_EndpointBase", field="endpoint_id",
        prose_hash="8bdac7d3ef18",
        structural=(
            "Field(pattern=SLUG_PATTERN) — the description interpolates the "
            "same constant"
        ),
    ),
    ProseObligation(
        model="_EndpointBase", field="tags",
        prose_hash="2d7a5288f193",
        structural=(
            "Field(max_length=TAGS_MAX) of `TrimmedTag` items, plus the "
            "`validate_tags` field validator"
        ),
    ),
    ProseObligation(
        model="_EndpointModel",
        prose_hash="d4a23f9910d9",
        structural=(
            "the frozen ConfigDict the docstring itself describes — "
            "instances reject post-construction mutation"
        ),
    ),
    ProseObligation(model="_RequestBase", prose_hash="87c66ef8d3e5", descriptive=True),
    ProseObligation(
        model="_RequestBase", field="headers",
        prose_hash="7b62a1a97b23",
        rule_ids=("ADV-ENDP-022",),
    ),
    ProseObligation(model="_RequestBase", field="headers_remove", prose_hash="b518759030fa", descriptive=True),
    ProseObligation(model="_RequestBase", field="path", prose_hash="21f5956ac9f8", descriptive=True),
    ProseObligation(
        model="_RequestBase", field="query",
        prose_hash="16b50146e9fe",
        rule_ids=("ADV-ENDP-022",),
    ),
    ProseObligation(model="endpoints.Batching", prose_hash="6d788e505a0c", descriptive=True),
    ProseObligation(model="endpoints.RefExpression", prose_hash="533282983d9a", descriptive=True),
    ProseObligation(model="endpoints.TemplateExpression", prose_hash="5f7be25f35b6", descriptive=True),
)
