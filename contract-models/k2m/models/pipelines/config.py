"""
Pipeline models and validators.

Aligned with the published Analitiq schema documentation (schema v1).
Cross-references:
- the published Analitiq schema documentation (display_name/tags)
- the published Analitiq schema documentation
- the published Analitiq schema documentation (cron wrapper, IANA timezones)
- the published Analitiq schema documentation (closed contract — no `x-*`)
- the published Analitiq schema documentation (draft/active/inactive)

Classes:
- `_PipelineAuthored` — private base carrying authored fields shared by every variant.
- `PipelineConfig` (read-side) — extends the base with optional server-managed
  fields and `extra="allow"` so DDB items round-trip without spurious errors.
- `PipelineInput` (write-side) — strict POST contract; the source of the
  published `pipeline/latest.json`. `_check_authored_top_level` rejects
  server-managed fields and unknown top-level keys.
- `PipelineDocument` — internal persisted-record validator for read paths;
  not published.
- `PipelinePatch` — partial-update body for PATCH routes.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from zoneinfo import available_timezones

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from k2m.models.shared.common import (
    CRON_PATTERN,
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
    validate_display_name as _validate_display_name,
    validate_tags as _validate_tags,
)
from k2m.models.shared.types import UUID_PATTERN
PIPELINE_SCHEMA_URL = schema_url_for("pipeline")

# Underscore aliases for in-module readability.
_DESCRIPTION_MAX = DESCRIPTION_MAX
_DISPLAY_NAME_MIN = DISPLAY_NAME_MIN
_DISPLAY_NAME_MAX = DISPLAY_NAME_MAX
_TAGS_MAX = TAGS_MAX

# Server-managed top-level fields per schema-contract §Server-Managed Fields.
# Clients must omit them on create/update; we reject them on `PipelineInput`
# rather than silently dropping so a misconfigured caller fails loud.
# `pipeline_id` is intentionally excluded so externally-authored pipeline
# documents (publicly managed artifacts) can supply their own UUID through the
# published validation schema.
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

# Authored sub-model base — `extra="forbid"`, no `x-*` allowance. Existing
# class declarations referencing `_XModel` keep working under the new name.
_XModel = StrictModel


class PipelineConnections(_XModel):
    """Connection set available to every stream in the pipeline."""

    source: _NonEmptyStr = Field(
        ...,
        description=(
            "Source connection reference. Typically a versioned connection "
            "ID (e.g. 'uuid_v1'); the schema does not enforce a specific "
            "shape — engines resolve the reference at runtime."
        ),
        examples=["00000000-0000-4000-8000-000000000001_v1"],
    )

    destinations: list[_NonEmptyStr] = Field(
        ...,
        min_length=1,
        description=(
            "Non-empty list of unique destination connection references. "
            "A destination reference may equal `source`. The schema accepts "
            "any non-empty string; engines resolve references at runtime."
        ),
        examples=[["00000000-0000-4000-8000-000000000002_v1"]],
        json_schema_extra={"uniqueItems": True},
    )

    @field_validator("destinations")
    @classmethod
    def _no_duplicate_destinations(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for d in v:
            if d in seen:
                duplicates.append(d)
            seen.add(d)
        if duplicates:
            raise ValueError(
                f"destinations must not contain duplicate connection IDs: "
                f"{sorted(set(duplicates))!r}"
            )
        return v


_SCHEDULE_CONDITIONAL_RULES: dict[str, Any] = {
    "allOf": [
        {
            "if": {"properties": {"type": {"const": "manual"}}, "required": ["type"]},
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["interval_minutes"]},
                        {"required": ["cron_expression"]},
                    ]
                }
            },
        },
        {
            "if": {"properties": {"type": {"const": "interval"}}, "required": ["type"]},
            "then": {
                "required": ["interval_minutes"],
                "not": {"required": ["cron_expression"]},
            },
        },
        {
            "if": {"properties": {"type": {"const": "cron"}}, "required": ["type"]},
            "then": {
                "required": ["cron_expression"],
                "not": {"required": ["interval_minutes"]},
            },
        },
    ],
    "additionalProperties": False,
}


class ScheduleConfig(_XModel):
    """Pipeline schedule. See the published Analitiq schema documentation."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_SCHEDULE_CONDITIONAL_RULES,
    )

    type: Literal["manual", "interval", "cron"] = Field(
        default="manual", description="Schedule type"
    )
    timezone: str = Field(default="UTC", description="IANA timezone name")
    # Plain `int` (not `CoerceInt`) so Pydantic emits `minimum: 1` on the
    # published JSON Schema. `CoerceInt`'s `BeforeValidator` causes the JSON
    # Schema generator to fall back to `ge: 1`, which JSON Schema 2020-12
    # ignores — leaving external validators with a weaker contract than the
    # runtime. DDB Decimal coercion isn't needed here because the field is
    # populated from JSON request bodies, where it always arrives as `int`.
    interval_minutes: int | None = Field(
        default=None, ge=1, description="Positive integer minutes (interval schedule only)"
    )
    cron_expression: str | None = Field(
        default=None,
        pattern=CRON_PATTERN,
        description=(
            "AWS/EventBridge wrapper string matching "
            "'^cron\\(.+\\)$' (cron schedule only; e.g. 'cron(0 6 * * ? *)')"
        ),
    )

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        if v not in available_timezones():
            raise ValueError(
                f"Invalid timezone: {v}. Use IANA timezone names "
                "(e.g., 'UTC', 'America/New_York')."
            )
        return v

    @model_validator(mode="after")
    def _validate_schedule_fields(self) -> "ScheduleConfig":
        if self.type == "manual":
            if self.interval_minutes is not None or self.cron_expression is not None:
                raise ValueError(
                    "schedule.type='manual' must not include interval_minutes or cron_expression"
                )
        elif self.type == "interval":
            if self.interval_minutes is None:
                raise ValueError(
                    "interval_minutes is required when schedule.type is 'interval'"
                )
            if self.cron_expression is not None:
                raise ValueError(
                    "schedule.type='interval' must not include cron_expression"
                )
        else:  # type == "cron"
            if self.cron_expression is None:
                raise ValueError(
                    "cron_expression is required when schedule.type is 'cron'"
                )
            if self.interval_minutes is not None:
                raise ValueError(
                    "schedule.type='cron' must not include interval_minutes"
                )
        return self


