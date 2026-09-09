r"""Pin capability block v2: `error_map`, `concurrency`, `sql_capabilities.limits`.

(Raw docstring: it quotes the published `(?![\s\S])` true-end regex, which a
normal string literal would mangle into an invalid escape sequence.)

The engine reads all three as driver facts declared on the connector itself.
The contract models are `extra="forbid"`, so a connector cannot declare any of
them until they ship here. `concurrency` and `sql_capabilities.limits` are
ADDITIVE — absence of the block or of a single cap is legal and means "no
declared cap"; an EMPTY block (`{}`) is legal and equivalent to omission.
`error_map` is additive as a whole (absence means no declared mapping) but not
uniformly inside: `http` keeps that same "empty map is a legal no-op" posture,
while `key_attrs`/`codes` do not — each requires at least one entry when
declared (RULE-CTOR-067's reasoning: an empty `codes` map is a second spelling
of omission that reads like a declared one, the same shape `write_unit`'s
at-least-one-bound rule guards against elsewhere in this file).

Facts that have to hold and stay held, so they are pinned here:

1. **Model boundary.** Declared content is validated fail-loud: an
   off-vocabulary category, a malformed `key_attrs` entry, a retired top-level
   `error_map` key, an unknown field, or a non-positive/boolean cap is a config
   error. `error_map`'s `http` grammar and the failure-category vocabulary
   mirror the engine's typed parser (`cdk/declarations.py`) exactly; `codes`
   keys are deliberately open (driver-defined, not family-defined).

2. **JSON-Schema parity.** Pydantic renders `http`'s patterned-key dict as
   `patternProperties` alone — under which a JSON-Schema-only consumer would
   accept off-grammar keys the model rejects. The model injects a sibling
   `additionalProperties: false` for `http` so an external Draft 2020-12
   validator rejects what Pydantic rejects — proven on the sub-model's own
   schema and end-to-end against the published `connector/latest.json`. The
   `key_attrs`/`codes` pairing rule gets the same treatment via an `allOf`
   mirror on `ErrorMap` itself.
   Unlike the contract's lax int fields (`write_unit.rows`), the three cap
   fields are strict (`strict=True`): booleans are rejected by the model AND by
   the schema's `type: integer`, mirroring the engine parser's explicit
   `isinstance(value, bool)` guard — pinned here in both layers.

   Parity is one-way lax on exactly one known edge, pinned as such below so
   it neither silently widens nor gets "fixed" in the wrong direction: JSON
   Schema's `type: integer` admits a zero-fraction float (`8.0`) the strict
   model rejects — the safe direction (the authoritative validator is the
   stricter layer). A second candidate edge — Python `jsonschema`'s
   `re.search`-based patterns letting `$` match before a trailing newline —
   is closed at the source for `http`'s dict key: the published pattern
   carries a true-end `(?![\s\S])` assertion in place of the trailing `$`
   (`_closed_true_end_keys`), which is end-of-string in BOTH regex dialects,
   so every schema consumer rejects `"429\n"` exactly as the model does.
   `key_attrs`' identifier pattern does NOT get this treatment — like every
   other plain (non-dict-key) `StringConstraints(pattern=...)` field in this
   contract (e.g. `connector_id`'s `SLUG_PATTERN`), it carries a bare
   trailing `$`, so a schema-only consumer admits a trailing-newline entry
   pydantic-core's Rust regex would reject. Consistent with the rest of the
   contract, not a gap unique to this field.
"""
from __future__ import annotations

import copy
import json
import os
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

