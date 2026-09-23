"""The in-memory request API (`analitiq.validator.document_set`).

The connector documents are reused from `packages/validator/tests/corpus/`,
content this suite already keeps model-valid, so these cases test the request
mechanism, not guesses at contract shapes. Fixtures are parsed documents; the
request helpers serialize them to the text a request carries. A malformed
argument — a bad key, a value that is not text, a key at a secret location, an
unknown kind — is refused by the request models at construction and belongs to
the contract package's own model tests.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL
from analitiq.contracts.validation_requests import (
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
    ValidateWorkspaceRequest,
)

CORPUS = Path(__file__).resolve().parent / "corpus"


def _package_request(package_kind: str, documents: dict) -> ValidatePackageRequest:
    return ValidatePackageRequest(
        package_kind=package_kind,
        documents={key: json.dumps(doc) for key, doc in documents.items()})


def _document_request(document, document_kind: str) -> ValidateSingleDocumentRequest:
    return ValidateSingleDocumentRequest(document=json.dumps(document), document_kind=document_kind)


def _workspace_request(documents: dict, run_pipeline: str | None = None) -> ValidateWorkspaceRequest:
    return ValidateWorkspaceRequest(
        documents={key: json.dumps(doc) for key, doc in documents.items()},
        run_pipeline=run_pipeline)


def _ids(result) -> list[str]:
    return [f["message_id"] for f in result["findings"]]


def _at(result, message_id: str) -> list[str]:
    """The paths of every finding under `message_id`."""
    return [f["path"] for f in result["findings"] if f["message_id"] == message_id]


# ---------------------------------------------------------------------------
# Finding — drift guard against the keys `analitiq.validator.finding` actually
# produces.
# ---------------------------------------------------------------------------

def test_finding_matches_the_keys_finding_builder_produces(validator):
    from typing import get_args, get_type_hints

    from analitiq.validator._core import _KINDS
    from analitiq.validator.document_set import Finding

    # Every call `finding()` admits, not a sample: a key set only on a branch
    # no sample visits would be invisible to both assertions below.
    produced = [validator.finding(rule=rule, message_id="m", kind=kind, path="p", message="msg")
                for kind in _KINDS for rule in (None, "RULE-PKG-030", "RULE-CTOR-043")]
    possible_keys = set().union(*(set(f) for f in produced))
    always_present = set.intersection(*(set(f) for f in produced))
    hints = get_type_hints(Finding)
    assert set(hints) == possible_keys
    assert Finding.__required_keys__ == always_present
    assert Finding.__optional_keys__ == possible_keys - always_present
    assert set(get_args(hints["kind"])) == set(_KINDS)

    # RULE-PKG-030 is error-tier and RULE-CTOR-043 warning-tier; an info-tier
    # rule (RULE-CTOR-032) never reaches a `fail` finding at all.
    observed = {validator.finding(rule=rule, message_id="m", kind="fail", path="p", message="msg")["severity"]
                for rule in ("RULE-PKG-030", "RULE-CTOR-043")}
    assert observed == set(get_args(hints["severity"]))
    with pytest.raises(ValueError):
        validator.finding(rule="RULE-CTOR-032", message_id="m", kind="fail", path="p", message="msg")


# ---------------------------------------------------------------------------
# Entry-point signatures. The request models are imported under
# `TYPE_CHECKING` and no type checker runs over this repo, so without this case
# a wrong annotation reaches a release unnoticed.
# ---------------------------------------------------------------------------

def test_entry_points_are_annotated_with_their_request_models(validator):
    import ast
    import importlib
    import inspect
    from typing import get_type_hints

    document_set = validator.document_set
    # The namespace is built from the module's OWN deferred imports, so a wrong
    # module path or a deleted import fails here.
    source = Path(document_set.__file__).read_text(encoding="utf-8")
    deferred = [statement
                for node in ast.parse(source).body
                if isinstance(node, ast.If)
                and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                for statement in node.body if isinstance(statement, ast.ImportFrom)]
    assert deferred, "no `if TYPE_CHECKING:` import resolves these annotations"
    namespace = {}
    for statement in deferred:
        module = importlib.import_module(statement.module)
        for alias in statement.names:
            namespace[alias.asname or alias.name] = getattr(module, alias.name)

    for entry_point, request_model in (
        (document_set.validate_single_document, ValidateSingleDocumentRequest),
        (document_set.validate_package, ValidatePackageRequest),
        (document_set.validate_workspace, ValidateWorkspaceRequest),
    ):
        hints = get_type_hints(entry_point, localns=namespace)
        assert hints["request"] is request_model, entry_point.__name__
        assert hints["return"] is document_set.ValidationEnvelope, entry_point.__name__
        assert tuple(inspect.signature(entry_point).parameters) == ("request",), entry_point.__name__


# ---------------------------------------------------------------------------
# Fixtures. Values are parsed documents.
# ---------------------------------------------------------------------------

_H = "https://schemas.analitiq.ai"


def _type_map_doc(**sections) -> dict:
    return {"$schema": TYPE_MAP_SCHEMA_URL, **sections}


def _connector_package_documents(*, native="STRING", arrow="Utf8") -> dict:
    """A model-valid, coverage-clean api connector package: the connector, a
    read map covering the one native/arrow pair below, and one endpoint
    declaring it."""
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": native, "arrow_type": arrow},
    }
    return {
        "definition/connector.json": connector,
        "definition/type-map.json": _type_map_doc(
            read=[{"match": "exact", "native_type": native, "arrow_type": arrow}]),
        f"definition/endpoints/{endpoint['endpoint_id']}.json": endpoint,
    }


def _uncovered_endpoint_document(*, endpoint_id="v2__widgets", request_path="/v2/widgets") -> dict:
    """A model-valid endpoint declaring a native type the package's read map
    does not cover: a distinguishable `native-type-unresolved` finding."""
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["endpoint_id"] = endpoint_id
    endpoint["operations"]["read"]["request"]["path"] = request_path
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "b": {"type": "string", "native_type": "BOOLEAN", "arrow_type": "Boolean"},
    }
    return endpoint


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
_WISE_TYPE_MAP = _type_map_doc(read=[{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}])
_CONNECTOR_PG_TYPE_MAP = _type_map_doc(
    read=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}],
    write=[
        {"match": "exact", "native_type": "bigint", "arrow_type": "Int64"},
        {"match": "regex", "native_type": "TEXT", "arrow_type": ".*"},
    ])


def _pg_connector() -> dict:
    connector = json.loads((CORPUS / "valid_connector_sync_driver.json").read_text())
    connector["connector_id"] = "postgresql"
    return connector


def _workspace_documents() -> dict:
    """A workspace every check passes: a pipeline reading a Wise connector
    endpoint and writing a Postgres connection endpoint, every package it
    references, and the manifest listing it."""
    return copy.deepcopy({
        "connectors/wise/definition/connector.json": _CONNECTOR_WISE,
        "connectors/wise/definition/type-map.json": _WISE_TYPE_MAP,
        "connectors/wise/definition/endpoints/transfers.json": _WISE_TRANSFERS_ENDPOINT,
        "connectors/postgresql/definition/connector.json": _pg_connector(),
        "connectors/postgresql/definition/type-map.json": _CONNECTOR_PG_TYPE_MAP,
        f"connections/{_SRC}/connection.json": _CONN_WISE,
        f"connections/{_DST}/connection.json": _CONN_PG,
        f"connections/{_DST}/definition/endpoints/{_EID}.json": _DB_ENDPOINT,
        f"pipelines/{_PID}/pipeline.json": _PIPELINE,
        f"pipelines/{_PID}/streams/{_SID}.json": _STREAM,
        "pipelines/manifest.json": {"pipelines": [
            {"pipeline_id": _PID, "status": "draft", "path": f"{_PID}/pipeline.json"}]},
    })


# ---------------------------------------------------------------------------
# validate_single_document — graded as the kind the caller names.
# ---------------------------------------------------------------------------

def test_a_valid_connector_passes_alone(validator):
    """A connector is graded by its own kind's rules: nothing asks for siblings
    a single document cannot carry."""
    document = json.loads((CORPUS / "valid_connector.json").read_text())
    result = validator.validate_single_document(_document_request(document, "connector"))
    assert result == {"passed": True, "findings": []}


def test_an_endpoint_naming_a_transport_passes_alone(validator):
    """Resolving `transport_ref` needs the connector, so it is a package check,
    not a rule of the endpoint alone."""
    document = copy.deepcopy(_WISE_TRANSFERS_ENDPOINT)
    document["operations"]["read"]["request"]["transport_ref"] = "api"
    result = validator.validate_single_document(_document_request(document, "api-endpoint"))
    assert result == {"passed": True, "findings": []}


def test_a_document_is_graded_as_the_named_kind_never_as_what_it_resembles(validator):
    """A stream named `connector` is graded against the connector model: its
    findings are the connector model's, never a verdict about what it resembles."""
    from analitiq.validator.connectors import _validate_connector_document

    result = validator.validate_single_document(_document_request(_STREAM, "connector"))
    assert result["passed"] is False
    assert result["findings"] == _validate_connector_document(copy.deepcopy(_STREAM))


