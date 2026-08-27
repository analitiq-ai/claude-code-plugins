"""`$ref` in an embedded response/input schema (RULE-ENDP-026).

Two halves, tested together because each only makes sense given the other:

1. **The guard** — `_validate_schema_refs`, run from `ResponseExtraction._validate`
   and `WriteInput._validate`. A `$ref` must be in-document (`#…`) and must
   resolve. A non-local ref names a document nothing on the offline
   validate/author/execute path can fetch; a dangling local ref asserts
   nothing, so every instance satisfies it. Both fail silently without the
   guard.
2. **`$ref`-aware declared-path resolution** — `effective_properties` /
   `resolve_declared_path` follow in-document refs, which is only safe
   *because* (1) already refused the two spellings that could land the
   resolver on a subtree nothing validated.

Plus `resolve_local_pointer`, the RFC 6901 walker both halves stand on. Its
"not found" answer is a sentinel rather than `None` — a pointer can legitimately
land on a JSON null — and that distinction is pinned here.
"""
import random
import threading
from typing import Any

import pytest
from pydantic import ValidationError

from analitiq.contracts.endpoints import (
    _MATERIALIZED_NODE,
    _MISSING,
    _OperationKind,
    _combine_schema_values,
    _compose_declarations,
    _property_contributors,
    _refuse_disjoint_types,
    _unresolved_harm,
    ApiEndpointDoc,
    DeclarationConflictError,
    DeclaredPathError,
    JSON_SCHEMA_LIST_OF_SCHEMA_KEYS,
    JSON_SCHEMA_SINGLE_SCHEMA_KEYS,
    JSON_SCHEMA_SUBSCHEMA_KEYS,
    ResponseExtraction,
    WriteInput,
    effective_properties,
    find_record_field_properties,
    materialize_node,
    parse_endpoint,
    resolve_declared_path,
    resolve_local_pointer,
    resolve_read_record_schema,
    resolve_schema_ref,
)
from analitiq.contracts.shared.rules import all_rules


API_SCHEMA_URL = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"

# Error-message fragments the guard emits. Matched (not restated in full) so a
# wording change does not break the suite but a change of *diagnosis* does.
NOT_IN_DOCUMENT = "is not an in-document reference"
DOES_NOT_RESOLVE = "does not resolve to a schema in this document"
MUST_BE_STRING = r"\$ref must be a string"
IS_AN_ANCHOR = "is a plain-name fragment"
NON_SCHEMA_POSITION = "points into a non-schema position"
NOT_AUTHORABLE = "is not authorable in an embedded response/input schema"


def _response(schema, records="response.body"):
    """Build a `response` block carrying `schema` (the RULE-ENDP-026 site)."""
    return {"records": {"ref": records}, "schema": schema}


def _read_doc(schema, records="response.body", pagination=None, metadata=None):
    """A minimal API read endpoint whose response.schema is `schema`."""
    read = {
        "request": {"method": "GET", "path": "/v1/x"},
        "params": {},
        "response": _response(schema, records),
    }
    if pagination is not None:
        read["pagination"] = pagination
    if metadata is not None:
        read["response"]["metadata"] = metadata
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": "records",
        "operations": {"read": read},
    }


def _endpoint_with_record_shape(items, defs):
    """A read endpoint whose records array has `items` as its record shape.

    The end-to-end frame for the composition tests: the helpers disagreeing is
    only the mechanism, and what actually ships is whether `parse_endpoint`
    accepts the document.
    """
    return _read_doc(
        {
            "type": "object",
            "$defs": defs,
            "properties": {"data": {"type": "array", "items": items}},
        },
        records="response.body.data",
    )


def _replicating_doc(schema, cursor_field, records="response.body.data"):
    """A read endpoint replicating on `cursor_field`, over `schema`.

    The cursor walk starts at the record shape — a SUBTREE — which is what
    makes it the one site where the pointer base can be got wrong.
    """
    doc = _read_doc(schema, records=records)
    read = doc["operations"]["read"]
    read["request"]["query"] = {"u": {"from_param": "u"}}
    read["params"] = {
        "u": {"in": "query", "type": "string", "required": False,
              "controlled_by": "replication"}
    }
    read["replication"] = {
        "supported_methods": ["incremental"],
        "cursor_mappings": [{"cursor_field": cursor_field, "param": "u", "operator": "gte"}],
    }
    return doc


# ---------------------------------------------------------------------------
# RULE-ENDP-026 — the guard, on response.schema and on input.schema
# ---------------------------------------------------------------------------


class TestValidRefsAccepted:
    def test_response_schema_defs_ref_accepted(self):
        schema = {
            "$schema": JSON_SCHEMA,
            "type": "array",
            "items": {"$ref": "#/$defs/Record"},
            "$defs": {"Record": {"type": "object", "properties": {"id": {"type": "string"}}}},
        }
        result = ResponseExtraction.model_validate(_response(schema))
        assert result.schema_["items"]["$ref"] == "#/$defs/Record"

    def test_input_schema_defs_ref_accepted(self):
        schema = {
            "type": "object",
            "properties": {"customer": {"$ref": "#/$defs/Customer"}},
            "$defs": {"Customer": {"type": "object", "properties": {"id": {"type": "string"}}}},
        }
        assert WriteInput.model_validate({"schema": schema}).schema_ is not None

    def test_definitions_ref_accepted(self):
        # `definitions` is the draft-07 spelling; the walker enumerates it
        # alongside `$defs`, so a ref into it must resolve like any other.
        schema = {
            "type": "object",
            "properties": {"a": {"$ref": "#/definitions/A"}},
            "definitions": {"A": {"type": "string"}},
        }
        assert WriteInput.model_validate({"schema": schema}).schema_ is not None

    def test_whole_document_self_reference_accepted(self):
        # `#` addresses the document root, which is a schema by construction.
        schema = {"type": "object", "properties": {"self": {"$ref": "#"}}}
        assert WriteInput.model_validate({"schema": schema}).schema_ is not None

    def test_ref_into_a_list_position_accepted(self):
        # A JSON Pointer may index a list — `#/allOf/0` is a legal target.
        schema = {
            "allOf": [{"type": "object", "properties": {"id": {"type": "string"}}}],
            "properties": {"echo": {"$ref": "#/allOf/0"}},
        }
        assert WriteInput.model_validate({"schema": schema}).schema_ is not None


class TestNonLocalRefsRejected:
    @pytest.mark.parametrize(
        "ref",
        [
            "https://schemas.example.com/record.json",
            "https://schemas.example.com/record.json#/$defs/Record",
            "other.json#/$defs/Record",
            "common.json",
            "definitions/Foo",
            "/absolute/path.json",
            "",
        ],
    )
    def test_response_schema_non_local_ref_rejected(self, ref):
        schema = {"type": "array", "items": {"$ref": ref}}
        with pytest.raises(ValidationError, match=NOT_IN_DOCUMENT):
            ResponseExtraction.model_validate(_response(schema))

    @pytest.mark.parametrize(
        "ref",
        [
            "https://schemas.example.com/record.json",
            "other.json#/$defs/Record",
            "definitions/Foo",
            "",
        ],
    )
    def test_input_schema_non_local_ref_rejected(self, ref):
        schema = {"type": "object", "properties": {"a": {"$ref": ref}}}
        with pytest.raises(ValidationError, match=NOT_IN_DOCUMENT):
            WriteInput.model_validate({"schema": schema})

    def test_non_local_ref_is_diagnosed_as_non_local_not_as_dangling(self):
        # The two failures have different fixes (inline/`$defs` vs. fix the
        # pointer), so an off-document ref must never be reported as dangling.
        schema = {"type": "object", "properties": {"a": {"$ref": "other.json#/$defs/A"}}}
        with pytest.raises(ValidationError) as excinfo:
            WriteInput.model_validate({"schema": schema})
        assert NOT_IN_DOCUMENT in str(excinfo.value)
        assert "does not resolve to a schema" not in str(excinfo.value)


class TestDanglingRefsRejected:
    def test_response_schema_dangling_ref_rejected(self):
        schema = {
            "type": "array",
            "items": {"$ref": "#/$defs/Typo"},
            "$defs": {"Record": {"type": "object"}},
        }
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE):
            ResponseExtraction.model_validate(_response(schema))

    def test_input_schema_dangling_ref_rejected(self):
        schema = {
            "type": "object",
            "properties": {"a": {"$ref": "#/$defs/Typo"}},
            "$defs": {"A": {"type": "string"}},
        }
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE):
            WriteInput.model_validate({"schema": schema})

    def test_ref_with_no_defs_at_all_rejected(self):
        schema = {"type": "object", "properties": {"a": {"$ref": "#/$defs/A"}}}
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE):
            WriteInput.model_validate({"schema": schema})

    def test_out_of_range_list_index_rejected(self):
        schema = {
            "allOf": [{"type": "object"}],
            "properties": {"a": {"$ref": "#/allOf/7"}},
        }
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE):
            WriteInput.model_validate({"schema": schema})

    def test_plain_name_anchor_fragment_rejected(self):
        # `#Foo` is a 2020-12 `$anchor` reference, not a JSON Pointer. Anchors
        # are not part of the authorable node vocabulary, so nothing resolves
        # it — it must be refused rather than silently treated as in-document.
        #
        # The DIAGNOSIS matters as much as the rejection: this document is not
        # dangling — `$anchor: "Foo"` is right there and a conformant resolver
        # finds it. Telling the author "does not resolve" would send them
        # hunting a typo that does not exist. The refusal must name the real
        # reason (anchors are not addressable in this contract) and the real
        # fix (`#/$defs/<name>`), which `IS_AN_ANCHOR` pins.
        schema = {"type": "object", "properties": {"a": {"$ref": "#Foo"}}, "$anchor": "Foo"}
        with pytest.raises(ValidationError, match=IS_AN_ANCHOR) as excinfo:
            WriteInput.model_validate({"schema": schema})
        assert "#/$defs/<name>" in str(excinfo.value)
        assert DOES_NOT_RESOLVE not in str(excinfo.value)

    def test_ref_through_a_scalar_intermediate_rejected(self):
        schema = {
            "title": "not a container",
            "properties": {"a": {"$ref": "#/title/nested"}},
        }
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE):
            WriteInput.model_validate({"schema": schema})


