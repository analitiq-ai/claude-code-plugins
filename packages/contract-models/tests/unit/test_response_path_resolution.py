"""Declared-path resolution and the pagination/metadata cross-block rule (#123).

Two layers, tested separately because they fail differently:

* :func:`resolve_declared_path` / :func:`effective_properties` — the ONE named
  algorithm that answers "does this dotted path address something the document
  actually declared?". Every `response.body[.<path>]` consumer calls it, so its
  behaviour is pinned here directly rather than only through a document.
* ADV-ENDP-023 — the cross-block constraint that runs that algorithm over the
  WHOLE `pagination` block and every `response.metadata` value. The bug it
  exists to catch is a one-character typo in a ref: before it, such a document
  validated and paging silently stopped after one page. Those typo cases are
  the point of the issue and are asserted per pagination strategy.

The load-bearing property of the resolver is MONOTONICITY: the "not statically
resolvable" diagnosis fires ONLY when the segment was not found, so a node
declaring both `properties.<seg>` and a conditional construct still resolves
through `properties`. Every path the old properties-only walk resolved must
still resolve — that is what guarantees no document that validates today starts
failing. One test per conditional construct pins it.
"""
import time

import pytest
from pydantic import ValidationError

from analitiq.contracts.endpoints import (
    DeclaredPathError,
    effective_properties,
    parse_endpoint,
    resolve_declared_path,
    resolve_local_pointer,
)


API_SCHEMA_URL = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


# ---------------------------------------------------------------------------
# Resolver fixtures
# ---------------------------------------------------------------------------

# Every conditional-declaration construct, in the form it appears on a node.
# `additionalProperties`/`unevaluatedProperties` appear ONLY in their
# schema-valued form here — their boolean form is deliberately NOT conditional
# and is covered by its own test below.
CONDITIONAL_CONSTRUCTS = {
    "anyOf": {"anyOf": [{"properties": {"elsewhere": {"type": "string"}}}]},
    "oneOf": {"oneOf": [{"properties": {"elsewhere": {"type": "string"}}}]},
    "if-then-else": {
        "if": {"properties": {"kind": {"const": "a"}}},
        "then": {"properties": {"elsewhere": {"type": "string"}}},
        "else": {"properties": {"otherwise": {"type": "string"}}},
    },
    "patternProperties": {"patternProperties": {"^n_": {"type": "integer"}}},
    "dependentSchemas": {
        "dependentSchemas": {"kind": {"properties": {"elsewhere": {"type": "string"}}}}
    },
    "additionalProperties": {"additionalProperties": {"type": "string"}},
    "unevaluatedProperties": {"unevaluatedProperties": {"type": "string"}},
}

# The keyword name each construct is reported under. `if-then-else` puts all
# three on the node, so the message names all three.
CONDITIONAL_REPORTED_KEYWORD = {
    "anyOf": "anyOf",
    "oneOf": "oneOf",
    "if-then-else": "if",
    "patternProperties": "patternProperties",
    "dependentSchemas": "dependentSchemas",
    "additionalProperties": "additionalProperties",
    "unevaluatedProperties": "unevaluatedProperties",
}


# ---------------------------------------------------------------------------
# resolve_declared_path — the happy paths
# ---------------------------------------------------------------------------


class TestResolveDeclaredPathPlainWalk:
    def test_nested_properties_walk_returns_the_addressed_node(self):
        leaf = {"type": "string"}
        schema = {
            "type": "object",
            "properties": {
                "links": {"type": "object", "properties": {"next": leaf}},
            },
        }
        assert resolve_declared_path(schema, ["links", "next"]) is leaf

    def test_single_segment_walk(self):
        schema = {"type": "object", "properties": {"total": {"type": "integer"}}}
        assert resolve_declared_path(schema, ["total"]) == {"type": "integer"}

    def test_empty_segments_resolve_to_root(self):
        """A bare `response.body` addresses the schema root — `[]` must return
        the root itself, not raise and not descend."""
        schema = {"type": "array", "items": {"type": "object"}}
        assert resolve_declared_path(schema, []) is schema

    def test_deeply_nested_walk(self):
        schema = {
            "properties": {
                "a": {"properties": {"b": {"properties": {"c": {"type": "integer"}}}}}
            }
        }
        assert resolve_declared_path(schema, ["a", "b", "c"]) == {"type": "integer"}


