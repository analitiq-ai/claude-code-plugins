"""Fixture corpus for the path-free document-set API (`analitiq.validator
.document_set`).

Two corpora already committed for the path-based routes are reused here
rather than re-authored: `packages/validator/tests/corpus/` (a connector
package) and `tests/pipeline_builder/test_validate.py`'s `_build_bundle`
layout (a pipeline bundle) — both are content this suite already keeps
model-valid, so the document-set versions built from them are testing the
document-set mechanism, not guessing at contract shapes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import get_args

import pytest

from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.contracts.shared.rule_record import DOCUMENT_ARTIFACT_KINDS

CORPUS = Path(__file__).resolve().parent / "corpus"

_PLUGIN_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "plugins" / "analitiq-pipeline-builder" / "scripts"
)
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))


# ---------------------------------------------------------------------------
# Entity — drift guard against DOCUMENT_ARTIFACT_KINDS.
# ---------------------------------------------------------------------------

def test_entity_matches_document_artifact_kinds(validator):
    assert set(get_args(validator.Entity)) == set(DOCUMENT_ARTIFACT_KINDS)


def test_every_entity_has_a_registered_validator(validator):
    """`validate_document`'s explicit-kind override falls through to shape
    auto-detection, silently, when `entity` names a kind with no
    `register_entity` binding — nothing else pins `_ENTITY_VALIDATORS`'s
    registered keys to the `Entity` vocabulary, so a member landing in one and
    not the other would ship with the override quietly doing nothing for it."""
    from analitiq.validator._core import _ENTITY_VALIDATORS

    assert set(get_args(validator.Entity)) == set(_ENTITY_VALIDATORS)


# ---------------------------------------------------------------------------
# Finding — drift guard against the keys `analitiq.validator.finding` actually
# produces. `finding()`'s own docstring is the source: `rule` only when given,
# `severity` only for a `fail` kind, everything else unconditional.
# ---------------------------------------------------------------------------

def test_finding_matches_the_keys_finding_builder_produces(validator):
    from typing import get_type_hints

    from analitiq.validator.document_set import Finding

    with_rule_and_severity = validator.finding(
        rule="RULE-PKG-030", message_id="m", kind="fail", path="p", message="msg")
    without_rule_or_severity = validator.finding(
        message_id="m", kind="notApplicable", path="p", message="msg")
    possible_keys = set(with_rule_and_severity) | set(without_rule_or_severity)
    always_present = set(with_rule_and_severity) & set(without_rule_or_severity)
    hints = get_type_hints(Finding)
    # `direction` is the one key `finding()` itself never sets — only
    # `resolve_type_map_gaps` adds it, on top of a `finding()`-built dict — so
    # it is the sole declared key excluded from this pin.
    assert set(hints) - possible_keys == {"direction"}
    # Required vs. optional tracks what these two real calls actually agreed
    # on: a key both included is one `finding()` always sets; a key only one
    # included (`rule`, `severity`) is conditional — as is `direction`, which
    # neither call included at all.
    assert Finding.__required_keys__ == always_present
    assert Finding.__optional_keys__ == (possible_keys - always_present) | {"direction"}

    # `kind`: pinned directly against `finding()`'s own vocabulary constant,
    # not just against the members this test happens to try — a member
    # landing in one and not the other fails here rather than staying invisible.
    from analitiq.validator._core import _KINDS
    assert set(get_args(hints["kind"])) == set(_KINDS)
    for k in _KINDS:
        assert validator.finding(message_id="m", kind=k, path="p", message="msg")["kind"] == k
    with pytest.raises(ValueError):
        validator.finding(message_id="m", kind="not-a-real-kind", path="p", message="msg")

    # `severity`: only ever set on a `fail` finding, derived from the named
    # rule's own severity. RULE-PKG-030 is error-tier, RULE-CTOR-043 is
    # warning-tier — real records, not hand-picked strings — and RULE-CTOR-032
    # (info-tier) proves the third real severity a rule record can declare
    # never reaches a Finding's severity field at all (finding() itself
    # refuses to report an info-tier violation as `kind: fail`).
    warning_finding = validator.finding(
        rule="RULE-CTOR-043", message_id="m", kind="fail", path="p", message="msg")
    observed_severities = {with_rule_and_severity["severity"], warning_finding["severity"]}
    assert observed_severities == set(get_args(hints["severity"]))
    with pytest.raises(ValueError):
        validator.finding(rule="RULE-CTOR-032", message_id="m", kind="fail", path="p", message="msg")


# ---------------------------------------------------------------------------
# Fixtures: a connector-tree DocumentSet and a pipeline-tree DocumentSet, each
# built from content this suite already keeps model-valid.
# ---------------------------------------------------------------------------

def _connector_tree_documents(*, native="STRING", arrow="Utf8") -> dict:
    """A model-valid, coverage-clean connector package as a `DocumentSet`:
    `connector.json` (`corpus/valid_connector.json`, kind=api), a sibling read
    map covering the one native/arrow pair below, and one endpoint
    (`corpus/valid_read.json`) declaring it."""
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": native, "arrow_type": arrow},
    }
    return {
        "connector.json": connector,
        "type-map-read.json": [{"match": "exact", "native_type": native, "arrow_type": arrow}],
        "endpoints/v1__records.json": endpoint,
    }


def _uncovered_endpoint_document(*, endpoint_id="v2__widgets", request_path="/v2/widgets",
                                  native="BOOLEAN", arrow="Boolean") -> dict:
    """A model-valid endpoint declaring a native type `_connector_tree_documents`'s
    read map does not cover — a real, distinguishable `native-type-unresolved`
    finding (RULE-PKG-033). Used where a test must tell "this document was
    reached and validated" apart from "this document was never reached" — a
    clean fixture can't distinguish the two, since both look like zero
    findings — or must show more than one real finding to make an
    order-sensitive comparison non-vacuous."""
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["endpoint_id"] = endpoint_id
    endpoint["operations"]["read"]["request"]["path"] = request_path
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "b": {"type": "string", "native_type": native, "arrow_type": arrow},
    }
    return endpoint


_H = "https://schemas.analitiq.ai"
_SRC, _DST, _PID, _SID = (
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "11111111-1111-4111-8111-111111111111",
    "44444444-4444-4444-8444-444444444444",
)
_EID = derive_db_endpoint_id(None, "public", "orders")
_DBOBJ = build_database_object(None, "public", "orders")

_CONN_WISE = {
    "$schema": f"{_H}/connection/latest.json", "connection_id": _SRC, "connector_id": "wise",
    "display_name": "Wise", "parameters": {"environment": "live"},
    "secret_refs": {"api_token": "env:ANALITIQ_WISE_API_TOKEN"},
}
_CONN_PG = {
    "$schema": f"{_H}/connection/latest.json", "connection_id": _DST, "connector_id": "postgresql",
    "display_name": "Prod Postgres",
    "parameters": {"host": "db.example.com", "port": 5432, "database": "analytics", "ssl_mode": "verify-full"},
    "secret_refs": {"password": "env:ANALITIQ_POSTGRESQL_PASSWORD"},
}
_PIPELINE = {
    "$schema": f"{_H}/pipeline/latest.json", "pipeline_id": _PID, "display_name": "Wise to Postgres",
    "connections": {"source": _SRC, "destinations": [_DST]}, "streams": [_SID],
    "schedule": {"type": "manual", "timezone": "UTC"}, "status": "draft",
}
_STREAM = {
    "$schema": f"{_H}/stream/latest.json", "stream_id": _SID, "pipeline_id": _PID, "display_name": "orders",
    "source": {
        "endpoint_ref": {"scope": "connector", "connection_id": _SRC, "endpoint_id": "transfers"},
        "replication": {"method": "incremental", "cursor_field": "updated_at"},
    },
    "destinations": [{
        "endpoint_ref": {"scope": "connection", "connection_id": _DST, "endpoint_id": _EID,
                         "database_object": _DBOBJ},
        "write": {"mode": "upsert", "conflict_keys": ["id"]},
    }],
    "status": "draft",
}
_DB_ENDPOINT = {
    "$schema": f"{_H}/database-endpoint/latest.json", "endpoint_id": _EID, "display_name": "public.orders",
    "database_object": _DBOBJ,
    "columns": [
        {"name": "id", "native_type": "bigint", "arrow_type": "Int64", "nullable": False, "ordinal_position": 1},
        {"name": "updated_at", "native_type": "timestamptz", "arrow_type": "Timestamp(MICROSECOND, UTC)",
         "nullable": False, "ordinal_position": 2},
    ],
    "primary_keys": ["id"],
}
# A connection's `connector_id` must resolve among the bundle's connector
# identities for the bundle's own referential check to pass at all, so the
# equivalence fixture's embedded connectors below are fully model-valid and
# coverage-clean documents, not identity-only stand-ins: closing the
# embedded-connector coverage gap must not make `validate_pipeline_tree`
# report findings against them that the path-based route never produces.
_CONNECTOR_WISE = {
    "$schema": f"{_H}/connector/latest.json", "connector_id": "wise", "kind": "api",
    "display_name": "Wise",
    "description": "Cross-border money transfer platform.",
    "documentation_url": "https://docs.wise.com/api-docs",
    "version": "1.0.0", "default_transport": "api",
    "transports": {"api": {
        "transport_type": "http", "base_url": "https://api.wise.com",
        "headers": {"Accept": "application/json",
                    "Authorization": {"template": "Bearer ${secrets.api_token}"}},
        "timeout_seconds": 30}},
    "auth": {"type": "api_key"},
    "connection_contract": {
        "inputs": {"api_token": {
            "source": "user", "phase": "pre_auth", "storage": "secrets",
            "type": "string", "required": True, "secret": True,
            "ui": {"label": "API Token", "widget": "password", "help_text": "Wise API token."}}},
        "required_for_activation": ["secrets.api_token"]},
}
_CONNECTOR_WISE_TYPE_MAP_READ = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
_WISE_TRANSFERS_ENDPOINT = {
    "$schema": f"{_H}/api-endpoint/latest.json", "endpoint_id": "transfers",
    "operations": {"read": {
        "request": {"method": "GET", "path": "/transfers"}, "params": {},
        "response": {
            "records": {"ref": "response.body"},
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "array",
                "items": {"type": "object", "properties": {
                    "id": {"type": "string", "native_type": "STRING", "arrow_type": "Utf8"}}}}}}},
}
_CONNECTOR_PG = {
    "$schema": f"{_H}/connector/latest.json", "kind": "database", "connector_id": "postgresql",
    "display_name": "PostgreSQL",
    "description": "Relational database.",
    "version": "1.0.0", "default_transport": "database",
    "transports": {"database": {
        "transport_type": "sqlalchemy", "driver": "postgresql+psycopg",
        "dsn": {
            "kind": "url_template",
            "template": "postgresql+psycopg://{username}:{password}@{host}:{port}/{database}",
            "bindings": {
                "username": {"value": {"ref": "connection.parameters.username"}, "encoding": "url_userinfo"},
                "password": {"value": {"ref": "secrets.password"}, "encoding": "url_userinfo"},
                "host": {"value": {"ref": "connection.parameters.host"}, "encoding": "host"},
                "port": {"value": {"ref": "connection.parameters.port"}, "encoding": "raw"},
                "database": {"value": {"ref": "connection.parameters.database"}, "encoding": "url_path_segment"},
            }}}},
    "auth": {"type": "db"},
    "connection_contract": {"inputs": {
        "host": {"source": "user", "phase": "pre_auth", "storage": "connection.parameters",
                 "type": "string", "required": True},
        "port": {"source": "user", "phase": "pre_auth", "storage": "connection.parameters",
                 "type": "integer", "required": True, "default": 5432},
        "database": {"source": "user", "phase": "pre_auth", "storage": "connection.parameters",
                     "type": "string", "required": True},
        "username": {"source": "user", "phase": "auth", "storage": "connection.parameters",
                     "type": "string", "required": True},
        "password": {"source": "user", "phase": "auth", "storage": "secrets",
                     "type": "string", "required": True, "secret": True},
    }},
    "resource_discovery": {
        "strategy": "information_schema", "transport_ref": "database",
        "implementation": {"type": "builtin"},
        "produces": ["connection.endpoints", "connection.type_map"],
        "triggers": {"list_resources": "on_activation", "describe_resource": "on_resource_selected"}},
}
_CONNECTOR_PG_TYPE_MAP_READ = [{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}]
_CONNECTOR_PG_TYPE_MAP_WRITE = [
    {"match": "exact", "native_type": "bigint", "arrow_type": "Int64"},
    {"match": "regex", "native_type": "TEXT", "arrow_type": ".*"},
]
def _pipeline_core_documents() -> dict:
    """The connection, stream, pipeline, and destination-endpoint documents a
    pipeline-tree `DocumentSet` carries regardless of what its embedded
    `connectors/` subtree looks like."""
    return {
        "connections/wise/connection.json": _CONN_WISE,
        "connections/postgresql/connection.json": _CONN_PG,
        f"connections/postgresql/definition/endpoints/{_EID}.json": _DB_ENDPOINT,
        "pipelines/p/streams/orders.json": _STREAM,
        "pipelines/p/pipeline.json": _PIPELINE,
    }


def _pipeline_tree_documents() -> dict:
    """A model-valid draft pipeline bundle as a `DocumentSet`, laid out at the
    same relative paths `_assemble_bundle`
    (`plugins/analitiq-pipeline-builder/scripts/validate.py`) already resolves
    from a filesystem root — so the document-set route and that function's
    on-disk route are given byte-identical content, just supplied two
    different ways. `wise`'s and `postgresql`'s embedded
    `connectors/<slug>/definition/...` subtrees are fully model-valid and
    coverage-clean: this is the fixture the equivalence test uses, so
    resolving an embedded subtree through the package-kind registry must
    contribute no findings the path-based route doesn't already produce."""
    return {
        **_pipeline_core_documents(),
        "connectors/wise/definition/connector.json": _CONNECTOR_WISE,
        "connectors/wise/definition/type-map-read.json": _CONNECTOR_WISE_TYPE_MAP_READ,
        "connectors/wise/definition/endpoints/transfers.json": _WISE_TRANSFERS_ENDPOINT,
        "connectors/postgresql/definition/connector.json": _CONNECTOR_PG,
        "connectors/postgresql/definition/type-map-read.json": _CONNECTOR_PG_TYPE_MAP_READ,
        "connectors/postgresql/definition/type-map-write.json": _CONNECTOR_PG_TYPE_MAP_WRITE,
    }


