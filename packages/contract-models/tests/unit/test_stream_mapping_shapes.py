"""Guards for the mapping/destination shapes reshaped in the #108 release.

Four of that release's six changes land on the stream surface, and each one
exists to make a specific wrong document *unrepresentable* rather than merely
wrong. That is the thing worth pinning: not "the field has a new type", but
"the shape it replaced no longer validates". So every class below pairs the
accept with the reject it was introduced for.

The endpoint-side changes (`WriteMode` gaining `truncate_insert`,
`PageSize.default` bounding its literal branch) are pinned in
test_endpoint_model.py, next to the models that own them; the stream side of
`truncate_insert` — a database destination selecting it — is pinned here.
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from analitiq.contracts.endpoints import WRITE_MODES
from analitiq.contracts.stream import (
    AssignmentTarget,
    AssignmentValue,
    ConstantAssignmentValue,
    Execution,
    ExpressionAssignmentValue,
    GetExpression,
    PipeExpression,
    StreamDestination,
    _DB_WRITE_MODES,
)

_ASSIGNMENT_VALUE = TypeAdapter(AssignmentValue)

_DB_ENDPOINT_REF = {
    "scope": "connection",
    "connection_id": "00000000-0000-4000-8000-000000000001_v1",
    "database_object": {"schema": "public", "name": "orders"},
}
_API_ENDPOINT_REF = {
    "scope": "connector",
    "connection_id": "00000000-0000-4000-8000-000000000002_v1",
    "endpoint_id": "transfers",
}


class TestGetExpressionPath:
    """`path` is a token array. The dotted string it replaced must not validate."""

    def test_token_array_accepted(self):
        assert GetExpression.model_validate(
            {"op": "get", "path": ["address", "city"]}
        ).path == ["address", "city"]

    def test_single_segment_is_a_one_element_array(self):
        assert GetExpression.model_validate({"op": "get", "path": ["id"]}).path == ["id"]

    def test_dotted_string_rejected(self):
        # The whole point of the change: the old shape must be a validation
        # error, not a value silently indexed character-wise downstream.
        with pytest.raises(ValidationError):
            GetExpression.model_validate({"op": "get", "path": "address.city"})

    def test_empty_array_rejected(self):
        with pytest.raises(ValidationError):
            GetExpression.model_validate({"op": "get", "path": []})

    def test_empty_segment_rejected(self):
        with pytest.raises(ValidationError):
            GetExpression.model_validate({"op": "get", "path": ["address", ""]})

    def test_a_dot_inside_a_token_is_one_segment(self):
        # There is no escape rule to get wrong: a field literally named `a.b` is
        # one token, and it is a different path from the nested `["a", "b"]`.
        literal = GetExpression.model_validate({"op": "get", "path": ["a.b"]})
        nested = GetExpression.model_validate({"op": "get", "path": ["a", "b"]})
        assert literal.path != nested.path

    def test_nested_get_inside_a_pipe_carries_tokens(self):
        # The failure that motivated the change lived here — a `get` nested in a
        # `pipe` was never reached by the root-level string split.
        pipe = PipeExpression.model_validate(
            {
                "op": "pipe",
                "args": [
                    {"op": "get", "path": ["amount"]},
                    {"op": "fn", "name": "to_string"},
                ],
            }
        )
        assert pipe.args[0].path == ["amount"]

    def test_nested_get_inside_a_pipe_rejects_a_dotted_string(self):
        with pytest.raises(ValidationError):
            PipeExpression.model_validate(
                {
                    "op": "pipe",
                    "args": [
                        {"op": "get", "path": "order.amount"},
                        {"op": "fn", "name": "to_string"},
                    ],
                }
            )


class TestAssignmentTargetPath:
    """A target addresses one field on the destination root — one segment."""

    def test_single_segment_accepted(self):
        assert AssignmentTarget.model_validate(
            {"path": "address", "arrow_type": "Utf8"}
        ).path == "address"

    def test_dotted_path_rejected(self):
        with pytest.raises(ValidationError):
            AssignmentTarget.model_validate(
                {"path": "address.city", "arrow_type": "Utf8"}
            )

    def test_nesting_is_expressed_by_arrow_type_and_properties(self):
        # The one sanctioned spelling for a nested destination field, which is
        # why the dotted target is redundant rather than merely unsupported.
        target = AssignmentTarget.model_validate(
            {
                "path": "address",
                "arrow_type": "Object",
                "properties": {"city": {"arrow_type": "Utf8"}},
            }
        )
        assert set(target.properties) == {"city"}

    def test_empty_path_rejected(self):
        with pytest.raises(ValidationError):
            AssignmentTarget.model_validate({"path": "", "arrow_type": "Utf8"})


class TestAssignmentValueKind:
    """`kind` discriminates; each variant admits exactly one payload key."""

    def test_expression_variant(self):
        value = _ASSIGNMENT_VALUE.validate_python(
            {"kind": "expression", "expression": {"op": "get", "path": ["id"]}}
        )
        assert isinstance(value, ExpressionAssignmentValue)

    def test_constant_variant(self):
        value = _ASSIGNMENT_VALUE.validate_python(
            {"kind": "constant", "constant": {"arrow_type": "Utf8", "value": "acme"}}
        )
        assert isinstance(value, ConstantAssignmentValue)

    def test_missing_kind_rejected(self):
        # The pre-#108 shape. It used to be the ONLY shape, so this reject is
        # what proves the discriminator is actually required rather than
        # defaulted from whichever payload key happens to be present.
        with pytest.raises(ValidationError, match="kind"):
            _ASSIGNMENT_VALUE.validate_python(
                {"expression": {"op": "get", "path": ["id"]}}
            )

    def test_empty_object_rejected(self):
        # The "neither key set" state that used to force every consumer to
        # invent a default.
        with pytest.raises(ValidationError, match="kind"):
            _ASSIGNMENT_VALUE.validate_python({})

    def test_payload_of_the_other_variant_rejected(self):
        with pytest.raises(ValidationError):
            _ASSIGNMENT_VALUE.validate_python(
                {"kind": "constant", "expression": {"op": "get", "path": ["id"]}}
            )

    def test_both_payloads_rejected(self):
        # Not by a cross-field rule any more: `constant` is simply not a
        # declared field of the expression variant, and the variant is closed.
        with pytest.raises(ValidationError):
            _ASSIGNMENT_VALUE.validate_python(
                {
                    "kind": "expression",
                    "expression": {"op": "get", "path": ["id"]},
                    "constant": {"arrow_type": "Utf8", "value": "acme"},
                }
            )

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValidationError):
            _ASSIGNMENT_VALUE.validate_python(
                {"kind": "template", "expression": {"op": "get", "path": ["id"]}}
            )


class TestDatabaseWriteModes:
    """The database vocabulary is derived from `endpoints.WriteMode`, not copied."""

    def test_derived_from_the_endpoint_vocabulary(self):
        # If these ever diverge it is because someone restated one of them; the
        # point of deriving is that adding a mode is a single edit.
        assert _DB_WRITE_MODES == frozenset(WRITE_MODES)

    def test_truncate_insert_is_in_the_vocabulary(self):
        assert "truncate_insert" in WRITE_MODES

    def test_database_destination_accepts_truncate_insert(self):
        dest = StreamDestination.model_validate(
            {"endpoint_ref": _DB_ENDPOINT_REF, "write": {"mode": "truncate_insert"}}
        )
        assert dest.write.mode == "truncate_insert"

    def test_truncate_insert_forbids_conflict_keys(self):
        # `conflict_keys` is an upsert concept; a full-refresh load has nothing
        # to match on, so declaring one is a defect rather than a no-op.
        with pytest.raises(ValidationError, match="only valid for a database upsert"):
            StreamDestination.model_validate(
                {
                    "endpoint_ref": _DB_ENDPOINT_REF,
                    "write": {"mode": "truncate_insert", "conflict_keys": ["id"]},
                }
            )

    def test_unknown_database_mode_still_rejected(self):
        with pytest.raises(ValidationError, match="not a valid database write mode"):
            StreamDestination.model_validate(
                {"endpoint_ref": _DB_ENDPOINT_REF, "write": {"mode": "overwrite"}}
            )

    def test_api_destination_mode_stays_endpoint_owned(self):
        # Widening the vocabulary must not accidentally close the API side,
        # whose mode is whatever key the selected endpoint declares.
        dest = StreamDestination.model_validate(
            {"endpoint_ref": _API_ENDPOINT_REF, "write": {"mode": "create_transfer"}}
        )
        assert dest.write.mode == "create_transfer"


class TestExecutionHasNoConcurrencyKnob:
    def test_batch_size_still_accepted(self):
        assert Execution.model_validate({"batch_size": 1000}).batch_size == 1000

    def test_max_concurrent_batches_rejected(self):
        # Retired, not deprecated: the model is closed, so a document still
        # carrying it fails rather than being quietly ignored.
        with pytest.raises(ValidationError):
            Execution.model_validate({"batch_size": 1000, "max_concurrent_batches": 3})
