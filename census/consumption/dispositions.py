"""The disposition of every field the pinned manifest leaves unread.

One :class:`FieldDisposition` per (model, field) the reachability walk finds
and ``claims`` does not — keyed and grouped by the model that carries the
field, models in sorted qualified-name order and fields in the model's own
declaration order, so an entry sits beside the field list it describes. The guard in ``tests/census`` holds this
tuple to the manifest in each direction: an unread field with no entry fails,
and so does an entry for a field the manifest now claims or no longer
reaches, so a pin bump that starts reading a field retires its entry here
in the same change.

Every ``engine_gap``, ``contract_surplus`` and ``manifest_gap`` reason has
a manifest half and a consequence half; what each may say, and that no check
rests on the second, is "The reason, and its halves" in
``.claude/rules/reachability-dispositions.md`` — the file a reader judges an
entry by.
"""
from __future__ import annotations

from census.consumption.disposition import (
    HUMAN_METADATA,
    UNION_DISCRIMINATOR,
    FieldDisposition,
)

# --- Named reasons shared across entries -------------------------------------

#: The per-kind ``schema_url`` literal.
SCHEMA_URL_LITERAL = (
    "required Literal pinned to the per-kind schema URL: pydantic refuses any "
    "other value, so the engine holds a document already known to be this kind"
)

#: A nested arrow shape declaration.
NESTED_SHAPE_DECLARATION = (
    "nested shape declaration: RULE-STRM-006 and RULE-STRM-007 name what it "
    "must agree with at validation time, and the run lands the destination "
    "shape by dumping the assignment target whole — an opaque model — so no "
    "field of the sub-tree is read by attribute"
)

#: A per-parameter value constraint.
PARAM_VALUE_CONSTRAINT = (
    "an author declaring a value constraint expects a request carrying a value "
    "outside it to be refused before it is sent; the pinned manifest claims no "
    "read of the constraint, so the request goes out with the value the "
    "binding resolved"
)

#: The wire half of a cursor mapping.
CURSOR_MAPPING_WIRE = (
    "an author declaring how the watermark is compared and formatted expects "
    "the incremental request to send it that way; the pinned manifest claims "
    "no read of the wire half of a cursor mapping, so the watermark goes out "
    "as the stored value"
)

#: Write-result extraction.
WRITE_RESPONSE_EXTRACTION = (
    "an author declaring write-result extraction expects the run to judge each "
    "write from the provider's response body — success, error detail, "
    "affected and generated records; the pinned manifest claims no read of "
    "the block, so a write is judged from the HTTP status alone and the "
    "declared expressions are never evaluated"
)

#: Pipeline-level logging and metrics defaults.
RUNTIME_LOGGING = (
    "an author setting a logging level or switching metrics off expects the "
    "run to honour it; the pinned manifest claims no read of the logging "
    "block, so a run logs and emits metrics at the engine's own defaults "
    "whatever the pipeline declares"
)

#: The per-assignment validation error-handling override.
VALIDATION_ERROR_HANDLING_OVERRIDE = (
    "an author declaring a per-assignment error-handling override expects a "
    "validation failure on that assignment to follow it; the pinned manifest "
    "claims no read of the override, so a validation failure follows the "
    "pipeline runtime's error handling — the outcome RULE-STRM-040 tells "
    "authors to expect. This census records removal: the record's "
    "statement already tells authors not to author the block as the policy "
    "and its rationale that the run never consults it — a knob nothing "
    "honours and, as the contract stands, nothing should. The record "
    "leaves the owners of both sides free to adopt instead; that decision "
    "moves these entries to engine_gap"
)

#: The page size of a database pagination variant.
DATABASE_PAGE_SIZE = (
    "the pinned manifest claims no read of it, and the field's own published "
    "description already states that no engine release consumes it. "
    "Removed rather than adopted because "
    "the contract has already told authors the knob is inert — adoption "
    "would honour a value the description promises nothing for; that "
    "sentence is the obligation's only statement, and the prose census "
    "pins it"
)