def test_a_malformed_endpoint_gets_findings_about_its_defects(validator):
    """An endpoint missing `operations` is not recognised by its shape; named
    as `api-endpoint`, its defect is what is reported."""
    document = {k: v for k, v in _WISE_TRANSFERS_ENDPOINT.items() if k != "operations"}
    result = validator.validate_single_document(_document_request(document, "api-endpoint"))
    assert result["passed"] is False
    assert any("operations" in f["path"] or "operations" in f["message"] for f in result["findings"]), result
    assert "unrecognized-document" not in _ids(result)


def test_a_type_map_is_held_to_the_write_vocabulary_alone(validator):
    """A type map is graded the same wherever it sits: a write section that
    renders too little of the write vocabulary earns RULE-TMAP-017's warning."""
    document = _type_map_doc(write=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}])
    result = validator.validate_single_document(_document_request(document, "type-map"))
    assert "RULE-TMAP-017" in {f.get("rule") for f in result["findings"]}, result
    assert result["passed"] is True


def test_unparseable_document_text_is_a_finding_not_a_raise(validator):
    request = ValidateSingleDocumentRequest(document="{not json", document_kind="connector")
    assert _ids(validator.validate_single_document(request)) == ["unreadable-document"]


def test_text_refused_outside_jsondecodeerror_is_a_finding_not_a_raise(
        validator, text_refused_outside_jsondecodeerror):
    result = validator.validate_single_document(ValidateSingleDocumentRequest(
        document=text_refused_outside_jsondecodeerror, document_kind="connector"))
    assert _ids(result) == ["unreadable-document"]


