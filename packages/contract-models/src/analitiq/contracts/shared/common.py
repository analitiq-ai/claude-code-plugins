"""
Shared constants, helpers, and base models for authored Pydantic schemas.

All five authored resources (connector, connection, pipeline, stream,
endpoint) share the same slug/semver grammar and the same display-name and
tags rules. Keeping the implementation here removes drift risk: a change to
the id, display-name, or tag policy lands in one place.

Authored contracts are CLOSED: no `x-*` keys are allowed at any authored
object level. Each model declares exactly what an author may write, so
anything Analitiq assigns is not merely rejected — it cannot be expressed.
"""
from __future__ import annotations

import os
import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from analitiq.contracts.shared.types import StrictInt, StrictNonNegativeInt

# --- Schema URL base --------------------------------------------------------
#
# `DOMAIN` is the canonical environment variable the deploy stamps into every
# runtime, and that local / CI rendering sources from the deploy
# configuration. Required — no
# fallback; an unset `DOMAIN` is a deploy / dev-env misconfiguration that
# should fail loud at import time rather than silently pin per-resource
# `Literal[$schema]` fields to a wrong domain.
SCHEMA_DOMAIN = os.environ["DOMAIN"]
SCHEMA_BASE_URL = f"https://schemas.{SCHEMA_DOMAIN}"


def schema_url_for(resource: str) -> str:
    """Build the published schema URL for a resource (e.g. `pipeline`, `api-endpoint`)."""
    return f"{SCHEMA_BASE_URL}/{resource}/latest.json"


def schema_url_pattern(resource: str) -> str:
    """Regex matching the published schema URL for `resource` on any environment host.

    The `$schema` a document advertises is an informational pointer that must
    stay valid across environments. A connector authored against the canonical
    `https://schemas.analitiq.ai/<resource>/latest.json` URL is copied verbatim
    into per-run bundles and validated by the engine against the *environment's*
    schema (served at `schemas.analitiq.work` / `.dev` / `.ai`). Pinning the
    field to a single host (a `Literal`) makes a `.ai`-authored document fail
    validation against the `.work`/`.dev` schema, so the published contract
    accepts any `schemas.analitiq.<tld>` host instead.
    """
    return rf"^https://schemas\.analitiq\.[a-z]+/{re.escape(resource)}/latest\.json$"


# --- Patterns ----------------------------------------------------------------


# Identifier slug shared by `connector_id` and `endpoint_id`: lowercase
# alphanumeric, `_`/`-` inside, must not start with a separator. One grammar,
# so a change to the id policy lands in one place.
SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"

# SemVer 2.0.0, including optional prerelease and build-metadata parts.
SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

CRON_PATTERN = r"^cron\(.+\)$"


# --- Length constants -------------------------------------------------------

DISPLAY_NAME_MIN = 1
DISPLAY_NAME_MAX = 120
DESCRIPTION_MAX = 2000
TAG_MIN_LEN = 1
TAG_MAX_LEN = 64
TAGS_MAX = 50


# Anchored "no leading/trailing whitespace" — the declarative mirror of
# `validate_display_name` / `validate_tags` for schemas that must carry the
# constraint on the wire (request contracts, where the FE validates a payload
# BEFORE sending and the contract must never approve what the gate rejects).
# `[\s\S]` instead of `.` so interior newlines don't defeat the anchors.
# The mirror is exact for all realistically-authorable input; ECMA `\s` and
# Python's `str.strip()` disagree only on U+001C-001F/U+0085 (FE-side
# under-reject; the imperative gate validators stay authoritative) and
# U+FEFF (benign FE-side over-reject).
NO_EDGE_WHITESPACE_PATTERN = r"^\S(?:[\s\S]*\S)?$"


# --- Annotated newtypes -----------------------------------------------------

Tag = Annotated[
    str, StringConstraints(min_length=TAG_MIN_LEN, max_length=TAG_MAX_LEN)
]

# Tag for request-contract surfaces: same bounds as `Tag` plus the trim
# constraint declared so it renders into the JSON contract. Read models keep
# plain `Tag` (their values already passed the write gate).
TrimmedTag = Annotated[
    str,
    StringConstraints(
        min_length=TAG_MIN_LEN,
        max_length=TAG_MAX_LEN,
        pattern=NO_EDGE_WHITESPACE_PATTERN,
    ),
]

# For cross-artifact references whose shape is owned by the runtime (pipeline
# stream/destination IDs, stream connection refs, etc.). Rejects empty and
# whitespace-only strings; engines resolve the actual shape at runtime.
NonEmptyStr = Annotated[
    str, StringConstraints(min_length=1, pattern=r"\S")
]


# --- Field-shape validators -------------------------------------------------

def validate_display_name(v: str | None) -> str | None:
    """Reject leading/trailing whitespace beyond the declarative length checks."""
    if v is None:
        return v
    if v != v.strip():
        raise ValueError("display_name must not have leading or trailing whitespace")
    return v


