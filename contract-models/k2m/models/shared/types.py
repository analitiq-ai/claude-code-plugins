"""
Shared type aliases and validators for Pydantic models.
"""
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, StringConstraints

from k2m._identity import _is_valid_uuid, parse_entity_id, validate_versioned_id

# Spec: the published Analitiq schema documentation §Identifier Forms.
# Surfacing the pattern via `StringConstraints` makes JSON-Schema consumers reject
# the same payloads the runtime validator does.
UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
VERSIONED_ID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    r"_v[1-9][0-9]*$"
)

# Endpoint-schema snapshot identifier (`sha256:<64 hex>`), as computed by
# `endpoint-materializer` and echoed by endpoint discovery / auto-map.
SCHEMA_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"

# Calendar date (`YYYY-MM-DD`) — the wire form of the metrics request window
# (`date_from`/`date_to`). A coarse shape gate only; calendar validity (real
# month/day ranges) is enforced where the value is parsed into a date.
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

# Run-state rows store `str(datetime)` (space separator); the wire contracts
# promise ISO 8601 (`T` separator). The pattern pins the normalized prefix
# only — fractional seconds and UTC offsets vary across writer Lambdas.
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


# Coerce DynamoDB Decimal to int
CoerceInt = Annotated[int, BeforeValidator(lambda v: int(v) if v is not None else v)]

# Plain UUID string
UuidStr = Annotated[
    str, StringConstraints(pattern=UUID_PATTERN), AfterValidator(validate_uuid)
]

# UUID with optional version suffix (uuid_v1)
VersionedUuidStr = Annotated[str, AfterValidator(validate_versioned_uuid)]

# Versioned ID validated via k2m.dynamodb
VersionedId = Annotated[
    str,
    StringConstraints(pattern=VERSIONED_ID_PATTERN),
    AfterValidator(validate_versioned_id),
]

# Alias used in pipeline connections
ConnectionId = UuidStr

# Calendar-date string (`YYYY-MM-DD`)
DateStr = Annotated[str, StringConstraints(pattern=DATE_PATTERN)]