def test_a_crash_while_validating_a_document_is_one_finding(validator, monkeypatch):
    from analitiq.validator import _core

    def _explodes(doc):
        raise RuntimeError("boom")

    monkeypatch.setitem(_core._DOCUMENT_VALIDATORS, "connector", _explodes)
    result = validator.validate_single_document(_document_request({}, "connector"))
    assert _ids(result) == ["check-crashed"]


# ---------------------------------------------------------------------------
# The document validator registry answers to the contract (FR-015a/d/e).
# ---------------------------------------------------------------------------

def _located_kinds(model) -> set[str]:
    secret = {model.LOCATIONS[pattern] for pattern in model.SECRET_LOCATIONS}
    return set(model.LOCATIONS.values()) - secret


def test_every_located_kind_has_one_named_document_validator(validator):
    """Keyed by every kind any request locates: a kind the contract adds to a
    location table fails here, never as a crash in a consumer."""
    from analitiq.contracts.workspace import _DOCUMENT_LOCATIONS, PACKAGE_MODELS
    from analitiq.validator._core import _DOCUMENT_VALIDATORS

    located = set().union(*(_located_kinds(m) for m in PACKAGE_MODELS.values()))
    located |= set(_DOCUMENT_LOCATIONS.values())
    assert set(_DOCUMENT_VALIDATORS) == located
    for kind, validate in _DOCUMENT_VALIDATORS.items():
        assert validate.__name__ == f"_validate_{kind.replace('-', '_')}_document", kind


def test_every_nameable_document_kind_is_located(validator):
    """Every kind a single-document request can name is one a location holds,
    so each has its validator."""
    from analitiq.contracts.validation_requests import DOCUMENT_SCHEMA_NAMES
    from analitiq.validator._core import _DOCUMENT_VALIDATORS

    assert set(DOCUMENT_SCHEMA_NAMES) <= set(_DOCUMENT_VALIDATORS)


# ---------------------------------------------------------------------------
# validate_package
# ---------------------------------------------------------------------------

def test_a_valid_connector_package_passes(validator):
    result = validator.validate_package(_package_request("connector", _connector_package_documents()))
    assert result == {"passed": True, "findings": []}


def test_a_package_without_its_root_fails_about_the_package(validator):
    documents = _connector_package_documents()
    del documents["definition/connector.json"]
    documents["definition/endpoints/v2__widgets.json"] = _uncovered_endpoint_document()
    result = validator.validate_package(_package_request("connector", documents))
    assert result["passed"] is False
    assert _at(result, "package-root-missing") == ["definition/connector.json#"]
    # Without the root, no check needing the connector runs.
    assert "native-type-unresolved" not in _ids(result)


def test_a_key_at_no_location_is_not_graded(validator):
    documents = _connector_package_documents()
    documents["README.json"] = {"not": "a document"}
    documents["connector.json"] = {"not": "located either"}
    result = validator.validate_package(_package_request("connector", documents))
    assert result == {"passed": True, "findings": []}


def test_an_unparseable_document_withholds_the_cross_document_checks(validator):
    documents = {key: json.dumps(doc) for key, doc in _connector_package_documents().items()}
    documents["definition/endpoints/v2__widgets.json"] = json.dumps(_uncovered_endpoint_document())
    documents["definition/endpoints/v3__gadgets.json"] = "{not json"
    result = validator.validate_package(ValidatePackageRequest(package_kind="connector", documents=documents))
    assert _ids(result) == ["unreadable-document"]


def test_a_package_finding_names_its_document_by_key(validator):
    documents = _connector_package_documents()
    documents["definition/endpoints/v2__widgets.json"] = _uncovered_endpoint_document()
    result = validator.validate_package(_package_request("connector", documents))
    assert [p.split("#")[0] for p in _at(result, "native-type-unresolved")] == [
        "definition/endpoints/v2__widgets.json"]


def test_a_package_key_is_percent_encoded_in_a_finding(validator):
    documents = _connector_package_documents()
    endpoint = _uncovered_endpoint_document(endpoint_id="v2 widgets#x")
    documents["definition/endpoints/v2 widgets#x.json"] = endpoint
    result = validator.validate_package(_package_request("connector", documents))
    assert any(p.startswith("definition/endpoints/v2%20widgets%23x.json#")
               for p in _at(result, "native-type-unresolved")), result


def test_package_findings_do_not_depend_on_key_order(validator):
    documents = _connector_package_documents()
    documents["definition/endpoints/v2__widgets.json"] = _uncovered_endpoint_document()
    documents["definition/endpoints/v3__gadgets.json"] = _uncovered_endpoint_document(
        endpoint_id="v3__gadgets", request_path="/v3/gadgets")
    forward = validator.validate_package(_package_request("connector", documents))
    backward = validator.validate_package(_package_request("connector", dict(reversed(documents.items()))))
    assert len(forward["findings"]) > 1
    assert forward == backward


