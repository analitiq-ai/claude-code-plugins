"""Fixture corpus for the path-free document-set API (`analitiq.validator
.document_set`) — every case here is `xfail(strict=True)` because every
function it exercises currently raises `NotImplementedError`. An
implementation turns each case from `xfail` to passing by replacing the stub
body it exercises and removing that case's marker; `strict=True` means a case
that starts passing while its marker is still on it fails the suite, so a
marker can never survive its own fix by accident.

Two corpora already committed for the path-based routes are reused here
rather than re-authored: `packages/validator/tests/corpus/` (a connector
package) and `tests/pipeline_builder/test_validate.py`'s `_build_bundle`
layout (a pipeline bundle) — both are content this suite already keeps
model-valid, so the document-set versions built from them are testing the
document-set mechanism, not guessing at contract shapes.

The fixtures below build *parsed* documents, because the equivalence cases
also write them to disk and hand them to the path-based route. A request
carries file text, so `_package_request` / `_document_request` serialize at the
call. Nothing here covers a malformed argument — a bad key, a non-string
value, a key that is also a directory, an `entity` outside the vocabulary.
Those are refused by the request models at construction and belong to the
contract package's own model tests; a case asserting one of them produces a
*finding* would contradict the gate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.contracts.validation_requests import (
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
)

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


def _package_request(documents: dict) -> ValidatePackageRequest:
    """A package request over `documents`, each serialized to the file text a
    request actually carries."""
    return ValidatePackageRequest(
        documents={key: json.dumps(doc) for key, doc in documents.items()})


def _document_request(document, entity: str) -> ValidateSingleDocumentRequest:
    """A single-document request over one parsed document and the published
    schema name its sender declares it is written against."""
    return ValidateSingleDocumentRequest(document=json.dumps(document), entity=entity)


# ---------------------------------------------------------------------------
# Finding — drift guard against the keys `analitiq.validator.finding` actually
# produces (not xfail: a type-contract fact settled now). `finding()`'s own
# docstring is the source: `rule` only when given, `severity` only for a
# `fail` kind, everything else unconditional.
# ---------------------------------------------------------------------------

def test_finding_matches_the_keys_finding_builder_produces(validator):
    from typing import get_args, get_type_hints

    from analitiq.validator.document_set import Finding

    with_rule_and_severity = validator.finding(
        rule="RULE-PKG-030", message_id="m", kind="fail", path="p", message="msg")
    without_rule_or_severity = validator.finding(
        message_id="m", kind="notApplicable", path="p", message="msg")
    possible_keys = set(with_rule_and_severity) | set(without_rule_or_severity)
    always_present = set(with_rule_and_severity) & set(without_rule_or_severity)
    hints = get_type_hints(Finding)
    # Set equality with no carve-out: every key this TypedDict declares is one
    # `finding()` can produce, and every key `finding()` can produce is
    # declared. A key added to either side alone fails here.
    assert set(hints) == possible_keys
    # Required vs. optional tracks what these two real calls actually agreed
    # on: a key both included is one `finding()` always sets; a key only one
    # included (`rule`, `severity`) is conditional.
    assert Finding.__required_keys__ == always_present
    assert Finding.__optional_keys__ == possible_keys - always_present

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
# Fixtures: a connector package and a pipeline package, each built from
# content this suite already keeps model-valid. Values are parsed documents;
# `_package_request` serializes them into the file text a request carries.
# ---------------------------------------------------------------------------

def _connector_package_documents(*, native="STRING", arrow="Utf8") -> dict:
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
        "type-map-read.json": _type_map_doc(
            "read", [{"match": "exact", "native_type": native, "arrow_type": arrow}]),
        "endpoints/v1__records.json": endpoint,
    }


def _uncovered_endpoint_document(*, endpoint_id="v2__widgets", request_path="/v2/widgets",
                                  native="BOOLEAN", arrow="Boolean") -> dict:
    """A model-valid endpoint declaring a native type `_connector_package_documents`'s
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


def _type_map_doc(direction: str, rules: list) -> dict:
    """A `{$schema, direction, rules}` type-map document for the given
    direction — the shape `TypeMapReadDoc`/`TypeMapWriteDoc` require."""
    return {"$schema": f"{_H}/type-map-{direction}/latest.json", "direction": direction, "rules": rules}

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
# embedded-connector coverage gap must not make `validate_pipeline_package`
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
_CONNECTOR_WISE_TYPE_MAP_READ = _type_map_doc(
    "read", [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}])
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
_CONNECTOR_PG_TYPE_MAP_READ = _type_map_doc(
    "read", [{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}])
