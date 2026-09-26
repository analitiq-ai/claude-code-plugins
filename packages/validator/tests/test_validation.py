"""End-to-end validation tests — the validator delegates single-document
validity to the contract models and adds the cross-document coverage checks.

A single document is graded through `validate_single_document` as the kind the
test names; a cross-document check through `validate_package` over a connector
package.

The `invalid_write_from_input` case is the original sevdesk defect that started
this work: a write body mapping a bare field name (`{from_input: "category"}`)
instead of the record. The model rejects it, so the validator now catches it —
the gap the old validator missed.
"""
import json
import re
import signal
import time
from pathlib import Path

import pytest

from analitiq.contracts.connection import CONNECTION_SCHEMA_URL
from analitiq.contracts.connector import CONNECTOR_SCHEMA_URL
from analitiq.contracts.endpoint_identity import derive_db_endpoint_id, slug
from analitiq.contracts.endpoints import _REFUSED_REFERENCE_KEYWORDS
from analitiq.contracts.pipelines.config import PIPELINE_SCHEMA_URL
from analitiq.contracts.stream import STREAM_SCHEMA_URL
from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL
from analitiq.contracts.validation_requests import (
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
)
from analitiq.validator.connectors import (
    _DATABASE_KINDS,
    _STORAGE_KINDS,
)

CORPUS = Path(__file__).resolve().parent / "corpus"

_CONNECTOR_KEY = "definition/connector.json"
_TYPE_MAP_KEY = "definition/type-map.json"


def _endpoint_key(filename: str) -> str:
    return f"definition/endpoints/{filename}"


class _RawText(str):
    """Document text a request carries as it is, rather than serialized from
    a parsed document — how a test hands over text that does not parse."""


def _request_text(document) -> str:
    return document if isinstance(document, _RawText) else json.dumps(document)


def _document_findings(validator, document, document_kind: str) -> list[dict]:
    """Every finding for `document` validated alone as `document_kind`."""
    return validator.validate_single_document(ValidateSingleDocumentRequest(
        document=_request_text(document), document_kind=document_kind))["findings"]


def _package_findings(validator, documents: dict) -> list[dict]:
    """Every finding for `documents`, keyed by location, validated as one
    connector package."""
    return validator.validate_package(ValidatePackageRequest(
        package_kind="connector",
        documents={key: _request_text(doc) for key, doc in documents.items()}))["findings"]


def _about(findings, key: str) -> list[dict]:
    """The findings whose `path` names the document at `key`."""
    return [f for f in findings if f["path"].split("#")[0] == key]


# (corpus file, kind, expected pass?) — single-document verdicts.
DOC_CASES = [
    ("valid_read.json", "api-endpoint", True),
    ("valid_write_insert.json", "api-endpoint", True),
    ("valid_connector_sync_driver.json", "connector", True),
    ("invalid_reserved_field.json", "api-endpoint", False),
    ("invalid_write_from_input.json", "api-endpoint", False),
    ("invalid_connector_bare_driver.json", "connector", False),
]


def _errors(findings):
    return [f for f in findings if f.get("severity") == "error"]


@pytest.mark.parametrize("name,kind,should_pass", DOC_CASES)
def test_single_document_verdict(name, kind, should_pass, validator):
    doc = json.loads((CORPUS / name).read_text())
    findings = _document_findings(validator, doc, kind)
    errors = _errors(findings)
    assert (not errors) == should_pass, (
        f"{name}: expected {'pass' if should_pass else 'fail'}, "
        f"got errors={[e['message'] for e in errors]}"
    )


def test_from_input_defect_is_caught(validator):
    """The sevdesk regression: a bare-field from_input write body must be rejected."""
    doc = json.loads((CORPUS / "invalid_write_from_input.json").read_text())
    findings = _document_findings(validator, doc, "api-endpoint")
    assert any(
        "from_input" in f["message"] and f["severity"] == "error" for f in findings
    ), "the from_input contract rule was not enforced"


def test_bare_sqlalchemy_driver_is_a_contract_model_finding(validator):
    """The sync-driver boundary — the driver pattern was async-only and now
    accepts a sync DBAPI too, so long as the value stays a full `dialect+driver`
    pair — exercised end-to-end: the valid corpus twin
    (`valid_connector_sync_driver.json`, driver `redshift+redshift_connector`)
    passes in DOC_CASES above; here the bare
    variant (no `dialect+` segment) must surface as a contract-model finding
    on the transport's driver field — the model rejection reaching a consumer
    of validate_single_document, not just the pydantic layer."""
    doc = json.loads((CORPUS / "invalid_connector_bare_driver.json").read_text())
    errors = _errors(_document_findings(validator, doc, "connector"))
    assert any(
        f.get("rule") is None and f["path"].endswith("/driver")
        for f in errors
    ), errors


def test_a_connector_without_its_kind_fails_the_model(validator):
    # `kind` is the connector union's discriminator, so a connector missing it
    # is a model failure, not a pass.
    doc = {"connector_id": "x", "transports": {}, "connection_contract": {},
           "default_transport": "m"}
    assert _errors(_document_findings(validator, doc, "connector"))


def test_a_connector_without_its_kind_still_reports_a_missing_schema_url(validator):
    # `Connector.schema_url` is optional, so RULE-SHRD-003 is the only thing
    # that reports its omission, and a connector the model cannot discriminate
    # is still told about it.
    doc = {"connector_id": "x", "transports": {}, "connection_contract": {},
           "default_transport": "m"}
    findings = _document_findings(validator, doc, "connector")
    assert any(f.get("rule") == "RULE-SHRD-003" for f in findings), findings


_TM_SCHEMA = TYPE_MAP_SCHEMA_URL


def _type_map_doc(read=None, write=None):
    """A `type-map.json` document carrying each section given."""
    sections = {"read": read, "write": write}
    return {"$schema": _TM_SCHEMA, **{d: r for d, r in sections.items() if r is not None}}


def _connector_package(connector: dict, read_map, endpoints: dict) -> dict:
    """A connector package: `connector`, a type map carrying `read_map` as its
    read section, and each endpoint under its filename."""
    return {
        _CONNECTOR_KEY: connector,
        _TYPE_MAP_KEY: _type_map_doc(read=read_map),
        **{_endpoint_key(name): ep for name, ep in endpoints.items()},
    }


API = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JS = "https://json-schema.org/draft/2020-12/schema"


def _endpoint(native_type, arrow_type, endpoint_id="widgets", path="/widgets"):
    # Delegates to `_read_endpoint` (defined below — resolved at call time,
    # not at def time, so the forward reference is fine) rather than building
    # a second copy of the same read-endpoint skeleton.
    return _read_endpoint(
        {"$schema": JS, "type": "array", "items": {"type": "object",
            "properties": {"a": {"type": "string",
                "native_type": native_type, "arrow_type": arrow_type}}}},
        endpoint_id, path,
    )


def test_valid_embedded_schema_passes(validator):
    ep = _endpoint("STRING", "Utf8")
    assert not any(
        e.get("rule") == "RULE-ENDP-048"
        for e in _errors(_document_findings(validator, ep, "api-endpoint"))
    )


def test_embedded_schema_must_be_valid_draft_2020_12(validator):
    """An embedded input/response schema that parses (arrow-valid) but is not a
    valid JSON Schema Draft 2020-12 document is caught by the validator. The
    contract model checks the arrow_type pairing, not meta-schema validity."""
    ep = _endpoint("STRING", "Utf8")
    # `minItems` must be a non-negative integer; a string is meta-invalid, and the
    # contract model doesn't inspect it, so only RULE-ENDP-048 fires.
    ep["operations"]["read"]["response"]["schema"]["minItems"] = "notanumber"
    errors = _errors(_document_findings(validator, ep, "api-endpoint"))
    assert any(e.get("rule") == "RULE-ENDP-048" for e in errors), errors


def test_embedded_schema_rejects_other_dialect(validator):
    """A meta-valid schema that DECLARES another draft (e.g. Draft-07) is not
    Draft 2020-12 and is rejected — `check_schema` alone would miss it."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["$schema"] = (
        "http://json-schema.org/draft-07/schema#"
    )
    errors = _errors(_document_findings(validator, ep, "api-endpoint"))
    assert any(e.get("rule") == "RULE-ENDP-048" for e in errors), errors


def test_embedded_schema_accepts_the_empty_fragment_spelling(validator):
    """`…/2020-12/schema#` and `…/2020-12/schema` identify the same resource
    under RFC 3986, and a JSON Schema implementation maps either spelling onto
    the 2020-12 dialect. The contract accepts either."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["$schema"] = f"{JS}#"
    errors = _errors(_document_findings(validator, ep, "api-endpoint"))
    assert not any(e.get("rule") == "RULE-ENDP-048" for e in errors), errors


def test_embedded_schema_rejects_a_repeated_empty_fragment(validator):
    """A single trailing `#` is the empty fragment. `##` is not a spelling of
    this URI, so it names something other than the dialect the contract is
    written in and is refused — stripping every trailing `#` would accept it."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["$schema"] = f"{JS}##"
    errors = _errors(_document_findings(validator, ep, "api-endpoint"))
    assert any(e.get("rule") == "RULE-ENDP-048" for e in errors), errors


def test_a_nested_dialect_declaration_is_a_contract_model_error(validator):
    """`$schema` below the root of an embedded schema is refused by the
    contract model, whatever its value, and nothing downstream of that model
    crashes on it — a "validator bug" finding would hide the actionable one and
    blame the tool for a defect in the document. Attributed to RULE-ENDP-064,
    the walker rule this exact "$schema on a subschema" complaint belongs to."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["items"]["$schema"] = 5
    findings = _document_findings(validator, ep, "api-endpoint")
    assert any(f.get("rule") == "RULE-ENDP-064" for f in _errors(findings)), findings
    assert not [f for f in findings if "validator bug" in f["message"]], findings