def _pipeline_tree_documents_with_two_findings() -> dict:
    """`_pipeline_tree_documents` with two additional connection ids on the
    pipeline's own `connections.destinations` that no bundled connection
    document resolves — two real, distinguishable `connection-ref-unresolved`
    findings (one per missing id) that `validate_pipeline_bundle` reports
    identically down either route, so the byte-identical equivalence
    comparison below is checking real, order-sensitive content instead of two
    empty findings lists."""
    documents = _pipeline_tree_documents()
    pipeline = {**documents["pipelines/p/pipeline.json"]}
    pipeline["connections"] = {
        **pipeline["connections"],
        "destinations": [*pipeline["connections"]["destinations"], "missing-connection-a", "missing-connection-b"],
    }
    return {**documents, "pipelines/p/pipeline.json": pipeline}


_WISE_BALANCES_ENDPOINT = {
    **_WISE_TRANSFERS_ENDPOINT,
    "endpoint_id": "balances",
    "operations": {"read": {
        "request": {"method": "GET", "path": "/balances"}, "params": {},
        "response": {
            "records": {"ref": "response.body"},
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "array",
                "items": {"type": "object", "properties": {
                    "id": {"type": "string", "native_type": "STRING", "arrow_type": "Utf8"}}}}}}},
}


def _pipeline_tree_documents_with_extra_wise_endpoint() -> dict:
    """`_pipeline_tree_documents_with_two_findings` plus a second
    connector-scoped endpoint under `wise`'s subtree (`balances.json`), so
    excluding `transfers.json` leaves `wise`'s published-endpoint-id set
    non-empty rather than empty — proving that a connector with at least one
    still-resolvable endpoint is STILL dropped from the published sets once
    any sibling endpoint fails to resolve, not only when every endpoint under
    it does. Without this second endpoint, excluding the connector's only
    endpoint always leaves the set empty either way, which could not tell
    "any failure marks the whole connector unknown" apart from the weaker,
    wrong "only an empty set marks it unknown"."""
    documents = _pipeline_tree_documents_with_two_findings()
    return {**documents, "connectors/wise/definition/endpoints/balances.json": _WISE_BALANCES_ENDPOINT}


def _pipeline_tree_documents_with_embedded_connectors() -> dict:
    """`_pipeline_core_documents` plus `wise`'s and `postgresql`'s own
    model-valid `connector.json` (the same documents the equivalence fixture
    ships fully covered) shipped alone — no sibling type-map or `endpoints/`
    directory — so a resolver that closes the embedded-connector coverage gap
    has something real to report for each: today's plugin reads only a
    connection's connector identity (for the referential check that its
    `connector_id` is bundled) and `wise`'s endpoint ids (for stream-ref
    resolution) from an embedded `connectors/<slug>/definition/...` subtree,
    never `check_coverage`'s own findings against it. `wise`'s own
    RULE-PKG-030/035 findings prove the gap is closed, and `postgresql`'s own
    RULE-PKG-030 finding proves a crash isolated to `wise`'s subtree still
    leaves the rest of the walk checkable."""
    return {
        **_pipeline_core_documents(),
        "connectors/wise/definition/connector.json": _CONNECTOR_WISE,
        "connectors/postgresql/definition/connector.json": _CONNECTOR_PG,
    }


def _write_tree(root: Path, documents: dict) -> None:
    for rel, doc in documents.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))


# ---------------------------------------------------------------------------
# resolve_type_map_gaps: never raises, {"findings"} only.
# ---------------------------------------------------------------------------

def test_gap_resolution_reports_unreadable_map_without_raising(validator):
    result = validator.resolve_type_map_gaps(
        maps={"type-map-read.json": "not json"}, direction="read", probes=["STRING"])
    assert [f["message_id"] for f in result["findings"]] == ["type-map-unreadable"]
    assert result["findings"][0]["kind"] == "fail"
    assert result["findings"][0]["severity"] == "error"
    assert "direction" not in result["findings"][0]


def test_gap_resolution_reports_invalid_map_with_its_direction(validator):
    # Fails TypeMapReadDoc: a list, but of a rule object missing its `match`
    # discriminator (and its arrow_type).
    result = validator.resolve_type_map_gaps(
        maps={"type-map-read.json": [{"native_type": "STRING"}]}, direction="read", probes=["STRING"])
    invalid = [f for f in result["findings"] if f["message_id"] == "invalid-type-map"]
    assert len(invalid) == 1, result["findings"]
    assert invalid[0]["kind"] == "fail" and invalid[0]["severity"] == "error"
    assert invalid[0]["direction"] == "read"


