"""The version floor `render_schemas.classify` derives for one schema change.

`write` and `bump-check` both take their floor from `classify`, and `--bump`
can only raise it, so a change classified below its real severity publishes
under a version that promises compatibility it does not have.

Each row pairs two schemas that differ only in one list. What a list's growth
means depends on the keyword holding it: a new member of a disjunction widens
what validates, a new member of a conjunction narrows it.
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


def _kind(value: str, *, required: bool = True) -> dict:
    return {
        "type": "object",
        "properties": {"kind": {"const": value}},
        "required": ["kind"] if required else [],
    }


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
        # Disjunctions: a new member widens, a lost member narrows.
        pytest.param(_obj(enum=["a"]), _obj(enum=["a", "b"]), "minor", id="enum-grows"),
        pytest.param(_obj(enum=["a", "b"]), _obj(enum=["a"]), "major", id="enum-shrinks"),
        pytest.param(_obj(type=["string"]), _obj(type=["string", "null"]), "minor", id="type-grows"),
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
        # A value, not a set of subschemas: any change is a different value.
        pytest.param(_obj(const=["a"]), _obj(const=["a", "b"]), "major", id="const-grows"),
    ],
)
def test_list_change_classifies_by_the_keyword_holding_it(old, new, expected):
    assert render_schemas.classify(old, new) == expected