@pytest.mark.parametrize("package_kind, documents, endpoint", [
    ("connector", _connector_package_documents(), _uncovered_endpoint_document()),
    ("connection", {"connection.json": _CONN_PG}, _DB_ENDPOINT),
])
def test_endpoint_ids_are_unique_in_every_package(validator, package_kind, documents, endpoint):
    """RULE-PKG-032 holds in a connection package as in a connector package,
    by one check whose message names the package."""
    documents = {**documents, "definition/endpoints/a.json": endpoint, "definition/endpoints/b.json": endpoint}
    result = validator.validate_package(_package_request(package_kind, documents))
    duplicates = [f for f in result["findings"] if f["message_id"] == "duplicate-endpoint-id"]
    assert [f["path"] for f in duplicates] == ["definition/endpoints/b.json#/endpoint_id"]
    assert duplicates[0]["rule"] == "RULE-PKG-032"
    assert "package" in duplicates[0]["message"] and "release" not in duplicates[0]["message"]


@pytest.mark.parametrize("package_kind, documents, endpoint", [
    ("connector", _connector_package_documents(), _uncovered_endpoint_document()),
    ("connection", {"connection.json": _CONN_PG}, _DB_ENDPOINT),
])
def test_an_endpoint_file_is_named_by_its_id(validator, package_kind, documents, endpoint):
    documents = {**documents, "definition/endpoints/misnamed.json": endpoint}
    result = validator.validate_package(_package_request(package_kind, documents))
    assert _at(result, "filename-id-mismatch") == ["definition/endpoints/misnamed.json#/endpoint_id"]


def test_an_api_connector_package_without_endpoints_fails(validator):
    documents = _connector_package_documents()
    documents = {k: v for k, v in documents.items() if "/endpoints/" not in k}
    result = validator.validate_package(_package_request("connector", documents))
    assert _at(result, "endpoints-missing") == ["definition/connector.json#"]


def test_an_endpoint_transport_ref_resolves_against_its_connector(validator):
    documents = _connector_package_documents()
    (key,) = [k for k in documents if "/endpoints/" in k]
    documents[key]["operations"]["read"]["request"]["transport_ref"] = "nowhere"
    result = validator.validate_package(_package_request("connector", documents))
    assert [f.get("rule") for f in result["findings"]] == ["RULE-ENDP-047"]
    assert result["findings"][0]["path"].startswith(f"{key}#")


def test_an_api_map_carrying_write_is_reported_on_the_map(validator):
    documents = _connector_package_documents()
    documents["definition/type-map.json"]["write"] = [
        {"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
    result = validator.validate_package(_package_request("connector", documents))
    assert _at(result, "write-map-not-allowed") == ["definition/type-map.json#/write"]


def test_a_database_connector_without_a_write_map_is_reported_on_the_connector(validator):
    documents = {"definition/connector.json": _pg_connector(),
                 "definition/type-map.json": _type_map_doc(
                     read=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}])}
    result = validator.validate_package(_package_request("connector", documents))
    assert _at(result, "write-map-missing") == ["definition/connector.json#"]


def test_a_connector_of_no_known_kind_skips_what_its_kind_decides(validator):
    documents = _connector_package_documents()
    documents["definition/connector.json"]["kind"] = "carrier-pigeon"
    result = validator.validate_package(_package_request("connector", documents))
    assert _at(result, "coverage-check-skipped-bad-kind") == ["definition/connector.json#/kind"]
    assert "endpoints-missing" not in _ids(result)


def test_a_defect_in_each_located_document_is_reported_under_its_key(validator):
    documents = {
        "connection.json": {**_CONN_PG, "connector_id": 7},
        "definition/type-map.json": _type_map_doc(read="not a list"),
        f"definition/endpoints/{_EID}.json": {**_DB_ENDPOINT, "columns": []},
    }
    result = validator.validate_package(_package_request("connection", documents))
    about = {f["path"].split("#")[0] for f in result["findings"] if f["kind"] == "fail"}
    assert about == set(documents)


def test_a_valid_connection_package_passes(validator):
    documents = {"connection.json": _CONN_PG, f"definition/endpoints/{_EID}.json": _DB_ENDPOINT}
    assert validator.validate_package(_package_request("connection", documents)) == {
        "passed": True, "findings": []}


def test_a_pipeline_package_catches_a_stream_it_does_not_carry(validator):
    documents = {"pipeline.json": _PIPELINE}
    result = validator.validate_package(_package_request("pipeline", documents))
    assert result["passed"] is False
    assert _at(result, "stream-ref-unresolved") == ["pipeline.json#/streams/0"]


def test_a_pipeline_package_catches_a_stream_under_another_pipeline(validator):
    stream = {**_STREAM, "pipeline_id": "55555555-5555-4555-8555-555555555555"}
    documents = {"pipeline.json": _PIPELINE, f"streams/{_SID}.json": stream}
    result = validator.validate_package(_package_request("pipeline", documents))
    assert _at(result, "stream-wrong-parent-pipeline") == [f"streams/{_SID}.json#/pipeline_id"]


def test_a_draft_pipeline_package_passes(validator):
    """Runnability is not a package property: a draft is authored content."""
    documents = {"pipeline.json": _PIPELINE, f"streams/{_SID}.json": _STREAM}
    assert validator.validate_package(_package_request("pipeline", documents)) == {
        "passed": True, "findings": []}


