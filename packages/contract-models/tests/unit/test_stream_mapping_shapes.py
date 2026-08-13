"""Guards for the stream mapping and destination shapes.

Each change exists to make a specific wrong document *unrepresentable* rather
than merely wrong, so that is what gets pinned: not "the field has a new type",
but "the shape it replaced no longer validates". Every class below pairs the
accept with the reject it was introduced for.

Covered here: the token-array `get` path, the single-segment assignment target,
the `kind`-discriminated assignment value, the database write-mode set and the
disposition table it derives from, the scope-discriminated destination and its
write shapes, the token-array validation-rule `field` and its resolution
against the mapping's targets, and the retirement of
`Execution.max_concurrent_batches`. `WriteMode` itself and `PageSize.default`
are pinned in test_endpoint_model.py, next to the models that own them;
`Batching.max_concurrent_batches` in test_pipeline_runtime.py.

Several tests assert against the RENDERED JSON Schema, not just the model. That
is deliberate — the discriminators, the variant branches and the shared
constraints are contract commitments to external validators, and the model
alone cannot show they survived rendering.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import get_args

import pytest
from jsonschema import Draft202012Validator
from pydantic import Tag, TypeAdapter, ValidationError

from analitiq.contracts.endpoints import WRITE_MODES
from analitiq.contracts.shared.common import NonEmptyStr
from analitiq.contracts.stream import (
    ApiStreamDestination,
    ApiWrite,
    AssignmentTarget,
    AssignmentValue,
    ConstantAssignmentValue,
    DatabaseConflictKeyedWrite,
    DatabaseKeylessWrite,
    DatabaseStreamDestination,
    DatabaseWrite,
    Execution,
    ExpressionAssignmentValue,
    GetExpression,
    PipeExpression,
    StreamDestination,
    StreamInput,
    StreamMapping,
    _DB_WRITE_MODES,
)

_ASSIGNMENT_VALUE = TypeAdapter(AssignmentValue)
# `StreamDestination` is a tagged union, not a model class, so both the runtime
# and the published-schema graders go through one adapter.
_DESTINATION = TypeAdapter(StreamDestination)
_DESTINATION_SCHEMA = Draft202012Validator(_DESTINATION.json_schema())

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


def _destination_union_tags() -> tuple[str, ...]:
    """Every scope `StreamDestination`'s discriminator selects a variant by."""
    tags = tuple(
        meta.tag
        for variant in get_args(get_args(StreamDestination)[0])
        for meta in getattr(variant, "__metadata__", ())
        if isinstance(meta, Tag)
    )
    assert tags, "StreamDestination is no longer a Tag-annotated union"
    return tags


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
        # requires a non-space character.
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

    @pytest.mark.parametrize(
        "name", ["id", "id_2", "a b", " a ", "Ünïcode", "", "   ", "\t", "\n"]
    )
    def test_dot_free_names_track_the_source_segment_constraint(self, name):
        # `SINGLE_SEGMENT_PATH_PATTERN` justifies its whitespace half as parity
        # with `NonEmptyStr` — the constraint on a SOURCE segment
        # (`GetExpression.path` items) — on the grounds that a destination field
        # name must not be laxer than the source name feeding it. Assert the
        # parity rather than the pattern: the two are written independently
        # (pydantic cannot compose two `pattern` constraints on one field), so
        # tightening or loosening `NonEmptyStr` must fail here.
        source = TypeAdapter(NonEmptyStr)
        try:
            source.validate_python(name)
            source_accepts = True
        except ValidationError:
            source_accepts = False
        try:
            AssignmentTarget.model_validate({"path": name, "arrow_type": "Utf8"})
            target_accepts = True
        except ValidationError:
            target_accepts = False
        assert source_accepts == target_accepts, (
            f"{name!r}: source segment accepts={source_accepts} but assignment "
            f"target accepts={target_accepts} — the two segment constraints have "
            "diverged on a dot-free name"
        )

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
        # The undiscriminated shape this replaced. It used to be the ONLY
        # shape, so this reject is
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

    @pytest.mark.parametrize("kind", ["expression", "constant"])
    def test_variant_payload_is_required(self, kind):
        # The AT-LEAST-ONE half of retired RULE-STRM-008. "Exactly one of
        # expression or constant" was two claims: at most one, which the union
        # now makes unrepresentable, and at least one, which is carried by the
        # `...` on each variant's payload field. Retiring a registry rule
        # means pinning both halves of whatever replaces it.
        with pytest.raises(ValidationError):
            _ASSIGNMENT_VALUE.validate_python({"kind": kind})

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValidationError):
            _ASSIGNMENT_VALUE.validate_python(
                {"kind": "template", "expression": {"op": "get", "path": ["id"]}}
            )

    def test_published_schema_carries_the_kind_discriminator(self):
        # The discriminator is a contract commitment, not an implementation
        # detail: it is what lets an external validator report WHICH variant
        # failed, and the model comment promises it. A plain smart union
        # rejects the same documents (`extra="forbid"` on each variant does
        # that work), so the commitment can only be asserted on the rendered
        # node itself.
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
        # One contract, two validators. What this pins is model/schema
        # AGREEMENT on the malformed shapes — not the discriminator, which a
        # bare `anyOf` over two `extra="forbid"` variants would satisfy just as
        # well (`test_published_schema_carries_the_kind_discriminator` covers
        # that).
        schema = StreamMapping.model_json_schema()
        validator = Draft202012Validator(schema)
        for payload in (
            {},
            {"expression": {"op": "get", "path": ["id"]}},  # undiscriminated
            {"kind": "constant", "expression": {"op": "get", "path": ["id"]}},
            {"kind": "template", "expression": {"op": "get", "path": ["id"]}},
            {
                "kind": "expression",
                "expression": {"op": "get", "path": ["id"]},
                "constant": {"arrow_type": "Utf8", "value": "acme"},
            },
            # Payload-less variants — the schema side of each variant's
            # required payload.
            {"kind": "expression"},
            {"kind": "constant"},
        ):
            doc = {"assignments": [{"target": {"path": "id", "arrow_type": "Utf8"},
                                    "value": payload}]}
            assert not validator.is_valid(doc), (
                f"published schema accepts {payload!r}, which the model rejects"
            )
            with pytest.raises(ValidationError):
                StreamMapping.model_validate(doc)