# The settled capability-block grammar example (with `bulk_load` in its
# per-transport map shape), reused as the accepted baseline that the negative
# cases mutate.
VALID_ERROR_MAP = {
    "key_attrs": ["sqlstate"],
    "codes": {"08": "unreachable", "28000": "auth", "23": "write_rejected"},
    "http": {"429": "rate_limited", "401": "auth"},
}
VALID_SQL_CAPS = {
    "catalog": "none",
    "session_targeting": "per_statement",
    "merge_form": "merge",
    "bulk_load": {"sqlalchemy": "copy_from", "adbc": "adbc_ingest"},
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
        {"http": {"429": "rate_limited"}},  # http alone
        {"http": {}},  # empty http map declares nothing — legal, unlike codes
        {"key_attrs": ["sqlstate"], "codes": {"08": "unreachable"}},  # key_attrs+codes alone
        {"key_attrs": ["sqlstate"], "codes": {"08": "unreachable"}, "http": {"500": "transient"}},
        {"key_attrs": ["errno", "vendor_code"], "codes": {"-803": "config"}},  # ordered, multi-attr
        # the reserved sentinel is a plain identifier-shaped entry, no special case
        {"key_attrs": ["__exception_class__"], "codes": {"OperationalError": "transient"}},
        {"http": {"100": "transient", "599": "transient"}},  # http range edges
        # codes keys are OPEN strings — no family-specific grammar, unlike the
        # retired sqlstate/exception/vendor_code patterns
        {"key_attrs": ["code"], "codes": {"E-1045!": "auth"}},
    ],
)
def test_error_map_accepts(payload):
    ErrorMap.model_validate(payload)
    assert _external_validator(ErrorMap).is_valid(payload)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"grpc": {"UNAVAILABLE": "transient"}}, "unknown field is rejected"),
        ({"sqlstate": {"08": "unreachable"}}, "retired top-level sqlstate key is rejected"),
        ({"exception": {"OperationalError": "transient"}}, "retired top-level exception key is rejected"),
        ({"vendor_code": {"1045": "auth"}}, "retired top-level vendor_code key is rejected"),
        ({"key_attrs": ["sqlstate"], "codes": {"08": "flaky"}}, "off-vocabulary category"),
        ({"key_attrs": ["bad-name!"], "codes": {"x": "auth"}}, "key_attrs entry must be identifier-shaped"),
        ({"key_attrs": [], "codes": {"x": "auth"}}, "key_attrs can't be empty when declared"),
        ({"key_attrs": ["sqlstate"], "codes": {}}, "codes can't be empty when declared"),
        ({"key_attrs": ["sqlstate"], "codes": {"": "auth"}}, "a codes key can't be an empty string"),
        ({"key_attrs": ["sqlstate"]}, "key_attrs without codes is rejected"),
        ({"codes": {"08": "auth"}}, "codes without key_attrs is rejected"),
        ({"key_attrs": "sqlstate", "codes": {"08": "auth"}}, "key_attrs must be an array, not a bare string"),
        ({"key_attrs": ["sqlstate"], "codes": ["08"]}, "codes must be an object, not an array"),
        ({"http": {"999": "auth"}}, "http status first digit is 1-5"),
        ({"http": {"42": "auth"}}, "http status is exactly 3 digits"),
        ({"http": {"4290": "auth"}}, "http status is exactly 3 digits (anchors)"),
        ({"key_attrs": ["sqlstate"], "codes": {"08": None}}, "category must be a string, never null"),
    ],
)
def test_error_map_rejects(payload, why):
    # Both layers, one list: the model must reject, and — thanks to the
    # `additionalProperties: false` mirror injected next to `http`'s
    # `patternProperties`, and the `allOf` mirror of the key_attrs/codes
    # pairing rule — the published schema must reject the same payload.
    # `4290` is the load-bearing anchor case for `http`: it flips to accepted
    # if either regex anchor is lost (prefix `429` / suffix `290` both match
    # unanchored).
    with pytest.raises(ValidationError):
        ErrorMap.model_validate(payload)
    assert not _external_validator(ErrorMap).is_valid(payload)


# Hand-pinned expected member sets — a deliberate restatement so a future
# NARROWING fails loudly (same rationale as EXPECTED_SQL_CAP_ENUMS in
# test_sql_capabilities.py).
#
# The category vocabulary (EXPECTED_ERROR_CATEGORIES) is guarded below against
# a live-imported, pinned `analitiq-cdk` install
# (`test_error_categories_match_engine_cdk`): `ERROR_CATEGORY_VALUES` is a
# public, unconditionally-shipped module constant the engine's own production
# code imports, so pinning the contract's `ErrorCategory` against it directly
# is safe.
#
# `http`'s key pattern stays an unguarded hand restatement: as of the pinned
# `analitiq-cdk`, the engine's equivalent (`_HTTP_KEY`) is module-private with
# no public re-export, so pinning against it would be a
# private-implementation-detail coupling rather than a stability signal the
# engine has actually published. `key_attrs`' pattern is this repo's own
# choice (identifier-shaped, admitting the reserved `__exception_class__`
# sentinel), not mirrored from anything engine-private — `codes` deliberately
# carries no key pattern at all.
EXPECTED_ERROR_CATEGORIES = {
    "transient",
    "config",
    "auth",
    "unreachable",
    "rate_limited",
    "write_rejected",
}
EXPECTED_ERROR_MAP_FIELDS = {"key_attrs", "codes", "http"}
EXPECTED_KEY_ATTR_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
EXPECTED_HTTP_KEY_PATTERN = r"^[1-5][0-9]{2}$"
# What the PUBLISHED schema carries for `http`: the same grammar with the
# trailing `$` replaced by the dialect-portable true-end assertion
# (`_closed_true_end_keys` — Python `re`'s `$` would admit a trailing newline
# that pydantic-core's Rust regex rejects). Pinned VERBATIM, not derived by
# re-running the transform, so a transform bug fails here instead of
# replicating into the expectation.
EXPECTED_PUBLISHED_HTTP_KEY_PATTERN = r"^[1-5][0-9]{2}(?![\s\S])"


