"""End-to-end validation tests — the validator delegates single-document
validity to the contract models and adds the cross-file coverage checks.

The `invalid_write_from_input` case is the original sevdesk defect that started
this work: a write body mapping a bare field name (`{from_input: "category"}`)
instead of the record. The model rejects it, so the validator now catches it —
the gap the old validator missed.
"""
import errno
import json
import os
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
from analitiq.validator._location import DiskTree
from analitiq.validator.connectors import (
    _DATABASE_KINDS,
    _STORAGE_KINDS,
    _STRAY_TYPE_MAP_GLOB,
    TYPE_MAP_FILENAME,
)

def _raise_timeout(signum, frame):
    raise AssertionError("the check read a sibling that is not a regular file")


CORPUS = Path(__file__).resolve().parent / "corpus"
_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_SRC_ROOT = _REPO_ROOT / "contract-models" / "src"
SRC_ROOT = _REPO_ROOT / "validator" / "src"
# Drive the CLI the way a consumer does: import the package and call main(). With
# `python -c "<code>" --document X`, argv is ["-c", "--document", "X"], so argparse
# parses the flags exactly as the `analitiq-validate` console script would. Only
# the two public source trees ride PYTHONPATH — the validator and the contract
# models — so this exercises precisely what an installed consumer gets.

# (corpus file, expected pass?) — single-document verdicts.
DOC_CASES = [
    ("valid_read.json", True),
    ("valid_write_insert.json", True),
    ("valid_connector_sync_driver.json", True),
    ("invalid_reserved_field.json", False),
    ("invalid_write_from_input.json", False),
    ("invalid_connector_bare_driver.json", False),
]


def _errors(findings):
    return [f for f in findings if f.get("severity") == "error"]


@pytest.mark.parametrize("name,should_pass", DOC_CASES)
def test_single_document_verdict(name, should_pass, validator):
    # No doc_path: these exercise pure single-document validity, not the
    # filename↔endpoint_id cross-file check (the corpus filenames are labels).
    doc = json.loads((CORPUS / name).read_text())
    findings = validator.validate_document(doc)
    errors = _errors(findings)
    assert (not errors) == should_pass, (
        f"{name}: expected {'pass' if should_pass else 'fail'}, "
        f"got errors={[e['message'] for e in errors]}"
    )


def test_from_input_defect_is_caught(validator):
    """The sevdesk regression: a bare-field from_input write body must be rejected."""
    doc = json.loads((CORPUS / "invalid_write_from_input.json").read_text())
    findings = validator.validate_document(doc)
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
    of validate_document, not just the pydantic layer."""
    doc = json.loads((CORPUS / "invalid_connector_bare_driver.json").read_text())
    errors = _errors(validator.validate_document(doc))
    assert any(
        f.get("rule") is None and f["path"].endswith("/driver")
        for f in errors
    ), errors


def test_unrecognized_document_errors(validator):
    # A document we were asked to validate but cannot identify is a failure,
    # not a pass — otherwise a broken doc silently gets a green light.
    for doc in ({"totally": "unknown"}, {}, 42, "hello", None):
        findings = validator.validate_document(doc)
        assert _errors(findings), f"{doc!r} should error, got {findings}"


def test_kindless_connector_errors(validator):
    # A connector-shaped dict missing its `kind` discriminator must reach the
    # model and fail (not fall through to a silent pass).
    doc = {"connector_id": "x", "transports": {}, "connection_contract": {},
           "default_transport": "m"}
    assert _errors(validator.validate_document(doc))


_TM_SCHEMA = TYPE_MAP_SCHEMA_URL


def _type_map_doc(read=None, write=None):
    """A `type-map.json` document carrying each section given."""
    sections = {"read": read, "write": write}
    return {"$schema": _TM_SCHEMA, **{d: r for d, r in sections.items() if r is not None}}


def _write_tree(root: Path, connector: dict, read_map, endpoints: dict):
    (root / "endpoints").mkdir(parents=True)
    (root / "connector.json").write_text(json.dumps(connector))
    (root / TYPE_MAP_FILENAME).write_text(json.dumps(_type_map_doc(read=read_map)))
    for name, ep in endpoints.items():
        (root / "endpoints" / name).write_text(json.dumps(ep))


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
        for e in _errors(validator.validate_document(ep))
    )


def test_embedded_schema_must_be_valid_draft_2020_12(validator):
    """An embedded input/response schema that parses (arrow-valid) but is not a
    valid JSON Schema Draft 2020-12 document is caught by the validator. The
    contract model checks the arrow_type pairing, not meta-schema validity."""
    ep = _endpoint("STRING", "Utf8")
    # `minItems` must be a non-negative integer; a string is meta-invalid, and the
    # contract model doesn't inspect it, so only RULE-ENDP-048 fires.
    ep["operations"]["read"]["response"]["schema"]["minItems"] = "notanumber"
    errors = _errors(validator.validate_document(ep))
    assert any(e.get("rule") == "RULE-ENDP-048" for e in errors), errors


def test_embedded_schema_rejects_other_dialect(validator):
    """A meta-valid schema that DECLARES another draft (e.g. Draft-07) is not
    Draft 2020-12 and is rejected — `check_schema` alone would miss it."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["$schema"] = (
        "http://json-schema.org/draft-07/schema#"
    )
    errors = _errors(validator.validate_document(ep))
    assert any(e.get("rule") == "RULE-ENDP-048" for e in errors), errors


def test_embedded_schema_accepts_the_empty_fragment_spelling(validator):
    """`…/2020-12/schema#` and `…/2020-12/schema` identify the same resource
    under RFC 3986, and a JSON Schema implementation maps either spelling onto
    the 2020-12 dialect. The contract accepts either."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["$schema"] = f"{JS}#"
    errors = _errors(validator.validate_document(ep))
    assert not any(e.get("rule") == "RULE-ENDP-048" for e in errors), errors


def test_embedded_schema_rejects_a_repeated_empty_fragment(validator):
    """A single trailing `#` is the empty fragment. `##` is not a spelling of
    this URI, so it names something other than the dialect the contract is
    written in and is refused — stripping every trailing `#` would accept it."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["$schema"] = f"{JS}##"
    errors = _errors(validator.validate_document(ep))
    assert any(e.get("rule") == "RULE-ENDP-048" for e in errors), errors


