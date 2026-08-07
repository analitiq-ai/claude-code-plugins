"""The contract's rules — authored data, one entry per rule about a document.

Two tiers live here. The **relational** tier is what this registry started as:
every entry is enforced at runtime by :mod:`advisory` (generic kinds) or by the
bespoke method it names (``kind="custom"``). Generic rules additionally carry a
valid/invalid fixture corpus (``contract-models/tests/fixtures/advisory``) a
non-Python second system reconciles against; custom rules are enforced only
in-process by their named validator and may carry no fixtures. Adding a
relational rule is a data edit here — never new imperative code.

The **structural** tier catalogues rules the models already enforce through
their own shape. Those rules were always enforced and always anonymous, so
plugin prose had no id to cite and hand-copied the shape instead — the member
list, the pattern, the required set — and the copies drifted. A structural
entry names the mechanism and the field; the values stay in the model and are
read from it at render time. An entry that spells them out again has recreated
the surface it was added to remove, and ``test_advisory_registry.py`` fails it.

Rules about anything other than a document — a connector's Python package, a
choice between transports that both validate — are the waived tier, in
:mod:`authoring_rules`.

IDs are ``ADV-<AREA>-NNN``, stable and never reused. ``targets`` name model
classes by string; a rule on a base class covers its subclasses (MRO match).

:mod:`advisory_prose` is this census's other half: every field description and
model docstring must resolve in :mod:`prose_census` to the rule, structural
mechanism, registered waiver, or descriptive marking carrying it — each entry
hash-pinned to its exact wording — so an obligation stated in prose and
enforced nowhere fails the build instead of shipping silently.
"""
from __future__ import annotations

from .advisory import AdvisoryRule, register