def test_gap_resolution_direction_selects_the_matching_model(validator):
    # Valid under TypeMapReadDoc (native_type is a bare matcher, unvalidated for
    # placeholders) but invalid under TypeMapWriteDoc (native_type is the write
    # side's render template, and `${` with no closing `}` is malformed there) —
    # so direction alone decides which model this rule is checked against.
    rule = {"match": "exact", "native_type": "VARCHAR${", "arrow_type": "Utf8"}
    read_result = validator.resolve_type_map_gaps(
        maps={"type-map.json": [rule]}, direction="read", probes=["VARCHAR${"])
    assert not any(f["message_id"] == "invalid-type-map" for f in read_result["findings"]), read_result
    write_result = validator.resolve_type_map_gaps(
        maps={"type-map.json": [rule]}, direction="write", probes=["Utf8"])
    invalid = [f for f in write_result["findings"] if f["message_id"] == "invalid-type-map"]
    assert len(invalid) == 1, write_result
    assert invalid[0]["direction"] == "write"


def test_gap_resolution_reports_an_unresolved_probe_as_informational(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["STRING", "BIGINT"])
    gaps = [f for f in result["findings"] if f["message_id"] == "type-map-gap"]
    assert len(gaps) == 1, result["findings"]
    assert gaps[0]["kind"] == "informational" and "severity" not in gaps[0]
    assert gaps[0]["direction"] == "read"


def test_gap_resolution_fully_covered_reports_no_findings(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["STRING"])
    assert result == {"findings": []}


def test_gap_resolution_falls_through_to_a_later_map_for_a_probe_the_first_does_not_cover(validator):
    # Neither map alone covers every probe: a probe the first key's map does
    # not render must still resolve via the second key's map rather than
    # being reported as a gap just because the first key didn't cover it.
    # (`resolve_type_map_gaps` reports only findings, never a resolved value,
    # so this is the one multi-map behaviour its envelope can observe —
    # WHICH map's value wins on a genuine conflict is not visible here.)
    maps = {
        "type-map-primary.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        "type-map-fallback.json": [{"match": "exact", "native_type": "BIGINT", "arrow_type": "Int64"}],
    }
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["STRING", "BIGINT"])
    assert result == {"findings": []}, result


def test_gap_resolution_contains_a_recursion_crash_while_parsing(validator):
    """Deeply nested but syntactically valid JSON exhausts the recursion
    limit `json.loads` parses with — a crash distinct from the `ValueError`
    a malformed document raises, and one `_validate_tree_document`'s own
    parse step already isolates the same way for the tree routes."""
    deeply_nested = "[" * 20_000 + "]" * 20_000
    result = validator.resolve_type_map_gaps(
        maps={"type-map-read.json": deeply_nested}, direction="read", probes=["STRING"])
    assert [f["message_id"] for f in result["findings"]] == ["type-map-unreadable"]
    assert result["findings"][0]["kind"] == "fail" and result["findings"][0]["severity"] == "error"