def _http_object_schema(schema: dict) -> dict:
    """The object branch of `http`'s `anyOf` (the other branch is null)."""
    branches = schema["properties"]["http"]["anyOf"]
    (obj,) = [b for b in branches if b.get("type") == "object"]
    return obj


def _key_attrs_array_schema(schema: dict) -> dict:
    """The array branch of `key_attrs`'s `anyOf` (the other branch is null)."""
    branches = schema["properties"]["key_attrs"]["anyOf"]
    (arr,) = [b for b in branches if b.get("type") == "array"]
    return arr


def _codes_object_schema(schema: dict) -> dict:
    """The object branch of `codes`'s `anyOf` (the other branch is null)."""
    branches = schema["properties"]["codes"]["anyOf"]
    (obj,) = [b for b in branches if b.get("type") == "object"]
    return obj


def test_error_map_http_grammar_is_pinned():
    schema = ErrorMap.model_json_schema()
    obj = _http_object_schema(schema)
    # Exactly the pinned published key pattern, closed against off-grammar
    # keys...
    assert set(obj["patternProperties"]) == {EXPECTED_PUBLISHED_HTTP_KEY_PATTERN}
    assert obj["additionalProperties"] is False
    # ...and exactly the pinned category vocabulary as values.
    (value_schema,) = obj["patternProperties"].values()
    assert set(value_schema["enum"]) == EXPECTED_ERROR_CATEGORIES


def test_error_map_key_attrs_grammar_is_pinned():
    schema = ErrorMap.model_json_schema()
    arr = _key_attrs_array_schema(schema)
    assert arr["minItems"] == 1
    assert arr["items"]["pattern"] == EXPECTED_KEY_ATTR_PATTERN


def test_error_map_codes_values_are_pinned():
    schema = ErrorMap.model_json_schema()
    obj = _codes_object_schema(schema)
    assert obj["minProperties"] == 1
    # No key grammar at all — `additionalProperties` is the value schema
    # directly, not `false`, and there is no `patternProperties` sibling.
    assert "patternProperties" not in obj
    assert set(obj["additionalProperties"]["enum"]) == EXPECTED_ERROR_CATEGORIES


def test_error_map_fields_are_pinned():
    # These fields are the whole surface; ErrorMap itself is closed
    # (extra="forbid" → additionalProperties: false), so a new field is a
    # contract change, never a silent addition.
    schema = ErrorMap.model_json_schema()
    assert set(schema["properties"]) == EXPECTED_ERROR_MAP_FIELDS
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("category", sorted(EXPECTED_ERROR_CATEGORIES))
def test_every_pinned_category_validates(category):
    error_map = ErrorMap.model_validate({"key_attrs": ["sqlstate"], "codes": {"08": category}})
    assert error_map.codes == {"08": category}


@pytest.mark.parametrize(
    "payload",
    [
        {"key_attrs": ["sqlstate"]},
        {"codes": {"08": "auth"}},
    ],
)
def test_key_attrs_and_codes_required_together(payload):
    # Both layers: the model's `_key_attrs_and_codes_together` validator, and
    # the published schema's symmetric `allOf`/`if`/`then` mirror.
    with pytest.raises(ValidationError):
        ErrorMap.model_validate(payload)
    assert not _external_validator(ErrorMap).is_valid(payload)


@pytest.mark.parametrize("retired_key", ["sqlstate", "exception", "vendor_code"])
def test_retired_family_keys_are_rejected(retired_key):
    # The engine's own acceptance criterion: a block still carrying a
    # top-level family key from the retired shape fails loud rather than
    # being silently reinterpreted or accepted.
    payload = {retired_key: {"08": "unreachable"}}
    with pytest.raises(ValidationError):
        ErrorMap.model_validate(payload)
    assert not _external_validator(ErrorMap).is_valid(payload)