class TestNonStringRefsRejected:
    @pytest.mark.parametrize("ref", [7, None, True, ["#/$defs/A"], {"$ref": "#"}])
    def test_non_string_ref_rejected_on_input_schema(self, ref):
        schema = {
            "type": "object",
            "properties": {"a": {"$ref": ref}},
            "$defs": {"A": {"type": "string"}},
        }
        with pytest.raises(ValidationError, match=MUST_BE_STRING):
            WriteInput.model_validate({"schema": schema})

    def test_non_string_ref_rejected_on_response_schema(self):
        schema = {"type": "array", "items": {"$ref": 7}}
        with pytest.raises(ValidationError, match=MUST_BE_STRING):
            ResponseExtraction.model_validate(_response(schema))


class TestGuardWalkReachesEveryStructuralPosition:
    """The walk must cover the same positions as the arrow_type walker.

    A `$ref` that escapes the walk is exactly the hole RULE-ENDP-026 closes, so
    each structural keyword family gets its own case.
    """

    @pytest.mark.parametrize(
        ("schema", "where"),
        [
            # top level
            ({"$ref": "#/$defs/Typo"}, "root"),
            # map-of-schemas keywords
            ({"properties": {"x": {"$ref": "#/$defs/Typo"}}}, "properties"),
            ({"patternProperties": {"^x$": {"$ref": "#/$defs/Typo"}}}, "patternProperties"),
            ({"$defs": {"Y": {"$ref": "#/$defs/Typo"}}}, "$defs"),
            ({"definitions": {"Y": {"$ref": "#/$defs/Typo"}}}, "definitions"),
            ({"dependentSchemas": {"x": {"$ref": "#/$defs/Typo"}}}, "dependentSchemas"),
            # list-of-schemas keywords
            ({"allOf": [{"$ref": "#/$defs/Typo"}]}, "allOf"),
            ({"anyOf": [{"type": "string"}, {"$ref": "#/$defs/Typo"}]}, "anyOf"),
            ({"oneOf": [{"$ref": "#/$defs/Typo"}]}, "oneOf"),
            ({"prefixItems": [{"$ref": "#/$defs/Typo"}]}, "prefixItems"),
            # single-schema keywords
            ({"items": {"$ref": "#/$defs/Typo"}}, "items"),
            ({"contains": {"$ref": "#/$defs/Typo"}}, "contains"),
            ({"additionalProperties": {"$ref": "#/$defs/Typo"}}, "additionalProperties"),
            ({"propertyNames": {"$ref": "#/$defs/Typo"}}, "propertyNames"),
            ({"unevaluatedItems": {"$ref": "#/$defs/Typo"}}, "unevaluatedItems"),
            ({"unevaluatedProperties": {"$ref": "#/$defs/Typo"}}, "unevaluatedProperties"),
            ({"not": {"$ref": "#/$defs/Typo"}}, "not"),
            ({"if": {"$ref": "#/$defs/Typo"}}, "if"),
            ({"then": {"$ref": "#/$defs/Typo"}}, "then"),
            ({"else": {"$ref": "#/$defs/Typo"}}, "else"),
            # draft-2019 tuple-form `items`
            ({"items": [{"type": "string"}, {"$ref": "#/$defs/Typo"}]}, "items[tuple]"),
        ],
    )
    def test_dangling_ref_in_each_structural_position_is_caught(self, schema, where):
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE) as excinfo:
            WriteInput.model_validate({"schema": dict(schema, type="object")})
        assert "#/$defs/Typo" in str(excinfo.value), where

    def test_ref_nested_three_levels_deep_is_caught(self):
        schema = {
            "type": "object",
            "properties": {
                "x": {"type": "array", "items": {"properties": {"y": {"$ref": "#/$defs/Typo"}}}}
            },
        }
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE):
            WriteInput.model_validate({"schema": schema})

    def test_ref_under_allof_zero_is_caught(self):
        schema = {"type": "object", "allOf": [{"properties": {"y": {"$ref": "#/$defs/Typo"}}}]}
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE):
            WriteInput.model_validate({"schema": schema})

    def test_ref_inside_a_defs_subtree_is_caught(self):
        # `$defs.Y` is itself walked, so a bad ref *inside* a definition — even
        # one nothing references — must still be refused.
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "$defs": {"Y": {"properties": {"z": {"$ref": "#/$defs/Nope"}}}},
        }
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE):
            WriteInput.model_validate({"schema": schema})

    def test_error_path_names_the_offending_position(self):
        schema = {
            "type": "object",
            "properties": {"x": {"items": {"$ref": "#/$defs/Typo"}}},
        }
        with pytest.raises(ValidationError, match=r"input\.schema\.properties\.x\.items\.\$ref"):
            WriteInput.model_validate({"schema": schema})

    def test_response_error_path_is_anchored_at_response_schema(self):
        schema = {"type": "array", "items": {"$ref": "#/$defs/Typo"}}
        with pytest.raises(ValidationError, match=r"response\.schema\.items\.\$ref"):
            ResponseExtraction.model_validate(_response(schema))


class TestGuardEndToEndOnADocument:
    def test_non_local_ref_rejected_when_parsing_a_whole_endpoint(self):
        schema = {"type": "array", "items": {"$ref": "https://example.com/rec.json"}}
        with pytest.raises(ValidationError, match=NOT_IN_DOCUMENT):
            parse_endpoint(_read_doc(schema))

    def test_valid_ref_accepted_when_parsing_a_whole_endpoint(self):
        schema = {
            "type": "array",
            "items": {"$ref": "#/$defs/Record"},
            "$defs": {"Record": {"type": "object", "properties": {"id": {"type": "string"}}}},
        }
        assert isinstance(parse_endpoint(_read_doc(schema)), ApiEndpointDoc)

    def test_write_input_ref_rejected_when_parsing_a_whole_endpoint(self):
        doc = {
            "$schema": API_SCHEMA_URL,
            "endpoint_id": "records",
            "operations": {
                "write": {
                    "insert": {
                        "request": {
                            "method": "POST",
                            "path": "/v1/x",
                            "headers": {"Accept": "application/json"},
                            "body": {"r": {"from_input": "record"}},
                        },
                        "params": {},
                        "input": {
                            "schema": {
                                "type": "object",
                                "properties": {"a": {"$ref": "#/$defs/Typo"}},
                            }
                        },
                    }
                }
            },
        }
        with pytest.raises(ValidationError, match=DOES_NOT_RESOLVE):
            parse_endpoint(doc)


class TestRuleRegistration:
    def test_rule_endp_026_is_registered_against_both_embedded_schema_classes(self):
        # Look the rule up by id rather than `next(...)`: an unregistered rule
        # is the failure this test exists to catch, and a KeyError on a dict
        # names it, where a bare StopIteration does not.
        rules = {rule.id: rule for rule in all_rules()}
        assert "RULE-ENDP-026" in rules, "RULE-ENDP-026 is not registered"
        rule = rules["RULE-ENDP-026"]
        assert set(rule.targets) == {"ResponseExtraction", "WriteInput"}
        # The record's `validator` binds one symbol; the same method name must
        # exist on every target, since the rule runs on each.
        enforcer = rule.validator_symbol.split(".")[-1]
        assert hasattr(ResponseExtraction, enforcer)
        assert hasattr(WriteInput, enforcer)


# ---------------------------------------------------------------------------
# resolve_local_pointer — the RFC 6901 walker both halves stand on
# ---------------------------------------------------------------------------


class TestResolveLocalPointer:
    ROOT = {
        "$defs": {
            "X": {"type": "string"},
            "a/b": {"type": "integer"},
            "~1": {"type": "boolean"},
            "Null": None,
            "": {"type": "null"},
        },
        "allOf": [{"i": 0}, {"i": 1}],
        "title": "scalar",
    }

    def test_bare_hash_resolves_to_the_document_root(self):
        assert resolve_local_pointer(self.ROOT, "#") is self.ROOT

    def test_pointer_into_defs(self):
        assert resolve_local_pointer(self.ROOT, "#/$defs/X") == {"type": "string"}

    def test_pointer_into_a_list(self):
        assert resolve_local_pointer(self.ROOT, "#/allOf/0") == {"i": 0}
        assert resolve_local_pointer(self.ROOT, "#/allOf/1") == {"i": 1}

    def test_tilde_one_unescapes_to_slash(self):
        assert resolve_local_pointer(self.ROOT, "#/$defs/a~1b") == {"type": "integer"}

    def test_tilde_zero_unescapes_to_tilde_in_the_right_order(self):
        # `~1` as a literal key encodes as `~01`. Unescaping `~0` first would
        # yield `~1` and then re-unescape it to `/` — the classic RFC 6901 trap.
        assert resolve_local_pointer(self.ROOT, "#/$defs/~01") == {"type": "boolean"}

    def test_empty_token_is_a_real_key_not_a_no_op(self):
        assert resolve_local_pointer(self.ROOT, "#/$defs/") == {"type": "null"}

    def test_out_of_range_list_index_is_missing(self):
        assert resolve_local_pointer(self.ROOT, "#/allOf/2") is _MISSING

    @pytest.mark.parametrize("token", ["x", "-1", "1.0", "01x", "+1", ""])
    def test_non_digit_index_into_a_list_is_missing(self, token):
        assert resolve_local_pointer(self.ROOT, f"#/allOf/{token}") is _MISSING

    def test_pointer_through_a_scalar_intermediate_is_missing(self):
        assert resolve_local_pointer(self.ROOT, "#/title/nested") is _MISSING

    def test_absent_key_is_missing(self):
        assert resolve_local_pointer(self.ROOT, "#/nope") is _MISSING
        assert resolve_local_pointer(self.ROOT, "#/$defs/Nope") is _MISSING

    def test_plain_name_anchor_fragment_does_not_resolve(self):
        assert resolve_local_pointer(self.ROOT, "#Foo") is _MISSING

    @pytest.mark.parametrize(
        "ref",
        [
            "https://example.com/x.json#/$defs/X",
            "other.json#/$defs/X",
            "/$defs/X",
            "$defs/X",
            "",
        ],
    )
    def test_non_local_refs_are_never_resolved(self, ref):
        assert resolve_local_pointer(self.ROOT, ref) is _MISSING

    @pytest.mark.parametrize("ref", [None, 7, ["#"], {"$ref": "#"}])
    def test_non_string_ref_is_missing_not_an_exception(self, ref):
        assert resolve_local_pointer(self.ROOT, ref) is _MISSING

    def test_found_null_is_distinguishable_from_not_found(self):
        # THE reason the function returns a sentinel: `None` is a legal thing
        # to find at a pointer, so "found null" must not read as "not found".
        found = resolve_local_pointer(self.ROOT, "#/$defs/Null")
        assert found is None
        assert found is not _MISSING
        assert resolve_local_pointer(self.ROOT, "#/$defs/Absent") is _MISSING

    def test_found_null_and_not_found_are_different_answers(self):
        assert resolve_local_pointer(self.ROOT, "#/$defs/Null") is not resolve_local_pointer(
            self.ROOT, "#/$defs/Absent"
        )

    def test_root_may_be_a_list(self):
        assert resolve_local_pointer([{"a": 1}], "#/0") == {"a": 1}