class EngineConfig(_XModel):
    """Per-run resource request.

    Minimums (`vcpu>=0.5`, `memory>=1024`) reserve the 0.25 vCPU / 512 MB
    sidecar baseline so the engine container always has at least the runtime
    floor (0.25 vCPU / 512 MB) after subtracting the destination container.
    """

    vcpu: float = Field(default=1.0, ge=0.5, description="vCPU allocation")
    memory: int = Field(default=8192, ge=1024, description="Memory allocation in MB")


class LoggingConfig(_XModel):
    """Runtime logging and metrics defaults."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Logging level"
    )
    metrics_enabled: bool = Field(
        default=True, description="Whether runtime metrics emission is enabled"
    )


class BatchingConfig(_XModel):
    """Pipeline-wide record batching defaults."""

    batch_size: int = Field(default=100, ge=1, le=100_000, description="Records per batch")
    max_concurrent_batches: int = Field(
        default=3,
        ge=1,
        le=100,
        description=(
            "Per-binding cap for each (stream, destination) execution binding "
            "(not a pipeline-wide aggregate ceiling)"
        ),
    )


_ERROR_HANDLING_CONDITIONAL_RULES: dict[str, Any] = {
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


class ErrorHandlingConfig(_XModel):
    """Runtime error handling. Strategy controls behavior after retries are exhausted."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_ERROR_HANDLING_CONDITIONAL_RULES,
    )

    strategy: Literal["fail", "dlq", "skip"] = Field(
        default="dlq", description="Action after retries are exhausted"
    )
    max_retries: int = Field(default=3, ge=0, le=5, description="Retry attempts before strategy")
    retry_delay_seconds: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Non-negative integer delay between retry attempts. Required when "
            "max_retries > 0; must be omitted or 0 when max_retries is 0."
        ),
    )

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
    def _validate_retry_fields(self) -> "ErrorHandlingConfig":
        if self.max_retries == 0 and self.retry_delay_seconds not in (None, 0):
            raise ValueError(
                "retry_delay_seconds must be omitted or 0 when max_retries is 0"
            )
        return self