def test_a_root_non_string_dialect_declaration_is_a_document_error(validator):
    """A root `$schema` is authorable at any value the contract model does not
    read, so the dialect check is what meets a non-string one. `check_schema`
    types the keyword against the meta-schema, and asking it before the string
    comparison is what keeps the comparison holding a string — reverse the two
    and this document raises inside the check and is reported as a defect in
    the tool instead of in the document."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["$schema"] = 5
    findings = _document_findings(validator, ep, "api-endpoint")
    errors = _errors(findings)
    assert any(e.get("rule") == "RULE-ENDP-048" for e in errors), errors
    assert not [f for f in findings if "validator bug" in f["message"]], findings


def _read_endpoint(response_schema, endpoint_id="widgets", path="/widgets"):
    return {
        "$schema": API, "endpoint_id": endpoint_id,
        "operations": {"read": {
            "request": {"method": "GET", "path": path}, "params": {},
            "response": {"records": {"ref": "response.body"}, "schema": response_schema},
        }}}


def _write_endpoint(input_schema, endpoint_id="widgets", path="/widgets"):
    return {
        "$schema": API, "endpoint_id": endpoint_id,
        "operations": {"write": {"insert": {
            "request": {
                "method": "POST", "path": path,
                "headers": {"Accept": "application/json"},
                "body": {"r": {"from_input": "record"}},
            },
            "params": {},
            "input": {"schema": input_schema},
        }}}}


#: One entry per key of `_REFUSED_REFERENCE_KEYWORDS`, shared by the
#: parametrized case below and the test that pins this list stays exhaustive
#: over that dict — a single list, not two copies that could disagree.
_REFUSED_KEYWORD_ATTRIBUTION = [
    # Covered by RULE-ENDP-026's own statement ("retargets the base URI"
    # or "defers a reference to evaluation time").
    ("$id", "RULE-ENDP-026", "ref-refused-keyword"),
    ("$dynamicRef", "RULE-ENDP-026", "ref-refused-keyword"),
    ("$recursiveRef", "RULE-ENDP-026", "ref-refused-keyword"),
    # Refused on the same underlying harm, but the statement names
    # neither mechanism for these — stay unattributed until it does.
    ("$anchor", None, "value_error"),
    ("$dynamicAnchor", None, "value_error"),
    ("$recursiveAnchor", None, "value_error"),
]


class TestFourWalkerRulesAreAttributed:
    """RULE-ENDP-005/006/026/064: `_validate_arrow_type_in_json_schema` and
    `_validate_schema_refs` accumulate one `RuleViolation` per complaint, and
    `ResponseExtraction._validate`/`WriteInput._validate` raise them together
    as one `MultiRuleViolation`, each entry keeping its own `rule_id` and a
    `path` located to the offending node — `_model_findings` expands it into
    one finding per complaint instead of folding them into one.
    """

    def _rule_findings(self, doc):
        # Isolated to the model-validation pass itself (not the whole
        # api-endpoint validator, which also runs the locator, embedded-schema
        # and sample checks — RULE-ENDP-046/048/063 — that would otherwise
        # leak into these exact-list assertions): a finding names no pass
        # that produced it, so this is the only way to isolate what a
        # `MultiRuleViolation` from the walker rules this class exercises
        # expands into.
        from analitiq.validator._core import _model_findings
        from analitiq.validator.connectors import _API_ENDPOINT_ADAPTER
        return _errors(_model_findings(doc, _API_ENDPOINT_ADAPTER))

    def test_response_schema_pairing_miss_is_rule_endp_005(self, validator):
        doc = _read_endpoint({
            "type": "object",
            "properties": {"a": {"native_type": "int"}},
        })
        findings = self._rule_findings(doc)
        assert [
            (f.get("rule"), f["path"]) for f in findings
            if f["message_id"] == "native-arrow-pairing-incomplete"
        ] == [("RULE-ENDP-005", "/operations/read/response/schema/properties/a")]

    def test_write_input_schema_pairing_miss_is_rule_endp_006(self, validator):
        doc = _write_endpoint({
            "type": "object",
            "properties": {"z": {"native_type": "int"}},
        })
        findings = self._rule_findings(doc)
        assert [
            (f.get("rule"), f["path"]) for f in findings
            if f["message_id"] == "native-arrow-pairing-incomplete"
        ] == [("RULE-ENDP-006", "/operations/write/insert/input/schema/properties/z")]

    def test_dangling_ref_in_response_schema_is_rule_endp_026(self, validator):
        doc = _read_endpoint({
            "type": "object",
            "properties": {"b": {"$ref": "#/$defs/Typo"}},
        })
        findings = self._rule_findings(doc)
        assert [
            (f.get("rule"), f["message_id"], f["path"]) for f in findings
        ] == [(
            "RULE-ENDP-026", "ref-dangling",
            "/operations/read/response/schema/properties/b",
        )]

    def test_dangling_ref_in_write_input_schema_is_also_rule_endp_026(self, validator):
        # `_validate_schema_refs` binds both `ResponseExtraction` and
        # `WriteInput` — this is the caller the pre-existing embedded-ref
        # tests never construct.
        doc = _write_endpoint({
            "type": "object",
            "properties": {"a": {"$ref": "#/$defs/Typo"}},
        })
        findings = self._rule_findings(doc)
        assert [
            (f.get("rule"), f["message_id"], f["path"]) for f in findings
        ] == [(
            "RULE-ENDP-026", "ref-dangling",
            "/operations/write/insert/input/schema/properties/a",
        )]

    def test_schema_keyword_below_root_is_rule_endp_064(self, validator):
        doc = _write_endpoint({
            "type": "object",
            "properties": {"b": {
                "type": "object", "$schema": JS, "properties": {},
            }},
        })
        findings = self._rule_findings(doc)
        assert [
            (f.get("rule"), f["message_id"], f["path"]) for f in findings
        ] == [(
            "RULE-ENDP-064", "schema-keyword-on-subschema",
            "/operations/write/insert/input/schema/properties/b",
        )]

    def test_two_simultaneous_violations_are_two_separate_findings(self, validator):
        # A pairing miss and a dangling ref at different nodes of the same
        # schema: each must land as its own attributed, located finding — not
        # folded into one message attributed to only the first one found.
        doc = _read_endpoint({
            "type": "object",
            "properties": {
                "a": {"native_type": "int"},
                "b": {"$ref": "#/$defs/Typo"},
            },
        })
        findings = self._rule_findings(doc)
        assert sorted((f.get("rule"), f["path"]) for f in findings) == sorted([
            ("RULE-ENDP-005", "/operations/read/response/schema/properties/a"),
            ("RULE-ENDP-026", "/operations/read/response/schema/properties/b"),
        ])

    def test_unattributed_complaint_still_carries_its_own_path(self, validator):
        # No registered rule covers an arrow_type spelling that fails the
        # canonical-vocabulary pattern; it must still surface as its own
        # `rule=None` finding, located, rather than vanishing into a joined
        # message or being silently dropped alongside an attributed sibling.
        doc = _read_endpoint({
            "type": "object",
            "properties": {
                "a": {"native_type": "int"},
                "c": {"native_type": "int", "arrow_type": "NotArrowType"},
            },
        })
        findings = self._rule_findings(doc)
        unattributed = [f for f in findings if f.get("rule") is None]
        assert [(f["message_id"], f["path"]) for f in unattributed] == [
            ("value_error", "/operations/read/response/schema/properties/c"),
        ]
        # And the attributed sibling is not displaced by it.
        assert ("RULE-ENDP-005", "/operations/read/response/schema/properties/a") in [
            (f.get("rule"), f["path"]) for f in findings
        ]

    @pytest.mark.parametrize("node,expected_message_id", [
        ({"native_type": "map", "arrow_type": "Object"}, "object-container-missing-properties"),
        (
            {"native_type": "map", "arrow_type": "Object", "properties": {}},
            "object-container-invalid-properties",
        ),
        (
            {
                "native_type": "map", "arrow_type": "Object",
                "properties": {"x": {"type": "string"}}, "items": {"type": "string"},
            },
            "object-container-has-items",
        ),
        ({"native_type": "array", "arrow_type": "List"}, "list-container-missing-items"),
        (
            {"native_type": "array", "arrow_type": "List", "items": True},
            "list-container-invalid-items",
        ),
        (
            {
                "native_type": "array", "arrow_type": "List",
                "items": {"type": "string"}, "properties": {"x": {"type": "string"}},
            },
            "list-container-has-properties",
        ),
        (
            {"native_type": "json", "arrow_type": "Json", "properties": {"x": {"type": "string"}}},
            "json-container-shape-invalid",
        ),
        (
            {"native_type": "string", "arrow_type": "Utf8", "properties": {"x": {"type": "string"}}},
            "scalar-container-shape-invalid",
        ),
    ], ids=[
        "object-missing-properties", "object-invalid-properties", "object-has-items",
        "list-missing-items", "list-invalid-items", "list-has-properties",
        "json", "scalar",
    ])
    @pytest.mark.parametrize("build,expected_rule,base_path", [
        (_read_endpoint, "RULE-ENDP-005", "/operations/read/response/schema/properties/a"),
        (_write_endpoint, "RULE-ENDP-006", "/operations/write/insert/input/schema/properties/a"),
    ], ids=["response", "write_input"])
    def test_container_shape_complaint_is_attributed(
        self, validator, node, expected_message_id, build, expected_rule, base_path,
    ):
        # `native_type`+`arrow_type` are both set (unlike the pairing-miss
        # tests above) so the container-shape complaint is the only one this
        # node raises — isolating the message_id under test.
        doc = build({"type": "object", "properties": {"a": node}})
        findings = self._rule_findings(doc)
        matches = [
            (f.get("rule"), f["path"]) for f in findings
            if f["message_id"] == expected_message_id
        ]
        assert matches == [(expected_rule, base_path)], findings

    @pytest.mark.parametrize("node,expected_message_id", [
        ({"$ref": 5}, "ref-not-string"),
        ({"$ref": "https://example.com/x"}, "ref-not-in-document"),
        ({"$ref": "#anchor"}, "ref-anchor-fragment"),
        ({"$ref": "#/$defs/B"}, "ref-resolves-to-boolean"),
        ({"$ref": "#/properties/a/default"}, "ref-non-schema-position"),
    ], ids=[
        "not-string", "not-in-document", "anchor-fragment",
        "resolves-to-boolean", "non-schema-position",
    ])
    def test_every_ref_complaint_kind_is_rule_endp_026(self, validator, node, expected_message_id):
        doc = _read_endpoint({
            "type": "object",
            "$defs": {"B": True},
            "properties": {
                "a": {"type": "string", "default": "hi"},
                "b": node,
            },
        })
        findings = self._rule_findings(doc)
        assert [
            (f.get("rule"), f["path"]) for f in findings
            if f["message_id"] == expected_message_id
        ] == [("RULE-ENDP-026", "/operations/read/response/schema/properties/b")]

    @pytest.mark.parametrize(
        "keyword,expected_rule,expected_message_id", _REFUSED_KEYWORD_ATTRIBUTION,
    )
    def test_every_refused_reference_keyword_gets_its_own_finding(
        self, validator, keyword, expected_rule, expected_message_id,
    ):
        doc = _read_endpoint({
            "type": "object",
            "properties": {"b": {keyword: "x" if keyword != "$id" else "https://example.com/"}},
        })
        findings = self._rule_findings(doc)
        assert [
            (f.get("rule"), f["message_id"], f["path"]) for f in findings
        ] == [(
            expected_rule, expected_message_id,
            "/operations/read/response/schema/properties/b",
        )]

    def test_refused_keyword_attribution_covers_every_refused_keyword(self):
        # `_REFUSED_KEYWORD_ATTRIBUTION` above is a hand-typed list; pin it
        # against the walker's own dict so a keyword added to
        # `_REFUSED_REFERENCE_KEYWORDS` without a matching case here fails
        # loudly instead of leaving this test's "every" silently false.
        tested = {keyword for keyword, _, _ in _REFUSED_KEYWORD_ATTRIBUTION}
        assert tested == _REFUSED_REFERENCE_KEYWORDS.keys()

    def test_cross_parameter_bound_violation_is_unattributed_and_located(self, validator):
        # `validate_cross_params` rejects Decimal scale > precision — a
        # complaint kind no registered rule covers, alongside the
        # arrow-pattern-mismatch case (`test_unattributed_complaint_still_
        # carries_its_own_path` above) and the malformed-node case (below) —
        # so it too must surface `rule=None` but still located.
        doc = _read_endpoint({
            "type": "object",
            "properties": {
                "a": {"native_type": "dec", "arrow_type": "Decimal128(2, 9)"},
            },
        })
        findings = self._rule_findings(doc)
        assert [
            (f.get("rule"), f["message_id"], f["path"]) for f in findings
        ] == [(
            None, "value_error", "/operations/read/response/schema/properties/a",
        )]

    def test_malformed_schema_node_violation_is_unattributed_and_located(self, validator):
        # A non-dict, non-boolean value at a schema position — the third and
        # last of the complaint kinds no registered rule covers — reaches
        # `_model_findings` the same way: unattributed, but located.
        doc = _read_endpoint({
            "type": "object",
            "properties": {"a": "not-a-schema"},
        })
        findings = self._rule_findings(doc)
        assert [
            (f.get("rule"), f["message_id"], f["path"]) for f in findings
        ] == [(
            None, "value_error", "/operations/read/response/schema/properties/a",
        )]


@pytest.fixture
def connector_base():
    # A real, model-valid api connector (hand-crafting the exact Connector
    # shape is error-prone; the corpus copy is the source of truth).
    return json.loads((CORPUS / "valid_connector.json").read_text())


def test_coverage_passes_when_map_covers_endpoints(connector_base, validator):
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"widgets.json": _endpoint("STRING", "Utf8")})
    findings = _package_findings(validator, package)
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_coverage_passes_with_lowercase_exact_matcher(connector_base, validator):
    # A lowercase `exact` native the runtime resolves fine must not be
    # reported as uncovered. The endpoint declares `varchar`; the runtime
    # normalizes both sides and matches, so coverage must too.
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "varchar", "arrow_type": "Utf8"}],
        {"widgets.json": _endpoint("varchar", "Utf8")})
    findings = _package_findings(validator, package)
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


# --- one field, two directional entries, one shared read map ------------------
# A field sampled on both sides is an entry per side, each declaring its own pair.
# The read map renders one canonical per native token and a connector has one map,
# so these pin what two entries can and cannot declare under one token.
_UTC, _NAIVE = "Timestamp(MICROSECOND, UTC)", "Timestamp(MICROSECOND)"


def _read_and_write(read_native, read_arrow, write_native, write_arrow):
    """The corpus-valid endpoint, plus a write mode whose input declares its own pair."""
    ep = _endpoint(read_native, read_arrow)
    node = {"type": "string"}
    if write_native:
        node |= {"native_type": write_native, "arrow_type": write_arrow}
    ep["operations"]["write"] = {"insert": {
        "request": {"method": "POST", "path": "/widgets", "body": {"from_input": "record"}},
        "input": {"schema": {"$schema": JS, "type": "object", "properties": {"a": node}}},
    }}
    return ep


@pytest.mark.parametrize("rules,read_pair,write_pair", [
    ([("date-time", _UTC)], ("date-time", _UTC), ("date-time", _UTC)),
    ([("date-time", _UTC), ("date-time-naive", _NAIVE)],
     ("date-time", _UTC), ("date-time-naive", _NAIVE)),
    ([("date-time", _UTC)], ("date-time", _UTC), (None, None)),
], ids=["one-token-agreeing", "distinct-tokens-per-zone", "write-undeclared"])
def test_directional_pairs_the_read_map_can_render(
        connector_base, validator, rules, read_pair, write_pair):
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": n, "arrow_type": c} for n, c in rules],
        {"widgets.json": _read_and_write(*read_pair, *write_pair)})
    errors = _errors(_package_findings(validator, package))
    assert not errors, [e["message"] for e in errors]


def test_one_token_cannot_carry_two_canonicals(connector_base, validator):
    # The write entry declaring the naive spelling under the read entry's token is
    # rejected: that token already resolves to the zoned canonical. Two entries that
    # resolve differently need two tokens, which the domain type map then spells.
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "date-time", "arrow_type": _UTC}],
        {"widgets.json": _read_and_write("date-time", _UTC, "date-time", _NAIVE)})
    errors = _errors(_package_findings(validator, package))
    assert any("resolves to" in e["message"] and _NAIVE in e["message"] for e in errors), \
        [e["message"] for e in errors]


def test_coverage_flags_uncovered_native(connector_base, validator):
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"widgets.json": _endpoint("BIGINT", "Int64")})
    errors = _errors(_package_findings(validator, package))
    assert any("no matching rule" in e["message"] for e in errors)


def test_coverage_flags_arrow_mismatch(connector_base, validator):
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"widgets.json": _endpoint("STRING", "Int64")})
    errors = _errors(_package_findings(validator, package))
    assert any("resolves to" in e["message"] and "Int64" in e["message"] for e in errors)


def test_coverage_still_runs_when_the_read_map_fails_its_model(connector_base, validator):
    # A map carrying a `read` section has named what it covers, whatever else
    # about it the model rejects. Coverage reads that section from the parsed
    # document rather than from the (rejected) model instance, so endpoint
    # coverage runs against those rules instead of silently no-oping behind the
    # map's own error — staged with an endpoint the map does not cover, so only
    # coverage having run can produce the second finding.
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"widgets.json": _endpoint("BIGINT", "Int64")})
    package[_TYPE_MAP_KEY]["$schema"] = CONNECTOR_SCHEMA_URL
    errors = _errors(_package_findings(validator, package))
    assert any(e["path"] == f"{_TYPE_MAP_KEY}#/$schema" for e in errors), errors
    assert any("no matching rule" in e["message"] for e in errors), errors


def test_coverage_flags_missing_read_map(connector_base, validator):
    errors = _errors(_package_findings(validator, {_CONNECTOR_KEY: connector_base}))
    assert any(e["message_id"] == "read-map-missing" for e in errors), errors


def test_an_endpoint_that_holds_a_json_null_is_graded(
        connector_base, validator):
    # `null` parses, so the document is graded rather than withheld as one
    # that could not be read; a package route reading the parsed VALUE as
    # "nothing here" would pass a document nothing graded.
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"widgets.json": _endpoint("STRING", "Utf8")})
    package[_endpoint_key("widgets.json")] = None
    errors = _about(_errors(_package_findings(validator, package)), _endpoint_key("widgets.json"))
    assert errors, "an endpoint holding a JSON null was graded by nothing"


def _object_endpoint():
    # An `Object` arrow_type requires a sibling `properties` map (model rule).
    return {
        "$schema": API, "endpoint_id": "widgets",
        "operations": {"read": {
            "request": {"method": "GET", "path": "/widgets"}, "params": {},
            "response": {
                "records": {"ref": "response.body"},
                "schema": {"$schema": JS, "type": "array", "items": {"type": "object",
                    "properties": {"a": {"type": "object",
                        "native_type": "JSONB", "arrow_type": "Object",
                        "properties": {"inner": {"type": "string"}}}}}},
            }}}}


def test_coverage_json_narrowing_allowed(connector_base, validator):
    # A read map that renders `Json` satisfies an endpoint declaring `Object`.
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "JSONB", "arrow_type": "Json"}],
        {"widgets.json": _object_endpoint()})
    assert not _errors(_package_findings(validator, package))


def test_coverage_json_narrowing_is_narrow(connector_base, validator):
    # ...but `Json` does NOT satisfy a scalar like `Int64` (the allowance is narrow).
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "JSONB", "arrow_type": "Json"}],
        {"widgets.json": _endpoint("JSONB", "Int64")})
    assert _errors(_package_findings(validator, package))


def test_coverage_checks_field_named_like_a_keyword(connector_base, validator):
    # A response field literally named `default` must still be coverage-checked
    # (the schema-aware walk treats `properties` children as field names).
    ep = {"$schema": API, "endpoint_id": "widgets",
          "operations": {"read": {
              "request": {"method": "GET", "path": "/widgets"}, "params": {},
              "response": {"records": {"ref": "response.body"},
                  "schema": {"$schema": JS, "type": "array", "items": {"type": "object",
                      "properties": {"default": {"type": "string",
                          "native_type": "WEIRDTYPE", "arrow_type": "Utf8"}}}}}}}}
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],  # no WEIRDTYPE rule
        {"widgets.json": ep})
    errors = _errors(_package_findings(validator, package))
    assert any("WEIRDTYPE" in e["message"] and "no matching rule" in e["message"] for e in errors)


def _within(seconds, call):
    """`call()`, failed rather than stalled when it has not returned in time."""
    def _stalled(_signum, _frame):
        raise AssertionError(f"did not return within {seconds}s")

    previous = signal.signal(signal.SIGALRM, _stalled)
    signal.alarm(seconds)
    try:
        return call()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


_NEAR_MATCH = "A" * 10_000 + "B"


def test_read_coverage_returns_promptly_on_a_nested_quantifier(connector_base, validator):
    package = _connector_package(
        connector_base,
        [{"match": "regex", "native_type": r"(A+)+$", "arrow_type": "Utf8"}],
        {"widgets.json": _endpoint(_NEAR_MATCH, "Utf8")})
    findings = _within(5, lambda: _package_findings(validator, package))
    assert any(f.get("rule") == "RULE-PKG-033" for f in _errors(findings)), findings


def test_normalize_native_is_the_canonical(validator):
    # The validator imports the single source of truth — it does not
    # reimplement it — so coverage normalizes exactly as every reader does.
    from analitiq.contracts.type_map import normalize_native_type
    assert validator.connectors._normalize_native is normalize_native_type
    # strip → collapse internal whitespace runs → uppercase.
    assert normalize_native_type("  character  varying ") == "CHARACTER VARYING"
    assert normalize_native_type("varchar") == "VARCHAR"
    assert normalize_native_type("Timestamp\tWith Time  Zone") == "TIMESTAMP WITH TIME ZONE"


def test_arrow_type_eq_normalizes_separators_not_identifiers(validator):
    eq = validator.connectors._arrow_type_eq
    assert eq("Decimal128(38, 9)", "Decimal128(38,9)")          # param spacing insignificant
    assert eq("Timestamp(MICROSECOND, UTC)", "Timestamp(MICROSECOND,UTC)")
    # Whitespace INSIDE a token is significant — must NOT compare equal.
    assert not eq("Time stamp(SECOND)", "Timestamp(SECOND)")
    assert not eq("Timestamp(MICRO SECOND)", "Timestamp(MICROSECOND)")


def test_walk_collects_tuple_form_items(validator):
    # Draft-2019-09 tuple-form `items: [...]` must be traversed (mirrors the model).
    ep = {"operations": {"read": {"response": {"schema": {"type": "array",
        "items": [{"type": "object", "properties": {
            "x": {"native_type": "WEIRDTYPE", "arrow_type": "Utf8"}}}]}}}}}
    pairs = validator.connectors._collect_native_arrow_pairs(ep)
    assert ("WEIRDTYPE", "Utf8", "/operations/read/response/schema/items/0/properties/x") in pairs


def test_coverage_regex_rule_with_capture(connector_base, validator):
    # A regex read rule with a named capture + ${name} render must resolve.
    package = _connector_package(
        connector_base,
        [{"match": "regex", "native_type": r"NUMERIC\((?<p>[1-9]|[12]\d|3[0-8]),\s*(?<s>\d|[12]\d|3[0-8])\)",
          "arrow_type": "Decimal128(${p}, ${s})"}],
        {"widgets.json": _endpoint("NUMERIC(38,9)", "Decimal128(38, 9)")})
    assert not _errors(_package_findings(validator, package))


def test_coverage_distinct_endpoint_ids_pass(connector_base, validator):
    # Two endpoints with distinct ids (matching filenames + paths) raise no error.
    a = _endpoint("STRING", "Utf8", endpoint_id="alpha", path="/alpha")
    b = _endpoint("STRING", "Utf8", endpoint_id="beta", path="/beta")
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"alpha.json": a, "beta.json": b})
    assert not _errors(_package_findings(validator, package))


# --- endpoint_id must be the derived path locator (io-contracts resources[].key) ---

def test_flatten_api_locator(validator):
    f = validator.connectors._flatten_api_locator
    assert f("/v1/blah/something/customer") == "v1__blah__something__customer"
    assert f("/v2/blah/something/customer") == "v2__blah__something__customer"
    assert f("/ping") == "ping"
    assert f("/v1/users/{id}/orders") == "v1__users__orders"   # path-param dropped
    assert f("/V1/Records/") == "v1__records"                  # lowercased, trailing slash
    assert f("/customers/v2/orders") == "customers__v2__orders"  # every segment IN ORDER (no hoist)
    assert f("/customers") == "customers"
    # A mixed segment ({id}-{slug}) is NOT a pure path-param -> not dropped, so it
    # does NOT collide with the pure-param sibling.
    assert f("/orders/{id}-{slug}") != f("/orders/{id}")
    assert "{" in f("/orders/{id}-{slug}")   # kept -> later flagged non-charset-safe
    assert f("/orders/{id}") == "orders"     # pure param dropped


def test_endpoint_id_must_match_locator(validator):
    # id equals the derived handle -> ok
    assert validator.connectors._endpoint_locator_findings(
        {"endpoint_id": "v1__records",
         "operations": {"read": {"request": {"path": "/v1/records"}}}}) == []
    # leaf-only id for a versioned path -> flagged with the expected handle
    errs = validator.connectors._endpoint_locator_findings(
        {"endpoint_id": "records",
         "operations": {"read": {"request": {"path": "/v1/records"}}}})
    assert errs and errs[0].get("rule") == "RULE-ENDP-046"
    assert "v1__records" in errs[0]["message"]


def test_endpoint_locator_derives_from_read_canonical_path(validator):
    # The id is checked against the read (canonical resource) locator; a write mode
    # carrying a path-param or a sub-path (e.g. /bulk) does not force a split.
    for write_path in ("/v1/users/{id}", "/v1/users/bulk"):
        doc = {"endpoint_id": "v1__users", "operations": {
            "read": {"request": {"path": "/v1/users"}},
            "write": {"upsert": {"request": {"path": write_path}}}}}
        assert validator.connectors._endpoint_locator_findings(doc) == [], write_path
    # A write-only endpoint derives from its write path.
    write_only = {"endpoint_id": "v1__events",
                  "operations": {"write": {"insert": {"request": {"path": "/v1/events"}}}}}
    assert validator.connectors._endpoint_locator_findings(write_only) == []


def test_endpoint_locator_non_derivable_path_errors(validator):
    # A path with NO derivable id is a hard gate failure (not a warning that would
    # let a decoupled endpoint_id through), and the message is about the PATH — never
    # a self-contradictory "must equal <invalid-id>".
    # (a) non-charset-safe (Shopify-style `.json`); (b) all-path-param (empty handle).
    for path in ("/admin/api/2024-01/orders.json", "/{id}"):
        doc = {"endpoint_id": "orders",
               "operations": {"read": {"request": {"path": path}}}}
        findings = validator.connectors._endpoint_locator_findings(doc)
        assert findings and findings[0]["severity"] == "error", path
        assert findings[0].get("rule") == "RULE-ENDP-046"
        assert "must equal" not in findings[0]["message"]      # no fabricated id
        assert "cannot derive" in findings[0]["message"]


def test_coverage_non_dict_endpoint_file_no_crash(connector_base, validator):
    # A JSON-array endpoint file is a recorded model error, NOT a generic
    # "validator bug" crash from the coverage walk calling .get() on a list.
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"widgets.json": []})  # array, not object
    errs = _errors(_package_findings(validator, package))
    assert errs
    assert not any("validator bug" in e["message"] for e in errs)


def test_coverage_flags_endpoint_id_locator_mismatch(connector_base, validator):
    # End-to-end: a model-valid endpoint whose id doesn't encode its versioned path
    # is gated (filename still matches the id; only the locator rule catches it).
    ep = _endpoint("STRING", "Utf8", endpoint_id="widgets", path="/v1/widgets")
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"widgets.json": ep})
    errors = _errors(_package_findings(validator, package))
    assert any(e.get("rule") == "RULE-ENDP-046" for e in errors)


# --- RULE-ENDP-044: a keyset block must omit `initial`, never spell it null -----

def _keyset_endpoint(initial=...):
    keyset = {"param": "after", "order_by_field": "id"}
    if initial is not ...:
        keyset["initial"] = initial
    request = {
        "method": "GET", "path": "/v1/records",
        "query": {"after": {"from_param": "after"}},
    }
    return {
        "$schema": "https://schemas.analitiq.ai/api-endpoint/latest.json",
        "endpoint_id": "v1__records",
        "operations": {
            "read": {
                "request": request,
                "params": {
                    "after": {"in": "query", "type": "string", "required": False,
                              "controlled_by": "pagination"},
                },
                "response": {
                    "records": {"ref": "response.body"},
                    "schema": {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                        },
                    },
                },
                "pagination": {
                    "type": "keyset",
                    "keyset": keyset,
                    "stop_when": {"empty": {"ref": "response.records"}},
                },
            },
        },
    }


def test_keyset_explicit_null_initial_warns(validator):
    findings = _document_findings(validator, _keyset_endpoint(initial=None), "api-endpoint")
    hits = [f for f in findings if f.get("rule") == "RULE-ENDP-044"]
    assert hits, findings
    assert hits[0]["kind"] == "fail"
    assert hits[0]["severity"] == "warning"
    assert hits[0]["path"] == "/operations/read/pagination/keyset/initial"


@pytest.mark.parametrize("initial", [..., "abc123", 0])
def test_keyset_non_null_initial_is_clean(initial, validator):
    findings = _document_findings(validator, _keyset_endpoint(initial=initial), "api-endpoint")
    assert not any(f.get("rule") == "RULE-ENDP-044" for f in findings), findings


# --- RULE-SHRD-003: every authored document must declare `$schema` ------------

def _connection_doc(schema_url=...):
    doc = {"connector_id": "stripe"}
    if schema_url is not ...:
        doc["$schema"] = schema_url
    return doc


def _stream_doc(schema_url=...):
    doc = {
        "pipeline_id": "b4904c77-0a4a-4a8d-a768-4a8b5f2f2414",
        "source": {
            "endpoint_ref": {
                "scope": "connector",
                "connection_id": "11111111-1111-4111-8111-111111111111_v1",
                "endpoint_id": "transfers",
            }
        },
        "destinations": [
            {
                "endpoint_ref": {
                    "scope": "connector",
                    "connection_id": "22222222-2222-4222-8222-222222222222_v1",
                    "endpoint_id": "orders",
                },
                "write": {"mode": "insert"},
            }
        ],
    }
    if schema_url is not ...:
        doc["$schema"] = schema_url
    return doc


def _pipeline_doc(schema_url=...):
    doc = {
        "connections": {
            "source": "11111111-1111-4111-8111-111111111111_v1",
            "destinations": ["22222222-2222-4222-8222-222222222222_v1"],
        }
    }
    if schema_url is not ...:
        doc["$schema"] = schema_url
    return doc


def _connector_doc(schema_url=...):
    doc = json.loads((CORPUS / "valid_connector.json").read_text())
    if schema_url is ...:
        del doc["$schema"]
    else:
        doc["$schema"] = schema_url
    return doc


_SHRD_003_FAMILIES = [
    (_connection_doc, "connection", CONNECTION_SCHEMA_URL),
    (_stream_doc, "stream", STREAM_SCHEMA_URL),
    (_pipeline_doc, "pipeline", PIPELINE_SCHEMA_URL),
    (_connector_doc, "connector", CONNECTOR_SCHEMA_URL),
]


@pytest.mark.parametrize("make_doc,document_kind,schema_url", _SHRD_003_FAMILIES)
def test_missing_schema_url_warns(make_doc, document_kind, schema_url, validator):
    findings = _document_findings(validator, make_doc(schema_url=...), document_kind)
    hits = [f for f in findings if f.get("rule") == "RULE-SHRD-003"]
    assert hits, findings
    assert hits[0]["kind"] == "fail"
    assert hits[0]["severity"] == "warning"
    assert hits[0]["path"] == "/$schema"


@pytest.mark.parametrize("make_doc,document_kind,schema_url", _SHRD_003_FAMILIES)
def test_present_schema_url_is_clean(make_doc, document_kind, schema_url, validator):
    findings = _document_findings(validator, make_doc(schema_url=schema_url), document_kind)
    assert not any(f.get("rule") == "RULE-SHRD-003" for f in findings), findings


@pytest.mark.parametrize("make_doc,document_kind,schema_url", _SHRD_003_FAMILIES)
def test_null_schema_url_reports_the_same_as_omission(make_doc, document_kind, schema_url, validator):
    # `$schema: null` is what every one of these models types as optional, so
    # nothing structural rejects it — and a document spelling the absence as a
    # null names no contract exactly as one leaving the key out does.
    findings = _document_findings(validator, make_doc(schema_url=None), document_kind)
    hits = [f for f in findings if f.get("rule") == "RULE-SHRD-003"]
    assert len(hits) == 1, findings
    assert hits[0]["severity"] == "warning"
    assert hits[0]["path"] == "/$schema"


# --- Database endpoint id = slug+hash8 (shared analitiq.contracts.endpoint_identity SSOT) ---

DB = "https://schemas.analitiq.ai/database-endpoint/latest.json"


def _db_endpoint(endpoint_id, schema="public", name="orders", catalog=None):
    dbo = {"name": name}
    if schema is not None:
        dbo["schema"] = schema
    if catalog is not None:
        dbo["catalog"] = catalog
    return {"$schema": DB, "endpoint_id": endpoint_id, "database_object": dbo,
            "columns": [{"name": "id", "native_type": "BIGINT", "arrow_type": "Int64"}]}


def test_db_endpoint_id_golden_vectors():
    # KNOWN-ANSWER vectors (hardcoded, NOT recomputed) so any drift in the shared
    # derivation — payload/order/hash — breaks the test. These are the reference
    # the minting Lambda must reproduce. Source: analitiq.contracts.endpoint_identity.
    assert slug("Sales") == "sales"
    assert slug("Order Items") == "order_items"
    assert slug("a.b-c") == "a_b_c"          # any non-[a-z0-9] run -> single "_"
    assert slug("__weird__") == "weird"      # leading/trailing trimmed
    assert slug("***") == ""                 # all out-of-charset -> empty
    assert derive_db_endpoint_id(None, "public", "orders") == "public__orders__371c8422"
    assert derive_db_endpoint_id(None, "Sales", "Order Items") == "sales__order_items__0e62f7e9"
    # Same slug, different verbatim name -> different hash (no collision).
    assert derive_db_endpoint_id(None, "Sales", "order_items") == "sales__order_items__ce7aee55"
    # Catalog present: slug order is schema, table, catalog (catalog last, before hash).
    assert derive_db_endpoint_id("Analytics", "Sales", "orders") == "sales__orders__analytics__a045c614"
    # Schemaless object (no schema) -> table slug then hash.
    assert derive_db_endpoint_id(None, None, "orders") == "orders__e53bb11a"
    # All-symbol name -> bare hash8, still a valid endpoint_id.
    assert re.fullmatch(r"[0-9a-f]{8}", derive_db_endpoint_id(None, None, "***"))


def test_database_endpoint_locator_gate(validator):
    # The derived id passes; the legacy `{schema}__{name}` form (no hash) is gated.
    good_id = derive_db_endpoint_id(None, "public", "orders")
    assert not _errors(_document_findings(validator, _db_endpoint(good_id), "database-endpoint"))
    legacy = _db_endpoint("public__orders")
    errs = _errors(_document_findings(validator, legacy, "database-endpoint"))
    assert any(e.get("rule") == "RULE-DBEP-011" and "public__orders" in e["message"]
               for e in errs)
    # Catalog + schemaless variants are gated the same way (derived id passes).
    assert not _errors(_document_findings(
        validator, _db_endpoint(derive_db_endpoint_id("wh", "public", "orders"), schema="public", catalog="wh"),
        "database-endpoint"))
    assert not _errors(_document_findings(
        validator, _db_endpoint(derive_db_endpoint_id(None, None, "orders"), schema=None), "database-endpoint"))


# --- coverage matrix: a connector package whose connector carries only `kind` ---
# `_min_connector` fails the connector model, which the package checks run past,
# so each test reads only the findings of the rule it is about.

def _min_connector(kind: str):
    return {"kind": kind, "transports": {}}


def _read_rules():
    return [{"match": "exact", "native_type": "BIGINT", "arrow_type": "Int64"}]


def _write_rules():
    return [{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}]


_SECTION_RULES = {"read": _read_rules, "write": _write_rules}


def _min_package(kind: str, type_map=None) -> dict:
    """A package holding `_min_connector(kind)` and `type_map`, or no map at
    all when `type_map` is None."""
    return {_CONNECTOR_KEY: _min_connector(kind),
            **({} if type_map is None else {_TYPE_MAP_KEY: type_map})}


def _map_carrying(sections) -> dict | None:
    """A type map carrying a valid rule list for each direction in `sections`,
    or None when `sections` is None."""
    if sections is None:
        return None
    return _type_map_doc(**{d: _SECTION_RULES[d]() for d in sections})


def _relation_ids(findings) -> set[str]:
    return {f["message_id"] for f in findings if f.get("rule") == "RULE-PKG-030"}


# The RULE-PKG-030 complaints each kind earns for the sections its map carries —
# `None` for a package shipping no map.
_SECTION_VERDICTS = [
    *(("api", sections, expected) for sections, expected in [
        (("read",), set()),
        (("read", "write"), {"write-map-not-allowed"}),
        (("write",), {"read-map-missing", "write-map-not-allowed"}),
        (None, {"read-map-missing"}),
    ]),
    *((kind, sections, expected) for kind in _DATABASE_KINDS for sections, expected in [
        (("read", "write"), set()),
        (("read",), {"write-map-missing"}),
        (("write",), {"read-map-missing"}),
        (None, {"read-map-missing", "write-map-missing"}),
    ]),
    *((kind, sections, set()) for kind in _STORAGE_KINDS
      for sections in (None, ("read",), ("write",), ("read", "write"))),
]


@pytest.mark.parametrize("kind,sections,expected", _SECTION_VERDICTS)
def test_a_kind_is_held_to_the_sections_its_map_carries(validator, kind, sections, expected):
    # A storage kind moves bytes and resolves no type, so it requires no
    # section; an api connector has no write direction; a database-family one
    # renders DDL, so it needs a `read` and a `write` section.
    findings = _package_findings(validator, _min_package(kind, _map_carrying(sections)))
    assert _relation_ids(findings) == expected, findings


@pytest.mark.parametrize("kind,expected", [("api", set()), ("database", {"write-map-missing"})])
def test_a_null_section_is_an_absent_one(validator, kind, expected):
    # The model reads a `null` section as absent, so the package check does
    # too: a database map's `null` write section is no write rules, and an api
    # map's is no write section to refuse.
    type_map = {"$schema": _TM_SCHEMA, "read": _read_rules(), "write": None}
    findings = _package_findings(validator, _min_package(kind, type_map))
    assert _relation_ids(findings) == expected, findings


@pytest.mark.parametrize("section", ["read", "write"])
def test_a_malformed_section_is_not_a_missing_one(validator, section):
    # A section present in the wrong shape is the model's finding; reporting it
    # missing as well would send the author to add a section they already have.
    type_map = {"$schema": _TM_SCHEMA,
                **{d: {} if d == section else _SECTION_RULES[d]() for d in ("read", "write")}}
    findings = _package_findings(validator, _min_package("database", type_map))
    assert f"{_TYPE_MAP_KEY}#/{section}" in {f["path"] for f in _errors(findings)}, findings
    assert not _relation_ids(findings), findings


@pytest.mark.parametrize("sections,reason", [
    (None, "the package carries no type map"),
    (("write",), "its type map carries no 'read' section"),
])
def test_a_missing_section_says_whether_the_package_carries_a_map(validator, sections, reason):
    # A package carrying no map asks for a different edit than one whose map
    # lacks the section, so the message says which it is.
    findings = _package_findings(validator, _min_package("database", _map_carrying(sections)))
    [missing] = [f for f in findings if f["message_id"] == "read-map-missing"]
    assert reason in missing["message"], missing


@pytest.mark.parametrize("payload,expected_id", [
    ({"$schema": _TM_SCHEMA, "read": "not-a-list"}, "list_type"),
    (_type_map_doc(read=_read_rules() * 2), "duplicate-type-map-rule"),
])
def test_a_map_finding_names_the_map_by_its_key(validator, payload, expected_id):
    # A document finding locates itself with a pointer into the document it
    # graded. Reported beside a connector without its key it reads as a defect
    # in the connector, which carries no such node, so the author is told what
    # is wrong and cannot tell which document to open.
    # Every finding, not just the error-severity ones: a warning about a map is
    # as unlocatable as a failure about it.
    reported = _package_findings(validator, _min_package("api", payload))
    named = [e for e in reported if e.get("message_id") == expected_id]
    assert named, [e.get("message_id") for e in reported]
    assert named[0]["path"].startswith(f"{_TYPE_MAP_KEY}#/"), named[0]


@pytest.mark.parametrize("kind", (*_DATABASE_KINDS, *_STORAGE_KINDS))
def test_coverage_holds_a_connector_write_map_to_the_whole_vocabulary(kind, validator):
    # A connector that renders one Arrow family materializes nothing else.
    findings = _package_findings(validator, _min_package(kind, _map_carrying(("read", "write"))))
    assert "RULE-TMAP-017" in {f.get("rule") for f in _about(findings, _TYPE_MAP_KEY)}, findings


@pytest.mark.parametrize("kind", _STORAGE_KINDS)
def test_a_storage_map_is_graded_in_every_section_it_carries(kind, validator):
    # A storage kind requires no section, and one it ships anyway is still read
    # by whoever resolves types through it — so a defect in any section is
    # reported, not waved through with the requirement.
    type_map = _type_map_doc(
        read=_read_rules(),
        write=[{"match": "exact", "arrow_type": "NotAnArrowFamily", "native_type": "TEXT"}])
    errors = _about(_errors(_package_findings(validator, _min_package(kind, type_map))), _TYPE_MAP_KEY)
    assert [e["path"] for e in errors] == [f"{_TYPE_MAP_KEY}#/write/0/arrow_type"], errors


@pytest.mark.parametrize("native_type, arrow_type, message_id", [
    ("UNCOVERED", "Utf8", "native-type-unresolved"),
    ("BIGINT", "Utf8", "native-type-arrow-mismatch"),
])
def test_a_coverage_finding_names_the_map_it_was_rendered_from(
        connector_base, native_type, arrow_type, message_id, validator):
    # The message sends an author to a document, so it names the one the rules
    # came from. Every verdict the endpoint walk renders off the read section
    # sends the author to it, not only the one where no rule matched.
    package = _connector_package(
        connector_base, _read_rules(),
        {"widgets.json": _endpoint(native_type, arrow_type)})
    errors = _errors(_package_findings(validator, package))
    named = [e for e in errors if e["message_id"] == message_id]
    assert named, errors
    assert "the package's type map" in named[0]["message"], named[0]


def test_a_map_holding_a_json_null_is_graded(validator):
    # `null` parses, so it is a document the type-map model grades, not an
    # absent map: a storage kind requires no map, so reading the parsed value
    # as "no map here" would pass the package with nothing said about it.
    package = {_CONNECTOR_KEY: _min_connector("file"), _TYPE_MAP_KEY: None}
    errors = _about(_errors(_package_findings(validator, package)), _TYPE_MAP_KEY)
    assert errors, "a map holding a JSON null was graded as nothing"


def test_endpoint_filename_findings_grades_the_filename_against_the_derived_id(validator):
    eid = derive_db_endpoint_id(None, "public", "orders")
    db = _db_endpoint(eid)
    # Mismatched stem -> exactly one RULE-PKG-031 error.
    mismatch = _errors(validator.connectors.endpoint_filename_findings(db, "orders.json"))
    assert [e.get("rule") for e in mismatch] == ["RULE-PKG-031"]
    # Correct {endpoint_id}.json -> no findings.
    assert validator.connectors.endpoint_filename_findings(db, f"{eid}.json") == []
    # Missing/unusable endpoint_id -> notApplicable (can't verify), not a fail.
    no_id = validator.connectors.endpoint_filename_findings({"database_object": {"name": "orders"}}, "orders.json")
    assert [(f.get("rule"), f["kind"]) for f in no_id] == [("RULE-PKG-031", "notApplicable")]


@pytest.mark.parametrize("doc", [
    {"$schema": TYPE_MAP_SCHEMA_URL},
    {"$schema": TYPE_MAP_SCHEMA_URL, "write": "not-a-list"},
], ids=["no-section", "malformed-section"])
def test_a_map_carrying_no_usable_section_fails_its_model(validator, doc):
    errors = _errors(_document_findings(validator, doc, "type-map"))
    assert errors, doc


def test_a_borrowed_diagnostic_does_not_carry_the_document_back_whole(validator):
    # A discriminator error's sentence comes from pydantic, which renders the
    # failing tag into it. This package is the gate over documents it did not
    # author, and the sentence reaches a CI log and an agent's context, so an
    # oversized value is clipped rather than echoed.
    oversized = "Z" * 5000
    errors = _errors(_document_findings(validator, {
        "$schema": CONNECTOR_SCHEMA_URL, "connector_id": "x", "display_name": "x",
        "kind": oversized, "transports": {}}, "connector"))
    [tag] = [e for e in errors if e["message_id"] == "union_tag_invalid"]
    assert oversized not in tag["message"], len(tag["message"])
    assert len(tag["message"]) < 500, len(tag["message"])
    # Only the echoed value is clipped. The accepted values sit after it in the
    # same sentence, and they are what the author needs to fix it.
    assert "'api'" in tag["message"], tag["message"][-120:]


def test_a_short_wrong_tag_comes_back_with_every_accepted_value(validator):
    # A tag of ordinary length echoes nothing worth bounding, and the sentence
    # is long only because the union accepts many values: clipping it drops the
    # ones an author could have picked.
    doc = _connector_doc()
    doc["auth"] = {"type": "oauth2"}
    tag = [e for e in _errors(_document_findings(validator, doc, "connector"))
           if e["message_id"] == "union_tag_invalid"]
    assert tag, doc["auth"]
    assert "…" not in tag[0]["message"], tag[0]["message"]
    assert "'none'" in tag[0]["message"], tag[0]["message"]


def test_a_borrowed_diagnostic_keeps_the_constraint_it_was_rejected_by(validator):
    # The other half of the bound: a sentence pydantic builds out of the
    # contract's own pattern carries no input at all, and its whole length is
    # the vocabulary an author needs to fix the value. Clipping it leaves them
    # a fragment of the legal alternatives and an ellipsis.
    doc = _type_map_doc(
        read=[{"match": "exact", "native_type": "X", "arrow_type": "NotAnArrowFamily"}])
    [error] = _errors(_document_findings(validator, doc, "type-map"))
    assert error["message_id"] == "string_pattern_mismatch", error
    assert error["message"].endswith("'"), error["message"][-80:]
    assert "…" not in error["message"], len(error["message"])


# --- database-endpoint kind ---

def test_database_endpoint_valid_and_invalid(validator):
    db = {"$schema": "https://schemas.analitiq.ai/database-endpoint/latest.json",
          "endpoint_id": derive_db_endpoint_id(None, "public", "orders"),
          "database_object": {"schema": "public", "name": "orders", "object_type": "table"},
          "columns": [{"name": "id", "native_type": "uuid", "arrow_type": "Utf8"}]}
    assert not _errors(_document_findings(validator, db, "database-endpoint"))
    bad = json.loads(json.dumps(db))
    bad["columns"][0]["arrow_type"] = "NotAnArrowType"
    assert _errors(_document_findings(validator, bad, "database-endpoint"))


# --- advisory warnings ---

def _warnings(findings):
    return [f for f in findings if f["severity"] == "warning"]


def test_duplicate_type_map_rule_warns(validator):
    rules = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"},
             {"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
    warns = _warnings(_document_findings(validator, _type_map_doc(read=rules), "type-map"))
    assert any("duplicate" in w["message"] for w in warns)


def test_duplicate_exact_read_rule_warns_across_case_and_whitespace(validator):
    # Two exact READ rules differing only by case/whitespace collapse to one
    # matcher at runtime (first wins), so the second is unreachable — the dedup
    # must normalize the same way the reader does and flag it, even when the
    # rules map to DIFFERENT canonicals (a real, if rare, authoring bug).
    rules = [{"match": "exact", "native_type": "character varying", "arrow_type": "Utf8"},
             {"match": "exact", "native_type": "CHARACTER  VARYING", "arrow_type": "LargeUtf8"}]
    warns = _warnings(_document_findings(validator, _type_map_doc(read=rules), "type-map"))
    assert any("duplicate" in w["message"] for w in warns)


# Each row names a witness the expectation is graded against, so the table is
# checked by RE2 itself and not only by the check under test: a silent row's
# witness is a native whose normalized form the pattern matches, and a warning
# or missed row's witness is a spelling the pattern matches only before
# normalization. A missed row is dead and draws no warning: the check reads the
# matcher's literal characters roughly.
@pytest.mark.parametrize("native,verdict,witness", [
    (r"^varchar$", "warns", "varchar"),
    (r"(?i)^varchar$", "silent", "varchar"),
    (r"^VAR(?i:char)$", "silent", "VARCHAR"),
    (r"(?i:var)CHAR", "silent", "VARCHAR"),
    (r"^A(?i)b|c$", "silent", "c"),
    (r"^(?s)VAR.$", "silent", "VARX"),
    (r"^\p{Lu}+$", "silent", "VARCHAR"),
    (r"^\pL+$", "silent", "VARCHAR"),
    (r"^\P{Ll}+$", "silent", "VARCHAR"),
    (r"^VAR\z", "silent", "VAR"),
    (r"^\Qvar\E$", "warns", "var"),
    (r"^[\p{Ll}]$", "silent", "ĸ"),
    (r"^A[]a]B$", "silent", "A]B"),
    (r"^FOO(?<x>[A-Za-z]+)$", "silent", "FOOX"),
    (r"^varchar\((?<n>\d+)\)$", "warns", "varchar(1)"),
    (r"^[a-z]+$", "missed", "abc"),
    (r"^(?i)(?-i:a)$", "missed", "a"),
    # Refused by the contract; the model reports it.
    (r"^(?P<x>\d)X$", "refused", None),
    (r"^A(?<t>[0-9])B\k<t>$", "refused", None),
])
def test_regex_lowercase_literal_warning_truth_table(validator, native, verdict, witness):
    import re2

    from analitiq.contracts.type_map import normalize_native_type

    if verdict == "silent":
        assert re2.fullmatch(native, normalize_native_type(witness)), witness
    elif verdict in ("warns", "missed"):
        assert re2.fullmatch(native, witness), witness
        assert not re2.fullmatch(native, normalize_native_type(witness)), witness
    findings = _document_findings(
        validator, _type_map_doc(read=[{"match": "regex", "native_type": native, "arrow_type": "Utf8"}]),
        "type-map")
    refused = [e for e in _errors(findings) if e.get("rule") == "RULE-TMAP-005"]
    assert bool(refused) is (verdict == "refused"), findings
    if verdict != "refused":
        dead = [w for w in _warnings(findings)
                if w.get("rule") == "RULE-TMAP-014" and "can never match" in w["message"]]
        assert bool(dead) is (verdict == "warns"), findings


@pytest.mark.parametrize("native,arrow_type,warns", [
    (r"^ARRAY<(?<t>[A-Z]+)>$", "Utf8", True),
    (r"^ARRAY<(?<t>[A-Z]+)>$", "Json", False),
    (r"^INT\[\]$", "Utf8", True),
    (r"^ARRAY\Q<INT>\E$", "Utf8", True),
    # A class is dropped whole, so its members are no container syntax.
    (r"^A[<>]B$", "Utf8", False),
    # Alternation is not read: the reading keeps every branch's literals.
    (r"^A(?:<|>)B$", "Utf8", True),
    (r"^(?:INT\[|X)\]$", "Utf8", False),
])
def test_regex_read_rule_container_warning(validator, native, arrow_type, warns):
    findings = _document_findings(
        validator, _type_map_doc(read=[{"match": "regex", "native_type": native, "arrow_type": arrow_type}]),
        "type-map")
    assert not _errors(findings), findings
    collapsed = [w for w in _warnings(findings)
                 if w.get("rule") == "RULE-TMAP-002" and w.get("message_id") == "read-regex-container-collapsed"]
    assert bool(collapsed) is warns, findings
    assert [w["path"] for w in collapsed] == ["/read/0"] * len(collapsed), collapsed


def test_regex_warnings_stay_cheap_across_a_whole_map(validator):
    rules = [{"match": "regex",
              "native_type": rf"^T{i}x?\((?<n>[1-9]\d*)\)[a-z]*\p{{Lu}}?$",
              "arrow_type": "Utf8"} for i in range(50)]
    started = time.perf_counter()
    findings = _document_findings(validator, _type_map_doc(read=rules), "type-map")
    elapsed = time.perf_counter() - started
    assert sum(w.get("rule") == "RULE-TMAP-014" for w in _warnings(findings)) == 50, findings
    assert elapsed < 1.0, elapsed


def test_write_vocabulary_gap_warns(validator):
    # A write map missing whole canonical families → advisory warning.
    findings = _document_findings(
        validator, _type_map_doc(write=[{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}]),
        "type-map")
    assert any(w.get("rule") == "RULE-TMAP-017" for w in _warnings(findings))


def test_write_vocabulary_probes_bare_container_markers(validator):
    # The engine probes the write map with a destination column's `arrow_type`
    # verbatim, and API-sourced documents carry the bare `Object`/`List` shape
    # markers — a map without rules for them hard-errors the stream at
    # configuration. The coverage warning must name both.
    findings = _document_findings(
        validator, _type_map_doc(write=[{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}]),
        "type-map")
    # StopIteration here is the failure signal working, not a case to guard:
    # no coverage warning at all means the probe stopped running.
    gap = next(  # skipcq: PTC-W0063
        w for w in _warnings(findings) if w.get("rule") == "RULE-TMAP-017"
    )
    assert "'Object'" in gap["message"] and "'List'" in gap["message"]

    covered = [{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"},
               {"match": "exact", "arrow_type": "Object", "native_type": "JSONB"},
               {"match": "exact", "arrow_type": "List", "native_type": "JSONB"}]
    findings = _document_findings(validator, _type_map_doc(write=covered), "type-map")
    # Covering the two markers must narrow the warning, not silence it — the map
    # still lacks rules for other probes. So StopIteration here is the failure
    # signal working: it means the warning vanished entirely, which would make
    # the assertion below pass for the wrong reason.
    gap = next(  # skipcq: PTC-W0063
        w for w in _warnings(findings) if w.get("rule") == "RULE-TMAP-017"
    )
    assert "'Object'" not in gap["message"] and "'List'" not in gap["message"]


def test_write_vocabulary_fully_covered_map_warns_nothing(validator):
    # Every probe must be satisfiable by a realistic map, and a map covering
    # them all must clear the warning entirely — otherwise an unsatisfiable
    # probe (a typo, or a family no exact/regex rule can express) would warn
    # on every author's map forever, teaching authors to ignore the signal.
    # Mirrors the reference postgresql example: the Decimal/Time/Timestamp
    # families are covered by regex on purpose, pinning that a regex rule
    # fullmatching the bare probe satisfies it.
    full_map = [
        {"match": "exact", "arrow_type": c, "native_type": n}
        for c, n in [
            ("Boolean", "BOOLEAN"), ("Int8", "SMALLINT"), ("Int16", "SMALLINT"),
            ("Int32", "INTEGER"), ("Int64", "BIGINT"), ("UInt8", "SMALLINT"),
            ("UInt16", "INTEGER"), ("UInt32", "BIGINT"), ("UInt64", "NUMERIC(20, 0)"),
            ("Float16", "REAL"), ("Float32", "REAL"), ("Float64", "DOUBLE PRECISION"),
            ("Utf8", "TEXT"), ("LargeUtf8", "TEXT"), ("Json", "JSONB"),
            ("Object", "JSONB"), ("List", "JSONB"), ("Binary", "BYTEA"),
            ("LargeBinary", "BYTEA"), ("Date32", "DATE"), ("Date64", "DATE"),
            ("Null", "TEXT"),
        ]
    ] + [
        {"match": "regex", "arrow_type": r"^Decimal(128|256)\((?<p>\d+),\s*(?<s>\d+)\)$",
         "native_type": "NUMERIC(${p}, ${s})"},
        {"match": "regex", "arrow_type": r"^Time(32|64)\([A-Z]+\)$", "native_type": "TIME"},
        {"match": "regex", "arrow_type": r"^Timestamp\([A-Z]+\)$", "native_type": "TIMESTAMP"},
        {"match": "regex", "arrow_type": r"^Duration\([A-Z]+\)$", "native_type": "INTERVAL"},
    ]
    findings = _document_findings(validator, _type_map_doc(write=full_map), "type-map")
    coverage = [f for f in findings if f.get("rule") == "RULE-TMAP-017"]
    assert not coverage, coverage


_COVERAGE_RULE_IDS = {"RULE-PKG-030", "RULE-PKG-032", "RULE-PKG-033", "RULE-PKG-035"}


# Each broken state of a package's read section that still parses, with what
# reports the map itself.
_BROKEN_READ_MAPS = (
    ("missing", None, "the package carries no type map"),
    ("no-read-section", _type_map_doc(write=_write_rules()), "its type map carries no 'read' section"),
    ("not-a-list", {"$schema": _TM_SCHEMA, "read": {}}, "Input should be a valid list"),
    ("not-an-object", [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
     "Input should be a valid dictionary"),
)

# What the defective endpoints below provoke: a rule id, and a fragment of the
# verdict that check writes. The fragment is what makes the assertion grade the
# check instead of its id — a check that crashes is reported under its own id
# too, and an id-only assertion would read that as the check having run. The
# document defects are graded from each endpoint alone; the package defects need
# the endpoint's key or its connector beside it.
_DOCUMENT_DEFECTS = {
    "RULE-ENDP-046": "must equal 'v1__widgets'",
    "RULE-ENDP-048": "is not a valid JSON Schema Draft 2020-12",
    "RULE-ENDP-063": "which the node declaring it rejects",
}
_PACKAGE_DEFECTS = {
    "RULE-PKG-031": "must be named 'widgets.json'",
    "RULE-ENDP-047": "is not declared in the package's connector",
}

# The verdicts the native→Arrow rendering writes. A map that renders nothing
# feeds no rendering, so a finding carrying any of these fragments contradicts
# the finding that says coverage went unrendered.
_RENDERED_COVERAGE = ("has no matching rule in", "resolves to")


def _defective_package(connector: dict, type_map) -> dict:
    """A connector package carrying every defect `_DOCUMENT_DEFECTS` and
    `_PACKAGE_DEFECTS` name, beside `type_map` — a document, `_RawText`, or
    None for no map at all."""
    # filename ≠ endpoint_id; endpoint_id ≠ the handle its path derives;
    # transport_ref names a transport the connector does not declare; a recorded
    # sample contradicts the node declaring it.
    ep = _endpoint("STRING", "Utf8", endpoint_id="widgets", path="/v1/widgets")
    ep["operations"]["read"]["request"]["transport_ref"] = "undeclared"
    ep["operations"]["read"]["response"]["schema"]["items"]["properties"]["a"]["examples"] = [123]
    # Sample grading is skipped on a schema that is unreadable as Draft 2020-12,
    # so the meta-schema defect rides its own endpoint.
    meta = _endpoint("STRING", "Utf8", endpoint_id="meta", path="/meta")
    meta["operations"]["read"]["response"]["schema"]["minItems"] = "notanumber"
    return {
        _CONNECTOR_KEY: connector,
        **({} if type_map is None else {_TYPE_MAP_KEY: type_map}),
        _endpoint_key("misnamed.json"): ep,
        _endpoint_key("meta.json"): meta,
    }


def _defects_reported(findings, defects: dict) -> set[str]:
    return {vid for vid, fragment in defects.items()
            if any(f.get("rule") == vid and fragment in f["message"] for f in findings)}


@pytest.mark.parametrize("state,type_map,reported", _BROKEN_READ_MAPS, ids=[s for s, _, _ in _BROKEN_READ_MAPS])
def test_endpoint_checks_run_when_read_map_is_broken(connector_base, validator, state, type_map, reported):
    errors = _errors(_package_findings(validator, _defective_package(connector_base, type_map)))
    assert any(reported in e["message"] for e in errors), errors
    every_defect = {**_DOCUMENT_DEFECTS, **_PACKAGE_DEFECTS}
    assert _defects_reported(errors, every_defect) == set(every_defect), (
        sorted(_defects_reported(errors, every_defect)), [e["message"] for e in errors])


# Text that does not parse: a decode error, and what the parser refuses without
# one — nesting deeper than it descends, and an integer past its digit limit.
_UNPARSEABLE_TEXT = {
    "not json": "{ not json",
    "nested past the parser": "[" * 100_000 + "]" * 100_000,
    "integer past the digit limit": '{"n": 1' + "0" * 5_000 + "}",
}


@pytest.mark.parametrize("text", list(_UNPARSEABLE_TEXT.values()), ids=list(_UNPARSEABLE_TEXT))
def test_an_unparseable_map_withholds_only_the_package_checks(connector_base, validator, text):
    # The package checks cannot tell a missing section from one in a map they
    # could not read, so they are withheld, never crashed through; each
    # endpoint is still graded on its own.
    findings = _package_findings(validator, _defective_package(connector_base, _RawText(text)))
    assert [f["message_id"] for f in _about(findings, _TYPE_MAP_KEY)] == ["unreadable-document"], findings
    assert _defects_reported(findings, _DOCUMENT_DEFECTS) == set(_DOCUMENT_DEFECTS), findings
    assert not _defects_reported(findings, _PACKAGE_DEFECTS), findings
    assert not {f.get("rule") for f in findings} & _COVERAGE_RULE_IDS, findings


@pytest.mark.parametrize("type_map", [t for _, t, _ in _BROKEN_READ_MAPS], ids=[s for s, _, _ in _BROKEN_READ_MAPS])
def test_unrendered_coverage_is_reported_not_silent(connector_base, validator, type_map):
    # The endpoint documents come back graded, so a reader must be told that the
    # one check the map feeds did not run — silence there reads as coverage passing.
    findings = _package_findings(validator, _defective_package(connector_base, type_map))
    assert any(f.get("rule") == "RULE-PKG-033" and f["kind"] == "notApplicable"
               and "not rendered" in f["message"] for f in findings), findings
    # ... and that finding is the whole of what coverage says here: a rendering
    # that never ran cannot also return per-endpoint verdicts.
    assert not [f for f in findings
                if any(fragment in f["message"] for fragment in _RENDERED_COVERAGE)], findings



def test_a_clean_package_emits_no_coverage_finding(connector_base, validator):
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"widgets.json": _endpoint("STRING", "Utf8")})
    findings = _package_findings(validator, package)
    assert [f for f in findings if f.get("rule") in _COVERAGE_RULE_IDS] == [], findings


def test_rendered_coverage_reports_only_the_uncovered_native(connector_base, validator):
    # A readable map renders, so the uncovered native is the only thing coverage
    # has to say — no warning about a rendering that did happen.
    package = _connector_package(
        connector_base,
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        {"widgets.json": _endpoint("BIGINT", "Int64")})
    findings = _package_findings(validator, package)
    coverage = [f for f in findings if f.get("rule") == "RULE-PKG-033"]
    assert len(coverage) == 1 and coverage[0]["severity"] == "error", coverage
    assert "no matching rule" in coverage[0]["message"], coverage


# ---------------------------------------------------------------------------
# One type-map document graded alone.
# ---------------------------------------------------------------------------

def test_a_type_map_grades_each_section_as_its_direction(validator):
    # `native_type` is read as a render template only under `write`, so the
    # same rule is clean under one key and a defect under the other: the key a
    # rule list sits under is what decides the model it is measured against.
    rule = [{"match": "exact", "native_type": "VARCHAR${", "arrow_type": "Utf8"}]
    assert not _errors(_document_findings(validator, _type_map_doc(read=rule), "type-map"))
    errors = _errors(_document_findings(validator, _type_map_doc(write=rule), "type-map"))
    assert [(e["path"], e["message_id"]) for e in errors] == [
        ("/write/0", "write-exact-malformed-placeholder")], errors


def test_a_type_map_points_each_advisory_into_its_section(validator):
    # A rule's matcher is `native_type` under `read` and `arrow_type` under
    # `write`, so each section is deduplicated on its own matcher and every
    # warning points into the section it is about.
    doc = _type_map_doc(read=_read_rules() * 2, write=_write_rules() * 2)
    duplicates = [f["path"] for f in _document_findings(validator, doc, "type-map")
                  if f["message_id"] == "duplicate-type-map-rule"]
    assert duplicates == ["/read/1", "/write/1"], duplicates


def test_a_type_map_reads_no_section_as_the_other_direction(validator):
    # These four are a legitimate many-to-one write mapping; read as matching on
    # `native_type`, they are one native matched four times.
    doc = _type_map_doc(write=[{"match": "exact", "arrow_type": a, "native_type": "BIGINT"}
                               for a in ("Int8", "Int16", "Int32", "Int64")])
    # The write-vocabulary warning is the only finding the section earns.
    assert [f.get("rule") for f in _document_findings(validator, doc, "type-map")] == ["RULE-TMAP-017"]


def test_a_type_map_reports_its_own_crash_as_unchecked(validator, monkeypatch):
    # a crash leaves the model errors and every advisory rule unevaluated
    # together, so the finding names no rule — and that is exactly what makes it
    # cost a pass rather than read as "nothing was wrong"
    from analitiq.validator import connectors
    monkeypatch.setattr(connectors, "_type_map_rule_warnings",
                        lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")))
    findings = _document_findings(validator, _type_map_doc(read=_read_rules()), "type-map")
    assert len(findings) == 1, findings
    assert findings[0].get("rule") is None, findings[0]
    assert findings[0]["message_id"] == "check-crashed", findings[0]
    assert findings[0]["kind"] == "notApplicable", findings[0]
    assert validator.finding_costs_a_pass(findings[0]) is True



