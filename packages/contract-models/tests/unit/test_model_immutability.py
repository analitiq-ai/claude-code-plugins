"""Contract models hold their invariants for the lifetime of an instance.

Every model under ``analitiq.contracts`` is parse-then-read: the cross-field
rules run once, in the model validators, at construction. Without immutability
that guarantee expires the moment the caller assigns an attribute — a parsed
model can be mutated into a document its own parser rejects, and
``model_dump()`` then emits it.

The guard below is DERIVED, not hand-listed: it walks the whole contract tree
through :func:`contract_classes` (the same scan the prose census uses), so a
model added tomorrow that does not inherit the policy fails here on the day it
is written. The named cases that follow it are the concrete repros the defect
was reported with, kept as executable statements of what "unrepresentable"
must mean after construction.
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from analitiq.contracts.connector import SqlBulkLoad
from analitiq.contracts.shared.introspect import contract_classes
from analitiq.contracts.stream import (
    AssignmentTarget,
    AssignmentValue,
    GetExpression,
)


def test_every_contract_model_is_frozen():
    """No contract model may be mutable — the scan is the membership rule."""
    mutable = sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in contract_classes()
        if not cls.model_config.get("frozen")
    )
    assert mutable == [], (
        "these contract models are mutable after construction; inherit "
        "StrictModel (which owns the frozen policy) instead of BaseModel: "
        + ", ".join(mutable)
    )


def test_assignment_cannot_build_the_pairing_the_parser_refuses():
    """`SqlBulkLoad.sqlalchemy` is a `Literal`; assignment must not widen it."""
    loader = SqlBulkLoad.model_validate({})
    with pytest.raises(ValidationError):
        loader.sqlalchemy = "adbc_ingest"


def test_assignment_cannot_desync_a_variant_from_its_discriminator():
    """Re-parsing a dumped model must never fail because of an assignment."""
    value = TypeAdapter(AssignmentValue).validate_python(
        {"kind": "expression", "expression": {"op": "get", "path": ["a"]}}
    )
    with pytest.raises(ValidationError):
        value.kind = "constant"
    assert TypeAdapter(AssignmentValue).validate_python(value.model_dump()) == value


def test_assignment_cannot_reintroduce_a_dotted_path():
    """Dotted paths are unrepresentable at parse time; assignment must not restore them.

    `GetExpression.path` is a list of segments and `AssignmentTarget.path` is a
    single segment, so each refuses a dotted string on the way in — and, frozen,
    on the way back in too.
    """
    expression = GetExpression.model_validate({"op": "get", "path": ["a", "b"]})
    with pytest.raises(ValidationError):
        expression.path = "a.b"

    target = AssignmentTarget.model_validate({"path": "a", "arrow_type": "Utf8"})
    with pytest.raises(ValidationError):
        target.path = "a.b.c"
