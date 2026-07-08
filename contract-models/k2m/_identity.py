"""Entity-id and UUID identity helpers — pure, stdlib-only.

The single home for the identifier-parsing/validation logic shared between
``k2m.dynamodb`` (versioned sort-key handling) and the Pydantic contract models
(``k2m.models.shared.types``). Kept boto3-free and dependency-free so the
``analitiq-contract-models`` package can ship it verbatim: the contract models
must validate identifiers offline, without dragging in the AWS-coupled
``k2m.dynamodb`` / ``k2m.helpers`` modules that used to own these functions.

``k2m.dynamodb`` and ``k2m.helpers`` re-export from here, so their public
surface is unchanged and every existing caller keeps working.
"""
from __future__ import annotations

import re
import uuid

# Version pattern: id_vX.Y.Z or id_vN (supports semantic versioning and simple version numbers)
VERSION_PATTERN = re.compile(r'^(.+)_v(\d+(?:\.\d+)*)$')

# Pattern for validating versioned UUIDs (uuid_v1 format).
# RFC-4122 strict: version nibble [1-5], variant nibble [89ab]. Must match
# `UUID_PATTERN` in k2m.models.shared.types so JSON-Schema consumers and the
# runtime validator agree.
VERSIONED_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}_v[1-9][0-9]*$"
)


def parse_version_string(version_str: str) -> int:
    """Parse a version string like '1.2.3' or '1' into an integer for storage.

    Converts semantic version to integer:
    - '1' -> 1
    - '1.0' -> 100
    - '1.2' -> 102
    - '1.2.3' -> 10203

    Args:
        version_str: Version string (e.g., '1', '1.2', '1.2.3')

    Returns:
        Integer version number
    """
    parts = version_str.split('.')
    if len(parts) == 1:
        return int(parts[0])
    elif len(parts) == 2:
        return int(parts[0]) * 100 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 10000 + int(parts[1]) * 100 + int(parts[2])
    else:
        raise ValueError(f"Invalid version format: {version_str}")


def parse_entity_id(entity_id_with_version: str) -> tuple[str, int | None]:
    """Parse an entity ID that may include a version suffix.

    Args:
        entity_id_with_version: Entity ID, optionally with version (e.g., 'uuid_v1.2.3')

    Returns:
        Tuple of (entity_id, version) where version is None if not specified
    """
    match = VERSION_PATTERN.match(entity_id_with_version)
    if match:
        entity_id = match.group(1)
        version_str = match.group(2)
        version = parse_version_string(version_str)
        return entity_id, version
    return entity_id_with_version, None


def validate_versioned_id(value: str) -> str:
    """Validate that ID follows versioned format: {uuid}_v{version}.

    Args:
        value: The ID string to validate

    Returns:
        The validated value

    Raises:
        ValueError: If the ID doesn't match the versioned format
    """
    if not VERSIONED_ID_PATTERN.match(value):
        raise ValueError(
            f"ID must be versioned in format '{{uuid}}_v{{version}}', got: {value}"
        )
    return value


def _is_valid_uuid(value):
    # Accept any RFC-4122 UUID (v1–v5). The DIP registry webhook mints
    # deterministic v5 ids from the connector slug so retries converge on
    # the same row; a v4-only check would reject those.
    try:
        val = uuid.UUID(str(value))
        return str(val).lower() == str(value).lower()
    except (ValueError, AttributeError, TypeError):
        return False
