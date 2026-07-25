"""Pin the SQL write-path capability models and the connector-level write unit.

Issue #87: the engine's SQL write path is "refuse, don't guess"
(analitiq-engine#390, ADR `docs/sql-write-path-v2.md` §5) — it reads SQL-shape
capabilities from the connector definition instead of probing the live database.
`DatabaseConnector` gains an optional `sql_capabilities` block and `ConnectorBase`
gains an optional `write_unit`; the contract models are `extra="forbid"`, so a
connector cannot declare either block until it ships here.

Two facts have to hold and stay held, so both are pinned here:

1. **Model boundary.** A declared `sql_capabilities` block is COMPLETE — all
   five top-level facts required — and the two cross-field rules
   (`stage.dedicated_schema` present iff `stage.schema == "dedicated"`;
   `write_unit` carries at least one of `rows`/`bytes`) are enforced. Omission
   of either block is legal (backwards compatible). `bulk_load` (issue #92,
   analitiq-engine#406) is a per-transport mapping — required as an object,
   `{}` legal, per-family mechanism enums, explicit null refused — mirroring
   the engine parser's acceptance/refusal exactly.

2. **JSON-Schema parity.** The two cross-field rules are mirrored into the
   published JSON Schema via `json_schema_extra` (the same technique as
   `AdbcTransport`'s dsn/db_kwargs `anyOf` and `ConnectionConditionPredicate`'s
   exactly-one-operator `oneOf`), and `bulk_load`'s per-family enums publish
   bare — no null branch, no default (`_enum_branch_only`) — so a JSON-Schema-only
   consumer (the FE, a third-party validator) rejects exactly what the Pydantic
   model rejects for these structural rules (including an explicit null
   mechanism) — proven here both on each sub-model's own schema and,
   end-to-end, against the published `connector/latest.json` a real consumer
   fetches. (Primitive-type coercion is deliberately out of scope: like
   every int field in the contract, `rows`/`bytes` inherit Pydantic's lax
   bool→int coercion that a `type: integer` schema does not share — a
   contract-wide characteristic, not a rule this file mirrors.) These tests
   exercise the parity through an external Draft 2020-12 validator, not just the
   Python model.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from analitiq.contracts.connector import (
    SqlBulkLoad,
    SqlCapabilities,
    SqlStageCapabilities,
    WriteUnit,
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

# A minimal, fully-declared stage/capabilities/write-unit trio reused as the
# accepted baseline that the negative cases mutate. `bulk_load` declares both
# transport families — the dual-transport case (postgres: ADBC default +
# SQLAlchemy) that motivated the per-transport mapping (issue #92,
# analitiq-engine#406).
VALID_STAGE = {"scope": "temp", "schema": "target", "transactional_ddl": True}
VALID_BULK_LOAD = {"sqlalchemy": "copy_from", "adbc": "adbc_ingest"}
VALID_SQL_CAPS = {
    "catalog": "none",
    "session_targeting": "per_statement",
    "merge_form": "merge",
    "bulk_load": VALID_BULK_LOAD,
    "stage": VALID_STAGE,
}


def _external_validator(model) -> Draft202012Validator:
    """A Draft 2020-12 validator over the model's own published JSON Schema.

    `json_schema_extra` rides into `model_json_schema()`, so this is exactly the
    contract an external consumer validates against — no runtime Python model in
    the loop.
    """
    return Draft202012Validator(model.model_json_schema())


# ---------------------------------------------------------------------------
# SqlStageCapabilities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "temp", "schema": "target", "transactional_ddl": True},
        {"scope": "real", "schema": "target", "transactional_ddl": False},
        {
            "scope": "real",
            "schema": "dedicated",
            "dedicated_schema": "_analitiq_stage",
            "transactional_ddl": True,
        },
    ],
)
def test_stage_accepts(payload):
    stage = SqlStageCapabilities.model_validate(payload)
    # `schema` is the wire key; the Python attribute is `schema_` (it would
    # otherwise shadow BaseModel.schema — the same aliasing the `in_`/`in`
    # predicate operator uses).
    assert stage.schema_ == payload["schema"]
    assert _external_validator(SqlStageCapabilities).is_valid(payload)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        (
            {"scope": "temp", "schema": "dedicated", "transactional_ddl": True},
            "schema='dedicated' requires dedicated_schema",
        ),
        (
            {
                "scope": "temp",
                "schema": "dedicated",
                "dedicated_schema": "",
                "transactional_ddl": True,
            },
            "dedicated_schema must be non-empty",
        ),
        (
            {
                "scope": "temp",
                "schema": "dedicated",
                "dedicated_schema": "   ",
                "transactional_ddl": True,
            },
            "dedicated_schema must not be whitespace-only (SQL identifier)",
        ),
        (
            {
                "scope": "temp",
                "schema": "dedicated",
                "dedicated_schema": " _stage ",
                "transactional_ddl": True,
            },
            "dedicated_schema must not have leading/trailing whitespace",
        ),
        (
            {
                "scope": "temp",
                "schema": "target",
                "dedicated_schema": "_stage",
                "transactional_ddl": True,
            },
            "schema='target' forbids dedicated_schema",
        ),
        (
            {"schema": "target", "transactional_ddl": True},
            "scope is required",
        ),
        (
            {"scope": "temp", "transactional_ddl": True},
            "schema is required",
        ),
        (
            {"scope": "temp", "schema": "target"},
            "transactional_ddl is required",
        ),
        (
            {"scope": "session", "schema": "target", "transactional_ddl": True},
            "scope outside the closed enum",
        ),
        (
            {"scope": "temp", "schema": "other", "transactional_ddl": True},
            "schema outside the closed enum",
        ),
        (
            {
                "scope": "temp",
                "schema": "target",
                "transactional_ddl": True,
                "x-extra": 1,
            },
            "unknown keys are forbidden (closed contract)",
        ),
    ],
)
def test_stage_rejects(payload, why):
    with pytest.raises(ValidationError):
        SqlStageCapabilities.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        # These trip the cross-field rule that the JSON-Schema `oneOf` mirror
        # must also reject — proving model and published schema agree.
        {"scope": "temp", "schema": "dedicated", "transactional_ddl": True},
        {
            "scope": "temp",
            "schema": "target",
            "dedicated_schema": "_stage",
            "transactional_ddl": True,
        },
        {
            "scope": "temp",
            "schema": "dedicated",
            "dedicated_schema": "",
            "transactional_ddl": True,
        },
    ],
)
def test_stage_json_schema_rejects_cross_field_violations(payload):
    assert not _external_validator(SqlStageCapabilities).is_valid(payload)


@pytest.mark.parametrize(
    "dedicated_schema",
    ["   ", " _stage ", "_stage "],
)
def test_stage_whitespace_dedicated_schema_rejected_by_model_and_schema(
    dedicated_schema,
):
    # `dedicated_schema` becomes a SQL identifier, so whitespace-only and
    # edge-whitespace names are rejected (NO_EDGE_WHITESPACE_PATTERN) — and the
    # model and its published schema must agree.
    payload = {
        "scope": "temp",
        "schema": "dedicated",
        "dedicated_schema": dedicated_schema,
        "transactional_ddl": True,
    }
    with pytest.raises(ValidationError):
        SqlStageCapabilities.model_validate(payload)
    assert not _external_validator(SqlStageCapabilities).is_valid(payload)


def test_stage_target_with_explicit_null_dedicated_schema_accepted():
    # The `oneOf` mirror carries a dedicated null-branch so `target` +
    # explicit `dedicated_schema: null` is accepted (not just omission). Pin
    # both the model and the published-schema acceptance so a refactor that
    # drops that branch fails as a parity break, not silently.
    payload = {
        "scope": "temp",
        "schema": "target",
        "dedicated_schema": None,
        "transactional_ddl": True,
    }
    stage = SqlStageCapabilities.model_validate(payload)
    assert stage.dedicated_schema is None
    assert _external_validator(SqlStageCapabilities).is_valid(payload)


def test_stage_schema_uses_wire_alias_not_python_attr():
    # The alias is authoritative on the wire: the Python attribute name must not
    # leak into the accepted contract.
    with pytest.raises(ValidationError):
        SqlStageCapabilities.model_validate(
            {"scope": "temp", "schema_": "target", "transactional_ddl": True}
        )


# ---------------------------------------------------------------------------
# SqlCapabilities
# ---------------------------------------------------------------------------


def test_sql_capabilities_accepts_full_block():
    caps = SqlCapabilities.model_validate(VALID_SQL_CAPS)
    assert caps.merge_form == "merge"
    assert caps.stage.scope == "temp"


@pytest.mark.parametrize(
    "missing",
    ["catalog", "session_targeting", "merge_form", "bulk_load", "stage"],
)
def test_sql_capabilities_rejects_partial_block(missing):
    # "All five facts required inside a declared block — a partial declaration
    # is a config error, not implicit defaults."
    payload = copy.deepcopy(VALID_SQL_CAPS)
    del payload[missing]
    with pytest.raises(ValidationError):
        SqlCapabilities.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("catalog", "readonly"),
        ("session_targeting", "per_session"),
        ("merge_form", "upsert"),
        # The rc16 connector-wide scalar (a valid mechanism name, wrong shape):
        # since analitiq-engine#406 a bulk mechanism is declared per transport
        # family, so the scalar is refused, not grandfathered.
        ("bulk_load", "copy_from"),
    ],
)
def test_sql_capabilities_rejects_off_vocabulary(field, value):
    payload = copy.deepcopy(VALID_SQL_CAPS)
    payload[field] = value
    with pytest.raises(ValidationError):
        SqlCapabilities.model_validate(payload)


def test_sql_capabilities_rejects_unknown_key():
    payload = copy.deepcopy(VALID_SQL_CAPS)
    payload["x-vendor"] = True
    with pytest.raises(ValidationError):
        SqlCapabilities.model_validate(payload)


# Hand-pinned expected member sets — a deliberate restatement so a future
# NARROWING of any enum (dropping/renaming a member) fails loudly. Neither the
# off-vocabulary reject test nor `render_schemas.py check` catches a narrowing:
# a narrow-then-re-render leaves the committed schema self-consistent and green.
# This is the sanctioned "test's assertion target" copy (no-drift rule #3),
# pinning the accept boundary the way test_connector_transports.py pins the
# driver pattern verbatim.
EXPECTED_SQL_CAP_ENUMS = {
    "catalog": {"none", "read", "full"},
    "session_targeting": {"per_statement", "session_default"},
    "merge_form": {"merge", "insert_on_conflict", "insert_on_duplicate_key", "none"},
}

# Same pin for the per-transport bulk mechanisms (analitiq-engine#406:
# `BULK_MECHANISMS_BY_TRANSPORT`): each family's accept set exactly — the
# dialect-implemented mechanisms on either family, the ADBC backend's native
# `adbc_ingest` only under `adbc`, and no `"none"` member anywhere (an absent
# family is the only none; it lands via executemany).
EXPECTED_BULK_LOAD_ENUMS = {
    "sqlalchemy": {"copy_from", "load_data_local_infile", "load_job"},
    "adbc": {"adbc_ingest", "copy_from", "load_data_local_infile", "load_job"},
}


@pytest.mark.parametrize(
    ("field", "expected"), sorted(EXPECTED_SQL_CAP_ENUMS.items())
)
def test_sql_capabilities_enum_membership_is_pinned(field, expected):
    # The rendered enum must equal the pinned set exactly (catches add OR drop)...
    rendered = set(SqlCapabilities.model_json_schema()["properties"][field]["enum"])
    assert rendered == expected, f"{field} enum drifted from the pinned set"
    # ...and every pinned member must actually validate through the model.
    for member in expected:
        payload = copy.deepcopy(VALID_SQL_CAPS)
        payload[field] = member
        assert getattr(SqlCapabilities.model_validate(payload), field) == member


@pytest.mark.parametrize(
    ("family", "expected"), sorted(EXPECTED_BULK_LOAD_ENUMS.items())
)
def test_bulk_load_family_enum_membership_is_pinned(family, expected):
    # The published per-family enum must equal the pinned set exactly — and
    # carry no null branch: null is not a mechanism, so the field publishes as
    # the bare enum (`_enum_branch_only`), not `anyOf: [enum, null]`.
    rendered_field = SqlBulkLoad.model_json_schema()["properties"][family]
    assert "anyOf" not in rendered_field
    assert "default" not in rendered_field
    assert set(rendered_field["enum"]) == expected, (
        f"bulk_load.{family} enum drifted from the pinned set"
    )
    # ...and every pinned member must validate through the full block.
    for member in expected:
        payload = copy.deepcopy(VALID_SQL_CAPS)
        payload["bulk_load"] = {family: member}
        caps = SqlCapabilities.model_validate(payload)
        assert getattr(caps.bulk_load, family) == member


# ---------------------------------------------------------------------------
# SqlBulkLoad (issue #92, analitiq-engine#391/#406)
# ---------------------------------------------------------------------------
# Acceptance/refusal mirrors the engine parser
# (`cdk/sql/capabilities.py::SqlCapabilities._parse_bulk_load`) exactly, in
# both layers: the Pydantic model and the published JSON Schema.


@pytest.mark.parametrize(
    "payload",
    [
        {"sqlalchemy": "copy_from", "adbc": "adbc_ingest"},  # both families
        {"sqlalchemy": "load_data_local_infile"},  # one family alone
        {"adbc": "adbc_ingest"},
        {"adbc": "copy_from"},  # dialect mechanisms are valid on adbc too
        {},  # empty object: no bulk mechanism anywhere — legal
    ],
)
def test_bulk_load_accepts(payload):
    SqlBulkLoad.model_validate(payload)
    assert _external_validator(SqlBulkLoad).is_valid(payload)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"flight_sql": "copy_from"}, "unknown transport family is rejected"),
        (
            {"sqlalchemy": "adbc_ingest"},
            "adbc_ingest is the ADBC backend's own landing — unrunnable on "
            "the sqlalchemy family, unrepresentable in the contract",
        ),
        ({"sqlalchemy": "none"}, "the scalar era's 'none' is not a mechanism"),
        ({"adbc": "none"}, "absence of the family is the only none"),
        ({"sqlalchemy": None}, "mechanism must be a string, never null"),
        ({"adbc": None}, "mechanism must be a string, never null"),
        ({"adbc": 1}, "non-string mechanism is rejected"),
        ({"adbc": ["adbc_ingest"]}, "non-string mechanism is rejected"),
        ("copy_from", "the rc16 connector-wide scalar shape is rejected"),
    ],
)
def test_bulk_load_rejects(payload, why):
    with pytest.raises(ValidationError):
        SqlBulkLoad.model_validate(payload)
    assert not _external_validator(SqlBulkLoad).is_valid(payload)


def test_bulk_load_is_required_but_may_be_empty():
    # `bulk_load` stays a required member of a declared block (the
    # all-facts-required rule) — but as an object, where `{}` is the declared
    # "no bulk mechanism anywhere". Both attributes read back as None.
    payload = copy.deepcopy(VALID_SQL_CAPS)
    payload["bulk_load"] = {}
    caps = SqlCapabilities.model_validate(payload)
    assert caps.bulk_load.sqlalchemy is None
    assert caps.bulk_load.adbc is None


def test_bulk_load_family_set_is_pinned():
    # Exact member-set pin, symmetric to test_cap_block_members_are_pinned
    # (capability v2): a transport family mirrors the engine's
    # `SQL_TRANSPORT_TYPES` — a new family is a coordinated engine + contract
    # change, never a silent field addition here.
    schema = SqlBulkLoad.model_json_schema()
    assert set(schema["properties"]) == set(EXPECTED_BULK_LOAD_ENUMS)
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"adbc": "adbc_ingest"},
        {"sqlalchemy": "copy_from", "adbc": "adbc_ingest"},
    ],
)
def test_bulk_load_dump_round_trips(payload):
    # "Absence is the only none" holds in the emit direction too
    # (`_undeclared_families_stay_absent`): undeclared families are omitted
    # from dumps, never emitted as the explicit null the model refuses — so
    # every dump is valid input for the model AND the published schema, with
    # no `exclude_none=True` needed at the call site.
    model = SqlBulkLoad.model_validate(payload)
    dumped = model.model_dump()
    assert dumped == payload
    SqlBulkLoad.model_validate(dumped)
    json_dumped = json.loads(model.model_dump_json())
    assert json_dumped == payload
    assert _external_validator(SqlBulkLoad).is_valid(json_dumped)


def test_sql_capabilities_dump_round_trips_through_nested_bulk_load():
    # The serializer must survive composition: dumping the whole block (with
    # the wire alias for `stage.schema`) re-validates, and the nested
    # `bulk_load` comes back without null members.
    caps = SqlCapabilities.model_validate(VALID_SQL_CAPS)
    dumped = caps.model_dump(by_alias=True, exclude_none=True)
    assert dumped == VALID_SQL_CAPS
    SqlCapabilities.model_validate(dumped)


# ---------------------------------------------------------------------------
# WriteUnit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"rows": 200_000},
        {"bytes": 33_554_432},
        {"rows": 200_000, "bytes": 33_554_432},
        {"rows": 1},
        {"bytes": 1},
    ],
)
def test_write_unit_accepts(payload):
    WriteUnit.model_validate(payload)
    assert _external_validator(WriteUnit).is_valid(payload)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({}, "at least one of rows/bytes is required"),
        ({"rows": None, "bytes": None}, "explicit nulls do not count as a bound"),
        ({"rows": 0}, "rows must be >= 1"),
        ({"bytes": 0}, "bytes must be >= 1"),
        ({"rows": -5}, "rows must be >= 1"),
        ({"rows": 100, "extra": 1}, "unknown keys are forbidden"),
    ],
)
def test_write_unit_rejects(payload, why):
    with pytest.raises(ValidationError):
        WriteUnit.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [{}, {"rows": None, "bytes": None}, {"rows": 0}, {"bytes": 0}],
)
def test_write_unit_json_schema_rejects(payload):
    # The `anyOf` mirror must reject the same empty/under-bound payloads.
    assert not _external_validator(WriteUnit).is_valid(payload)


# ---------------------------------------------------------------------------
# Field wiring on the connector documents
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_example() -> dict:
    return json.loads(POSTGRES_EXAMPLE.read_text())


def test_database_connector_carries_both_blocks(db_example):
    doc = copy.deepcopy(db_example)
    doc["sql_capabilities"] = copy.deepcopy(VALID_SQL_CAPS)
    doc["write_unit"] = {"rows": 200_000, "bytes": 33_554_432}
    connector = parse_connector(doc)
    assert connector.sql_capabilities.bulk_load.sqlalchemy == "copy_from"
    assert connector.sql_capabilities.bulk_load.adbc == "adbc_ingest"
    assert connector.sql_capabilities.stage.schema_ == "target"
    assert connector.write_unit.rows == 200_000


def test_both_blocks_are_optional(db_example):
    # Omission is legal — every connector authored before #87 stays valid.
    connector = parse_connector(copy.deepcopy(db_example))
    assert connector.sql_capabilities is None
    assert connector.write_unit is None


def test_database_connector_rejects_partial_sql_capabilities(db_example):
    doc = copy.deepcopy(db_example)
    caps = copy.deepcopy(VALID_SQL_CAPS)
    del caps["stage"]
    doc["sql_capabilities"] = caps
    with pytest.raises(ValidationError):
        parse_connector(doc)


def test_database_connector_rejects_empty_write_unit(db_example):
    # The at-least-one-bound rule must fire through the whole connector, not
    # only on the isolated WriteUnit model — symmetric to the partial
    # sql_capabilities check above.
    doc = copy.deepcopy(db_example)
    doc["write_unit"] = {}
    with pytest.raises(ValidationError):
        parse_connector(doc)


def test_sql_capabilities_is_database_only(db_example):
    # `sql_capabilities` is a SQL fact; other kinds must not be able to smuggle
    # it in past the closed (`extra="forbid"`) contract. Build a minimal API
    # connector off the DB example's shared blocks and confirm rejection.
    api_doc = {
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
        "sql_capabilities": copy.deepcopy(VALID_SQL_CAPS),
    }
    with pytest.raises(ValidationError):
        parse_connector(api_doc)


def test_write_unit_is_connector_level(db_example):
    # `write_unit` is connector-level, so it is valid on a non-database kind.
    api_doc = {
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
        "write_unit": {"rows": 5_000},
    }
    connector = parse_connector(api_doc)
    assert connector.write_unit.rows == 5_000


# ---------------------------------------------------------------------------
# Published-artifact structural pin
# ---------------------------------------------------------------------------


def test_published_connector_schema_exposes_new_defs():
    """The rendered public schema must carry the new $defs and their mirrors.

    `render_schemas.py check` already pins committed-vs-rendered, but this makes
    the *presence* of the cross-field mirrors an explicit, named assertion so a
    silent regression (e.g. dropping the `json_schema_extra`) fails loudly here
    too, at the exact contract external consumers fetch.
    """
    latest = json.loads(
        (REPO_ROOT / "schemas" / "connector" / "latest.json").read_text()
    )
    defs = latest["$defs"]
    assert {
        "SqlBulkLoad",
        "SqlCapabilities",
        "SqlStageCapabilities",
        "WriteUnit",
    } <= set(defs)
    assert "oneOf" in defs["SqlStageCapabilities"]
    assert "anyOf" in defs["WriteUnit"]
    # The per-family enums publish bare (no null branch, no default) — the
    # `_enum_branch_only` mirror survived rendering.
    for family in ("sqlalchemy", "adbc"):
        assert "enum" in defs["SqlBulkLoad"]["properties"][family]
        assert "anyOf" not in defs["SqlBulkLoad"]["properties"][family]
    assert "sql_capabilities" in defs["DatabaseConnector"]["properties"]
    assert "write_unit" in defs["DatabaseConnector"]["properties"]
    # Connector-level: present on every kind that has properties.
    assert "write_unit" in defs["ApiConnector"]["properties"]
    # SQL-only: absent on non-database kinds.
    assert "sql_capabilities" not in defs["ApiConnector"]["properties"]


def test_full_connector_validates_against_published_schema(db_example):
    """End-to-end parity against the artifact a real consumer actually fetches.

    The other `_external_validator` tests exercise isolated sub-model schemas;
    a consumer validates a whole connector.json against `connector/latest.json`.
    This proves the mirrors survive composition into the full connector document
    — a valid doc passes, and each cross-field / field rule is rejected by the
    published schema exactly as the Pydantic model rejects it.
    """
    schema = json.loads(
        (REPO_ROOT / "schemas" / "connector" / "latest.json").read_text()
    )
    validator = Draft202012Validator(schema)

    valid = copy.deepcopy(db_example)
    valid["sql_capabilities"] = copy.deepcopy(VALID_SQL_CAPS)
    valid["write_unit"] = {"rows": 200_000, "bytes": 33_554_432}
    # A dedicated stage naming its schema, to exercise that branch end-to-end.
    valid["sql_capabilities"]["stage"] = {
        "scope": "real",
        "schema": "dedicated",
        "dedicated_schema": "_analitiq_stage",
        "transactional_ddl": True,
    }
    assert validator.is_valid(valid), sorted(
        e.message for e in validator.iter_errors(valid)
    )
    parse_connector(valid)  # the model agrees

    # The other legal bulk_load shapes must also survive composition:
    # single-family and the empty declares-nothing object.
    for accepted_bulk in ({}, {"adbc": "adbc_ingest"}):
        variant = copy.deepcopy(valid)
        variant["sql_capabilities"]["bulk_load"] = accepted_bulk
        assert validator.is_valid(variant), sorted(
            e.message for e in validator.iter_errors(variant)
        )
        parse_connector(variant)

    # Each `mutate` is applied to a fresh deepcopy of the valid doc, so a
    # rejection isolates to that one change. Cover every cross-field / field rule
    # end-to-end — not just one failure mode — against the composed artifact.
    def _drop_stage_name(doc):
        doc["sql_capabilities"]["stage"].pop("dedicated_schema")  # dedicated, unnamed

    def _blank_stage_name(doc):
        doc["sql_capabilities"]["stage"]["dedicated_schema"] = "   "  # field pattern

    def _empty_write_unit(doc):
        doc["write_unit"] = {}  # anyOf at-least-one-bound

    def _scalar_bulk_load(doc):
        doc["sql_capabilities"]["bulk_load"] = "copy_from"  # rc16 scalar shape

    def _unknown_bulk_family(doc):
        doc["sql_capabilities"]["bulk_load"] = {"flight_sql": "copy_from"}

    def _unrunnable_bulk_pairing(doc):
        doc["sql_capabilities"]["bulk_load"] = {"sqlalchemy": "adbc_ingest"}

    def _null_bulk_mechanism(doc):
        doc["sql_capabilities"]["bulk_load"] = {"adbc": None}

    for mutate in (
        _drop_stage_name,
        _blank_stage_name,
        _empty_write_unit,
        _scalar_bulk_load,
        _unknown_bulk_family,
        _unrunnable_bulk_pairing,
        _null_bulk_mechanism,
    ):
        broken = copy.deepcopy(valid)
        mutate(broken)
        assert not validator.is_valid(broken), broken
        with pytest.raises(ValidationError):
            parse_connector(broken)
