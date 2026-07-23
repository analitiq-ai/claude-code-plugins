"""Stream-surface `arrow_type` vocabulary guards.

`ArrowFieldSpec`, `ConstantValue`, and `AssignmentTarget` share the same
`ARROW_TYPE_PATTERN` field constraint and the same `enforce_container_shape`
chokepoint (which carries the cross-parameter check) as the endpoint `Column`
classes — but nothing else in the suite exercises the STREAM side of that
wiring, so a stream-model refactor that dropped either would be invisible.
Representative accept/reject per model, not a full per-family matrix: the
vocabulary itself is exhaustively pinned in test_canonical_types_schema.py.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.stream import ArrowFieldSpec, AssignmentTarget, ConstantValue


class TestArrowFieldSpec:
    def test_scalar_accepted(self):
        ArrowFieldSpec.model_validate({"arrow_type": "Decimal128(5, 5)"})

    def test_trimmed_family_rejected(self):
        with pytest.raises(ValidationError):
            ArrowFieldSpec.model_validate({"arrow_type": "Struct<id:Int64>"})

    def test_bare_parameterized_rejected(self):
        with pytest.raises(ValidationError):
            ArrowFieldSpec.model_validate({"arrow_type": "Timestamp"})

    def test_cross_param_bound_rejected(self):
        with pytest.raises(ValidationError, match="scale .* must be <= precision"):
            ArrowFieldSpec.model_validate({"arrow_type": "Decimal128(5, 6)"})

    def test_authored_shape_object_needs_properties(self):
        ArrowFieldSpec.model_validate(
            {"arrow_type": "Object", "properties": {"id": {"arrow_type": "Int64"}}}
        )
        with pytest.raises(ValidationError, match="requires sibling 'properties'"):
            ArrowFieldSpec.model_validate({"arrow_type": "Object"})


class TestConstantValue:
    def test_trimmed_family_rejected(self):
        with pytest.raises(ValidationError):
            ConstantValue.model_validate(
                {"arrow_type": "Map<Utf8, Int64>", "value": {}}
            )

    def test_cross_param_bound_rejected(self):
        with pytest.raises(ValidationError, match="scale .* must be <= precision"):
            ConstantValue.model_validate({"arrow_type": "Decimal256(10, 11)", "value": "1"})


class TestAssignmentTarget:
    def test_trimmed_family_rejected(self):
        with pytest.raises(ValidationError):
            AssignmentTarget.model_validate(
                {"path": "customer", "arrow_type": "List<Int64>"}
            )

    def test_cross_param_bound_rejected(self):
        with pytest.raises(ValidationError, match="scale .* must be <= precision"):
            AssignmentTarget.model_validate(
                {"path": "amount", "arrow_type": "Decimal128(5, 6)"}
            )
