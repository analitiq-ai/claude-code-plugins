"""Assert that a contract model refuses a document at one field.

A bare `pytest.raises(ValidationError)` passes on a document refused for any
reason, so a test bending one field would stay green after the bend stopped
mattering. The shape tests call `refusal_at` instead.
"""
from __future__ import annotations

from typing import Any, Callable

import pytest
from pydantic import ValidationError


def _holds_in_order(path: tuple[Any, ...], loc: tuple[Any, ...]) -> bool:
    # In order but not contiguous: pydantic threads a union's branch tag
    # (`api`, `http`, `connector`, …) between the field names of a location.
    remaining = iter(loc)
    return all(any(segment == part for part in remaining) for segment in path)


def refusal_at(
    validate: Callable[[Any], Any],
    document: Any,
    path: tuple[Any, ...],
    error_type: str,
) -> None:
    """`validate(document)` raises, every error sits under `path`, and one of
    them is `error_type`."""
    with pytest.raises(ValidationError) as exc:
        validate(document)
    errors = exc.value.errors()
    elsewhere = [err for err in errors if not _holds_in_order(path, tuple(err["loc"]))]
    assert not elsewhere, f"refused outside {path}: {elsewhere}"
    types = {err["type"] for err in errors}
    assert error_type in types, f"expected {error_type!r} at {path}, got {types}"
