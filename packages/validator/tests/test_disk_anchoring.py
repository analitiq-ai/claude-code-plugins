"""A document on disk is graded as part of the layout its author wrote.

A linked file stands where the link is, not where its bytes live: the package a
document belongs to is the directory that holds the link. The same goes for a
path spelled through `..` or relative to the working directory — it names one
place, and the checks read the siblings at that place.
"""
from __future__ import annotations

import json
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / "corpus"
_H = "https://schemas.analitiq.ai"


def _corpus(name: str) -> dict:
    return json.loads((CORPUS / name).read_text())


def _endpoint(endpoint_id: str = "thing", transport_ref: str = "api") -> dict:
    doc = _corpus("valid_read.json")
    doc["endpoint_id"] = endpoint_id
    doc["operations"]["read"]["request"]["path"] = "/" + endpoint_id.replace("__", "/")
    doc["operations"]["read"]["request"]["transport_ref"] = transport_ref
    return doc


def _endpoint_047(findings: list[dict]) -> list[tuple[str, str]]:
    return [(f["kind"], f["message_id"]) for f in findings if f.get("rule") == "RULE-ENDP-047"]


def test_a_linked_endpoint_is_graded_against_the_connector_beside_the_link(tmp_path, validator):
    pkg, shared = tmp_path / "pkg", tmp_path / "shared"
    (pkg / "endpoints").mkdir(parents=True)
    shared.mkdir()
    (pkg / "connector.json").write_text(json.dumps(
        {**_corpus("valid_connector.json"), "transports": {"other": {}}}))
    doc = _endpoint()
    (shared / "thing.json").write_text(json.dumps(doc))
    (pkg / "endpoints" / "thing.json").symlink_to(shared / "thing.json")

    findings = validator.validate_document(doc, doc_path=pkg / "endpoints" / "thing.json")

    assert ("fail", "transport-ref-undeclared") in _endpoint_047(findings), findings


def test_the_cli_grades_a_linked_connector_with_the_package_beside_the_link(
        tmp_path, validator, validator_cli):
    def package(root: Path) -> None:
        (root / "endpoints").mkdir(parents=True)
        (root / "type-map-read.json").write_text(json.dumps({
            "$schema": f"{_H}/type-map-read/latest.json", "direction": "read",
            "rules": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}))
        endpoint = _endpoint("v1__records")
        endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
            "a": {"type": "string", "native_type": "STRING", "arrow_type": "Utf8"}}
        (root / "endpoints" / "v1__records.json").write_text(json.dumps(endpoint))

    connector = json.dumps(_corpus("valid_connector.json"))
    regular, linked, shared = tmp_path / "regular", tmp_path / "linked", tmp_path / "shared"
    package(regular)
    (regular / "connector.json").write_text(connector)
    package(linked)
    shared.mkdir()
    (shared / "connector.json").write_text(connector)
    (linked / "connector.json").symlink_to(shared / "connector.json")

    expected = validator_cli.run("--document", str(regular / "connector.json"))
    got = validator_cli.run("--document", str(linked / "connector.json"))

    assert json.loads(expected.stdout)["passed"], expected.stdout
    assert (got.returncode, json.loads(got.stdout)) == (expected.returncode, json.loads(expected.stdout))


def test_an_endpoint_outside_an_endpoints_directory_reads_no_connector(tmp_path, validator):
    """The lookup is for `endpoints/{id}.json` one level below its connector.
    Anywhere else, two levels up is outside any package, so the connector found
    there says nothing about this endpoint."""
    (tmp_path / "connector.json").write_text(json.dumps(
        {**_corpus("valid_connector.json"), "transports": {"other": {}}}))
    (tmp_path / "pkg").mkdir()
    doc = _endpoint()
    (tmp_path / "pkg" / "thing.json").write_text(json.dumps(doc))

    findings = validator.validate_document(doc, doc_path=tmp_path / "pkg" / "thing.json")

    assert _endpoint_047(findings) == [
        ("notApplicable", "transport-ref-check-skipped-no-sibling")], findings