class TestEffectivePropertiesAllOf:
    def test_allof_branches_merge(self):
        node = {
            "allOf": [
                {"properties": {"a": {"type": "string"}}},
                {"properties": {"b": {"type": "integer"}}},
            ]
        }
        assert set(effective_properties(node)) == {"a", "b"}
        assert resolve_declared_path(node, ["b"]) == {"type": "integer"}

    def test_allof_merges_with_the_nodes_own_properties(self):
        node = {
            "properties": {"own": {"type": "string"}},
            "allOf": [{"properties": {"merged": {"type": "integer"}}}],
        }
        assert set(effective_properties(node)) == {"own", "merged"}

    def test_allof_nested_inside_a_branch_merges(self):
        """A contributor may carry its own `allOf`; the merge is recursive."""
        node = {
            "allOf": [
                {"allOf": [{"properties": {"deep": {"type": "string"}}}]},
            ]
        }
        assert resolve_declared_path(node, ["deep"]) == {"type": "string"}

    def test_allof_merge_applies_partway_down_a_path(self):
        schema = {
            "properties": {
                "meta": {"allOf": [{"properties": {"count": {"type": "integer"}}}]}
            }
        }
        assert resolve_declared_path(schema, ["meta", "count"]) == {"type": "integer"}

    def test_identical_redeclaration_across_allof_branches_resolves(self):
        """Two branches saying the SAME thing about one key is a harmless
        restatement — the resolver has one answer, so it gives it."""
        declaration = {"type": "string", "description": "same"}
        node = {
            "allOf": [
                {"properties": {"a": dict(declaration)}},
                {"properties": {"a": dict(declaration)}},
            ]
        }
        assert resolve_declared_path(node, ["a"]) == declaration

    def test_identical_redeclaration_between_properties_and_allof_resolves(self):
        node = {
            "properties": {"a": {"type": "string"}},
            "allOf": [{"properties": {"a": {"type": "string"}}}],
        }
        assert resolve_declared_path(node, ["a"]) == {"type": "string"}

    def test_unequal_redeclaration_across_allof_branches_is_a_conflict(self):
        """Disjoint `type` sets on one name: nothing can satisfy the
        intersection, so there is no honest answer about the field."""
        node = {
            "allOf": [
                {"properties": {"a": {"type": "string"}}},
                {"properties": {"a": {"type": "integer"}}},
            ]
        }
        with pytest.raises(DeclaredPathError, match="conflicting redeclaration of 'a'"):
            resolve_declared_path(node, ["a"])

    def test_unequal_redeclaration_between_properties_and_allof_is_a_conflict(self):
        node = {
            "properties": {"a": {"type": "string"}},
            "allOf": [{"properties": {"a": {"type": "integer"}}}],
        }
        with pytest.raises(DeclaredPathError, match="conflicting redeclaration"):
            resolve_declared_path(node, ["a"])

    def test_conflict_error_carries_the_segment_it_stopped_on(self):
        """`effective_properties` inspects a node, not a path, so the conflict
        is re-framed with the failing segment's coordinates by the caller."""
        schema = {
            "properties": {
                "meta": {
                    "allOf": [
                        {"properties": {"a": {"type": "string"}}},
                        {"properties": {"a": {"type": "integer"}}},
                    ]
                }
            }
        }
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(schema, ["meta", "a"])
        assert excinfo.value.segment == "a"
        assert excinfo.value.index == 1

    def test_unequal_redeclaration_across_a_ref_branch_is_a_conflict(self):
        """`$ref` contributes unconditionally like `allOf`, so it conflicts the
        same way."""
        root = {
            "$defs": {"Base": {"properties": {"a": {"type": "integer"}}}},
            "properties": {"a": {"type": "string"}},
            "$ref": "#/$defs/Base",
        }
        with pytest.raises(DeclaredPathError, match="conflicting redeclaration"):
            resolve_declared_path(root, ["a"])


# ---------------------------------------------------------------------------
# MONOTONICITY — the property the whole design rests on
# ---------------------------------------------------------------------------


class TestMonotonicity:
    """A node declaring BOTH `properties.<seg>` and a conditional construct
    resolves through `properties` and does NOT raise.

    This is what guarantees the new resolver is monotone with respect to the
    `properties`-only walk it replaces: the ambiguity check is reached only when
    the segment was NOT found, so no document that validates today can start
    failing because it happens to carry an `anyOf` next to its `properties`.
    """

    @pytest.mark.parametrize("construct", sorted(CONDITIONAL_CONSTRUCTS))
    def test_properties_wins_over_a_sibling_conditional_construct(self, construct):
        leaf = {"type": "string"}
        node = {
            "type": "object",
            "properties": {"target": leaf},
            **CONDITIONAL_CONSTRUCTS[construct],
        }
        assert resolve_declared_path(node, ["target"]) is leaf

    @pytest.mark.parametrize("construct", sorted(CONDITIONAL_CONSTRUCTS))
    def test_properties_wins_partway_down_a_path(self, construct):
        """Same guarantee at an intermediate node, not just at the root."""
        node = {
            "properties": {
                "wrapper": {
                    "properties": {"target": {"type": "integer"}},
                    **CONDITIONAL_CONSTRUCTS[construct],
                }
            }
        }
        assert resolve_declared_path(node, ["wrapper", "target"]) == {"type": "integer"}

    def test_properties_wins_over_every_conditional_construct_at_once(self):
        """The worst case: one node carrying all of them plus `properties`."""
        node = {"properties": {"target": {"type": "string"}}}
        for fragment in CONDITIONAL_CONSTRUCTS.values():
            node.update(fragment)
        assert resolve_declared_path(node, ["target"]) == {"type": "string"}

    @pytest.mark.parametrize("construct", sorted(CONDITIONAL_CONSTRUCTS))
    def test_allof_contributed_declaration_also_wins(self, construct):
        """The unconditional merge feeds the same lookup, so an `allOf`-supplied
        declaration beats a sibling conditional construct too."""
        node = {
            "allOf": [{"properties": {"target": {"type": "boolean"}}}],
            **CONDITIONAL_CONSTRUCTS[construct],
        }
        assert resolve_declared_path(node, ["target"]) == {"type": "boolean"}


# ---------------------------------------------------------------------------
# The two failure diagnoses (they are fixed differently, so they read differently)
# ---------------------------------------------------------------------------


