"""Fixture corpus for the path-free document-set API (`analitiq.validator
.document_set`) — every case here is `xfail(strict=True)` because every
function it exercises currently raises `NotImplementedError`. An
implementation PR turns each case from `xfail` to passing by replacing the
stub body it exercises and removing that case's marker; `strict=True` means a
case that starts passing while its marker is still on it fails the suite,
so a marker can never survive its own fix by accident.

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


def _xfail(fn_name: str):
    return pytest.mark.xfail(
        strict=True, raises=NotImplementedError,
        reason=f"{fn_name} is not yet implemented (analitiq.validator.document_set)")


# ---------------------------------------------------------------------------
# Entity — drift guard against DOCUMENT_ARTIFACT_KINDS (not xfail: this is a
# type-contract fact settled now, independent of any function body below
# being implemented).
# ---------------------------------------------------------------------------

def test_entity_matches_document_artifact_kinds(validator):
    assert set(get_args(validator.Entity)) == set(DOCUMENT_ARTIFACT_KINDS)


# ---------------------------------------------------------------------------
# Finding — drift guard against the keys `analitiq.validator.finding` actually
# produces (not xfail: a type-contract fact settled now). `finding()`'s own
# docstring is the source: `rule` only when given, `severity` only for a
# `fail` kind, everything else unconditional.
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

@_xfail("resolve_type_map_gaps")
def test_gap_resolution_reports_unreadable_map_without_raising(validator):
    result = validator.resolve_type_map_gaps(
        maps={"type-map-read.json": "not json"}, direction="read", probes=["STRING"])
    assert [f["message_id"] for f in result["findings"]] == ["type-map-unreadable"]
    assert result["findings"][0]["kind"] == "fail"
    assert result["findings"][0]["severity"] == "error"
    assert "direction" not in result["findings"][0]


@_xfail("resolve_type_map_gaps")
def test_gap_resolution_reports_invalid_map_with_its_direction(validator):
    # Fails TypeMapReadDoc: a list, but of a rule object missing its `match`
    # discriminator (and its arrow_type).
    result = validator.resolve_type_map_gaps(
        maps={"type-map-read.json": [{"native_type": "STRING"}]}, direction="read", probes=["STRING"])
    invalid = [f for f in result["findings"] if f["message_id"] == "invalid-type-map"]
    assert len(invalid) == 1, result["findings"]
    assert invalid[0]["kind"] == "fail" and invalid[0]["severity"] == "error"
    assert invalid[0]["direction"] == "read"


@_xfail("resolve_type_map_gaps")
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


@_xfail("resolve_type_map_gaps")
def test_gap_resolution_reports_an_unresolved_probe_as_informational(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["STRING", "BIGINT"])
    gaps = [f for f in result["findings"] if f["message_id"] == "type-map-gap"]
    assert len(gaps) == 1, result["findings"]
    assert gaps[0]["kind"] == "informational" and "severity" not in gaps[0]
    assert gaps[0]["direction"] == "read"


@_xfail("resolve_type_map_gaps")
def test_gap_resolution_fully_covered_reports_no_findings(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.resolve_type_map_gaps(maps=maps, direction="read", probes=["STRING"])
    assert result == {"findings": []}


@_xfail("resolve_type_map_gaps")
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


# ---------------------------------------------------------------------------
# Key handling.
# ---------------------------------------------------------------------------

@_xfail("validate_tree")
def test_leading_dot_slash_is_normalized_away(validator):
    with_prefix = validator.validate_tree({f"./{k}": v for k, v in _connector_tree_documents().items()})
    without_prefix = validator.validate_tree(_connector_tree_documents())
    assert with_prefix == without_prefix


@pytest.mark.parametrize("bad_key", ["/connector.json", "../connector.json", ""])
@_xfail("validate_tree")
def test_invalid_keys_are_reported_not_raised(validator, bad_key):
    documents = {**_connector_tree_documents(), bad_key: {}}
    result = validator.validate_tree(documents)
    assert any(f["message_id"] == "invalid-key" and f["path"] == bad_key for f in result["findings"])
    assert result["passed"] is False


@_xfail("validate_tree")
def test_key_that_is_both_document_and_directory_prefix_conflicts(validator):
    documents = {**_connector_tree_documents(), "endpoints/v1__records.json/extra.json": {}}
    result = validator.validate_tree(documents)
    assert any(f["message_id"] == "key-path-conflict" for f in result["findings"])


@_xfail("validate_tree")
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


@_xfail("validate_tree")
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

@_xfail("validate_tree")
def test_bytes_value_is_decoded_as_utf8_with_bom_stripped(validator):
    documents = _connector_tree_documents()
    text_result = validator.validate_tree(documents)
    as_bytes = dict(documents)
    as_bytes["connector.json"] = ("﻿" + json.dumps(documents["connector.json"])).encode("utf-8")
    bytes_result = validator.validate_tree(as_bytes)
    assert bytes_result == text_result


@_xfail("validate_tree")
def test_non_str_bytes_object_value_is_invalid_not_raised(validator):
    documents = {**_connector_tree_documents(), "connector.json": 42}
    result = validator.validate_tree(documents)
    assert any(f["message_id"] == "invalid-value" and f["path"] == "connector.json"
               for f in result["findings"])
    assert result["passed"] is False


@_xfail("resolve_type_map_gaps")
def test_invalid_value_applies_to_resolve_type_map_gaps_maps_too(validator):
    result = validator.resolve_type_map_gaps(maps={"type-map-read.json": 42}, direction="read", probes=["STRING"])
    assert any(f["message_id"] == "invalid-value" for f in result["findings"])


# ---------------------------------------------------------------------------
# Package-kind dispatch is a registry.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_tree")
def test_validate_connector_tree_validates_its_own_root_shape_directly(validator):
    result = validator.validate_connector_tree(_connector_tree_documents())
    assert result["passed"] is True
    assert not any(f["message_id"] in ("ambiguous-layout", "unrecognized-layout") for f in result["findings"])


@_xfail("validate_pipeline_tree")
def test_validate_pipeline_tree_validates_its_own_root_shape_directly(validator):
    result = validator.validate_pipeline_tree(_pipeline_tree_documents())
    assert result["passed"] is True
    assert not any(f["message_id"] in ("ambiguous-layout", "unrecognized-layout") for f in result["findings"])


@_xfail("validate_tree")
def test_validate_tree_reports_unrecognized_layout_for_neither_shape(validator):
    result = validator.validate_tree({"README.md": "not a document set at all"})
    assert result["passed"] is False
    assert any(f["message_id"] == "unrecognized-layout" for f in result["findings"])


@_xfail("validate_tree")
def test_validate_tree_reports_ambiguous_layout_when_both_shapes_match(validator):
    # A pipeline tree that ALSO carries a root-level connector.json — matching
    # both the connector-tree and the pipeline-tree detector at once.
    documents = {**_pipeline_tree_documents(), **_connector_tree_documents()}
    result = validator.validate_tree(documents)
    assert result["passed"] is False
    assert any(f["message_id"] == "ambiguous-layout" for f in result["findings"])


@_xfail("validate_tree")
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


@_xfail("validate_pipeline_tree")
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


@_xfail("validate_tree")
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


@_xfail("validate_pipeline_tree")
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


# ---------------------------------------------------------------------------
# diagnostics — auto-detects single-document vs. document-set input and
# dispatches to validate_document / validate_tree accordingly, wrapping
# either result in one ValidationEnvelope shape.
# ---------------------------------------------------------------------------

@_xfail("diagnostics")
def test_diagnostics_dispatches_a_single_document_to_validate_document(validator):
    document = json.loads((CORPUS / "valid_connector.json").read_text())
    expected_findings = validator.validate_document(document)
    expected = {"passed": not any(validator.finding_costs_a_pass(f) for f in expected_findings),
                "findings": expected_findings}
    assert json.dumps(validator.diagnostics(document)) == json.dumps(expected)


@_xfail("diagnostics")
def test_diagnostics_dispatches_a_document_set_to_validate_tree(validator):
    documents = {
        **_connector_tree_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(),
    }
    assert json.dumps(validator.diagnostics(documents)) == json.dumps(validator.validate_tree(documents))


# ---------------------------------------------------------------------------
# Acceptance — equivalence: the path-based route and the document-set route
# produce byte-identical results for the same content, per package kind.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_tree")
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


@_xfail("validate_pipeline_tree")
def test_pipeline_tree_equivalence_with_the_path_based_route(validator, tmp_path):
    import validate as pipeline_adapter  # plugins/analitiq-pipeline-builder/scripts/validate.py

    documents = _pipeline_tree_documents_with_two_findings()
    _write_tree(tmp_path, documents)
    path_based = pipeline_adapter.diagnostics_for(
        "pipeline", tmp_path / "pipelines" / "p" / "pipeline.json", bundle_root=tmp_path)
    assert len(path_based["findings"]) >= 2, path_based  # non-vacuous: order genuinely matters below
    tree_based = validator.validate_pipeline_tree(documents)
    assert json.dumps(tree_based) == json.dumps(path_based)