class TestDatabaseWriteModes:
    """The SQL destination's mode set, and the disposition table it derives from.

    The table's agreement with `endpoints.WriteMode` is enforced by a `raise
    AssertionError` at import in `stream.py`. Asserting the same condition
    *inline* here would prove nothing — this module imports `stream`, so a
    violation is a collection error and the test never runs. It is pinned in a
    subprocess instead (`test_import_guard_*` below).
    """

    def test_exact_members(self):
        # Pinned as an exact set: which modes the SQL write path implements is a
        # coordinated engine + contract fact, so adding one should fail here and
        # be restated deliberately.
        assert _DB_WRITE_MODES == {"insert", "upsert", "truncate_insert"}

    def test_the_vocabulary_is_derived_from_the_variants(self):
        # `_DB_WRITE_MODES` is what the plugin doc generator renders into prose.
        # If it were a hand-kept second list, prose could name a mode no
        # destination accepts (or miss one it does), so pin that it is exactly
        # the union of the two database write variants' declared modes.
        declared = set()
        for variant in get_args(get_args(DatabaseWrite)[0]):
            declared |= set(get_args(variant.model_fields["mode"].annotation))
        assert declared == _DB_WRITE_MODES

    @pytest.mark.parametrize(
        "label, mutation, expected",
        [
            (
                "a new universe member nobody dispositioned",
                'endpoints.WRITE_MODES = (*endpoints.WRITE_MODES, "merge")',
                "undispositioned",
            ),
            (
                "a disposition for a mode outside the universe",
                'endpoints.WRITE_MODES = ("insert", "upsert")',
                "unknown",
            ),
        ],
        ids=["undispositioned-mode", "unknown-mode"],
    )
    def test_import_guard_forces_a_disposition(self, label, mutation, expected):
        # Import `stream` fresh in a subprocess with `WRITE_MODES` mutated, so
        # the guard runs against a diverged vocabulary. In-process there is no
        # way to reach it: `stream` is already imported by the time any test
        # body runs.
        #
        # Both directions matter, and they are different failures. A mode added
        # to the universe with no disposition is the one the issue asks for:
        # nobody decided whether the SQL write path implements it, and the build
        # must stop until someone does. A disposition for a mode the universe
        # dropped is the stale-copy direction.
        source = textwrap.dedent(
            f"""
            import analitiq.contracts.endpoints as endpoints
            {mutation}
            try:
                import analitiq.contracts.stream  # noqa: F401
            except AssertionError as exc:
                assert "{expected}" in str(exc), exc
                print("GUARD_FIRED")
            """
        )
        # The source tree is on `sys.path` via the repo-root conftest, not on
        # PYTHONPATH, so hand it to the child explicitly — derived from the
        # loaded package rather than hardcoded, so it follows a layout change.
        import analitiq.contracts as contracts

        src_root = str(Path(contracts.__path__[0]).parents[1])
        result = subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            text=True,
            # The child's exit status is not the signal — it prints GUARD_FIRED
            # on success and would exit non-zero if the guard did NOT fire and
            # something else raised. Assert on stdout below, which distinguishes
            # those; `check=True` would conflate them into the same CalledProcessError.
            check=False,
            env={
                **os.environ,
                "DOMAIN": "analitiq.ai",
                "PYTHONPATH": os.pathsep.join(
                    [src_root, *filter(None, [os.environ.get("PYTHONPATH")])]
                ),
            },
        )
        assert "GUARD_FIRED" in result.stdout, (
            "the import-time disposition guard in stream.py did not fire for "
            f"{label}.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
        )

    def test_database_destination_accepts_truncate_insert(self):
        dest = _DESTINATION.validate_python(
            {"endpoint_ref": _DB_ENDPOINT_REF, "write": {"mode": "truncate_insert"}}
        )
        assert dest.write.mode == "truncate_insert"

    def test_unknown_database_mode_still_rejected(self):
        with pytest.raises(ValidationError):
            _DESTINATION.validate_python(
                {"endpoint_ref": _DB_ENDPOINT_REF, "write": {"mode": "overwrite"}}
            )

    @pytest.mark.parametrize("mode", WRITE_MODES)
    def test_api_destination_accepts_every_universe_mode(self, mode):
        # The API branch is bounded by the UNIVERSE, not by the SQL subset: a
        # mode the SQL write path does not implement is still a key an endpoint
        # may declare, so closing the database side must not close the API side
        # to the same set.
        dest = _DESTINATION.validate_python(
            {"endpoint_ref": _API_ENDPOINT_REF, "write": {"mode": mode}}
        )
        assert dest.write.mode == mode

    def test_api_mode_vocabulary_is_the_endpoint_write_key_universe(self):
        # An API destination's mode names a key of the selected endpoint's
        # `operations.write`, and that map is keyed by `endpoints.WriteMode` —
        # so the vocabulary IS enumerable, from the same declaration the
        # endpoint document is bound by. Derived, never restated here.
        assert set(get_args(ApiWrite.model_fields["mode"].annotation)) == set(
            WRITE_MODES
        )

    def test_api_destination_rejects_a_mode_no_endpoint_can_declare(self):
        # THE silent wrong answer this closes: a stream could bind an API
        # destination to a write operation no api-endpoint document is able to
        # declare, and nothing in the contract saw it.
        with pytest.raises(ValidationError):
            _DESTINATION.validate_python(
                {"endpoint_ref": _API_ENDPOINT_REF, "write": {"mode": "create_transfer"}}
            )

    def test_api_destination_mode_rejects_whitespace(self):
        # Falls out of the `Literal`: an endpoint declares no operation named
        # "   ", so a mode that is only whitespace names nothing.
        with pytest.raises(ValidationError):
            _DESTINATION.validate_python(
                {"endpoint_ref": _API_ENDPOINT_REF, "write": {"mode": "   "}}
            )