class TestUndeclaredSegment:
    def test_missing_segment_with_no_conditional_construct_is_not_declared(self):
        """The typo case: the node commits to a closed set of properties, so
        the segment is simply wrong."""
        node = {"type": "object", "properties": {"total": {"type": "integer"}}}
        with pytest.raises(DeclaredPathError, match="'totl' is not declared"):
            resolve_declared_path(node, ["totl"])

    def test_missing_segment_on_a_node_with_no_properties_at_all(self):
        with pytest.raises(DeclaredPathError, match="'a' is not declared"):
            resolve_declared_path({"type": "object"}, ["a"])

    def test_typo_case_reports_the_failing_segment_not_the_first_one(self):
        schema = {"properties": {"links": {"properties": {"next": {"type": "string"}}}}}
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(schema, ["links", "nxt"])
        assert excinfo.value.segment == "nxt"
        assert excinfo.value.index == 1
        assert "'nxt' is not declared" in excinfo.value.reason

    @pytest.mark.parametrize("keyword", ["additionalProperties", "unevaluatedProperties"])
    @pytest.mark.parametrize("value", [True, False])
    def test_boolean_catchall_is_not_conditional(self, keyword, value):
        """`additionalProperties: true/false` declares nothing about any
        particular NAME, so the plain "not declared" diagnosis stays correct —
        only the schema-valued form is ambiguous."""
        node = {"type": "object", "properties": {"a": {}}, keyword: value}
        with pytest.raises(DeclaredPathError, match="'b' is not declared"):
            resolve_declared_path(node, ["b"])

    @pytest.mark.parametrize("keyword", ["additionalProperties", "unevaluatedProperties"])
    def test_boolean_catchall_does_not_leak_into_the_ambiguity_message(self, keyword):
        node = {"type": "object", keyword: True}
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(node, ["b"])
        assert "not statically resolvable" not in excinfo.value.reason


class TestNotStaticallyResolvable:
    @pytest.mark.parametrize("construct", sorted(CONDITIONAL_CONSTRUCTS))
    def test_missing_segment_with_a_conditional_construct(self, construct):
        """A node carrying a conditional construct MIGHT really describe the
        missing segment — just not in a way a static resolver can commit to.
        The fix is to tighten the schema, not to correct a spelling, so the two
        diagnoses must not be confused."""
        node = {"type": "object", **CONDITIONAL_CONSTRUCTS[construct]}
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(node, ["missing"])
        reason = excinfo.value.reason
        assert "path is not statically resolvable" in reason
        assert CONDITIONAL_REPORTED_KEYWORD[construct] in reason
        assert "declare 'missing' under 'properties'" in reason

    def test_message_names_every_construct_present(self):
        node = {"anyOf": [{}], "oneOf": [{}], "patternProperties": {"^x": {}}}
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(node, ["missing"])
        reason = excinfo.value.reason
        for keyword in ("anyOf", "oneOf", "patternProperties"):
            assert keyword in reason

    def test_unresolvable_ref_is_reported_as_conditional(self):
        """A `$ref` that resolved has already contributed everything it
        declares, so it says nothing about a still-missing segment. One that
        did NOT resolve is genuinely unknowable — and only then is it named."""
        node = {"$ref": "#/$defs/Nope"}
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(node, ["missing"])
        assert "$ref" in excinfo.value.reason

    def test_resolved_ref_is_not_reported_as_conditional(self):
        root = {"$defs": {"Base": {"properties": {"a": {}}}}, "$ref": "#/$defs/Base"}
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(root, ["missing"])
        assert excinfo.value.reason == "'missing' is not declared"


class TestNonObjectIntermediate:
    @pytest.mark.parametrize("leaf", [True, False, "string", 7, None, ["a"]])
    def test_intermediate_node_that_is_not_an_object_schema(self, leaf):
        """A JSON Schema boolean short-form (or any non-dict slot) has nothing
        to look a property up in."""
        schema = {"properties": {"a": leaf}}
        with pytest.raises(DeclaredPathError) as excinfo:
            resolve_declared_path(schema, ["a", "b"])
        assert excinfo.value.reason == "intermediate node is not an object schema"
        assert excinfo.value.segment == "b"
        assert excinfo.value.index == 1

    def test_non_dict_root_with_segments(self):
        with pytest.raises(DeclaredPathError, match="not an object schema"):
            resolve_declared_path(True, ["a"])

    def test_non_dict_root_with_no_segments_is_returned_verbatim(self):
        """No segment means no lookup, so there is nothing to reject."""
        assert resolve_declared_path(True, []) is True


class TestResolveLocalPointer:
    """Thin coverage of the pointer helper the merge leans on — enough to pin
    that "found null" never reads as "not found"."""

    def test_root_pointer(self):
        root = {"a": 1}
        assert resolve_local_pointer(root, "#") is root

    def test_nested_pointer(self):
        root = {"$defs": {"Base": {"type": "object"}}}
        assert resolve_local_pointer(root, "#/$defs/Base") == {"type": "object"}

    def test_list_index(self):
        root = {"allOf": [{"type": "object"}, {"type": "null"}]}
        assert resolve_local_pointer(root, "#/allOf/1") == {"type": "null"}

    @pytest.mark.parametrize(
        "ref",
        [
            "https://example.com/other.json#/$defs/X",
            "other.json#/$defs/X",
            "#/$defs/Missing",
            "#Anchor",
            "#/allOf/9",
        ],
    )
    def test_unresolvable_refs_do_not_contribute(self, ref):
        root = {"$defs": {"Base": {}}, "allOf": [{}]}
        node = {"$ref": ref}
        assert effective_properties(node, root) == {}

    def test_found_null_is_not_confused_with_not_found(self):
        """`None` is a legal thing to find at a pointer, so the helper returns a
        sentinel instead. A `$ref` at a null target contributes nothing and
        raises nothing."""
        root = {"$defs": {"Base": None}}
        assert resolve_local_pointer(root, "#/$defs/Base") is None
        assert effective_properties({"$ref": "#/$defs/Base"}, root) == {}