class RuntimeConfig(_XModel):
    """Pipeline-wide execution defaults."""

    buffer_size: int = Field(default=5000, ge=100, description="Record buffer size")
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    batching: BatchingConfig = Field(default_factory=BatchingConfig)
    error_handling: ErrorHandlingConfig = Field(default_factory=ErrorHandlingConfig)


_VERSIONED_ID_SUFFIX_RE = re.compile(r"_v\d+$")


def _check_streams_unique_base(v: list[str] | None) -> list[str] | None:
    """Reject duplicate stream references.

    For versioned-ID-shaped refs (`<base>_v<digits>`), the dedup key is the
    base (so `<id>_v1` and `<id>_v2` are duplicates). For anything else
    (refs are runtime-resolved, no inherent shape), the dedup key is the
    full string. This avoids false positives on legitimate non-UUID refs
    that happen to contain `_v` followed by non-digits.
    """
    if v is None:
        return v
    seen: set[str] = set()
    duplicates: list[str] = []
    for s in v:
        m = _VERSIONED_ID_SUFFIX_RE.search(s)
        key = s[: m.start()] if m else s
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    if duplicates:
        raise ValueError(
            f"streams must not include duplicate references "
            f"(versioned IDs collapse to base; other refs compared as-is): "
            f"{sorted(set(duplicates))!r}"
        )
    return v


class _PipelineAuthored(BaseModel):
    """Authored pipeline fields shared between the input and persisted models.

    Splitting this out of `PipelineConfig` keeps the published JSON Schema
    (rendered from `PipelineInput`) free of server-managed fields. The runtime
    `_check_authored_top_level` still rejects them on input by name, but they
    no longer surface as accepted-optional properties in the public artifact.
    """

    schema_url: Literal[PIPELINE_SCHEMA_URL] | None = Field(
        default=None,
        alias="$schema",
        description="Pipeline schema URL (optional in API payloads).",
    )

    display_name: str | None = Field(
        default=None,
        min_length=_DISPLAY_NAME_MIN,
        max_length=_DISPLAY_NAME_MAX,
        description="User-facing pipeline label (1-120 chars, no leading/trailing whitespace)",
    )
    description: str | None = Field(
        default=None, max_length=_DESCRIPTION_MAX, description="User-facing summary"
    )
    status: Literal["draft", "active", "inactive"] = Field(
        default="draft", description="Pipeline lifecycle status"
    )
    tags: list[_Tag] | None = Field(
        default=None,
        max_length=_TAGS_MAX,
        description="Grouping/search labels (max 50, each 1-64 chars, unique, trimmed)",
    )

    connections: PipelineConnections = Field(
        ..., description="Source and destination connection references"
    )
    streams: list[_NonEmptyStr] = Field(
        default_factory=list,
        description=(
            "Ordered list of stream references. Typically versioned stream "
            "IDs (e.g. 'uuid_v1'); the schema accepts any non-empty string."
        ),
        json_schema_extra={"uniqueItems": True},
    )
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

    @field_validator("display_name")
    @classmethod
    def _validate_display_name_field(cls, v: str | None) -> str | None:
        return _validate_display_name(v)

    @field_validator("tags")
    @classmethod
    def _validate_tags_field(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)

    @field_validator("streams")
    @classmethod
    def _validate_streams_unique_base(cls, v: list[str]) -> list[str]:
        return _check_streams_unique_base(v)


