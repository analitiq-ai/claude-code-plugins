"""A contract model instance only ever comes out of the validators.

Every model under ``analitiq.contracts`` is parse-then-read: the cross-field
rules run once, in the model validators, at construction. Every route that
produces or alters an instance without running them is a way to hold a model
whose own parser rejects what ``model_dump()`` emits — assigning a field, and
the constructors pydantic offers for skipping validation
(``model_construct``, ``model_copy(update=...)``).

The guards below are DERIVED, not hand-listed: they walk the whole contract
tree through :func:`contract_classes` (the same scan the prose census uses),
so a model added tomorrow that does not inherit the policy fails here on the
day it is written. The named cases that follow are the concrete repros the
defect was reported with, kept as executable statements of what
"unrepresentable" must mean after construction.

What the policy does NOT reach is stated where the policy is: pydantic's
freeze binds a field to the value the validator accepted, not the contents of
a list or dict that value holds, so no guard here claims otherwise.
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from analitiq.contracts.connector import SqlBulkLoad
from analitiq.contracts.shared.common import ParseOnly
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


def test_every_contract_model_refuses_the_unvalidated_constructors():
    """Freezing an instance is worth nothing if one can be built unvalidated."""
    unguarded = sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in contract_classes()
        if cls.model_construct.__func__ is not ParseOnly.model_construct.__func__
        or cls.model_copy is not ParseOnly.model_copy
    )
    assert unguarded == [], (
        "these contract models can be built or altered without running a "
        "validator; inherit ParseOnly (StrictModel already does): "
        + ", ".join(unguarded)
    )


def test_model_construct_cannot_build_the_pairing_the_parser_refuses():
    """The same repro as assignment, through the unvalidated constructor."""
    with pytest.raises(TypeError, match="model_validate"):
        SqlBulkLoad.model_construct(sqlalchemy="adbc_ingest")


def test_model_copy_cannot_update_its_way_past_the_parser():
    """`update=` writes straight into `__dict__`, frozen or not."""
    loader = SqlBulkLoad.model_validate({})
    with pytest.raises(TypeError, match="model_validate"):
        loader.model_copy(update={"sqlalchemy": "adbc_ingest"})

    # A copy that changes nothing changes nothing — it stays available.
    assert loader.model_copy() == loader
    assert loader.model_copy(deep=True) == loader


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