def test_error_categories_match_engine_cdk():
    """Live-import drift guard: `ErrorCategory` against the pinned `analitiq-cdk`.

    Reads `cdk.declarations.ERROR_CATEGORY_VALUES` from the `analitiq-cdk`
    version pinned in requirements-cdk.txt, fresh on every run — no vendored
    snapshot to go stale. A category the engine adds, renames or removes fails
    here before `ErrorCategory` (and this module's `EXPECTED_ERROR_CATEGORIES`)
    silently drifts from what the engine actually classifies.

    `analitiq-cdk` needs its own `--no-deps` install step (requirements-cdk.txt),
    so — unlike this module's other imports — it skips locally when absent
    rather than aborting collection of the whole module; CI sets
    `DRIFT_REQUIRE_CONTRACT_MODELS=1` (the same flag that already guards the
    contract-models pin) so this can never pass by skipping there.
    """
    if os.environ.get("DRIFT_REQUIRE_CONTRACT_MODELS") != "1":
        pytest.importorskip(
            "cdk.declarations",
            reason="requires: pip install --no-deps -r requirements-cdk.txt",
        )
    from cdk.declarations import ERROR_CATEGORY_VALUES

    engine_categories = set(ERROR_CATEGORY_VALUES)
    assert engine_categories == EXPECTED_ERROR_CATEGORIES, (
        "cdk.declarations.ERROR_CATEGORY_VALUES disagrees with the pinned "
        f"contract — engine-only={sorted(engine_categories - EXPECTED_ERROR_CATEGORIES)} "
        f"contract-only={sorted(EXPECTED_ERROR_CATEGORIES - engine_categories)}. "
        "Update ErrorCategory in analitiq/contracts/connector.py and "
        "EXPECTED_ERROR_CATEGORIES here together, as a coordinated engine + "
        "contract revision."
    )


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
        ({"max_connections": 2.5}, "fractional floats are rejected"),
        ({"pool_size": 4}, "unknown fields are rejected"),
    ],
)
def test_concurrency_rejects(payload, why):
    # Both layers: `type: integer` + `minimum: 1` + `additionalProperties:
    # false` reject the same payloads strict Pydantic rejects — including
    # booleans, which the contract's lax int fields (e.g. write_unit.rows)
    # coerce but this block must not. (Zero-fraction floats are the one known
    # divergence — see test_integral_float_is_a_known_one_way_divergence.)
    with pytest.raises(ValidationError):
        Concurrency.model_validate(payload)
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
        {"max_bind_params": 1},  # the ge=1 accept boundary
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
        ({"max_bind_params": 2.5}, "fractional floats are rejected"),
        ({"max_rows": 10}, "unknown fields are rejected"),
    ],
)
def test_limits_rejects(payload, why):
    # Both layers — same discipline as test_concurrency_rejects.
    with pytest.raises(ValidationError):
        SqlLimits.model_validate(payload)
    assert not _external_validator(SqlLimits).is_valid(payload)


# The one known one-way parity edge (see module docstring). Pinned so a
# future change that widens the divergence — or "fixes" it by relaxing the
# model instead of the schema — fails loudly. It goes the safe direction:
# the authoritative validator (the model) is the stricter layer.


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (Concurrency, {"max_connections": 8.0}),
        (SqlLimits, {"max_bind_params": 2100.0}),
    ],
)
def test_integral_float_is_a_known_one_way_divergence(model, payload):
    # Draft 2020-12 defines `type: integer` mathematically — a zero-fraction
    # float qualifies — while the strict model (like the engine parser's
    # `isinstance` check) rejects any float. Inexpressible to close in JSON
    # Schema.
    with pytest.raises(ValidationError):
        model.model_validate(payload)
    assert _external_validator(model).is_valid(payload)


def test_trailing_newline_http_keys_rejected_by_both_layers():
    # Python `jsonschema` matches patterns with `re.search`, where a trailing
    # `$` also matches before a final newline — which would let this key
    # through a schema-only consumer while pydantic-core's Rust regex
    # (end-of-string `$`) rejects it. The published pattern therefore ends in
    # the dialect-portable `(?![\s\S])` (`_closed_true_end_keys`), so BOTH
    # layers reject. Would fail if the transform were dropped. `codes` carries
    # no such mechanism at all — its keys are open strings by design, so a
    # trailing-newline `codes` key is legal, not tested here.
    payload = {"http": {"429\n": "auth"}}
    with pytest.raises(ValidationError):
        ErrorMap.model_validate(payload)
    assert not _external_validator(ErrorMap).is_valid(payload)