# ---------------------------------------------------------------------------
# effective_properties / resolve_declared_path — following in-document refs
# ---------------------------------------------------------------------------


def _run_with_timeout(fn, seconds=5.0):
    """Run `fn` on a daemon thread; fail rather than hang the suite.

    A cycle bug in the resolver is an infinite recursion or an infinite loop,
    and a hung suite is worse than a failed test.
    """
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        pytest.fail(f"resolution did not terminate within {seconds}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


class TestEffectivePropertiesFollowsRefs:
    def test_ref_target_properties_are_contributed(self):
        node = {
            "$ref": "#/$defs/Base",
            "$defs": {"Base": {"properties": {"base": {"type": "integer"}}}},
        }
        assert sorted(effective_properties(node, node)) == ["base"]

    def test_ref_and_own_properties_both_apply(self):
        # 2020-12: `$ref` is an applicator that applies ALONGSIDE its siblings
        # (a one-branch `allOf`), not a replacement for the node.
        node = {
            "$ref": "#/$defs/Base",
            "properties": {"own": {"type": "string"}},
            "$defs": {"Base": {"properties": {"base": {"type": "integer"}}}},
        }
        assert sorted(effective_properties(node, node)) == ["base", "own"]

    def test_ref_inside_an_allof_branch_is_followed(self):
        node = {
            "allOf": [{"$ref": "#/$defs/Base"}],
            "properties": {"own": {"type": "string"}},
            "$defs": {"Base": {"properties": {"base": {"type": "integer"}}}},
        }
        assert sorted(effective_properties(node, node)) == ["base", "own"]

    def test_ref_chain_is_followed_transitively(self):
        node = {
            "$ref": "#/$defs/A",
            "$defs": {
                "A": {"$ref": "#/$defs/B", "properties": {"a": {"type": "string"}}},
                "B": {"properties": {"b": {"type": "string"}}},
            },
        }
        assert sorted(effective_properties(node, node)) == ["a", "b"]

    def test_unresolvable_ref_contributes_nothing_and_does_not_raise(self):
        node = {"$ref": "#/$defs/Missing", "properties": {"own": {"type": "string"}}}
        assert sorted(effective_properties(node, node)) == ["own"]

    def test_non_local_ref_contributes_nothing(self):
        node = {"$ref": "other.json#/$defs/Base", "properties": {"own": {"type": "string"}}}
        assert sorted(effective_properties(node, node)) == ["own"]

    def test_equal_redeclaration_across_ref_and_properties_is_accepted(self):
        node = {
            "$ref": "#/$defs/Base",
            "properties": {"x": {"type": "string"}},
            "$defs": {"Base": {"properties": {"x": {"type": "string"}}}},
        }
        assert effective_properties(node, node) == {"x": {"type": "string"}}

    def test_conflicting_redeclaration_across_ref_and_properties_raises(self):
        node = {
            "$ref": "#/$defs/Base",
            "properties": {"x": {"type": "string"}},
            "$defs": {"Base": {"properties": {"x": {"type": "integer"}}}},
        }
        # A contradiction has no path coordinates: `effective_properties`
        # inspects a NODE, so it cannot know where that node sits in anyone's
        # path. It raises the coordinate-free half of the pair; only
        # `resolve_declared_path` — which knows the segment it was resolving —
        # converts it into a `DeclaredPathError`. Pinning the base class here
        # would let the old placeholder (`segment=None, index=-1`) back in.
        with pytest.raises(
            DeclarationConflictError, match="conflicting redeclaration of 'x'"
        ) as exc:
            effective_properties(node, node)
        assert not isinstance(exc.value, DeclaredPathError)
        assert not hasattr(exc.value, "segment")

    def test_root_defaults_to_the_node_itself(self):
        # Documented default: when the node IS the whole embedded schema, its
        # own `$defs` are addressable without passing `root`.
        node = {"$ref": "#/$defs/Base", "$defs": {"Base": {"properties": {"b": {}}}}}
        assert sorted(effective_properties(node)) == ["b"]

    def test_a_subtree_node_needs_the_document_root_to_see_defs(self):
        # The mirror image, stated as a fact about the contract: resolving a
        # subtree node against ITSELF cannot find the document's `$defs`.
        root = {
            "properties": {"data": {"$ref": "#/$defs/D"}},
            "$defs": {"D": {"properties": {"id": {}}}},
        }
        subtree = root["properties"]["data"]
        assert effective_properties(subtree, subtree) == {}
        assert sorted(effective_properties(subtree, root)) == ["id"]


class TestResolveDeclaredPathThroughRefs:
    ROOT = {
        "type": "object",
        "properties": {"data": {"$ref": "#/$defs/Data"}},
        "$defs": {
            "Data": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "meta": {"$ref": "#/$defs/Meta"}},
            },
            "Meta": {"type": "object", "properties": {"total": {"type": "integer"}}},
        },
    }

    def test_path_through_a_ref_resolves(self):
        assert resolve_declared_path(self.ROOT, ["data", "id"]) == {"type": "string"}

    def test_path_through_two_chained_refs_resolves(self):
        assert resolve_declared_path(self.ROOT, ["data", "meta", "total"]) == {"type": "integer"}

    def test_ref_met_partway_down_resolves_against_the_document_root(self):
        # The load-bearing threading rule. `#/$defs/Meta` is only reachable
        # from the document root; if the resolver threaded the CURRENT subtree
        # as the pointer base, `data.meta` would be a bare `{"$ref": ...}` with
        # no `$defs` under it and `total` would not resolve. `$defs` is
        # deliberately absent from every subtree here so the wrong base cannot
        # accidentally succeed.
        assert "$defs" not in self.ROOT["$defs"]["Data"]
        assert "$defs" not in self.ROOT["properties"]["data"]
        assert resolve_declared_path(self.ROOT, ["data", "meta", "total"]) == {"type": "integer"}

    def test_typo_past_a_ref_is_a_plain_undeclared_segment(self):
        # A resolvable ref has already contributed everything it declares, so a
        # still-missing segment is a typo — not "unresolvable". The two have
        # different fixes.
        with pytest.raises(DeclaredPathError, match="'nope' is not declared") as excinfo:
            resolve_declared_path(self.ROOT, ["data", "nope"])
        assert excinfo.value.segment == "nope"
        assert excinfo.value.index == 1
        assert "not statically resolvable" not in excinfo.value.reason

    def test_typo_in_the_segment_before_the_ref_is_reported_at_that_segment(self):
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(self.ROOT, ["dta", "id"])
        assert excinfo.value.segment == "dta"
        assert excinfo.value.index == 0

    def test_unresolvable_ref_classifies_as_not_statically_resolvable(self):
        # `$ref` is normally NOT a conditional keyword (it is followed), but a
        # ref that does not resolve makes the node genuinely unknowable, so the
        # diagnosis flips.
        root = {"type": "object", "properties": {"data": {"$ref": "#/$defs/Missing"}}}
        with pytest.raises(DeclaredPathError, match="not statically resolvable") as excinfo:
            resolve_declared_path(root, ["data", "id"])
        assert "$ref" in excinfo.value.reason
        assert excinfo.value.segment == "id"

    def test_non_local_ref_also_classifies_as_not_statically_resolvable(self):
        root = {"type": "object", "properties": {"data": {"$ref": "other.json#/D"}}}
        with pytest.raises(DeclaredPathError, match="not statically resolvable"):
            resolve_declared_path(root, ["data", "id"])

    def test_resolvable_ref_is_not_listed_as_a_conditional_keyword(self):
        # A node whose ref DID resolve must not be blamed for the miss.
        root = {
            "type": "object",
            "properties": {"data": {"$ref": "#/$defs/D"}},
            "$defs": {"D": {"properties": {"id": {}}}},
        }
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(root, ["data", "nope"])
        assert "$ref" not in excinfo.value.reason

    def test_monotonicity_properties_still_wins_over_a_sibling_ref(self):
        # The ambiguity check fires ONLY when the segment was not found, so a
        # node declaring both `properties.<seg>` and a `$ref` resolves through
        # `properties` exactly as the old properties-only walk did.
        root = {
            "properties": {
                "data": {
                    "$ref": "#/$defs/Other",
                    "properties": {"id": {"type": "string"}},
                }
            },
            "$defs": {"Other": {"properties": {"unrelated": {}}}},
        }
        assert resolve_declared_path(root, ["data", "id"]) == {"type": "string"}

    def test_ref_conflict_is_reframed_with_the_segments_coordinates(self):
        root = {
            "properties": {
                "data": {
                    "$ref": "#/$defs/Base",
                    "properties": {"x": {"type": "string"}},
                }
            },
            "$defs": {"Base": {"properties": {"x": {"type": "integer"}}}},
        }
        with pytest.raises(DeclaredPathError, match="conflicting redeclaration") as excinfo:
            resolve_declared_path(root, ["data", "x"])
        assert excinfo.value.segment == "x"
        assert excinfo.value.index == 1

    def test_empty_segments_resolve_to_the_root_even_with_a_ref(self):
        assert resolve_declared_path(self.ROOT, []) is self.ROOT

    def test_root_level_ref_is_followed(self):
        root = {
            "$ref": "#/$defs/Envelope",
            "$defs": {"Envelope": {"properties": {"data": {"type": "array"}}}},
        }
        assert resolve_declared_path(root, ["data"]) == {"type": "array"}


