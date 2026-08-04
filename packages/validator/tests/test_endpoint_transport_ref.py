"""`endpoint-transport-ref` — an endpoint's `request.transport_ref` must name a
transport the sibling connector.json declares (issue #124, repo half).

The connector model's `_transport_refs_resolvable` already gates every
connector-INTERNAL ref site, but an endpoint is a separate document: no
single-document validator can see both sides, so the rule is only checkable from
the connector-anchored walk in `check_coverage`. These tests drive it exactly
that way — through `validate_document(connector, doc_path=...)` over a real
on-disk connector package.
"""
import json
from pathlib import Path

import pytest

CORPUS = Path(__file__).resolve().parent / "corpus"

API = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JS = "https://json-schema.org/draft/2020-12/schema"

# The transport the corpus connector declares (`valid_connector.json`), asserted
# below so this constant cannot silently drift from it.
DECLARED_TRANSPORT = "api"


@pytest.fixture
def connector_base():
    return json.loads((CORPUS / "valid_connector.json").read_text())


def _read_endpoint(transport_ref=..., endpoint_id="widgets", path="/widgets"):
    """A minimal model-valid read endpoint; `transport_ref` omitted entirely
    unless given (the `...` sentinel distinguishes "absent" from an explicit
    `None`, which the contract also allows and which means default_transport)."""
    request = {"method": "GET", "path": path}
    if transport_ref is not ...:
        request["transport_ref"] = transport_ref
    return {
        "$schema": API, "endpoint_id": endpoint_id,
        "operations": {"read": {
            "request": request, "params": {},
            "response": {
                "records": {"ref": "response.body"},
                "schema": {"$schema": JS, "type": "array", "items": {
                    "type": "object", "properties": {"a": {
                        "type": "string", "native_type": "STRING",
                        "arrow_type": "Utf8"}}}},
            }}}}


def _write_endpoint(transport_ref=..., endpoint_id="widgets", path="/widgets"):
    request = {"method": "POST", "path": path,
               "headers": {"Content-Type": "application/json"},
               "body": {"r": {"from_input": "record"}}}
    if transport_ref is not ...:
        request["transport_ref"] = transport_ref
    return {
        "$schema": API, "endpoint_id": endpoint_id,
        "operations": {"write": {"insert": {
            "request": request, "params": {},
            "input": {"schema": {"$schema": JS, "type": "object", "properties": {
                "a": {"type": "string", "native_type": "STRING",
                      "arrow_type": "Utf8"}}}},
        }}}}


def _write_tree(root: Path, connector: dict, endpoints: dict):
    (root / "endpoints").mkdir(parents=True)
    (root / "connector.json").write_text(json.dumps(connector))
    (root / "type-map-read.json").write_text(json.dumps(
        [{"match": "exact", "native": "STRING", "canonical": "Utf8"}]))
    for name, ep in endpoints.items():
        (root / "endpoints" / name).write_text(
            ep if isinstance(ep, str) else json.dumps(ep))


def _run(tmp_path, connector, endpoints, validator):
    _write_tree(tmp_path, connector, endpoints)
    return validator.validate_document(connector, doc_path=tmp_path / "connector.json")


def _ref_errors(findings):
    return [f for f in findings
            if f["validator"] == "endpoint-transport-ref" and f["severity"] == "error"]


def _errors(findings):
    return [f for f in findings if f["severity"] == "error"]


def test_corpus_connector_declares_the_expected_transport(connector_base):
    # Pins DECLARED_TRANSPORT to the corpus fixture: if the corpus renames its
    # transport, these tests fail loudly here rather than passing vacuously.
    assert list(connector_base["transports"]) == [DECLARED_TRANSPORT]