ADVISORY_RULES: list[AdvisoryRule] = [
    # --- HTTP request blocks (endpoints + connector share the intent) -------
    AdvisoryRule(
        id="ADV-HTTP-001",
        kind="disjoint",
        resource="shared",
        prose=(
            "A header named in headers_remove must not also be declared in "
            "headers by the same block (case-insensitive)."
        ),
        targets=(
            "_RequestBase",
            "AuthOperationTemplate",
            "HttpTransport",
            "TransportDefaults",
        ),
        fields=("headers", "headers_remove"),
        options={"case_insensitive": True},
        fixture_model="AuthOperationTemplate",
    ),
    # --- api-endpoint request binding ---------------------------------------
    AdvisoryRule(
        id="ADV-ENDP-001",
        kind="custom",
        resource="api-endpoint",
        prose=(
            "request.path_params keys must equal the {placeholder} names in "
            "request.path, and path_params is present exactly when the path "
            "declares placeholders."
        ),
        targets=("_RequestBase",),
        enforcer="_validate",
        fixture_model="WriteRequest",
    ),
    # --- connector connection_contract inputs -------------------------------
    AdvisoryRule(
        id="ADV-CONN-001",
        kind="set_equal",
        resource="connector",
        prose="ui.options must enumerate exactly the same value set as enum.",
        targets=("ConnectionContractInput",),
        fields=("ui.options[].value", "enum"),
    ),
    AdvisoryRule(
        id="ADV-CONN-002",
        kind="member_of",
        resource="connector",
        prose="default must be a member of enum when both are present.",
        targets=("ConnectionContractInput",),
        fields=("default", "enum"),
    ),
    # --- stream destinations + mapping --------------------------------------
    AdvisoryRule(
        id="ADV-STRM-001",
        kind="unique_by",
        resource="stream",
        prose=(
            "destinations must be unique by "
            "(endpoint_ref.scope, endpoint_ref.connection_id, endpoint_ref.endpoint_id)."
        ),
        targets=("StreamAuthored",),
        fields=("destinations",),
        options={
            "key": [
                "endpoint_ref.scope",
                "endpoint_ref.connection_id",
                "endpoint_ref.endpoint_id",
            ]
        },
        fixture_model="StreamInput",
    ),
    AdvisoryRule(
        id="ADV-STRM-002",
        kind="unique_by",
        resource="stream",
        prose="mapping.assignments[].target.path must be unique within the mapping.",
        targets=("StreamMapping",),
        fields=("assignments",),
        options={"key": ["target.path"]},
    ),
    # --- database-endpoint columns ------------------------------------------
    AdvisoryRule(
        id="ADV-DBEP-001",
        kind="unique_by",
        resource="database-endpoint",
        prose="columns[].name must be unique.",
        targets=("DatabaseEndpointDoc",),
        fields=("columns",),
        options={"key": ["name"]},
    ),
    AdvisoryRule(
        id="ADV-DBEP-002",
        kind="unique_by",
        resource="database-endpoint",
        prose="columns[].ordinal_position must be unique where present.",
        targets=("DatabaseEndpointDoc",),
        fields=("columns",),
        options={"key": ["ordinal_position"], "skip_null": True},
    ),
    AdvisoryRule(
        id="ADV-DBEP-003",
        kind="subset_of",
        resource="database-endpoint",
        prose="primary_keys must reference declared columns[].name.",
        targets=("DatabaseEndpointDoc",),
        fields=("primary_keys", "columns[].name"),
    ),
    # --- connector document -------------------------------------------------
    AdvisoryRule(
        id="ADV-CTOR-001",
        kind="member_of",
        resource="connector",
        prose="default_transport must name a key declared in transports.",
        targets=("ConnectorBase",),
        fields=("default_transport", "transports"),
        fixture_model="ApiConnector",
    ),
    # --- pipeline connections -----------------------------------------------
    AdvisoryRule(
        id="ADV-PIPE-001",
        kind="unique_by",
        resource="pipeline",
        prose="connections.destinations must not contain duplicate connection IDs.",
        targets=("PipelineConnections",),
        fields=("destinations",),
    ),
    # ======================================================================
    # CUSTOM catalog — value-conditioned / compound / recursive rules whose
    # enforcement is irreducibly bespoke. The named model validator stays the
    # enforcer; these entries make the census complete (every relational rule
    # declares its enforcer) and drive the generated docs + checklist.
    # ======================================================================
    # --- api-endpoint -------------------------------------------------------
    AdvisoryRule(
        id="ADV-ENDP-002", kind="custom", resource="api-endpoint",
        prose="A controlled_by parameter must not also declare operators.",
        targets=("Param",), enforcer="_validate",
    ),
    AdvisoryRule(
        id="ADV-ENDP-003", kind="custom", resource="api-endpoint",
        prose="A query parameter of type array or object must declare style and explode.",
        targets=("Param",), enforcer="_validate",
    ),
    AdvisoryRule(
        id="ADV-ENDP-004", kind="custom", resource="api-endpoint",
        prose="A cursor mapping must not mix the single-parameter form with the window (start/end) form.",
        targets=("Replication",), enforcer="_reject_mixed_cursor_forms",
    ),
    AdvisoryRule(
        id="ADV-ENDP-005", kind="custom", resource="api-endpoint",
        prose="Every node in response.schema must pair native_type/arrow_type and match the Object/List/scalar container shape.",
        targets=("ResponseExtraction",), enforcer="_validate",
    ),
    AdvisoryRule(
        id="ADV-ENDP-006", kind="custom", resource="api-endpoint",
        prose="Every node in input.schema must pair native_type/arrow_type and match its container shape.",
        targets=("WriteInput",), enforcer="_validate",
    ),
    AdvisoryRule(
        id="ADV-ENDP-007", kind="custom", resource="api-endpoint",
        prose="A GET read operation must not declare a parameter located in the body.",
        targets=("ReadOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-008", kind="custom", resource="api-endpoint",
        prose="Every {from_param} binding must reference a declared parameter whose location matches the binding site.",
        targets=("ReadOperation", "WriteOperation"), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-009", kind="custom", resource="api-endpoint",
        prose="Every declared parameter must be referenced by exactly one request binding.",
        targets=("ReadOperation", "WriteOperation"), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-010", kind="custom", resource="api-endpoint",
        prose="Pagination parameter references must exist and declare controlled_by='pagination'.",
        targets=("ReadOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-011", kind="custom", resource="api-endpoint",
        prose="Replication cursor parameter references must exist and declare controlled_by='replication'.",
        targets=("ReadOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-012", kind="custom", resource="api-endpoint",
        prose="response.records.ref must resolve to an array node inside response.schema.",
        targets=("ReadOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-013", kind="custom", resource="api-endpoint",
        prose="Each replication cursor_field must resolve to a field in the record shape of response.schema.",
        targets=("ReadOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-014", kind="custom", resource="api-endpoint",
        prose="write.conflict_keys must reference top-level fields declared in input.schema.",
        targets=("WriteOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-015", kind="custom", resource="api-endpoint",
        prose="idempotency and batching are mutually exclusive on a write operation.",
        targets=("WriteOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-016", kind="custom", resource="api-endpoint",
        prose="An idempotency key name must not collide with a declared header or body field.",
        targets=("WriteOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-017", kind="custom", resource="api-endpoint",
        prose="batching selects the from_input arity in request.body (records when batching, record otherwise), and the referenced field must exist in input.schema.",
        targets=("WriteOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-018", kind="custom", resource="api-endpoint",
        prose="An operations block must declare at least one of read or write.",
        targets=("Operations",), enforcer="_at_least_one",
    ),
    AdvisoryRule(
        id="ADV-ENDP-019", kind="custom", resource="api-endpoint",
        prose="An upsert write mode requires conflict_keys; any other write mode forbids it.",
        targets=("Operations",), enforcer="_conflict_keys_by_mode",
    ),
    AdvisoryRule(
        id="ADV-ENDP-020", kind="custom", resource="api-endpoint",
        prose="A column field's arrow_type must match its container shape: Object declares properties, List declares items, scalars neither.",
        targets=("ColumnFieldSpec",), enforcer="_validate_container_shape",
    ),
    AdvisoryRule(
        id="ADV-ENDP-021", kind="custom", resource="api-endpoint",
        prose="A column's arrow_type must match its container shape: Object declares properties, List declares items, scalars neither.",
        targets=("Column",), enforcer="_validate_container_shape",
    ),
    AdvisoryRule(
        id="ADV-ENDP-022", kind="custom", resource="api-endpoint",
        prose="Every expression dict in a request slot must declare exactly one primary key (ref/template/literal/function/from_param/from_input) alongside only x-* siblings.",
        targets=("ReadOperation", "WriteOperation"), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-023", kind="custom", resource="api-endpoint",
        prose="Every response.body path referenced by pagination, response.metadata, a request path_params/headers/query/body slot, or a params.<name>.default must resolve by declared-path resolution against response.schema, to a node that declares a type; a response.* reference in a request slot is refused outright (the request is built before the response exists), as are a response.* reference naming no known sub-scope, a response.metadata.<key> naming an undeclared key, and a keyset order_by_field absent from the record shape.",
        targets=("ReadOperation",), enforcer="_wiring",
    ),
    # Stated as the CONSTRAINT, not as a grant — the registry's general style.
    # This table renders verbatim into `references/advisory-rules.md` and sits
    # beside hand-authored authoring prose, so a row reading "a write path_param
    # MAY bind from_input" would read as an instruction to use it; teaching
    # belongs in `spec-request-binding.md`, which owns the craft (when to choose
    # it over `from_param`, the batching exclusion, engine-owned encoding).
    # The rule is the same either way — what changes is whether the reference
    # table teaches.
    AdvisoryRule(
        id="ADV-ENDP-024", kind="custom", resource="api-endpoint",
        prose="A path_param from_input binding is invalid on a read operation, and on a write must address exactly one record field (record.<dotted>, never record or records) that input.schema declares.",
        targets=("ReadOperation", "WriteOperation"), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-025", kind="custom", resource="api-endpoint",
        prose="from_input in request.path_params and batching are mutually exclusive on a write operation.",
        targets=("WriteOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-026", kind="custom", resource="api-endpoint",
        prose="Every $ref in an embedded response/input schema must be an in-document reference that resolves to a schema in the same document.",
        targets=("ResponseExtraction", "WriteInput"), enforcer="_validate",
    ),
    AdvisoryRule(
        id="ADV-ENDP-027", kind="custom", resource="api-endpoint",
        prose="A request.path_params binding must not apply a wire-encoding function (url_encode/base64_encode): the engine percent-encodes each substituted path segment.",
        targets=("ReadOperation", "WriteOperation"), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-028", kind="custom", resource="api-endpoint",
        prose="A write request.path_params from_param binding must name a param declaring a default; a write param has no other source, so a sourceless one can never resolve.",
        targets=("WriteOperation",), enforcer="_wiring",
    ),
    AdvisoryRule(
        id="ADV-ENDP-029", kind="custom", resource="api-endpoint",
        prose="Write-response metadata keys must match the metadata-key pattern and must not collide with a reserved response-scope name.",
        targets=("WriteResponse",), enforcer="_metadata_keys",
    ),
    AdvisoryRule(
        id="ADV-ENDP-030", kind="custom", resource="api-endpoint",
        prose="A write-response expression must not reference response.record_count, a read-only response scope.",
        targets=("WriteResponse",), enforcer="_reject_record_count",
    ),
    AdvisoryRule(
        id="ADV-ENDP-031", kind="custom", resource="api-endpoint",
        prose="database_object.catalog and database_object.schema must be omitted when not applicable; explicit null is invalid.",
        targets=("DatabaseObject",), enforcer="_reject_explicit_null_namespaces",
    ),
    # --- stream -------------------------------------------------------------
    AdvisoryRule(
        id="ADV-STRM-003", kind="custom", resource="stream",
        prose="A supplied endpoint_id must equal derive_db_endpoint_id(database_object).",
        targets=("ConnectionEndpointRef",), enforcer="_derive_or_verify_endpoint_id",
    ),
    AdvisoryRule(
        id="ADV-STRM-004", kind="custom", resource="stream",
        prose="A unary filter operator (is_null/is_not_null) must omit value; every other operator requires it.",
        targets=("Filter",), enforcer="_validate_value_presence",
    ),
    AdvisoryRule(
        id="ADV-STRM-005", kind="custom", resource="stream",
        prose="A pipe expression must start with a get step and be followed only by fn steps.",
        targets=("PipeExpression",), enforcer="_validate_positional_grammar",
    ),
    AdvisoryRule(
        id="ADV-STRM-006", kind="custom", resource="stream",
        prose="An arrow field's arrow_type must match its container shape: Object declares properties, List declares items, scalars neither.",
        targets=("ArrowFieldSpec",), enforcer="_validate_container_shape",
    ),
    AdvisoryRule(
        id="ADV-STRM-007", kind="custom", resource="stream",
        prose="constant.value's JSON kind must match arrow_type, and the Object/List/scalar container shape rule applies.",
        targets=("ConstantValue",), enforcer="_validate_container_shape",
    ),
    # ADV-STRM-008 retired in 1.0.0rc19: `AssignmentValue` became a
    # `kind`-discriminated union, so "exactly one of expression or constant" is
    # no longer a rule anything enforces — the union states it. Advisory ids are
    # stable identifiers that appear in user-facing findings, so the gap stays a
    # gap: do NOT reuse 008 for an unrelated rule, or archived findings decode
    # to the wrong meaning.
    AdvisoryRule(
        id="ADV-STRM-009", kind="custom", resource="stream",
        prose="A validation rule requires value for value-taking types and omits it for required/not_null.",
        targets=("ValidationRule",), enforcer="_validate_value_for_rule",
    ),
    AdvisoryRule(
        id="ADV-STRM-010", kind="custom", resource="stream",
        prose="An assignment target's arrow_type must match its container shape: Object declares properties, List declares items, scalars neither.",
        targets=("AssignmentTarget",), enforcer="_validate_container_shape",
    ),
    # ADV-STRM-011 ("conflict_keys required for a connection-scope upsert,
    # forbidden otherwise") retired: the destination became an
    # `endpoint_ref.scope`-tagged union whose database branch is itself
    # `mode`-discriminated, so only `DatabaseConflictKeyedWrite` declares the
    # field at all and it is required there. No validator enforces the rule
    # because no shape can break it. Ids are stable identifiers in archived
    # findings: do NOT reuse 011.
    AdvisoryRule(
        id="ADV-STRM-012", kind="custom", resource="stream",
        prose="A filter operator must belong to the source scope's vocabulary: the database operator set for a connection source, the API operator set for a connector source.",
        targets=("StreamSource",), enforcer="_validate_filter_operator_scope",
    ),
    # ADV-STRM-013 ("a database destination's write.mode belongs to the closed
    # database vocabulary; an API destination's mode is endpoint-declared")
    # retired alongside 011: the database branch of the destination union types
    # `mode` as a Literal over that vocabulary, and only the API branch leaves
    # it open. Do NOT reuse 013.
    AdvisoryRule(
        id="ADV-STRM-014", kind="custom", resource="stream",
        prose="selected_columns, replication.tie_breaker_fields and database_pagination are database-source features: a connector-scope (API) source must not declare them.",
        targets=("StreamSource",), enforcer="_validate_database_only_read_features",
    ),
    AdvisoryRule(
        id="ADV-STRM-015", kind="custom", resource="stream",
        prose="A validation rule's field must resolve against the mapping's assignment targets: its first token names a declared target.path and each later token names a field declared under that target's properties.",
        targets=("StreamMapping",), enforcer="_validate_rule_fields_resolve",
    ),
    # --- connector (ConnectionContractInput + connector document) -----------
    AdvisoryRule(
        id="ADV-CONN-003", kind="custom", resource="connector",
        prose="secret must be true if and only if storage is 'secrets'.",
        targets=("ConnectionContractInput",), enforcer="_consistency",
    ),
    # --- connection stored maps ---------------------------------------------
    AdvisoryRule(
        id="ADV-CONN-004", kind="custom", resource="connection",
        prose="parameters, selections and discovered keys must not be secret-shaped; a secret lives in secret storage and is referenced via secret_refs.",
        targets=("ConnectionStoredMaps",), enforcer="_validate_no_secret_keys",
    ),
    # --- connector document (post-auth, discovery, DSN, capabilities) -------
    AdvisoryRule(
        id="ADV-CTOR-002", kind="custom", resource="connector",
        prose="A user_selection post-auth output requires options_request and forbids discovery_request; an auto_discovery output requires discovery_request and forbids options_request/options_path/label_path; storage is constrained by mode.",
        targets=("PostAuthOutput",), enforcer="_mode_consistency",
    ),
    AdvisoryRule(
        id="ADV-CTOR-003", kind="custom", resource="connector",
        prose="A connector_plugin resource discovery requires an entrypoint; a builtin forbids it.",
        targets=("ResourceDiscoveryImplementation",), enforcer="_entrypoint_matches_type",
    ),
    AdvisoryRule(
        id="ADV-CTOR-004", kind="custom", resource="connector",
        prose="An ADBC transport must declare at least one of dsn or db_kwargs.",
        targets=("AdbcTransport",), enforcer="_require_dsn_or_kwargs",
    ),
    AdvisoryRule(
        id="ADV-CTOR-005", kind="custom", resource="connector",
        prose="Every transport_ref (auth operations, resource discovery, post-auth requests) must resolve to a declared transport.",
        targets=("ConnectorBase",), enforcer="_transport_refs_resolvable",
    ),
    AdvisoryRule(
        id="ADV-CTOR-006", kind="custom", resource="connector",
        prose="No two connection_contract declarations may write the same secrets storage path.",
        targets=("ConnectorBase",), enforcer="_connection_contract_internal_refs",
    ),
    AdvisoryRule(
        id="ADV-CTOR-007", kind="custom", resource="connector",
        prose="required_for_activation must reference declared input or post-auth-output storage paths.",
        targets=("ConnectorBase",), enforcer="_connection_contract_internal_refs",
    ),
    AdvisoryRule(
        id="ADV-CTOR-008", kind="custom", resource="connector",
        prose="validation.rules[].when.field must reference a declared input.",
        targets=("ConnectorBase",), enforcer="_connection_contract_internal_refs",
    ),
    AdvisoryRule(
        id="ADV-CTOR-009", kind="custom", resource="connector",
        prose="validation.rules[].require and .forbid must reference declared inputs.",
        targets=("ConnectorBase",), enforcer="_connection_contract_internal_refs",
    ),
    AdvisoryRule(
        id="ADV-CTOR-010", kind="custom", resource="connector",
        prose="file/s3/stdout connectors must not declare post_auth_outputs or required_for_activation.",
        targets=("FileConnector", "S3Connector", "StdoutConnector"),
        enforcer="_validate_no_post_auth_contract",
    ),
    AdvisoryRule(
        id="ADV-CTOR-011", kind="custom", resource="connector",
        prose="Every {placeholder} in a url_template DSN must have a matching entry in bindings, and every binding must be referenced by the template.",
        targets=("UrlTemplateDsn",), enforcer="_validate_placeholder_bindings",
    ),
    AdvisoryRule(
        id="ADV-CTOR-012", kind="custom", resource="connector",
        prose="A connection condition predicate must declare field and exactly one operator key (eq/in/not_in/present/regex).",
        targets=("ConnectionConditionPredicate",), enforcer="_exactly_one_operator",
    ),
    AdvisoryRule(
        id="ADV-CTOR-013", kind="custom", resource="connector",
        prose="dedicated_schema is required when the stage schema is 'dedicated' and must be omitted or null otherwise.",
        targets=("SqlStageCapabilities",), enforcer="_dedicated_schema_matches_scope",
    ),
    AdvisoryRule(
        id="ADV-CTOR-014", kind="custom", resource="connector",
        prose="A write_unit must declare at least one of rows / bytes.",
        targets=("WriteUnit",), enforcer="_at_least_one_bound",
    ),
    AdvisoryRule(
        id="ADV-CTOR-015", kind="custom", resource="connector",
        prose="An explicit null bulk-load mechanism is refused: absence of the family key is the only 'none'.",
        targets=("SqlBulkLoad",), enforcer="_null_is_not_a_mechanism",
    ),
    # --- type-map -----------------------------------------------------------
    AdvisoryRule(
        id="ADV-TMAP-001", kind="custom", resource="type-map",
        prose="A schemaless or structured native type must not resolve to a scalar canonical type.",
        targets=("TypeMapReadExactRule",), enforcer="_check",
    ),
    AdvisoryRule(
        id="ADV-TMAP-002", kind="custom", resource="type-map",
        prose="A schemaless or structured native pattern must not resolve to a scalar canonical type.",
        targets=("TypeMapReadRegexRule",), enforcer="_check",
    ),
    AdvisoryRule(
        id="ADV-TMAP-003", kind="custom", resource="type-map",
        prose="Every ${name} in the canonical render must name a capture group in the native pattern.",
        targets=("TypeMapReadRegexRule",), enforcer="_check",
    ),
    AdvisoryRule(
        id="ADV-TMAP-004", kind="custom", resource="type-map",
        prose="A native pattern with named captures must not map to a canonical whose parenthesised parameters are all hardcoded, discarding them. The detector keys on the parentheses, so a capture dropped into a canonical carrying none is out of scope.",
        targets=("TypeMapReadRegexRule",), enforcer="_check",
    ),
    AdvisoryRule(
        id="ADV-TMAP-005", kind="custom", resource="type-map",
        prose="A regex read rule's native must compile as an ECMA-262 regex; Python-only (?P…) syntax and otherwise-invalid patterns are rejected.",
        targets=("TypeMapReadRegexRule",), enforcer="_check",
    ),
    AdvisoryRule(
        id="ADV-TMAP-006", kind="custom", resource="type-map",
        prose="A regex read rule's canonical must be a valid (optionally ${name}-templated) Arrow type matched full-string, so a trailing newline is rejected.",
        targets=("TypeMapReadRegexRule",), enforcer="_check",
    ),
    AdvisoryRule(
        id="ADV-TMAP-007", kind="custom", resource="type-map",
        prose="A ${...} placeholder in a canonical render must be well-formed: no empty ${} and no unclosed ${.",
        targets=("TypeMapReadRegexRule",), enforcer="_check",
    ),
    AdvisoryRule(
        id="ADV-TMAP-008", kind="custom", resource="type-map",
        prose="A write exact rule's canonical must satisfy the cross-parameter Arrow bounds (Decimal scale <= precision), and its native DDL render's ${...} placeholders must be well-formed.",
        targets=("TypeMapWriteExactRule",), enforcer="_check",
    ),
    AdvisoryRule(
        id="ADV-TMAP-009", kind="custom", resource="type-map",
        prose="A write regex rule's canonical must compile as an ECMA-262 regex, and its native DDL render's ${...} placeholders must be well-formed.",
        targets=("TypeMapWriteRegexRule",), enforcer="_check",
    ),
    AdvisoryRule(
        id="ADV-TMAP-010", kind="custom", resource="type-map",
        prose="A ${name} capture feeding a canonical parameter position must be unable to match a value that position refuses — a byte width of 0, a unit only a sibling family admits — and where a cross-parameter bound applies (Decimal scale <= precision) that bound resolves against the literal sibling present; a literal in such a bounded position must in turn hold against every value the capture bounding it can match. Two things are left undecided: a position whose admissible values the grammar states as an open pattern rather than a member list (a timezone) is not interrogated, and where a bound carries a placeholder on each side every capture is judged against its own position, so the pair reachable from those captures together is not judged at all.",
        targets=("TypeMapReadRegexRule",), enforcer="_check",
    ),
    # --- pipeline -----------------------------------------------------------
    AdvisoryRule(
        id="ADV-PIPE-002", kind="custom", resource="pipeline",
        prose="schedule.type gates its fields: manual forbids interval/cron, interval requires interval_minutes, cron requires cron_expression.",
        targets=("Schedule",), enforcer="_validate_schedule_fields",
    ),
    AdvisoryRule(
        id="ADV-PIPE-003", kind="custom", resource="pipeline",
        prose="streams must be unique by version-stripped base id.",
        targets=("PipelineAuthored",), enforcer="_validate_streams_unique_base",
    ),
    AdvisoryRule(
        id="ADV-PIPE-004", kind="custom", resource="pipeline",
        prose="An active pipeline must reference at least one stream.",
        targets=("PipelineAuthored",), enforcer="_check_active_requires_streams",
    ),
    # --- data-sync ----------------------------------------------------------
    AdvisoryRule(
        id="ADV-DSYNC-001", kind="custom", resource="data-sync-run-status",
        prose="error is present only for failed/partial runs, and a failed run always carries error.",
        targets=("PipelineRunStatusData",), enforcer="_error_matches_status",
    ),
    AdvisoryRule(
        id="ADV-DSYNC-002", kind="custom", resource="data-sync-run-status",
        prose="message is the fixed customer-safe text PUBLIC_ERROR_MESSAGES maps to code.",
        targets=("PublicRunError",), enforcer="_message_matches_code",
    ),
    # --- shared -------------------------------------------------------------
    AdvisoryRule(
        id="ADV-RETRY-001", kind="custom", resource="shared",
        prose="retry_delay_seconds must be omitted or 0 when max_retries is 0.",
        targets=("RetryErrorHandlingBase",), enforcer="_validate_retry_fields",
    ),
    # === Rules the plugins' prose carried, catalogued here =============
    #
    # Below this line are rules the contract always enforced and never
    # named: relational checks whose enforcer carried no id, and the
    # structural tier, which had no ids at all. Plugin prose restated them
    # instead — an enum's members typed into a table, a pattern spelled
    # out — and the copies drifted from the models and from each other.
    # Grouped by document family, as above.
    # --- api-endpoint document --------------------------------------------
    AdvisoryRule(
        id="ADV-ENDP-032", tier="relational", resource="api-endpoint",
        prose=(
            "A request slot must not carry a direct ref into the per-run "
            "scopes; a per-run value reaches the request only through a "
            "declared param."
        ),
        kind="custom", enforcer="_wiring",
        targets=("ReadOperation", "WriteOperation"),
    ),
    AdvisoryRule(
        id="ADV-ENDP-033", tier="relational", resource="api-endpoint",
        prose=(
            "Every ref and every `${...}` template placeholder in a request "
            "slot must lead with one of the contract's declared resolution "
            "scopes."
        ),
        kind="custom", enforcer="_wiring",
        targets=("ReadOperation", "WriteOperation"),
    ),
    AdvisoryRule(
        id="ADV-ENDP-034", tier="relational", resource="api-endpoint",
        prose=(
            "A `from_input` binding is confined to the request sites where a "
            "record is in scope: it is refused in `request.headers` and "
            "`request.query`, and anywhere in a read operation's request."
        ),
        kind="custom", enforcer="_wiring",
        targets=("ReadOperation", "WriteOperation"),
    ),
    AdvisoryRule(
        id="ADV-ENDP-035", tier="relational", resource="api-endpoint",
        prose=(
            "A write request body's `from_input` value must address the record, "
            "the batch, or one dotted field of the record; a dotted path "
            "through the batch array is refused."
        ),
        kind="custom", enforcer="_wiring",
        targets=("WriteOperation",),
    ),
    AdvisoryRule(
        id="ADV-ENDP-036", tier="structural", resource="api-endpoint",
        prose=(
            "An endpoint document's `endpoint_id` matches the slug pattern "
            "`_EndpointBase.endpoint_id` declares."
        ),
        mechanism="pattern",
        targets=("_EndpointBase",),
        fields=("endpoint_id",),
    ),
    AdvisoryRule(
        id="ADV-ENDP-037", tier="structural", resource="api-endpoint",
        prose=(
            "A predicate object carries exactly one operator key, and that key "
            "selects the branch of the contract's predicate union that shapes "
            "it; a key the union does not tag is not a predicate."
        ),
        mechanism="discriminated_union",
        targets=(
            "PredicateEq", "PredicateNeq", "PredicateLt",
            "PredicateLte", "PredicateGt", "PredicateGte",
            "PredicateExists", "PredicateMissing", "PredicateEmpty",
            "PredicateNotEmpty", "PredicateAnd", "PredicateOr",
            "PredicateNot"
        ),
    ),
    AdvisoryRule(
        id="ADV-ENDP-038", tier="structural", resource="api-endpoint",
        prose=(
            "An endpoint's `replication.supported_methods` names only methods "
            "the vocabulary `Replication.supported_methods` declares, and the "
            "block carries no separate default-method key."
        ),
        mechanism="literal_enum",
        targets=("Replication",),
        fields=("supported_methods",),
    ),
    AdvisoryRule(
        id="ADV-ENDP-039", tier="structural", resource="api-endpoint",
        prose=(
            "A write operation's `idempotency` declares only where the "
            "provider's key is placed, from the placement vocabulary "
            "`Idempotency.location` carries; the key's value is engine-owned "
            "and is never authored."
        ),
        mechanism="literal_enum",
        targets=("Idempotency",),
        fields=("location",),
    ),
    # --- connector document -----------------------------------------------
    AdvisoryRule(
        id="ADV-CTOR-016", tier="structural", resource="connector",
        prose=(
            "An ADBC transport's `driver` names a member of the closed enum "
            "`AdbcTransport.driver` carries; a driver the enum does not name "
            "cannot be declared at all."
        ),
        mechanism="literal_enum",
        targets=("AdbcTransport",),
        fields=("driver",),
    ),
    AdvisoryRule(
        id="ADV-CTOR-017", tier="structural", resource="connector",
        prose=(
            "A `sql_capabilities.bulk_load` family key names a mechanism the "
            "matching field on `SqlBulkLoad` admits — the families' "
            "vocabularies differ, so a mechanism is declarable only under the "
            "family whose field carries it — and a protocol outside them is not "
            "declarable."
        ),
        mechanism="literal_enum",
        targets=("SqlBulkLoad",),
        fields=("sqlalchemy", "adbc"),
    ),
    AdvisoryRule(
        id="ADV-CTOR-018", tier="structural", resource="connector",
        prose=(
            "Every binding in a `url_template` DSN declares an `encoding` from "
            "the closed vocabulary `DsnBinding.encoding` carries, selected for "
            "the URL position the value is substituted into."
        ),
        mechanism="literal_enum",
        targets=("DsnBinding",),
        fields=("encoding",),
    ),
    AdvisoryRule(
        id="ADV-CTOR-019", tier="structural", resource="connector",
        prose=(
            "A `resource_discovery.produces` entry names an artifact kind the "
            "contract's `produces` vocabulary admits, and discovery writes "
            "nothing outside it."
        ),
        mechanism="literal_enum",
        targets=("ResourceDiscovery",),
        fields=("produces",),
    ),
    AdvisoryRule(
        id="ADV-CTOR-020", tier="structural", resource="connector",
        prose=(
            "Each discovery action on `ResourceDiscoveryTriggers` declares its "
            "trigger from the same closed vocabulary, so the actions never "
            "differ in what may trigger them."
        ),
        mechanism="literal_enum",
        targets=("ResourceDiscoveryTriggers",),
        fields=("list_resources", "describe_resource"),
    ),
    AdvisoryRule(
        id="ADV-CTOR-021", tier="structural", resource="connector",
        prose=(
            "A connection-contract input's provisioning, lifecycle phase, "
            "storage location and value type are each drawn from the closed "
            "vocabulary the matching field on `ConnectionContractInput` "
            "declares."
        ),
        mechanism="literal_enum",
        targets=("ConnectionContractInput",),
        fields=("source", "phase", "storage", "type"),
    ),
    AdvisoryRule(
        id="ADV-CTOR-022", tier="structural", resource="connector",
        prose=(
            "A post-auth output's `mode` and `storage` are each drawn from the "
            "closed vocabulary the matching field on `PostAuthOutput` declares; "
            "which pairings are legal is ADV-CTOR-002."
        ),
        mechanism="literal_enum",
        targets=("PostAuthOutput",),
        fields=("mode", "storage"),
    ),
    AdvisoryRule(
        id="ADV-CTOR-023", tier="structural", resource="connector",
        prose=(
            "A connector document's `connector_id` matches the slug pattern "
            "`ConnectorBase.connector_id` declares."
        ),
        mechanism="pattern",
        targets=("ConnectorBase",),
        fields=("connector_id",),
    ),
    AdvisoryRule(
        id="ADV-CTOR-024", tier="structural", resource="connector",
        prose=(
            "A connector's `auth` block declares the children its `type` branch "
            "declares and no others, so an operation belonging to a different "
            "auth type is rejected rather than ignored."
        ),
        mechanism="discriminated_union",
        targets=(
            "ApiKeyAuth", "BasicAuth", "OAuth2AuthorizationCodeAuth",
            "OAuth2ClientCredentialsAuth", "JwtAuth", "DbAuth",
            "CredentialsAuth", "AwsIamAuth", "NoneAuth"
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-025", tier="structural", resource="connector",
        prose=(
            "Every auth operation slot holds an `AuthOperationTemplate`, which "
            "declares exactly the request fields that model names and rejects "
            "any other key."
        ),
        mechanism="closed_object",
        targets=("AuthOperationTemplate",),
    ),
    # --- pipeline document ------------------------------------------------
    AdvisoryRule(
        id="ADV-PIPE-005", tier="structural", resource="pipeline",
        prose=(
            "A pipeline's `schedule.type` is a member of the vocabulary "
            "`Schedule.type` declares, and the chosen type gates which schedule "
            "fields are legal (ADV-PIPE-002)."
        ),
        mechanism="literal_enum",
        targets=("Schedule",),
        fields=("type",),
    ),
    AdvisoryRule(
        id="ADV-PIPE-006", tier="structural", resource="pipeline",
        prose=(
            "A pipeline authored with no scheduling facts omits `schedule.type` "
            "and `schedule.timezone` and takes the defaults `Schedule` declares "
            "for them."
        ),
        mechanism="default",
        targets=("Schedule",),
        fields=("type", "timezone"),
    ),
    # --- stream document --------------------------------------------------
    AdvisoryRule(
        id="ADV-STRM-016", tier="structural", resource="stream",
        prose=(
            "A stream destination's `write.mode` selects the write shape, and "
            "only the conflict-keyed database shape declares `conflict_keys` — "
            "every other shape has no such field to set."
        ),
        mechanism="discriminated_union",
        targets=(
            "DatabaseKeylessWrite", "DatabaseConflictKeyedWrite",
            "ApiWrite"
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-017", tier="structural", resource="stream",
        prose=(
            "A stream's `replication.method` selects the replication branch, "
            "and the branch decides which further fields the block declares."
        ),
        mechanism="discriminated_union",
        targets=("FullRefreshReplication", "IncrementalReplication"),
    ),
]

register(ADVISORY_RULES)