class TestRecursiveSchemasTerminate:
    def test_self_recursive_defs_node_terminates(self):
        root = {
            "$ref": "#/$defs/Node",
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "child": {"$ref": "#/$defs/Node"},
                    },
                }
            },
        }
        result = _run_with_timeout(
            lambda: resolve_declared_path(root, ["child", "child", "child", "id"])
        )
        assert result == {"type": "string"}

    def test_direct_self_reference_terminates(self):
        root = {"$ref": "#", "properties": {"a": {"type": "string"}}}
        assert _run_with_timeout(lambda: sorted(effective_properties(root, root))) == ["a"]

    def test_cycle_through_allof_terminates(self):
        root = {
            "$ref": "#/$defs/A",
            "$defs": {"A": {"allOf": [{"$ref": "#/$defs/A"}], "properties": {"x": {"type": "string"}}}},
        }
        assert _run_with_timeout(lambda: resolve_declared_path(root, ["x"])) == {"type": "string"}

    def test_mutually_recursive_defs_terminate(self):
        root = {
            "$ref": "#/$defs/A",
            "$defs": {
                "A": {"$ref": "#/$defs/B", "properties": {"a": {"type": "string"}}},
                "B": {"$ref": "#/$defs/A", "properties": {"b": {"type": "string"}}},
            },
        }
        assert _run_with_timeout(lambda: sorted(effective_properties(root, root))) == ["a", "b"]

    def test_recursive_schema_survives_the_guard_and_full_parse(self):
        schema = {
            "$schema": JSON_SCHEMA,
            "type": "array",
            "items": {"$ref": "#/$defs/Node"},
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "children": {"type": "array", "items": {"$ref": "#/$defs/Node"}},
                    },
                }
            },
        }
        assert isinstance(_run_with_timeout(lambda: parse_endpoint(_read_doc(schema))), ApiEndpointDoc)


# ---------------------------------------------------------------------------
# The two together: cross-block paths (RULE-ENDP-023) resolving through `$defs`
# ---------------------------------------------------------------------------


class TestCrossBlockPathsThroughRefs:
    SCHEMA = {
        "type": "object",
        "properties": {
            "data": {"type": "array", "items": {"type": "object"}},
            "links": {"$ref": "#/$defs/Links"},
        },
        "$defs": {"Links": {"type": "object", "properties": {"next": {"type": "string"}}}},
    }

    @staticmethod
    def _link_pagination(path):
        return {
            "type": "link",
            "link": {"next_url": {"ref": path}},
            "stop_when": {"missing": {"ref": path}},
        }

    def test_pagination_ref_resolving_through_defs_is_accepted(self):
        doc = _read_doc(
            self.SCHEMA,
            records="response.body.data",
            pagination=self._link_pagination("response.body.links.next"),
        )
        assert isinstance(parse_endpoint(doc), ApiEndpointDoc)

    def test_pagination_typo_past_a_ref_is_still_caught(self):
        doc = _read_doc(
            self.SCHEMA,
            records="response.body.data",
            pagination=self._link_pagination("response.body.links.nxt"),
        )
        with pytest.raises(ValidationError, match="'nxt' is not declared"):
            parse_endpoint(doc)

    def test_metadata_ref_resolving_through_defs_is_accepted(self):
        doc = _read_doc(
            self.SCHEMA,
            records="response.body.data",
            metadata={"next_link": {"ref": "response.body.links.next"}},
        )
        assert isinstance(parse_endpoint(doc), ApiEndpointDoc)

    def test_metadata_typo_past_a_ref_is_still_caught(self):
        doc = _read_doc(
            self.SCHEMA,
            records="response.body.data",
            metadata={"next_link": {"ref": "response.body.links.nxt"}},
        )
        with pytest.raises(ValidationError, match="'nxt' is not declared"):
            parse_endpoint(doc)


# ---------------------------------------------------------------------------
# A ref must land on a SCHEMA position, not merely somewhere in the document
# ---------------------------------------------------------------------------


class TestRefsIntoNonSchemaPositions:
    """`#/properties/x/default` is a valid JSON Pointer into a real node — and a
    hole. Neither structural walker descends into `default`/`examples`/`const`/
    `enum` (they carry arbitrary user data that can look exactly like a schema),
    so a ref landing there reaches a subtree nothing annotation-checked and no
    type map covers. That is the same harm as a non-local ref, spelled locally,
    so it gets the same answer: refusal.
    """

    #: A node the arrow_type walker rejects on sight — the marker that tells us
    #: whether the walker reached a position at all.
    UNCHECKED = {"type": "string", "native_type": "str", "arrow_type": "Timestamp"}

    def test_control_the_same_payload_in_defs_is_rejected(self):
        # The control half of the pair: in a position the walker DOES visit,
        # this payload is caught. So a later acceptance can only mean the
        # position was never visited.
        schema = {
            "type": "object",
            "properties": {"data": {"type": "array", "items": {"type": "object"}}},
            "$defs": {"Page": {"type": "object", "properties": {"cursor": self.UNCHECKED}}},
        }
        with pytest.raises(ValidationError, match="is not a canonical Arrow type"):
            parse_endpoint(_read_doc(schema, records="response.body.data"))

    def test_ref_into_a_default_payload_is_rejected(self):
        schema = {
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"type": "object"}},
                "meta": {
                    "type": "object",
                    "default": {"type": "object", "properties": {"cursor": self.UNCHECKED}},
                },
                "page": {"$ref": "#/properties/meta/default"},
            },
        }
        with pytest.raises(ValidationError, match=NON_SCHEMA_POSITION):
            parse_endpoint(_read_doc(schema, records="response.body.data"))

    @pytest.mark.parametrize("keyword", ["default", "const", "examples", "enum"])
    def test_every_data_carrying_keyword_is_refused_as_a_ref_target(self, keyword):
        payload = [{"type": "object"}] if keyword in ("examples", "enum") else {"type": "object"}
        pointer = (
            f"#/properties/meta/{keyword}/0"
            if keyword in ("examples", "enum")
            else f"#/properties/meta/{keyword}"
        )
        schema = {
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"type": "object"}},
                "meta": {"type": "object", keyword: payload},
                "page": {"$ref": pointer},
            },
        }
        with pytest.raises(ValidationError, match=NON_SCHEMA_POSITION):
            parse_endpoint(_read_doc(schema, records="response.body.data"))

    def test_the_diagnosis_is_not_confused_with_dangling(self):
        """A ref into a real-but-wrong position and a ref into nothing are
        different mistakes with different fixes, so they must read
        differently."""
        schema = {
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"type": "object"}},
                "meta": {"type": "object", "default": {"type": "object"}},
                "page": {"$ref": "#/properties/meta/default"},
            },
        }
        with pytest.raises(ValidationError) as excinfo:
            parse_endpoint(_read_doc(schema, records="response.body.data"))
        assert DOES_NOT_RESOLVE not in str(excinfo.value)

    def test_resolution_cannot_reach_a_non_schema_position(self):
        """The resolver half: even called directly on a document the guard
        would have refused, following the ref must not hand back the
        unvalidated node."""
        schema = {
            "properties": {
                "meta": {"default": {"properties": {"cursor": self.UNCHECKED}}},
                "page": {"$ref": "#/properties/meta/default"},
            }
        }
        with pytest.raises(DeclaredPathError):
            resolve_declared_path(schema, ["page", "cursor"])

    def test_a_legitimate_pointer_through_properties_still_resolves(self):
        """The restriction is to schema POSITIONS, not to `$defs`: a pointer
        through `properties` addresses a real subschema and must keep working."""
        schema = {
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"type": "object"}},
                "meta": {"type": "object", "properties": {"cursor": {"type": "string"}}},
                "page": {"$ref": "#/properties/meta"},
            },
        }
        parse_endpoint(_read_doc(schema, records="response.body.data"))
        assert resolve_declared_path(schema, ["page", "cursor"]) == {"type": "string"}


# ---------------------------------------------------------------------------
# Reference keywords the contract does not author
# ---------------------------------------------------------------------------


class TestRefusedReferenceKeywords:
    """`$id` and `$dynamicRef` defeat RULE-ENDP-026 by the exact mechanism it
    exists to close, so the guard refuses the keywords rather than trying to
    follow them.

    `$id` retargets the base URI: under 2020-12 a `#`-leading ref beneath an
    `$id` is a reference into ANOTHER document, so a resolver that ignores `$id`
    (as a pointer walk must) and a conformant one disagree about which
    subschema applied. `$dynamicRef` defers its target to evaluation time, so
    nothing offline can decide what it points at.
    """

    @staticmethod
    def _schema_with(node):
        return {
            "type": "object",
            "properties": {"data": {"type": "array", "items": node}},
            "$defs": {"Thing": {"type": "object", "properties": {"id": {"type": "string"}}}},
        }

    def test_subschema_id_retargeting_is_refused(self):
        node = {"$id": "https://evil.example/sub", "$ref": "#/$defs/Thing"}
        with pytest.raises(ValidationError, match=NOT_AUTHORABLE) as excinfo:
            parse_endpoint(_read_doc(self._schema_with(node), records="response.body.data"))
        assert "$id" in str(excinfo.value)

    def test_root_level_id_is_refused(self):
        schema = self._schema_with({"$ref": "#/$defs/Thing"})
        schema["$id"] = "https://evil.example/root"
        with pytest.raises(ValidationError, match=NOT_AUTHORABLE):
            parse_endpoint(_read_doc(schema, records="response.body.data"))

    @pytest.mark.parametrize(
        "keyword, value",
        [
            ("$dynamicRef", "https://evil.example/x.json#meta"),
            ("$dynamicRef", "#nope"),
            ("$dynamicAnchor", "meta"),
            ("$recursiveRef", "#"),
            ("$recursiveAnchor", True),
            ("$anchor", "thing"),
        ],
    )
    def test_dynamic_and_anchor_keywords_are_refused(self, keyword, value):
        node = {"type": "object", keyword: value}
        with pytest.raises(ValidationError, match=NOT_AUTHORABLE) as excinfo:
            parse_endpoint(_read_doc(self._schema_with(node), records="response.body.data"))
        assert keyword in str(excinfo.value)

    def test_the_refusal_names_the_supported_spelling(self):
        node = {"type": "object", "$dynamicRef": "#meta"}
        with pytest.raises(ValidationError) as excinfo:
            parse_endpoint(_read_doc(self._schema_with(node), records="response.body.data"))
        assert "#/$defs/<name>" in str(excinfo.value)

    def test_input_schema_is_guarded_too(self):
        # Both targets of RULE-ENDP-026, not just the response side.
        with pytest.raises(ValidationError, match=NOT_AUTHORABLE):
            WriteInput.model_validate({"schema": {"type": "object", "$id": "https://evil.example/x"}})


# ---------------------------------------------------------------------------
# The record shape reached through a ref — the shape the guard's own message
# tells authors to write
# ---------------------------------------------------------------------------