# ---------------------------------------------------------------------------
# ADV-ENDP-023 end-to-end: pagination / metadata refs must resolve
# ---------------------------------------------------------------------------

# One response body shape every strategy pages over. Each strategy's ref site
# has a real target here, so the ONLY difference between the accepted document
# and the rejected one is a single character in a ref.
RESPONSE_SCHEMA = {
    "$schema": JSON_SCHEMA,
    "type": "object",
    "properties": {
        "data": {
            "type": "array",
            "items": {"type": "object", "properties": {"id": {"type": "integer"}}},
        },
        "total": {"type": "integer"},
        "next_page": {"type": "integer"},
        "next_cursor": {"type": "string"},
        "last_id": {"type": "string"},
        "has_more": {"type": "boolean"},
        "links": {"type": "object", "properties": {"next": {"type": "string"}}},
    },
}

# (params, request.query, strategy block factory taking the ref under test),
# plus the ref that strategy's own "advance" site reads and a ONE-CHARACTER
# typo of it.
STRATEGY_CASES = {
    "offset": {
        "params": {"o": {"in": "query", "type": "integer", "required": False,
                         "controlled_by": "pagination"}},
        "query": {"o": {"from_param": "o"}},
        "site": "offset.increment_by",
        "block": lambda ref: {"offset": {"param": "o", "initial": 0,
                                         "increment_by": {"ref": ref}}},
        "good": "response.body.total",
        "typo": "response.body.totl",
    },
    "page": {
        "params": {"p": {"in": "query", "type": "integer", "required": False,
                         "controlled_by": "pagination"}},
        "query": {"p": {"from_param": "p"}},
        "site": "page.initial",
        "block": lambda ref: {"page": {"param": "p", "initial": {"ref": ref}}},
        "good": "response.body.next_page",
        "typo": "response.body.next_pag",
    },
    "cursor": {
        "params": {"c": {"in": "query", "type": "string", "required": False,
                         "controlled_by": "pagination"}},
        "query": {"c": {"from_param": "c"}},
        "site": "cursor.next_cursor",
        "block": lambda ref: {"cursor": {"param": "c", "next_cursor": {"ref": ref}}},
        "good": "response.body.next_cursor",
        "typo": "response.body.next_cursur",
    },
    "link": {
        "params": {},
        "query": {},
        "site": "link.next_url",
        "block": lambda ref: {"link": {"next_url": {"ref": ref}}},
        "good": "response.body.links.next",
        "typo": "response.body.links.nxt",
    },
    "keyset": {
        "params": {"k": {"in": "query", "type": "string", "required": False,
                         "controlled_by": "pagination"}},
        "query": {"k": {"from_param": "k"}},
        "site": "keyset.initial",
        "block": lambda ref: {"keyset": {"param": "k", "order_by_field": "id",
                                         "initial": {"ref": ref}}},
        "good": "response.body.last_id",
        "typo": "response.body.last_di",
    },
}

STRATEGIES = sorted(STRATEGY_CASES)


def _read_payload(strategy, *, ref=None, stop_when=None, metadata=None, records=None):
    """A complete, otherwise-valid API read endpoint paging with ``strategy``.

    Only the ref under test varies, so a rejection can be attributed to nothing
    else.
    """
    case = STRATEGY_CASES[strategy]
    request = {"method": "GET", "path": "/v1/x"}
    if case["query"]:
        request["query"] = dict(case["query"])
    pagination = {
        "type": strategy,
        **case["block"](case["good"] if ref is None else ref),
        "stop_when": stop_when or {"empty": {"ref": "response.body.data"}},
    }
    response = {
        "records": {"ref": records or "response.body.data"},
        "schema": RESPONSE_SCHEMA,
    }
    if metadata is not None:
        response["metadata"] = metadata
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": "x",
        "operations": {
            "read": {
                "request": request,
                "params": dict(case["params"]),
                "pagination": pagination,
                "response": response,
            }
        },
    }