def test_a_nested_dialect_declaration_is_a_contract_model_error(validator):
    """`$schema` below the root of an embedded schema is refused by the
    contract model, whatever its value, and nothing downstream of that model
    crashes on it — a "validator bug" finding would hide the actionable one and
    blame the tool for a defect in the document. Attributed to RULE-ENDP-064,
    the walker rule this exact "$schema on a subschema" complaint belongs to."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["items"]["$schema"] = 5
    findings = validator.validate_document(ep)
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
    findings = validator.validate_document(ep)
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
        # Isolated to the model-validation pass itself (not the full
        # validate_document dispatch, which also runs cross-document checks
        # on the same api-endpoint doc — RULE-ENDP-046/047/048/063 — that
        # would otherwise leak into these exact-list assertions): a finding
        # no longer names which pass produced it, so this is now the only
        # way to isolate what a `MultiRuleViolation` from the walker rules
        # this class exercises expands into.
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


def test_coverage_passes_when_map_covers_endpoints(tmp_path, connector_base, validator):
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    findings = validator.validate_document(connector_base, doc_path=tmp_path / "connector.json")
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_coverage_passes_with_lowercase_exact_matcher(tmp_path, connector_base, validator):
    # A lowercase `exact` native the runtime resolves fine must not be
    # reported as uncovered. The endpoint declares `varchar`; the runtime
    # normalizes both sides and matches, so coverage must too.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "varchar", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("varchar", "Utf8")})
    findings = validator.validate_document(connector_base, doc_path=tmp_path / "connector.json")
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
        tmp_path, connector_base, validator, rules, read_pair, write_pair):
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": n, "arrow_type": c} for n, c in rules],
                {"widgets.json": _read_and_write(*read_pair, *write_pair)})
    errors = _errors(validator.validate_document(
        connector_base, doc_path=tmp_path / "connector.json"))
    assert not errors, [e["message"] for e in errors]


def test_one_token_cannot_carry_two_canonicals(tmp_path, connector_base, validator):
    # The write entry declaring the naive spelling under the read entry's token is
    # rejected: that token already resolves to the zoned canonical. Two entries that
    # resolve differently need two tokens, which the domain type map then spells.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "date-time", "arrow_type": _UTC}],
                {"widgets.json": _read_and_write("date-time", _UTC, "date-time", _NAIVE)})
    errors = _errors(validator.validate_document(
        connector_base, doc_path=tmp_path / "connector.json"))
    assert any("resolves to" in e["message"] and _NAIVE in e["message"] for e in errors), \
        [e["message"] for e in errors]


def test_coverage_flags_uncovered_native(tmp_path, connector_base, validator):
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("BIGINT", "Int64")})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("no matching rule" in e["message"] for e in errors)


def test_coverage_flags_arrow_mismatch(tmp_path, connector_base, validator):
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Int64")})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("resolves to" in e["message"] and "Int64" in e["message"] for e in errors)


def test_coverage_still_runs_when_the_read_map_fails_its_model(tmp_path, connector_base, validator):
    # A map carrying a `read` section has named what it covers, whatever else
    # about it the model rejects. `check_coverage` reads that
    # section from the raw dict rather than from the (rejected) model instance,
    # so endpoint coverage runs against those rules instead of silently no-oping
    # behind the envelope's own error — staged with an endpoint the map does not
    # cover, so only coverage having run can produce the second finding.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("BIGINT", "Int64")})
    read_map_path = tmp_path / TYPE_MAP_FILENAME
    doc = json.loads(read_map_path.read_text())
    doc["$schema"] = CONNECTOR_SCHEMA_URL
    read_map_path.write_text(json.dumps(doc))
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any(e["path"] == f"{TYPE_MAP_FILENAME}#/$schema" for e in errors), errors
    assert any("no matching rule" in e["message"] for e in errors), errors


def test_coverage_flags_missing_read_map(tmp_path, connector_base, validator):
    (tmp_path / "connector.json").write_text(json.dumps(connector_base))
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any(e["message_id"] == "read-map-missing" for e in errors), errors


def test_an_endpoint_that_holds_a_json_null_is_graded(
        tmp_path, connector_base, validator):
    # A file holding `null` is read successfully and holds no document. A walk
    # that reads the loader's VALUE cannot tell that from a file it failed to
    # read, so it drops the document reporting nothing — a clean pass over a
    # file nothing graded. Every other malformed payload is rejected by the
    # model, which is what keeps the gap out of sight.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    (tmp_path / "endpoints" / "widgets.json").write_text("null")
    errors = _errors(validator.validate_document(
        connector_base, doc_path=tmp_path / "connector.json"))
    assert errors, "an endpoint holding a JSON null was graded by nothing"


def test_a_sibling_connector_that_holds_a_json_null_is_not_called_unparseable(
        tmp_path, connector_base, validator):
    # The endpoint-anchored route names why `transports` could not be read, and
    # the reasons ask for different edits: a file that did not parse is fixed by
    # fixing its JSON, one holding `null` by writing a connector into it.
    # Reading the loader's value collapses the second into the first and sends
    # the author looking for a syntax error that is not there.
    (tmp_path / "connector.json").write_text("null")
    ep_path = tmp_path / "endpoints" / "widgets.json"
    ep_path.parent.mkdir(parents=True)
    ep_doc = _keyset_endpoint(initial=None, transport_ref="main")
    ep_path.write_text(json.dumps(ep_doc))
    findings = validator.validate_document(ep_doc, doc_path=ep_path)
    skipped = [f for f in findings
               if str(f.get("message_id", "")).startswith("transport-ref-check-skipped")]
    assert skipped, [f.get("message_id") for f in findings]
    assert skipped[0]["message_id"] != "transport-ref-check-skipped-unparseable", (
        skipped[0]["message"])


def test_coverage_flags_missing_endpoints_directory(tmp_path, connector_base, validator):
    # RULE-PKG-035: an API connector's release ships at least one endpoint
    # document. No `endpoints/` directory at all is the more severe of its two
    # ways to fail — nothing here even attempts to name an endpoint.
    (tmp_path / "connector.json").write_text(json.dumps(connector_base))
    (tmp_path / TYPE_MAP_FILENAME).write_text(json.dumps(_type_map_doc(
        read=[{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}])))
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("endpoints" in e["message"] and "missing" in e["message"] for e in errors)


def test_coverage_flags_empty_endpoints_directory(tmp_path, connector_base, validator):
    # RULE-PKG-035's other way to fail: the directory exists but ships no
    # endpoint document — a connector authored to look complete at the
    # filesystem level while offering nothing to validate against.
    (tmp_path / "endpoints").mkdir()
    (tmp_path / "connector.json").write_text(json.dumps(connector_base))
    (tmp_path / TYPE_MAP_FILENAME).write_text(json.dumps(_type_map_doc(
        read=[{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}])))
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("endpoints" in e["message"] and "no *.json files" in e["message"] for e in errors)


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


def test_coverage_json_narrowing_allowed(tmp_path, connector_base, validator):
    # A read map that renders `Json` satisfies an endpoint declaring `Object`.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "JSONB", "arrow_type": "Json"}],
                {"widgets.json": _object_endpoint()})
    assert not _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))


def test_coverage_json_narrowing_is_narrow(tmp_path, connector_base, validator):
    # ...but `Json` does NOT satisfy a scalar like `Int64` (the allowance is narrow).
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "JSONB", "arrow_type": "Json"}],
                {"widgets.json": _endpoint("JSONB", "Int64")})
    assert _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))


def test_coverage_checks_field_named_like_a_keyword(tmp_path, connector_base, validator):
    # A response field literally named `default` must still be coverage-checked
    # (the schema-aware walk treats `properties` children as field names).
    ep = {"$schema": API, "endpoint_id": "widgets",
          "operations": {"read": {
              "request": {"method": "GET", "path": "/widgets"}, "params": {},
              "response": {"records": {"ref": "response.body"},
                  "schema": {"$schema": JS, "type": "array", "items": {"type": "object",
                      "properties": {"default": {"type": "string",
                          "native_type": "WEIRDTYPE", "arrow_type": "Utf8"}}}}}}}}
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],  # no WEIRDTYPE rule
                {"widgets.json": ep})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("WEIRDTYPE" in e["message"] and "no matching rule" in e["message"] for e in errors)


def test_coverage_exact_match_normalizes_both_sides(validator):
    # Mirrors the runtime reader: an `exact` rule's `native_type` is
    # normalized the same way as the probe — trim, collapse internal whitespace
    # runs, uppercase — on BOTH sides. So a lowercase or extra-spaced matcher
    # covers the (normalized) endpoint native, exactly as the runtime resolves
    # it — the validator is no longer stricter than the runtime.
    assert validator._render_arrow_type("STRING", [{"match": "exact", "native_type": "string", "arrow_type": "Utf8"}]) == "Utf8"
    assert validator._render_arrow_type("STRING", [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]) == "Utf8"
    # Whitespace: a two-space matcher covers a single-space native.
    assert validator._render_arrow_type("character varying", [{"match": "exact", "native_type": "CHARACTER  VARYING", "arrow_type": "Utf8"}]) == "Utf8"
    # A genuinely different native is still uncovered.
    assert validator._render_arrow_type("STRING", [{"match": "exact", "native_type": "BIGINT", "arrow_type": "Int64"}]) is None


def test_coverage_matches_a_regex_rule_with_re2_semantics(validator):
    # RE2's `\d` is ASCII, so a rule spelling digits with it does not cover a
    # non-ASCII digit.
    rules = [{"match": "regex", "native_type": r"^N\d$", "arrow_type": "Int8"}]
    assert validator._render_arrow_type("N3", rules) == "Int8"
    assert validator._render_arrow_type("N٣", rules) is None


def test_coverage_matches_no_regex_rule_against_a_native_re2_cannot_read(validator):
    # An endpoint's raw JSON can spell a lone surrogate, which has no UTF-8
    # encoding for RE2 to read, so the native is left uncovered rather than
    # crashing the check.
    rules = [{"match": "regex", "native_type": r"^A.*$", "arrow_type": "Utf8"}]
    assert validator._render_arrow_type("A\ud800", rules) is None


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


def test_write_render_returns_promptly_on_a_nested_quantifier():
    from analitiq.validator.connectors import _first_match_render

    rules = [{"match": "regex", "arrow_type": r"(A+)+$", "native_type": "TEXT"}]
    assert _within(5, lambda: _first_match_render(
        _NEAR_MATCH, rules, "arrow_type", "native_type")) is None


def test_read_coverage_returns_promptly_on_a_nested_quantifier(tmp_path, connector_base, validator):
    _write_tree(tmp_path, connector_base,
                [{"match": "regex", "native_type": r"(A+)+$", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint(_NEAR_MATCH, "Utf8")})
    findings = _within(5, lambda: validator.check_coverage(
        connector_base, tmp_path / "connector.json"))
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
    eq = validator._arrow_type_eq
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
    pairs = validator._collect_native_arrow_pairs(ep)
    assert ("WEIRDTYPE", "Utf8", "/operations/read/response/schema/items/0/properties/x") in pairs


def test_coverage_regex_rule_with_capture(tmp_path, connector_base, validator):
    # A regex read rule with a named capture + ${name} render must resolve.
    _write_tree(tmp_path, connector_base,
                [{"match": "regex", "native_type": r"NUMERIC\((?<p>[1-9]|[12]\d|3[0-8]),\s*(?<s>\d|[12]\d|3[0-8])\)",
                  "arrow_type": "Decimal128(${p}, ${s})"}],
                {"widgets.json": _endpoint("NUMERIC(38,9)", "Decimal128(38, 9)")})
    assert not _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))


def test_coverage_flags_duplicate_endpoint_id(tmp_path, connector_base, validator):
    # Two endpoint files sharing an endpoint_id are flagged
    # as a duplicate (spec: endpoint_id unique within the connector release),
    # not only obliquely as a filename mismatch.
    ep = _endpoint("STRING", "Utf8", endpoint_id="dup", path="/dup")
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"dup.json": ep, "other.json": ep})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any(e.get("rule") == "RULE-PKG-032" and "dup" in e["message"] for e in errors)


def test_coverage_distinct_endpoint_ids_pass(tmp_path, connector_base, validator):
    # Two endpoints with distinct ids (matching filenames + paths) raise no error.
    a = _endpoint("STRING", "Utf8", endpoint_id="alpha", path="/alpha")
    b = _endpoint("STRING", "Utf8", endpoint_id="beta", path="/beta")
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"alpha.json": a, "beta.json": b})
    assert not _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))


# --- endpoint_id must be the derived path locator (io-contracts resources[].key) ---

def test_flatten_api_locator(validator):
    f = validator._flatten_api_locator
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
    assert validator._endpoint_locator_findings(
        {"endpoint_id": "v1__records",
         "operations": {"read": {"request": {"path": "/v1/records"}}}}) == []
    # leaf-only id for a versioned path -> flagged with the expected handle
    errs = validator._endpoint_locator_findings(
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
        assert validator._endpoint_locator_findings(doc) == [], write_path
    # A write-only endpoint derives from its write path.
    write_only = {"endpoint_id": "v1__events",
                  "operations": {"write": {"insert": {"request": {"path": "/v1/events"}}}}}
    assert validator._endpoint_locator_findings(write_only) == []


def test_endpoint_locator_non_derivable_path_errors(validator):
    # A path with NO derivable id is a hard gate failure (not a warning that would
    # let a decoupled endpoint_id through), and the message is about the PATH — never
    # a self-contradictory "must equal <invalid-id>".
    # (a) non-charset-safe (Shopify-style `.json`); (b) all-path-param (empty handle).
    for path in ("/admin/api/2024-01/orders.json", "/{id}"):
        doc = {"endpoint_id": "orders",
               "operations": {"read": {"request": {"path": path}}}}
        findings = validator._endpoint_locator_findings(doc)
        assert findings and findings[0]["severity"] == "error", path
        assert findings[0].get("rule") == "RULE-ENDP-046"
        assert "must equal" not in findings[0]["message"]      # no fabricated id
        assert "cannot derive" in findings[0]["message"]


def test_coverage_non_dict_endpoint_file_no_crash(tmp_path, connector_base, validator):
    # A JSON-array endpoint file is a recorded model error, NOT a generic
    # "validator bug" crash from the coverage walk calling .get() on a list.
    (tmp_path / "endpoints").mkdir(parents=True)
    (tmp_path / "connector.json").write_text(json.dumps(connector_base))
    (tmp_path / TYPE_MAP_FILENAME).write_text(
        json.dumps(_type_map_doc(read=[{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}])))
    (tmp_path / "endpoints" / "widgets.json").write_text("[]")  # array, not object
    errs = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert errs
    assert not any("validator bug" in e["message"] for e in errs)


def test_coverage_flags_endpoint_id_locator_mismatch(tmp_path, connector_base, validator):
    # End-to-end: a model-valid endpoint whose id doesn't encode its versioned path
    # is gated (filename still matches the id; only the locator rule catches it).
    ep = _endpoint("STRING", "Utf8", endpoint_id="widgets", path="/v1/widgets")
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": ep})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any(e.get("rule") == "RULE-ENDP-046" for e in errors)


# --- RULE-ENDP-044: a keyset block must omit `initial`, never spell it null -----

def _keyset_endpoint(initial=..., transport_ref=...):
    keyset = {"param": "after", "order_by_field": "id"}
    if initial is not ...:
        keyset["initial"] = initial
    request = {
        "method": "GET", "path": "/v1/records",
        "query": {"after": {"from_param": "after"}},
    }
    if transport_ref is not ...:
        request["transport_ref"] = transport_ref
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
    findings = validator.validate_document(_keyset_endpoint(initial=None))
    hits = [f for f in findings if f.get("rule") == "RULE-ENDP-044"]
    assert hits, findings
    assert hits[0]["kind"] == "fail"
    assert hits[0]["severity"] == "warning"
    assert hits[0]["path"] == "/operations/read/pagination/keyset/initial"


@pytest.mark.parametrize("initial", [..., "abc123", 0])
def test_keyset_non_null_initial_is_clean(initial, validator):
    findings = validator.validate_document(_keyset_endpoint(initial=initial))
    assert not any(f.get("rule") == "RULE-ENDP-044" for f in findings), findings


def test_coverage_flags_keyset_explicit_null_initial(tmp_path, connector_base, validator):
    # End-to-end through the connector-package route (check_coverage's sibling-
    # endpoint loop), not just the standalone single-document route: the two
    # walk different code paths and must reach the same verdict.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"v1__records.json": _keyset_endpoint(initial=None)})
    findings = validator.validate_document(connector_base, doc_path=tmp_path / "connector.json")
    assert any(f.get("rule") == "RULE-ENDP-044" for f in findings), findings


def test_check_coverage_and_standalone_route_agree_on_shared_per_endpoint_checks(
        tmp_path, connector_base, validator):
    # check_coverage's sibling-endpoint loop and the standalone
    # _validate_api_endpoint route each reach `_api_endpoint_document_findings`
    # from a different caller. Everything that function decides must come back
    # identical, so this compares the two sets rather than asserting a named
    # pair is in both: a check wired into one caller and not the other shows up
    # as a set difference whatever rule it grades.
    ep_doc = _keyset_endpoint(initial=None, transport_ref="bogus")
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"v1__records.json": ep_doc})

    def _graded(findings):
        return {(f.get("rule"), f.get("message_id"), f.get("kind")) for f in findings}

    coverage_findings = validator.validate_document(connector_base, doc_path=tmp_path / "connector.json")
    standalone_findings = validator.validate_document(
        ep_doc, doc_path=tmp_path / "endpoints" / "v1__records.json")

    # The tree is a clean connector whose single endpoint carries the defects,
    # so neither route has anything of its own to add: equality both ways, not
    # a subset, so a check wired into either caller alone shows up here.
    assert _graded(coverage_findings), coverage_findings
    assert _graded(coverage_findings) == _graded(standalone_findings), (
        coverage_findings, standalone_findings)


@pytest.mark.parametrize("rel, graded", [
    ("draft.json", False),
    ("staging/draft.json", False),
    ("endpoints/draft.json", True),
    ("definition/endpoints/draft.json", True),
])
def test_api_endpoint_filename_is_graded_only_where_the_engine_resolves_it(
        rel, graded, tmp_path, validator):
    # RULE-PKG-031 is about the name the engine will look the endpoint up by.
    # A file not yet in an `endpoints/` directory has no such name, so the gate
    # has nothing to grade — and reporting it anyway fires on every pass of an
    # authoring fix loop with no way to clear it. An api endpoint has one home,
    # unlike a database endpoint's second hash-addressed shape, so the
    # `endpoints/` parent is the whole test.
    ep = _keyset_endpoint(initial="abc")
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    findings = validator.validate_document(ep, doc_path=path)
    hit = any(f.get("rule") == "RULE-PKG-031" for f in findings)
    assert hit is graded, findings


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
    (_connection_doc, CONNECTION_SCHEMA_URL),
    (_stream_doc, STREAM_SCHEMA_URL),
    (_pipeline_doc, PIPELINE_SCHEMA_URL),
    (_connector_doc, CONNECTOR_SCHEMA_URL),
]


@pytest.mark.parametrize("make_doc,schema_url", _SHRD_003_FAMILIES)
def test_missing_schema_url_warns(make_doc, schema_url, validator):
    findings = validator.validate_document(make_doc(schema_url=...))
    hits = [f for f in findings if f.get("rule") == "RULE-SHRD-003"]
    assert hits, findings
    assert hits[0]["kind"] == "fail"
    assert hits[0]["severity"] == "warning"
    assert hits[0]["path"] == "/$schema"


@pytest.mark.parametrize("make_doc,schema_url", _SHRD_003_FAMILIES)
def test_present_schema_url_is_clean(make_doc, schema_url, validator):
    findings = validator.validate_document(make_doc(schema_url=schema_url))
    assert not any(f.get("rule") == "RULE-SHRD-003" for f in findings), findings


@pytest.mark.parametrize("make_doc,schema_url", _SHRD_003_FAMILIES)
def test_null_schema_url_reports_the_same_as_omission(make_doc, schema_url, validator):
    # `$schema: null` is what every one of these models types as optional, so
    # nothing structural rejects it — and a document spelling the absence as a
    # null names no contract exactly as one leaving the key out does.
    findings = validator.validate_document(make_doc(schema_url=None))
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
    assert not _errors(validator.validate_document(_db_endpoint(good_id)))
    legacy = _db_endpoint("public__orders")
    errs = _errors(validator.validate_document(legacy))
    assert any(e.get("rule") == "RULE-DBEP-011" and "public__orders" in e["message"]
               for e in errs)
    # Catalog + schemaless variants are gated the same way (derived id passes).
    assert not _errors(validator.validate_document(
        _db_endpoint(derive_db_endpoint_id("wh", "public", "orders"), schema="public", catalog="wh")))
    assert not _errors(validator.validate_document(
        _db_endpoint(derive_db_endpoint_id(None, None, "orders"), schema=None)))


# --- coverage matrix (check_coverage isolates file-behavior from model validity) ---

def _min_connector(kind: str):
    return {"kind": kind, "transports": {}}


def _read_rules():
    return [{"match": "exact", "native_type": "BIGINT", "arrow_type": "Int64"}]


def _write_rules():
    return [{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}]


_SECTION_RULES = {"read": _read_rules, "write": _write_rules}


def _plant_map(root: Path, sections) -> None:
    """`type-map.json` carrying a valid rule list for each direction in
    `sections`, or no map at all when `sections` is None."""
    if sections is not None:
        (root / TYPE_MAP_FILENAME).write_text(json.dumps(
            _type_map_doc(**{d: _SECTION_RULES[d]() for d in sections})))


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
def test_a_kind_is_held_to_the_sections_its_map_carries(tmp_path, validator, kind, sections, expected):
    # A storage kind moves bytes and resolves no type, so it requires no
    # section; an api connector has no write direction; a database-family one
    # renders DDL, so it needs a `read` and a `write` section.
    _plant_map(tmp_path, sections)
    (tmp_path / "endpoints").mkdir()
    findings = validator.check_coverage(_min_connector(kind), tmp_path / "connector.json")
    assert {f["message_id"] for f in findings if f.get("rule") == "RULE-PKG-030"} == expected, findings


@pytest.mark.parametrize("kind,expected", [("api", set()), ("database", {"write-map-missing"})])
def test_a_null_section_is_an_absent_one(tmp_path, validator, kind, expected):
    # The model reads a `null` section as absent, so the package check does
    # too: a database map's `null` write section is no write rules, and an api
    # map's is no write section to refuse.
    (tmp_path / TYPE_MAP_FILENAME).write_text(json.dumps(
        {"$schema": _TM_SCHEMA, "read": _read_rules(), "write": None}))
    (tmp_path / "endpoints").mkdir()
    findings = validator.check_coverage(_min_connector(kind), tmp_path / "connector.json")
    assert {f["message_id"] for f in findings if f.get("rule") == "RULE-PKG-030"} == expected, findings


@pytest.mark.parametrize("section", ["read", "write"])
def test_a_malformed_section_is_not_a_missing_one(tmp_path, validator, section):
    # A section present in the wrong shape is the model's finding; reporting it
    # missing as well would send the author to add a section they already have.
    doc = {"$schema": _TM_SCHEMA,
           **{d: {} if d == section else _SECTION_RULES[d]() for d in ("read", "write")}}
    (tmp_path / TYPE_MAP_FILENAME).write_text(json.dumps(doc))
    (tmp_path / "endpoints").mkdir()
    findings = validator.check_coverage(_min_connector("database"), tmp_path / "connector.json")
    assert f"{TYPE_MAP_FILENAME}#/{section}" in {
        f.get("path") for f in _errors(findings)}, findings
    assert not {f["message_id"] for f in findings if f.get("rule") == "RULE-PKG-030"}, findings


def test_a_refused_write_section_is_not_held_to_the_write_vocabulary(tmp_path, validator):
    # An api connector has no write direction, so its write section is refused
    # whole; asking in the same pass for write rules covering the Arrow
    # vocabulary would tell the author to extend the section and delete it.
    _plant_map(tmp_path, ("read", "write"))
    (tmp_path / "endpoints").mkdir()
    findings = validator.check_coverage(_min_connector("api"), tmp_path / "connector.json")
    rules = {f.get("rule") for f in findings}
    assert "RULE-PKG-030" in rules and "RULE-TMAP-017" not in rules, findings


@pytest.mark.parametrize("sections,reason", [
    (None, f"no {TYPE_MAP_FILENAME} was read"),
    (("write",), f"{TYPE_MAP_FILENAME} carries no 'read' section"),
])
def test_a_missing_section_says_whether_the_map_or_the_section_is_absent(
        tmp_path, validator, sections, reason):
    # The two causes ask for different edits — ship the document, or add a
    # section to the one that ships — so the message names which one it is.
    _plant_map(tmp_path, sections)
    findings = validator.check_coverage(_min_connector("database"), tmp_path / "connector.json")
    [missing] = [f for f in findings if f["message_id"] == "read-map-missing"]
    assert reason in missing["message"], missing


@pytest.mark.parametrize("payload,expected_id", [
    ({"$schema": _TM_SCHEMA, "read": "not-a-list"}, "list_type"),
    (_type_map_doc(read=_read_rules() * 2), "duplicate-type-map-rule"),
])
def test_a_map_finding_names_the_file_it_was_read_from(tmp_path, validator, payload, expected_id):
    # A document finding locates itself with a pointer into the document it
    # graded. Forwarded beside a connector it reads as a defect in
    # `connector.json`, which carries no such node, so the author is told what
    # is wrong and cannot tell which file to open.
    (tmp_path / TYPE_MAP_FILENAME).write_text(json.dumps(payload))
    (tmp_path / "endpoints").mkdir()
    # Every finding, not just the error-severity ones: a warning about a map is
    # as unlocatable as a failure about it.
    reported = validator.check_coverage(_min_connector("api"), tmp_path / "connector.json")
    named = [e for e in reported if e.get("message_id") == expected_id]
    assert named, [e.get("message_id") for e in reported]
    assert named[0]["path"].startswith(f"{TYPE_MAP_FILENAME}#/"), named[0]


def _plant_stray(parent: Path, name: str, shape: str) -> None:
    """Put a type-map-shaped name at `parent` as one of the things a name can be.
    The regular file is a well-formed split read map carrying a defect, so a
    loader that read it would report that defect."""
    stray = parent / name
    if shape == "regular file":
        stray.write_text(json.dumps({
            "$schema": _TM_SCHEMA.replace("/type-map/", "/type-map-read/"),
            "direction": "read",
            "rules": [{"match": "exact", "native_type": "X", "arrow_type": "NotAnArrowFamily"}]}))
    elif shape == "directory":
        stray.mkdir()
    elif shape == "dangling symlink":
        stray.symlink_to(parent / "nothing-here.json")
    else:  # pragma: no cover - a shape the parametrization does not carry
        raise AssertionError(shape)


@pytest.mark.parametrize("shape", ["regular file", "directory", "dangling symlink"])
@pytest.mark.parametrize("kind,sections", [
    ("api", ("read",)), ("database", ("read", "write")), ("file", None)])
def test_a_stray_type_map_name_is_refused_and_not_read(tmp_path, validator, kind, sections, shape):
    # One document carries the whole vocabulary, so a second file under a
    # type-map name holds rules nothing grades while reading as part of the
    # package. It is refused for carrying the name, whatever sits under it —
    # reading it as "a regular file with this name" would let an author keep
    # the name by making it something else.
    _plant_map(tmp_path, sections)
    _plant_stray(tmp_path, "type-map-natives.json", shape)
    (tmp_path / "endpoints").mkdir()
    findings = validator.check_coverage(_min_connector(kind), tmp_path / "connector.json")
    stray = [f for f in findings if f["message_id"] == "stray-type-map-document"]
    assert [(f.get("rule"), f["kind"]) for f in stray] == [("RULE-PKG-030", "fail")], findings
    assert "type-map-natives.json" in stray[0]["message"], stray[0]
    assert not any("NotAnArrowFamily" in f["message"] for f in findings), findings


@pytest.mark.parametrize("kind", (*_DATABASE_KINDS, *_STORAGE_KINDS))
def test_coverage_holds_a_connector_write_map_to_the_whole_vocabulary(tmp_path, kind, validator):
    # The package route grades at connector scope: a connector that renders
    # one Arrow family materializes nothing else, and the gap-only allowance
    # belongs to a connection map filling in behind one.
    _plant_map(tmp_path, ("read", "write"))
    findings = validator.check_coverage(_min_connector(kind), tmp_path / "connector.json")
    assert "RULE-TMAP-017" in {f.get("rule") for f in findings}, findings


@pytest.mark.parametrize("kind", _STORAGE_KINDS)
def test_a_storage_map_is_graded_in_every_section_it_carries(tmp_path, kind, validator):
    # A storage kind requires no section, and one it ships anyway is still read
    # by whoever resolves types through it — so a defect in any section is
    # reported, not waved through with the requirement.
    (tmp_path / TYPE_MAP_FILENAME).write_text(json.dumps(_type_map_doc(
        read=_read_rules(),
        write=[{"match": "exact", "arrow_type": "NotAnArrowFamily", "native_type": "TEXT"}])))
    errors = _errors(validator.check_coverage(_min_connector(kind), tmp_path / "connector.json"))
    assert [e["path"] for e in errors] == [
        f"{TYPE_MAP_FILENAME}#/write/0/arrow_type"], errors


@pytest.mark.parametrize("native_type, arrow_type, message_id", [
    ("UNCOVERED", "Utf8", "native-type-unresolved"),
    ("BIGINT", "Utf8", "native-type-arrow-mismatch"),
])
def test_a_coverage_finding_names_the_map_it_was_rendered_from(
        tmp_path, connector_base, native_type, arrow_type, message_id, validator):
    # The message sends an author to a file, so it names the one the rules came
    # from. Every verdict the endpoint walk renders off the read section sends
    # the author to it, not only the one where no rule matched.
    _write_tree(tmp_path, connector_base, _read_rules(),
                {"widgets.json": _endpoint(native_type, arrow_type)})
    errors = _errors(validator.check_coverage(connector_base, tmp_path / "connector.json"))
    named = [e for e in errors if e["message_id"] == message_id]
    assert named, errors
    assert TYPE_MAP_FILENAME in named[0]["message"], named[0]


def test_coverage_grades_a_map_that_parsed_to_no_document(tmp_path, validator):
    # `null` parses to `None`, the value a loader answering `None` for "nothing
    # was read" also returns. Reading the loader's answer as "nothing to grade"
    # would let it through: a storage kind
    # requires no map, so a package whose only map is that file passes with
    # nothing said about it. What says the file was read is the load's
    # `loaded`, not the value it parsed to.
    (tmp_path / TYPE_MAP_FILENAME).write_text("null")
    (tmp_path / "connector.json").write_text("{}")
    errors = _errors(validator.check_coverage(_min_connector("file"), tmp_path / "connector.json"))
    assert errors, "a sibling that is no document was graded as nothing"


def test_an_unparseable_map_is_reported_as_unparseable_and_nothing_else(tmp_path, validator):
    # Nothing was read, so there is no document to grade: a model finding over
    # it would describe a value the file never held.
    (tmp_path / TYPE_MAP_FILENAME).write_text("[ not json")
    (tmp_path / "connector.json").write_text("{}")
    findings = validator.check_coverage(_min_connector("file"), tmp_path / "connector.json")
    assert [(f["message_id"], f.get("rule")) for f in findings] == [
        ("type-map-unparseable", "RULE-PKG-030")], findings


# Text the parser refuses without raising a decode error: nesting deeper than
# it descends, and an integer longer than its digit limit.
_PARSER_REFUSALS = {
    "nested past the parser": "[" * 100_000 + "]" * 100_000,
    "integer past the digit limit": '{"n": 1' + "0" * 5_000 + "}",
}


@pytest.mark.parametrize("text", list(_PARSER_REFUSALS.values()), ids=list(_PARSER_REFUSALS))
def test_a_map_the_parser_refuses_is_reported_as_unparseable(tmp_path, validator, text):
    # Uncaught, the refusal ends the whole coverage check, so the package loses
    # every other finding about it — the sections its kind requires included.
    (tmp_path / TYPE_MAP_FILENAME).write_text(text)
    findings = validator.check_coverage(_min_connector("database"), tmp_path / "connector.json")
    assert [f["message_id"] for f in findings] == [
        "type-map-unparseable", "read-map-missing", "write-map-missing"], findings


@pytest.mark.parametrize("text", list(_PARSER_REFUSALS.values()), ids=list(_PARSER_REFUSALS))
def test_cli_reports_a_document_the_parser_refuses_as_unreadable(
        tmp_path, monkeypatch, capsys, text):
    from analitiq.validator import _core
    document = tmp_path / "connector.json"
    document.write_text(text)
    monkeypatch.setattr("sys.argv", ["validator", "--document", str(document)])
    assert _core.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert [f["message_id"] for f in out["findings"]] == ["unreadable-document"], out


def test_coverage_reports_a_map_that_is_not_a_regular_file(tmp_path, validator):
    # The name locates a directory entry, not a document, so what the loader is
    # handed is whatever carries it. A directory raises on the read and reports
    # an errno that says nothing about the package.
    (tmp_path / TYPE_MAP_FILENAME).mkdir()
    (tmp_path / "connector.json").write_text("{}")
    errors = _errors(validator.check_coverage(
        _min_connector("file"), tmp_path / "connector.json"))
    assert any("not a regular file" in e["message"] for e in errors), errors


def test_coverage_does_not_read_a_map_that_blocks(tmp_path, validator):
    # The payload nothing can time out on its own: `read_text` on a FIFO waits
    # for a writer that never comes, so a check that opens the map file never
    # answers at all.
    os.mkfifo(tmp_path / TYPE_MAP_FILENAME)
    (tmp_path / "connector.json").write_text("{}")
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(10)
    try:
        errors = _errors(validator.check_coverage(
            _min_connector("file"), tmp_path / "connector.json"))
    finally:
        signal.alarm(0)
    assert any("not a regular file" in e["message"] for e in errors), errors


@pytest.mark.parametrize("mode", [0o000, 0o300], ids=["no access", "searchable only"])
@pytest.mark.parametrize("unlisted", ["endpoints/sub", "endpoints"])
def test_coverage_does_not_pass_an_endpoints_directory_it_cannot_list(tmp_path, validator, refuse, unlisted, mode):
    # Listed, `sub`'s document fails as nested. Read as empty instead, the
    # connector would pass on the strength of what the walk never saw.
    _write_tree(tmp_path, _min_connector("api"), [{"match": "exact", "native_type": "STRING",
                                                   "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    (tmp_path / "endpoints/sub").mkdir()
    (tmp_path / "endpoints/sub/nested.json").write_text("{}")
    refuse(tmp_path / unlisted, mode)
    findings = validator.check_coverage(_min_connector("api"), tmp_path / "connector.json")
    unlisted_findings = [f for f in findings if f["message_id"] == "endpoints-dir-unlisted"]
    assert [(f["kind"], f["rule"]) for f in unlisted_findings] == [
        ("notApplicable", "RULE-PKG-031")], findings
    assert str(tmp_path / unlisted) in unlisted_findings[0]["message"]
    from analitiq.validator._core import _passed
    assert not _passed(findings), findings


@pytest.mark.parametrize("mode", [0o000, 0o300], ids=["no access", "searchable only"])
def test_a_type_map_in_a_directory_that_cannot_be_listed_is_unchecked_not_absent(
        tmp_path, validator, refuse, mode):
    (tmp_path / TYPE_MAP_FILENAME).write_text(json.dumps(_type_map_doc(read=[])))
    refuse(tmp_path, mode)
    load = validator.load_type_map(tmp_path, rule="RULE-PKG-030")
    assert [(name, f["kind"], f["rule"], f["message_id"]) for name, f in load.findings] == [
        (".", "notApplicable", "RULE-PKG-030", "type-map-dir-unlisted")], load.findings
    assert (load.loaded, load.unread) == (False, ["."])


_UNLISTED = ("notApplicable", "RULE-PKG-030", "type-map-dir-unlisted")
_MAP_UNREADABLE = ("notApplicable", "RULE-PKG-030", "type-map-unreadable")
_COVERAGE_SKIPPED = ("notApplicable", "RULE-PKG-033", "native-type-coverage-skipped")
_ENDPOINTS_UNLISTED = ("notApplicable", "RULE-PKG-031", "endpoints-dir-unlisted")
# What the empty write section is graded to wherever the map is read.
_WRITE_MAP_GRADED = [("fail", None, "too_short"), ("fail", "RULE-TMAP-017", "write-map-missing-family")]


@pytest.mark.parametrize("kind,mode,expected", [
    ("api", 0o755, []),
    ("api", 0o300, [_UNLISTED, _COVERAGE_SKIPPED]),
    ("api", 0o400, [_MAP_UNREADABLE, _COVERAGE_SKIPPED, _ENDPOINTS_UNLISTED]),
    ("api", 0o000, [_UNLISTED, _COVERAGE_SKIPPED, _ENDPOINTS_UNLISTED]),
    ("database", 0o755, _WRITE_MAP_GRADED),
    ("database", 0o300, [_UNLISTED]),
    ("database", 0o400, [_MAP_UNREADABLE]),
    ("database", 0o000, [_UNLISTED]),
    ("file", 0o755, _WRITE_MAP_GRADED),
    ("file", 0o300, [_UNLISTED]),
    ("file", 0o400, [_MAP_UNREADABLE]),
    ("file", 0o000, [_UNLISTED]),
], ids=lambda v: f"{v:o}" if isinstance(v, int) else None)
def test_a_refused_lookup_in_the_package_withholds_only_what_depends_on_it(
        tmp_path, validator, refuse, kind, mode, expected):
    # 300 refuses the listing only, 400 every lookup by name, 000 both. What a
    # refusal hides is unknown, not absent; every check not reading it still runs.
    read = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
    _write_tree(tmp_path, _min_connector(kind), read,
                {"widgets.json": _endpoint("STRING", "Utf8")} if kind == "api" else {})
    if kind != "api":
        (tmp_path / TYPE_MAP_FILENAME).write_text(json.dumps(_type_map_doc(read=read, write=[])))
    refuse(tmp_path, mode)
    findings = validator.check_coverage(_min_connector(kind), tmp_path / "connector.json")
    assert [(f["kind"], f.get("rule"), f["message_id"]) for f in findings] == expected, findings


# How an author lets the kernel answer follows from what it refused. A lookup
# needs every directory on the path searchable; a listing needs the directory
# readable too; a read needs the file readable; a walk needs every directory
# below readable and searchable.
_LOOKUP = "Make every directory on the path to it searchable, then re-run."
_LISTING = "Make it readable, and every directory on the path to it searchable, then re-run."
_READ = "Make it readable, then re-run."
_WALK = ("Make it and every directory below it readable and searchable, and every "
         "directory on the path to it searchable, then re-run.")


@pytest.mark.parametrize("shut,mode,message_id,remedy", [
    (".", 0o300, "type-map-dir-unlisted", _LISTING),
    (".", 0o400, "type-map-unreadable", _LOOKUP),
    (TYPE_MAP_FILENAME, 0o000, "type-map-unreadable", _READ),
    (".", 0o400, "endpoints-dir-unlisted", _LOOKUP),
    ("endpoints/sub", 0o000, "endpoints-dir-unlisted", _WALK),
    ("endpoints", 0o400, "endpoint-file-unreadable", _LOOKUP),
    ("endpoints/widgets.json", 0o000, "endpoint-file-unreadable", _READ),
], ids=["package unlisted", "map lookup", "map read", "endpoints lookup", "endpoints walk",
        "endpoint lookup", "endpoint read"])
def test_each_refusal_names_the_remedy_for_what_was_refused(
        tmp_path, validator, refuse, shut, mode, message_id, remedy):
    _write_tree(tmp_path, _min_connector("api"), [{"match": "exact", "native_type": "STRING",
                                                   "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    if not (tmp_path / shut).exists():
        (tmp_path / shut).mkdir()
    refuse(tmp_path / shut, mode)
    findings = validator.check_coverage(_min_connector("api"), tmp_path / "connector.json")
    [refused] = [f for f in findings if f["message_id"] == message_id]
    assert refused["message"].endswith(remedy), refused


def test_a_failure_the_author_cannot_chmod_away_names_no_remedy(tmp_path, validator, monkeypatch):
    # Only a refused permission is the author's to grant; a failing disk is not.
    _write_tree(tmp_path, _min_connector("api"), [{"match": "exact", "native_type": "STRING",
                                                   "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    def fail(self, key):
        raise OSError(errno.EIO, "Input/output error", str(key))
    monkeypatch.setattr(DiskTree, "read_text", fail)
    findings = validator.check_coverage(_min_connector("api"), tmp_path / "connector.json")
    failed = {f["message_id"]: f["message"] for f in findings
              if f["message_id"] in ("type-map-unreadable", "endpoint-file-unreadable")}
    assert set(failed) == {"type-map-unreadable", "endpoint-file-unreadable"}, findings
    for message in failed.values():
        assert re.search(r"the read failed \(\[Errno 5\] Input/output error: '[^']+'\)\.$",
                         message), message


def test_a_map_the_kernel_will_not_read_is_not_reported_missing(tmp_path, validator, refuse):
    # What it carries is unknown, so no section can be said to be missing.
    _write_tree(tmp_path, _min_connector("database"), [], {})
    refuse(tmp_path / TYPE_MAP_FILENAME, 0o000)
    findings = validator.check_coverage(_min_connector("database"), tmp_path / "connector.json")
    assert [(f["kind"], f.get("rule"), f["message_id"]) for f in findings] == [
        _MAP_UNREADABLE], findings


def test_an_endpoint_the_kernel_will_not_open_is_reported_unread(tmp_path, validator, refuse):
    # Listable but not searchable: the walk finds each name, and every lookup of it is refused.
    _write_tree(tmp_path, _min_connector("api"), [{"match": "exact", "native_type": "STRING",
                                                   "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    refuse(tmp_path / "endpoints", 0o400)
    findings = validator.check_coverage(_min_connector("api"), tmp_path / "connector.json")
    assert [(f["kind"], f["message_id"]) for f in findings] == [
        ("fail", "endpoint-file-unreadable")], findings
    assert "widgets.json" in findings[0]["message"]


@pytest.mark.parametrize("shape", ["regular file", "directory", "dangling symlink"])
def test_a_stray_name_is_found_where_the_package_lists_but_cannot_be_searched(
        tmp_path, validator, refuse, shape):
    _plant_stray(tmp_path, "type-map-read.json", shape)
    refuse(tmp_path, 0o400)
    load = validator.load_type_map(tmp_path, rule="RULE-PKG-030")
    assert [(name, f["message_id"]) for name, f in load.findings] == [
        ("type-map-read.json", "stray-type-map-document")], load.findings


def test_database_endpoint_filename_not_checked_for_snapshot(validator, tmp_path):
    # The hash-addressed materialized snapshot lives at
    # `.../endpoints/{endpoint_id}/schemas/{schema_hash}.json` — its basename is a
    # content hash by design, so the filename↔endpoint_id gate must NOT fire there.
    eid = derive_db_endpoint_id(None, "public", "orders")
    db = _db_endpoint(eid)
    snap_dir = tmp_path / "endpoints" / eid / "schemas"
    snap_dir.mkdir(parents=True)
    p = snap_dir / "sha256-abc123.json"  # hash basename, not {endpoint_id}.json
    p.write_text(json.dumps(db))
    errors = _errors(validator.validate_document(db, doc_path=p))
    assert not any(e.get("rule") == "RULE-PKG-031" for e in errors)


def test_database_endpoint_filename_checked_in_bundle_layout(validator, tmp_path):
    # The authored connection-scoped file the engine locates by stem lives at
    # `connections/{cid}/definition/endpoints/{endpoint_id}.json`. A correct id
    # inside but a mismatched filename stem passes model + locator gates yet fails
    # at runtime (the engine registers it under the wrong stem), so the gate fires.
    eid = derive_db_endpoint_id(None, "public", "orders")
    db = _db_endpoint(eid)
    ep_dir = tmp_path / "connections" / "conn-1" / "definition" / "endpoints"
    ep_dir.mkdir(parents=True)
    wrong = ep_dir / "orders.json"  # stem != endpoint_id
    wrong.write_text(json.dumps(db))
    errors = _errors(validator.validate_document(db, doc_path=wrong))
    assert any(e.get("rule") == "RULE-PKG-031" for e in errors)
    # Correctly named -> no filename error.
    right = ep_dir / f"{eid}.json"
    right.write_text(json.dumps(db))
    assert not any(e.get("rule") == "RULE-PKG-031"
                   for e in _errors(validator.validate_document(db, doc_path=right)))


def test_database_endpoint_filename_not_checked_when_unanchored(validator, tmp_path):
    # A staged single-doc path not yet at its final `definition/endpoints/` home
    # carries no stem contract, so the gate stays silent.
    eid = derive_db_endpoint_id(None, "public", "orders")
    db = _db_endpoint(eid)
    p = tmp_path / "orders.json"  # bare staged file, wrong stem, no endpoints/ parent
    p.write_text(json.dumps(db))
    errors = _errors(validator.validate_document(db, doc_path=p))
    assert not any(e.get("rule") == "RULE-PKG-031" for e in errors)


def test_endpoint_filename_findings_public_helper(validator):
    # The filename gate is exported so a bundle-assembling consumer —
    # which validates filename-less in-memory docs via validate_pipeline_bundle and
    # so cannot reach the gate there — calls ONE shared implementation instead of
    # reimplementing the ~4-line check, keeping the invariant define-once.
    eid = derive_db_endpoint_id(None, "public", "orders")
    db = _db_endpoint(eid)
    # Mismatched stem -> exactly one RULE-PKG-031 error.
    mismatch = _errors(validator.endpoint_filename_findings(db, "orders.json"))
    assert [e.get("rule") for e in mismatch] == ["RULE-PKG-031"]
    # Correct {endpoint_id}.json -> no findings.
    assert validator.endpoint_filename_findings(db, f"{eid}.json") == []
    # Missing/unusable endpoint_id -> notApplicable (can't verify), not a fail.
    no_id = validator.endpoint_filename_findings({"database_object": {"name": "orders"}}, "orders.json")
    assert [(f.get("rule"), f["kind"]) for f in no_id] == [("RULE-PKG-031", "notApplicable")]


def test_is_stem_addressed_endpoint_path_public_helper(validator):
    # Consumers apply the gate on the SAME layout condition the
    # validator uses — true only for the authored `definition/endpoints/{id}.json`
    # the engine resolves by stem, false for the hash-addressed snapshot and any
    # bare/staged path.
    eid = derive_db_endpoint_id(None, "public", "orders")
    bundle = Path("connections/conn-1/definition/endpoints") / f"{eid}.json"
    snapshot = Path("connections/conn-1/endpoints") / eid / "schemas" / "sha256-abc.json"
    assert validator.is_stem_addressed_endpoint_path(bundle) is True
    assert validator.is_stem_addressed_endpoint_path(snapshot) is False
    assert validator.is_stem_addressed_endpoint_path(Path("orders.json")) is False


def test_a_document_with_no_path_is_graded_the_same_way(validator, tmp_path):
    # The route takes a path only because its callers have one; nothing here
    # reads it, so a document handed over without one is graded identically.
    # A map earning a warning is used: a route that skipped a pathless document
    # altogether would also return no errors, and only the findings it does
    # produce separate grading from not grading.
    doc = _type_map_doc(write=_write_rules())
    pathless = validator.validate_document(doc)
    assert any(f.get("rule") == "RULE-TMAP-017" for f in pathless), pathless
    assert pathless == validator.validate_document(doc, doc_path=tmp_path / TYPE_MAP_FILENAME)


@pytest.mark.parametrize("doc", [
    {"$schema": TYPE_MAP_SCHEMA_URL},
    {"read": _read_rules()},
    {"write": _write_rules()},
    {"$schema": TYPE_MAP_SCHEMA_URL, "write": "not-a-list"},
    {"$schema": TYPE_MAP_SCHEMA_URL.replace("/type-map/", "/type-map-read/"), "read": _read_rules()},
], ids=["no-section", "no-schema", "write-only-no-schema", "malformed-section", "stale-schema"])
def test_a_map_missing_its_parts_is_graded_as_a_map(validator, doc):
    # Either the type-map `$schema` or a direction's section identifies the
    # document, so a map missing the other half is told what it lacks rather
    # than that it is no known artifact.
    errors = _errors(validator.validate_document(doc))
    assert errors, doc
    assert "unrecognized-document" not in {f["message_id"] for f in errors}, errors


@pytest.mark.parametrize("doc,stray,own", [
    ({"source": {}, "destinations": [], "write": {}}, "write", "source"),
    ({"connector_id": "postgres", "read": {}}, "read", "connector_id"),
], ids=["stream", "connection"])
def test_a_stray_section_key_does_not_claim_another_kind(validator, doc, stray, own):
    # `read` and `write` are words other kinds nest; one misplaced at the top
    # of a stream or a connection is that document's extra key, not a type map
    # whose every other key is extra — which would hide the document's own
    # defects behind findings about a map nobody wrote.
    extra = {f["path"] for f in validator.validate_document(doc)
             if f.get("message_id") == "extra_forbidden"}
    assert f"/{stray}" in extra and f"/{own}" not in extra, extra


@pytest.mark.parametrize("doc", [
    [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
    {"$schema": TYPE_MAP_SCHEMA_URL.replace("/type-map/", "/type-map-read/"),
     "direction": "read",
     "rules": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]},
    {},
], ids=["bare-array", "split-shape", "empty"])
def test_a_rule_list_in_no_type_map_shape_is_not_recognized(validator, tmp_path, doc):
    # A bare rule array has no `$schema` to claim it, and a split-shape
    # document names a `$schema` no detector registers; each fails loud as an
    # unrecognized document rather than passing under some other detector.
    findings = validator.validate_document(doc, doc_path=tmp_path / TYPE_MAP_FILENAME)
    assert [f["message_id"] for f in _errors(findings)] == ["unrecognized-document"], findings


def test_a_borrowed_diagnostic_does_not_carry_the_document_back_whole(validator):
    # A discriminator error's sentence comes from pydantic, which renders the
    # failing tag into it. This package is the gate over documents it did not
    # author, and the sentence reaches a CI log and an agent's context, so an
    # oversized value is clipped rather than echoed.
    oversized = "Z" * 5000
    errors = _errors(validator.validate_document(
        {"$schema": CONNECTOR_SCHEMA_URL, "connector_id": "x", "display_name": "x",
         "kind": oversized, "transports": {}}))
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
    tag = [e for e in _errors(validator.validate_document(doc))
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
    [error] = _errors(validator.validate_document(doc))
    assert error["message_id"] == "string_pattern_mismatch", error
    assert error["message"].endswith("'"), error["message"][-80:]
    assert "…" not in error["message"], len(error["message"])


def test_coverage_flags_nested_endpoint_file(tmp_path, connector_base, validator):
    # A nested endpoints/**/x.json must be flagged (matches the registry gate,
    # which rejects non-flat endpoint paths) rather than silently ignored.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    nested = tmp_path / "endpoints" / "v1"
    nested.mkdir()
    (nested / "buried.json").write_text(json.dumps(_endpoint("STRING", "Utf8")))
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("nested" in e["message"] for e in errors)


def test_coverage_flags_unparseable_read_map(tmp_path, validator):
    (tmp_path / TYPE_MAP_FILENAME).write_text("{ not json")
    (tmp_path / "connector.json").write_text("{}")
    errors = _errors(validator.check_coverage(_min_connector("database"), tmp_path / "connector.json"))
    assert any("could not be read or parsed" in e["message"] for e in errors)


# --- database-endpoint kind ---

def test_database_endpoint_valid_and_invalid(validator):
    db = {"$schema": "https://schemas.analitiq.ai/database-endpoint/latest.json",
          "endpoint_id": derive_db_endpoint_id(None, "public", "orders"),
          "database_object": {"schema": "public", "name": "orders", "object_type": "table"},
          "columns": [{"name": "id", "native_type": "uuid", "arrow_type": "Utf8"}]}
    assert not _errors(validator.validate_document(db))
    bad = json.loads(json.dumps(db))
    bad["columns"][0]["arrow_type"] = "NotAnArrowType"
    assert _errors(validator.validate_document(bad))


# --- advisory warnings ---

def _warnings(findings):
    return [f for f in findings if f["severity"] == "warning"]


def test_duplicate_type_map_rule_warns(validator):
    rules = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"},
             {"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
    warns = _warnings(validator.validate_document(_type_map_doc(read=rules)))
    assert any("duplicate" in w["message"] for w in warns)


def test_duplicate_exact_read_rule_warns_across_case_and_whitespace(validator):
    # Two exact READ rules differing only by case/whitespace collapse to one
    # matcher at runtime (first wins), so the second is unreachable — the dedup
    # must normalize the same way the reader does and flag it, even when the
    # rules map to DIFFERENT canonicals (a real, if rare, authoring bug).
    rules = [{"match": "exact", "native_type": "character varying", "arrow_type": "Utf8"},
             {"match": "exact", "native_type": "CHARACTER  VARYING", "arrow_type": "LargeUtf8"}]
    warns = _warnings(validator.validate_document(_type_map_doc(read=rules)))
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
def test_regex_lowercase_literal_warning_truth_table(validator, tmp_path, native, verdict, witness):
    import re2

    from analitiq.contracts.type_map import normalize_native_type

    if verdict == "silent":
        assert re2.fullmatch(native, normalize_native_type(witness)), witness
    elif verdict in ("warns", "missed"):
        assert re2.fullmatch(native, witness), witness
        assert not re2.fullmatch(native, normalize_native_type(witness)), witness
    findings = validator.validate_document(
        _type_map_doc(read=[{"match": "regex", "native_type": native, "arrow_type": "Utf8"}]),
        doc_path=tmp_path / TYPE_MAP_FILENAME)
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
def test_regex_read_rule_container_warning(validator, tmp_path, native, arrow_type, warns):
    findings = validator.validate_document(
        _type_map_doc(read=[{"match": "regex", "native_type": native, "arrow_type": arrow_type}]),
        doc_path=tmp_path / TYPE_MAP_FILENAME)
    assert not _errors(findings), findings
    collapsed = [w for w in _warnings(findings)
                 if w.get("rule") == "RULE-TMAP-002" and w.get("message_id") == "read-regex-container-collapsed"]
    assert bool(collapsed) is warns, findings
    assert [w["path"] for w in collapsed] == ["/read/0"] * len(collapsed), collapsed


def test_regex_warnings_stay_cheap_across_a_whole_map(validator, tmp_path):
    rules = [{"match": "regex",
              "native_type": rf"^T{i}x?\((?<n>[1-9]\d*)\)[a-z]*\p{{Lu}}?$",
              "arrow_type": "Utf8"} for i in range(50)]
    started = time.perf_counter()
    findings = validator.validate_document(
        _type_map_doc(read=rules), doc_path=tmp_path / TYPE_MAP_FILENAME)
    elapsed = time.perf_counter() - started
    assert sum(w.get("rule") == "RULE-TMAP-014" for w in _warnings(findings)) == 50, findings
    assert elapsed < 1.0, elapsed


def test_write_vocabulary_gap_warns(validator, tmp_path):
    # A write map missing whole canonical families → advisory warning.
    p = tmp_path / TYPE_MAP_FILENAME
    findings = validator.validate_document(
        _type_map_doc(write=[{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}]),
        doc_path=p)
    assert any(w.get("rule") == "RULE-TMAP-017" for w in _warnings(findings))


def test_write_vocabulary_probes_bare_container_markers(validator, tmp_path):
    # The engine probes the write map with a destination column's `arrow_type`
    # verbatim, and API-sourced documents carry the bare `Object`/`List` shape
    # markers — a map without rules for them hard-errors the stream at
    # configuration. The coverage warning must name both.
    p = tmp_path / TYPE_MAP_FILENAME
    findings = validator.validate_document(
        _type_map_doc(write=[{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}]),
        doc_path=p)
    # StopIteration here is the failure signal working, not a case to guard:
    # no coverage warning at all means the probe stopped running.
    gap = next(  # skipcq: PTC-W0063
        w for w in _warnings(findings) if w.get("rule") == "RULE-TMAP-017"
    )
    assert "'Object'" in gap["message"] and "'List'" in gap["message"]

    covered = [{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"},
               {"match": "exact", "arrow_type": "Object", "native_type": "JSONB"},
               {"match": "exact", "arrow_type": "List", "native_type": "JSONB"}]
    findings = validator.validate_document(_type_map_doc(write=covered), doc_path=p)
    # Covering the two markers must narrow the warning, not silence it — the map
    # still lacks rules for other probes. So StopIteration here is the failure
    # signal working: it means the warning vanished entirely, which would make
    # the assertion below pass for the wrong reason.
    gap = next(  # skipcq: PTC-W0063
        w for w in _warnings(findings) if w.get("rule") == "RULE-TMAP-017"
    )
    assert "'Object'" not in gap["message"] and "'List'" not in gap["message"]


def test_write_vocabulary_fully_covered_map_warns_nothing(validator, tmp_path):
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
            ("UInt16", "INTEGER"), ("UInt32", "BIGINT"), ("UInt64", "BIGINT"),
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
    findings = validator.validate_document(
        _type_map_doc(write=full_map), doc_path=tmp_path / TYPE_MAP_FILENAME)
    coverage = [f for f in findings if f.get("rule") == "RULE-TMAP-017"]
    assert not coverage, coverage


# Each broken state of the read section, with what still reports the map itself.
_BROKEN_READ_MAPS = (
    ("missing", None, "no type-map.json was read"),
    ("no-read-section", json.dumps(_type_map_doc(write=_write_rules())),
     "carries no 'read' section"),
    ("not-a-list", json.dumps({"$schema": _TM_SCHEMA, "read": {}}),
     "Input should be a valid list"),
    ("not-an-object",
     json.dumps([{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]),
     "Input should be a valid dictionary"),
    ("unparseable", "{ not json", "could not be read or parsed"),
)

# What the defective endpoint tree below provokes: a rule id, and a fragment
# of the verdict that check writes. The fragment is what makes the assertion
# grade the check instead of its id — a check that crashes is reported under its
# own id too, and an id-only assertion would read that as the check having run.
_ENDPOINT_DEFECTS = {
    "RULE-PKG-031": "must be named 'widgets.json'",
    "RULE-ENDP-046": "must equal 'v1__widgets'",
    "RULE-ENDP-047": "is not declared in the sibling connector.json",
    "RULE-ENDP-048": "is not a valid JSON Schema Draft 2020-12",
    "RULE-ENDP-063": "which the node declaring it rejects",
}

# The verdicts the native→Arrow rendering writes. A map that did not load feeds
# no rendering, so a finding carrying any of these fragments contradicts the
# warning that says coverage went unrendered.
_RENDERED_COVERAGE = ("has no matching rule in", "resolves to")


def _write_defective_endpoints(root: Path, connector: dict, read_map_text: str | None):
    """A connector tree carrying every defect `_ENDPOINT_DEFECTS` names, beside a
    map in one of the broken states (or none at all)."""
    (root / "endpoints").mkdir(parents=True)
    (root / "connector.json").write_text(json.dumps(connector))
    if read_map_text is not None:
        (root / TYPE_MAP_FILENAME).write_text(read_map_text)
    # filename ≠ endpoint_id; endpoint_id ≠ the handle its path derives;
    # transport_ref names a transport the connector does not declare; a recorded
    # sample contradicts the node declaring it.
    ep = _endpoint("STRING", "Utf8", endpoint_id="widgets", path="/v1/widgets")
    ep["operations"]["read"]["request"]["transport_ref"] = "undeclared"
    ep["operations"]["read"]["response"]["schema"]["items"]["properties"]["a"]["examples"] = [123]
    (root / "endpoints" / "misnamed.json").write_text(json.dumps(ep))
    # Sample grading is skipped on a schema that is unreadable as Draft 2020-12,
    # so the meta-schema defect rides its own endpoint.
    meta = _endpoint("STRING", "Utf8", endpoint_id="meta", path="/meta")
    meta["operations"]["read"]["response"]["schema"]["minItems"] = "notanumber"
    (root / "endpoints" / "meta.json").write_text(json.dumps(meta))


def _defects_reported(findings) -> set[str]:
    return {vid for vid, fragment in _ENDPOINT_DEFECTS.items()
            if any(f.get("rule") == vid and fragment in f["message"] for f in findings)}


def _database_tree(root: Path, *, type_map: str | None, endpoints: bool) -> Path:
    """A database-family connector tree, with the type map's text and an
    `endpoints/` directory the kind has no business shipping each optional.
    Returns the connector.json path."""
    root.mkdir(parents=True)
    (root / "connector.json").write_text("{}")
    if type_map is not None:
        (root / TYPE_MAP_FILENAME).write_text(type_map)
    if endpoints:
        (root / "endpoints").mkdir()
        (root / "endpoints" / "misnamed.json").write_text(
            json.dumps(_endpoint("STRING", "Utf8", endpoint_id="widgets", path="/v1/widgets")))
    return root / "connector.json"


def _assert_endpoint_checks_survive(root, connector, validator, read_map_text, reported):
    _write_defective_endpoints(root, connector, read_map_text)
    errors = _errors(validator.validate_document(connector, doc_path=root / "connector.json"))
    assert any(reported in e["message"] for e in errors), errors
    assert _defects_reported(errors) == set(_ENDPOINT_DEFECTS), (
        sorted(_defects_reported(errors)), [e["message"] for e in errors])


@pytest.mark.parametrize("state,text,reported", _BROKEN_READ_MAPS, ids=[s for s, _, _ in _BROKEN_READ_MAPS])
def test_endpoint_checks_run_when_read_map_is_broken(tmp_path, connector_base, validator, state, text, reported):
    _assert_endpoint_checks_survive(tmp_path, connector_base, validator, text, reported)


def test_endpoint_checks_run_when_read_map_text_is_refused_outside_jsondecodeerror(
        tmp_path, connector_base, validator, text_refused_outside_jsondecodeerror):
    # Reported as unparseable like any other bad text, never as a crash of the
    # coverage check, which would replace every endpoint verdict with one finding.
    _assert_endpoint_checks_survive(tmp_path, connector_base, validator,
                                    text_refused_outside_jsondecodeerror, "could not be read or parsed")


@pytest.mark.parametrize("text", [t for _, t, _ in _BROKEN_READ_MAPS], ids=[s for s, _, _ in _BROKEN_READ_MAPS])
def test_unrendered_coverage_is_reported_not_silent(tmp_path, connector_base, validator, text):
    # The endpoint documents come back graded, so a reader must be told that the
    # one check the map feeds did not run — silence there reads as coverage passing.
    _write_defective_endpoints(tmp_path, connector_base, text)
    findings = validator.validate_document(connector_base, doc_path=tmp_path / "connector.json")
    assert any(f.get("rule") == "RULE-PKG-033" and f["kind"] == "notApplicable"
               and "not rendered" in f["message"] for f in findings), findings
    # ... and the warning is the whole of what coverage says here: a rendering
    # that never ran cannot also return per-endpoint verdicts.
    assert not [f for f in findings
                if any(fragment in f["message"] for fragment in _RENDERED_COVERAGE)], findings


def test_validating_a_connector_with_no_path_fails_closed(validator):
    """The coverage verdict, isolated. A connector validated with no filesystem
    path cannot be asked whether PKG-030/032/033/035 hold — they are each about
    a sibling file located by path — and those are all `error`-tier, so a check
    that could not even attempt them is not one that found them satisfied:
    `passed` must be `False`. Reporting the skipped question as a `warning`
    instead would leave `passed` true, which is the failure this pins. A
    model-valid connector is used so this is the ONLY finding in play, unlike
    `test_endpoint_checks_run_when_read_map_is_broken`'s fixtures, which also
    carry unrelated error findings and so cannot isolate this one.
    `rules/SCHEMA.md`'s Findings section owns what each finding costs the pass.
    """
    from analitiq.validator._core import _passed

    doc = json.loads((CORPUS / "valid_connector_sync_driver.json").read_text())
    findings = validator.validate_document(doc)  # no doc_path
    assert [f["message_id"] for f in findings] == ["coverage-check-skipped-no-path"]
    assert not _passed(findings), findings


def test_database_missing_both_sections_reports_both(tmp_path, validator):
    # The read section's absence must not hide the write section's: the same connector
    # otherwise answers differently depending on how its map is broken. The
    # branch stays terminal, so an `endpoints/` directory beside it is neither
    # enumerated nor demanded.
    doc = _min_connector("database")
    with_dir = validator.check_coverage(doc, _database_tree(
        tmp_path / "with", type_map=None, endpoints=True))
    without_dir = validator.check_coverage(doc, _database_tree(
        tmp_path / "without", type_map=None, endpoints=False))
    assert with_dir == without_dir, (with_dir, without_dir)
    assert {"read-map-missing", "write-map-missing"} <= {
        e["message_id"] for e in _errors(with_dir)}, with_dir
    assert not [f for f in with_dir if "endpoints" in f["message"]], with_dir


_COVERAGE_RULE_IDS = {"RULE-PKG-030", "RULE-PKG-032", "RULE-PKG-033", "RULE-PKG-035"}


def test_clean_tree_emits_no_coverage_finding(tmp_path, connector_base, validator):
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    findings = validator.validate_document(connector_base, doc_path=tmp_path / "connector.json")
    assert [f for f in findings if f.get("rule") in _COVERAGE_RULE_IDS] == [], findings


def test_rendered_coverage_reports_only_the_uncovered_native(tmp_path, connector_base, validator):
    # A readable map renders, so the uncovered native is the only thing coverage
    # has to say — no warning about a rendering that did happen.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": _endpoint("BIGINT", "Int64")})
    findings = validator.validate_document(connector_base, doc_path=tmp_path / "connector.json")
    coverage = [f for f in findings if f.get("rule") == "RULE-PKG-033"]
    assert len(coverage) == 1 and coverage[0]["severity"] == "error", coverage
    assert "no matching rule" in coverage[0]["message"], coverage


@pytest.mark.parametrize("kind", _DATABASE_KINDS)
def test_database_family_never_enumerates_endpoints(tmp_path, kind, validator):
    # A database connector's release ships no endpoint documents, so the walk
    # enumerates `endpoints/` for api connectors only — a broken read section does
    # not change that. An `endpoints/` directory beside the connector therefore moves
    # nothing in the verdict, and a rendering the kind never asks for is not
    # warned about either.
    doc = _min_connector(kind)
    with_dir = validator.check_coverage(doc, _database_tree(
        tmp_path / "with", type_map=json.dumps({"$schema": _TM_SCHEMA, "read": {}, "write": _write_rules()}),
        endpoints=True))
    without_dir = validator.check_coverage(doc, _database_tree(
        tmp_path / "without", type_map=json.dumps({"$schema": _TM_SCHEMA, "read": {}, "write": _write_rules()}),
        endpoints=False))
    assert with_dir == without_dir, (with_dir, without_dir)
    assert not [f for f in with_dir if "endpoints" in f["message"]], with_dir
    assert not [f for f in with_dir if "not rendered" in f["message"]], with_dir


# --- CLI / exit-code contract (the integration surface consumers depend on) ---

def test_cli_valid_doc_exit0(validator_cli):
    # Name the file after its endpoint_id so the filename↔id check is satisfied.
    doc = json.loads((CORPUS / "valid_read.json").read_text())
    r = validator_cli.on_document(doc, filename=f"{doc['endpoint_id']}.json")
    assert r.returncode == 0, r.stdout
    out = json.loads(r.stdout)
    assert out["passed"] is True and isinstance(out["findings"], list)


def test_cli_invalid_doc_exit1(validator_cli):
    r = validator_cli.on_document(json.loads((CORPUS / "invalid_write_from_input.json").read_text()))
    assert r.returncode == 1
    assert json.loads(r.stdout)["passed"] is False


def test_cli_unreadable_document_exit1(tmp_path, validator_cli):
    # A directory path: read raises IsADirectoryError → must still emit JSON + exit 1.
    r = validator_cli.run("--document", str(tmp_path))
    assert r.returncode == 1
    assert json.loads(r.stdout)["passed"] is False


def test_cli_text_refused_outside_jsondecodeerror_is_unreadable(
        tmp_path, validator_cli, text_refused_outside_jsondecodeerror):
    path = tmp_path / "doc.json"
    path.write_text(text_refused_outside_jsondecodeerror)
    r = validator_cli.run("--document", str(path))
    assert r.returncode == 1, r.stderr
    assert [f["message_id"] for f in json.loads(r.stdout)["findings"]] == ["unreadable-document"]


def test_cli_missing_arg_exit2(validator_cli):
    r = validator_cli.run()
    assert r.returncode == 2


# --- One document, one verdict, whichever route reaches it --------------------
# A document's findings are a property of the document, not of the call that
# produced them. Two routes reach an api-endpoint — `check_coverage`'s
# sibling-endpoint loop and the standalone single-document route — and two
# reach a connector-shaped dict, with and without its `kind`. Each pair must
# agree.

def test_kindless_connector_still_reports_a_missing_schema_url(validator):
    # `Connector.schema_url` is optional, so RULE-SHRD-003 is the only thing
    # that reports its omission — and a connector-shaped dict with no `kind` is
    # the document most likely to be missing `$schema` too, so the route that
    # claims it must carry the check as well as the model.
    doc = {"connector_id": "x", "transports": {}, "connection_contract": {},
           "default_transport": "m"}
    findings = validator.validate_document(doc)
    assert any(f.get("rule") == "RULE-SHRD-003" for f in findings), findings


def test_endpoint_findings_locate_the_same_node_on_both_routes(tmp_path, connector_base, validator):
    # RULE-ENDP-048 locates the offending schema. Each route reads `path` from
    # the document it validated: the standalone route validated the endpoint,
    # so a bare pointer is the whole address; the coverage route validated the
    # connector, so the pointer follows the endpoint's reference. Resolved, the
    # two name one node, and the message is the same sentence on both.
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["minItems"] = "notanumber"
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
                {"widgets.json": ep})

    [via_connector] = [f for f in validator.validate_document(
        connector_base, doc_path=tmp_path / "connector.json")
        if f.get("rule") == "RULE-ENDP-048"]
    [via_endpoint] = [f for f in validator.validate_document(
        ep, doc_path=tmp_path / "endpoints" / "widgets.json")
        if f.get("rule") == "RULE-ENDP-048"]

    assert via_connector["path"] == "endpoints/widgets.json#/operations/read/response/schema"
    assert via_endpoint["path"] == "/operations/read/response/schema"
    assert via_connector["message"] == via_endpoint["message"]


# ---------------------------------------------------------------------------
# type_map_findings — the published grading of one type-map document.
# ---------------------------------------------------------------------------

def test_type_map_findings_grades_each_section_as_its_direction(validator):
    # `native_type` is read as a render template only under `write`, so the
    # same rule is clean under one key and a defect under the other: the key a
    # rule list sits under is what decides the model it is measured against.
    rule = [{"match": "exact", "native_type": "VARCHAR${", "arrow_type": "Utf8"}]
    assert not _errors(validator.type_map_findings(_type_map_doc(read=rule), scope="connection"))
    errors = _errors(validator.type_map_findings(_type_map_doc(write=rule), scope="connection"))
    assert [(e["path"], e["message_id"]) for e in errors] == [
        ("/write/0", "write-exact-malformed-placeholder")], errors


def test_type_map_findings_points_each_advisory_into_its_section(validator):
    # A rule's matcher is `native_type` under `read` and `arrow_type` under
    # `write`, so each section is deduplicated on its own matcher and every
    # warning points into the section it is about.
    doc = _type_map_doc(read=_read_rules() * 2, write=_write_rules() * 2)
    duplicates = [f["path"] for f in validator.type_map_findings(doc, scope="connection")
                  if f["message_id"] == "duplicate-type-map-rule"]
    assert duplicates == ["/read/1", "/write/1"], duplicates


def test_type_map_findings_reads_no_section_as_the_other_direction(validator):
    # These four are a legitimate many-to-one write mapping; read as matching on
    # `native_type`, they are one native matched four times.
    doc = _type_map_doc(write=[{"match": "exact", "arrow_type": a, "native_type": "BIGINT"}
                               for a in ("Int8", "Int16", "Int32", "Int64")])
    assert validator.type_map_findings(doc, scope="connection") == []


def test_type_map_findings_scope_decides_the_write_vocabulary_alone(validator):
    # the whole reason `scope` exists: a connector write map must render the
    # canonical vocabulary, a connection map is gap-only and would earn
    # RULE-TMAP-017 forever
    gap_only = _type_map_doc(write=_write_rules())
    at_connector = validator.type_map_findings(gap_only, scope="connector")
    at_connection = validator.type_map_findings(gap_only, scope="connection")
    assert [(f.get("rule"), f["path"]) for f in at_connector] == [("RULE-TMAP-017", "/write")], at_connector
    assert at_connection == [], at_connection


def test_type_map_findings_scope_does_not_reach_the_read_direction(validator):
    # nothing about a read section differs by scope; a divergence here would
    # mean `scope` had grown a second meaning
    # The rule earns an advisory, so the equality has content: over a clean
    # document both sides are empty and any scope-keyed filter passes.
    doc = _type_map_doc(read=[{"match": "regex", "native_type": "^vector\\(", "arrow_type": "Utf8"}])
    at_connector = validator.type_map_findings(doc, scope="connector")
    assert at_connector, "the document must earn a finding or this asserts nothing"
    assert at_connector == validator.type_map_findings(doc, scope="connection")


@pytest.mark.parametrize("scope", ["Connection", None])
def test_type_map_findings_rejects_its_own_bad_arguments(validator, scope):
    # a typo'd scope would silently grade the document at the one nobody asked
    # for. It is the caller's mistake, not the document's, so it raises past the
    # crash guard instead of arriving as a finding about the map.
    with pytest.raises(ValueError, match="scope must be"):
        validator.type_map_findings(_type_map_doc(read=_read_rules()), scope=scope)


def test_type_map_findings_reports_its_own_crash_as_unchecked(validator, monkeypatch):
    # a crash leaves the model errors and every advisory rule unevaluated
    # together, so the finding names no rule — and that is exactly what makes it
    # cost a pass rather than read as "nothing was wrong"
    from analitiq.validator import connectors
    monkeypatch.setattr(connectors, "_type_map_rule_warnings",
                        lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")))
    findings = validator.type_map_findings(_type_map_doc(read=_read_rules()))
    assert len(findings) == 1, findings
    assert findings[0].get("rule") is None, findings[0]
    assert findings[0]["message_id"] == "check-crashed", findings[0]
    assert findings[0]["kind"] == "notApplicable", findings[0]
    assert validator.finding_costs_a_pass(findings[0]) is True


# --- the loader, shared with every caller holding a `definition/` directory ---

def test_load_type_map_reads_the_one_name_and_refuses_the_rest(validator, tmp_path):
    # The connector beside the map is not one, and a type-map-shaped name that
    # is not `type-map.json` is refused rather than read.
    doc = _type_map_doc(read=_read_rules())
    for name in (TYPE_MAP_FILENAME, "connector.json", "type-map-extra.json"):
        (tmp_path / name).write_text(json.dumps(doc))
    load = validator.load_type_map(tmp_path, rule=None)
    assert load.loaded and load.document == doc
    assert [(name, f["message_id"], f.get("rule")) for name, f in load.findings] == [
        ("type-map-extra.json", "stray-type-map-document", None)]


def test_load_type_map_reports_nothing_for_a_directory_without_one(validator, tmp_path):
    # Whether an absent map is a defect is the caller's to say: a connector
    # requires one for most kinds, a connection never does.
    load = validator.load_type_map(tmp_path, rule="RULE-PKG-030")
    assert not load.loaded and load.findings == []
    # Nothing was read, so there is no document to hand over: asking for one
    # is the caller's defect, and grading a stand-in would report a file that
    # is not there.
    with pytest.raises(LookupError):
        load.document


def test_load_type_map_attributes_every_finding_to_the_rule_given(validator, tmp_path):
    (tmp_path / TYPE_MAP_FILENAME).write_text("[ not json")
    (tmp_path / "type-map-b.json").mkdir()
    (tmp_path / "type-map-a.json").write_text(json.dumps(_type_map_doc(read=_read_rules())))
    load = validator.load_type_map(tmp_path, rule="RULE-PKG-030")
    assert not load.loaded
    assert [(name, f["message_id"], f.get("rule")) for name, f in load.findings] == [
        (TYPE_MAP_FILENAME, "type-map-unparseable", "RULE-PKG-030"),
        ("type-map-a.json", "stray-type-map-document", "RULE-PKG-030"),
        ("type-map-b.json", "stray-type-map-document", "RULE-PKG-030")]


def test_stray_type_map_paths_orders_what_the_directory_hands_back():
    # Directory order is the filesystem's, and it is not sorted: two runs taking
    # it as given would report the same strays in different orders. A stub
    # stands in for the directory because a real one cannot be made to hand back
    # an unsorted listing on demand.
    from analitiq.validator.connectors import _stray_type_map_paths

    class _Scrambled:
        def glob(self, pattern):
            assert pattern == _STRAY_TYPE_MAP_GLOB
            return iter(Path(f"/d/type-map-{c}.json") for c in "cabd")

    assert [p.name for p in _stray_type_map_paths(_Scrambled())] == [
        f"type-map-{c}.json" for c in "abcd"]
