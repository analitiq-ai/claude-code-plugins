"""Pin capability block v2: `error_map`, `concurrency`, `sql_capabilities.limits`.

Issue #89 (engine analitiq-engine#401/#407): the engine reads three additional
driver-fact declarations from the connector definition. The contract models are
`extra="forbid"`, so a connector cannot declare any of them until they ship
here. All three are ADDITIVE — unlike the five required shape facts of
`sql_capabilities` (and unlike `write_unit`'s at-least-one-bound rule), absence
of a block, a family, or a single cap is legal and means "no declared mapping /
no declared cap"; an EMPTY block (`{}`) is legal and equivalent to omission.

Facts that have to hold and stay held, so they are pinned here:

1. **Model boundary.** Declared content is validated fail-loud: an
   off-vocabulary category, a malformed family identifier, an unknown family or
   field, or a non-positive/boolean cap is a config error. Key grammars and the
   failure-category vocabulary mirror the engine's typed parsers
   (`cdk/declarations.py`, `cdk/sql/capabilities.py`) exactly.

2. **JSON-Schema parity.** Pydantic renders a patterned-key dict as
   `patternProperties` alone — under which a JSON-Schema-only consumer would
   accept off-grammar keys the model rejects. The models inject a sibling
   `additionalProperties: false` per family so an external Draft 2020-12
   validator rejects exactly what Pydantic rejects — proven on each sub-model's
   own schema and end-to-end against the published `connector/latest.json`.
   Unlike the contract's lax int fields (`write_unit.rows`), the three cap
   fields are strict (`strict=True`): booleans are rejected by the model AND by
   the schema's `type: integer`, mirroring the engine parser's explicit
   `isinstance(value, bool)` guard — pinned here in both layers.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from analitiq.contracts.connector import (
    Concurrency,
    ErrorMap,
    SqlCapabilities,
    SqlLimits,
    parse_connector,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
POSTGRES_EXAMPLE = (
    REPO_ROOT
    / "plugins"
    / "analitiq-connector-builder"
    / "skills"
    / "connector-spec-db"
    / "examples"
    / "postgresql"
    / "postgresql.example.json"
)

# The issue #89 grammar example, reused as the accepted baseline that the
# negative cases mutate.
VALID_ERROR_MAP = {
    "sqlstate": {"08": "unreachable", "28000": "auth", "23": "write_rejected"},
    "exception": {"OperationalError": "transient"},
    "vendor_code": {"1045": "auth"},
    "http": {"429": "rate_limited", "401": "auth"},
}
VALID_SQL_CAPS = {
    "catalog": "none",
    "session_targeting": "per_statement",
    "merge_form": "merge",
    "bulk_load": "copy_from",
    "stage": {"scope": "temp", "schema": "target", "transactional_ddl": True},
}
VALID_LIMITS = {"max_bind_params": 2100, "max_identifier_len": 63}


def _external_validator(model) -> Draft202012Validator:
    """A Draft 2020-12 validator over the model's own published JSON Schema."""
    return Draft202012Validator(model.model_json_schema())