def test_sql_capabilities_accepts_limits_member():
    caps = SqlCapabilities.model_validate(
        {**copy.deepcopy(VALID_SQL_CAPS), "limits": dict(VALID_LIMITS)}
    )
    assert caps.limits.max_bind_params == 2100
    assert caps.limits.max_identifier_len == 63


def test_sql_capabilities_limits_is_optional():
    # A block carrying every required fact but omitting `limits` stays valid:
    # `limits` is additive, and every other member is required.
    caps = SqlCapabilities.model_validate(copy.deepcopy(VALID_SQL_CAPS))
    assert caps.limits is None


def test_sql_capabilities_shape_facts_stay_required_alongside_limits():
    # Declaring `limits` does not relax the required-facts rule.
    payload = {"limits": dict(VALID_LIMITS), **copy.deepcopy(VALID_SQL_CAPS)}
    del payload["merge_form"]
    with pytest.raises(ValidationError):
        SqlCapabilities.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "expected_members"),
    [
        (Concurrency, {"max_connections"}),
        (SqlLimits, {"max_bind_params", "max_identifier_len"}),
    ],
)
def test_cap_block_members_are_pinned(model, expected_members):
    # Exact member-set pins, symmetric to test_error_map_families_are_pinned:
    # a new cap field is a contract change (a new engine-consumed fact), never
    # a silent addition.
    schema = model.model_json_schema()
    assert set(schema["properties"]) == expected_members
    assert schema["additionalProperties"] is False


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
    assert connector.error_map.codes["08"] == "unreachable"
    assert connector.error_map.http["429"] == "rate_limited"
    assert connector.concurrency.max_connections == 8
    assert connector.sql_capabilities.limits.max_bind_params == 2100


def test_all_three_blocks_are_optional(db_example):
    # Omission is legal — every connector authored before these three blocks
    # existed stays valid.
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
    # Connector-level: present on EVERY kind. The kind set is pinned exactly
    # so a new kind must consciously join this assertion (it will pass for
    # free via ConnectorBase, but the author should see it happen).
    kind_defs = {name for name in defs if name.endswith("Connector")}
    assert kind_defs == {
        "ApiConnector",
        "DatabaseConnector",
        "NosqlConnector",
        "DocumentConnector",
        "FileConnector",
        "S3Connector",
        "StdoutConnector",
    }
    for kind_def in sorted(kind_defs):
        assert "error_map" in defs[kind_def]["properties"], kind_def
        assert "concurrency" in defs[kind_def]["properties"], kind_def
    # SQL-only: `limits` lives inside SqlCapabilities, which stays
    # database-only.
    assert "limits" in defs["SqlCapabilities"]["properties"]
    assert "sql_capabilities" not in defs["ApiConnector"]["properties"]
    # The parity mirrors survived rendering: `http` stays closed, `key_attrs`
    # keeps its minItems/pattern, and the key_attrs/codes pairing rule's
    # `allOf` mirror is present on `ErrorMap` itself.
    assert _http_object_schema(defs["ErrorMap"])["additionalProperties"] is False
    assert _key_attrs_array_schema(defs["ErrorMap"])["minItems"] == 1
    assert "allOf" in defs["ErrorMap"]


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
    def _unknown_field(doc):
        doc["error_map"]["grpc"] = {"UNAVAILABLE": "transient"}

    def _retired_top_level_key(doc):
        doc["error_map"]["sqlstate"] = {"08": "unreachable"}

    def _off_grammar_key_attrs_entry(doc):
        doc["error_map"]["key_attrs"] = ["bad-name!"]

    def _off_vocabulary_category(doc):
        doc["error_map"]["codes"]["08"] = "flaky"

    def _boolean_cap(doc):
        doc["concurrency"]["max_connections"] = True

    def _zero_limit(doc):
        doc["sql_capabilities"]["limits"]["max_bind_params"] = 0

    def _unknown_limit_field(doc):
        doc["sql_capabilities"]["limits"]["max_rows"] = 10

    for mutate in (
        _unknown_field,
        _retired_top_level_key,
        _off_grammar_key_attrs_entry,
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