class TestRecordShapeThroughRefs:
    """RULE-ENDP-026 rejects a non-local ref with "put it in this document's
    `$defs`". An author who follows that advice must get a working connector —
    not a document that validates and then extracts zero fields.
    """

    SCHEMA = {
        "type": "object",
        "properties": {"data": {"type": "array", "items": {"$ref": "#/$defs/Rec"}}},
        "$defs": {
            "Rec": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "updated_at": {"type": "string"}},
            }
        },
    }

    def test_record_fields_are_enumerated_through_the_ref(self):
        record = resolve_read_record_schema(
            {"records": {"ref": "response.body.data"}}, self.SCHEMA
        )
        assert find_record_field_properties(record) == self.SCHEMA["$defs"]["Rec"]["properties"]

    def test_record_fields_are_enumerated_through_an_allof_ref(self):
        schema = {
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"allOf": [{"$ref": "#/$defs/Rec"}]}}
            },
            "$defs": self.SCHEMA["$defs"],
        }
        record = resolve_read_record_schema({"records": {"ref": "response.body.data"}}, schema)
        assert set(find_record_field_properties(record) or {}) == {"id", "updated_at"}

    def test_a_ref_shaped_collection_node_is_recognised_as_an_array(self):
        """The mirror failure: the ARRAY itself behind a ref. Reading `type` off
        the raw node sees `None` and rejects a perfectly good document."""
        schema = {
            "type": "object",
            "properties": {"data": {"$ref": "#/$defs/Coll"}},
            "$defs": {
                "Coll": {"type": "array", "items": {"type": "object",
                                                    "properties": {"id": {"type": "string"}}}}
            },
        }
        parse_endpoint(_read_doc(schema, records="response.body.data"))
        record = resolve_read_record_schema({"records": {"ref": "response.body.data"}}, schema)
        assert set(find_record_field_properties(record) or {}) == {"id"}

    def test_cursor_field_resolves_through_a_ref_record_shape(self):
        """The record shape is a SUBTREE of `response.schema`, so the cursor
        walk must resolve `#/$defs/…` against the DOCUMENT. Rooting it at the
        subtree finds no `$defs` and reports a declared field as undeclared —
        with a diagnosis ("declare it under 'properties'") that is factually
        wrong, since it already is."""
        parse_endpoint(_replicating_doc(self.SCHEMA, "updated_at"))

    def test_cursor_field_resolves_through_an_allof_ref_record_shape(self):
        schema = {
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"allOf": [{"$ref": "#/$defs/Rec"}]}}
            },
            "$defs": self.SCHEMA["$defs"],
        }
        parse_endpoint(_replicating_doc(schema, "updated_at"))

    def test_a_typo_in_a_cursor_field_is_still_caught_through_a_ref(self):
        with pytest.raises(ValidationError, match="not declared in response.schema record-shape"):
            parse_endpoint(_replicating_doc(self.SCHEMA, "updated_ta"))


class TestThePublishedLandingSetMatchesTheResolver:
    """The published algorithm names the positions a `$ref` may land on, and
    `schemas/api-endpoint/16.1.0.json` is immutable once merged — a wrong list
    there can only be superseded, never corrected.

    It used to enumerate all twenty keywords `JsonSchemaPropertyNode`
    constrains, then say separately that a path must not CROSS a conditional
    one. A reader takes "cross" as "pass through", so `#/properties/x/not` read
    as legal. `resolve_schema_ref` tests every token INCLUDING the last, so only
    eight of the twenty are landable.
    """

    LANDABLE = {
        "properties", "$defs", "definitions",     # maps
        "allOf", "prefixItems",                   # lists
        "items", "propertyNames", "contentSchema",  # single
    }

    def _root(self):
        leaf = {"type": "string"}
        return {
            "$defs": {"A": leaf},
            "definitions": {"B": leaf},
            "properties": {"x": {
                "type": "object",
                "allOf": [leaf],
                "prefixItems": [leaf],
                "items": leaf,
                "propertyNames": leaf,
                "contentSchema": leaf,
                "contains": leaf,
                "not": leaf,
                "if": leaf, "then": leaf, "else": leaf,
                "anyOf": [leaf], "oneOf": [leaf],
                "additionalProperties": leaf,
                "unevaluatedProperties": leaf,
                "unevaluatedItems": leaf,
                "patternProperties": {"^a": leaf},
                "dependentSchemas": {"k": leaf},
            }},
        }

    def _pointer(self, keyword):
        if keyword == "$defs":
            return "#/$defs/A"
        if keyword == "definitions":
            return "#/definitions/B"
        if keyword == "properties":
            return "#/properties/x"
        if keyword in ("allOf", "prefixItems", "anyOf", "oneOf"):
            return f"#/properties/x/{keyword}/0"
        if keyword == "patternProperties":
            return "#/properties/x/patternProperties/^a"
        if keyword == "dependentSchemas":
            return "#/properties/x/dependentSchemas/k"
        return f"#/properties/x/{keyword}"

    @pytest.mark.parametrize(
        "keyword",
        sorted(
            JSON_SCHEMA_SUBSCHEMA_KEYS
            | JSON_SCHEMA_LIST_OF_SCHEMA_KEYS
            | JSON_SCHEMA_SINGLE_SCHEMA_KEYS
        ),
    )
    def test_each_keyword_lands_exactly_as_the_published_prose_says(self, keyword):
        resolved = resolve_schema_ref(self._root(), self._pointer(keyword))
        assert (resolved is not _MISSING) == (keyword in self.LANDABLE), (
            f"{keyword!r} disagrees with the published landing set: the schema "
            "says one thing and the resolver does another"
        )

    def test_the_published_sentence_names_exactly_those_eight(self):
        """Cheap textual pin: the description renders verbatim into the
        published schema, so the count and the names must track the set above."""
        description = ResponseExtraction.model_fields["schema_"].description
        assert "Exactly eight keywords qualify" in description
        for keyword in self.LANDABLE:
            assert f"`{keyword}`" in description, f"{keyword} missing from prose"


class TestEachOperationKindGetsItsOwnHarmText:
    """Unreachable through the enum — all three members are handled — so no
    document-level test can catch a regression here. Pinned directly instead."""

    def test_every_member_has_a_distinct_harm_text(self):
        texts = {kind: _unresolved_harm(kind) for kind in _OperationKind}
        assert len(set(texts.values())) == len(_OperationKind)

    def test_a_raw_equal_string_is_refused_rather_than_given_the_read_text(self):
        """`"write" == _OperationKind.WRITE` is True but `is` is False, and the
        dispatch uses `is`. With the read text as a fall-through, a raw string
        silently got paging advice about an operation that does not page — the
        wrong-message bug the enum was introduced to remove."""
        with pytest.raises(AssertionError, match="unhandled operation kind"):
            _unresolved_harm("write")


class TestKeywordVocabularyHasOneOwner:
    """The JSON-Schema keyword vocabulary every walker keys off was restated
    per walker — the contract's `JSON_SCHEMA_*_KEYS`, a copy in the validator,
    and the renderer's `JsonSchemaPropertyNode`. Adding `contentSchema`
    required editing each, and the expression of it nobody thought of — that
    node's own published *description* — was missed, so the shipped contract
    constrained a keyword while its prose said it did not recurse there.

    `.claude/rules/no-drift-surfaces.md` asks first that a copy be removed and
    only then that an unavoidable one be pinned. The validator's is gone: it
    filters `iter_schema_nodes`, so the vocabulary reaches it by import. What
    remains to pin is the renderer's, which cannot be — a published schema
    states the keywords as properties — so the rendered constraint map and the
    rendered sentence are both compared against the live sets.
    """

    def _rendered_node(self):
        import json
        from pathlib import Path

        repo = Path(__file__).resolve().parents[4]
        doc = json.loads((repo / "schemas/api-endpoint/latest.json").read_text())
        return doc["$defs"]["JsonSchemaPropertyNode"]

    def test_the_validator_keeps_no_copy_of_the_vocabulary(self):
        """It walks with the contract's own iterator, so there is no second set
        to compare.

        A module global holding any keyword of the vocabulary is reported, and
        one that IS the contract's own object is not — importing the owner is
        the prescribed form, restating it is the defect, and equality alone
        cannot tell them apart. What this cannot see is a copy that has already
        diverged past sharing any keyword at all; that a walker still recurses
        where the contract's does is the reader's, and the neighbouring
        `test_rendered_node_*` cases pin the surface the copy would show up on.
        """
        from analitiq.contracts import endpoints as ep
        from analitiq.validator import connectors as vc

        assert vc.iter_schema_nodes is ep.iter_schema_nodes
        owned = (ep.JSON_SCHEMA_SUBSCHEMA_KEYS
                 | ep.JSON_SCHEMA_LIST_OF_SCHEMA_KEYS
                 | ep.JSON_SCHEMA_SINGLE_SCHEMA_KEYS)
        restated = sorted(
            name for name, value in vars(vc).items()
            if isinstance(value, (frozenset, set))
            and value & owned
            and not any(value is bucket for bucket in (
                ep.JSON_SCHEMA_SUBSCHEMA_KEYS,
                ep.JSON_SCHEMA_LIST_OF_SCHEMA_KEYS,
                ep.JSON_SCHEMA_SINGLE_SCHEMA_KEYS,
            ))
        )
        assert not restated, (
            f"{restated} restate keywords the contract owns; walk with "
            "`iter_schema_nodes`, or import the bucket rather than copying it"
        )

    def test_rendered_node_constrains_exactly_the_contract_vocabulary(self):
        from analitiq.contracts import endpoints as ep

        vocabulary = (
            ep.JSON_SCHEMA_SUBSCHEMA_KEYS
            | ep.JSON_SCHEMA_LIST_OF_SCHEMA_KEYS
            | ep.JSON_SCHEMA_SINGLE_SCHEMA_KEYS
        )
        rendered = set(self._rendered_node()["properties"]) - {"arrow_type", "native_type"}
        assert rendered == vocabulary

    def test_rendered_description_names_every_keyword_it_constrains(self):
        from analitiq.contracts import endpoints as ep

        vocabulary = (
            ep.JSON_SCHEMA_SUBSCHEMA_KEYS
            | ep.JSON_SCHEMA_LIST_OF_SCHEMA_KEYS
            | ep.JSON_SCHEMA_SINGLE_SCHEMA_KEYS
        )
        description = self._rendered_node()["description"]
        missing = sorted(k for k in vocabulary if f"`{k}`" not in description)
        assert not missing, (
            f"the published node constrains {missing!r} but its description does "
            "not name them — the contract contradicts its own prose"
        )


