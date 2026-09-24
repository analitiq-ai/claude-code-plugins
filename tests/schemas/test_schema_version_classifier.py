"""The version floor `render_schemas.classify` derives for one schema change.

`write` and `bump-check` both take their floor from `classify`, and `--bump`
can only raise it, so a change classified below its real severity publishes
under a version that promises compatibility it does not have.

Each row pairs two schemas that differ in one keyword's value. What a change
means depends on the keyword: a new member of a disjunction widens what
validates, a new member of a conjunction narrows it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts", "render_schemas")

import render_schemas  # noqa: E402

STR = {"type": "string"}
INT = {"type": "integer"}
PATTERN = {"pattern": "^a"}


def _kind(value: str, *, required: bool = True, typed: bool = True) -> dict:
    branch = {"properties": {"kind": {"const": value}}, "required": ["kind"] if required else []}
    return {"type": "object", **branch} if typed else branch


def _union(*kinds: str) -> dict:
    return {
        "oneOf": [{"$ref": f"#/$defs/{k}"} for k in kinds],
        "$defs": {k: _kind(k) for k in kinds},
    }


def _obj(**node) -> dict:
    return {"type": "object", "properties": {"x": node}}


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        # Introducing a keyword: only annotations, definitions and optional
        # properties leave every valid document valid.
        pytest.param(_obj(), _obj(allOf=[PATTERN]), "major", id="allOf-introduced"),
        pytest.param(
            {"type": "object"}, {"type": "object", "required": ["a"]}, "major", id="required-introduced"
        ),
        pytest.param(_obj(), _obj(**PATTERN), "major", id="pattern-introduced"),
        pytest.param({"type": "object"}, _obj(**STR), "minor", id="properties-introduced"),
        pytest.param(_obj(), _obj(readOnly=True), "minor", id="annotation-introduced"),
        pytest.param(_obj(**{"x-secret": True}), _obj(**{"x-secret": False}), "minor", id="extension-changes"),
        pytest.param(_obj(readOnly=True), _obj(), "minor", id="annotation-removed"),
        pytest.param(_obj(**PATTERN), _obj(), "major", id="validation-keyword-removed"),
        pytest.param(
            {"patternProperties": {"^a": STR}},
            {"patternProperties": {"^a": STR, "^b": INT}},
            "major",
            id="patternProperties-gains-pattern",
        ),
        # Disjunctions: a new member widens, a lost member narrows.
        pytest.param(_obj(enum=["a"]), _obj(enum=["a", "b"]), "minor", id="enum-grows"),
        pytest.param(_obj(enum=["a", "b"]), _obj(enum=["a"]), "major", id="enum-shrinks"),
        pytest.param(_obj(type=["string"]), _obj(type=["string", "null"]), "minor", id="type-grows"),
        pytest.param(_obj(type="string"), _obj(type=["string", "null"]), "minor", id="type-string-widens"),
        pytest.param(_obj(anyOf=[STR]), _obj(anyOf=[STR, INT]), "minor", id="anyOf-grows"),
        pytest.param(_obj(anyOf=[STR, INT]), _obj(anyOf=[STR]), "major", id="anyOf-shrinks"),
        # Conjunctions: a new member narrows, a lost member widens.
        pytest.param({"required": ["a"]}, {"required": ["a", "b"]}, "major", id="required-grows"),
        pytest.param({"required": ["a", "b"]}, {"required": ["a"]}, "minor", id="required-shrinks"),
        pytest.param(_obj(allOf=[STR]), _obj(allOf=[STR, PATTERN]), "major", id="allOf-grows"),
        pytest.param(_obj(allOf=[STR, PATTERN]), _obj(allOf=[STR]), "minor", id="allOf-shrinks"),
        pytest.param(
            {"dependentRequired": {"a": ["b"]}},
            {"dependentRequired": {"a": ["b", "c"]}},
            "major",
            id="dependentRequired-grows",
        ),
        pytest.param(
            {"dependentRequired": {"a": ["b", "c"]}},
            {"dependentRequired": {"a": ["b"]}},
            "minor",
            id="dependentRequired-shrinks",
        ),
        pytest.param(
            {"dependentRequired": {"a": ["b"]}},
            {"dependentRequired": {"a": ["b"], "c": ["d"]}},
            "major",
            id="dependentRequired-gains-key",
        ),
        pytest.param(
            {"dependentSchemas": {"a": STR}},
            {"dependentSchemas": {"a": STR, "c": INT}},
            "major",
            id="dependentSchemas-gains-key",
        ),
        pytest.param(
            {"properties": {"dependentRequired": {"enum": ["a", "b"]}}},
            {"properties": {"dependentRequired": {"enum": ["a"]}}},
            "major",
            id="property-named-dependentRequired-is-not-the-keyword",
        ),
        # Exclusive choice: a new branch can make a unique match ambiguous,
        # unless a required discriminator keeps every branch disjoint.
        pytest.param(
            _obj(oneOf=[STR]), _obj(oneOf=[STR, INT]), "major", id="oneOf-grows-without-discriminator"
        ),
        pytest.param(_union("a"), _union("a", "b"), "minor", id="oneOf-grows-discriminated-by-ref"),
        pytest.param(
            _obj(oneOf=[_kind("a")]),
            _obj(oneOf=[_kind("a"), _kind("b")]),
            "minor",
            id="oneOf-grows-discriminated-inline",
        ),
        pytest.param(
            _obj(oneOf=[_kind("a")]),
            _obj(oneOf=[_kind("a"), _kind("b", required=False)]),
            "major",
            id="oneOf-grows-with-optional-discriminator",
        ),
        pytest.param(
            _obj(oneOf=[_kind("a")]),
            _obj(oneOf=[_kind("a"), _kind("a")]),
            "major",
            id="oneOf-grows-with-repeated-discriminator-value",
        ),
        pytest.param(
            _obj(oneOf=[_kind("a", typed=False)]),
            _obj(oneOf=[_kind("a", typed=False), _kind("b", typed=False)]),
            "major",
            id="oneOf-grows-without-object-type",
        ),
        pytest.param(_obj(oneOf=[STR, INT]), _obj(oneOf=[STR]), "major", id="oneOf-shrinks"),
        # Positional: each member constrains its own index.
        pytest.param(
            _obj(prefixItems=[STR]), _obj(prefixItems=[STR, INT]), "major", id="prefixItems-grows"
        ),
        pytest.param(
            _obj(prefixItems=[STR, INT]), _obj(prefixItems=[INT, STR]), "major", id="prefixItems-reorders"
        ),
        pytest.param(
            _obj(prefixItems=[{"enum": ["a"]}]),
            _obj(prefixItems=[{"enum": ["a", "b"]}]),
            "minor",
            id="prefixItems-member-widens",
        ),
        # A subschema is graded as a schema of its own.
        pytest.param(
            _obj(items={"enum": ["a"]}), _obj(items={"enum": ["a", "b"]}), "minor", id="items-widens"
        ),
        pytest.param(
            _obj(items={"enum": ["a", "b"]}), _obj(items={"enum": ["a"]}), "major", id="items-narrows"
        ),
        # A subschema whose effect on the parent runs the other way (`not`), or
        # either way (`if`, `contains` beside `maxContains`), is not graded as
        # a schema of its own: any change to it is major.
        pytest.param(_obj(**{"not": {"enum": ["a"]}}), _obj(**{"not": {"enum": ["a", "b"]}}), "major", id="not-widens"),
        pytest.param(
            _obj(**{"not": {"type": "string"}}),
            _obj(**{"not": {"type": ["string", "null"]}}),
            "major",
            id="not-type-widens",
        ),
        pytest.param(
            _obj(**{"if": {"enum": ["a"]}, "then": PATTERN}),
            _obj(**{"if": {"enum": ["a", "b"]}, "then": PATTERN}),
            "major",
            id="if-widens",
        ),
        pytest.param(
            _obj(contains={"enum": ["a"]}, maxContains=1),
            _obj(contains={"enum": ["a", "b"]}, maxContains=1),
            "major",
            id="contains-widens-under-maxContains",
        ),
        # Removals and the tightenings a closed object or a bound makes.
        pytest.param({"properties": {"a": STR, "b": INT}}, {"properties": {"a": STR}}, "major", id="property-removed"),
        pytest.param({"$defs": {"A": STR, "B": INT}}, {"$defs": {"A": STR}}, "major", id="defs-entry-removed"),
        pytest.param(_obj(), _obj(additionalProperties=False), "major", id="additionalProperties-false-introduced"),
        pytest.param(
            _obj(additionalProperties=True), _obj(additionalProperties=False), "major", id="additionalProperties-closes"
        ),
        pytest.param(_obj(maxLength=10), _obj(maxLength=5), "major", id="bound-changes"),
        # A value, not a set of subschemas: any change is a different value.
        pytest.param(_obj(const=["a"]), _obj(const=["a", "b"]), "major", id="const-grows"),
        pytest.param(_obj(const={"a": 1}), _obj(const={"a": 1, "b": 2}), "major", id="const-dict-grows"),
    ],
)
def test_change_classifies_by_the_keyword_holding_it(old, new, expected):
    assert render_schemas.classify(old, new) == expected