def test_declared_transport_ref_passes(tmp_path, connector_base, validator):
    findings = _run(tmp_path, connector_base,
                    {"widgets.json": _read_endpoint(DECLARED_TRANSPORT)}, validator)
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_absent_transport_ref_passes(tmp_path, connector_base, validator):
    # No `transport_ref` at all -> the connector's default_transport; nothing to resolve.
    findings = _run(tmp_path, connector_base,
                    {"widgets.json": _read_endpoint()}, validator)
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_null_transport_ref_passes(tmp_path, connector_base, validator):
    # An explicit null is the same statement as omitting it.
    findings = _run(tmp_path, connector_base,
                    {"widgets.json": _read_endpoint(None)}, validator)
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_unknown_transport_ref_on_read_errors(tmp_path, connector_base, validator):
    findings = _run(tmp_path, connector_base,
                    {"widgets.json": _read_endpoint("nope")}, validator)
    errors = _ref_errors(findings)
    assert len(errors) == 1, findings
    (err,) = errors
    assert err["path"] == "/operations/read/request/transport_ref"
    assert "widgets.json/operations/read/request/transport_ref" in err["message"]
    assert "'nope'" in err["message"]
    assert f"['{DECLARED_TRANSPORT}']" in err["message"]  # the declared set is listed
    assert "§Transport Selection" in err["message"]


def test_unknown_transport_ref_on_write_mode_errors(tmp_path, connector_base, validator):
    findings = _run(tmp_path, connector_base,
                    {"widgets.json": _write_endpoint("nope")}, validator)
    errors = _ref_errors(findings)
    assert len(errors) == 1, findings
    (err,) = errors
    assert err["path"] == "/operations/write/insert/request/transport_ref"
    assert "widgets.json/operations/write/insert/request/transport_ref" in err["message"]


def test_every_operation_is_checked_independently(tmp_path, connector_base, validator):
    # A doc with a good read ref and a bad write ref reports exactly the bad one.
    ep = _read_endpoint(DECLARED_TRANSPORT)
    write = _write_endpoint("nope")
    ep["operations"]["write"] = write["operations"]["write"]
    findings = _run(tmp_path, connector_base, {"widgets.json": ep}, validator)
    paths = [e["path"] for e in _ref_errors(findings)]
    assert paths == ["/operations/write/insert/request/transport_ref"]


def test_each_endpoint_file_is_checked(tmp_path, connector_base, validator):
    findings = _run(tmp_path, connector_base, {
        "widgets.json": _read_endpoint("nope", endpoint_id="widgets", path="/widgets"),
        "gadgets.json": _read_endpoint(DECLARED_TRANSPORT, endpoint_id="gadgets",
                                       path="/gadgets"),
    }, validator)
    messages = [e["message"] for e in _ref_errors(findings)]
    assert len(messages) == 1, messages
    assert "widgets.json" in messages[0]


@pytest.mark.parametrize("ep", [
    "[]",                                             # a JSON array, not an object
    "\"just a string\"",                              # a JSON scalar
    json.dumps({"endpoint_id": "widgets"}),           # no `operations` at all
    json.dumps({"endpoint_id": "widgets", "operations": "nope"}),
    json.dumps({"endpoint_id": "widgets", "operations": {"read": "nope"}}),
    json.dumps({"endpoint_id": "widgets",
                "operations": {"read": {"request": "nope"}}}),
    json.dumps({"endpoint_id": "widgets",
                "operations": {"write": "nope"}}),
    json.dumps({"endpoint_id": "widgets",
                "operations": {"write": {"insert": {"request": {
                    "transport_ref": ["not", "a", "string"]}}}}}),
])
def test_malformed_endpoint_does_not_crash_the_check(tmp_path, connector_base, ep, validator):
    """check_coverage runs over documents that may already carry model errors; a
    crash here would replace every actionable finding with a generic
    "validator bug". Each malformed shape must still produce findings and no
    fabricated transport-ref error."""
    findings = _run(tmp_path, connector_base, {"widgets.json": ep}, validator)
    assert not any("validator bug" in f["message"] for f in findings), findings
    assert not _ref_errors(findings), findings
    assert _errors(findings), "a malformed endpoint must still be reported"


def test_malformed_connector_transports_yields_no_fabricated_finding(
        tmp_path, connector_base, validator):
    """If `transports` itself is not a map the connector's own model error is the
    real report; this check stays quiet rather than burying it."""
    connector_base["transports"] = "not-a-map"
    findings = _run(tmp_path, connector_base,
                    {"widgets.json": _read_endpoint("nope")}, validator)
    assert not _ref_errors(findings), findings
    assert _errors(findings), "the malformed connector must still be reported"