def test_gap_resolution_rejects_a_canonical_filename_naming_the_wrong_direction(validator):
    """`type-map-write.json`'s rows and `type-map-read.json`'s rows share the
    same `{match, native_type, arrow_type}` keys, so a write map can also
    satisfy the read model — model validation alone cannot catch a map loaded
    under the wrong direction. The canonical filename is the one signal that
    can, mirroring `type_map_gaps.py`'s own filename/direction check."""
    maps = {"type-map-write.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["STRING"])
    assert [f["message_id"] for f in result["findings"]] == ["type-map-wrong-direction"]
    assert result["findings"][0]["direction"] == "read"
    assert not any(f["message_id"] == "type-map-gap" for f in result["findings"]), result


def test_gap_resolution_allows_a_non_canonical_filename_of_either_direction(validator):
    """The filename/direction check only fires on a load-bearing canonical
    name (`type-map-read.json`/`type-map-write.json`); a map filed under any
    other key is judged by its content alone, same as before this check
    existed."""
    maps = {"connections/pg/definition/type-map.json": [
        {"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["STRING"])
    assert result == {"findings": []}, result


def test_diagnostics_accepts_a_schema_url_direction_hint_for_an_ambiguous_type_map(validator):
    """`diagnostics()` has no `doc_path`, so `_validate_type_map`'s
    filename-based direction detection can never fire on this route; without
    a direction hint an ambiguous-direction type-map array always defaults to
    the read model, rejecting a genuinely-valid write-only rule (`arrow_type`
    holding the regex matcher, `native_type` the literal it renders to) as an
    invalid Arrow type name. `schema_url` is the same disambiguation hint
    `validate_document`'s own filesystem route already accepts."""
    write_rules = [{"match": "regex", "arrow_type": r"^Decimal128\((?<p>\d+),(?<s>\d+)\)",
                     "native_type": "NUMERIC(${p}, ${s})"}]
    defaulted = validator.diagnostics(write_rules, entity="type-map")
    assert defaulted["passed"] is False, defaulted

    told = validator.diagnostics(
        write_rules, entity="type-map",
        schema_url="https://schemas.analitiq.ai/type-map-write/latest.json")
    assert told["passed"] is True, told


def test_diagnostics_reports_the_defaulted_direction_even_with_no_filename_to_name(validator):
    """`diagnostics()`'s path-free route has no `doc_path`, so it has no
    filename for `_validate_type_map` to call ambiguous or confirm — it is
    the one caller that can reach `validate_document(doc_path=None)` at all.
    Guessing a direction there without saying so is the same silent guess the
    filesystem route already reports via `type-map-direction-defaulted`; the
    gate must widen to `doc_path is None`, not stay conditioned on a filename
    that this route never has."""
    read_rules = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
    result = validator.diagnostics(read_rules, entity="type-map")
    assert any(f["message_id"] == "type-map-direction-defaulted" for f in result["findings"]), result


# ---------------------------------------------------------------------------
# Key handling.
# ---------------------------------------------------------------------------

def test_leading_dot_slash_is_normalized_away(validator):
    with_prefix = validator.validate_tree({f"./{k}": v for k, v in _connector_tree_documents().items()})
    without_prefix = validator.validate_tree(_connector_tree_documents())
    assert with_prefix == without_prefix


@pytest.mark.parametrize("bad_key", ["/connector.json", "../connector.json", ""])
def test_invalid_keys_are_reported_not_raised(validator, bad_key):
    documents = {**_connector_tree_documents(), bad_key: {}}
    result = validator.validate_tree(documents)
    assert any(f["message_id"] == "invalid-key" and f["path"] == bad_key for f in result["findings"])
    assert result["passed"] is False


# ---------------------------------------------------------------------------
# A bundle member is validated as the entity its position already names, not
# whatever kind its shape happens to auto-detect as.
# ---------------------------------------------------------------------------

def test_connector_json_that_is_not_a_connector_is_rejected_not_misdetected(validator):
    # _PIPELINE is a fully model-valid pipeline document; handed as
    # connector.json with no entity pin, it would auto-detect as pipeline and
    # validate clean, silently skipping connector validation and coverage.
    result = validator.validate_connector_tree({"connector.json": _PIPELINE})
    assert result["passed"] is False, result


def test_pipeline_json_that_is_not_a_pipeline_is_rejected_not_misdetected(validator):
    # A valid type-map array in place of the pipeline document would
    # auto-detect as type-map and produce no blocking finding, so the whole
    # tree could pass with no pipeline ever actually validated.
    documents = {**_pipeline_tree_documents(), "pipelines/p/pipeline.json": _CONNECTOR_WISE_TYPE_MAP_READ}
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False, result


def test_stream_json_that_is_not_a_stream_is_rejected_not_misdetected(validator):
    documents = {**_pipeline_tree_documents(), "pipelines/p/streams/orders.json": _CONNECTOR_WISE_TYPE_MAP_READ}
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False, result


def test_connection_json_that_is_not_a_connection_is_rejected_not_misdetected(validator):
    documents = {**_pipeline_tree_documents(), "connections/wise/connection.json": _CONNECTOR_WISE_TYPE_MAP_READ}
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False, result


def test_model_invalid_pipeline_member_does_not_crash_the_referential_pass(validator):
    """A pipeline document that materializes, parses, and is a `dict` is still
    a bundle member even when it fails its own model validation
    (`_resolved_member`'s gate, by design, mirrors the plugin's
    `complete`/`crashed` distinction rather than re-checking model validity).
    `connections.destinations` set to a non-iterable is exactly the shape
    `validate_pipeline_bundle`'s own `_check_connection_version_conflicts`
    cannot handle (it splats `destinations` directly): the referential pass
    must report that crash as a finding, not propagate the `TypeError`."""
    documents = _pipeline_tree_documents()
    pipeline = {**documents["pipelines/p/pipeline.json"]}
    pipeline["connections"] = {**pipeline["connections"], "destinations": 5}
    documents = {**documents, "pipelines/p/pipeline.json": pipeline}

    result = validator.validate_pipeline_tree(documents)

    assert result["passed"] is False, result
    assert any(f["message_id"] == "internal-error" for f in result["findings"]), result


def test_invalid_connection_value_still_leaves_its_key_discoverable(validator):
    """A connection whose VALUE is unusable (e.g. `None`) is not the same
    defect as a connection whose KEY is unusable: the key is still a real
    path identity in this document set, so `validate_pipeline_tree`'s
    connection-slug discovery must still find it and still check its
    independently-checkable siblings — here, the legacy `type-map.json`
    filename (RULE-CONN-012), which does not depend on the connection
    document's own content at all."""
    documents = {
        **_pipeline_tree_documents(),
        "connections/wise/connection.json": None,
        "connections/wise/definition/type-map.json": "[]",
    }

    result = validator.validate_pipeline_tree(documents)

    message_ids = {f["message_id"] for f in result["findings"]}
    assert "invalid-value" in message_ids, result
    assert "legacy-type-map-filename" in message_ids, result


def test_connection_scoped_endpoint_that_is_not_a_database_endpoint_is_rejected_not_misdetected(validator):
    """A connection-scoped endpoint is always a database endpoint by its
    position in the tree — unlike an embedded connector's own endpoints,
    where api-vs-database is genuinely ambiguous and left to shape
    auto-detection. A valid type-map array in its place would otherwise
    auto-detect as type-map and produce no blocking finding."""
    documents = {
        **_pipeline_tree_documents(),
        f"connections/postgresql/definition/endpoints/{_EID}.json": _CONNECTOR_WISE_TYPE_MAP_READ,
    }
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False, result


def test_two_keys_normalizing_to_the_same_key_report_a_finding(validator):
    documents = _connector_tree_documents()
    key = next(iter(documents))
    documents[f"./{key}"] = documents[key]
    result = validator.validate_tree(documents)
    assert any(f["message_id"] == "duplicate-key" and f["path"] == key for f in result["findings"]), result
    assert result["passed"] is False


def test_non_string_key_is_reported_not_raised_by_the_sort(validator):
    """`path` is a public string field (`rules/SCHEMA.md`'s Findings shape):
    the raw key is stringified into it rather than passed through, so an
    int key doesn't produce a schema-invalid finding."""
    documents = {**_connector_tree_documents(), 1: {}}
    result = validator.validate_tree(documents)
    assert any(f["message_id"] == "invalid-key" and f["path"] == "1" for f in result["findings"]), result


def test_key_that_is_both_document_and_directory_prefix_conflicts(validator):
    documents = {**_connector_tree_documents(), "endpoints/v1__records.json/extra.json": {}}
    result = validator.validate_tree(documents)
    assert any(f["message_id"] == "key-path-conflict" for f in result["findings"])


def test_output_finding_order_is_independent_of_input_mapping_order(validator):
    # Two distinct uncovered endpoints, not the clean tree: comparing two
    # empty findings lists cannot detect order-sensitivity at all.
    documents = {
        **_connector_tree_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        "endpoints/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    forward = validator.validate_tree(documents)
    assert len(forward["findings"]) >= 2, forward  # non-vacuous: order genuinely matters below
    reversed_documents = dict(reversed(list(documents.items())))
    backward = validator.validate_tree(reversed_documents)
    assert forward == backward


def test_one_invalid_key_does_not_block_validating_the_rest(validator):
    documents = {
        **_connector_tree_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(),
        "../escape.json": {},
    }
    result = validator.validate_tree(documents)
    assert any(f["message_id"] == "invalid-key" for f in result["findings"])
    assert result["passed"] is False
    # The rest of the tree was still validated: the second endpoint's own
    # real coverage finding — which only exists if that document was reached
    # and checked — is present alongside the invalid-key finding.
    assert any(f["message_id"] == "native-type-unresolved" for f in result["findings"]), result["findings"]


# ---------------------------------------------------------------------------
# Value handling, shared by validate_tree's `documents` and
# resolve_type_map_gaps's `maps`.
# ---------------------------------------------------------------------------

def test_bytes_value_is_decoded_as_utf8_with_bom_stripped(validator):
    documents = _connector_tree_documents()
    text_result = validator.validate_tree(documents)
    as_bytes = dict(documents)
    as_bytes["connector.json"] = ("﻿" + json.dumps(documents["connector.json"])).encode("utf-8")
    bytes_result = validator.validate_tree(as_bytes)
    assert bytes_result == text_result


def test_non_str_bytes_object_value_is_invalid_not_raised(validator):
    documents = {**_connector_tree_documents(), "connector.json": 42}
    result = validator.validate_tree(documents)
    assert any(f["message_id"] == "invalid-value" and f["path"] == "connector.json"
               for f in result["findings"])
    assert result["passed"] is False


def test_invalid_value_applies_to_resolve_type_map_gaps_maps_too(validator):
    result = validator.resolve_type_map_gaps(maps={"type-map-read.json": 42}, direction="read", probes=["STRING"])
    assert any(f["message_id"] == "invalid-value" for f in result["findings"])


def test_colliding_map_keys_are_rejected_before_resolving_probes(validator):
    """`"type-map-read.json"` and `"./type-map-read.json"` normalize to one
    path, but only one map could actually exist at runtime. Each carries a
    rule the other lacks, so treating them as two independent maps would let
    their union cover every probe and report no gap — exactly the undefined-
    document hazard `_normalize_documents`'s own `duplicate-key` finding
    already guards against for the tree APIs."""
    maps = {
        "type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        "./type-map-read.json": [{"match": "exact", "native_type": "BIGINT", "arrow_type": "Int64"}],
    }
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["STRING", "BIGINT"])
    assert any(f["message_id"] == "duplicate-key" for f in result["findings"]), result
    assert not any(f["message_id"] == "type-map-gap" for f in result["findings"]), result


# ---------------------------------------------------------------------------
# Package-kind dispatch is a registry.
# ---------------------------------------------------------------------------

def test_validate_connector_tree_validates_its_own_root_shape_directly(validator):
    result = validator.validate_connector_tree(_connector_tree_documents())
    assert result["passed"] is True
    assert not any(f["message_id"] in ("ambiguous-layout", "unrecognized-layout") for f in result["findings"])


def test_validate_pipeline_tree_validates_its_own_root_shape_directly(validator):
    result = validator.validate_pipeline_tree(_pipeline_tree_documents())
    assert result["passed"] is True
    assert not any(f["message_id"] in ("ambiguous-layout", "unrecognized-layout") for f in result["findings"])


def test_validate_tree_reports_unrecognized_layout_for_neither_shape(validator):
    result = validator.validate_tree({"README.md": "not a document set at all"})
    assert result["passed"] is False
    assert any(f["message_id"] == "unrecognized-layout" for f in result["findings"])


def test_validate_tree_reports_ambiguous_layout_when_both_shapes_match(validator):
    # A pipeline tree that ALSO carries a root-level connector.json — matching
    # both the connector-tree and the pipeline-tree detector at once.
    documents = {**_pipeline_tree_documents(), **_connector_tree_documents()}
    result = validator.validate_tree(documents)
    assert result["passed"] is False
    assert any(f["message_id"] == "ambiguous-layout" for f in result["findings"])


def test_validate_tree_dispatches_a_pipeline_tree_to_pipeline_validation(validator):
    # Every other validate_tree case above exercises the connector-tree
    # detector or the fallthrough cases; this is the one case that proves the
    # registry actually dispatches a pipeline-shaped tree to pipeline
    # validation rather than, say, always matching the connector-tree
    # detector first. `_pipeline_tree_documents_with_embedded_connectors`
    # gives it a real, path-scoped finding to prove the dispatch happened,
    # the same way the connector-tree cases use `native-type-unresolved`.
    result = validator.validate_tree(_pipeline_tree_documents_with_embedded_connectors())
    assert not any(f["message_id"] in ("ambiguous-layout", "unrecognized-layout") for f in result["findings"])
    assert any(f["path"].startswith("connectors/wise/") and f["rule"] == "RULE-PKG-030"
               for f in result["findings"]), result["findings"]


def test_embedded_connector_subtree_gets_its_own_coverage_findings(validator):
    """`connectors/wise/definition/connector.json` (kind=api) ships no sibling
    type-map or `endpoints/` directory — today's plugin never notices, because
    `_connector_endpoint_sets` only reads endpoint ids for stream-ref
    resolution. `validate_pipeline_tree` must resolve this subtree through the
    same package-kind registry `validate_tree` walks and report its OWN
    coverage findings (RULE-PKG-030 missing read map, RULE-PKG-035 missing
    endpoints/), scoped under the subtree's key prefix."""
    result = validator.validate_pipeline_tree(_pipeline_tree_documents_with_embedded_connectors())
    scoped = [f for f in result["findings"] if f["path"].startswith("connectors/wise/")]
    assert any(f["rule"] == "RULE-PKG-030" for f in scoped), result["findings"]
    assert any(f["rule"] == "RULE-PKG-035" for f in scoped), result["findings"]


# ---------------------------------------------------------------------------
# Failure isolation, including the recursive embedded-package
# case. A self-referential dict is used as the crash trigger: whatever the
# implementation's internal walk turns out to be (recursive traversal,
# re-serialization for the equivalence contract, deep comparison), a cyclic
# structure is a canonical way to force it to fail rather than merely report
# an ordinary shape defect.
# ---------------------------------------------------------------------------

def _cyclic_dict() -> dict:
    node: dict = {"endpoint_id": "widgets"}
    node["self"] = node
    return node


def test_one_document_crash_is_isolated_to_its_key(validator):
    documents = {
        **_connector_tree_documents(),
        "endpoints/widgets.json": _cyclic_dict(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(),
    }
    result = validator.validate_tree(documents)
    assert result["passed"] is False
    crashed = [f for f in result["findings"] if f["path"] == "endpoints/widgets.json"]
    assert any(f["message_id"] == "internal-error" and f["kind"] == "fail" and f["severity"] == "error"
               for f in crashed), result["findings"]
    # The rest of the tree was still validated — a real, distinguishable finding
    # for the OTHER (well-formed but deliberately uncovered) endpoint is
    # present, proving it was reached rather than silently dropped once one key
    # crashed.
    assert any(f["message_id"] == "native-type-unresolved" for f in result["findings"]), result["findings"]


def test_embedded_package_crash_is_isolated_to_its_subtree_prefix(validator):
    documents = {
        **_pipeline_tree_documents_with_embedded_connectors(),
        "connectors/wise/definition/connector.json": _cyclic_dict(),
    }
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False
    assert any(f["path"].startswith("connectors/wise/") and f["message_id"] == "internal-error"
               for f in result["findings"]), result["findings"]
    # The rest of the pipeline is still reported: `postgresql`'s embedded
    # connector is itself coverage-dirty (no type-map, same as `wise`), so its
    # own real RULE-PKG-030 finding proves the walk continued past the crash
    # rather than merely not reporting on the crashed subtree twice.
    assert any(f["path"].startswith("connectors/postgresql/") and f["rule"] == "RULE-PKG-030"
               for f in result["findings"]), result["findings"]


def test_unparseable_json_text_crash_is_contained(validator):
    """Exercises `_validate_tree_document`'s OWN parse catch — distinct from
    `_normalize_documents`'s materialization catch the two crash tests above
    hit: a raw string that is not valid JSON materializes into text just
    fine, so the crash surfaces only once `fs.parsed()` is asked to turn that
    text into a document."""
    documents = {**_connector_tree_documents(), "connector.json": "{not valid json"}
    result = validator.validate_connector_tree(documents)
    assert result["passed"] is False
    # The crash finding's own path is "/" (the whole-document convention
    # `validate_document` itself uses), never `key` — `validate_connector_tree`
    # never re-roots the tree's own root document under a site, so this is the
    # finding's own final path here, not merely an intermediate one.
    assert any(f["message_id"] == "internal-error" and f["kind"] == "fail" and f["severity"] == "error"
               and f["path"] == "/" for f in result["findings"]), result["findings"]


def test_double_prefixing_is_not_reintroduced_for_a_site_scoped_crash(validator):
    """Regression for a finding built by `_validate_tree_document` and then
    re-rooted by `_at_site`: the crash's own `path` must be the whole-document
    convention (`"/"`), not the site key itself — the latter would have
    `_at_site` append the key a second time (e.g.
    `streams/orders.json/streams/orders.json`) instead of once."""
    documents = _pipeline_tree_documents()
    documents = {**documents, "pipelines/p/streams/orders.json": "{not valid json"}
    result = validator.validate_pipeline_tree(documents)
    crashed = [f for f in result["findings"] if f["message_id"] == "internal-error"]
    assert any(f["path"] == "streams/orders.json" for f in crashed), result["findings"]
    assert not any("streams/orders.json/streams/orders.json" in f["path"] for f in crashed), result["findings"]


def test_connector_tree_with_no_root_document_is_reported_not_silently_passed(validator):
    documents = {"endpoints/v1__records.json": _uncovered_endpoint_document()}
    result = validator.validate_connector_tree(documents)
    assert result["passed"] is False
    assert any(f["message_id"] == "missing-connector-document" and f["path"] == "connector.json"
               for f in result["findings"]), result["findings"]


def test_embedded_connector_subtree_with_no_root_document_is_reported(validator):
    documents = {
        **_pipeline_core_documents(),
        # A `connectors/wise/definition/...` subtree exists (an endpoints
        # file lives under it), but no connector.json.
        "connectors/wise/definition/endpoints/transfers.json": _WISE_TRANSFERS_ENDPOINT,
    }
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "missing-connector-document"
               and f["path"].startswith("connectors/wise/") for f in result["findings"]), result["findings"]


def test_a_second_pipeline_document_is_named_not_silently_ignored(validator):
    documents = {
        **_pipeline_tree_documents(),
        "pipelines/q/pipeline.json": {**_PIPELINE, "pipeline_id": _PID},
    }
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "ignored-pipeline-document" and f["path"] == "pipelines/q/pipeline.json"
               for f in result["findings"]), result["findings"]


def test_multiple_pipeline_documents_pick_the_pathlib_first_as_primary(validator):
    """Slugs "p" and "p-2" collide under plain string sort ('-' 0x2D sorts
    below '/' 0x2F): a plain `sorted()` over the raw keys would put
    "pipelines/p-2/pipeline.json" first, the opposite of what
    `_assemble_bundle` (plugins/analitiq-pipeline-builder/scripts/validate.py),
    which sorts real `Path` objects, picks for byte-identical content laid out
    on disk."""
    documents = {
        **_pipeline_tree_documents(),
        "pipelines/p-2/pipeline.json": {**_PIPELINE, "pipeline_id": _PID},
    }
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "ignored-pipeline-document" and f["path"] == "pipelines/p-2/pipeline.json"
               for f in result["findings"]), result["findings"]
    assert not any(f["message_id"] == "ignored-pipeline-document" and f["path"] == "pipelines/p/pipeline.json"
                   for f in result["findings"]), result["findings"]


def test_connection_scan_order_matches_pathlib_not_string_order(validator):
    """Connection slugs "a" and "a-b" collide the same way pipeline slugs do
    above. `_check_connection_connector_refs` (`pipelines.py`) reports one
    finding per connection at `/connections/{i}/connector_id`, so a scan order
    that disagrees with `_assemble_bundle`'s `Path`-sorted order reports the
    same defect at a different index — an equivalence break the two
    promised-equivalent routes must not have."""
    documents = {
        **_pipeline_tree_documents(),
        "connections/a/connection.json": {
            "$schema": f"{_H}/connection/latest.json", "connection_id": "55555555-5555-4555-8555-555555555555",
            "connector_id": "ghost", "display_name": "A", "parameters": {}, "secret_refs": {},
        },
        "connections/a-b/connection.json": {
            "$schema": f"{_H}/connection/latest.json", "connection_id": "66666666-6666-4666-8666-666666666666",
            "connector_id": "ghost", "display_name": "A-B", "parameters": {}, "secret_refs": {},
        },
    }
    result = validator.validate_pipeline_tree(documents)
    refs = {f["path"]: f["message"] for f in result["findings"] if f["message_id"] == "connector-ref-unresolved"}
    assert "55555555-5555-4555-8555-555555555555" in refs["/connections/0/connector_id"], refs
    assert "66666666-6666-4666-8666-666666666666" in refs["/connections/1/connector_id"], refs


def test_a_document_that_fails_to_parse_skips_the_referential_pass(validator):
    """Item: a document that fails to parse must not be silently dropped from
    the bundle `validate_pipeline_bundle` grades — that would let a
    spurious `*-ref-unresolved` finding fire against a sibling that was
    actually fine, mirroring `plugins/analitiq-pipeline-builder/scripts/
    validate.py`'s own `complete`/`crashed` gate."""
    documents = _pipeline_tree_documents()
    documents = {**documents, "connections/postgresql/connection.json": "{not valid json"}
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "internal-error" for f in result["findings"]), result["findings"]
    # The postgresql connection failed to parse and dropped out of the
    # bundle, so the pipeline's own destination reference to it would
    # (wrongly) look unresolved to validate_pipeline_bundle's own referential
    # pass were it still run against a bundle this incomplete.
    assert not any(f.get("message_id") == "connection-ref-unresolved" for f in result["findings"]), \
        result["findings"]


def test_a_document_that_never_materializes_also_skips_the_referential_pass(validator):
    """The other of `_VirtualFS`'s two crash layers: a key whose own value
    could not even be turned into JSON text (a cyclic structure) never reaches
    `_validate_tree_document` at all — `_normalize_documents` reports it
    directly. That must cost the bundle the same completeness gate a
    parse-time crash does, since either way the connection dropped out of the
    bundle just the same."""
    cyclic: dict = {}
    cyclic["self"] = cyclic
    documents = _pipeline_tree_documents()
    documents = {**documents, "connections/postgresql/connection.json": cyclic}
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "internal-error" for f in result["findings"]), result["findings"]
    assert not any(f.get("message_id") == "connection-ref-unresolved" for f in result["findings"]), \
        result["findings"]