# ---------------------------------------------------------------------------
# ErrorMap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        VALID_ERROR_MAP,
        {},  # empty block declares nothing — legal, ≡ absence
        {"sqlstate": {}},  # empty family map declares nothing — legal
        {"http": {"429": "rate_limited"}},  # any single family alone
        {"sqlstate": {"08": "unreachable"}, "http": {"500": "transient"}},
        {"vendor_code": {"-803": "config"}},  # negative vendor codes are legal
        {"exception": {"_Timeout": "transient"}},  # leading underscore is legal
        {"http": {"100": "transient", "599": "transient"}},  # range edges
    ],
)
def test_error_map_accepts(payload):
    ErrorMap.model_validate(payload)
    assert _external_validator(ErrorMap).is_valid(payload)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"grpc": {"UNAVAILABLE": "transient"}}, "unknown family is rejected"),
        ({"sqlstate": {"08": "flaky"}}, "off-vocabulary category"),
        ({"sqlstate": {"zz": "auth"}}, "sqlstate keys are uppercase-only"),
        ({"sqlstate": {"0": "auth"}}, "sqlstate class is exactly 2 chars"),
        ({"sqlstate": {"0800": "auth"}}, "sqlstate is 2 or 5 chars, never 4"),
        ({"exception": {"1BadName": "auth"}}, "exception name can't start with a digit"),
        ({"exception": {"Op.Error": "auth"}}, "exception name is a bare class name"),
        ({"vendor_code": {"1.5": "auth"}}, "vendor code is an integer string"),
        ({"vendor_code": {"": "auth"}}, "vendor code can't be empty"),
        ({"http": {"999": "auth"}}, "http status first digit is 1-5"),
        ({"http": {"42": "auth"}}, "http status is exactly 3 digits"),
        ({"http": {"4290": "auth"}}, "http status is exactly 3 digits"),
        ({"sqlstate": ["08"]}, "family must be an object, not an array"),
        ({"sqlstate": {"08": None}}, "category must be a string, never null"),
    ],
)
def test_error_map_rejects(payload, why):
    with pytest.raises(ValidationError):
        ErrorMap.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"grpc": {"UNAVAILABLE": "transient"}},
        {"sqlstate": {"08": "flaky"}},
        {"sqlstate": {"zz": "auth"}},
        {"http": {"999": "auth"}},
        {"exception": {"1BadName": "auth"}},
        {"vendor_code": {"1.5": "auth"}},
    ],
)
def test_error_map_json_schema_rejects(payload):
    # The `additionalProperties: false` mirror injected next to each family's
    # `patternProperties` must make the published schema reject off-grammar
    # keys exactly as the model does — without it, patternProperties alone
    # would let them through.
    assert not _external_validator(ErrorMap).is_valid(payload)


# Hand-pinned expected member sets — a deliberate restatement so a future
# NARROWING fails loudly (same rationale as EXPECTED_SQL_CAP_ENUMS in
# test_sql_capabilities.py). The engine's `cdk/declarations.py`
# (ERROR_CATEGORY_VALUES, per-family key regexes) is the owner; this is the
# sanctioned "test's assertion target" copy (no-drift rule #3).
EXPECTED_ERROR_CATEGORIES = {
    "transient",
    "config",
    "auth",
    "unreachable",
    "rate_limited",
    "write_rejected",
}
EXPECTED_FAMILY_KEY_PATTERNS = {
    "sqlstate": r"^[0-9A-Z]{2}([0-9A-Z]{3})?$",
    "exception": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "vendor_code": r"^-?[0-9]+$",
    "http": r"^[1-5][0-9]{2}$",
}


def _family_object_schema(schema: dict, family: str) -> dict:
    """The object branch of a family's `anyOf` (the other branch is null)."""
    branches = schema["properties"][family]["anyOf"]
    (obj,) = [b for b in branches if b.get("type") == "object"]
    return obj


@pytest.mark.parametrize("family", sorted(EXPECTED_FAMILY_KEY_PATTERNS))
def test_error_map_family_grammar_is_pinned(family):
    schema = ErrorMap.model_json_schema()
    obj = _family_object_schema(schema, family)
    # Exactly the pinned key pattern, closed against off-grammar keys...
    assert set(obj["patternProperties"]) == {EXPECTED_FAMILY_KEY_PATTERNS[family]}
    assert obj["additionalProperties"] is False
    # ...and exactly the pinned category vocabulary as values.
    (value_schema,) = obj["patternProperties"].values()
    assert set(value_schema["enum"]) == EXPECTED_ERROR_CATEGORIES


def test_error_map_families_are_pinned():
    # The four families are the whole surface; ErrorMap itself is closed
    # (extra="forbid" → additionalProperties: false), so a fifth family is a
    # contract change, never a silent addition.
    schema = ErrorMap.model_json_schema()
    assert set(schema["properties"]) == set(EXPECTED_FAMILY_KEY_PATTERNS)
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("category", sorted(EXPECTED_ERROR_CATEGORIES))
def test_every_pinned_category_validates(category):
    error_map = ErrorMap.model_validate({"sqlstate": {"08": category}})
    assert error_map.sqlstate == {"08": category}


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"max_connections": 8},
        {"max_connections": 1},
        {},  # empty block ≡ absence — legal (contrast write_unit)
    ],
)
def test_concurrency_accepts(payload):
    Concurrency.model_validate(payload)
    assert _external_validator(Concurrency).is_valid(payload)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"max_connections": 0}, "must be >= 1"),
        ({"max_connections": -1}, "must be >= 1"),
        ({"max_connections": True}, "booleans are rejected (strict int)"),
        ({"max_connections": "8"}, "strings are rejected (strict int)"),
        ({"max_connections": 2.5}, "floats are rejected (strict int)"),
        ({"pool_size": 4}, "unknown fields are rejected"),
    ],
)
def test_concurrency_rejects(payload, why):
    with pytest.raises(ValidationError):
        Concurrency.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"max_connections": 0},
        {"max_connections": True},
        {"max_connections": "8"},
        {"pool_size": 4},
    ],
)
def test_concurrency_json_schema_rejects(payload):
    # `type: integer` + `minimum: 1` reject the same payloads strict Pydantic
    # rejects — including booleans, which the contract's lax int fields
    # (e.g. write_unit.rows) coerce but this block must not.
    assert not _external_validator(Concurrency).is_valid(payload)


