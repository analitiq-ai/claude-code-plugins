"""
Shared type aliases and validators for Pydantic models.
"""
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (  # noqa: F401  (StrictInt/StrictFloat are re-exported)
    AfterValidator,
    BeforeValidator,
    Field,
    StrictFloat,
    StrictInt,
    StringConstraints,
)

# Identifier forms: a bare UUID, or a UUID with a `_v{n}` version suffix. Both
# patterns are declared in `_identity` alongside the runtime checks that compile
# them, and re-exported here: surfacing them via `StringConstraints` makes
# JSON-Schema consumers reject exactly the payloads the runtime validator does.
from analitiq.contracts._identity import (  # noqa: F401
    UUID_PATTERN,
    VERSIONED_ID_PATTERN,
    _is_valid_uuid,
    parse_entity_id,
    validate_versioned_id,
)

# Endpoint-schema snapshot identifier (`sha256:<64 hex>`), as computed at
# schema-materialization time and echoed by discovery.
SCHEMA_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"

# Calendar date (`YYYY-MM-DD`) — the wire form of the metrics request window
# (`date_from`/`date_to`). A coarse shape gate only; calendar validity (real
# month/day ranges) is enforced where the value is parsed into a date.
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

# Run-state rows store `str(datetime)` (space separator); the wire contracts
# promise ISO 8601 (`T` separator). The pattern pins the normalized prefix
# only — fractional seconds and UTC offsets vary across writers.
# Shared by every contract that surfaces run timestamps (pipeline-run-history
# `start_ts`/`stop_ts`, pipeline-read `last_run_ts`).
ISO_TS_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"


def to_iso8601(value: Any) -> Any:
    """Normalize a stored run timestamp to ISO 8601 (`T` separator).

    Intended as a `mode="before"` validator so the declarative
    `ISO_TS_PATTERN` constraint validates the normalized value; non-strings
    pass through to fail type validation with the precise error.
    """
    if isinstance(value, str):
        return value.replace(" ", "T")
    return value


def validate_uuid(value: str) -> str:
    """Validate that value is a valid UUID."""
    if not _is_valid_uuid(value):
        raise ValueError(f"Invalid UUID format: {value}")
    return value


def validate_versioned_uuid(value: str) -> str:
    """Validate that value is a valid UUID, optionally with version suffix (uuid_v1)."""
    base_id, _ = parse_entity_id(value)
    if not _is_valid_uuid(base_id):
        raise ValueError(f"Invalid UUID format: {value}")
    return value


# --- Strict numeric aliases -------------------------------------------------
#
# The strict-numeric policy: a numeric contract field is spelled with one of
# these, never bare `int` / `float`.
#
# Pydantic's lax mode coerces on the way in — `true` becomes 1, `"50"` becomes
# 50 — while the rendered JSON Schema says `type: integer` and rejects both. A
# bare `int` therefore makes this package accept documents that every external
# consumer of the published schema refuses, and `true -> 1` does it silently and
# with the wrong value. `Strict()` closes that; the bound rides in the alias so
# "positive integer" is named once rather than respelled `ge=1` / `gt=0` per
# field.
#
# `Strict()` costs an asymmetry in the other direction: JSON Schema's
# `type: integer` matches a zero-fraction float, so `50.0` passes the published
# schema and fails here. Kept, because the property the plugins rely on is that
# a document this package accepts is always one the schema accepts, and that
# direction still holds. Which fields the gap reaches is not written down
# anywhere — a list would rot on the next field. `test_strict_numeric_policy.py`
# measures it instead, and reads its own measurement, so the arm that watches
# for the gap INVERTING cannot go quiet by finding nothing.
#
# The gap is tolerable on an authoring gate, where the author controls the
# spelling. It is not tolerable where a model READS a producer's document, so
# `CoerceInt` below opts out of that half.
#
# Schema-neutral: pydantic emits `type: integer` with or without `Strict()`, and
# the bound is an ordinary `Field(ge=...)`.
#
# `StrictInt` / `StrictFloat` are pydantic's own; they are re-exported above so
# every contract module reaches the whole vocabulary through one import.
# `StrictFloat` accepts an int (as `type: number` does) and refuses `bool`/`str`.

# Integer ≥ 0 — a count, an offset, a window that may legitimately be zero.
StrictNonNegativeInt = Annotated[StrictInt, Field(ge=0)]

# Integer ≥ 1 — a size, a step, a limit for which zero is meaningless.
StrictPositiveInt = Annotated[StrictInt, Field(ge=1)]


def _narrow_integral_number(value: Any) -> Any:
    """Narrow an integral `Decimal` / `float` to `int`; pass everything else on.

    A field spelled `CoerceInt` is written by a producer and read here, so the
    alias has to accept every spelling a producer legitimately writes a whole
    number in:

    - a driver hands a numeric column back as `Decimal`;
    - a JSON producer serialises a computed count as `1500.0` — which the
      published schema accepts, because JSON Schema's `type: integer` matches
      any number with a zero fractional part.

    Exactly those spellings convert, and only while the value is integral.
    Widening to anything `int()` happens to swallow would re-open the `"50"` /
    `True` coercion the strict aliases above close. Narrowing to `Decimal`
    alone would make this package refuse a document the published schema calls
    valid — tolerable where the model is the authoring gate and the author
    picks the spelling, not on a field the model READS off a producer.

    A non-integral value converts to nothing and is left for `StrictInt` to
    reject, rather than being silently truncated to a wrong count.
    """
    if isinstance(value, Decimal):
        if value.is_finite() and value == value.to_integral_value():
            return int(value)
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# An integer field a producer writes and this package reads: additionally
# accepts the integral `Decimal` a driver returns and the `1500.0` a JSON
# producer emits. Strict against `"50"` / `True` like every alias above.
CoerceInt = Annotated[StrictInt, BeforeValidator(_narrow_integral_number)]

# Plain UUID string
UuidStr = Annotated[
    str, StringConstraints(pattern=UUID_PATTERN), AfterValidator(validate_uuid)
]

# UUID with optional version suffix (uuid_v1)
VersionedUuidStr = Annotated[str, AfterValidator(validate_versioned_uuid)]

# Versioned ID (`{uuid}_v{n}`) — version suffix required
VersionedId = Annotated[
    str,
    StringConstraints(pattern=VERSIONED_ID_PATTERN),
    AfterValidator(validate_versioned_id),
]

# Alias used in pipeline connections
ConnectionId = UuidStr

# Calendar-date string (`YYYY-MM-DD`)
DateStr = Annotated[str, StringConstraints(pattern=DATE_PATTERN)]