def test_a_crash_in_a_cross_document_check_costs_only_that_check(validator, monkeypatch):
    from analitiq.validator import document_set

    def _explodes(documents):
        raise RuntimeError("boom")

    (check,) = [c for c in document_set._CHECKS if c.run.__name__ == "_check_stream_refs"]
    crashed = [document_set._Check(_explodes, c.reads, c.gates_run) if c is check else c
               for c in document_set._CHECKS]
    monkeypatch.setattr(document_set, "_CHECKS", tuple(crashed))
    stream = {**_STREAM, "pipeline_id": "55555555-5555-4555-8555-555555555555"}
    documents = {"pipeline.json": _PIPELINE, f"streams/{_SID}.json": stream}
    result = validator.validate_package(_package_request("pipeline", documents))
    assert "check-crashed" in _ids(result)
    assert "stream-wrong-parent-pipeline" in _ids(result)


# ---------------------------------------------------------------------------
# validate_workspace
# ---------------------------------------------------------------------------

def test_a_valid_workspace_passes(validator):
    assert validator.validate_workspace(_workspace_request(_workspace_documents())) == {
        "passed": True, "findings": []}


def test_a_connection_scoped_endpoint_must_be_in_its_connection(validator):
    documents = _workspace_documents()
    del documents[f"connections/{_DST}/definition/endpoints/{_EID}.json"]
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "endpoint-ref-unresolved") == [
        f"pipelines/{_PID}/streams/{_SID}.json#/destinations/0/endpoint_ref"]


def test_a_connector_scoped_endpoint_must_be_in_the_connections_connector(validator):
    documents = _workspace_documents()
    documents[f"pipelines/{_PID}/streams/{_SID}.json"]["source"]["endpoint_ref"]["endpoint_id"] = "transfer"
    result = validator.validate_workspace(_workspace_request(documents))
    findings = [f for f in result["findings"] if f["message_id"] == "connector-endpoint-ref-unresolved"]
    assert [f["path"] for f in findings] == [
        f"pipelines/{_PID}/streams/{_SID}.json#/source/endpoint_ref"]
    assert findings[0]["severity"] == "warning"
    assert result["passed"] is True



def test_a_connector_scoped_endpoint_from_another_connector_is_unresolved(validator):
    documents = _workspace_documents()
    balances = copy.deepcopy(_WISE_TRANSFERS_ENDPOINT) | {"endpoint_id": "balances"}
    documents["connectors/postgresql/definition/endpoints/balances.json"] = balances
    documents[f"pipelines/{_PID}/streams/{_SID}.json"]["source"]["endpoint_ref"]["endpoint_id"] = "balances"
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "connector-endpoint-ref-unresolved") == [
        f"pipelines/{_PID}/streams/{_SID}.json#/source/endpoint_ref"]

def test_a_pipeline_must_find_its_connections(validator):
    documents = _workspace_documents()
    documents = {k: v for k, v in documents.items() if not k.startswith(f"connections/{_SRC}/")}
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "connection-ref-unresolved") == [f"pipelines/{_PID}/pipeline.json#/connections"]


def test_a_connection_must_find_its_connector(validator):
    documents = _workspace_documents()
    documents = {k: v for k, v in documents.items() if not k.startswith("connectors/postgresql/")}
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "connector-ref-unresolved") == [f"connections/{_DST}/connection.json#/connector_id"]


def test_a_connection_write_rule_shadowing_its_connector_is_reported(validator):
    """The connector's map now renders every family; the connection restates
    one it already covers, which RULE-TMAP-018 reports on the connection's map."""
    documents = _workspace_documents()
    documents["connectors/postgresql/definition/type-map.json"]["write"] = [
        {"match": "regex", "native_type": "TEXT", "arrow_type": ".*"}]
    documents[f"connections/{_DST}/definition/type-map.json"] = _type_map_doc(
        write=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}])
    result = validator.validate_workspace(_workspace_request(documents))
    findings = [f for f in result["findings"]
                if f["message_id"] == "connection-write-map-shadows-connector"]
    assert [f["path"] for f in findings] == [f"connections/{_DST}/definition/type-map.json#/write"]
    assert findings[0]["rule"] == "RULE-TMAP-018"
    assert result["passed"] is False


def test_a_connection_write_rule_covering_a_genuine_gap_is_not_reported(validator):
    """The connector's map covers only `Int64`; the connection covers `Utf8`,
    a family its connector leaves unresolved — the case RULE-TMAP-018 protects."""
    documents = _workspace_documents()
    documents["connectors/postgresql/definition/type-map.json"]["write"] = [
        {"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}]
    documents[f"connections/{_DST}/definition/type-map.json"] = _type_map_doc(
        write=[{"match": "exact", "native_type": "text", "arrow_type": "Utf8"}])
    result = validator.validate_workspace(_workspace_request(documents))
    assert "connection-write-map-shadows-connector" not in _ids(result)


def test_the_shadow_check_never_runs_for_a_bare_package_request(validator):
    """`ConnectionPackage.LOCATIONS` never carries a `connector` kind, so the
    check cannot run outside a workspace, where both packages are in hand."""
    connection_documents = {
        "connection.json": _CONN_PG,
        "definition/type-map.json": _type_map_doc(
            write=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}]),
    }
    result = validator.validate_package(_package_request("connection", connection_documents))
    assert "connection-write-map-shadows-connector" not in _ids(result)

    connector_documents = {
        "definition/connector.json": _pg_connector(),
        "definition/type-map.json": _type_map_doc(
            write=[{"match": "regex", "native_type": "TEXT", "arrow_type": ".*"}]),
    }
    result = validator.validate_package(_package_request("connector", connector_documents))
    assert "connection-write-map-shadows-connector" not in _ids(result)