class TestDestinationScopeDiagnostics:
    """A destination whose `endpoint_ref.scope` is missing or unknown.

    The variant is selected by a nested key, which a plain field discriminator
    cannot reach, so a callable reads it — and pydantic's default diagnostic
    then names that private function instead of the document key. The key is
    what an author can act on, so the union states it.
    """

    def _destination(self, ref: dict) -> dict:
        return {"endpoint_ref": ref, "write": {"mode": "insert"}}

    @pytest.mark.parametrize(
        "label, ref",
        [
            ("missing", {k: v for k, v in _DB_ENDPOINT_REF.items() if k != "scope"}),
            ("unknown", {**_DB_ENDPOINT_REF, "scope": "nonsense"}),
        ],
    )
    def test_the_diagnostic_names_the_document_key(self, label, ref):
        # `_destination_scope` appears in no document, no published schema and
        # no skill prose, so naming it tells an author nothing. One message
        # covers both cases because a callable discriminator has one.
        with pytest.raises(ValidationError) as exc:
            _DESTINATION.validate_python(self._destination(ref))
        message = exc.value.errors()[0]["msg"]
        assert "endpoint_ref.scope" in message, message
        assert "_destination_scope" not in message, message

    @pytest.mark.parametrize(
        "label, ref",
        [
            ("missing", {k: v for k, v in _DB_ENDPOINT_REF.items() if k != "scope"}),
            ("unknown", {**_DB_ENDPOINT_REF, "scope": "nonsense"}),
        ],
    )
    def test_the_diagnostic_names_every_scope_the_union_tags(self, label, ref):
        # Derived from the union's own tags, not restated: a variant added to
        # the union must appear in the diagnostic without anyone remembering to
        # edit the sentence. The default unknown-tag message named the expected
        # tags, and replacing it must not lose that.
        with pytest.raises(ValidationError) as exc:
            _DESTINATION.validate_python(self._destination(ref))
        message = exc.value.errors()[0]["msg"]
        for tag in _destination_union_tags():
            assert tag in message, (tag, message)


