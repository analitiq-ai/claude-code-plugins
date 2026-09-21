"""Grading by declared kind and by published package.

A caller names what it holds: one document and the kind it is written against,
or a package and the published package schema it forms. Nothing reads a
document's content to decide what it is. A package's root and the kind of each
document come from `PACKAGE_MODELS[package]` alone; a key no location matches is
not part of the package and is not graded.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analitiq.contracts.connection import CONNECTION_SCHEMA_URL
from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.contracts.endpoints import DATABASE_ENDPOINT_SCHEMA_URL
from analitiq.contracts.pipelines.config import PIPELINE_SCHEMA_URL
from analitiq.contracts.stream import STREAM_SCHEMA_URL
from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL
from analitiq.contracts.validation_requests import (
    PACKAGE_MODELS,
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
)

CORPUS = Path(__file__).resolve().parent / "corpus"

_SRC, _DST, _STREAM_ID, _PIPELINE_ID = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
)
_EID = derive_db_endpoint_id(None, "public", "orders")


def _type_map(**sections) -> dict:
    return {"$schema": TYPE_MAP_SCHEMA_URL, **sections}


def _api_endpoint(endpoint_id="v1__records", path="/v1/records", native="STRING", arrow="Utf8") -> dict:
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["endpoint_id"] = endpoint_id
    endpoint["operations"]["read"]["request"]["path"] = path
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": native, "arrow_type": arrow}}
    return endpoint


def _connector_package() -> dict:
    return {
        "definition/connector.json": json.loads((CORPUS / "valid_connector.json").read_text()),
        "definition/type-map.json": _type_map(
            read=[{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]),
        "definition/endpoints/v1__records.json": _api_endpoint(),
    }


def _db_endpoint(endpoint_id=_EID) -> dict:
    return {
        "$schema": DATABASE_ENDPOINT_SCHEMA_URL, "endpoint_id": endpoint_id,
        "database_object": build_database_object(None, "public", "orders"),
        "columns": [{"name": "id", "native_type": "bigint", "arrow_type": "Int64",
                     "nullable": False, "ordinal_position": 1}],
    }


def _connection_package() -> dict:
    return {
        "connection.json": {"$schema": CONNECTION_SCHEMA_URL, "connector_id": "stripe"},
        f"definition/endpoints/{_EID}.json": _db_endpoint(),
    }


def _stream() -> dict:
    return {
        "$schema": STREAM_SCHEMA_URL, "stream_id": _STREAM_ID, "pipeline_id": _PIPELINE_ID,
        "source": {"endpoint_ref": {"scope": "connector", "connection_id": f"{_SRC}_v1",
                                    "endpoint_id": "transfers"}},
        "destinations": [{"endpoint_ref": {"scope": "connector", "connection_id": f"{_DST}_v1",
                                           "endpoint_id": "orders"},
                          "write": {"mode": "insert"}}],
    }


def _pipeline_package() -> dict:
    return {
        "pipeline.json": {
            "$schema": PIPELINE_SCHEMA_URL, "pipeline_id": _PIPELINE_ID,
            "connections": {"source": f"{_SRC}_v1", "destinations": [f"{_DST}_v1"]},
            "streams": [f"{_STREAM_ID}_v2"]},
        "streams/orders.json": _stream(),
    }


_PACKAGES = {
    "connector-package": _connector_package,
    "connection-package": _connection_package,
    "pipeline-package": _pipeline_package,
}


def _request(package: str, documents: dict) -> ValidatePackageRequest:
    return ValidatePackageRequest(
        package=package, documents={key: json.dumps(doc) for key, doc in documents.items()})


def _write(root: Path, documents: dict) -> None:
    for key, doc in documents.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))


def _at(findings: list, message_id: str) -> list[str]:
    return [f["path"] for f in findings if f["message_id"] == message_id]


# ---------------------------------------------------------------------------
# The kind vocabulary is the kinds the published packages locate.
# ---------------------------------------------------------------------------

def test_the_graded_kinds_are_the_kinds_packages_locate(validator):
    from analitiq.validator._core import document_kinds

    located = {kind for model in PACKAGE_MODELS.values() for kind in model.LOCATIONS.values()}
    assert document_kinds() == located


def test_every_package_has_a_package_check(validator):
    from analitiq.validator.document_set import _PACKAGE_CHECKS

    assert set(_PACKAGE_CHECKS) == set(PACKAGE_MODELS)


def test_an_unknown_kind_is_the_callers_error(validator):
    with pytest.raises(ValueError, match="pipeline-bundle"):
        validator.validate_document({}, "pipeline-bundle")


def test_a_document_is_graded_as_the_kind_its_caller_names(validator):
    """No detection: a stream graded as a connector is graded against the
    connector model and fails it, where graded as a stream it passes."""
    as_stream = validator.validate_document(_stream(), "stream")
    as_connector = validator.validate_document(_stream(), "connector")
    assert not any(validator.finding_costs_a_pass(f) for f in as_stream), as_stream
    assert any(validator.finding_costs_a_pass(f) for f in as_connector), as_connector
    assert not _at(as_connector, "entity-mismatch") and not _at(as_connector, "unrecognized-document")


def test_a_single_connector_is_graded_without_siblings(validator):
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    assert validator.validate_document(connector, "connector") == []


def test_single_document_request_grades_as_its_entity(validator):
    result = validator.validate_single_document(
        ValidateSingleDocumentRequest(document=json.dumps(_stream()), entity="connector"))
    assert result["passed"] is False
    assert result["findings"] == validator.validate_document(_stream(), "connector")


# ---------------------------------------------------------------------------
# Packages: the published model decides root and locations.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("package", sorted(_PACKAGES))
def test_a_package_its_model_accepts_passes(validator, package):
    documents = _PACKAGES[package]()
    PACKAGE_MODELS[package].model_validate(documents)
    assert validator.validate_package(_request(package, documents)) == {"passed": True, "findings": []}


@pytest.mark.parametrize("package", sorted(_PACKAGES))
def test_the_disk_route_grades_what_the_request_route_grades(validator, package, tmp_path):
    documents = _PACKAGES[package]()
    documents[f"{PACKAGE_MODELS[package].ROOT}.bak"] = {"not": "located"}
    _write(tmp_path, documents)
    assert validator.validate_package_at(tmp_path, package) == validator.validate_package(
        _request(package, documents))


@pytest.mark.parametrize("package", sorted(_PACKAGES))
def test_a_package_without_its_root_fails_at_the_package(validator, package):
    documents = _PACKAGES[package]()
    del documents[PACKAGE_MODELS[package].ROOT]
    result = validator.validate_package(_request(package, documents))
    assert result["passed"] is False
    assert [f["path"] for f in result["findings"] if validator.finding_costs_a_pass(f)] == [""], result


def test_a_set_that_is_another_package_fails_as_the_declared_one(validator):
    result = validator.validate_package(_request("connector-package", _pipeline_package()))
    assert result["passed"] is False
    assert [f["path"] for f in result["findings"]] == [""], result


def test_an_unlocated_key_is_not_graded(validator):
    documents = {**_connector_package(), "notes/readme.json": {"anything": 1}}
    assert validator.validate_package(_request("connector-package", documents))["passed"] is True


def test_an_unparseable_located_document_is_a_finding_at_its_key(validator):
    texts = {key: json.dumps(doc) for key, doc in _connector_package().items()}
    texts["definition/endpoints/v2 x.json"] = "{not json"
    result = validator.validate_package(
        ValidatePackageRequest(package="connector-package", documents=texts))
    assert _at(result["findings"], "unreadable-document") == ["definition/endpoints/v2%20x.json#"]


def test_an_unreadable_file_is_a_finding_at_its_key(validator, tmp_path):
    documents = _connector_package()
    _write(tmp_path, documents)
    (tmp_path / "definition/endpoints/v1__records.json").write_bytes(b"\xff\xfe")
    result = validator.validate_package_at(tmp_path, "connector-package")
    assert _at(result["findings"], "unreadable-document") == ["definition/endpoints/v1__records.json#"]


def test_an_unknown_package_on_disk_is_the_callers_error(validator, tmp_path):
    with pytest.raises(ValueError):
        validator.validate_package_at(tmp_path, "pipeline-bundle")


# ---------------------------------------------------------------------------
# Connector package checks, each at the document it is about.
# ---------------------------------------------------------------------------

def _connector_findings(validator, documents: dict) -> list:
    return validator.validate_package(_request("connector-package", documents))["findings"]


def test_a_connector_without_a_type_map_is_reported_at_the_connector(validator):
    documents = _connector_package()
    del documents["definition/type-map.json"]
    assert _at(_connector_findings(validator, documents), "read-map-missing") == [
        "definition/connector.json#"]


def test_an_unreadable_type_map_does_not_also_report_it_missing(validator):
    texts = {key: json.dumps(doc) for key, doc in _connector_package().items()}
    texts["definition/type-map.json"] = "{not json"
    findings = validator.validate_package(
        ValidatePackageRequest(package="connector-package", documents=texts))["findings"]
    assert _at(findings, "unreadable-document") == ["definition/type-map.json#"]
    assert not _at(findings, "read-map-missing"), findings


def test_an_api_connector_without_endpoints_is_reported(validator):
    documents = _connector_package()
    del documents["definition/endpoints/v1__records.json"]
    assert _at(_connector_findings(validator, documents), "endpoints-missing") == [
        "definition/connector.json#"]


def test_an_endpoint_named_apart_from_its_id_is_reported_at_it(validator):
    documents = _connector_package()
    documents["definition/endpoints/other.json"] = documents.pop("definition/endpoints/v1__records.json")
    paths = [f["path"] for f in _connector_findings(validator, documents) if f.get("rule") == "RULE-PKG-031"]
    assert paths and all(p.startswith("definition/endpoints/other.json#") for p in paths), paths


def test_duplicate_endpoint_ids_are_reported(validator):
    documents = {**_connector_package(), "definition/endpoints/copy.json": _api_endpoint()}
    assert [f for f in _connector_findings(validator, documents) if f.get("rule") == "RULE-PKG-032"]


def test_an_unresolved_transport_ref_is_reported_at_the_endpoint(validator):
    documents = _connector_package()
    endpoint = documents["definition/endpoints/v1__records.json"]
    endpoint["operations"]["read"]["request"]["transport_ref"] = "nowhere"
    paths = [f["path"] for f in _connector_findings(validator, documents) if f.get("rule") == "RULE-ENDP-047"]
    assert paths and all(p.startswith("definition/endpoints/v1__records.json#") for p in paths), paths


def test_an_uncovered_native_type_is_reported_at_the_endpoint(validator):
    documents = {**_connector_package(),
                 "definition/endpoints/v2__w.json": _api_endpoint("v2__w", "/v2/w", "BOOLEAN", "Boolean")}
    assert _at(_connector_findings(validator, documents), "native-type-unresolved") == [
        "definition/endpoints/v2__w.json#/operations/read/response/schema/items/properties/a"]


# ---------------------------------------------------------------------------
# Connection and pipeline package checks.
# ---------------------------------------------------------------------------

def test_a_database_endpoint_named_apart_from_its_id_is_reported(validator):
    documents = _connection_package()
    documents["definition/endpoints/other.json"] = documents.pop(f"definition/endpoints/{_EID}.json")
    result = validator.validate_package(_request("connection-package", documents))
    paths = [f["path"] for f in result["findings"] if f.get("rule") == "RULE-PKG-031"]
    assert paths and all(p.startswith("definition/endpoints/other.json#") for p in paths), result


def test_a_pipeline_naming_a_stream_the_package_lacks_is_reported(validator):
    documents = _pipeline_package()
    documents["pipeline.json"]["streams"].append("55555555-5555-4555-8555-555555555555_v1")
    findings = validator.validate_package(_request("pipeline-package", documents))["findings"]
    assert [f["path"] for f in findings if f["kind"] == "fail"] == ["pipeline.json#/streams/1"], findings


def test_a_stream_of_another_pipeline_is_reported_at_the_stream(validator):
    documents = _pipeline_package()
    documents["streams/orders.json"]["pipeline_id"] = "66666666-6666-4666-8666-666666666666"
    findings = validator.validate_package(_request("pipeline-package", documents))["findings"]
    assert "streams/orders.json#/pipeline_id" in [f["path"] for f in findings if f["kind"] == "fail"], findings


# ---------------------------------------------------------------------------
# CLI: the caller names the kind or the package.
# ---------------------------------------------------------------------------

def test_cli_grades_a_document_as_its_named_kind(validator_cli, tmp_path):
    path = tmp_path / "anything.json"
    path.write_text(json.dumps(_stream()))
    assert validator_cli.run("--document", str(path), "--kind", "stream").returncode == 0
    assert validator_cli.run("--document", str(path), "--kind", "connector").returncode == 1


def test_cli_grades_a_package(validator_cli, tmp_path):
    _write(tmp_path, _connector_package())
    result = validator_cli.run("--package", str(tmp_path), "--kind", "connector-package")
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout) == {"passed": True, "findings": []}


@pytest.mark.parametrize("argv", [
    ("--document", "x.json"),
    ("--document", "x.json", "--kind", "connector-package"),
    ("--package", ".", "--kind", "connector"),
])
def test_cli_refuses_a_kind_outside_the_vocabulary(validator_cli, argv):
    result = validator_cli.run(*argv)
    assert result.returncode == 2, result