def test_only_the_shadowing_connection_is_reported_among_several(validator):
    """Two connections against two different connectors: one shadows, one
    covers a genuine gap. The `connector_id` pairing reports only the first."""
    other_connection_id = "55555555-5555-4555-8555-555555555556"
    documents = _workspace_documents()
    documents["connectors/postgresql/definition/type-map.json"]["write"] = [
        {"match": "regex", "native_type": "TEXT", "arrow_type": ".*"}]
    documents[f"connections/{_DST}/definition/type-map.json"] = _type_map_doc(
        write=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}])
    documents["connectors/mysql/definition/connector.json"] = _pg_connector() | {"connector_id": "mysql"}
    documents["connectors/mysql/definition/type-map.json"] = _type_map_doc(
        write=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}])
    documents[f"connections/{other_connection_id}/connection.json"] = (
        _CONN_PG | {"connection_id": other_connection_id, "connector_id": "mysql"})
    documents[f"connections/{other_connection_id}/definition/type-map.json"] = _type_map_doc(
        write=[{"match": "exact", "native_type": "text", "arrow_type": "Utf8"}])
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "connection-write-map-shadows-connector") == [
        f"connections/{_DST}/definition/type-map.json#/write"]


def test_a_connection_exact_rule_outside_the_family_probe_is_detected(validator):
    """A parameterized value (`Decimal128(38, 9)`) the family's representative
    probe (`Decimal128(1, 0)`) never tests on its own — resolved from the
    connection rule's own literal, never approximated by the family sample."""
    documents = _workspace_documents()
    documents["connectors/postgresql/definition/type-map.json"]["write"] = [
        {"match": "regex", "arrow_type": r"^Decimal128\((?<p>\d+), (?<s>\d+)\)$",
         "native_type": "NUMERIC(${p},${s})"}]
    documents[f"connections/{_DST}/definition/type-map.json"] = _type_map_doc(
        write=[{"match": "exact", "arrow_type": "Decimal128(38, 9)", "native_type": "numeric(38,9)"}])
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "connection-write-map-shadows-connector") == [
        f"connections/{_DST}/definition/type-map.json#/write"]


def test_a_connection_regex_write_rule_shadowing_its_connector_is_reported(validator):
    """The regex branch of `_connection_write_shadow_probes`: a connection
    regex wide enough to match the family-probe vocabulary shadows the
    connector's exact `Int64` rule."""
    documents = _workspace_documents()
    documents["connectors/postgresql/definition/type-map.json"]["write"] = [
        {"match": "exact", "arrow_type": "Int64", "native_type": "bigint"}]
    documents[f"connections/{_DST}/definition/type-map.json"] = _type_map_doc(
        write=[{"match": "regex", "arrow_type": ".*", "native_type": "TEXT"}])
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "connection-write-map-shadows-connector") == [
        f"connections/{_DST}/definition/type-map.json#/write"]


def test_a_connection_regex_narrower_than_every_family_probe_still_shadows_a_connector_literal(validator):
    """A connection regex matching only `Decimal128(38, 9)` — narrower than
    the family's representative probe (`Decimal128(1, 0)`), so no family
    probe falls inside it — still shadows a connector rule declaring that
    exact value, because the probe pool also carries the connector's own
    authored `exact` literals, not just the family sample."""
    documents = _workspace_documents()
    documents["connectors/postgresql/definition/type-map.json"]["write"] = [
        {"match": "exact", "arrow_type": "Decimal128(38, 9)", "native_type": "numeric(38,9)"}]
    documents[f"connections/{_DST}/definition/type-map.json"] = _type_map_doc(
        write=[{"match": "regex", "arrow_type": r"^Decimal128\(38, 9\)$", "native_type": "NUMERIC(38,9)"}])
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "connection-write-map-shadows-connector") == [
        f"connections/{_DST}/definition/type-map.json#/write"]


def test_a_connection_regex_matching_only_an_excluded_write_family_is_detected(validator):
    """`Time32` is excluded from `_WRITE_VOCABULARY_PROBES` (RULE-TMAP-017's
    coverage-warning pool), but the shadow check draws from the unfiltered
    `_ALL_WRITE_FAMILY_PROBES` instead — this fails if that pool were swapped
    for the filtered one, since neither side here declares an `exact` literal
    the connector-literal widening could fall back on."""
    documents = _workspace_documents()
    documents["connectors/postgresql/definition/type-map.json"]["write"] = [
        {"match": "regex", "arrow_type": r"^Time32\(.*\)$", "native_type": "TIME"}]
    documents[f"connections/{_DST}/definition/type-map.json"] = _type_map_doc(
        write=[{"match": "regex", "arrow_type": r"^Time32\(SECOND\)$", "native_type": "TIME(0)"}])
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "connection-write-map-shadows-connector") == [
        f"connections/{_DST}/definition/type-map.json#/write"]