class TestWriteShapesAreScopeDiscriminated:
    """The illegal write/conflict_keys combinations must be UNREPRESENTABLE.

    Rejecting them was the previous design: two `@model_validator`s plus four
    hand-written `allOf` branches that had to be kept in lockstep with them by
    hand. What replaces them is shape — a destination union tagged by
    `endpoint_ref.scope`, whose database branch is itself `mode`-discriminated
    — so the first thing pinned here is the ABSENCE of the field, not a
    rejection message.

    Every document is graded against the published JSON Schema as well as the
    model: the two variants are what an external validator sees, and the model
    alone cannot show they survived rendering.
    """

    def test_api_write_declares_no_conflict_keys_field(self):
        # Not "optional and forbidden" — absent. A model that still declared the
        # field and rejected it on the way in would pass every rejection test
        # below while being exactly the design this replaced.
        assert "conflict_keys" not in ApiWrite.model_fields

    def test_keyless_database_write_declares_no_conflict_keys_field(self):
        assert "conflict_keys" not in DatabaseKeylessWrite.model_fields

    def test_conflict_keyed_database_write_requires_its_key_set(self):
        assert DatabaseConflictKeyedWrite.model_fields["conflict_keys"].is_required()

    def test_destination_variants_carry_no_conditional_rules(self):
        # The four `allOf` branches were the hand-written mirror of the two
        # deleted validators. A reader (and an external validator) should now
        # find the rule in the variant shapes alone; a surviving branch would
        # mean the refactor left the old mirror in place.
        for variant in (DatabaseStreamDestination, ApiStreamDestination):
            schema = variant.model_json_schema()
            assert "allOf" not in schema, (
                f"{variant.__name__} still carries conditional `allOf` branches; "
                "the scope/mode split is supposed to have replaced them"
            )

    def test_published_destination_is_a_two_branch_oneof(self):
        items = StreamInput.model_json_schema()["properties"]["destinations"]["items"]
        assert {branch["$ref"] for branch in items["oneOf"]} == {
            "#/$defs/DatabaseStreamDestination",
            "#/$defs/ApiStreamDestination",
        }

    # (label, document) — every one must be rejected by model and schema alike.
    REJECTED = [
        ("db mode outside the vocabulary",
         {"endpoint_ref": _DB_ENDPOINT_REF, "write": {"mode": "overwrite"}}),
        ("api conflict_keys are endpoint-owned",
         {"endpoint_ref": _API_ENDPOINT_REF,
          "write": {"mode": "upsert", "conflict_keys": ["id"]}}),
        ("api mode outside the endpoint write-key universe",
         {"endpoint_ref": _API_ENDPOINT_REF, "write": {"mode": "create_transfer"}}),
        ("db upsert without its conflict key set",
         {"endpoint_ref": _DB_ENDPOINT_REF, "write": {"mode": "upsert"}}),
        ("db upsert with an empty conflict key set",
         {"endpoint_ref": _DB_ENDPOINT_REF,
          "write": {"mode": "upsert", "conflict_keys": []}}),
        ("insert with a conflict key set",
         {"endpoint_ref": _DB_ENDPOINT_REF,
          "write": {"mode": "insert", "conflict_keys": ["id"]}}),
        ("truncate_insert with a conflict key set",
         {"endpoint_ref": _DB_ENDPOINT_REF,
          "write": {"mode": "truncate_insert", "conflict_keys": ["id"]}}),
        ("api mode that is only whitespace",
         {"endpoint_ref": _API_ENDPOINT_REF, "write": {"mode": "   "}}),
        ("a scope no branch declares",
         {"endpoint_ref": {**_API_ENDPOINT_REF, "scope": "workspace"},
          "write": {"mode": "insert"}}),
    ]

    @pytest.mark.parametrize("label, doc", REJECTED, ids=[r[0] for r in REJECTED])
    def test_model_and_published_schema_both_reject(self, label, doc):
        assert not _DESTINATION_SCHEMA.is_valid(doc), (
            f"published schema accepts a destination the model rejects ({label}); "
            "one of the two branches is laxer than its model"
        )
        with pytest.raises(ValidationError):
            _DESTINATION.validate_python(doc)

    ACCEPTED = [
        ("db insert", {"endpoint_ref": _DB_ENDPOINT_REF, "write": {"mode": "insert"}}),
        ("db upsert with keys",
         {"endpoint_ref": _DB_ENDPOINT_REF,
          "write": {"mode": "upsert", "conflict_keys": ["id"]}}),
        ("db composite upsert key",
         {"endpoint_ref": _DB_ENDPOINT_REF,
          "write": {"mode": "upsert", "conflict_keys": ["org_id", "external_id"]}}),
        ("db truncate_insert", {"endpoint_ref": _DB_ENDPOINT_REF,
                                "write": {"mode": "truncate_insert"}}),
        ("api endpoint-owned mode",
         {"endpoint_ref": _API_ENDPOINT_REF, "write": {"mode": "upsert"}}),
        ("db insert with an execution override",
         {"endpoint_ref": _DB_ENDPOINT_REF, "write": {"mode": "insert"},
          "execution": {"batch_size": 1000}}),
    ]

    @pytest.mark.parametrize("label, doc", ACCEPTED, ids=[r[0] for r in ACCEPTED])
    def test_model_and_published_schema_both_accept(self, label, doc):
        # The other direction: a split that rejects too much is equally broken,
        # and an over-tightened branch would otherwise look like a passing test.
        assert _DESTINATION_SCHEMA.is_valid(doc), (
            f"published schema rejects a valid destination ({label})"
        )
        _DESTINATION.validate_python(doc)


