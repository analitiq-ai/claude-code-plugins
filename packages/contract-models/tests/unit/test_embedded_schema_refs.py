"""`$ref` in an embedded response/input schema (issue #123, ADV-ENDP-026).

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
import threading

import pytest
from pydantic import ValidationError

from analitiq.contracts.endpoints import (
    _MISSING,
    ApiEndpointDoc,
    DeclarationConflictError,
    DeclaredPathError,
    ResponseExtraction,
    WriteInput,
    effective_properties,
    find_record_field_properties,
    parse_endpoint,
    resolve_declared_path,
    resolve_local_pointer,
    resolve_read_record_schema,
)
from analitiq.contracts.shared.advisory_rules import ADVISORY_RULES


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
    """Build a `response` block carrying `schema` (the ADV-ENDP-026 site)."""
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
# ADV-ENDP-026 — the guard, on response.schema and on input.schema
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

    A `$ref` that escapes the walk is exactly the hole ADV-ENDP-026 closes, so
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
                            "headers": {"Content-Type": "application/json"},
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


class TestAdvisoryRegistration:
    def test_adv_endp_026_is_registered_against_both_embedded_schema_classes(self):
        rule = next(r for r in ADVISORY_RULES if r.id == "ADV-ENDP-026")
        assert set(rule.targets) == {"ResponseExtraction", "WriteInput"}
        # One enforcer name must exist on every target.
        assert hasattr(ResponseExtraction, rule.enforcer)
        assert hasattr(WriteInput, rule.enforcer)


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
# The two together: cross-block paths (ADV-ENDP-023) resolving through `$defs`
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
    """`$id` and `$dynamicRef` defeat ADV-ENDP-026 by the exact mechanism it
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
        # Both targets of ADV-ENDP-026, not just the response side.
        with pytest.raises(ValidationError, match=NOT_AUTHORABLE):
            WriteInput.model_validate({"schema": {"type": "object", "$id": "https://evil.example/x"}})


# ---------------------------------------------------------------------------
# The record shape reached through a ref — the shape the guard's own message
# tells authors to write
# ---------------------------------------------------------------------------


class TestRecordShapeThroughRefs:
    """ADV-ENDP-026 rejects a non-local ref with "put it in this document's
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