class TestPaginationRefsResolve:
    """The bug #123 exists to catch: a one-character typo in a pagination ref
    used to validate, then paged exactly once at run time and silently returned
    a truncated dataset. It must now be rejected — for every strategy."""

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_good_document_validates(self, strategy):
        parse_endpoint(_read_payload(strategy))

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_one_character_typo_in_the_advance_ref_is_rejected(self, strategy):
        case = STRATEGY_CASES[strategy]
        payload = _read_payload(strategy, ref=case["typo"])
        with pytest.raises(ValidationError) as excinfo:
            parse_endpoint(payload)
        message = str(excinfo.value)
        assert "does not resolve in response.schema" in message
        assert case["typo"] in message

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_typo_error_names_the_undeclared_segment(self, strategy):
        """The author is told WHICH segment is wrong, not just that something
        is — that is the whole difference between a usable error and a shrug."""
        case = STRATEGY_CASES[strategy]
        bad_segment = case["typo"].rsplit(".", 1)[1]
        with pytest.raises(ValidationError) as excinfo:
            parse_endpoint(_read_payload(strategy, ref=case["typo"]))
        assert f"{bad_segment!r} is not declared" in str(excinfo.value)

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_one_character_typo_in_stop_when_is_rejected(self, strategy):
        """`stop_when` is the other half: a predicate that never fires paginates
        forever, one that always fires paginates once."""
        payload = _read_payload(
            strategy, stop_when={"empty": {"ref": "response.body.dat"}}
        )
        with pytest.raises(ValidationError, match="does not resolve in response.schema"):
            parse_endpoint(payload)

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_typo_nested_deep_inside_a_stop_when_boolean_tree(self, strategy):
        """The whole pagination block is walked, so a ref buried under
        `and`/`not` is reached exactly like a top-level one."""
        stop_when = {
            "and": [
                {"empty": {"ref": "response.body.data"}},
                {"not": {"eq": [{"ref": "response.body.has_mor"}, True]}},
            ]
        }
        with pytest.raises(ValidationError, match="response.body.has_mor"):
            parse_endpoint(_read_payload(strategy, stop_when=stop_when))

    def test_nested_stop_when_tree_without_a_typo_validates(self):
        stop_when = {
            "and": [
                {"empty": {"ref": "response.body.data"}},
                {"not": {"eq": [{"ref": "response.body.has_more"}, True]}},
            ]
        }
        parse_endpoint(_read_payload("cursor", stop_when=stop_when))

    def test_dotted_ref_typo_in_a_nested_object_is_rejected(self):
        """The typo need not be in the last segment: `links.nxt` fails on the
        second hop, under a node that IS declared."""
        with pytest.raises(ValidationError, match="'nxt' is not declared"):
            parse_endpoint(_read_payload("link", ref="response.body.links.nxt"))

    def test_intermediate_typo_is_rejected(self):
        with pytest.raises(ValidationError, match="'lnks' is not declared"):
            parse_endpoint(_read_payload("link", ref="response.body.lnks.next"))

    def test_ref_through_a_non_object_intermediate_is_rejected(self):
        """`total` is an integer, so `total.value` addresses nothing."""
        with pytest.raises(ValidationError, match="does not resolve in response.schema"):
            parse_endpoint(_read_payload("offset", ref="response.body.total.value"))

    def test_bare_response_body_ref_resolves_to_the_root(self):
        parse_endpoint(_read_payload("cursor", ref="response.body"))

    def test_literal_subtree_is_not_path_checked(self):
        """A `{literal}` payload is opaque data, not a ref — it must not be
        resolved, or authors could not carry a string that merely looks like
        one."""
        stop_when = {"eq": [{"literal": "${response.body.not_a_ref}"}, "x"]}
        parse_endpoint(_read_payload("cursor", stop_when=stop_when))


class TestPaginationRefsInTemplates:
    def test_ref_inside_a_template_resolves(self):
        parse_endpoint(
            _read_payload(
                "cursor",
                ref=None,
                stop_when={"eq": [{"template": "${response.body.total}"}, "0"]},
            )
        )

    def test_typo_inside_a_template_is_rejected(self):
        """Templates are parsed with the shared resolver grammar, so a ref
        buried in `${...}` is checked exactly like a bare `{ref}`."""
        with pytest.raises(ValidationError, match="response.body.totl"):
            parse_endpoint(
                _read_payload(
                    "cursor",
                    stop_when={"eq": [{"template": "${response.body.totl}"}, "0"]},
                )
            )

    def test_typo_in_a_templated_next_url_is_rejected(self):
        payload = _read_payload("link")
        payload["operations"]["read"]["pagination"]["link"]["next_url"] = {
            "template": "https://api.example.com${response.body.links.nxt}"
        }
        with pytest.raises(ValidationError, match="response.body.links.nxt"):
            parse_endpoint(payload)

    def test_templated_next_url_without_a_typo_validates(self):
        payload = _read_payload("link")
        payload["operations"]["read"]["pagination"]["link"]["next_url"] = {
            "template": "https://api.example.com${response.body.links.next}"
        }
        parse_endpoint(payload)


class TestMetadataRefsResolve:
    def test_good_metadata_refs_validate(self):
        metadata = {
            "total": {"ref": "response.body.total"},
            "next": {"ref": "response.body.links.next"},
        }
        parse_endpoint(_read_payload("cursor", metadata=metadata))

    def test_typo_in_a_metadata_ref_is_rejected(self):
        metadata = {"total": {"ref": "response.body.totl"}}
        with pytest.raises(ValidationError) as excinfo:
            parse_endpoint(_read_payload("cursor", metadata=metadata))
        message = str(excinfo.value)
        assert "response.metadata['total']" in message
        assert "'totl' is not declared" in message

    def test_typo_in_a_metadata_template_is_rejected(self):
        metadata = {"page_url": {"template": "https://x/${response.body.links.nxt}"}}
        with pytest.raises(ValidationError, match="response.body.links.nxt"):
            parse_endpoint(_read_payload("cursor", metadata=metadata))

    def test_metadata_ref_is_checked_even_without_pagination(self):
        """The rule is not a pagination rule — it is a `response.body` rule."""
        payload = {
            "$schema": API_SCHEMA_URL,
            "endpoint_id": "x",
            "operations": {
                "read": {
                    "request": {"method": "GET", "path": "/v1/x"},
                    "params": {},
                    "response": {
                        "records": {"ref": "response.body.data"},
                        "schema": RESPONSE_SCHEMA,
                        "metadata": {"total": {"ref": "response.body.totl"}},
                    },
                }
            },
        }
        with pytest.raises(ValidationError, match="'totl' is not declared"):
            parse_endpoint(payload)

    def test_metadata_literal_is_not_path_checked(self):
        metadata = {"note": {"literal": "${response.body.whatever}"}}
        parse_endpoint(_read_payload("cursor", metadata=metadata))

    def test_typo_in_a_function_expression_input_is_rejected(self):
        """A `function`'s `input` is a nested expression, so a ref hidden one
        level down is still the author's ref."""
        metadata = {
            "total": {"function": "to_int", "input": {"ref": "response.body.totl"}}
        }
        with pytest.raises(ValidationError, match="'totl' is not declared"):
            parse_endpoint(_read_payload("cursor", metadata=metadata))

    def test_function_expression_map_is_a_data_table_not_a_ref(self):
        """`map` is a lookup table, not an expression subtree — its values must
        not be resolved."""
        metadata = {
            "total": {
                "function": "map_value",
                "input": {"ref": "response.body.total"},
                "map": {"0": "${response.body.nonsense}"},
            }
        }
        parse_endpoint(_read_payload("cursor", metadata=metadata))