def validate_tags(v: list[str] | None) -> list[str] | None:
    """Reject whitespace-padded tags and duplicates; per-item length is declarative."""
    if v is None:
        return v
    seen: set[str] = set()
    duplicates: list[str] = []
    for tag in v:
        if tag != tag.strip():
            raise ValueError("tags must not have leading or trailing whitespace")
        if tag in seen:
            duplicates.append(tag)
        seen.add(tag)
    if duplicates:
        raise ValueError(
            f"tags must not contain duplicates: {sorted(set(duplicates))!r}"
        )
    return v


# --- Strict base for authored sub-models -----------------------------------

class ParseOnly:
    """The parse-only policy: an instance only ever comes out of a validator.

    A plain mixin rather than a base model, so the one statement of the policy
    also reaches the root model that cannot inherit :class:`StrictModel`.
    It closes each route that would otherwise produce or alter an instance
    without running the validators:

    - ``frozen=True`` makes ``__setattr__`` raise, so no field can be rebound
      to a value its validators never saw;
    - ``model_construct`` and ``model_copy(update=...)`` write straight into
      the instance dict, frozen or not, and are refused here.

    The refusals point the caller at ``model_validate``: a changed document is
    built by parsing one, so the cross-field rules run on the result. Inside a
    ``mode="after"`` validator, :func:`set_derived_field` is the one sanctioned
    write.

    The freeze is pydantic's, and therefore shallow: it binds a field to the
    value the validator accepted, not the contents of a list or dict that
    value holds. In-place mutation of such a container is outside what this
    policy reaches.
    """

    model_config = ConfigDict(frozen=True)

    @classmethod
    def model_construct(cls, *args: Any, **values: Any) -> Any:
        """Refuse the constructor whose whole purpose is skipping validation."""
        raise TypeError(
            f"{cls.__name__}.model_construct skips every validator, so it can "
            f"build a document the contract rejects. Parse instead: "
            f"{cls.__name__}.model_validate(...)."
        )

    def model_copy(
        self, *, update: dict[str, Any] | None = None, deep: bool = False
    ) -> Any:
        """Copy freely; refuse the `update=` that writes past the validators."""
        if update:
            raise TypeError(
                f"{type(self).__name__}.model_copy(update=...) writes the new "
                f"values straight into the copy without validating them. Parse "
                f"the changed document instead: "
                f"{type(self).__name__}.model_validate(model.model_dump(...) | changes)."
            )
        return super().model_copy(deep=deep)


