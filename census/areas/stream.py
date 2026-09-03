"""Census entries for ``analitiq.contracts.stream``."""
from __future__ import annotations

from census.obligation import (
    ENGINE_CONDUCT,
    ENGINE_OWNED_DEFAULTING,
    ProseObligation,
)

#: Shared by both `DatabasePagination` variants' `type`, whose descriptions each
#: report that the discriminator selects a document shape and not a read path.
#: The variant it selects is structural; that the selection changes nothing at
#: run time is a statement about engine code, which nothing here reads. It is
#: stated rather than left out because the field name promises a strategy, and
#: an author who takes the promise authors a keyset variant and gets an
#: offset-paged read.
_PAGINATION_TYPE_INERT = (
    "the description also reports that no engine release branches on this "
    "value — every database source read is offset-paged whichever variant is "
    "declared — which is engine behaviour no document here can be checked against"
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    # === stream ==============================================================
    ProseObligation(
        model="ConnectionEndpointRef", field="endpoint_id",
        prose_hash="e5cb382c9319",
        rule_ids=("RULE-STRM-003",),
    ),
    ProseObligation(
        model="StreamSource", field="selected_columns", rule_ids=("RULE-STRM-014",),
        prose_hash="7e3a158455ee",
    ),
    ProseObligation(
        model="_ReplicationBase", field="tie_breaker_fields",
        prose_hash="975844e642e8",
        rule_ids=("RULE-STRM-014",),
    ),
    ProseObligation(
        model="StreamSource", field="replication",
        prose_hash="9d0ca39dbabf",
        waiver=(
            "cross-document: the supported replication methods live on the "
            "endpoint document (endpoints.Replication.supported_methods), "
            "which this document cannot see"
        ),
    ),
    ProseObligation(
        model="StreamSource", field="database_pagination",
        prose_hash="c34682321b09",
        rule_ids=("RULE-STRM-014",),
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="PipeExpression", rule_ids=("RULE-STRM-005",),
        prose_hash="eaa9d838057e",
        structural=(
            "`args` carries a `Field` length floor, and "
            "`_pipe_args_positional_grammar` publishes the same positional "
            "grammar into the schema"
        ),
    ),
    ProseObligation(
        model="ArrowFieldSpec", field="arrow_type",
        prose_hash="cc0623bb24b9",
        structural=(
            "Field(pattern=ARROW_TYPE_PATTERN), generated from the vendored "
            "engine grammar — parameterized families require their parameters"
        ),
    ),
    ProseObligation(
        model="AssignmentTarget", field="arrow_type",
        prose_hash="cc0623bb24b9",
        structural=(
            "Field(pattern=ARROW_TYPE_PATTERN), generated from the vendored "
            "engine grammar — parameterized families require their parameters"
        ),
    ),
    ProseObligation(
        model="ConstantValue", field="arrow_type",
        prose_hash="cc0623bb24b9",
        structural=(
            "Field(pattern=ARROW_TYPE_PATTERN), generated from the vendored "
            "engine grammar — parameterized families require their parameters"
        ),
    ),
    ProseObligation(
        model="ValidationRule", field="value", rule_ids=("RULE-STRM-009",),
        prose_hash="3503a143ee9e",
    ),
    ProseObligation(
        model="ArrowFieldSpec", rule_ids=("RULE-STRM-006",),
        prose_hash="69d61d5979b3",
    ),
    ProseObligation(model="Assignment", prose_hash="37a26808a1c9", descriptive=True),
    ProseObligation(model="Assignment", field="validation", prose_hash="292c8abcf344", descriptive=True),
    ProseObligation(model="AssignmentTarget", prose_hash="ec7a5efb26c5", descriptive=True),
    ProseObligation(model="AssignmentTarget", field="native_type", prose_hash="fa390bd49541", descriptive=True),
    ProseObligation(
        model="AssignmentTarget", field="path",
        prose_hash="529c1c3108df",
        structural=(
            "a `Field` pattern (`SINGLE_SEGMENT_PATH_PATTERN`) makes a "
            "dotted segment unparseable"
        ),
    ),
    ProseObligation(
        model="ConnectionEndpointRef", rule_ids=("RULE-STRM-003",),
        prose_hash="6c05e00e6d61",
        structural=(
            "`database_object` is a required non-optional field on this "
            "variant of the `scope`-discriminated `EndpointRef` union"
        ),
    ),
    ProseObligation(
        model="ConnectionEndpointRef", field="database_object",
        prose_hash="758019a3ecb9",
        structural="a required non-optional field typed `DatabaseObject`",
    ),
    ProseObligation(
        model="ConnectionEndpointRef", field="scope",
        prose_hash="34ee047d112c",
        structural=(
            "`Literal` tag of the `scope`-discriminated `EndpointRef` union"
        ),
    ),
    ProseObligation(
        model="ConnectorEndpointRef",
        prose_hash="adb37d67ca20",
        structural=(
            "the variant declares no `database_object` field and "
            "`StrictModel` extra='forbid' rejects one supplied"
        ),
    ),
    ProseObligation(
        model="ConnectorEndpointRef", field="endpoint_id",
        prose_hash="514c40d2ae26",
        waiver=(
            "cross-document: the id names an endpoint the referenced "
            "connector ships as its own endpoint documents, which this "
            "document cannot see — registry membership is resolved at "
            "configure time"
        ),
    ),
    ProseObligation(
        model="ConnectorEndpointRef", field="scope",
        prose_hash="40fa6ffcd872",
        structural=(
            "`Literal` tag of the `scope`-discriminated `EndpointRef` union"
        ),
    ),
    ProseObligation(model="ConstantAssignmentValue", prose_hash="042936c95a12", descriptive=True),
    ProseObligation(model="ConstantAssignmentValue", field="constant", prose_hash="4752f5d5b2cb", descriptive=True),
    ProseObligation(
        model="ConstantAssignmentValue", field="kind",
        prose_hash="98f9cb2ca52d",
        structural=(
            "`Literal` tag of the `kind`-discriminated `AssignmentValue` union"
        ),
    ),
    ProseObligation(model="ConstantValue", prose_hash="5086a7098a50", descriptive=True),
    ProseObligation(
        model="ConstantValue", field="value", rule_ids=("RULE-STRM-007",),
        prose_hash="a2b5c9af0d6d",
    ),
    ProseObligation(model="Execution", prose_hash="7ecf26dedde2", descriptive=True),
    ProseObligation(model="Execution", field="batch_size", prose_hash="218a6964b727", descriptive=True),
    ProseObligation(model="ExpressionAssignmentValue", prose_hash="583661f1c4db", descriptive=True),
    ProseObligation(model="ExpressionAssignmentValue", field="expression", prose_hash="5d6d099a0144", descriptive=True),
    ProseObligation(
        model="ExpressionAssignmentValue", field="kind",
        prose_hash="7d2a2d79a0fd",
        structural=(
            "`Literal` tag of the `kind`-discriminated `AssignmentValue` union"
        ),
    ),
    ProseObligation(
        model="Filter", rule_ids=("RULE-STRM-004",),
        prose_hash="a2bddda2c40d",
        waiver=(
            "cross-document: which fields are filterable and which operators "
            "each offers is endpoint-owned; and the requires-half admits an "
            "explicit null with a binary operator — the validator cannot tell "
            "omitted from null, so that residue is deferred to endpoint "
            "resolution"
        ),
    ),
    ProseObligation(
        model="Filter", field="field", rule_ids=("RULE-STRM-022", "RULE-STRM-026"),
        prose_hash="82c56f1963f2",
        waiver=(
            "cross-document: whether the name resolves — to a column on a "
            "database source, or to a field the endpoint's read `filters` map "
            "offers on an API source — is settled against the endpoint "
            "document, which the stream does not carry"
        ),
    ),
    ProseObligation(
        model="Filter", field="operator", rule_ids=("RULE-STRM-012",),
        prose_hash="6382c8ad9ce6",
        structural="the `FilterOperator` `Literal` is the structural floor",
        waiver=(
            "cross-document: the finer per-endpoint operator subset an API "
            "source may use is endpoint-owned and resolved at runtime"
        ),
    ),
    ProseObligation(
        model="Filter", field="value", rule_ids=("RULE-STRM-004",),
        prose_hash="bf827fea3cd0",
    ),
    ProseObligation(
        model="FnExpression",
        prose_hash="d0b4746c49c4",
        structural=(
            "`name` is a closed `Literal` tracking the engine-published "
            "conversion matrix; `StrictModel` extra='forbid' keeps the "
            "engine's undeclared node fields unrepresentable"
        ),
    ),
    ProseObligation(
        model="FnExpression", field="name",
        prose_hash="365a58ad39c8",
        structural=(
            "a closed `Literal`; widening it tracks the engine-published "
            "conversion matrix"
        ),
    ),
    ProseObligation(
        model="FullRefreshReplication",
        prose_hash="f2e6a5dfa4fa",
        structural=(
            "the full-refresh variant of the `method`-discriminated "
            "`Replication` union declares no `cursor_field` and "
            "extra='forbid' rejects one"
        ),
    ),
    ProseObligation(model="FullRefreshReplication", field="method", prose_hash="cfe312611d1e", descriptive=True),
    ProseObligation(
        model="GetExpression",
        prose_hash="13891ce9f2d3",
        structural=(
            "`path` is typed as an array of `NonEmptyStr` tokens with a "
            "`Field` length floor — no splitting convention is left to state"
        ),
    ),
    ProseObligation(
        model="GetExpression", field="path",
        prose_hash="e3fe1a99d6d4",
        structural=(
            "typed as an array of `NonEmptyStr` tokens with a `Field` "
            "length floor — no splitting convention is left to state"
        ),
    ),
    ProseObligation(model="IncrementalReplication", prose_hash="f578cc740356", descriptive=True),
    ProseObligation(model="IncrementalReplication", field="cursor_field", prose_hash="1187bd765dd0", descriptive=True),
    ProseObligation(model="IncrementalReplication", field="method", prose_hash="cfe312611d1e", descriptive=True),
    ProseObligation(
        model="KeysetDatabasePagination",
        prose_hash="521daa26daad",
        structural=(
            "`order_by_field` is required (no default) on the keyset variant "
            "of the `type`-discriminated `DatabasePagination` union"
        ),
    ),
    ProseObligation(
        model="KeysetDatabasePagination", field="order_by_field",
        prose_hash="9fbe9368f9bb",
        structural="declared required with no default on this variant",
    ),
    ProseObligation(
        model="KeysetDatabasePagination", field="type",
        prose_hash="4388a16a4775",
        structural="the `Literal` selects which variant of the union applies",
        waiver=_PAGINATION_TYPE_INERT,
    ),
    ProseObligation(model="OffsetDatabasePagination", prose_hash="b18e21310f08", descriptive=True),
    ProseObligation(model="OffsetDatabasePagination", field="order_by_field", prose_hash="f0584d1307f0", descriptive=True),
    ProseObligation(
        model="OffsetDatabasePagination", field="type",
        prose_hash="4388a16a4775",
        structural="the `Literal` selects which variant of the union applies",
        waiver=_PAGINATION_TYPE_INERT,
    ),
    ProseObligation(
        model="PipeExpression", field="args", rule_ids=("RULE-STRM-005",),
        prose_hash="8941ee97faed",
        structural=(
            "a `Field` length floor, and `_pipe_args_positional_grammar` "
            "publishes the same positional grammar into the schema"
        ),
    ),
    ProseObligation(model="StreamAuthored", prose_hash="37db262d3f4a", descriptive=True),
    ProseObligation(model="StreamAuthored", field="description", prose_hash="e8037a4395dd", descriptive=True),
    ProseObligation(
        model="StreamAuthored", field="destinations",
        prose_hash="75caa55ba7f6",
        structural="a `Field` length floor rejects an empty array",
    ),
    ProseObligation(model="StreamAuthored", field="display_name", prose_hash="e2018541008f", descriptive=True),
    ProseObligation(
        model="StreamAuthored", field="mapping",
        prose_hash="5e39ac841135",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="StreamAuthored", field="pipeline_id",
        prose_hash="559e139f235c",
        structural="`NonEmptyStr` carries the static constraint",
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(model="StreamAuthored", field="schema_url", prose_hash="a23f8399df2b", descriptive=True),
    ProseObligation(model="StreamAuthored", field="status", prose_hash="8ee95e9d5922", descriptive=True),
    ProseObligation(
        model="StreamAuthored", field="tags",
        prose_hash="2d7a5288f193",
        structural=(
            "`TrimmedTag` items, the `Field` count ceiling, and the "
            "`validate_tags` field validator carry the stated length, "
            "count, uniqueness and trim constraints"
        ),
    ),
    ProseObligation(model="DatabaseStreamDestination", prose_hash="07b2c0e27c4d", descriptive=True),
    ProseObligation(model="DatabaseStreamDestination", field="endpoint_ref", prose_hash="693d86d9a948", descriptive=True),
    ProseObligation(model="DatabaseStreamDestination", field="write", prose_hash="b937c2092d5d", descriptive=True),
    ProseObligation(model="ApiStreamDestination", prose_hash="8073ad298700", descriptive=True),
    ProseObligation(model="ApiStreamDestination", field="endpoint_ref", prose_hash="693d86d9a948", descriptive=True),
    ProseObligation(model="ApiStreamDestination", field="write", prose_hash="b937c2092d5d", descriptive=True),
    ProseObligation(
        model="StreamInput",
        prose_hash="1834827e70c4",
        structural=(
            "`ConfigDict` extra='forbid' closes the authored surface; "
            "`stream_id` carries a `Field` pattern (`UUID_PATTERN`)"
        ),
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="StreamInput", field="stream_id",
        prose_hash="fb7004c260e1",
        structural="a `Field` pattern (`UUID_PATTERN`)",
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="StreamMapping", waiver=ENGINE_OWNED_DEFAULTING,
        prose_hash="f8f3289150f0",
    ),
    ProseObligation(model="StreamMapping", field="assignments", prose_hash="367c0f2dc2b7", descriptive=True),
    ProseObligation(model="StreamSource", prose_hash="e514f308d8da", descriptive=True),
    ProseObligation(model="StreamSource", field="endpoint_ref", prose_hash="693d86d9a948", descriptive=True),
    ProseObligation(model="StreamSource", field="filters", prose_hash="9e1bb33d4ab4", descriptive=True),
    ProseObligation(model="StreamSource", field="primary_keys", prose_hash="1939bc8bd122", descriptive=True),
    ProseObligation(
        model="StreamValidationErrorHandling",
        prose_hash="4b72a3e2f218",
        structural=(
            "inherits the shared shape from `RetryErrorHandlingBase`, the "
            "same base the pipeline block subclasses — the mirror holds by "
            "construction"
        ),
    ),
    ProseObligation(model="Validation", prose_hash="20738e91c5c2", descriptive=True),
    ProseObligation(
        model="Validation", field="error_handling",
        prose_hash="9ce9c331f1e7",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(model="ValidationRule", prose_hash="abaa76935a9d", descriptive=True),
    ProseObligation(
        model="ValidationRule", field="field", prose_hash="0e4f13107d1d",
        rule_ids=("RULE-STRM-015",),
        structural=(
            "`NonEmptyStr` items and a list length floor carry the token-array "
            "shape — one token is one field name, matching a source `get` "
            "segment; the rule carries the resolution against the mapping's "
            "targets"
        ),
    ),
    ProseObligation(model="ValidationRule", field="message", prose_hash="15b46cce1ca8", descriptive=True),
    ProseObligation(
        model="DatabaseKeylessWrite", prose_hash="b10ba6d08d8a",
        structural=(
            "the variant declares no `conflict_keys` field and is closed "
            "(extra='forbid'), so the absence the docstring states is the shape"
        ),
    ),
    ProseObligation(
        model="DatabaseKeylessWrite", field="mode", prose_hash="91877a23c37d",
        structural="a `Literal` over the keyless database write modes",
    ),
    ProseObligation(
        model="DatabaseConflictKeyedWrite", prose_hash="8cabbdd5c0ea",
        descriptive=True,
    ),
    ProseObligation(
        model="DatabaseConflictKeyedWrite", field="mode", prose_hash="27a3fe0f15f6",
        structural="a `Literal` over the conflict-keyed database write modes",
    ),
    ProseObligation(
        model="DatabaseConflictKeyedWrite", field="conflict_keys",
        prose_hash="65fc9fa027df",
        structural=(
            "a required list of `NonEmptyStr` with a `Field` length floor: one "
            "key set of field names, non-empty, and not a list of key sets"
        ),
    ),
    ProseObligation(
        model="ApiWrite", prose_hash="9fa299150c55",
        structural=(
            "the variant declares no `conflict_keys` field and is closed "
            "(extra='forbid'), so the absence the docstring states is the shape"
        ),
    ),
    ProseObligation(
        model="ApiWrite", field="mode", prose_hash="2b80af8d1987",
        structural=(
            "`WriteMode` — the same `Literal` that types the keys of "
            "`endpoints.Operations.write`, so a mode no api-endpoint document "
            "could declare is unrepresentable here"
        ),
        waiver=(
            "cross-document: WHICH of those keys the selected endpoint actually "
            "declares lives on the endpoint document this one cannot see; only "
            "the vocabulary it is drawn from is reachable"
        ),
    ),
    ProseObligation(model="_StreamDestinationBase", prose_hash="79a9457b1ac7", descriptive=True),
    ProseObligation(model="_StreamDestinationBase", field="execution", prose_hash="ca92ad7c8970", descriptive=True),
    ProseObligation(model="_DatabasePaginationBase", prose_hash="f1a6f478c511", descriptive=True),
    ProseObligation(
        model="_DatabasePaginationBase", field="page_size",
        prose_hash="9b10a8032319",
        structural="a `Field` lower bound rejects a non-positive size",
        waiver=(
            "the description's second half reports that no engine release reads "
            "this field, so a declared size never takes effect. Nothing here can "
            "check it — the claim is about engine code, not about this document "
            "— and it is stated rather than omitted because an author who "
            "believes the field works gets a read size they never chose"
        ),
    ),
    ProseObligation(
        model="_EndpointRefBase", rule_ids=("RULE-STRM-003",),
        prose_hash="c7da82042663",
    ),
    ProseObligation(
        model="_EndpointRefBase", field="connection_id",
        prose_hash="059be2a13a93",
        structural="`NonEmptyStr` carries the static constraint",
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(model="_ReplicationBase", prose_hash="848a0697f02d", descriptive=True),
    ProseObligation(
        model="_ReplicationBase", field="safety_window_seconds",
        prose_hash="443040c5c1ac",
        structural="a `Field` lower bound rejects a negative window",
    ),
)
