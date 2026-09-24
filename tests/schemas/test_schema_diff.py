"""The structural diff the bump models classify and bump records hash."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from schema_diff import diff, diff_lines, diff_sha256  # noqa: E402


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        pytest.param({}, {"minLength": 1}, ["ADDED minLength = 1"], id="added"),
        pytest.param({"minLength": 1}, {}, ["REMOVED minLength = 1"], id="removed"),
        pytest.param({"type": "string"}, {"type": "integer"}, ['CHANGED type: "string" -> "integer"'], id="changed"),
        pytest.param(
            {"type": "string"}, {"type": ["string", "null"]},
            ['CHANGED type: "string" -> ["string","null"]'], id="changed-kind",
        ),
        pytest.param({"enum": ["a"]}, {"enum": ["a", "b"]}, ['LIST-ADDED enum: ["b"]'], id="list-added"),
        pytest.param({"enum": ["a", "b"]}, {"enum": ["a"]}, ['LIST-REMOVED enum: ["b"]'], id="list-removed"),
        pytest.param(
            {"enum": ["a", "b"]}, {"enum": ["a", "c"]},
            ['LIST-ADDED enum: ["c"]', 'LIST-REMOVED enum: ["b"]'], id="list-added-and-removed",
        ),
        pytest.param({"enum": ["a", "b"]}, {"enum": ["b", "a"]}, ["LIST-REORDERED enum"], id="list-reordered"),
        pytest.param(
            {"anyOf": [{"type": "string"}]}, {"anyOf": [{"type": "string"}, {"type": "null"}]},
            ['ITEM-ADDED anyOf: {"type":"null"}'], id="item-added",
        ),
        pytest.param(
            {"anyOf": [{"type": "string"}, {"type": "null"}]}, {"anyOf": [{"type": "string"}]},
            ['ITEM-REMOVED anyOf: {"type":"null"}'], id="item-removed",
        ),
        pytest.param(
            {"allOf": [{"minLength": 1}, {"maxLength": 5}]}, {"allOf": [{"minLength": 1}, {"maxLength": 3}]},
            ["CHANGED allOf/1/maxLength: 5 -> 3"], id="equal-length-lists-recurse-positionally",
        ),
        pytest.param({}, {"title": "T"}, ['DOC-ADDED title = "T"'], id="doc-added"),
        pytest.param({"$comment": "c"}, {}, ['DOC-REMOVED $comment = "c"'], id="doc-removed"),
        pytest.param(
            {"description": "a"}, {"description": "b"}, ['DOC-CHANGED description: "a" -> "b"'], id="doc-changed",
        ),
        pytest.param(
            {"examples": [1]}, {"examples": [1, 2]}, ["DOC-CHANGED examples: [1] -> [1,2]"], id="doc-list-changed",
        ),
    ],
)
def test_each_difference_renders_as_its_tag(old, new, expected):
    assert diff_lines(old, new) == expected


def test_a_property_named_like_a_doc_keyword_is_a_schema_change():
    old = {"properties": {"title": {"type": "string"}}}
    new = {"properties": {"title": {"type": "integer"}, "description": {"type": "string"}}}
    assert diff_lines(old, new) == [
        'ADDED properties/description = {"type":"string"}',
        'CHANGED properties/title/type: "string" -> "integer"',
    ]


def test_a_doc_keyword_inside_a_value_is_part_of_the_value():
    old = {"default": {"description": "a"}, "const": {"title": "x"}}
    new = {"default": {"description": "b"}, "const": {"title": "y"}}
    assert diff_lines(old, new) == [
        'CHANGED const/title: "x" -> "y"',
        'CHANGED default/description: "a" -> "b"',
    ]


def test_stamps_are_not_differences():
    assert diff_lines({"$id": "a", "version": "1.0.0"}, {"$id": "b", "version": "2.0.0"}) == []


def test_a_nested_key_named_version_is_a_difference():
    old = {"properties": {"version": {"type": "string"}}}
    new = {"properties": {"version": {"type": "integer"}}}
    assert diff_lines(old, new) == ['CHANGED properties/version/type: "string" -> "integer"']


def test_keys_are_walked_in_sorted_order():
    assert diff_lines({}, {"b": 1, "a": 1, "c": {"z": 1, "y": 1}}) == [
        "ADDED a = 1", "ADDED b = 1", 'ADDED c = {"y":1,"z":1}',
    ]


def test_no_value_is_truncated():
    required = [f"field_{i}" for i in range(400)]
    added = {"type": "object", "properties": {"x": {"description": "d" * 5000}}, "required": required}
    [line] = diff_lines({"$defs": {}}, {"$defs": {"Big": added}})
    assert '"field_399"' in line
    assert "d" * 5000 in line


def test_changes_carry_their_key_path():
    old = {"$defs": {"A/B": {"type": "string"}}}
    new = {"$defs": {"A/B": {"type": "integer"}}}
    [change] = diff(old, new)
    assert change.path == ("$defs", "A/B", "type")


def test_the_digest_pins_the_exact_lines():
    lines = diff_lines({"enum": ["a"]}, {"enum": ["a", "b"]})
    assert diff_sha256(lines) == diff_sha256(list(lines))
    assert diff_sha256(lines) != diff_sha256([*lines, "ADDED x = 1"])
    assert diff_sha256(lines) != diff_sha256(list(reversed([*lines, "ADDED x = 1"])))
