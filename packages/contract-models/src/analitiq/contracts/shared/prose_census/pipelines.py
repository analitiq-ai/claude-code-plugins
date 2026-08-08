"""Census entries for ``analitiq.contracts.pipelines`` (``config`` and
``data_sync``)."""
from __future__ import annotations

from analitiq.contracts.shared.prose_obligation import (
    ENGINE_CONDUCT,
    ENGINE_OWNED_DEFAULTING,
    ProseObligation,
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    # === pipeline ============================================================
    ProseObligation(
        model="PipelineConnections", descriptive=True,
        prose_hash="3dcc139cd95f",
    ),
    ProseObligation(
        model="Schedule", field="interval_minutes",
        prose_hash="e2b3b4f1946e",
        rule_ids=("RULE-PIPE-002",), structural="a Field ge lower bound",
    ),
    ProseObligation(
        model="Schedule", field="cron_expression",
        prose_hash="d4f394338c5b",
        rule_ids=("RULE-PIPE-002",), structural="Field(pattern=CRON_PATTERN)",
    ),
    ProseObligation(
        model="ErrorHandling", field="retry_delay_seconds",
        prose_hash="b3dadb69ebd7",
        rule_ids=("RULE-RETRY-001",),
        structural="the effective-delay defaulting is _default_retry_delay's",
    ),
    # === data-sync ===========================================================
    ProseObligation(
        model="PipelineRunRequest",
        prose_hash="ce650bdf7ce2",
        structural=(
            "a StrictModel declaring terminate_existing_sync — an unknown "
            "key is rejected, an empty body validates"
        ),
    ),
    ProseObligation(
        model="PipelineRunAcceptedResponse",
        prose_hash="2c6b13f315a0",
        structural=(
            "the closed PipelineRunAcceptedData shape declares invocation_id "
            "and pipeline_id — no job_id field exists"
        ),
    ),
    ProseObligation(
        model="PipelineRunAcceptedResponse", field="data",
        prose_hash="1d96376806d2",
        structural=(
            "`data` is required; the tracking identifiers are "
            "PipelineRunAcceptedData's own required fields"
        ),
    ),
    ProseObligation(
        model="PipelineRunStatusData", field="error", rule_ids=("RULE-DSYNC-001",),
        prose_hash="80a5b57a4a94",
    ),
    ProseObligation(
        model="PublicRunError", rule_ids=("RULE-DSYNC-001",),
        prose_hash="743cc6a95785",
    ),
    ProseObligation(
        model="PublicErrorCode", waiver=ENGINE_CONDUCT,
        prose_hash="572b0067b359",
    ),
    ProseObligation(
        model="PublicRunStatus", waiver=ENGINE_CONDUCT,
        prose_hash="321ed840600a",
    ),
    ProseObligation(
        model="PipelineTerminateData", waiver=ENGINE_CONDUCT,
        prose_hash="aaf019dace56",
    ),
    ProseObligation(
        model="PipelineTerminateData", field="job_id", waiver=ENGINE_CONDUCT,
        prose_hash="c78e8a7fcbed",
    ),
    # === pipelines.config ====================================================
    ProseObligation(
        model="Engine",
        prose_hash="6f88e04462d3",
        structural="Field ge lower bounds on `vcpu` and `memory`",
    ),
    ProseObligation(model="Engine", field="memory", prose_hash="35a829082981", descriptive=True),
    ProseObligation(model="Engine", field="vcpu", prose_hash="8131dd70555c", descriptive=True),
    ProseObligation(model="ErrorHandling", prose_hash="a4ce2afaa40b", descriptive=True),
    ProseObligation(model="ErrorHandling", field="max_retries", prose_hash="aa01cf69353c", descriptive=True),
    ProseObligation(model="ErrorHandling", field="strategy", prose_hash="b08a860cd1d8", descriptive=True),
    ProseObligation(model="Logging", prose_hash="15c570a1d03b", descriptive=True),
    ProseObligation(model="Logging", field="log_level", prose_hash="a39230af1b0e", descriptive=True),
    ProseObligation(model="Logging", field="metrics_enabled", prose_hash="c5ffa43d5ec9", descriptive=True),
    ProseObligation(model="PipelineAuthored", prose_hash="b122e682eeb4", descriptive=True),
    ProseObligation(model="PipelineAuthored", field="connections", prose_hash="acbba787be01", descriptive=True),
    ProseObligation(model="PipelineAuthored", field="description", prose_hash="074711310a68", descriptive=True),
    ProseObligation(
        model="PipelineAuthored", field="display_name",
        prose_hash="d74f13f69917",
        structural=(
            "Field length bounds between `DISPLAY_NAME_MIN` and "
            "`DISPLAY_NAME_MAX` with Field(pattern=NO_EDGE_WHITESPACE_PATTERN); "
            "`_validate_display_name_field` applies `validate_display_name`"
        ),
    ),
    ProseObligation(
        model="PipelineAuthored", field="schema_url",
        prose_hash="8a81a7862154",
        structural="an optional, defaulted field typed `Literal[PIPELINE_SCHEMA_URL]`",
    ),
    ProseObligation(model="PipelineAuthored", field="status", prose_hash="fc5404ea0fb0", descriptive=True),
    ProseObligation(
        model="PipelineAuthored", field="streams",
        prose_hash="9be6c94e9555",
        structural="typed as a list of `NonEmptyStr`",
        rule_ids=("RULE-PIPE-010",),
    ),
    ProseObligation(
        model="PipelineAuthored", field="tags",
        prose_hash="a1f0142a61e3",
        structural=(
            "a Field item cap at `TAGS_MAX` over `TrimmedTag` items; "
            "`_validate_tags_field` applies `validate_tags`"
        ),
    ),
    ProseObligation(
        model="PipelineConnections", field="destinations",
        prose_hash="2b8f07e52c07",
        rule_ids=("RULE-PIPE-001",),
        structural="a Field lower bound keeps the list non-empty; items are `NonEmptyStr`",
    ),
    ProseObligation(
        model="PipelineConnections", field="source",
        prose_hash="561d66c56e11",
        structural="typed `NonEmptyStr`",
    ),
    ProseObligation(
        model="PipelineInput",
        prose_hash="b2dd3a26b063",
        rule_ids=("RULE-PIPE-004",),
        structural=(
            "the extra-forbid `model_config` closes the shape; "
            "`_ACTIVE_REQUIRES_STREAMS_RULE` projects "
            "`_check_active_requires_streams` into the published schema"
        ),
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="PipelineInput", field="pipeline_id",
        prose_hash="2dd1826374b2",
        structural="Field(pattern=UUID_PATTERN) on an optional, defaulted field",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(model="Runtime", prose_hash="8af67520a5a1", descriptive=True),
    ProseObligation(model="Runtime", field="buffer_size", prose_hash="556932e51b99", descriptive=True),
    ProseObligation(model="Schedule", prose_hash="b586be072d56", descriptive=True),
    ProseObligation(
        model="Schedule", field="timezone",
        prose_hash="1e7ed3b35d5c",
        structural=(
            "the `_validate_timezone` single-field validator (membership in "
            "`available_timezones`)"
        ),
    ),
    ProseObligation(model="Schedule", field="type", prose_hash="e628819cbc35", descriptive=True),
    ProseObligation(model="pipelines.config.Batching", prose_hash="7f4ab66ab02c", descriptive=True),
    ProseObligation(model="pipelines.config.Batching", field="batch_size", prose_hash="828d5dd08b74", descriptive=True),
    # === pipelines.data_sync =================================================
    ProseObligation(model="PipelineRunAcceptedData", prose_hash="cd7889e07c2a", descriptive=True),
    ProseObligation(model="PipelineRunAcceptedData", field="invocation_id", prose_hash="8c2c0179de3e", descriptive=True),
    ProseObligation(model="PipelineRunAcceptedData", field="pipeline_id", prose_hash="1589d53c0eb4", descriptive=True),
    ProseObligation(model="PipelineRunAcceptedResponse", field="message", prose_hash="fdee914580b5", descriptive=True),
    ProseObligation(
        model="PipelineRunAcceptedResponse", field="success",
        prose_hash="c84333014ad8",
        structural="typed `Literal[True]`",
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="PipelineRunRequest", field="terminate_existing_sync",
        prose_hash="3eecf8004851",
        structural="the parenthetical default is the field's declared Field default",
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="PipelineRunStatusData",
        prose_hash="c082942eeefb",
        waiver=(
            "wire-shape conduct: dropping absent optionals is the producing "
            "service's serialization (`PipelineRunStatusResponse` body method), "
            "and reading a missing field as not-yet-recorded binds the "
            "consumer — neither is a checkable shape of this document"
        ),
    ),
    ProseObligation(
        model="PipelineRunStatusData", field="finished_at",
        prose_hash="83c624636989",
        structural="Field(pattern=ISO_TS_PATTERN) through the `_iso8601` coercion",
    ),
    ProseObligation(model="PipelineRunStatusData", field="invocation_id", prose_hash="a2eebba30698", descriptive=True),
    ProseObligation(model="PipelineRunStatusData", field="pipeline_id", prose_hash="edabc5ae1687", descriptive=True),
    ProseObligation(model="PipelineRunStatusData", field="records_failed", prose_hash="eac1f4b09afd", descriptive=True),
    ProseObligation(model="PipelineRunStatusData", field="records_processed", prose_hash="b2ec5f36e33e", descriptive=True),
    ProseObligation(model="PipelineRunStatusData", field="records_total", prose_hash="c68e562409f4", descriptive=True),
    ProseObligation(
        model="PipelineRunStatusData", field="started_at",
        prose_hash="e80dea7ab275",
        structural="Field(pattern=ISO_TS_PATTERN) through the `_iso8601` coercion",
    ),
    ProseObligation(
        model="PipelineRunStatusData", field="status",
        prose_hash="2ba9f181955b",
        structural="typed `PublicRunStatus`",
    ),
    ProseObligation(
        model="PipelineRunStatusData", field="submitted_at",
        prose_hash="658f0df37813",
        structural=(
            "Field(pattern=ISO_TS_PATTERN) through the `_iso8601` coercion; "
            "requiredness (no default) makes it always present"
        ),
    ),
    ProseObligation(
        model="PipelineRunStatusResponse",
        prose_hash="d71f31c3f2c0",
        structural=(
            "`success` is typed `Literal[True]`; `data` is required and typed "
            "`PipelineRunStatusData`"
        ),
    ),
    ProseObligation(
        model="PipelineRunStatusResponse", field="data",
        prose_hash="eeea5415ac67",
        structural="required field typed `PipelineRunStatusData`, no default",
    ),
    ProseObligation(model="PipelineRunStatusResponse", field="message", prose_hash="fdee914580b5", descriptive=True),
    ProseObligation(
        model="PipelineRunStatusResponse", field="success",
        prose_hash="c84333014ad8",
        structural="typed `Literal[True]`",
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(model="PipelineTerminateData", field="pipeline_id", prose_hash="dc4ccc0c29ee", descriptive=True),
    ProseObligation(
        model="PipelineTerminateResponse", waiver=ENGINE_CONDUCT,
        prose_hash="48fd2d6b11b7",
    ),
    ProseObligation(
        model="PipelineTerminateResponse", field="data", waiver=ENGINE_CONDUCT,
        prose_hash="b5a04ee65c0b",
    ),
    ProseObligation(model="PipelineTerminateResponse", field="message", prose_hash="fdee914580b5", descriptive=True),
    ProseObligation(
        model="PipelineTerminateResponse", field="success",
        prose_hash="c84333014ad8",
        structural="typed `Literal[True]`",
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="PublicRunError", field="code",
        prose_hash="b95e78013ffb",
        structural="typed `PublicErrorCode`",
    ),
    ProseObligation(
        model="PublicRunError", field="message",
        prose_hash="d486e13aef87",
        rule_ids=("RULE-DSYNC-002",),
        structural="`NonEmptyStr` requiredness",
    ),
)