DISPOSITIONS: tuple[FieldDisposition, ...] = (
    # --- endpoints.ApiEndpointDoc: catalog labels and the kind pin ----------
    FieldDisposition("endpoints.ApiEndpointDoc", "display_name", "authoring_only", HUMAN_METADATA),
    FieldDisposition("endpoints.ApiEndpointDoc", "description", "authoring_only", HUMAN_METADATA),
    FieldDisposition("endpoints.ApiEndpointDoc", "tags", "authoring_only", HUMAN_METADATA),
    FieldDisposition("endpoints.ApiEndpointDoc", "schema_url", "structural", SCHEMA_URL_LITERAL),
    # --- endpoints.DatabaseEndpointDoc: catalog labels and the kind pin -----
    FieldDisposition("endpoints.DatabaseEndpointDoc", "display_name", "authoring_only", HUMAN_METADATA),
    FieldDisposition("endpoints.DatabaseEndpointDoc", "description", "authoring_only", HUMAN_METADATA),
    FieldDisposition("endpoints.DatabaseEndpointDoc", "tags", "authoring_only", HUMAN_METADATA),
    FieldDisposition("endpoints.DatabaseEndpointDoc", "schema_url", "structural", SCHEMA_URL_LITERAL),
    # --- endpoints.DatabaseObject: the descriptive type label ---------------
    FieldDisposition(
        "endpoints.DatabaseObject", "object_type", "authoring_only",
        "descriptive label a person reads in the discovered catalog; "
        "RULE-DBEP-013 forbids execution from branching on it, so the absence "
        "of a read is the rule being kept",
    ),
    # --- endpoints.Param: placement is graded at validation, the label is
    # for people, and the value constraints reach nobody.
    FieldDisposition(
        "endpoints.Param", "location", "contract_surplus",
        "the request slot a binding sits in already states where the value "
        "goes, and the slot and the field state the same placement; "
        "RULE-ENDP-008's location clause names the obligation, the pinned "
        "manifest claims no read of the field, and the field is the copy "
        "that goes. The model validators that read it — the slot agreement, "
        "the style/explode requirement on an array or object query param, "
        "and the refusal of a body param on a GET read — each re-key onto "
        "the binding slot. The style/explode rule also has a declarative "
        "mirror in the published schema, keyed on the wire name of this "
        "field; a schema cannot see the binding slot, so that half is "
        "lost on removal and survives only as the model validator — the "
        "cost the removal accepts",
    ),
    FieldDisposition(
        "endpoints.Param", "required", "engine_gap",
        "an author marking a param required expects a request whose binding "
        "resolves to nothing to be refused; the pinned manifest claims no read "
        "of the flag, so the request goes out with the slot empty",
    ),
    FieldDisposition("endpoints.Param", "description", "authoring_only", HUMAN_METADATA),
    FieldDisposition("endpoints.Param", "enum", "engine_gap", PARAM_VALUE_CONSTRAINT),
    FieldDisposition("endpoints.Param", "format", "engine_gap", PARAM_VALUE_CONSTRAINT),
    FieldDisposition("endpoints.Param", "pattern", "engine_gap", PARAM_VALUE_CONSTRAINT),
    FieldDisposition("endpoints.Param", "minimum", "engine_gap", PARAM_VALUE_CONSTRAINT),
    FieldDisposition("endpoints.Param", "maximum", "engine_gap", PARAM_VALUE_CONSTRAINT),
    FieldDisposition("endpoints.Param", "min_length", "engine_gap", PARAM_VALUE_CONSTRAINT),
    FieldDisposition("endpoints.Param", "max_length", "engine_gap", PARAM_VALUE_CONSTRAINT),
    FieldDisposition("endpoints.Param", "min_items", "engine_gap", PARAM_VALUE_CONSTRAINT),
    FieldDisposition("endpoints.Param", "max_items", "engine_gap", PARAM_VALUE_CONSTRAINT),
    FieldDisposition(
        "endpoints.Param", "operators", "authoring_only",
        "read by the registry service's on-save comparison of a stream's "
        "filter operators against the subset this param declares — the "
        "consumer RULE-STRM-026's rationale describes, beside the "
        "obligation its statement names on the filter; the pinned manifest claims no "
        "read of the set, so the run sends the filter with whatever operator "
        "the stream declares",
    ),
    # --- endpoints.Replication: the method set the endpoint supports --------
    FieldDisposition(
        "endpoints.Replication", "supported_methods", "engine_gap",
        "an author naming the methods an endpoint supports expects a stream "
        "selecting another to be refused (RULE-STRM-025 names the "
        "obligation); the pinned manifest claims no read of the set, "
        "so the stream's method runs whether or not the endpoint declared it",
    ),
    # --- endpoints.ResponseExtraction: named metadata extractions -----------
    FieldDisposition(
        "endpoints.ResponseExtraction", "metadata", "engine_gap",
        "an author declaring named metadata extractions expects each to be "
        "evaluated against the response and exposed under the response scope "
        "(RULE-ENDP-023 names the obligation on the keys); the pinned manifest "
        "claims no read of the block, so the declared key is never populated",
    ),
    # --- endpoints.SingleCursorMapping: cursor_field and param are read,
    # the wire half is not.
    FieldDisposition(
        "endpoints.SingleCursorMapping", "operator", "contract_surplus",
        "a required field the pinned manifest claims no read of: the single "
        "form is read except for operator and format, so a required field the "
        "read path ignores misleads in a form that otherwise works — unlike "
        "the windowed form, unread as a whole and dispositioned as one gap. "
        "Removed rather than adopted because neither side needs the value: "
        "the read runs the single form without it, and the document has no "
        "other use for it — yet it is required, so every document must state "
        "a comparison the run does not apply. The check that reads it, "
        "the refusal of a cursor mapping mixing the single and windowed "
        "forms, detects the single form by the presence of param and "
        "operator, and re-keys onto param alone",
    ),
    FieldDisposition("endpoints.SingleCursorMapping", "format", "engine_gap", CURSOR_MAPPING_WIRE),
    # --- endpoints.WindowCursorMapping: the whole windowed form is unread ---
    FieldDisposition(
        "endpoints.WindowCursorMapping", "cursor_field", "engine_gap",
        "an author declaring a windowed cursor mapping expects an incremental "
        "read bounded at each end by the watermark; the pinned manifest "
        "claims no read of any field of the windowed form, so the document "
        "validates and the read runs as though no cursor mapping were declared",
    ),
    FieldDisposition(
        "endpoints.WindowCursorMapping", "start_param", "engine_gap",
        "an author naming the window's start param expects the watermark sent "
        "under it; the pinned manifest claims no read of the windowed form, so "
        "the param is never filled",
    ),
    FieldDisposition(
        "endpoints.WindowCursorMapping", "end_param", "engine_gap",
        "an author naming the window's end param expects the run's upper bound "
        "sent under it; the pinned manifest claims no read of the windowed "
        "form, so the param is never filled",
    ),
    FieldDisposition("endpoints.WindowCursorMapping", "start_operator", "engine_gap", CURSOR_MAPPING_WIRE),
    FieldDisposition("endpoints.WindowCursorMapping", "end_operator", "engine_gap", CURSOR_MAPPING_WIRE),
    FieldDisposition("endpoints.WindowCursorMapping", "format", "engine_gap", CURSOR_MAPPING_WIRE),
    # --- endpoints.WriteError / WriteResponse / WriteOperation.response:
    # the write-result extraction block, unread as a whole.
    FieldDisposition("endpoints.WriteError", "code", "engine_gap", WRITE_RESPONSE_EXTRACTION),
    FieldDisposition("endpoints.WriteError", "message", "engine_gap", WRITE_RESPONSE_EXTRACTION),
    FieldDisposition("endpoints.WriteError", "details", "engine_gap", WRITE_RESPONSE_EXTRACTION),
    FieldDisposition(
        "endpoints.WriteOperation", "conflict_keys", "engine_gap",
        "an author naming the upsert's conflict keys expects the run to match "
        "on them — at least to reject a batch whose records collide on the "
        "key (RULE-ENDP-014 and RULE-ENDP-019 name the obligation on the "
        "declaration); the pinned manifest claims no read of the list, so "
        "the request carries only what its template spells",
    ),
    FieldDisposition("endpoints.WriteOperation", "response", "engine_gap", WRITE_RESPONSE_EXTRACTION),
    FieldDisposition("endpoints.WriteResponse", "success_when", "engine_gap", WRITE_RESPONSE_EXTRACTION),
    FieldDisposition("endpoints.WriteResponse", "error", "engine_gap", WRITE_RESPONSE_EXTRACTION),
    FieldDisposition("endpoints.WriteResponse", "affected_records", "engine_gap", WRITE_RESPONSE_EXTRACTION),
    FieldDisposition("endpoints.WriteResponse", "generated_keys", "engine_gap", WRITE_RESPONSE_EXTRACTION),
    FieldDisposition("endpoints.WriteResponse", "metadata", "engine_gap", WRITE_RESPONSE_EXTRACTION),
    # --- pipelines.config.Logging / Runtime.logging -------------------------
    FieldDisposition("pipelines.config.Logging", "log_level", "engine_gap", RUNTIME_LOGGING),
    FieldDisposition("pipelines.config.Logging", "metrics_enabled", "engine_gap", RUNTIME_LOGGING),
    FieldDisposition("pipelines.config.Runtime", "logging", "engine_gap", RUNTIME_LOGGING),
    # --- stream.ArrowFieldSpec: the JSON-container shape under a constant or
    # an assignment target. Under AssignmentTarget (opaque) it is dumped
    # whole into the destination schema; under ConstantValue the literal is
    # the payload and RULE-STRM-006 / RULE-STRM-007 grade the shape against
    # it at validation time.
    FieldDisposition("stream.ArrowFieldSpec", "arrow_type", "authoring_only", NESTED_SHAPE_DECLARATION),
    FieldDisposition("stream.ArrowFieldSpec", "nullable", "authoring_only", NESTED_SHAPE_DECLARATION),
    FieldDisposition("stream.ArrowFieldSpec", "properties", "authoring_only", NESTED_SHAPE_DECLARATION),
    FieldDisposition("stream.ArrowFieldSpec", "items", "authoring_only", NESTED_SHAPE_DECLARATION),
    # --- stream.ConnectionEndpointRef: the locator beside the derived handle
    FieldDisposition(
        "stream.ConnectionEndpointRef", "database_object", "engine_gap",
        "an author supplying the verbatim locator expects it to be the "
        "identity the run resolves (RULE-DBEP-007 and RULE-STRM-003 name the "
        "obligation); the pinned manifest claims no read "
        "of database_object, so the run resolves the derived handle and the "
        "locator the stream carries is never consulted",
    ),
    # --- stream.ConstantAssignmentValue: the AssignmentValue tag ------------
    FieldDisposition("stream.ConstantAssignmentValue", "kind", "structural", UNION_DISCRIMINATOR),
    # --- stream.ConstantValue: value is read; the declared type and shape are
    # what RULE-STRM-007 grades the value against, and the cast the run
    # applies is the assignment target's arrow_type, which is claimed.
    FieldDisposition(
        "stream.ConstantValue", "arrow_type", "authoring_only",
        "RULE-STRM-007 grades the literal against the declared type at "
        "validation time; the type the run casts to is the assignment "
        "target's, which the manifest claims",
    ),
    FieldDisposition(
        "stream.ConstantValue", "properties", "authoring_only",
        "RULE-STRM-007 grades the literal against the declared inner shape at "
        "validation time; the shape the run lands is the assignment target's, "
        "dumped whole",
    ),
    FieldDisposition(
        "stream.ConstantValue", "items", "authoring_only",
        "RULE-STRM-007 grades the literal against the declared inner shape at "
        "validation time; the shape the run lands is the assignment target's, "
        "dumped whole",
    ),
    # --- stream.FullRefreshReplication: a window with no watermark ----------
    FieldDisposition(
        "stream.FullRefreshReplication", "safety_window_seconds", "contract_surplus",
        "an author declaring a late-arrival window on a full refresh expects "
        "an overlap; the pinned manifest claims no read of "
        "safety_window_seconds on the full-refresh variant, and a full "
        "refresh has no cursor to overlap, so the value is meaningless where "
        "it sits. The field is declared on the shared replication base and "
        "inherited by each variant, so removal here is moving it off the base "
        "onto the incremental variant, which stops the full-refresh resource "
        "declaring it; RULE-STRM-039 names the obligation the window carries "
        "where it stays",
    ),
    # --- stream.KeysetDatabasePagination / OffsetDatabasePagination: the
    # tag selects the shape; the size reaches nobody, as the field's own
    # description already states.
    FieldDisposition("stream.KeysetDatabasePagination", "page_size", "contract_surplus", DATABASE_PAGE_SIZE),
    FieldDisposition("stream.KeysetDatabasePagination", "type", "structural", UNION_DISCRIMINATOR),
    FieldDisposition("stream.OffsetDatabasePagination", "page_size", "contract_surplus", DATABASE_PAGE_SIZE),
    FieldDisposition("stream.OffsetDatabasePagination", "type", "structural", UNION_DISCRIMINATOR),
    # --- stream.StreamSource: the stream-owned identity hint ----------------
    FieldDisposition(
        "stream.StreamSource", "primary_keys", "manifest_gap",
        "the pinned manifest claims no read of primary_keys; the engine "
        "reports reading it through the resolved source's dict, a read the "
        "manifest's extractor does not attribute to a field, so the report "
        "is the engine's and the artifact says nothing — the manifest "
        "claiming the read is what retires this entry",
    ),
    # --- stream.StreamValidationErrorHandling / Validation.error_handling ---
    FieldDisposition("stream.StreamValidationErrorHandling", "strategy", "contract_surplus", VALIDATION_ERROR_HANDLING_OVERRIDE),
    FieldDisposition("stream.StreamValidationErrorHandling", "max_retries", "contract_surplus", VALIDATION_ERROR_HANDLING_OVERRIDE),
    FieldDisposition("stream.StreamValidationErrorHandling", "retry_delay_seconds", "contract_surplus", VALIDATION_ERROR_HANDLING_OVERRIDE),
    FieldDisposition("stream.Validation", "error_handling", "contract_surplus", VALIDATION_ERROR_HANDLING_OVERRIDE),
)