class TestExecutionHasNoConcurrencyKnob:
    def test_batch_size_still_accepted(self):
        assert Execution.model_validate({"batch_size": 1000}).batch_size == 1000

    def test_max_concurrent_batches_rejected(self):
        # Retired, not deprecated: the model is closed, so a document still
        # carrying it fails rather than being quietly ignored. Breaking for any
        # stored stream that spells it out — hence stream 16.0.0 -> 17.0.0.
        with pytest.raises(ValidationError):
            Execution.model_validate({"batch_size": 1000, "max_concurrent_batches": 3})


def _mapping(target: dict, rules: list[dict], extra_assignments: list[dict] = ()) -> dict:
    """A one-assignment mapping carrying `rules`, plus any extra assignments."""
    return {
        "assignments": [
            {
                "target": target,
                "value": {
                    "kind": "expression",
                    "expression": {"op": "get", "path": [target["path"]]},
                },
                "validate": {"rules": rules},
            },
            *extra_assignments,
        ]
    }


_SCALAR_TARGET = {"path": "email", "arrow_type": "Utf8"}
_OBJECT_TARGET = {
    "path": "address",
    "arrow_type": "Object",
    "properties": {
        "city": {"arrow_type": "Utf8"},
        "geo": {
            "arrow_type": "Object",
            "properties": {"lat": {"arrow_type": "Float64"}},
        },
    },
}
_LIST_TARGET = {
    "path": "lines",
    "arrow_type": "List",
    "items": {
        "arrow_type": "Object",
        "properties": {"sku": {"arrow_type": "Utf8"}},
    },
}
# A nested field whose NAME contains a dot. `properties` keys are field names,
# not paths, so this is a legally declared field — and therefore one a rule has
# to be able to address.
_DOTTED_NAME_TARGET = {
    "path": "meta",
    "arrow_type": "Object",
    "properties": {"user.id": {"arrow_type": "Utf8"}},
}