# ---------------------------------------------------------------------------
# The crash-isolation matrix: every bundle member `_resolved_member` gates
# (pipeline, stream, connection, connection-scoped endpoint, embedded
# connector identity, connector-scoped endpoint id) against every way it can
# fail to resolve (missing key, materialization crash, parse crash, wrong
# shape). One mechanism gates all six member kinds, so this is one
# parametrized test rather than N hand-written per-case tests — reverting any
# one piece of that mechanism should turn most of this matrix red at once,
# not just the cell that names the piece.
# ---------------------------------------------------------------------------

def _drop(documents: dict, key: str) -> dict:
    return {k: v for k, v in documents.items() if k != key}


def _crash_materialization(documents: dict, key: str) -> dict:
    return {**documents, key: _cyclic_dict()}


def _crash_parse(documents: dict, key: str) -> dict:
    return {**documents, key: "{not valid json"}


def _wrong_shape(documents: dict, key: str) -> dict:
    # Valid JSON, wrong Python shape: every member this matrix covers is
    # expected to be an object, so a list is "parses fine, still excluded".
    return {**documents, key: []}


_MATRIX_MUTATORS = {
    "missing": _drop,
    "materialization-crash": _crash_materialization,
    "parse-crash": _crash_parse,
    "wrong-shape": _wrong_shape,
}