class TestReservedScopesAreNotPathChecked:
    """`response.schema` describes the BODY. The other response scopes are
    engine-owned, so the schema has no opinion about them and they must pass
    untouched — checking them would reject correct documents."""

    @pytest.mark.parametrize(
        "predicate",
        [
            {"eq": [{"ref": "response.headers.x-next-page"}, ""]},
            {"missing": {"ref": "response.headers.link"}},
            {"eq": [{"ref": "response.status"}, 204]},
            {"eq": [{"ref": "response.record_count"}, 0]},
            {"empty": {"ref": "response.records"}},
        ],
    )
    def test_reserved_scope_ref_in_stop_when_validates(self, predicate):
        parse_endpoint(_read_payload("cursor", stop_when=predicate))

    def test_reserved_scopes_together_validate(self):
        stop_when = {
            "or": [
                {"eq": [{"ref": "response.status"}, 204]},
                {"eq": [{"ref": "response.record_count"}, 0]},
                {"missing": {"ref": "response.headers.x-next"}},
            ]
        }
        parse_endpoint(_read_payload("cursor", stop_when=stop_when))

    def test_reserved_scope_in_a_template_validates(self):
        stop_when = {"eq": [{"template": "${response.headers.x-next}"}, ""]}
        parse_endpoint(_read_payload("cursor", stop_when=stop_when))

    def test_record_count_increment_by_validates(self):
        """The canonical offset step reads a reserved scope — the single most
        common pagination ref must not be dragged into the body check."""
        parse_endpoint(_read_payload("offset", ref="response.record_count"))

    def test_non_response_scopes_are_untouched(self):
        parse_endpoint(_read_payload("offset", ref="runtime.batch_size"))


class TestSchemaShapesTheRuleAccepts:
    """The cross-block rule runs the SAME resolver, so `allOf` composition and
    `$defs` reuse in a `response.schema` are addressable from pagination too."""

    def _payload_with_schema(self, schema, ref):
        payload = _read_payload("cursor", ref=ref)
        payload["operations"]["read"]["response"]["schema"] = schema
        return payload

    def test_pagination_ref_resolves_through_allof(self):
        schema = {
            "$schema": JSON_SCHEMA,
            "type": "object",
            "properties": {"data": {"type": "array", "items": {"type": "object"}}},
            "allOf": [{"properties": {"next_cursor": {"type": "string"}}}],
        }
        parse_endpoint(self._payload_with_schema(schema, "response.body.next_cursor"))

    def test_typo_still_rejected_through_allof(self):
        schema = {
            "$schema": JSON_SCHEMA,
            "type": "object",
            "properties": {"data": {"type": "array", "items": {"type": "object"}}},
            "allOf": [{"properties": {"next_cursor": {"type": "string"}}}],
        }
        with pytest.raises(ValidationError, match="'next_cursr' is not declared"):
            parse_endpoint(self._payload_with_schema(schema, "response.body.next_cursr"))

    def test_pagination_ref_resolves_through_a_local_ref(self):
        schema = {
            "$schema": JSON_SCHEMA,
            "$defs": {"Envelope": {"properties": {"next_cursor": {"type": "string"}}}},
            "type": "object",
            "properties": {"data": {"type": "array", "items": {"type": "object"}}},
            "allOf": [{"$ref": "#/$defs/Envelope"}],
        }
        parse_endpoint(self._payload_with_schema(schema, "response.body.next_cursor"))

    def test_conditional_only_schema_is_rejected_as_untightened(self):
        """A response schema that declares its paging key only under `oneOf`
        gets the OTHER diagnosis — the fix is to tighten the schema, and the
        message must say so instead of accusing the author of a typo."""
        schema = {
            "$schema": JSON_SCHEMA,
            "type": "object",
            "properties": {"data": {"type": "array", "items": {"type": "object"}}},
            "oneOf": [{"properties": {"next_cursor": {"type": "string"}}}],
        }
        with pytest.raises(ValidationError) as excinfo:
            parse_endpoint(self._payload_with_schema(schema, "response.body.next_cursor"))
        message = str(excinfo.value)
        assert "not statically resolvable" in message
        assert "oneOf" in message

    def test_records_anchor_failure_is_reported_before_the_pagination_one(self):
        """ADV-ENDP-023 runs LAST: a broken `response.records` is the more
        fundamental failure and must be what the author sees first."""
        payload = _read_payload(
            "cursor", ref="response.body.next_cursr", records="response.body.dta"
        )
        with pytest.raises(ValidationError) as excinfo:
            parse_endpoint(payload)
        message = str(excinfo.value)
        assert "response.records ref" in message
        assert "does not resolve in response.schema" not in message