#: A media type's shape, as RFC 9110 writes one: a type and a subtype of
#: token characters, optionally followed by parameters. It constrains the
#: SHAPE and deliberately not the vocabulary — `application/vnd.api+json`
#: passes — because which media types a provider accepts is a provider fact
#: no contract here owns. What it refuses is a value that is not a media type
#: at all, the empty string among them: absent and "declared as nothing" are
#: the same request to a reader and different values to a resolver.
MEDIA_TYPE_PATTERN = (
    r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+/[A-Za-z0-9!#$%&'*+.^_`|~-]+ *(;.*)?$"
)

MediaType = Annotated[str, StringConstraints(pattern=MEDIA_TYPE_PATTERN)]


class StrictModel(ParseOnly, BaseModel):
    """Base for authored sub-models. Rejects all unknown keys, and is parse-only.

    `x-*` extension keys are NOT allowed; the authored contract is closed.
    Provider extensions must use first-class fields rather than `x-*`
    smuggling.

    The parse-only policy is `ParseOnly`, mixed in here: a field a validator
    checked cannot be rebound afterwards, and the constructors that skip the
    validators are refused, so a caller that needs a changed document builds
    one — ``model_validate(model.model_dump(...) | changes)`` — and the
    validators run again on the result. That policy reaches every contract
    model through this base, and it reaches as far as the mixin says it does:
    a list or dict a field holds can still be mutated in place, which no
    config setting prevents.
    """

    model_config = ConfigDict(extra="forbid")


def set_derived_field(model: BaseModel, field: str, value: Any) -> None:
    """Write a value a `mode="after"` validator derived from validated input.

    Contract models are frozen, so a validator cannot assign to `self`. This is
    the ONE sanctioned way past that, and only for a value that is a pure
    function of what the author already wrote — never for accepting caller
    input, which must go through `model_validate` so the validators run.

    It deliberately leaves `__pydantic_fields_set__` alone: a derived value is
    not author input, and an `exclude_unset` dump must keep saying so. Every
    derived field re-derives on re-parse, so such a dump still round-trips.
    """
    object.__setattr__(model, field, value)


# --- Shared retry/error-handling behavior ----------------------------------

# JSON-Schema conditional rule shared by the pipeline and stream error-handling
# blocks: when `max_retries == 0`, `retry_delay_seconds` must be 0 or null.
# Defined once so the two blocks' published schemas cannot drift.
_RETRY_ERROR_HANDLING_CONDITIONAL_RULES: dict[str, Any] = {
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


# The retry contract's field types, named once. The pipeline block re-declares
# a field to attach a public description; without a shared annotation that
# re-declaration is a second hand-maintained copy of the bounds, which is
# exactly the drift this base class exists to prevent.
RetryAttempts = Annotated[StrictInt, Field(ge=0, le=5)]
RetryDelaySeconds = StrictNonNegativeInt

# Records per batch, named once for the same reason. The stream-level field is
# an OVERRIDE of the pipeline-level one, so the override has to admit the same
# set of values as the field it overrides — otherwise a document valid at the
# stream level describes a batch size the pipeline default could never have
# taken. Spelling the bounds at each site made that agreement a thing a human
# remembers.
BatchSize = Annotated[StrictInt, Field(ge=1, le=100_000)]


class RetryErrorHandlingBase(StrictModel):
    """Shared error-handling contract for the pipeline and stream blocks.

    Owns the whole `max_retries` / `retry_delay_seconds` retry contract — the
    field bounds, the defaulting behavior, the cross-field rule, and the shared
    JSON-Schema conditional rules — so the two blocks cannot drift. A subclass
    re-declares a field only to attach a public description (the pipeline block
    documents its fields; the stream block does not); the stream block inherits
    the fields verbatim.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_RETRY_ERROR_HANDLING_CONDITIONAL_RULES,
    )

    strategy: Literal["fail", "dlq", "skip"] = Field(default="dlq")
    max_retries: RetryAttempts = Field(default=3)
    retry_delay_seconds: RetryDelaySeconds | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_retry_fields(self) -> "RetryErrorHandlingBase":
        if self.max_retries == 0 and self.retry_delay_seconds not in (None, 0):
            raise ValueError(
                "retry_delay_seconds must be omitted or 0 when max_retries is 0"
            )
        return self

    @model_validator(mode="after")
    def _default_retry_delay(self) -> "RetryErrorHandlingBase":
        # Fill the effective default here, never by mutating the input dict in a
        # `mode="before"` validator: that marks the key as provided, corrupting
        # the one signal consumers use to tell author-set from defaulted.
        # `retry_delay_seconds is None` means the author omitted it (or sent
        # null); `set_derived_field` writes without recording the assignment, so
        # the injected default cannot read as author input. The
        # `0 if max_retries == 0 else 5` value is consistent with the cross-field
        # rule above regardless of validator order (an author-supplied delay
        # under `max_retries == 0` is rejected there; the only value ever
        # injected for that case is 0).
        if self.retry_delay_seconds is None:
            set_derived_field(
                self, "retry_delay_seconds", 0 if self.max_retries == 0 else 5
            )
        return self


# --- Read-contract corrupted-row placeholders --------------------------------


def validation_error_summary(e: ValidationError) -> str:
    """Client-safe one-line summary of a pydantic ValidationError.

    `str(e)` embeds `input_value=...` — the offending row's contents, which
    for connections can include raw secret material persisted by a buggy
    writer (the exact row class that fails read-contract validation). This
    summary keeps loc + msg only, so it is safe for wire `error` fields,
    5xx messages, and server logs.
    """
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in e.errors(include_url=False, include_input=False)
    )


class CorruptedPlaceholderBase(StrictModel):
    """Shared base for the read contracts' corrupted-row placeholders.

    Owns the `_corrupted` discriminator, the client-safe `error` reason, and
    the serialization rule: placeholders follow the same absent-never-null
    wire policy as healthy records — an identity field that could not be
    read is OMITTED, not null. This keeps a placeholder's shape identical
    whether it ships top-level (a degraded list row) or nested inside
    another record's sidecar (where the parent's `exclude_none` dump would
    strip nulls anyway). `CorruptedPipelinePlaceholder` overrides `wire()`
    for its legacy null-stuffed shape.

    Resource placeholders subclass this with their identity fields.
    """


    corrupted: Literal[True] = Field(
        alias="_corrupted",
        description="Corrupted-row discriminator — REQUIRED on every placeholder.",
    )
    error: str = Field(..., description="Client-safe reason the row was degraded.")

    def wire(self) -> dict:
        """Placeholder wire payload — unreadable identity fields are omitted."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    @classmethod
    def from_validation_error(cls, e: ValidationError, **identity: Any):
        """Build a placeholder from a read-contract ValidationError.

        Sanitizes the reason via `validation_error_summary` (never `str(e)`)
        and null-coalesces non-string identity values so a degrade path can
        never crash on a row whose id field itself is corrupt.
        """
        safe_identity = {
            k: (v if isinstance(v, str) else None) for k, v in identity.items()
        }
        return cls(
            corrupted=True,
            error=f"read-contract validation failed: {validation_error_summary(e)}",
            **safe_identity,
        )