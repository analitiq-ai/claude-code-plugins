"""`endpoint-transport-ref` — an endpoint's `request.transport_ref` must name a
transport the sibling connector.json declares.

The connector model's `_transport_refs_resolvable` already gates every
connector-INTERNAL ref site, but an endpoint is a separate document: no
single-document validator can see both sides, so the rule is only checkable from
the connector-anchored walk in `check_coverage`. These tests drive it exactly
that way — through `validate_document(connector, doc_path=...)` over a real
on-disk connector package.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

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


class TestOriginContainmentGapIsRecorded:
    """`CONTRIBUTING.md` → "A fix that narrows a rule records what it
    deliberately left wide": the record ships with the narrowing, as a test or
    a follow-up issue.

    `transport_ref`'s containment rule has two halves. The NAME half is enforced
    here. The ORIGIN half — every URL a request produces landing on a declared
    transport's origin — is enforced by nothing: not this contract, not this
    validator, and not the engine, which opens one session from
    `default_transport`, pins the read path to that single origin and has no
    write-path origin guard at all.

    Each test below asserts the CURRENT behaviour, not the desired one: a
    document the ORIGIN half would refuse, accepted. They run through the
    connector-anchored walk, which is where an origin check would have to live
    for the same reason the NAME half does — origins are declared on the
    connector and consumed by the endpoint. Three documents, because they reach
    the walk by different legs and a check need not cover all three: a read
    request, a write request, and a next-page URL the document takes from the
    response body.

    Each asserts on EVERY error the walk emits, not on `endpoint-transport-ref`
    alone: the NAME half already owns that id, so an origin rule arriving under
    an id of its own — the likelier shape, since each check registers one —
    would pass a scoped assertion unnoticed.

    That the field description still declares the half unenforced is a reader's
    check, not this module's: deciding it means reading what a description
    means, which `.claude/rules/guards.md` keeps out of tests and
    `.claude/rules/contract-prose.md` states as an authoring obligation. The
    description lives on `_RequestBase.transport_ref`, which every
    endpoint-operation request model — read and write alike — inherits, and its
    census entry records the ORIGIN half as its waiver, so softening the
    disclaimer is a hash mismatch a reviewer must re-affirm. The connector
    document declares its own `transport_ref` sites — the auth operation
    template, the post-auth operation request, resource discovery — whose
    descriptions state no origin half at all and whose waivers record
    engine-owned defaulting instead.
    """

    def test_a_second_origin_is_accepted_because_nothing_checks_origins(
        self, tmp_path, connector_base, validator
    ):
        # The connector declares a second transport on its own origin, and the
        # endpoint DECLARES dispatch through it rather than through
        # `default_transport`. The NAME half is satisfied — the transport is
        # declared — so the document states an origin nothing can currently
        # reach, and it validates clean: the engine opens one session from
        # `default_transport` and no production call site selects a transport
        # per operation. The ref is read here only to grade the name.
        _declare_second_origin(connector_base)
        findings = _run(
            tmp_path,
            connector_base,
            {"widgets.json": _read_endpoint(SECOND_TRANSPORT)},
            validator,
        )
        assert not _errors(findings), findings

    def test_a_second_origin_on_the_write_path_is_accepted_too(
        self, tmp_path, connector_base, validator
    ):
        # The read path's single-origin pinning does not extend to the write
        # path, which has no origin guard of its own, so recording only the
        # read one would leave half the gap unrecorded.
        _declare_second_origin(connector_base)
        findings = _run(
            tmp_path,
            connector_base,
            {"widgets.json": _write_endpoint(SECOND_TRANSPORT)},
            validator,
        )
        assert not _errors(findings), findings

    def test_a_response_driven_next_url_is_accepted_because_nothing_checks_origins(
        self, tmp_path, connector_base, validator
    ):
        # The next-page URL is read out of the response body, so the document
        # bounds it by no origin at all — which is why
        # `pagination.link.next_url` is named in the description's ORIGIN half.
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
        findings = _run(tmp_path, connector_base, {"widgets.json": endpoint}, validator)
        assert not _errors(findings), findings


class TestStandaloneEndpointValidation:
    """`_validate_api_endpoint` — the path the connector-builder skill actually
    takes when it validates one endpoint file at a time.

    Every other test in this module drives `check_coverage` via the connector.
    That is why two defects shipped here unnoticed: a connector whose
    `transports` was unusable produced NO finding at all (a clean pass on an
    endpoint whose `transport_ref` resolves to nothing), and a connector that
    would not parse was reported as a `type-map-coverage` ERROR against an
    otherwise-valid endpoint, under an id the fix loop does not filter on.
    """

    def _endpoint(self, ref="api"):
        return {
            "$schema": "https://schemas.analitiq.ai/api-endpoint/latest.json",
            "endpoint_id": "thing",
            "operations": {
                "read": {
                    "request": {"method": "GET", "path": "/v1/things", "transport_ref": ref},
                    "params": {},
                    "response": {
                        "records": {"ref": "response.body.data"},
                        "schema": {
                            "type": "object",
                            "properties": {"data": {
                                "type": "array",
                                "items": {"type": "object",
                                          "properties": {"id": {"type": "string"}}},
                            }},
                        },
                    },
                }
            },
        }

    def _run(self, tmp_path, connector_body):
        from analitiq.validator.connectors import _validate_api_endpoint

        pkg = tmp_path / "pkg"
        (pkg / "endpoints").mkdir(parents=True)
        if connector_body is not None:
            (pkg / "connector.json").write_text(connector_body)
        doc_path = pkg / "endpoints" / "thing.json"
        doc = self._endpoint()
        doc_path.write_text(json.dumps(doc))
        return _validate_api_endpoint(doc, doc_path, None)

    def _ids(self, findings):
        return {(f["validator"], f["severity"]) for f in findings}

    def test_declared_transport_resolves_clean(self, tmp_path):
        findings = self._run(tmp_path, '{"kind":"api","transports":{"api":{}}}')
        assert not [f for f in findings if f["validator"] == "endpoint-transport-ref"]

    def test_undeclared_transport_is_an_error(self, tmp_path):
        findings = self._run(tmp_path, '{"kind":"api","transports":{"other":{}}}')
        assert ("endpoint-transport-ref", "error") in self._ids(findings)

    def test_connector_without_transports_warns_rather_than_passing_clean(self, tmp_path):
        # The silent-clean-pass case. `_endpoint_transport_ref_findings` returns
        # [] here, which is right when the CONNECTOR is under validation (its own
        # model error stands) and wrong here, where that model never runs.
        findings = self._run(tmp_path, '{"kind":"api"}')
        assert ("endpoint-transport-ref", "warning") in self._ids(findings)

    def test_connector_with_non_dict_transports_warns(self, tmp_path):
        findings = self._run(tmp_path, '{"kind":"api","transports":[]}')
        assert ("endpoint-transport-ref", "warning") in self._ids(findings)

    def test_absent_connector_warns(self, tmp_path):
        findings = self._run(tmp_path, None)
        assert ("endpoint-transport-ref", "warning") in self._ids(findings)

    def test_unparseable_connector_is_reported_under_this_checks_own_id(self, tmp_path):
        findings = self._run(tmp_path, "{not json")
        reported = {f["validator"] for f in findings}
        assert "endpoint-transport-ref" in reported
        assert "type-map-coverage" not in reported, (
            "a connector-read failure surfaced under the type-map id; a fix loop "
            "filtering on endpoint-transport-ref would never see it"
        )

    def test_unparseable_connector_is_not_described_as_unreachable(self, tmp_path):
        """The file was found and read; only the parse failed. Reporting it as
        "no sibling connector.json was reachable" contradicts the parse error
        emitted beside it under the same id, and points the author at the wrong
        problem."""
        findings = self._run(tmp_path, "{not json")
        warnings = [
            f for f in findings
            if f["validator"] == "endpoint-transport-ref" and f["severity"] == "warning"
        ]
        assert warnings, "expected a not-checked warning"
        assert not any("was reachable" in f["message"] for f in warnings)
        assert any("could not be parsed" in f["message"] for f in warnings)

    @pytest.mark.parametrize("shape", ["relative", "dotdot"])
    def test_a_non_absolute_document_path_still_finds_the_sibling(
        self, tmp_path, monkeypatch, shape
    ):
        """`Path("thing.json").parent.parent` is `.`, so a relative `--document`
        run from inside `endpoints/` missed the connector entirely and downgraded
        a genuinely broken `transport_ref` to a warning — a silent pass on the
        one check this adds."""
        from analitiq.validator.connectors import _validate_api_endpoint

        pkg = tmp_path / "pkg"
        (pkg / "endpoints").mkdir(parents=True)
        (pkg / "connector.json").write_text('{"kind":"api","transports":{"other":{}}}')
        doc = self._endpoint()
        (pkg / "endpoints" / "thing.json").write_text(json.dumps(doc))

        monkeypatch.chdir(pkg / "endpoints")
        doc_path = (
            Path("thing.json") if shape == "relative"
            else Path("..") / "endpoints" / "thing.json"
        )
        findings = _validate_api_endpoint(doc, doc_path, None)
        assert ("endpoint-transport-ref", "error") in self._ids(findings), (
            "the undeclared transport_ref was downgraded to a warning because "
            "the sibling lookup missed"
        )