# ---------------------------------------------------------------------------
# The keyword partition
# ---------------------------------------------------------------------------


class TestConditionalKeywordPartitionIsPinnedToTheWalkerSets:
    """`_CONDITIONAL_DECLARATION_KEYWORDS` is a second classification of the same
    JSON-Schema keyword vocabulary the module already owns in
    `_JSON_SCHEMA_*_KEYS`, so it can rot silently: add a new applicator to the
    walker sets, forget to triage it here, and `resolve_declared_path` reports a
    node that conditionally declares the segment as a plain typo — and rejects a
    document that is honest about its schema.

    These two tests make the omission fail the build and name the keyword. They
    are the pin the no-drift rule requires of an unavoidable restatement.
    """

    #: Walker keywords that cannot conditionally declare a PROPERTY NAME, with
    #: the reason each is exempt. Anything not listed here and not in
    #: `_CONDITIONAL_DECLARATION_KEYWORDS` /
    #: `_SCHEMA_VALUED_CATCHALL_KEYWORDS` fails the partition test below.
    NON_DECLARING = {
        # Contribute unconditionally — the resolver reads them directly.
        "properties": "collected as a contributor",
        "allOf": "collected as a contributor",
        # Name a schema, not a property of THIS object.
        "$defs": "a definition store, not an applicator",
        "definitions": "a definition store, not an applicator",
        # Apply to array items or to property NAMES, never to a named property.
        "items": "applies to array items",
        "prefixItems": "applies to array items",
        "contains": "applies to array items",
        "unevaluatedItems": "applies to array items",
        "propertyNames": "constrains names, declares none",
        # A negation declares nothing.
        "not": "declares nothing",
        # Annotation only; carries no assertion about names.
        "contentSchema": "annotation, asserts nothing",
    }

    def test_every_conditional_keyword_is_a_known_schema_position(self):
        from analitiq.contracts.endpoints import (
            _CONDITIONAL_DECLARATION_KEYWORDS,
            _JSON_SCHEMA_LIST_OF_SCHEMA_KEYS,
            _JSON_SCHEMA_SINGLE_SCHEMA_KEYS,
            _JSON_SCHEMA_SUBSCHEMA_KEYS,
            _SCHEMA_VALUED_CATCHALL_KEYWORDS,
        )

        walker_keywords = (
            _JSON_SCHEMA_SUBSCHEMA_KEYS
            | _JSON_SCHEMA_LIST_OF_SCHEMA_KEYS
            | _JSON_SCHEMA_SINGLE_SCHEMA_KEYS
        )
        classified = set(_CONDITIONAL_DECLARATION_KEYWORDS) | set(
            _SCHEMA_VALUED_CATCHALL_KEYWORDS
        )
        assert classified <= walker_keywords, (
            "these keywords are classified as conditional declarers but are not "
            f"schema positions the walkers visit: {sorted(classified - walker_keywords)}"
        )

    def test_every_walker_keyword_is_triaged(self):
        from analitiq.contracts.endpoints import (
            _CONDITIONAL_DECLARATION_KEYWORDS,
            _JSON_SCHEMA_LIST_OF_SCHEMA_KEYS,
            _JSON_SCHEMA_SINGLE_SCHEMA_KEYS,
            _JSON_SCHEMA_SUBSCHEMA_KEYS,
            _SCHEMA_VALUED_CATCHALL_KEYWORDS,
        )

        walker_keywords = (
            _JSON_SCHEMA_SUBSCHEMA_KEYS
            | _JSON_SCHEMA_LIST_OF_SCHEMA_KEYS
            | _JSON_SCHEMA_SINGLE_SCHEMA_KEYS
        )
        classified = set(_CONDITIONAL_DECLARATION_KEYWORDS) | set(
            _SCHEMA_VALUED_CATCHALL_KEYWORDS
        )
        untriaged = walker_keywords - classified - set(self.NON_DECLARING)
        assert not untriaged, (
            f"new schema-position keyword(s) {sorted(untriaged)} are not triaged: "
            "either they can conditionally declare a property name (add them to "
            "_CONDITIONAL_DECLARATION_KEYWORDS or _SCHEMA_VALUED_CATCHALL_KEYWORDS, "
            "so resolve_declared_path stops calling such a path a typo) or they "
            "cannot (add them to NON_DECLARING here, with the reason)"
        )


# ---------------------------------------------------------------------------
# Review findings (PR #131) — each of these shipped as an ACCEPTED document
# until the review probed it. Every test here is a regression pin for one.
# ---------------------------------------------------------------------------


