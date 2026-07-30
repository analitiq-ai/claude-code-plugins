"""Guards for the mapping/destination shapes reshaped in the #108 release.

Each change exists to make a specific wrong document *unrepresentable* rather
than merely wrong, so that is what gets pinned: not "the field has a new type",
but "the shape it replaced no longer validates". Every class below pairs the
accept with the reject it was introduced for.

Covered here: the token-array `get` path, the single-segment assignment target,
the `kind`-discriminated assignment value, the database write-mode set (both its
membership and its subset relationship to `endpoints.WriteMode`), and the
retirement of `Execution.max_concurrent_batches`. `WriteMode` itself and
`PageSize.default` are pinned in test_endpoint_model.py, next to the models that
own them; `Batching.max_concurrent_batches` in test_pipeline_runtime.py.

Several tests assert against the RENDERED JSON Schema, not just the model. That
is deliberate — the discriminator and the shared constraints are contract
commitments to external validators, and the model alone cannot show they
survived rendering.
"""
from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator
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
    StreamMapping,
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
        # Assert the value, not `literal != nested` — that comparison is true by
        # construction and would hold against any model at all.
        assert GetExpression.model_validate(
            {"op": "get", "path": ["a.b"]}
        ).path == ["a.b"]

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

    @pytest.mark.parametrize("path", ["  ", "\t", "\n", " "])
    def test_whitespace_only_path_rejected(self, path):
        # A destination field name must not be laxer than the source name that
        # feeds it: `GetExpression.path` segments are `NonEmptyStr`, which
        # requires a non-space character. Without these cases the pattern can be
        # loosened back to `^[^.]+$` and only a doc-echo assertion notices.
        with pytest.raises(ValidationError):
            AssignmentTarget.model_validate({"path": path, "arrow_type": "Utf8"})

    @pytest.mark.parametrize("path", ["a b", " a ", "id_2"])
    def test_inner_and_edge_whitespace_still_accepted(self, path):
        # The constraint is "contains a non-space", NOT "is stripped" or "has no
        # spaces" — provider field names legitimately contain them. Pinned so
        # the next tightening does not quietly overreach.
        assert AssignmentTarget.model_validate(
            {"path": path, "arrow_type": "Utf8"}
        ).path == path

    def test_published_schema_carries_the_single_segment_pattern(self):
        # The constraint is a commitment to external validators, so assert it
        # survived rendering rather than trusting the model alone.
        schema = StreamMapping.model_json_schema()
        pattern = schema["$defs"]["AssignmentTarget"]["properties"]["path"]["pattern"]
        validator = Draft202012Validator({"type": "string", "pattern": pattern})
        assert validator.is_valid("id")
        assert validator.is_valid("a b")
        assert not validator.is_valid("a.b")
        assert not validator.is_valid("   ")


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

    def test_published_schema_carries_the_kind_discriminator(self):
        # Without this the discriminator is INVISIBLE to the suite. Every other
        # test in this class passes against a plain smart union, because
        # `extra="forbid"` on each variant does the rejecting — so deleting
        # `Field(discriminator="kind")` stays green, and the only signal is a
        # separate render job reporting the schema as "stale", which a
        # re-render silences.
        #
        # The discriminator is a contract commitment, not an implementation
        # detail: it is what lets an external validator report which variant
        # failed, and the model comment promises it. Assert the rendered node.
        value = StreamMapping.model_json_schema()["$defs"]["Assignment"]["properties"]["value"]
        assert "discriminator" in value, (
            "the published assignment value is a bare union — `Field(discriminator=...)` "
            f"was dropped from AssignmentValue; got {sorted(value)}"
        )
        assert value["discriminator"] == {
            "propertyName": "kind",
            "mapping": {
                "expression": "#/$defs/ExpressionAssignmentValue",
                "constant": "#/$defs/ConstantAssignmentValue",
            },
        }
        assert {branch["$ref"] for branch in value["oneOf"]} == {
            "#/$defs/ExpressionAssignmentValue",
            "#/$defs/ConstantAssignmentValue",
        }

    def test_published_schema_rejects_what_the_model_rejects(self):
        # One contract, two validators. Note this does NOT pin the
        # discriminator — a bare `anyOf` with `extra="forbid"` on both variants
        # rejects these same payloads, so this passes with `discriminator=`
        # removed. `test_published_schema_carries_the_kind_discriminator` above
        # carries that alone. What this pins is model/schema agreement on the
        # malformed shapes, which is a separate and equally losable property.
        schema = StreamMapping.model_json_schema()
        validator = Draft202012Validator(schema)
        for payload in (
            {},
            {"expression": {"op": "get", "path": ["id"]}},  # the pre-#108 shape
            {"kind": "constant", "expression": {"op": "get", "path": ["id"]}},
            {"kind": "template", "expression": {"op": "get", "path": ["id"]}},
            {
                "kind": "expression",
                "expression": {"op": "get", "path": ["id"]},
                "constant": {"arrow_type": "Utf8", "value": "acme"},
            },
        ):
            doc = {"assignments": [{"target": {"path": "id", "arrow_type": "Utf8"},
                                    "value": payload}]}
            assert not validator.is_valid(doc), (
                f"published schema accepts {payload!r}, which the model rejects"
            )
            with pytest.raises(ValidationError):
                StreamMapping.model_validate(doc)


class TestDatabaseWriteModes:
    """The SQL destination's mode set.

    Its subset relationship to `endpoints.WriteMode` is NOT tested here. That
    guard is the `raise AssertionError` at import in `stream.py`, and an
    import-time check cannot be exercised by a test in a module that imports it:
    a violation is a collection error, so any such test would be skipped rather
    than failed. Asserting the same condition here would be a comment wearing a
    test's clothes. Verified by mutation instead — removing `truncate_insert`
    from `WriteMode` fails collection with the guard's own message.
    """

    def test_exact_members(self):
        # Pinned as an exact set: which modes the SQL write path implements is a
        # coordinated engine + contract fact, so adding one should fail here and
        # be restated deliberately.
        assert _DB_WRITE_MODES == {"insert", "upsert", "truncate_insert"}

    def test_database_destination_accepts_truncate_insert(self):
        dest = StreamDestination.model_validate(
            {"endpoint_ref": _DB_ENDPOINT_REF, "write": {"mode": "truncate_insert"}}
        )
        assert dest.write.mode == "truncate_insert"

    def test_truncate_insert_forbids_conflict_keys(self):
        # `conflict_keys` is an upsert concept; a full-refresh load has nothing
        # to match on, so declaring one is a defect rather than a no-op.
        #
        # Match on the mode in the message, not just the leading sentence:
        # `_validate_write_conflict_keys` runs BEFORE `_validate_db_write_mode`
        # and short-circuits, so "only valid for a database upsert" is what any
        # non-upsert mode produces — including a mode that is not in the
        # vocabulary at all. The generic prefix passed for `mode: "banana"`.
        #
        # The mode-specific match still does not prove membership: the message
        # interpolates the mode from the INPUT document, so it matches whenever
        # the document says `truncate_insert`, valid or not. Hence the explicit
        # membership assertion — without it this test stays green with
        # `truncate_insert` removed from `_DB_WRITE_MODES`.
        assert "truncate_insert" in _DB_WRITE_MODES
        with pytest.raises(
            ValidationError, match=r"write\.mode='truncate_insert' must not declare it"
        ):
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
        # carrying it fails rather than being quietly ignored. Breaking for any
        # stored stream that spells it out — hence stream 16.0.0 -> 17.0.0.
        with pytest.raises(ValidationError):
            Execution.model_validate({"batch_size": 1000, "max_concurrent_batches": 3})