class TestMaterializeMatchesTheNaiveFold:
    """Differential pin: the memoized fold must equal an uncached recursive one.

    Three successive attempts at making composition fast changed what it
    COMPUTES, and the suite caught none of them:

    * reversing a pre-order chain inverted sibling precedence, so in
      `allOf: [{$ref: Base}, {refinement}]` the base beat the refinement — the
      idiom `allOf` exists for, backwards;
    * a real post-order still emitted a node reached twice at its FIRST
      position, the LOWEST precedence under last-wins, while the fold re-merged
      it at every position. 1502 of 4000 random acyclic schemas disagreed.

    Both were silent: no error, no rejection, just a different `arrow_type` on
    a destination column. Only a differential test catches that class, so this
    is the pin. `materialize_node` may be reimplemented however is fastest; it
    may not change the answer.
    """

    @staticmethod
    def _naive_fold(node, root):
        """The reference: no memo, no visited set, re-merged at every position.
        Exponential — the generated graphs below are forward-only and small."""
        if not isinstance(node, dict):
            return node
        sources = []
        ref = node.get("$ref")
        if isinstance(ref, str):
            target = resolve_schema_ref(root, ref)
            if isinstance(target, dict):
                sources.append(TestMaterializeMatchesTheNaiveFold._naive_fold(target, root))
        for branch in node.get("allOf") or []:
            if isinstance(branch, dict):
                sources.append(TestMaterializeMatchesTheNaiveFold._naive_fold(branch, root))
        sources.append({k: v for k, v in node.items() if k not in ("$ref", "allOf")})
        _refuse_disjoint_types(_MATERIALIZED_NODE, sources)
        merged = {}
        for source in sources:
            for name, value in source.items():
                if name == "properties":
                    continue  # composed per-name below, as the real fold does
                merged[name] = (
                    _combine_schema_values(merged[name], value) if name in merged else value
                )
        # `properties` is NOT an ordinary dict key. Merging it as one is exactly
        # the bug `materialize_node` was carrying — it is how the two views came
        # to disagree — so a reference that does it cannot report the defect it
        # exists to catch. Compose per name, and prove satisfiability from the
        # RAW contributors, which is what `_compose_declarations` proves.
        own_properties = [
            s["properties"] for s in sources
            if isinstance(s, dict) and isinstance(s.get("properties"), dict)
        ]
        if own_properties:
            raw = TestMaterializeMatchesTheNaiveFold._naive_contributors(node, root)
            by_name = {}
            for source_map in own_properties:
                for name, declaration in source_map.items():
                    by_name.setdefault(name, []).append(declaration)
            properties = {}
            for name, declarations in by_name.items():
                contributors = raw.get(name) or declarations
                if len(contributors) > 1:
                    _refuse_disjoint_types(name, contributors)
                folded = declarations[0]
                for declaration in declarations[1:]:
                    folded = _combine_schema_values(folded, declaration, kind="schema")
                properties[name] = folded
            merged["properties"] = properties
        return merged

    @staticmethod
    def _naive_contributors(node, root):
        """Reference for `_property_contributors` — no memo, no shared state.

        Same source order (`$ref` target, `allOf` branches in document order,
        the node itself last) and the same dedup rule (a declaration
        re-contributed by a later source MOVES to the end). Exponential, which
        is why the graphs are small.
        """
        if not isinstance(node, dict):
            return {}
        recurse = TestMaterializeMatchesTheNaiveFold._naive_contributors
        sources = []
        ref = node.get("$ref")
        if isinstance(ref, str):
            target = resolve_schema_ref(root, ref)
            if isinstance(target, dict):
                sources.append(recurse(target, root))
        for branch in node.get("allOf") or []:
            if isinstance(branch, dict):
                sources.append(recurse(branch, root))
        own = node.get("properties")
        sources.append(
            {k: [v] for k, v in own.items()} if isinstance(own, dict) else {}
        )

        contributors = {}
        for source in sources:
            for name, declarations in source.items():
                bucket = contributors.setdefault(name, [])
                for declaration in declarations:
                    for index, seen in enumerate(bucket):
                        if declaration == seen:
                            bucket.pop(index)
                            break
                    bucket.append(declaration)
        return contributors

    def test_agrees_with_the_fold_on_random_shared_defs_graphs(self):
        random.seed(20260804)  # fixed: a flaky differential test is worthless
        names = [f"D{i}" for i in range(10)]
        divergences = []
        for trial in range(1500):
            # Every third graph may close a BACK-EDGE. The generator was
            # forward-only, so it could not produce a cycle at all — which is
            # why a missing cycle guard, and a memo that behaved differently on
            # cyclic input, both survived it. A cyclic graph makes the naive
            # fold non-terminating by construction, so those trials compare the
            # two only where the fold can answer: the guard below bounds it.
            allow_cycle = trial % 3 == 0
            defs = {}
            for i, name in enumerate(names):
                entry = {}
                if random.random() < 0.7:
                    entry["properties"] = {
                        "f": {"native_type": f"NT{i}", "arrow_type": f"AT{i}"},
                        f"own{i}": {"type": "string"},
                    }
                reachable = names[i + 1:] + ([names[0]] if allow_cycle and i else [])
                picks = random.sample(reachable, min(len(reachable), random.randint(0, 2)))
                if picks:
                    entry["allOf"] = [{"$ref": f"#/$defs/{p}"} for p in picks]
                if names[i + 1:] and random.random() < 0.35:
                    entry["$ref"] = f"#/$defs/{random.choice(names[i + 1:])}"
                defs[name] = entry
            root = {"$defs": defs, "allOf": [{"$ref": "#/$defs/D0"}]}
            try:
                reference = self._naive_fold(root, root)
            except RecursionError:
                # Cyclic: the reference cannot answer. `materialize_node` still
                # must — terminating is the whole point of the cycle guard.
                materialize_node(root, root)
                continue
            if materialize_node(root, root) != reference:
                divergences.append(root)
        assert not divergences, (
            f"{len(divergences)} schemas materialize differently from the "
            f"reference fold; first: {divergences[0]!r}"
        )

    def test_the_contributor_walk_agrees_with_its_own_naive_reference(self):
        """The second differential, and the one that was missing.

        `materialize_node` was pinned; `_contributors` — the walk that
        `resolve_declared_path`, `effective_properties`, RULE-ENDP-023, record
        field enumeration and cursor-field resolution ALL run — was pinned by
        nothing differential. Every historically-shipped bug class was
        reinstallable there with a fully green suite: own-source-first,
        `reversed(branches)`, `$ref` after `allOf`, and dedup keeping the first
        occurrence.

        This compares the composed VALUES, not merely whether both sides raise,
        because the failure mode is a different `arrow_type` on a derived
        column rather than an error.
        """
        random.seed(20260805)
        names = [f"D{i}" for i in range(9)]
        types = ["string", "integer", "boolean", "number"]
        divergences = []
        for trial in range(1200):
            allow_cycle = trial % 3 == 0
            defs = {}
            for i, name in enumerate(names):
                entry = {}
                if random.random() < 0.75:
                    entry["properties"] = {
                        # A varying `type` so the disjoint-type proof is
                        # differentially exercised; the old generator gave `f`
                        # no `type` at all, so `_refuse_disjoint_types` never
                        # fired on either side.
                        "f": {
                            "native_type": f"NT{i}",
                            "arrow_type": f"AT{i}",
                            "type": random.sample(types, random.randint(1, 3)),
                        },
                        f"own{i}": {"type": "string"},
                    }
                reachable = names[i + 1:] + ([names[0]] if allow_cycle and i else [])
                picks = random.sample(
                    reachable, min(len(reachable), random.randint(0, 2))
                )
                if picks:
                    entry["allOf"] = [{"$ref": f"#/$defs/{p}"} for p in picks]
                if names[i + 1:] and random.random() < 0.35:
                    entry["$ref"] = f"#/$defs/{random.choice(names[i + 1:])}"
                defs[name] = entry
            root = {"$defs": defs, "allOf": [{"$ref": "#/$defs/D0"}]}

            try:
                reference = self._naive_contributors(root, root)
            except RecursionError:
                # Cyclic: the reference cannot answer, but the real walk must
                # still terminate.
                try:
                    effective_properties(root, root)
                except DeclarationConflictError:
                    pass
                continue
            except DeclarationConflictError:
                # The reference itself proved the graph unsatisfiable (an
                # `allOf` branch it walked into). The real walk must agree.
                with pytest.raises(DeclarationConflictError):
                    _property_contributors(root, root)
                continue
            def attempt(thunk):
                """Refusal is an ANSWER here, not an escape: both sides prove
                satisfiability from the same contributor list, so 'both refused'
                is agreement and 'one refused' is the divergence worth naming."""
                try:
                    return ("ok", thunk())
                except DeclarationConflictError as exc:
                    return ("refused", exc.reason)

            # Default-arg binding: each thunk is called by `attempt` within
            # this same iteration, so binding at definition time is equivalent
            # — it only stops the closure reading the loop cell.
            actual = attempt(lambda root=root: _property_contributors(root, root))
            if actual != ("ok", reference):
                divergences.append(root)
                continue
            composed = attempt(lambda reference=reference: {
                name: _compose_declarations(name, declarations)
                for name, declarations in reference.items()
            })
            if attempt(lambda root=root: effective_properties(root, root)) != composed:
                divergences.append(root)
        assert not divergences, (
            f"{len(divergences)} graphs contribute differently from the "
            f"reference walk; first: {divergences[0]!r}"
        )

    @pytest.mark.parametrize(
        "view",
        [
            pytest.param(_property_contributors, id="contributors"),
            pytest.param(effective_properties, id="effective"),
            pytest.param(materialize_node, id="materialize"),
        ],
    )
    @pytest.mark.parametrize(
        "node",
        [
            {"allOf": [False], "properties": {"a": {"type": "string"}}},
            {"allOf": [{"type": "object"}, False]},
            {"$ref": "#/$defs/B", "properties": {"a": {"type": "string"}}},
        ],
    )
    def test_an_unsatisfiable_branch_is_refused_by_every_view(self, view, node):
        """`_reject_unsatisfiable_branch` is called from BOTH walkers, and
        deleting either copy left the suite green — the generative differentials
        cannot reach it because neither generator emits a boolean branch.

        A rule enforced by one view and not the other is how
        `effective_properties` came to answer `{}` where `materialize_node`
        raised, which crashed a public helper.
        """
        root = {"$defs": {"B": {"allOf": [False]}}, **node}
        with pytest.raises(DeclarationConflictError):
            view(node, root)

    def test_materialize_terminates_and_unions_on_mutual_recursion(self):
        # `materialize_node` carries its own cycle guard, separate from
        # `_contributors`'. Nothing timed or tested it: removing it left the
        # suite green and made a legal recursive `$defs` raise RecursionError.
        root = {
            "$defs": {
                "A": {"properties": {"a": {"type": "string"}},
                      "allOf": [{"$ref": "#/$defs/B"}]},
                "B": {"properties": {"b": {"type": "string"}},
                      "allOf": [{"$ref": "#/$defs/A"}]},
            },
            "$ref": "#/$defs/A",
        }
        assert sorted(materialize_node(root, root)["properties"]) == ["a", "b"]

    def test_materialize_is_linear_on_a_shared_defs_diamond(self):
        # `materialize_node` is a SECOND walk with its own memo, and the only
        # perf test drove `resolve_declared_path`. Deleting this memo left the
        # suite green while a depth-22 diamond went from 0s to 72s.
        #
        # Run on a bounded thread, not just timed: without the memo a depth-40
        # diamond is 2**40 expansions, so the wall-clock assertion below is
        # never REACHED — the suite wedges and CI reports an unattributed job
        # timeout instead of this test's name.
        import time

        depth = 40
        defs = {}
        for i in range(depth):
            defs[f"L{i}"] = {"allOf": [{"$ref": f"#/$defs/A{i + 1}"},
                                       {"$ref": f"#/$defs/B{i + 1}"}]}
            defs[f"A{i + 1}"] = {"allOf": [{"$ref": f"#/$defs/L{i + 1}"}]}
            defs[f"B{i + 1}"] = {"allOf": [{"$ref": f"#/$defs/L{i + 1}"}]}
        defs[f"L{depth}"] = {"type": "object", "properties": {"leaf": {"type": "string"}}}
        root = {"type": "object", "$defs": defs, "allOf": [{"$ref": "#/$defs/L0"}]}

        started = time.monotonic()
        result = _run_with_timeout(lambda: materialize_node(root, root))
        assert "leaf" in result["properties"]
        assert time.monotonic() - started < 5.0

    def test_the_two_views_agree_when_the_contributors_are_NESTED(self):
        # The single-level case below was the only agreement pin, and it passes
        # whether the proof reads raw contributors or already-materialized ones.
        # Nesting is what tells them apart: `_materialize` collects a name's
        # declarations from sources that have THEMSELVES been materialized, and
        # `type` is a data position that merges last-wins, so an inner level
        # collapses its own contributors before the outer proof can see them.
        #
        # {string,integer} & {integer,boolean} & {string,boolean} is empty, but
        # no two of the three are disjoint — so the emptiness is only visible
        # while all three declarations still exist side by side.
        record = {
            "type": "object",
            "allOf": [{"$ref": "#/$defs/Base"}],
            "properties": {"id": {"type": ["string", "boolean"]}},
        }
        root = {
            "type": "object",
            "$defs": {"Base": {"allOf": [
                {"properties": {"id": {"type": ["string", "integer"]}}},
                {"properties": {"id": {"type": ["integer", "boolean"]}}},
            ]}},
            "properties": {"data": {"type": "array", "items": record}},
        }
        with pytest.raises(DeclarationConflictError):
            effective_properties(record, root)
        with pytest.raises(DeclarationConflictError):
            materialize_node(record, root)

    def test_a_shared_contributor_cache_cannot_answer_another_walks_question(self):
        """The deterministic pin for the mechanism the generator finds only ~0.4%
        of the time — 60 seeds had a ~1-in-4 chance of catching it, which is not
        a test, it is a coin.

        `_contributors` caches `{}` for a node it meets on the CURRENT path. A
        contributor memo shared across a whole materialization therefore stores
        entries carrying some ancestor's truncation, and a later node reads a
        WEAKER contributor set than `effective_properties` computes for it from a
        standing start — reintroducing the two-view disagreement the proof
        exists to prevent, via the cache added to make it cheap.

        `x` is `{string,boolean}` ∩ `{integer,boolean}` ∩ `{string,integer}` =
        empty, with no two disjoint, spread over a `$ref` cycle so it is only
        visible to a walk that starts at B.
        """
        root = {"$defs": {
            "A": {"$ref": "#/$defs/C",
                  "properties": {"x": {"type": ["string", "boolean"]}}},
            "B": {"$ref": "#/$defs/A",
                  "properties": {"x": {"type": ["integer", "boolean"]}}},
            "C": {"$ref": "#/$defs/A",
                  "properties": {"x": {"type": ["string", "integer"]}}},
        }}
        record = root["$defs"]["B"]
        with pytest.raises(DeclarationConflictError):
            effective_properties(record, root)
        with pytest.raises(DeclarationConflictError):
            materialize_node(record, root)

    def test_every_schema_keyword_is_classified(self):
        """The partition that makes the next omission fail rather than ship.

        The ring check follows the same-instance half and not the descending
        half, so a keyword in neither is one the check silently does not
        follow — which is how `dependentSchemas` came to be missed: a
        same-instance applicator with no bucket of its own shape.
        """
        from analitiq.contracts import endpoints as ep

        vocabulary = (
            ep.JSON_SCHEMA_SUBSCHEMA_KEYS
            | ep.JSON_SCHEMA_LIST_OF_SCHEMA_KEYS
            | ep.JSON_SCHEMA_SINGLE_SCHEMA_KEYS
        )
        same_instance = (
            ep.SAME_INSTANCE_MAP_APPLICATORS
            | ep.SAME_INSTANCE_LIST_APPLICATORS
            | ep.SAME_INSTANCE_SINGLE_APPLICATORS
        )
        assert ep.IF_CONDITIONED_APPLICATORS <= ep.SAME_INSTANCE_SINGLE_APPLICATORS, (
            "a conditional applicator outside the set it is drawn from: the "
            "edge builder reads it as a member and the partition below cannot "
            "see it"
        )
        assert same_instance & ep.DESCENDING_SCHEMA_KEYWORDS == set(), (
            "a keyword is classified as both handing the value on and "
            "descending into part of it"
        )
        assert same_instance | ep.DESCENDING_SCHEMA_KEYWORDS == vocabulary, (
            "unclassified: "
            f"{sorted(vocabulary ^ (same_instance | ep.DESCENDING_SCHEMA_KEYWORDS))}. "
            "Every keyword the contract's walkers recurse through either hands "
            "the value on — and belongs in the same-instance set matching how "
            "it holds its subschemas, so the ring check follows it — or "
            "descends into a part of it. Decide which, and say so there."
        )

    def test_a_document_full_of_rings_reports_a_bounded_number_of_them(self):
        """One message per distinct ring, and a `$defs` where every entry
        composes every other carries a ring per pair — so the count grows with
        the square of the entries and the text with their cube. What is left
        out is stated rather than dropped."""
        from analitiq.contracts import endpoints as ep

        size = 30
        defs = {
            f"D{i}": {"allOf": [{"$ref": f"#/$defs/D{j}"}
                                for j in range(size) if j != i]}
            for i in range(size)
        }
        errors: list[str] = []
        ep._validate_schema_refs(
            {"type": "object", "$defs": defs,
             "properties": {"f": {"$ref": "#/$defs/D0"}}},
            "response.schema", errors,
        )
        listed = [e for e in errors if "hands the same value round" in e]
        remainder = [e for e in errors if "further reference ring" in e]
        assert len(listed) == 10, len(listed)
        assert len(remainder) == 1, errors[-3:]
        # Every ring is still counted; only the listing is bounded.
        assert f"{size * (size - 1) // 2 - len(listed)} further" in remainder[0]

    def test_a_ring_through_a_map_applicator_is_refused(self):
        """`dependentSchemas` conditions on a property being present and then
        validates the WHOLE instance (2020-12 §10.2.2.4), so it hands the value
        on rather than descending into that property."""
        payload = _endpoint_with_record_shape(
            items={"$ref": "#/$defs/Ring"},
            defs={"Ring": {"dependentSchemas": {"id": {"$ref": "#/$defs/Ring"}}}},
        )
        with pytest.raises(ValidationError, match="hands the same value round"):
            parse_endpoint(payload)

    #: `if` value -> the branch it can never select. `None` is "no `if` at
    #: all", which kills both. Each row was checked against what `jsonschema`
    #: actually does with the shape: the live half loops, the dead half returns.
    @pytest.mark.parametrize("condition, dead", [
        (None, "then"),
        (None, "else"),
        # The whole-schema short-forms select one branch for every instance,
        # so the other is unreachable.
        (True, "else"),
        (False, "then"),
    ])
    def test_a_branch_no_instance_enters_is_not_a_ring(self, condition, dead):
        """`then`/`else` apply only where `if` is present, and only on the
        outcome each is the branch for (2020-12 §10.2.2.2-3). A cycle through a
        branch nothing enters is a cycle nothing follows."""
        inert: dict = {"type": "object", dead: {"$ref": "#/$defs/Inert"}}
        if condition is not None:
            inert["if"] = condition
        parse_endpoint(_endpoint_with_record_shape(
            items={"type": "object", "properties": {
                "id": {"type": "string"},
                "nested": {"$ref": "#/$defs/Inert"},
            }},
            defs={"Inert": inert},
        ))

    @pytest.mark.parametrize("condition, live", [
        (True, "then"),
        (False, "else"),
        ({"type": "object"}, "then"),
        ({"type": "object"}, "else"),
    ])
    def test_a_branch_an_instance_can_enter_is_a_ring(self, condition, live):
        """The other side of the same reading. A boolean `if` is easy to miss:
        it is a legal schema, not a truth value the edge builder may skip."""
        with pytest.raises(ValidationError, match="hands the same value round"):
            parse_endpoint(_endpoint_with_record_shape(
                items={"$ref": "#/$defs/Live"},
                defs={"Live": {"if": condition,
                               live: {"$ref": "#/$defs/Live"}}},
            ))

    def test_a_ring_through_a_composition_keyword_is_refused(self):
        """`allOf`/`anyOf`/`not` hand the SAME value on, so a ring through one
        never consumes any part of it — a `$ref`-only walk sees a node whose
        content is a composition and stops one hop short of the defect."""
        for keyword, branch in (
            ("allOf", [{"$ref": "#/$defs/Ring"}]),
            ("anyOf", [{"$ref": "#/$defs/Ring"}]),
            ("not", {"$ref": "#/$defs/Ring"}),
        ):
            payload = _endpoint_with_record_shape(
                items={"$ref": "#/$defs/Ring"},
                defs={"Ring": {keyword: branch}},
            )
            with pytest.raises(ValidationError,
                               match="hands the same value round") as exc:
                parse_endpoint(payload)
            assert f"'#/$defs/Ring/{keyword}" in str(exc.value), (
                keyword, str(exc.value))

    @pytest.mark.parametrize("shape", [
        {"type": "array", "items": {"$ref": "#/$defs/Deep"}},
        {"type": "array", "prefixItems": [{"$ref": "#/$defs/Deep"}]},
        {"type": "object", "additionalProperties": {"$ref": "#/$defs/Deep"}},
        {"type": "object", "patternProperties": {"^x": {"$ref": "#/$defs/Deep"}}},
    ])
    def test_a_shape_referring_to_itself_through_a_descending_keyword_is_fine(
        self, shape,
    ):
        """The boundary of the same-instance set, from the other side: these
        rings close through the reference alone, and each is legal because the
        keyword carrying it applies to a PART of the value. Move any of these
        keywords into the same-instance set and this document is refused."""
        parse_endpoint(_endpoint_with_record_shape(
            items={"type": "object", "properties": {
                "id": {"type": "string"},
                "nested": {"$ref": "#/$defs/Deep"},
            }},
            defs={"Deep": shape},
        ))

    @pytest.mark.parametrize("carrier", [
        {"type": "array", "items": {"$ref": "#/$defs/Node"}},
        {"type": "array", "prefixItems": [{"$ref": "#/$defs/Node"}]},
        {"type": "object", "properties": {"n": {"$ref": "#/$defs/Node"}}},
        {"type": "object", "patternProperties": {"^n": {"$ref": "#/$defs/Node"}}},
        {"type": "object", "additionalProperties": {"$ref": "#/$defs/Node"}},
    ])
    def test_recursion_that_descends_is_not_a_ring(self, carrier):
        """The shapes the rule must not break. Each hop goes through a keyword
        that applies to a PART of the value, so following it reaches a smaller
        value every time and bottoms out — which is what separates these from
        the compositions above, and is why the edge set is the same-instance
        applicators rather than every schema position."""
        parse_endpoint(_endpoint_with_record_shape(
            items={"$ref": "#/$defs/Node"},
            defs={"Node": {"type": "object", "properties": {
                "id": {"type": "string"},
                "children": carrier,
            }}},
        ))

    def test_a_ring_written_as_a_percent_encoded_reference_is_still_a_ring(self):
        """A `$ref` is a URI-reference and may percent-encode a token; a
        pointer built from the document's own keys never does. Comparing the
        two spellings unnormalized loses the ring."""
        payload = _endpoint_with_record_shape(
            items={"type": "object"},
            defs={"my def": {"$ref": "#/$defs/my%20def"}},
        )
        with pytest.raises(ValidationError, match="hands the same value round"):
            parse_endpoint(payload)

    def test_that_shape_is_refused_end_to_end_not_just_by_the_helpers(self):
        """Because the helper disagreement is only the mechanism — the harm is
        `parse_endpoint` accepting the document and `find_record_field_properties`
        then naming the destination column's type from the permissive view, while
        `resolve_declared_path` raises on the same field."""
        payload = _endpoint_with_record_shape(
            items={"$ref": "#/$defs/B"},
            defs={
                "A": {"properties": {"x": {"type": ["string", "boolean"]}}},
                "B": {"allOf": [{"$ref": "#/$defs/A"}, {"$ref": "#/$defs/C"}],
                      "properties": {"x": {"type": ["integer", "boolean"]}}},
                "C": {"properties": {"x": {"type": ["string", "integer"]}}},
            },
        )
        with pytest.raises(ValidationError, match="self-contradictory"):
            parse_endpoint(payload)

    def test_a_contradiction_reached_through_a_ring_is_refused_as_the_ring(self):
        """The same contributors wired into a cycle. `A` and `C` reference each
        other, so the fold this class is about never runs — and the diagnostic
        an author can act on is the ring, which needs no folding to see."""
        payload = _endpoint_with_record_shape(
            items={"$ref": "#/$defs/B"},
            defs={
                "A": {"$ref": "#/$defs/C",
                      "properties": {"x": {"type": ["string", "boolean"]}}},
                "B": {"$ref": "#/$defs/A",
                      "properties": {"x": {"type": ["integer", "boolean"]}}},
                "C": {"$ref": "#/$defs/A",
                      "properties": {"x": {"type": ["string", "integer"]}}},
            },
        )
        with pytest.raises(ValidationError, match="hands the same value round") as exc:
            parse_endpoint(payload)
        # `B` points into the cycle without being on it, so the ring the
        # diagnostic names is `A` and `C`.
        assert "'#/$defs/A', '#/$defs/C'" in str(exc.value), str(exc.value)

    @pytest.mark.parametrize("seed", range(240))
    def test_the_two_views_never_disagree_about_satisfiability(self, seed):
        """Generative pin on the invariant the whole gate rests on.

        `materialize_node` is the PERMISSIVE view and the one that derives the
        destination column; `effective_properties` is the one the path walk
        consults. Whenever they disagree, a record shape no instance can satisfy
        is accepted and some arbitrary contributor names the column type. Six
        separate rewrites of this composition have each shipped a version where
        they disagreed on some shape, so the property — not any one shape — is
        what has to be tested.

        Chains are 2-4 levels deep so a level's own contributors are folded
        before the level above reads them, which is exactly the case a
        single-level fixture cannot reach. A third of the shapes close a
        back-edge: under a cycle both walks truncate at the node they re-meet,
        and they must truncate IDENTICALLY — a proof walk that reused state
        from mid-materialization truncated differently than a standing start
        (the shared-cache bug pinned deterministically by
        `test_a_shared_contributor_cache_cannot_answer_another_walks_question`;
        this generator hits it ~0.4% per seed, hence 240 seeds AND the pin).
        """
        rng = random.Random(seed)
        universe = ["string", "integer", "boolean", "number"]

        def type_set():
            return rng.sample(universe, rng.randint(1, 3))

        depth = rng.randint(2, 4)
        cyclic = rng.random() < 0.34
        defs: dict[str, Any] = {}
        for level in range(depth):
            if level + 1 < depth:
                nxt = [{"$ref": f"#/$defs/L{level + 1}"}]
            elif cyclic:
                nxt = [{"$ref": f"#/$defs/L{rng.randint(0, level)}"}]
            else:
                nxt = []
            defs[f"L{level}"] = {
                "allOf": nxt + [
                    {"properties": {"id": {"type": type_set()}}}
                    for _ in range(rng.randint(1, 3))
                ]
            }
        record = {
            "type": "object",
            "allOf": [{"$ref": "#/$defs/L0"}],
            "properties": {"id": {"type": type_set()}},
        }
        root = {"type": "object", "$defs": defs,
                "properties": {"data": {"type": "array", "items": record}}}

        def refuses(view):
            try:
                view(record, root)
            except DeclarationConflictError:
                return True
            return False

        assert refuses(effective_properties) == refuses(materialize_node), (
            "the two views disagree about whether this record shape is "
            f"satisfiable (seed {seed}): effective_properties="
            f"{refuses(effective_properties)}, "
            f"materialize_node={refuses(materialize_node)}"
        )

    def test_a_contradictory_field_across_branches_is_refused_by_both_views(self):
        # The refusal `materialize_node` applies at a schema position, and the
        # one `_compose_declarations` applies per name, must fire together — a
        # document where only one fires is a document where the two views
        # disagree about which declaration derives the column.
        node = {"allOf": [
            {"properties": {"id": {"type": "string", "arrow_type": "Utf8"}}},
            {"properties": {"id": {"type": "integer", "arrow_type": "Int64"}}},
        ]}
        with pytest.raises(DeclarationConflictError):
            materialize_node(node, node)
        with pytest.raises(DeclarationConflictError):
            effective_properties(node, node)

    def test_a_data_payload_shaped_like_a_schema_is_not_read_as_one(self):
        # `default`/`const`/`examples`/`x-*` carry arbitrary user data. Running
        # the type-contradiction proof at every depth read two `default` objects
        # that both happened to have a field named `type` as contradictory.
        node = {"allOf": [
            {"type": "object", "default": {"type": "premium", "seats": 1}},
            {"type": "object", "default": {"type": "basic"}},
        ]}
        # Merged, not refused. (Data dicts deep-merge last-wins like any other
        # dict value — the point here is that no contradiction is proved.)
        assert materialize_node(node, node)["default"] == {"type": "basic", "seats": 1}

    def test_a_near_refinement_beats_a_distant_base(self):
        # The shape that survived three rounds: `Overrides` is BOTH a
        # contributor of `Legacy` (distant) and the document's last word
        # (direct). Last wins.
        root = {
            "$defs": {
                "Overrides": {"properties": {"updated_at": {
                    "native_type": "TIMESTAMP", "arrow_type": "Timestamp(MICROSECOND)"}}},
                "Legacy": {
                    "allOf": [{"$ref": "#/$defs/Overrides"}],
                    "properties": {"updated_at": {
                        "native_type": "STRING", "arrow_type": "Utf8"}},
                },
            },
            "items": {"allOf": [{"$ref": "#/$defs/Legacy"}, {"$ref": "#/$defs/Overrides"}]},
        }
        resolved = materialize_node(root["items"], root)["properties"]["updated_at"]
        assert resolved["arrow_type"] == "Timestamp(MICROSECOND)"

        # BOTH views, because the dedup that makes this work lives only in
        # `_contributors` — `materialize_node` has none. Asserting the
        # permissive view alone let the fix be reverted with a green suite: a
        # keep-FIRST dedup puts `Overrides` at its distant (lowest) position, so
        # `effective_properties` and `resolve_declared_path` answer `Utf8` while
        # `materialize_node` still answers `Timestamp(MICROSECOND)`. Two views,
        # two column types for one field, no error anywhere.
        composed = effective_properties(root["items"], root)["updated_at"]
        assert materialize_node(composed, root)["arrow_type"] == "Timestamp(MICROSECOND)"
        walked = resolve_declared_path(root["items"], ["updated_at"], root=root)
        assert materialize_node(walked, root)["arrow_type"] == "Timestamp(MICROSECOND)"