class TestMisspelledResponseScopeIsRefused:
    """`response.bodyy.x` was skipped, not checked.

    `_response_body_segments` returns None for anything that is not
    `response.body[.…]`, on the stated grounds that every other `response.*`
    scope is reserved and engine-owned. Nothing checked that the token actually
    NAMED one, so misspelling `body` bought the same silent truncation #123
    exists to close — one segment to the left of where the rule was looking.
    `_has_known_scope` cannot catch it either: it inspects only the leading
    token, and `response` is real.
    """

    @pytest.mark.parametrize(
        "token",
        ["response.bodyy.next_cursor", "response.bod.next_cursor",
         "response.data.next_cursor", "response.nope"],
    )
    def test_unknown_response_sub_scope_is_rejected(self, token):
        payload = _read_payload("cursor", ref=token)
        with pytest.raises(ValidationError, match="is not one of"):
            parse_endpoint(payload)

    @pytest.mark.parametrize(
        "token",
        ["response.headers.x_next", "response.status", "response.record_count"],
    )
    def test_reserved_sub_scopes_still_pass(self, token):
        # Engine-owned: `response.schema` describes the BODY and has no opinion
        # about them, so they must stay unchecked rather than become collateral.
        parse_endpoint(_read_payload("cursor", stop_when={"missing": {"ref": token}}))


class TestKeysetOrderByFieldResolves:
    """`order_by_field` is a RECORD path, so the `response.body` sweep never saw
    it and its only guard was a shape regex. A seek order defined over a field
    the record shape does not declare advances from a value the engine cannot
    read — truncating or repeating pages, the #123 failure by another route."""

    def _payload(self, order_by_field):
        payload = _read_payload("keyset")
        payload["operations"]["read"]["pagination"]["keyset"]["order_by_field"] = order_by_field
        return payload

    def test_declared_order_by_field_is_accepted(self):
        parse_endpoint(self._payload("id"))

    def test_undeclared_order_by_field_is_rejected(self):
        with pytest.raises(ValidationError, match="order_by_field"):
            parse_endpoint(self._payload("totally_bogus_field"))


class TestCompositionIsLinearNotExponential:
    """A shared `$defs` reached through several `allOf` branches is a DAG, not a
    tree. Expanding each route separately was exponential in depth, and that
    shape is what following ADV-ENDP-026's own advice produces at scale. The
    existing recursion tests only cover CYCLES, which terminate by construction;
    sharing does not."""

    def test_a_deep_diamond_resolves_promptly(self):
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
        assert resolve_declared_path(root, ["leaf"]) == {"type": "string"}
        # Generous on purpose: the assertion is linear-vs-exponential, not a
        # benchmark. Before the memo, depth 24 alone ran for minutes.
        assert time.monotonic() - started < 5.0


class TestRefIntoAConditionalBranchIsRefused:
    """`#/anyOf/0` names a real schema position, but one that applies only to
    instances taking that branch. Following it let the resolver commit to a
    branch — the exact guess it refuses to make when it meets `anyOf` on a node
    directly. Reaching the branch by pointer must not be a way around it."""

    def test_pointer_through_anyOf_does_not_resolve(self):
        root = {
            "type": "object",
            "anyOf": [{"properties": {"only_in_branch_0": {"type": "string"}}}],
            "properties": {"x": {"$ref": "#/anyOf/0"}},
        }
        with pytest.raises(DeclaredPathError):
            resolve_declared_path(root, ["x", "only_in_branch_0"])

    def test_pointer_through_allOf_still_resolves(self):
        # `allOf` branches all apply, so a pointer through one addresses
        # something unconditional and must keep working.
        root = {
            "type": "object",
            "allOf": [{"properties": {"shared": {"type": "string"}}}],
            "properties": {"x": {"$ref": "#/allOf/0"}},
        }
        assert resolve_declared_path(root, ["x", "shared"]) == {"type": "string"}


class TestScopeTyposOnEitherSideOfTheDot:
    """A misspelled SCOPE (`responses.body`) and a misspelled SUB-SCOPE
    (`response.bodyy`) fail identically at run time, so both are refused. The
    typed Expression fields carry a published pattern; the `Any`-typed paging
    slots — where these were accepted — carry nothing."""

    @pytest.mark.parametrize(
        "token",
        ["responses.body.last", "respons.body.last", "Response.body.last"],
    )
    def test_misspelled_leading_scope_is_rejected(self, token):
        payload = _read_payload("keyset")
        payload["operations"]["read"]["pagination"]["keyset"]["initial"] = {"ref": token}
        with pytest.raises(ValidationError, match="not a known resolution scope"):
            parse_endpoint(payload)

    def test_misspelled_scope_in_a_predicate_operand_is_rejected(self):
        payload = _read_payload(
            "cursor", stop_when={"missing": {"ref": "responses.body.has_more"}}
        )
        with pytest.raises(ValidationError, match="not a known resolution scope"):
            parse_endpoint(payload)


class TestRequestSlotsAreSweptToo:
    """A request is built before the response exists, so a `response.*` ref in
    `request.query`/`headers`/`body` is doubly wrong — and it was accepted. It
    is also the site where the value goes onto the wire."""

    def test_undeclared_response_body_path_in_query_is_rejected(self):
        payload = _read_payload("cursor")
        payload["operations"]["read"]["request"]["query"]["extra"] = {
            "ref": "response.body.nope"
        }
        with pytest.raises(ValidationError, match="does not resolve in response.schema"):
            parse_endpoint(payload)

    def test_misspelled_sub_scope_in_headers_is_rejected(self):
        payload = _read_payload("cursor")
        payload["operations"]["read"]["request"]["headers"] = {
            "X-A": {"ref": "response.bodyy.x"}
        }
        with pytest.raises(ValidationError, match="is not one of"):
            parse_endpoint(payload)