# ---------------------------------------------------------------------------
# SqlLimits (sql_capabilities.limits)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        VALID_LIMITS,
        {"max_bind_params": 2100},  # partial declaration is legal (additive)
        {"max_identifier_len": 63},
        {},  # empty block ≡ absence — legal
    ],
)
def test_limits_accepts(payload):
    SqlLimits.model_validate(payload)
    assert _external_validator(SqlLimits).is_valid(payload)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"max_bind_params": 0}, "must be >= 1"),
        ({"max_identifier_len": -5}, "must be >= 1"),
        ({"max_bind_params": True}, "booleans are rejected (strict int)"),
        ({"max_bind_params": "2100"}, "strings are rejected (strict int)"),
        ({"max_bind_params": 2.5}, "floats are rejected (strict int)"),
        ({"max_rows": 10}, "unknown fields are rejected"),
    ],
)
def test_limits_rejects(payload, why):
    with pytest.raises(ValidationError):
        SqlLimits.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"max_bind_params": 0},
        {"max_bind_params": True},
        {"max_bind_params": "2100"},
        {"max_rows": 10},
    ],
)
def test_limits_json_schema_rejects(payload):
    assert not _external_validator(SqlLimits).is_valid(payload)


def test_sql_capabilities_accepts_limits_member():
    caps = SqlCapabilities.model_validate(
        {**copy.deepcopy(VALID_SQL_CAPS), "limits": dict(VALID_LIMITS)}
    )
    assert caps.limits.max_bind_params == 2100
    assert caps.limits.max_identifier_len == 63


def test_sql_capabilities_limits_is_optional():
    # A pre-#89 five-fact block stays valid — `limits` is the one additive
    # member of an otherwise all-required block.
    caps = SqlCapabilities.model_validate(copy.deepcopy(VALID_SQL_CAPS))
    assert caps.limits is None


def test_sql_capabilities_shape_facts_stay_required_alongside_limits():
    # Declaring `limits` does not relax the five-required-facts rule.
    payload = {"limits": dict(VALID_LIMITS), **copy.deepcopy(VALID_SQL_CAPS)}
    del payload["merge_form"]
    with pytest.raises(ValidationError):
        SqlCapabilities.model_validate(payload)


# ---------------------------------------------------------------------------
# Field wiring on the connector documents
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_example() -> dict:
    return json.loads(POSTGRES_EXAMPLE.read_text())


def _minimal_api_doc(db_example: dict) -> dict:
    return {
        "$schema": db_example["$schema"],
        "kind": "api",
        "connector_id": "acme-api",
        "version": "1.0.0",
        "default_transport": "main",
        "transports": {
            "main": {"transport_type": "http", "base_url": "https://api.acme.test"}
        },
        "auth": {"type": "none"},
        "connection_contract": {},
    }


def test_database_connector_carries_all_three_blocks(db_example):
    doc = copy.deepcopy(db_example)
    doc["error_map"] = copy.deepcopy(VALID_ERROR_MAP)
    doc["concurrency"] = {"max_connections": 8}
    doc["sql_capabilities"] = {
        **copy.deepcopy(VALID_SQL_CAPS),
        "limits": dict(VALID_LIMITS),
    }
    connector = parse_connector(doc)
    assert connector.error_map.sqlstate["08"] == "unreachable"
    assert connector.error_map.http["429"] == "rate_limited"
    assert connector.concurrency.max_connections == 8
    assert connector.sql_capabilities.limits.max_bind_params == 2100


def test_all_three_blocks_are_optional(db_example):
    # Omission is legal — every connector authored before #89 stays valid.
    connector = parse_connector(copy.deepcopy(db_example))
    assert connector.error_map is None
    assert connector.concurrency is None


