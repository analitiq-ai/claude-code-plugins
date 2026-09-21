"""RULE-ENDP-047 — an endpoint's `request.transport_ref` must name a
transport the package's connector declares.

The connector model's `_transport_refs_resolvable` already gates every
connector-INTERNAL ref site, but an endpoint is a separate document: no
single-document validator can see both sides, so the rule is checked by the
connector package check, where both documents are in hand. The tests here
grade connector packages through `validate_package`.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL
from analitiq.contracts.validation_requests import ConnectorPackage, ValidatePackageRequest

CORPUS = Path(__file__).resolve().parent / "corpus"

API = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JS = "https://json-schema.org/draft/2020-12/schema"

# The transport the corpus connector declares (`valid_connector.json`), asserted
# below so this constant cannot silently drift from it.
DECLARED_TRANSPORT = "api"

# The second transport the origin-containment record declares, and the origin
# it puts it on.
SECOND_TRANSPORT = "cdn"
SECOND_ORIGIN = "https://cdn.example.test"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _declare_second_origin(connector: dict) -> None:
    """Add `SECOND_TRANSPORT` on an origin the default transport is not on.

    Comparing the two transport NAMES would not establish a second origin: a
    corpus whose default moved to this host would state one origin under two
    names, and the record would pass while recording nothing. So the default's
    own `base_url` is read, and it must carry a scheme and a host that are
    knowable from the document — a scheme-less string has no origin to compare,
    the object value-expression arms resolve at connection time, and a bare
    string may still bear `${...}` placeholders that do the same.
    """
    default = connector["transports"][connector["default_transport"]]
    base_url = default.get("base_url")
    parts = urlsplit(base_url) if isinstance(base_url, str) else None
    assert (
        parts is not None
        and parts.scheme
        and parts.netloc
        and "${" not in base_url
        and _origin(base_url) != SECOND_ORIGIN
    ), (
        "the corpus default transport must declare a base_url whose origin is "
        f"knowable here and is not {SECOND_ORIGIN}; it declares {base_url!r}, "
        "so the transport added below states no second origin and the record "
        "records nothing"
    )
    connector["transports"][SECOND_TRANSPORT] = {
        "transport_type": "http",
        "base_url": SECOND_ORIGIN + "/v1",
        "timeout_seconds": 30,
    }


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


def _run(connector, endpoints, validator):
    documents = {
        ConnectorPackage.ROOT: json.dumps(connector),
        "definition/type-map.json": json.dumps({
            "$schema": TYPE_MAP_SCHEMA_URL,
            "read": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        }),
        **{f"definition/endpoints/{name}": ep if isinstance(ep, str) else json.dumps(ep)
           for name, ep in endpoints.items()},
    }
    return validator.validate_package(ValidatePackageRequest(
        package="connector-package", documents=documents))["findings"]


def _ref_errors(findings):
    return [f for f in findings
            if f.get("rule") == "RULE-ENDP-047" and f.get("severity") == "error"]


def _errors(findings):
    return [f for f in findings if f["severity"] == "error"]


def test_corpus_connector_declares_the_expected_transport(connector_base):
    # Pins DECLARED_TRANSPORT to the corpus fixture: if the corpus renames its
    # transport, these tests fail loudly here rather than passing vacuously.
    assert list(connector_base["transports"]) == [DECLARED_TRANSPORT]


def test_declared_transport_ref_passes(connector_base, validator):
    findings = _run(connector_base,
                    {"widgets.json": _read_endpoint(DECLARED_TRANSPORT)}, validator)
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_absent_transport_ref_passes(connector_base, validator):
    # No `transport_ref` at all -> the connector's default_transport; nothing to resolve.
    findings = _run(connector_base,
                    {"widgets.json": _read_endpoint()}, validator)
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_null_transport_ref_passes(connector_base, validator):
    # An explicit null is the same statement as omitting it.
    findings = _run(connector_base,
                    {"widgets.json": _read_endpoint(None)}, validator)
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_unknown_transport_ref_on_read_errors(connector_base, validator):
    findings = _run(connector_base,
                    {"widgets.json": _read_endpoint("nope")}, validator)
    errors = _ref_errors(findings)
    assert len(errors) == 1, findings
    (err,) = errors
    assert err["path"] == "definition/endpoints/widgets.json#/operations/read/request/transport_ref"
    assert "'nope'" in err["message"]
    assert f"['{DECLARED_TRANSPORT}']" in err["message"]  # the declared set is listed
    assert "§Transport Selection" in err["message"]


def test_unknown_transport_ref_on_write_mode_errors(connector_base, validator):
    findings = _run(connector_base,
                    {"widgets.json": _write_endpoint("nope")}, validator)
    errors = _ref_errors(findings)
    assert len(errors) == 1, findings
    (err,) = errors
    assert err["path"] == "definition/endpoints/widgets.json#/operations/write/insert/request/transport_ref"


def test_every_operation_is_checked_independently(connector_base, validator):
    # A doc with a good read ref and a bad write ref reports exactly the bad one.
    ep = _read_endpoint(DECLARED_TRANSPORT)
    write = _write_endpoint("nope")
    ep["operations"]["write"] = write["operations"]["write"]
    findings = _run(connector_base, {"widgets.json": ep}, validator)
    paths = [e["path"] for e in _ref_errors(findings)]
    assert paths == ["definition/endpoints/widgets.json#/operations/write/insert/request/transport_ref"]


def test_each_endpoint_file_is_checked(connector_base, validator):
    findings = _run(connector_base, {
        "widgets.json": _read_endpoint("nope", endpoint_id="widgets", path="/widgets"),
        "gadgets.json": _read_endpoint(DECLARED_TRANSPORT, endpoint_id="gadgets",
                                       path="/gadgets"),
    }, validator)
    paths = [e["path"] for e in _ref_errors(findings)]
    assert paths == ["definition/endpoints/widgets.json#/operations/read/request/transport_ref"], paths


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
def test_malformed_endpoint_does_not_crash_the_check(connector_base, ep, validator):
    """The package check runs over documents that may already carry model errors; a
    crash here would replace every actionable finding with a generic
    "validator bug". Each malformed shape must still produce findings and no
    fabricated transport-ref error."""
    findings = _run(connector_base, {"widgets.json": ep}, validator)
    assert not any("validator bug" in f["message"] for f in findings), findings
    assert not _ref_errors(findings), findings
    assert _errors(findings), "a malformed endpoint must still be reported"


def test_malformed_connector_transports_yields_no_fabricated_finding(
        connector_base, validator):
    """If `transports` itself is not a map the connector's own model error is the
    real report; this check stays quiet rather than burying it."""
    connector_base["transports"] = "not-a-map"
    findings = _run(connector_base,
                    {"widgets.json": _read_endpoint("nope")}, validator)
    assert not _ref_errors(findings), findings
    assert _errors(findings), "the malformed connector must still be reported"


class TestOriginContainmentIsAValidatorBlindSpot:
    """`CONTRIBUTING.md` → "A fix that narrows a rule records what it
    deliberately left wide": the record ships with the narrowing, as a test or
    a follow-up issue.

    `transport_ref`'s containment rule has two halves. The NAME half is
    enforced here. The ORIGIN half — every URL a request produces landing on
    the origin of the transport actually selected for the operation — is
    enforced by the engine at run time, on both the read and the write path,
    resolved per operation. It is enforced by neither this contract nor this
    validator: the package check grades a `transport_ref` by name only, and an
    offline check never sees a response, so it cannot follow a response-driven
    next-page URL to see where it lands.

    Each test below asserts the CURRENT behaviour of THIS validator, not of
    the engine: a document the ORIGIN half would refuse at run time still
    validates clean here. They run through the connector package check, which
    is where an origin check would have to live for the same reason the NAME
    half does — origins are declared on the connector and consumed by the
    endpoint. Three documents, because they reach the check by different legs
    and a check need not cover all three: a read request, a write request, and
    a next-page URL the document takes from the response body.

    Each asserts on EVERY error the package emits, not on `RULE-ENDP-047`
    alone: the NAME half already owns that id, so an origin rule arriving under
    an id of its own — the likelier shape, since each check registers one —
    would pass a scoped assertion unnoticed.

    That the field description still describes this split correctly is a
    reader's check, not this module's: deciding it means reading what a
    description means, which `.claude/rules/guards.md` keeps out of tests and
    `.claude/rules/contract-prose.md` states as an authoring obligation. The
    description lives on `_RequestBase.transport_ref`, which every
    endpoint-operation request model — read and write alike — inherits, and its
    census entry records the ORIGIN half as engine-conduct, so changing the
    disclaimer is a hash mismatch a reviewer must re-affirm. The connector
    document declares its own `transport_ref` sites — the auth operation
    template, the post-auth operation request, resource discovery — whose
    descriptions state no origin half at all and whose waivers record
    engine-owned defaulting instead.
    """

    def test_a_second_origin_is_accepted_because_this_validator_checks_names_not_origins(
        self, connector_base, validator
    ):
        # The connector declares a second transport on its own origin, and the
        # endpoint DECLARES dispatch through it rather than through
        # `default_transport`. The NAME half is satisfied — the transport is
        # declared — so this offline validator, which cannot resolve which
        # origin a transport_ref names without walking the response the
        # engine would send, validates the document clean. The ref is read
        # here only to grade the name.
        _declare_second_origin(connector_base)
        findings = _run(
            connector_base,
            {"widgets.json": _read_endpoint(SECOND_TRANSPORT)},
            validator,
        )
        assert not _errors(findings), findings

    def test_a_second_origin_on_the_write_path_is_accepted_too(
        self, connector_base, validator
    ):
        # This validator's blind spot is not read-path-specific — it never
        # resolves an origin on either path — so recording only the read one
        # would leave half the gap unrecorded.
        _declare_second_origin(connector_base)
        findings = _run(
            connector_base,
            {"widgets.json": _write_endpoint(SECOND_TRANSPORT)},
            validator,
        )
        assert not _errors(findings), findings

    def test_a_response_driven_next_url_is_accepted_because_this_validator_checks_names_not_origins(
        self, connector_base, validator
    ):
        # The next-page URL is read out of the response body, so this
        # validator — which never fetches a response — has no origin to check
        # it against, which is why `pagination.link.next_url` is named in the
        # description's ORIGIN half.
        endpoint = _read_endpoint(DECLARED_TRANSPORT)
        read = endpoint["operations"]["read"]
        read["pagination"] = {
            "type": "link",
            "link": {"next_url": {"ref": "response.body.next"}},
            "stop_when": {"missing": {"ref": "response.body.next"}},
        }
        read["response"] = {
            "records": {"ref": "response.body.data"},
            "schema": {
                "$schema": JS, "type": "object",
                "properties": {
                    "next": {"type": "string"},
                    "data": {"type": "array", "items": {
                        "type": "object", "properties": {"a": {
                            "type": "string", "native_type": "STRING",
                            "arrow_type": "Utf8"}}}},
                },
            },
        }
        findings = _run(connector_base, {"widgets.json": endpoint}, validator)
        assert not _errors(findings), findings