class TestValidationRuleField:
    """`field` is a token array that must resolve against a declared target.

    Two gaps closed at once, and they fail differently: the SHAPE (a dotted
    string is no longer parseable, so the two conventions in this file cannot
    contradict each other) and the REFERENCE (a rule naming nothing used to
    validate nothing, silently — the failure mode a test must pin because
    nothing else can see it).
    """

    def test_single_token_accepted(self):
        mapping = StreamMapping.model_validate(
            _mapping(_SCALAR_TARGET, [{"type": "required", "field": ["email"]}])
        )
        assert mapping.assignments[0].validation.rules[0].field == ["email"]

    def test_dotted_string_rejected(self):
        # The old spelling. `AssignmentTarget.path` refuses a dotted path, so a
        # dotted `field` would be a second, contradictory nesting convention in
        # the same document.
        with pytest.raises(ValidationError):
            StreamMapping.model_validate(
                _mapping(_OBJECT_TARGET, [{"type": "required", "field": "address.city"}])
            )

    def test_a_dotted_token_is_one_field_name_not_a_nested_path(self):
        # A token array carries no splitting convention — that is the whole
        # reason it replaced the dotted string — so `["address.city"]` names
        # ONE field called `address.city`. `_OBJECT_TARGET` declares no such
        # field, so it fails at resolution rather than at the shape, and the
        # dotted spelling is still not a way back to nesting.
        with pytest.raises(ValidationError, match="names no assignment target"):
            StreamMapping.model_validate(
                _mapping(_OBJECT_TARGET, [{"type": "required", "field": ["address.city"]}])
            )

    def test_a_dotted_field_name_is_addressable(self):
        # The gap this closes: `properties` keys are unconstrained field names,
        # so a nested field called `user.id` is declarable. A token pattern
        # forbidding `.` made it permanently unaddressable — no spelling of a
        # rule could name it — so the contract admitted a field that could not
        # be validated at all. Tokens match the source `get` segment rule
        # instead, which is the rule this field's own description cites.
        StreamMapping.model_validate(
            _mapping(
                _DOTTED_NAME_TARGET,
                [{"type": "required", "field": ["meta", "user.id"]}],
            )
        )

    def test_a_dotted_field_name_is_not_reachable_by_splitting_it(self):
        with pytest.raises(ValidationError, match="declares no field"):
            StreamMapping.model_validate(
                _mapping(
                    _DOTTED_NAME_TARGET,
                    [{"type": "required", "field": ["meta", "user", "id"]}],
                )
            )

    def test_empty_array_rejected(self):
        with pytest.raises(ValidationError):
            StreamMapping.model_validate(
                _mapping(_SCALAR_TARGET, [{"type": "required", "field": []}])
            )

    def test_whitespace_token_rejected(self):
        with pytest.raises(ValidationError):
            StreamMapping.model_validate(
                _mapping(_SCALAR_TARGET, [{"type": "required", "field": ["  "]}])
            )

    def test_nested_object_field_resolves(self):
        mapping = StreamMapping.model_validate(
            _mapping(_OBJECT_TARGET, [{"type": "required", "field": ["address", "city"]}])
        )
        assert mapping.assignments[0].validation.rules[0].field == ["address", "city"]

    def test_deeply_nested_object_field_resolves(self):
        StreamMapping.model_validate(
            _mapping(
                _OBJECT_TARGET,
                [{"type": "required", "field": ["address", "geo", "lat"]}],
            )
        )

    def test_list_element_field_resolves(self):
        # A `List` declares its element in `items`, so the element is stepped
        # through transparently rather than named by a token of its own.
        StreamMapping.model_validate(
            _mapping(_LIST_TARGET, [{"type": "required", "field": ["lines", "sku"]}])
        )

    def test_rule_may_address_another_assignments_target(self):
        # Rules are authored per assignment but grade the record the whole
        # mapping builds, so the scope is the mapping, not the enclosing
        # assignment.
        StreamMapping.model_validate(
            _mapping(
                _SCALAR_TARGET,
                [{"type": "required", "field": ["address", "city"]}],
                extra_assignments=[
                    {
                        "target": _OBJECT_TARGET,
                        "value": {
                            "kind": "expression",
                            "expression": {"op": "get", "path": ["address"]},
                        },
                    }
                ],
            )
        )

    def test_undeclared_target_rejected(self):
        # THE silent wrong answer: a typo named no mapped output, so the rule
        # graded nothing and the document looked validated.
        with pytest.raises(ValidationError, match="names no assignment target"):
            StreamMapping.model_validate(
                _mapping(_SCALAR_TARGET, [{"type": "required", "field": ["emial"]}])
            )

    def test_undeclared_nested_field_rejected(self):
        with pytest.raises(ValidationError, match="declares no field"):
            StreamMapping.model_validate(
                _mapping(_OBJECT_TARGET, [{"type": "required", "field": ["address", "zip"]}])
            )

    def test_descending_into_a_scalar_target_rejected(self):
        # A scalar target declares no `properties`, so there is nothing beneath
        # it to address — the rule is naming a field that cannot exist.
        with pytest.raises(ValidationError, match="declares no field"):
            StreamMapping.model_validate(
                _mapping(_SCALAR_TARGET, [{"type": "required", "field": ["email", "domain"]}])
            )

    def test_published_schema_agrees_on_the_shape(self):
        # The referential rule is a model validator (it needs sibling
        # assignments, which JSON Schema cannot reach), but the SHAPE is a
        # contract commitment to external validators.
        validator = Draft202012Validator(StreamMapping.model_json_schema())
        assert validator.is_valid(
            _mapping(_OBJECT_TARGET, [{"type": "required", "field": ["address", "city"]}])
        )
        for bad in ("address.city", [], ["  "]):
            assert not validator.is_valid(
                _mapping(_OBJECT_TARGET, [{"type": "required", "field": bad}])
            ), f"published schema accepts field={bad!r}, which the model rejects"
        # `["address.city"]` is a well-SHAPED token array naming one field, so
        # the shape check passes and only the referential rule rejects it —
        # which is the split this test exists to state, not a hole in it.
        assert validator.is_valid(
            _mapping(_OBJECT_TARGET, [{"type": "required", "field": ["address.city"]}])
        )