def test_a_package_check_in_a_workspace_is_reported_once(validator):
    documents = _workspace_documents()
    del documents[f"pipelines/{_PID}/streams/{_SID}.json"]
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "stream-ref-unresolved") == [f"pipelines/{_PID}/pipeline.json#/streams/0"]


def test_an_unparseable_document_withholds_its_packages_checks_in_a_workspace(validator):
    documents = {k: json.dumps(v) for k, v in _workspace_documents().items()}
    documents["connectors/wise/definition/endpoints/v2__widgets.json"] = json.dumps(_uncovered_endpoint_document())
    documents["connectors/wise/definition/endpoints/v3__gadgets.json"] = "{not json"
    result = validator.validate_workspace(ValidateWorkspaceRequest(documents=documents))
    assert _ids(result) == ["unreadable-document"]


def test_an_unparseable_document_withholds_the_workspace_checks(validator):
    documents = {k: json.dumps(v) for k, v in _workspace_documents().items()}
    del documents[f"connections/{_DST}/definition/endpoints/{_EID}.json"]
    documents[f"connections/{_SRC}/connection.json"] = "{not json"
    result = validator.validate_workspace(ValidateWorkspaceRequest(documents=documents))
    assert _at(result, "unreadable-document") == [f"connections/{_SRC}/connection.json#"]
    assert "endpoint-ref-unresolved" not in _ids(result)




_PHASES = ("root", "document", "package-check", "workspace-check", "run-check")


def _graded_phases(monkeypatch, grade) -> list[tuple[str, str]]:
    """`grade()`'s findings in order, each as its phase and the key it is
    ordered by. A check finding's phase is the unit whose checks produced it,
    read from the check registry, and a package check's key is the directory of
    the package its documents came from; any other finding is a document's,
    keyed by its document, except a missing root."""
    from analitiq.contracts.workspace import PACKAGE_MODELS
    from analitiq.validator import document_set

    def unit(checks, documents) -> tuple[str, str]:
        if all(c.gates_run for c in checks):
            return "run-check", ""
        if all(c in document_set._workspace_checks() for c in checks):
            return "workspace-check", ""
        assert any(checks == document_set._package_checks(m) for m in PACKAGE_MODELS.values()), checks
        (directory,) = {d.package for docs in documents.values() for d in docs}
        return "package-check", directory

    produced: dict[int, tuple[str, str]] = {}
    check_findings = document_set._check_findings

    def recording(checks, documents):
        findings = check_findings(checks, documents)
        if findings:
            produced.update((id(f), unit(checks, documents)) for f in findings)
        return findings

    monkeypatch.setattr(document_set, "_check_findings", recording)
    result = grade()
    return [produced.get(id(f)) or ("root" if f["message_id"] == "package-root-missing" else "document",
                                    f["path"].split("#")[0]) for f in result["findings"]]


def _assert_graded_in_phase_order(phases: list[tuple[str, str]], expected: set[str]) -> None:
    """Phases never go backwards, documents come in key order, and a package's
    check findings come together, packages in directory order. Nothing is
    claimed about order among one unit's check findings."""
    assert {phase for phase, _ in phases} == expected, phases
    ranks = [_PHASES.index(phase) for phase, _ in phases]
    assert ranks == sorted(ranks), phases
    for phase in ("document", "package-check"):
        keys = [key for p, key in phases if p == phase]
        assert keys == sorted(keys), phases


def test_a_package_is_graded_documents_first_then_its_checks(validator, monkeypatch):
    documents = {k.removeprefix("connectors/wise/"): v for k, v in _workspace_documents().items()
                 if k.startswith("connectors/wise/")}
    documents["definition/endpoints/aaa.json"] = copy.deepcopy(_WISE_TRANSFERS_ENDPOINT) | {"endpoint_id": "bbb"}
    documents["definition/endpoints/transfers.json"]["unknown_field"] = True
    phases = _graded_phases(monkeypatch, lambda: validator.validate_package(_package_request("connector", documents)))
    _assert_graded_in_phase_order(phases, {"document", "package-check"})


def test_a_workspace_is_graded_in_one_request_wide_phase_order(validator, monkeypatch):
    documents = _workspace_documents()
    documents["connectors/wise/definition/endpoints/aaa.json"] = (
        copy.deepcopy(_WISE_TRANSFERS_ENDPOINT) | {"endpoint_id": "bbb"})
    documents[f"pipelines/{_PID}/streams/{_SID}.json"]["source"]["endpoint_ref"]["endpoint_id"] = "transfer"
    orphan = "66666666-6666-4666-8666-666666666666"
    documents[f"pipelines/{_PID}/streams/{orphan}.json"] = {
        **documents[f"pipelines/{_PID}/streams/{_SID}.json"], "stream_id": orphan,
        "pipeline_id": "55555555-5555-4555-8555-555555555555"}
    entry = documents["pipelines/manifest.json"]["pipelines"][0]
    documents["pipelines/manifest.json"]["pipelines"].append(dict(entry))

    results = []

    def graded(arrival):
        def grade():
            results.append(validator.validate_workspace(
                _workspace_request(arrival, run_pipeline=f"pipelines/{_PID}/")))
            return results[-1]
        return _graded_phases(monkeypatch, grade)

    for arrival in (documents, dict(reversed(documents.items()))):
        phases = graded(arrival)
        assert {key for phase, key in phases if phase == "package-check"} == {
            "connectors/wise/", f"pipelines/{_PID}/"}, phases
        _assert_graded_in_phase_order(phases, {"document", "package-check", "workspace-check", "run-check"})
    assert results[0] == results[1]