# Five of the six member kinds are gated by `bundle_is_incomplete`: excluding
# one for any reason skips `validate_pipeline_bundle`'s whole referential pass
# for this call, which is observed here by the two `connection-ref-unresolved`
# findings `_pipeline_tree_documents_with_two_findings` otherwise always
# produces (for its two permanently-unresolvable injected connection ids)
# going missing along with everything else.
#
# `missing` is absent for pipeline/stream/connection/connection-endpoint:
# each of those is DISCOVERED by the presence of its own key
# (`pipelines/<slug>/pipeline.json`, `pipelines/<slug>/streams/*.json`,
# `connections/<slug>/connection.json`,
# `connections/<slug>/definition/endpoints/*.json`) — a key that was never
# there is never discovered at all, so there is nothing for `_resolved_member`
# to exclude. That is a structurally different case from a key that IS
# discovered and then fails to resolve, and it is not silently skipped here:
# see the four `test_missing_*_is_not_an_exclusion...` tests below, which
# pin what actually happens instead (each is independently reachable through
# a referential check that already exists for a different reason).
#
# `connector-identity` is the one member of these five discovered by its
# SUBTREE's presence (any file under `connectors/<slug>/definition/`,
# independent of `connector.json` itself), so a missing `connector.json`
# alongside an otherwise-present subtree IS reachable through this gate.
_INCOMPLETENESS_CELLS = {
    "pipeline": ("pipelines/p/pipeline.json",
                 ("materialization-crash", "parse-crash", "wrong-shape")),
    "stream": ("pipelines/p/streams/orders.json",
               ("materialization-crash", "parse-crash", "wrong-shape")),
    "connection": ("connections/postgresql/connection.json",
                   ("materialization-crash", "parse-crash", "wrong-shape")),
    "connection-endpoint": (f"connections/postgresql/definition/endpoints/{_EID}.json",
                            ("materialization-crash", "parse-crash", "wrong-shape")),
    "connector-identity": ("connectors/wise/definition/connector.json",
                           ("missing", "materialization-crash", "parse-crash", "wrong-shape")),
}


@pytest.mark.parametrize(
    "member_kind,failure_mode",
    [(kind, mode) for kind, (_, modes) in _INCOMPLETENESS_CELLS.items() for mode in modes],
)
def test_excluded_bundle_member_marks_the_bundle_incomplete(validator, member_kind, failure_mode):
    key, _ = _INCOMPLETENESS_CELLS[member_kind]
    documents = _MATRIX_MUTATORS[failure_mode](_pipeline_tree_documents_with_two_findings(), key)
    result = validator.validate_pipeline_tree(documents)
    assert not any(
        f.get("message_id") == "connection-ref-unresolved" and "missing-connection" in f.get("message", "")
        for f in result["findings"]), result["findings"]
    if member_kind == "pipeline":
        # A crashed (not merely absent) pipeline document is `bundle_is_incomplete`'s
        # OWN job to catch: `bundle["pipeline"]` reaches `validate_pipeline_bundle`
        # as `None` either way, so without this flag skipping the call entirely,
        # that function's `bundle-missing-pipeline-document` framework check would
        # fire in its place — a real, observable finding this flag exists to
        # prevent, not just the two-findings-absent signal every other kind shares.
        assert not any(f["message_id"] == "bundle-missing-pipeline-document" for f in result["findings"]), \
            result["findings"]


@pytest.mark.parametrize("failure_mode,connector_endpoint_ref_should_warn", [
    ("missing", True),
    ("materialization-crash", False),
    ("parse-crash", False),
    ("wrong-shape", False),
])
def test_excluded_connector_scoped_endpoint_marks_the_whole_connector_unknown(
        validator, failure_mode, connector_endpoint_ref_should_warn):
    """The sixth member kind is not gated by `bundle_is_incomplete` at all —
    excluding a connector-scoped endpoint file drops the ENTIRE connector
    from `_connector_endpoint_sets`'s published sets, not just that one id,
    even though `wise` still publishes a perfectly good `balances` endpoint
    alongside the excluded `transfers` one: a partial set is exactly as
    unreliable an answer to "does this connector publish `transfers`" as no
    set at all, since an id it doesn't yet know about could be the very one
    a ref names. `_connector_endpoint_ref_findings` treats an omitted
    connector as *unknown* and skips its refs (its own docstring), so
    RULE-STRM-043 (`connector-endpoint-ref-unresolved`) does not fire against
    the stream's `transfers` ref here — a warning would be a false positive
    against a ref the author wrote correctly.

    `missing` is NOT one of the three modes this applies to, and is the
    exception the parametrization carries rather than silently sharing the
    other three's expectation: a `transfers.json` that was never authored at
    all is never discovered by `known_json_children` in the first place, so
    there is nothing for `_resolved_member` to exclude — `wise`'s set is
    still recorded, just without a `transfers` id, and RULE-STRM-043 fires
    exactly as it would for any other genuinely-unpublished endpoint id,
    because no crashed content exists that COULD have published it. This is
    the same missing-vs-excluded distinction the five bundle-member kinds
    above draw, extended to this sixth one."""
    key = "connectors/wise/definition/endpoints/transfers.json"
    documents = _MATRIX_MUTATORS[failure_mode](_pipeline_tree_documents_with_extra_wise_endpoint(), key)
    result = validator.validate_pipeline_tree(documents)
    fired = any(
        f.get("rule") == "RULE-STRM-043" and f["message_id"] == "connector-endpoint-ref-unresolved"
        for f in result["findings"])
    assert fired == connector_endpoint_ref_should_warn, result["findings"]


@pytest.mark.parametrize("failure_mode,expected_internal_errors", [
    ("missing", 0),
    ("materialization-crash", 1),
    ("parse-crash", 1),
    ("wrong-shape", 0),
])
def test_excluded_connector_scoped_endpoint_crash_is_reported_exactly_once(
        validator, failure_mode, expected_internal_errors):
    """`_connector_endpoint_sets` is the only walk that ever reads a
    connector-scoped endpoint file for a `database`/`storage`-kind connector
    (`check_coverage` never routes through `endpoints/` for those kinds), so
    its own materialize/parse gate must report a genuine parse crash exactly
    once and must NOT re-report a materialization crash `_normalize_documents`
    already named — both are `_resolved_member`'s job, and a shape defect or
    an absent file (never a crash) reports neither."""
    key = f"connectors/postgresql/definition/endpoints/{_EID}.json"
    documents = _MATRIX_MUTATORS[failure_mode](_pipeline_tree_documents(), key)
    result = validator.validate_pipeline_tree(documents)
    hits = [f for f in result["findings"] if f["message_id"] == "internal-error" and f["path"] == key]
    assert len(hits) == expected_internal_errors, result["findings"]


def test_connector_scoped_endpoint_is_not_independently_shape_validated(validator):
    """`_connector_endpoint_sets` reads a connector-scoped endpoint file only
    to check whether it resolves to a `dict` an id can be read off —
    `validate=False` is what keeps it from ALSO running full model validation
    (shape auto-detection included) over that same document a second time.
    Without it, a document this specific shape's own dedicated check already
    covers (via `check_coverage`'s sibling-endpoint walk, run once through the
    connector's own `validate_connector_tree` resolution) would additionally
    be graded as a standalone document of unknown kind — reporting
    `unrecognized-document` for a document that is not unrecognized, merely
    incomplete in a way its own dedicated check already names."""
    documents = _pipeline_tree_documents()
    broken_endpoint = {**_WISE_TRANSFERS_ENDPOINT}
    del broken_endpoint["operations"]
    documents = {**documents, "connectors/wise/definition/endpoints/transfers.json": broken_endpoint}
    result = validator.validate_pipeline_tree(documents)
    assert not any(f["message_id"] == "unrecognized-document" for f in result["findings"]), result["findings"]
    # The endpoint's one real defect (a missing `operations`) is still
    # reported — by `check_coverage`'s own walk, not a second time by this one.
    assert any(f["message_id"] == "missing" for f in result["findings"]), result["findings"]


def test_a_crashed_connection_still_leaves_its_sibling_type_map_checked(validator):
    """`conn_slug` is derived from the connection document's own KEY, never its
    content, so a connection that fails to resolve does not also block its
    sibling scoped type map from being independently checked — RULE-CONN-012's
    legacy-filename rejection still fires for the crashed connection's own
    sibling, proving the sibling scan is not skipped just because the
    connection it sits beside excluded itself."""
    documents = _pipeline_tree_documents()
    documents = {
        **documents,
        "connections/postgresql/connection.json": _cyclic_dict(),
        "connections/postgresql/definition/type-map.json": _CONNECTOR_PG_TYPE_MAP_READ,
    }
    result = validator.validate_pipeline_tree(documents)
    assert any(
        f.get("rule") == "RULE-CONN-012" and f["message_id"] == "legacy-type-map-filename"
        and f["path"] == "connections/postgresql/definition/type-map.json"
        for f in result["findings"]), result["findings"]


