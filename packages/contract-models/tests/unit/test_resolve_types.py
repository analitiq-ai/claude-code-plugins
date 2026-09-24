"""Translating types through type maps: `resolve_type` over one rule list, and
`resolve_types` over a `ResolveTypesRequest` carrying the maps as text.

A malformed request — including a map that is not a type map — is refused when
it is built, so each such rejection is a `ValidationError`, and the published
schema refuses the same request shapes.
"""
from __future__ import annotations

import json
import signal
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from analitiq.contracts.type_map import (
    TYPE_MAP_SCHEMA_URL,
    ResolveTypesRequest,
    resolve_type,
    resolve_types,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PUBLISHED = json.loads((REPO_ROOT / "schemas" / "resolve-types-request" / "latest.json").read_text())


def _map(**sections) -> str:
    return json.dumps({"$schema": TYPE_MAP_SCHEMA_URL, **sections})


def _exact_read(native, arrow):
    return {"match": "exact", "native_type": native, "arrow_type": arrow}


def _regex_read(native, arrow):
    return {"match": "regex", "native_type": native, "arrow_type": arrow}


def _exact_write(arrow, native):
    return {"match": "exact", "arrow_type": arrow, "native_type": native}


_READ_MAP = _map(read=[_exact_read("int8", "Int64")])


def _request(direction, types, maps) -> ResolveTypesRequest:
    return ResolveTypesRequest(direction=direction, types=types, maps=maps)


# --- one rule list ----------------------------------------------------------

def test_read_exact_rule_normalizes_both_sides():
    # Every runtime reader normalizes an exact rule's `native_type` the way it
    # normalizes the probe: trim, collapse whitespace runs, uppercase.
    assert resolve_type("STRING", [_exact_read("string", "Utf8")], "read") == "Utf8"
    assert resolve_type("character varying", [_exact_read("CHARACTER  VARYING", "Utf8")], "read") == "Utf8"
    assert resolve_type("STRING", [_exact_read("BIGINT", "Int64")], "read") is None


def test_read_regex_matches_the_normalized_probe_not_the_authored_spelling():
    # The probe is uppercased before a regex sees it, so a lowercase literal
    # in the pattern never matches.
    assert resolve_type("varchar(255)", [_regex_read(r"^varchar\((?<n>\d+)\)$", "Utf8")], "read") is None
    assert resolve_type("varchar(255)", [_regex_read(r"^VARCHAR\((?<n>\d+)\)$", "Utf8")], "read") == "Utf8"


def test_regex_captures_substitute_into_the_render():
    rules = [_regex_read(r"^DECIMAL\((?<p>\d+),(?<s>\d+)\)$", "Decimal128(${p}, ${s})")]
    assert resolve_type("decimal(38,9)", rules, "read") == "Decimal128(38, 9)"


def test_regex_uses_re2_semantics():
    # RE2's `\d` is ASCII, so a rule spelling digits with it does not cover a
    # non-ASCII digit.
    rules = [_regex_read(r"^N\d$", "Int8")]
    assert resolve_type("N3", rules, "read") == "Int8"
    assert resolve_type("N٣", rules, "read") is None


def test_regex_leaves_a_probe_re2_cannot_read_unresolved():
    # Raw JSON can spell a lone surrogate, which has no UTF-8 encoding for RE2.
    assert resolve_type("A\ud800", [_regex_read(r"^A.*$", "Utf8")], "read") is None


def test_write_compares_the_arrow_type_as_authored():
    rules = [_exact_write("Utf8", "TEXT")]
    assert resolve_type("Utf8", rules, "write") == "TEXT"
    assert resolve_type("UTF8", rules, "write") is None


def test_first_matching_rule_wins():
    rules = [_exact_read("int", "Int32"), _exact_read("INT", "Int64")]
    assert resolve_type("int", rules, "read") == "Int32"


def _within(seconds, call):
    """`call()`, failed rather than stalled when it has not returned in time."""
    def _stalled(_signum, _frame):
        raise AssertionError(f"did not return within {seconds}s")

    previous = signal.signal(signal.SIGALRM, _stalled)
    signal.alarm(seconds)
    try:
        return call()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_returns_promptly_on_a_nested_quantifier():
    rules = [{"match": "regex", "arrow_type": r"(A+)+$", "native_type": "TEXT"}]
    assert _within(5, lambda: resolve_type("A" * 10_000 + "B", rules, "write")) is None


# --- the request ------------------------------------------------------------

def test_earlier_map_takes_precedence():
    connection = _map(read=[_exact_read("citext", "LargeUtf8")])
    connector = _map(read=[_exact_read("citext", "Utf8"), _exact_read("int8", "Int64")])
    result = resolve_types(_request("read", ["citext", "int8"], [connection, connector]))
    assert result == {"resolved": {"citext": "LargeUtf8", "int8": "Int64"}, "gaps": []}


def test_unresolved_types_are_gaps_in_request_order():
    connector = _map(read=[_exact_read("int8", "Int64")])
    result = resolve_types(_request("read", ["vector(3)", "int8", "citext", "vector(3)"], [connector]))
    assert result == {
        "resolved": {"vector(3)": None, "int8": "Int64", "citext": None},
        "gaps": ["vector(3)", "citext"],
    }


def test_a_map_without_the_direction_contributes_no_rules():
    connection = _map(write=[_exact_write("Utf8", "TEXT")])
    connector = _map(read=[_exact_read("int8", "Int64")])
    result = resolve_types(_request("read", ["int8"], [connection, connector]))
    assert result == {"resolved": {"int8": "Int64"}, "gaps": []}


def test_refuses_when_no_map_has_the_direction():
    # Every type would come back a gap, which reads as a vocabulary to cover
    # rather than the missing section it is.
    with pytest.raises(ValidationError, match="no map carries a 'read' section"):
        _request("read", ["int8"], [_map(write=[_exact_write("Utf8", "TEXT")])])


@pytest.mark.parametrize("text", ["not json", "[]", json.dumps({"read": []}),
                                  _map(read=[{"match": "exact", "native_type": "int8"}])])
def test_refuses_a_map_that_is_not_a_type_map(text):
    # A broken rule is skipped at resolution, so resolving over a broken map
    # would report a gap the map meant to cover.
    with pytest.raises(ValidationError, match="maps\\[1\\]"):
        _request("read", ["int8"], [_map(read=[_exact_read("int8", "Int64")]), text])


def _refused_by_both(raw: dict) -> None:
    with pytest.raises(ValidationError):
        ResolveTypesRequest.model_validate(raw)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, PUBLISHED)


def test_published_schema_accepts_a_request():
    raw = {"direction": "read", "types": ["int8"], "maps": [_READ_MAP]}
    jsonschema.validate(raw, PUBLISHED)
    assert ResolveTypesRequest.model_validate(raw).direction == "read"



@pytest.mark.parametrize("raw", [
    {"direction": "sideways", "types": ["int8"], "maps": [_READ_MAP]},
    {"direction": "read", "types": [], "maps": [_READ_MAP]},
    {"direction": "read", "types": [""], "maps": [_READ_MAP]},
    {"direction": "read", "types": [1], "maps": [_READ_MAP]},
    {"direction": "read", "types": ["int8"], "maps": []},
    {"direction": "read", "types": ["int8"], "maps": [{}]},
    {"direction": "read", "types": ["int8"]},
    {"direction": "read", "types": ["int8"], "maps": [_READ_MAP], "extra": 1},
])
def test_refuses_a_malformed_request(raw):
    _refused_by_both(raw)