def test_error_map_and_concurrency_are_connector_level(db_example):
    # Both are valid on a non-database kind: `http` classifies API driver
    # errors, and any kind may cap its connections.
    doc = _minimal_api_doc(db_example)
    doc["error_map"] = {"http": {"429": "rate_limited", "401": "auth"}}
    doc["concurrency"] = {"max_connections": 4}
    connector = parse_connector(doc)
    assert connector.error_map.http["429"] == "rate_limited"
    assert connector.concurrency.max_connections == 4


def test_database_connector_rejects_bad_error_map(db_example):
    doc = copy.deepcopy(db_example)
    doc["error_map"] = {"grpc": {"UNAVAILABLE": "transient"}}
    with pytest.raises(ValidationError):
        parse_connector(doc)


def test_database_connector_rejects_bad_concurrency(db_example):
    doc = copy.deepcopy(db_example)
    doc["concurrency"] = {"max_connections": True}
    with pytest.raises(ValidationError):
        parse_connector(doc)


# ---------------------------------------------------------------------------
# Published-artifact structural pin
# ---------------------------------------------------------------------------


def test_published_connector_schema_exposes_new_defs():
    """The rendered public schema must carry the new $defs and their mirrors.

    `render_schemas.py check` already pins committed-vs-rendered; this makes
    the presence of the v2 blocks — and the per-family
    `additionalProperties: false` parity mirror — an explicit, named assertion
    at the exact contract external consumers fetch.
    """
    latest = json.loads(
        (REPO_ROOT / "schemas" / "connector" / "latest.json").read_text()
    )
    defs = latest["$defs"]
    assert {"ErrorMap", "Concurrency", "SqlLimits"} <= set(defs)
    # Connector-level: present on every kind.
    for kind_def in ("ApiConnector", "DatabaseConnector", "StdoutConnector"):
        assert "error_map" in defs[kind_def]["properties"]
        assert "concurrency" in defs[kind_def]["properties"]
    # SQL-only: `limits` lives inside SqlCapabilities, which stays
    # database-only.
    assert "limits" in defs["SqlCapabilities"]["properties"]
    assert "sql_capabilities" not in defs["ApiConnector"]["properties"]
    # The parity mirror survived rendering on every family.
    for family in EXPECTED_FAMILY_KEY_PATTERNS:
        obj = _family_object_schema(defs["ErrorMap"], family)
        assert obj["additionalProperties"] is False


def test_full_connector_validates_against_published_schema(db_example):
    """End-to-end parity against the artifact a real consumer actually fetches."""
    schema = json.loads(
        (REPO_ROOT / "schemas" / "connector" / "latest.json").read_text()
    )
    validator = Draft202012Validator(schema)

    valid = copy.deepcopy(db_example)
    valid["error_map"] = copy.deepcopy(VALID_ERROR_MAP)
    valid["concurrency"] = {"max_connections": 8}
    valid["sql_capabilities"] = {
        **copy.deepcopy(VALID_SQL_CAPS),
        "limits": dict(VALID_LIMITS),
    }
    assert validator.is_valid(valid), sorted(
        e.message for e in validator.iter_errors(valid)
    )
    parse_connector(valid)  # the model agrees

    # Each `mutate` is applied to a fresh deepcopy of the valid doc, so a
    # rejection isolates to that one change — covering every v2 rule
    # end-to-end against the composed artifact.
    def _unknown_family(doc):
        doc["error_map"]["grpc"] = {"UNAVAILABLE": "transient"}

    def _off_grammar_key(doc):
        doc["error_map"]["sqlstate"]["zz"] = "auth"

    def _off_vocabulary_category(doc):
        doc["error_map"]["sqlstate"]["08"] = "flaky"

    def _boolean_cap(doc):
        doc["concurrency"]["max_connections"] = True

    def _zero_limit(doc):
        doc["sql_capabilities"]["limits"]["max_bind_params"] = 0

    def _unknown_limit_field(doc):
        doc["sql_capabilities"]["limits"]["max_rows"] = 10

    for mutate in (
        _unknown_family,
        _off_grammar_key,
        _off_vocabulary_category,
        _boolean_cap,
        _zero_limit,
        _unknown_limit_field,
    ):
        broken = copy.deepcopy(valid)
        mutate(broken)
        assert not validator.is_valid(broken), broken
        with pytest.raises(ValidationError):
            parse_connector(broken)