def test_missing_pipeline_document_is_not_an_exclusion(validator):
    """pipeline x missing is the one cell of the six-kind matrix
    `_resolved_member`'s own gate cannot reach: with zero
    `pipelines/<slug>/pipeline.json` keys at all, `validate_pipeline_tree`'s
    `if pipeline_keys:` guard is never entered, so `_resolved_member` is never
    called for a pipeline document and `bundle_is_incomplete` stays False —
    `bundle["pipeline"]` reaches `validate_pipeline_bundle` as `None`, and
    that function's own framework-level `bundle-missing-pipeline-document`
    check (fired before any registered referential rule runs) reports it
    instead, short-circuiting the referential pass by itself."""
    documents = _drop(_pipeline_tree_documents_with_two_findings(), "pipelines/p/pipeline.json")
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "bundle-missing-pipeline-document" for f in result["findings"]), \
        result["findings"]
    assert not any(
        f.get("message_id") == "connection-ref-unresolved" and "missing-connection" in f.get("message", "")
        for f in result["findings"]), result["findings"]


def test_missing_stream_document_is_not_an_exclusion(validator):
    """A `pipelines/<slug>/streams/*.json` key that was never there is never
    discovered by `fs.known_json_children`, so it is not something
    `_resolved_member` excludes — `bundle_is_incomplete` stays False, the
    referential pass runs, and the stream's own absence surfaces the ordinary
    way: RULE-PIPE-011's `stream-ref-unresolved` against the pipeline's own
    `streams` list, alongside the always-otherwise-firing injected findings,
    which still fire because the pass was never skipped."""
    documents = _drop(_pipeline_tree_documents_with_two_findings(), "pipelines/p/streams/orders.json")
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "stream-ref-unresolved" for f in result["findings"]), result["findings"]
    assert any(
        f.get("message_id") == "connection-ref-unresolved" and "missing-connection" in f.get("message", "")
        for f in result["findings"]), result["findings"]


def test_missing_connection_document_is_not_an_exclusion(validator):
    """A `connections/<slug>/connection.json` key that was never there is
    never iterated by the connections loop at all — not just the connection
    itself but its sibling scoped endpoint is never discovered either, since
    both are located from that same key's presence (never its content). The
    referential pass still runs (`bundle_is_incomplete` stays False): the
    missing connection surfaces as its stream's now-unresolvable destination
    `endpoint_ref` (RULE-STRM-034's `endpoint-ref-unresolved`, since the
    connection-scoped endpoint the ref names was never discovered either),
    alongside the always-otherwise-firing injected findings."""
    documents = _drop(_pipeline_tree_documents_with_two_findings(), "connections/postgresql/connection.json")
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "endpoint-ref-unresolved" for f in result["findings"]), result["findings"]
    assert any(
        f.get("message_id") == "connection-ref-unresolved" and "missing-connection" in f.get("message", "")
        for f in result["findings"]), result["findings"]


def test_missing_connection_endpoint_document_is_not_an_exclusion(validator):
    """A connection-scoped `endpoints/*.json` key that was never there is
    never discovered by `fs.known_json_children`, so — unlike a materialization
    crash, parse crash, or wrong-shaped value at that same key — there is
    nothing for `_resolved_member` to exclude and `bundle_is_incomplete` stays
    False. The referential pass still runs, and the missing endpoint surfaces
    as the stream's destination `endpoint_ref` failing to resolve against the
    bundle's (now one entry short) `endpoints` list."""
    documents = _drop(
        _pipeline_tree_documents_with_two_findings(),
        f"connections/postgresql/definition/endpoints/{_EID}.json")
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "endpoint-ref-unresolved" for f in result["findings"]), result["findings"]
    assert any(
        f.get("message_id") == "connection-ref-unresolved" and "missing-connection" in f.get("message", "")
        for f in result["findings"]), result["findings"]


# ---------------------------------------------------------------------------
# Entity override — end-to-end: validate_document(entity=...) dispatches
# straight to the named kind, bypassing shape auto-detection; diagnostics on a
# DocumentSet never consults it.
# ---------------------------------------------------------------------------

def test_validate_document_entity_override_bypasses_shape_auto_detection(validator):
    doc: dict = {}
    auto = validator.validate_document(doc)
    assert any(f["message_id"] == "unrecognized-document" for f in auto), auto

    forced = validator.validate_document(doc, entity="connection")
    assert not any(f["message_id"] == "unrecognized-document" for f in forced), forced
    assert forced, forced  # the connection model still rejects an empty document


def test_diagnostics_entity_override_reaches_only_the_single_document_route(validator):
    # A non-mapping value, not `{}` — an empty dict is itself a (trivially
    # valid) empty DocumentSet, so `diagnostics` would route it to
    # `validate_tree` rather than `validate_document`, never reaching the
    # shape auto-detection this test means to bypass.
    doc = "not a document"
    without_entity = validator.diagnostics(doc)
    with_entity = validator.diagnostics(doc, entity="connection")
    assert any(f["message_id"] == "unrecognized-document" for f in without_entity["findings"])
    assert not any(f["message_id"] == "unrecognized-document" for f in with_entity["findings"])

    documents = _connector_tree_documents()
    without_entity_set = validator.validate_tree(documents)
    with_entity_set = validator.diagnostics(documents, entity="connection")
    assert json.dumps(with_entity_set) == json.dumps(without_entity_set)


# ---------------------------------------------------------------------------
# RULE-CONN-012 / RULE-STRM-043 — the finding-producing branches every
# existing pipeline-tree fixture leaves dead (no connection ever ships a
# scoped type map; no connector-scoped endpoint_ref is ever misaligned).
# ---------------------------------------------------------------------------

def test_connection_scoped_legacy_type_map_filename_is_rejected(validator):
    documents = {
        **_pipeline_tree_documents(),
        "connections/wise/definition/type-map.json": _CONNECTOR_WISE_TYPE_MAP_READ,
    }
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False
    assert any(
        f["rule"] == "RULE-CONN-012" and f["message_id"] == "legacy-type-map-filename"
        and f["path"] == "connections/wise/definition/type-map.json"
        for f in result["findings"]), result["findings"]


def test_connection_scoped_type_map_dict_is_rejected_not_auto_detected_as_something_else(validator):
    """A dict under a connection's type-map-read.json must fail against the
    type-map model specifically — `entity="type-map"` is what forces it
    through that model instead of shape auto-detection. Without the
    override, a dict this malformed still fails (shape auto-detection
    matches no registered kind and reports `unrecognized-document`), so
    `result["passed"] is False` alone cannot tell the override was applied —
    only the finding's own `message_id` can: the type-map model's own
    rejection (`list_type`, for a dict where the model requires a list),
    never the auto-detection fallback."""
    documents = {
        **_pipeline_tree_documents(),
        "connections/wise/definition/type-map-read.json": {"not": "a list"},
    }
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False
    site = "connections/wise/definition/type-map-read.json"
    scoped = [f for f in result["findings"] if f["path"].startswith(site)]
    assert any(f["message_id"] == "list_type" for f in scoped), result["findings"]
    assert not any(f["message_id"] == "unrecognized-document" for f in scoped), result["findings"]


def test_connector_scoped_endpoint_ref_naming_an_unpublished_endpoint_warns(validator):
    documents = _pipeline_tree_documents()
    stream = {**documents["pipelines/p/streams/orders.json"]}
    stream["source"] = {
        **stream["source"],
        "endpoint_ref": {**stream["source"]["endpoint_ref"], "endpoint_id": "not-a-real-endpoint"},
    }
    documents = {**documents, "pipelines/p/streams/orders.json": stream}
    result = validator.validate_pipeline_tree(documents)
    assert any(
        f["rule"] == "RULE-STRM-043" and f["message_id"] == "connector-endpoint-ref-unresolved"
        for f in result["findings"]), result["findings"]


def test_embedded_connector_endpoint_that_fails_to_parse_is_reported(validator):
    """Item: `check_coverage` never reads `endpoints/*.json` for a
    `database`/`storage`-kind connector, so `_connector_endpoint_sets`'s own
    scan is the only walk that ever reads this file for `postgresql` (a
    database-kind connector here) — a crash there must not be silently
    swallowed."""
    documents = {
        **_pipeline_tree_documents(),
        "connectors/postgresql/definition/endpoints/broken.json": "{not valid json",
    }
    result = validator.validate_pipeline_tree(documents)
    assert any(
        f["message_id"] == "internal-error"
        and f["path"] == "connectors/postgresql/definition/endpoints/broken.json"
        for f in result["findings"]), result["findings"]


def test_pipeline_tree_equivalence_covers_connection_type_maps_and_endpoint_refs(validator, tmp_path):
    """Extends the path-based/document-set equivalence coverage to
    RULE-CONN-012's legacy-filename branch and RULE-STRM-043's
    finding-emitting tail — both are locally reimplemented on each route (the
    plugin's own `_finding` shape, not `analitiq.validator.finding()`), so
    this checks the two routes agree on WHICH rule fires and WHERE, not a
    byte-identical envelope the way the two shared-code equivalence tests
    above do."""
    import validate as pipeline_adapter  # plugins/analitiq-pipeline-builder/scripts/validate.py

    documents = _pipeline_tree_documents()
    documents = {**documents, "connections/wise/definition/type-map.json": _CONNECTOR_WISE_TYPE_MAP_READ}
    stream = {**documents["pipelines/p/streams/orders.json"]}
    stream["source"] = {
        **stream["source"],
        "endpoint_ref": {**stream["source"]["endpoint_ref"], "endpoint_id": "not-a-real-endpoint"},
    }
    documents["pipelines/p/streams/orders.json"] = stream

    _write_tree(tmp_path, documents)
    path_based = pipeline_adapter.diagnostics_for(
        "pipeline", tmp_path / "pipelines" / "p" / "pipeline.json", bundle_root=tmp_path)
    tree_based = validator.validate_pipeline_tree(documents)

    def _sites(findings, needle):
        return {f["path"] for f in findings if needle in json.dumps(f)}

    assert _sites(path_based["findings"], "type-map.json"), path_based
    assert _sites(path_based["findings"], "not-a-real-endpoint"), path_based
    assert _sites(tree_based["findings"], "type-map.json") == _sites(path_based["findings"], "type-map.json")
    assert (_sites(tree_based["findings"], "not-a-real-endpoint")
            == _sites(path_based["findings"], "not-a-real-endpoint"))