_CONNECTOR_PG_TYPE_MAP_WRITE = _type_map_doc("write", [
    {"match": "exact", "native_type": "bigint", "arrow_type": "Int64"},
    {"match": "regex", "native_type": "TEXT", "arrow_type": ".*"},
])
def _pipeline_core_documents() -> dict:
    """The connection, stream, pipeline, and destination-endpoint documents a
    pipeline package carries regardless of what its embedded
    `connectors/` subtree looks like."""
    return {
        "connections/wise/connection.json": _CONN_WISE,
        "connections/postgresql/connection.json": _CONN_PG,
        f"connections/postgresql/definition/endpoints/{_EID}.json": _DB_ENDPOINT,
        "pipelines/p/streams/orders.json": _STREAM,
        "pipelines/p/pipeline.json": _PIPELINE,
    }


def _pipeline_package_documents() -> dict:
    """A model-valid draft pipeline bundle as a `DocumentSet`, laid out at the
    same relative paths `_assemble_bundle`
    (`plugins/analitiq-pipeline-builder/scripts/validate.py`) already resolves
    from a filesystem root — so the document-set route and that function's
    on-disk route are given byte-identical content, just supplied two
    different ways. `wise`'s and `postgresql`'s embedded
    `connectors/<slug>/definition/...` subtrees are fully model-valid and
    coverage-clean: this is the fixture the equivalence test uses, so
    validating an embedded subtree as a connector package of its own must
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


def _pipeline_package_documents_with_two_findings() -> dict:
    """`_pipeline_package_documents` with two additional connection ids on the
    pipeline's own `connections.destinations` that no bundled connection
    document resolves — two real, distinguishable `connection-ref-unresolved`
    findings (one per missing id) that `validate_pipeline_bundle` reports
    identically down either route, so the byte-identical equivalence
    comparison below is checking real, order-sensitive content instead of two
    empty findings lists."""
    documents = _pipeline_package_documents()
    pipeline = {**documents["pipelines/p/pipeline.json"]}
    pipeline["connections"] = {
        **pipeline["connections"],
        "destinations": [*pipeline["connections"]["destinations"], "missing-connection-a", "missing-connection-b"],
    }
    return {**documents, "pipelines/p/pipeline.json": pipeline}


def _pipeline_package_documents_with_embedded_connectors() -> dict:
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
    RULE-PKG-030 finding proves each embedded subtree is reported on its
    own rather than the walk stopping at the first one."""
    return {
        **_pipeline_core_documents(),
        "connectors/wise/definition/connector.json": _CONNECTOR_WISE,
        "connectors/postgresql/definition/connector.json": _CONNECTOR_PG,
    }


def _write_package(root: Path, documents: dict) -> None:
    for rel, doc in documents.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))


# ---------------------------------------------------------------------------
# validate_single_document — one document, the caller's declared schema name
# checked against detection rather than trusted.
# ---------------------------------------------------------------------------

@_xfail("validate_single_document")
def test_single_document_wraps_the_path_based_route(validator):
    """A document whose declared `entity` matches what detection finds reports
    exactly the path-based route's findings, wrapped in one envelope."""
    document = json.loads((CORPUS / "valid_connector.json").read_text())
    expected_findings = validator.validate_document(document)
    expected = {"passed": not any(validator.finding_costs_a_pass(f) for f in expected_findings),
                "findings": expected_findings}
    result = validator.validate_single_document(_document_request(document, "connector"))
    assert json.dumps(result) == json.dumps(expected)


@_xfail("validate_single_document")
def test_declared_entity_that_disagrees_with_the_document_is_a_finding(validator):
    """The caller's declaration is checked, not trusted: a stream document sent
    as a connector is reported, not silently validated as whatever it looks
    like. The same document sent under its real schema name is not."""
    mismatched = validator.validate_single_document(_document_request(_STREAM, "connector"))
    assert mismatched["passed"] is False
    assert any(f["kind"] == "fail" for f in mismatched["findings"]), mismatched

    matched = validator.validate_single_document(_document_request(_STREAM, "stream"))
    assert not any(f["kind"] == "fail" for f in matched["findings"]), matched


@_xfail("validate_single_document")
def test_unparseable_document_text_is_a_finding_not_a_raise(validator):
    """Document *content* is what this API judges, so text the JSON parser
    cannot read comes back as a finding under the message id the path-based
    route already uses — never as a raised error, which is reserved for a
    defect in this package."""
    request = ValidateSingleDocumentRequest(document="{not json", entity="connector")
    result = validator.validate_single_document(request)
    assert result["passed"] is False
    assert any(f["message_id"] == "unreadable-document" for f in result["findings"]), result