class PipelineConfig(_PipelineAuthored):
    """Read-side model. Round-trips persisted DDB documents.

    `extra="allow"` lets DDB-internal attributes (`pk`, `sk`, GSI keys) flow
    through without spurious validation errors. Server-managed business fields
    are explicit declarations so consumers can read them typed.

    API write paths use `PipelineInput` / `PipelinePatch` instead, which apply
    the strict `x-*` extension policy and reject server-managed authoring.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    pipeline_id: str | None = Field(
        default=None, description="Stable base pipeline UUID (server-managed)"
    )
    version: int | None = Field(
        default=None, ge=1, description="Pipeline configuration version (server-managed)"
    )
    org_id: str | None = Field(
        default=None, description="Tenant identifier derived from auth context (server-managed)"
    )
    created_at: datetime | None = Field(default=None, description="Creation timestamp (server-managed)")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp (server-managed)")


class PipelineInput(_PipelineAuthored):
    """Strict API input variant — the source of truth for the `pipeline` published JSON Schema.

    Server-managed fields are not declared here, so the published artifact
    does not advertise them as accepted properties. `_check_authored_top_level`
    rejects them on input by name (alongside unknown top-level keys) so a
    misconfigured caller fails loud rather than silent-drop.

    `pipeline_id` is an optional authored identifier (RFC-4122 UUID) so
    externally-authored pipeline definitions can supply their own UUID; the
    service assigns one when the create payload omits it.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pipeline_id: str | None = Field(
        default=None,
        pattern=UUID_PATTERN,
        description=(
            "Pipeline UUID (RFC-4122). Optional in authored definitions so "
            "external publishers can assign their own identifier; "
            "service-assigned on ingest when omitted."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_top_level(cls, data: Any) -> Any:
        return _check_authored_top_level(cls, data)


class PipelineDocument(_PipelineAuthored):
    """Persisted-record variant — internal read-side validator for `GET /pipelines/{id}`.

    Same authored shape as `PipelineInput` plus the server-managed fields the
    service stamps on at create time. NOT published as a JSON Schema; the
    public `pipeline/latest.json` is rendered from `PipelineInput`.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pipeline_id: str = Field(..., description="Stable base pipeline UUID (server-managed).")
    version: int = Field(..., ge=1, description="Pipeline configuration version (server-managed).")
    org_id: str = Field(..., description="Tenant identifier (server-managed).")
    created_at: datetime = Field(..., description="Creation timestamp (server-managed).")
    updated_at: datetime = Field(..., description="Last update timestamp (server-managed).")


class PipelinePatch(BaseModel):
    """Partial-update model for PATCH /pipelines/{id}.

    Authored fields only — server-managed fields are not patchable.

    The `_validate_top_level` before-validator rejects misspelled top-level
    keys and server-managed authoring with a single aggregated message,
    matching `PipelineInput`'s runtime strictness.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    display_name: str | None = Field(
        default=None, min_length=_DISPLAY_NAME_MIN, max_length=_DISPLAY_NAME_MAX
    )
    description: str | None = Field(default=None, max_length=_DESCRIPTION_MAX)
    status: Literal["draft", "active", "inactive"] | None = Field(default=None)
    tags: list[_Tag] | None = Field(default=None, max_length=_TAGS_MAX)
    connections: PipelineConnections | None = Field(default=None)
    streams: list[_NonEmptyStr] | None = Field(default=None)
    schedule: ScheduleConfig | None = Field(default=None)
    engine: EngineConfig | None = Field(default=None)
    runtime: RuntimeConfig | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _validate_top_level(cls, data: Any) -> Any:
        return _check_authored_top_level(cls, data)

    @field_validator("display_name")
    @classmethod
    def _validate_display_name_field(cls, v: str | None) -> str | None:
        return _validate_display_name(v)

    @field_validator("tags")
    @classmethod
    def _validate_tags_field(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)

    @field_validator("streams")
    @classmethod
    def _validate_streams_unique_base(cls, v: list[str] | None) -> list[str] | None:
        return _check_streams_unique_base(v)


def validate_pipeline_config(payload: dict) -> dict:
    """Validate and normalize a pipeline configuration dict.

    Used by `create_pipeline` (`POST /pipelines`). Update/patch routes call
    `validate_pipeline_patch` instead.

    Returns the validated, coerced payload as a JSON-serializable dict using
    public alias names (e.g. `$schema`). Raises `pydantic.ValidationError` on
    invalid payloads.
    """
    config = PipelineInput.model_validate(payload)
    return config.model_dump(exclude_none=True, mode="json", by_alias=True)


def validate_pipeline_patch(payload: dict) -> dict:
    """Validate a partial pipeline update payload.

    Used by `patch_pipeline` (`PATCH /pipelines/{id}`). Create/save routes
    call `validate_pipeline_config` instead.

    Returns only the keys the caller supplied (unset fields are dropped so
    callers cannot accidentally patch attributes back to None). Raises
    `pydantic.ValidationError` on unknown top-level keys or malformed
    sub-models.
    """
    patch = PipelinePatch.model_validate(payload)
    return patch.model_dump(exclude_unset=True, mode="json", by_alias=True)