# ---------------------------------------------------------------------------
# diagnostics — auto-detects single-document vs. document-set input and
# dispatches to validate_document / validate_tree accordingly, wrapping
# either result in one ValidationEnvelope shape.
# ---------------------------------------------------------------------------

def test_diagnostics_dispatches_a_single_document_to_validate_document(validator):
    document = json.loads((CORPUS / "valid_connector.json").read_text())
    expected_findings = validator.validate_document(document)
    expected = {"passed": not any(validator.finding_costs_a_pass(f) for f in expected_findings),
                "findings": expected_findings}
    assert json.dumps(validator.diagnostics(document)) == json.dumps(expected)


def test_diagnostics_dispatches_a_document_set_to_validate_tree(validator):
    documents = {
        **_connector_tree_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(),
    }
    assert json.dumps(validator.diagnostics(documents)) == json.dumps(validator.validate_tree(documents))


def test_a_document_set_key_named_like_a_document_field_is_not_misdetected_as_that_document(validator):
    """A document set's keys are not restricted to `.json` names, so a
    connector tree that happens to also carry a root-level key literally
    named `"connections"` must still be recognized as the document set it is:
    `pipelines.is_pipeline_doc` claims any mapping merely because it has a
    `"connections"` key and no `"pipeline"` key, which the whole document set
    mapping itself satisfies once such a key is present. A path-shaped sibling
    key (`connector.json` here) must mark the mapping as a document set first."""
    documents = {**_connector_tree_documents(), "connections": {"anything": "goes"}}
    result = validator.diagnostics(documents)
    assert result == validator.validate_tree(documents)
    assert result["passed"] is True, result  # the base fixture is coverage-clean


def test_a_rootless_document_set_with_a_field_named_key_is_not_misdetected_either(validator):
    """The same misdetection can happen with no tree root present at all: a
    document set carrying only a `connections/pg/connection.json` member and a
    root-level `"connections"` key matches neither the connector-package root
    shape nor the pipeline-bundle root shape, so a check for those two roots
    alone would say "not a document set" and let `pipelines.is_pipeline_doc`
    claim the whole mapping — same defect, without needing a `connector.json`
    to reach it. It must still route through `validate_tree` and land on
    `unrecognized-layout`, not on a pipeline-document verdict."""
    documents = {
        "connections/pg/connection.json": {
            "$schema": f"{_H}/connection/latest.json",
            "connection_id": "77777777-7777-4777-8777-777777777777",
            "connector_id": "pg", "display_name": "PG", "parameters": {}, "secret_refs": {},
        },
        "connections": {"anything": "goes"},
    }
    result = validator.diagnostics(documents)
    assert result == validator.validate_tree(documents)
    assert "unrecognized-layout" in [f["message_id"] for f in result["findings"]], result


# ---------------------------------------------------------------------------
# Acceptance — equivalence: the path-based route and the document-set route
# produce byte-identical results for the same content, per package kind.
# ---------------------------------------------------------------------------

def test_connector_tree_equivalence_with_the_path_based_route(validator, tmp_path):
    # Two distinct uncovered endpoints, not just a clean tree: a route
    # producing zero findings would make "findings order included" vacuous.
    documents = {
        **_connector_tree_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        "endpoints/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    _write_tree(tmp_path, documents)
    path_based = validator.validate_document(documents["connector.json"], doc_path=tmp_path / "connector.json")
    assert len(path_based) >= 2, path_based  # non-vacuous: order genuinely matters below
    tree_based = validator.validate_connector_tree(documents)
    expected = {"passed": not any(validator.finding_costs_a_pass(f) for f in path_based),
                "findings": path_based}
    assert json.dumps(tree_based) == json.dumps(expected)


def test_pipeline_tree_equivalence_with_the_path_based_route(validator, tmp_path):
    import validate as pipeline_adapter  # plugins/analitiq-pipeline-builder/scripts/validate.py

    documents = _pipeline_tree_documents_with_two_findings()
    _write_tree(tmp_path, documents)
    path_based = pipeline_adapter.diagnostics_for(
        "pipeline", tmp_path / "pipelines" / "p" / "pipeline.json", bundle_root=tmp_path)
    assert len(path_based["findings"]) >= 2, path_based  # non-vacuous: order genuinely matters below
    tree_based = validator.validate_pipeline_tree(documents)
    assert json.dumps(tree_based) == json.dumps(path_based)


# ---------------------------------------------------------------------------
# _extract_subtree: a raw-key collision on the same subtree-relative key
# resolves deterministically, regardless of the caller's insertion order.
# ---------------------------------------------------------------------------

def test_extract_subtree_resolves_a_raw_key_collision_by_sorted_order_not_insertion_order(validator):
    """`_normalize_key` only strips a LEADING `./`, so two raw keys that both
    normalize to the same subtree-relative key (a `./`-prefixed duplicate of
    the whole path, here) must resolve to the same value regardless of which
    one the caller happened to insert first — the same sorted-key-order-wins
    rule `_normalize_documents` already applies at the whole tree's own top
    level, per this function's own docstring."""
    from analitiq.validator.document_set import _extract_subtree

    forward = {
        "connectors/wise/definition/connector.json": {"which": "unprefixed"},
        "./connectors/wise/definition/connector.json": {"which": "dot-slash-prefixed"},
    }
    backward = dict(reversed(list(forward.items())))

    forward_subtree = _extract_subtree(forward, "connectors/wise/definition/")
    backward_subtree = _extract_subtree(backward, "connectors/wise/definition/")

    # "./connectors/..." sorts before "connectors/..." in plain string order
    # ("." < "c"), so it is the raw key `sorted(documents, key=str)` visits
    # first — the one `setdefault` keeps.
    assert forward_subtree == backward_subtree == {
        "connector.json": {"which": "dot-slash-prefixed"},
    }


# ---------------------------------------------------------------------------
# resolve_type_map_gaps: probe deduplication and probe/probes validation.
# ---------------------------------------------------------------------------

def test_gap_resolution_deduplicates_repeated_probes(validator):
    """A repeated probe is one verdict, not one `type-map-gap` finding per
    occurrence — mirroring the outcome `type_map_gaps.py`'s own `resolve`
    already gives its CLI probes."""
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["BIGINT", "BIGINT", "BIGINT"])
    gaps = [f for f in result["findings"] if f["message_id"] == "type-map-gap"]
    assert len(gaps) == 1, result["findings"]


def test_gap_resolution_deduplicates_a_repeated_invalid_probe_too(validator):
    """De-duplication runs before the per-probe string check, not after: a
    repeated non-string probe is one `invalid-probe` finding, not one per
    occurrence — the same "one verdict per probe" rule as a repeated valid
    one, and it is the harder half to get right, since the natural
    implementation is to validate probes into a `set` as they're seen, which
    itself crashes on an unhashable probe (a `list`, say) rather than
    reporting it."""
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=[None, None, ["not", "hashable"]])
    assert [f["message_id"] for f in result["findings"]] == ["invalid-probe", "invalid-probe"], result["findings"]


def test_gap_resolution_reports_a_non_string_probe_on_the_read_route_instead_of_crashing(validator):
    """The read route's `_render_arrow_type` normalizes a probe by calling
    string methods on it directly (`.strip()`), so a non-string probe would
    otherwise crash this function rather than being reported, breaking the
    "never raises" contract `resolve_type_map_gaps` documents for itself."""
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["STRING", None])
    assert [f["message_id"] for f in result["findings"]] == ["invalid-probe"], result["findings"]
    invalid = result["findings"][0]
    assert invalid["kind"] == "fail" and invalid["severity"] == "error"
    assert invalid["direction"] == "read"
    assert "native-type" in invalid["message"]


def test_gap_resolution_reports_a_non_string_probe_on_the_write_route_too(validator):
    """The write route's `_first_match_render` matches a probe against each
    rule's `arrow_type` with no string normalization at all, so a non-string
    probe does not crash there — pre-fix, it was silently graded resolved or
    gapped by whatever comparison its type happened to support instead, a
    wrong-answer failure mode rather than a crash, but the same underlying
    defect: this route must report it exactly like the read route does,
    naming the write-side vocabulary (Arrow types, not native types) in the
    message."""
    maps = {"type-map-write.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="write", probes=["Utf8", None])
    assert [f["message_id"] for f in result["findings"]] == ["invalid-probe"], result["findings"]
    invalid = result["findings"][0]
    assert invalid["direction"] == "write"
    assert "Arrow-type" in invalid["message"]


def test_gap_resolution_reports_a_non_list_probes_instead_of_raising(validator):
    """`probes` itself, not one of its elements, can be the wrong shape: a
    bare string iterates character by character rather than probe by probe,
    and `None` is not iterable at all — both are the same "never raises"
    contract the per-element `invalid-probe` check exists for, one level up."""
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}

    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=None)
    assert [f["message_id"] for f in result["findings"]] == ["invalid-probes"], result["findings"]

    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes="STRING")
    assert [f["message_id"] for f in result["findings"]] == ["invalid-probes"], result["findings"]