# ---------------------------------------------------------------------------
# The package entry points — each called directly for the kind it names.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_package")
def test_validate_connector_package_validates_its_own_root_shape(validator):
    result = validator.validate_connector_package(
        _package_request(_connector_package_documents()))
    assert result["passed"] is True


@_xfail("validate_pipeline_package")
def test_validate_pipeline_package_validates_its_own_root_shape(validator):
    result = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents()))
    assert result["passed"] is True


@_xfail("validate_pipeline_package")
def test_embedded_connector_subtree_gets_its_own_coverage_findings(validator):
    """`connectors/wise/definition/connector.json` (kind=api) ships no sibling
    type-map or `endpoints/` directory — today's plugin never notices, because
    `_connector_endpoint_sets` only reads endpoint ids for stream-ref
    resolution. `validate_pipeline_package` calls `validate_connector_package`
    on the subtree and reports its OWN coverage findings (RULE-PKG-030 missing
    read map, RULE-PKG-035 missing endpoints/), scoped under the subtree's key
    prefix."""
    result = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents_with_embedded_connectors()))
    scoped = [f for f in result["findings"] if f["path"].startswith("connectors/wise/")]
    assert any(f["rule"] == "RULE-PKG-030" for f in scoped), result["findings"]
    assert any(f["rule"] == "RULE-PKG-035" for f in scoped), result["findings"]


# ---------------------------------------------------------------------------
# Deterministic output: findings do not depend on the order the caller happened
# to build its mapping in.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_package")
def test_connector_package_finding_order_is_independent_of_input_order(validator):
    # Two distinct uncovered endpoints, not the clean package: comparing two
    # empty findings lists cannot detect order-sensitivity at all.
    documents = {
        **_connector_package_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        "endpoints/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    forward = validator.validate_connector_package(_package_request(documents))
    assert len(forward["findings"]) >= 2, forward  # non-vacuous: order genuinely matters below
    reversed_documents = dict(reversed(list(documents.items())))
    backward = validator.validate_connector_package(_package_request(reversed_documents))
    assert forward == backward


@_xfail("validate_pipeline_package")
def test_pipeline_package_finding_order_is_independent_of_input_order(validator):
    # The same determinism rule on the other package entry point, over a
    # fixture that reports real findings down both orders.
    documents = _pipeline_package_documents_with_two_findings()
    forward = validator.validate_pipeline_package(_package_request(documents))
    assert len(forward["findings"]) >= 2, forward  # non-vacuous: order genuinely matters below
    reversed_documents = dict(reversed(list(documents.items())))
    backward = validator.validate_pipeline_package(_package_request(reversed_documents))
    assert forward == backward


# ---------------------------------------------------------------------------
# Acceptance — equivalence: the path-based route and the document-set route
# produce byte-identical results for the same content, per package kind.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_package")
def test_connector_package_equivalence_with_the_path_based_route(validator, tmp_path):
    # Two distinct uncovered endpoints, not just a clean package: a route
    # producing zero findings would make "findings order included" vacuous.
    documents = {
        **_connector_package_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        "endpoints/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    _write_package(tmp_path, documents)
    path_based = validator.validate_document(documents["connector.json"], doc_path=tmp_path / "connector.json")
    assert len(path_based) >= 2, path_based  # non-vacuous: order genuinely matters below
    package_based = validator.validate_connector_package(_package_request(documents))
    expected = {"passed": not any(validator.finding_costs_a_pass(f) for f in path_based),
                "findings": path_based}
    assert json.dumps(package_based) == json.dumps(expected)


@_xfail("validate_pipeline_package")
def test_pipeline_package_equivalence_with_the_path_based_route(validator, tmp_path):
    import validate as pipeline_adapter  # plugins/analitiq-pipeline-builder/scripts/validate.py

    documents = _pipeline_package_documents_with_two_findings()
    _write_package(tmp_path, documents)
    path_based = pipeline_adapter.diagnostics_for(
        "pipeline", tmp_path / "pipelines" / "p" / "pipeline.json", bundle_root=tmp_path)
    assert len(path_based["findings"]) >= 2, path_based  # non-vacuous: order genuinely matters below
    package_based = validator.validate_pipeline_package(_package_request(documents))
    assert json.dumps(package_based) == json.dumps(path_based)