def test_a_missing_root_is_reported_before_every_document(validator):
    documents = _workspace_documents()
    del documents["connectors/wise/definition/connector.json"]
    documents[f"connections/{_DST}/connection.json"] = {}
    result = validator.validate_workspace(_workspace_request(documents))
    assert _ids(result)[0] == "package-root-missing"
    keys = [f["path"].split("#")[0] for f in result["findings"][1:]]
    assert keys == sorted(keys) and f"connections/{_DST}/connection.json" in keys

def test_an_unparseable_document_withholds_every_packages_checks_in_a_workspace(validator):
    documents = {k: json.dumps(v) for k, v in _workspace_documents().items()}
    del documents[f"pipelines/{_PID}/streams/{_SID}.json"]
    documents["connectors/wise/definition/endpoints/v3__gadgets.json"] = "{not json"
    result = validator.validate_workspace(ValidateWorkspaceRequest(documents=documents))
    assert _ids(result) == ["unreadable-document"]

def test_an_unparseable_manifest_withholds_the_workspace_checks(validator):
    documents = {k: json.dumps(v) for k, v in _workspace_documents().items()}
    del documents[f"connections/{_DST}/definition/endpoints/{_EID}.json"]
    documents["pipelines/manifest.json"] = "{not json"
    result = validator.validate_workspace(ValidateWorkspaceRequest(documents=documents))
    assert _ids(result) == ["unreadable-document"]


def test_a_workspace_package_without_its_root_fails_about_that_package(validator):
    documents = _workspace_documents()
    del documents[f"connections/{_DST}/connection.json"]
    result = validator.validate_workspace(_workspace_request(documents))
    assert _at(result, "package-root-missing") == [f"connections/{_DST}/connection.json#"]


def test_the_pipeline_named_to_run_must_be_active(validator):
    documents = _workspace_documents()
    ran = validator.validate_workspace(_workspace_request(documents, run_pipeline=f"pipelines/{_PID}/"))
    assert _at(ran, "pipeline-not-active") == [f"pipelines/{_PID}/pipeline.json#/status"]
    assert ran["passed"] is False
    assert "pipeline-not-active" not in _ids(validator.validate_workspace(_workspace_request(documents)))


def test_an_active_pipeline_named_to_run_needs_a_runnable_stream(validator):
    documents = _workspace_documents()
    documents[f"pipelines/{_PID}/pipeline.json"]["status"] = "active"
    result = validator.validate_workspace(_workspace_request(documents, run_pipeline=f"pipelines/{_PID}/"))
    assert _at(result, "active-pipeline-no-runnable-stream") == [f"pipelines/{_PID}/pipeline.json#/streams"]


def test_only_the_pipeline_named_to_run_is_held_to_running(validator):
    other = "66666666-6666-4666-8666-666666666666"
    documents = _workspace_documents()
    documents[f"pipelines/{other}/pipeline.json"] = {**_PIPELINE, "pipeline_id": other, "streams": []}
    documents[f"pipelines/{_PID}/pipeline.json"]["status"] = "active"
    result = validator.validate_workspace(_workspace_request(documents, run_pipeline=f"pipelines/{_PID}/"))
    assert "pipeline-not-active" not in _ids(result)


def test_the_manifest_is_graded_by_its_model(validator):
    documents = _workspace_documents()
    entry = documents["pipelines/manifest.json"]["pipelines"][0]
    documents["pipelines/manifest.json"]["pipelines"].append(dict(entry))
    result = validator.validate_workspace(_workspace_request(documents))
    assert result["passed"] is False
    assert all(f["path"].startswith("pipelines/manifest.json") for f in result["findings"]), result


def test_workspace_findings_do_not_depend_on_key_order(validator):
    documents = {k: v for k, v in _workspace_documents().items() if not k.startswith("connectors/")}
    forward = validator.validate_workspace(_workspace_request(documents))
    backward = validator.validate_workspace(_workspace_request(dict(reversed(documents.items()))))
    assert len(forward["findings"]) > 1
    assert forward == backward


# ---------------------------------------------------------------------------
# Routing (FR-010): where each check runs is computed from what it reads.
# ---------------------------------------------------------------------------

def test_every_check_runs_at_exactly_one_kind_of_unit(validator):
    """A check no unit runs is a defect, and one run by a package is never run
    again by the workspace."""
    from analitiq.contracts.workspace import PACKAGE_MODELS
    from analitiq.validator import document_set

    for check in document_set._CHECKS:
        packages = [m for m in PACKAGE_MODELS.values() if check in document_set._package_checks(m)]
        at_workspace = check in document_set._workspace_checks()
        at_run = check in document_set._run_checks()
        assert (bool(packages), at_workspace, at_run).count(True) == 1, check


def test_a_check_is_handed_only_the_kinds_it_reads(validator):
    from analitiq.validator import document_set

    everything = {kind: (document_set._Doc(f"{kind}.json", "", {}),)
                  for check in document_set._CHECKS for kind in check.reads} | {
        "type-map": (document_set._Doc("t.json", "", {}),)}
    for check in document_set._CHECKS:
        assert set(document_set._documents_for(check, everything)) == set(check.reads), check
