"""End-to-end validation tests — the validator delegates single-document
validity to the contract models and adds the cross-file coverage checks.

The `invalid_write_from_input` case is the original sevdesk defect that started
this work: a write body mapping a bare field name (`{from_input: "category"}`)
instead of the record. The model rejects it, so the validator now catches it —
the gap the old validator missed.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _screen import cli_env, run_cli, run_cli_argv
from analitiq.contracts.endpoint_identity import derive_db_endpoint_id, slug
from analitiq.validator import (
    GUARD_DEFAULT_BLAME,
    GUARD_RESOURCE_BLAME,
    is_guard_finding,
)

CORPUS = Path(__file__).resolve().parent / "corpus"
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
    return [f for f in findings if f["severity"] == "error"]


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
        f["validator"] == "contract-model" and f["path"].endswith("/driver")
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


def _write_tree(root: Path, connector: dict, read_map, endpoints: dict):
    (root / "endpoints").mkdir(parents=True)
    (root / "connector.json").write_text(json.dumps(connector))
    (root / "type-map-read.json").write_text(json.dumps(read_map))
    for name, ep in endpoints.items():
        (root / "endpoints" / name).write_text(json.dumps(ep))


API = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JS = "https://json-schema.org/draft/2020-12/schema"


def _endpoint(native_type, arrow_type, endpoint_id="widgets", path="/widgets"):
    return {
        "$schema": API, "endpoint_id": endpoint_id,
        "operations": {"read": {
            "request": {"method": "GET", "path": path}, "params": {},
            "response": {
                "records": {"ref": "response.body"},
                "schema": {"$schema": JS, "type": "array", "items": {"type": "object",
                    "properties": {"a": {"type": "string",
                        "native_type": native_type, "arrow_type": arrow_type}}}},
            }}}}


def test_valid_embedded_schema_passes(validator):
    ep = _endpoint("STRING", "Utf8")
    assert not any(
        e["validator"] == "embedded-json-schema"
        for e in _errors(validator.validate_document(ep))
    )


def test_embedded_schema_must_be_valid_draft_2020_12(validator):
    """An embedded input/response schema that parses (arrow-valid) but is not a
    valid JSON Schema Draft 2020-12 document is caught by the validator. The
    contract model checks the arrow_type pairing, not meta-schema validity."""
    ep = _endpoint("STRING", "Utf8")
    # `minItems` must be a non-negative integer; a string is meta-invalid, and the
    # contract model doesn't inspect it, so only the embedded-json-schema check fires.
    ep["operations"]["read"]["response"]["schema"]["minItems"] = "notanumber"
    errors = _errors(validator.validate_document(ep))
    assert any(e["validator"] == "embedded-json-schema" for e in errors), errors


def test_embedded_schema_rejects_other_dialect(validator):
    """A meta-valid schema that DECLARES another draft (e.g. Draft-07) is not
    Draft 2020-12 and is rejected — `check_schema` alone would miss it."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["$schema"] = (
        "http://json-schema.org/draft-07/schema#"
    )
    errors = _errors(validator.validate_document(ep))
    assert any(e["validator"] == "embedded-json-schema" for e in errors), errors


def _sample_endpoint(node: dict, **defs):
    """An endpoint whose record carries one field node — the slot a recorded
    sample sits in."""
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["items"]["properties"]["a"] = node
    if defs:
        schema["$defs"] = defs
    return ep


def _example_errors(validator, ep):
    return [
        e for e in _errors(validator.validate_document(ep))
        if e["validator"] == "embedded-schema-example"
    ]


def test_recorded_sample_satisfying_its_node_passes(validator):
    ep = _sample_endpoint({
        "type": ["boolean", "null"], "native_type": "boolean",
        "arrow_type": "Boolean", "examples": [True, None]})
    assert _example_errors(validator, ep) == []


def test_recorded_sample_contradicting_its_node_is_rejected(validator):
    """The shape that ships a connector which validates clean and dies on the
    first batch: the provider documents a boolean and sends the string "0"."""
    ep = _sample_endpoint({
        "type": ["boolean", "null"], "native_type": "boolean",
        "arrow_type": "Boolean", "examples": ["0"]})
    errors = _example_errors(validator, ep)
    assert len(errors) == 1, errors
    assert errors[0]["path"].endswith("/properties/a/examples/0"), errors
    assert "'0'" in errors[0]["message"], errors


def test_every_recorded_sample_is_graded(validator):
    """Not just the first: an author who fixes one spelling and reruns should
    not walk into the next one report at a time."""
    ep = _sample_endpoint({
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        "examples": ["fine", 7, 9]})
    assert len(_example_errors(validator, ep)) == 2


def test_a_sample_is_graded_through_the_reference_its_node_carries(validator):
    """A node whose declaration is a `#/$defs/...` reference is graded against
    what the reference resolves to — the whole document is in scope, so a
    validator built on the node alone would call the reference unresolvable."""
    ep = _sample_endpoint(
        {"$ref": "#/$defs/Money", "examples": [9.99]},
        Money={"type": "string", "native_type": "STRING", "arrow_type": "Utf8"})
    errors = _example_errors(validator, ep)
    assert len(errors) == 1, errors
    assert "9.99" in errors[0]["message"], errors


def test_one_sample_earns_one_finding_however_many_ways_it_fails(validator):
    """A sample violating several keywords is one defect, reported once — and
    the reporting stays a list of findings.

    Pinned because `_sample_findings`' return type is what tells a crash from
    a verdict: the guard around it hands back whatever it returns, and a
    change to "report every way it fails" that reached for `iter_errors`
    directly would push raw `ValidationError` objects into the finding stream,
    where the screen reading `message` off each item meets an attribute that
    is not there.
    """
    ep = _sample_endpoint({
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        "minLength": 5, "pattern": "^z", "examples": ["ab"]})
    errors = _example_errors(validator, ep)
    assert len(errors) == 1, errors


def test_a_sample_finding_names_the_file_it_came_from(tmp_path, connector_base,
                                                      validator):
    """`path` is a pointer into one document and says nothing about which. An
    author validating a connector holds several endpoint files, so the message
    carries the label — the half `path` cannot."""
    ep = _sample_endpoint({
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        "examples": [7]})
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
                {"widgets.json": ep})
    hits = [f for f in validator.check_coverage(connector_base,
                                                tmp_path / "connector.json")
            if f["validator"] == "embedded-schema-example"]
    assert len(hits) == 1, hits
    assert "widgets.json" in hits[0]["message"], hits[0]


def test_a_write_input_records_samples_too(validator):
    """The other schema an endpoint declares. An implementation grading only
    the read response, or only the first embedded schema it meets, passes every
    other test here."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["write"] = {
        mode: {
            "request": {"method": "POST", "path": "/widgets",
                        "body": {"from_input": "record"}},
            "params": {},
            "input": {"schema": {"$schema": JS, "type": "object", "properties": {
                "a": {"type": "string", "native_type": "STRING",
                      "arrow_type": "Utf8", "examples": [bad]}}}},
        }
        for mode, bad in (("insert", 1), ("upsert", 2))
    }
    ep["operations"]["write"]["upsert"]["conflict_keys"] = ["a"]
    paths = [e["path"] for e in _example_errors(validator, ep)]
    assert sorted(paths) == [
        "/operations/write/insert/input/schema/properties/a/examples/0",
        "/operations/write/upsert/input/schema/properties/a/examples/0",
    ], paths


def test_a_sample_is_graded_against_its_own_node_not_the_composition(validator):
    """`examples` sits on the node that records it, and that node is what
    grades it — an `allOf` sibling constrains the instances the schema as a
    whole accepts, not the sample recorded one branch down.

    Both halves in one document, because either alone passes vacuously: a
    branch the walk never reaches yields no finding, which is indistinguishable
    from a branch it reached and let through.
    """
    ep = _sample_endpoint({"allOf": [
        {"type": "integer"},
        {"examples": ["x"]},
        {"type": "string", "examples": [1]},
    ]})
    errors = _example_errors(validator, ep)
    # The third branch's sample fails its OWN branch, so the walk demonstrably
    # descends into `allOf`; the second branch's does not fail anything,
    # because the sibling `type: integer` is not its node's declaration.
    assert [e["path"] for e in errors] == [
        "/operations/read/response/schema/items/properties/a/allOf/2/examples/0"
    ], errors


@pytest.mark.parametrize("key", ["not", "if", "propertyNames"])
def test_a_sample_under_a_negation_is_not_graded_against_its_own_opposite(
    validator, key,
):
    """RULE-ENDP-064 grades a sample against "that node's own declarations".
    Under one of these the declarations describe what the value must NOT be —
    a `"0"` recorded beside `not: {type: boolean}` is the correct
    counter-example — so grading it reports the author's right answer as a
    contradiction, error-severity, telling them to change one of the two.

    `propertyNames` is the same shape by a different route: it constrains the
    KEYS, and a sample is never a key."""
    ep = _sample_endpoint({key: {"type": "boolean", "examples": ["0"]}})
    assert _example_errors(validator, ep) == []


@pytest.mark.parametrize("name", ["not", "if", "propertyNames"])
def test_a_field_named_after_a_negating_keyword_is_still_graded(validator, name):
    """A pointer segment is a property NAME as often as it is a keyword
    position, and `/properties/not` is a field a provider called `not`. Read
    back off the pointer, the two are the same string — so a field named for a
    keyword dropped out of grading silently, which is the validates-clean-and-
    breaks shape this rule exists to close, on any provider whose field is
    called `if`."""
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["items"]["properties"][name] = {
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        "examples": [7]}
    paths = [e["path"] for e in _example_errors(validator, ep)]
    assert any(f"/properties/{name}/examples/0" in p for p in paths), paths


@pytest.mark.parametrize("key", ["not", "if", "propertyNames"])
@pytest.mark.parametrize("descent", ["properties", "anyOf", "items"])
def test_a_typed_sample_under_a_negation_parses_and_is_exempt(
    validator, key, descent,
):
    """The end-to-end case neither half covered: a node carrying an
    `arrow_type` under a negating position. The contract models have to let the
    document through AND this check has to pass over it — an untyped node
    cannot tell the two apart, since a model rejection would satisfy the same
    empty result.

    Nested one level down, once per descent SHAPE: the flag travels through
    three loops, and nesting under `properties` alone pins one of them."""
    inner = {"type": "string", "native_type": "STRING", "arrow_type": "Utf8",
             "examples": [7]}
    below = {
        "properties": {"type": "object", "properties": {"inner": inner}},
        "anyOf": {"anyOf": [inner]},
        "items": {"type": "array", "items": inner},
        # No tuple-form `items` here: 2020-12 requires `items` to be a schema,
        # so `check_schema` refuses the document and grading never runs — the
        # assertion would be satisfied by the metaschema rejection rather than
        # by the exemption. That arm is pinned at the walker instead, in
        # `test_embedded_schema_refs.py`.
    }[descent]
    ep = _sample_endpoint({key: below})
    findings = validator.validate_document(ep)
    assert not [f for f in findings if f["validator"] == "contract-model"], findings
    assert not [f for f in findings
                if f["validator"] == "embedded-schema-example"], findings


def test_a_sample_beside_a_negation_is_still_graded(validator):
    """The negation is a position, not a spreading property: a node that
    merely HAS a `not` still grades the sample recorded on itself."""
    ep = _sample_endpoint({
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        "not": {"const": "x"}, "examples": [7]})
    errors = _example_errors(validator, ep)
    assert len(errors) == 1, errors


def test_samples_are_graded_wherever_a_declaration_can_sit(validator):
    """`properties` is where a record's fields live, and it is not the only
    schema position the walk reaches."""
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["items"]["prefixItems"] = [{"type": "string", "examples": [1]}]
    schema["examples"] = ["not an array"]
    paths = sorted(e["path"] for e in _example_errors(validator, ep))
    assert paths == [
        "/operations/read/response/schema/examples/0",
        "/operations/read/response/schema/items/prefixItems/0/examples/0",
    ], paths


def test_an_empty_examples_array_records_nothing_and_is_graded_as_nothing(
    validator,
):
    """The boundary between "no samples" and "samples": the key is present, so
    the node is collected, and there is still nothing to disagree with.

    Asserted beside a node that DOES record one, because an empty list alone is
    indistinguishable from a collection predicate that stopped collecting:
    relaxing `isinstance(node.get("examples"), list)` to a truthiness test
    leaves an empty-list-only document green either way.
    """
    ep = _sample_endpoint({
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        "examples": []})
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["items"]["properties"]["b"] = {
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        "examples": [7]}
    paths = [e["path"] for e in _example_errors(validator, ep)]
    assert paths == [
        "/operations/read/response/schema/items/properties/b/examples/0"
    ], paths


def test_the_finding_carries_the_shared_remedy(validator):
    """One wording, whichever rule fires — an author meeting both on one
    document should not read one instruction twice."""
    from analitiq.contracts.endpoints import SAMPLE_CONTRADICTION_REMEDY

    ep = _sample_endpoint({
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        "examples": [1]})
    errors = _example_errors(validator, ep)
    assert len(errors) == 1, errors
    assert SAMPLE_CONTRADICTION_REMEDY in errors[0]["message"], errors[0]


def test_a_finding_path_is_a_resolvable_json_pointer(validator):
    """A property name carrying `/` is ONE pointer token; unescaped it points
    at a node that is not there, and the reader is sent to the wrong field."""
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["items"]["properties"]["a/b~c"] = {
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        "examples": [7]}
    errors = _example_errors(validator, ep)
    assert len(errors) == 1, errors
    assert errors[0]["path"].endswith("/properties/a~1b~0c/examples/0"), errors


@pytest.mark.parametrize("ref", ["#/$defs/Missing", "https://example.com/x.json"])
def test_a_reference_that_does_not_resolve_still_names_itself(validator, ref):
    """Resolution happens here for the first time in this module, so a
    reference the contract already refuses raises out of the grading. Two
    things must hold: RULE-ENDP-026 still names the reference precisely, and
    the grading failure reads as the document's rather than as a bug in this
    tool — the reference IS the author's to fix."""
    ep = _sample_endpoint({"$ref": ref, "examples": [1]})
    # The reference is what makes grading raise, so the crash IS the subject.
    findings = validator.validate_document(ep, expect_crash=True)
    crashed = [f for f in findings if is_guard_finding(f)]
    # Constrained to ONE crash from the sample grading: satisfied by a crash
    # from any check, this would still pass if the reference brought down
    # something else and the grading never ran.
    assert len(crashed) == 1, findings
    assert crashed[0]["validator"] == "embedded-schema-example", crashed
    assert ref in crashed[0]["message"], crashed[0]
    assert not any(GUARD_DEFAULT_BLAME in f["message"] for f in findings), findings


def test_the_fixture_screen_refuses_a_crash(validator):
    """The screen every other test here leans on, pinned.

    Nothing else can see it: it only ever ADDS an assertion, so neutering it
    leaves every suite green — the silent-death shape this branch already met
    once, when a renamed message left the probe registry's crash tripwire
    matching nothing.
    """
    ep = _sample_endpoint({"$ref": "#/$defs/Missing", "examples": [1]})

    with pytest.raises(AssertionError, match="a check crashed on this document"):
        validator.validate_document(ep)

    # And the other direction: a flag that stopped describing its document.
    with pytest.raises(AssertionError, match="nothing crashed"):
        validator.validate_document(_endpoint("STRING", "Utf8"), expect_crash=True)


def test_the_fixture_screen_covers_the_other_entry_point(
    tmp_path, connector_base, validator,
):
    """`check_coverage` returns findings too, so a crash reaches a test through
    it just as well. Screening one entry point and not the other is a screen
    with a hole exactly where nobody is looking."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["items"]["properties"]["a"] = {
        "$ref": "#/$defs/Missing", "examples": [1]}
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
                {"widgets.json": ep})
    with pytest.raises(AssertionError, match="a check crashed on this document"):
        validator.check_coverage(connector_base, tmp_path / "connector.json")


def test_a_sample_that_cannot_be_graded_costs_only_that_check(validator):
    """Grading a sample raises on author content — a reference with no target.
    Unguarded, that escapes the dispatch and replaces every finding on the
    document with one line telling the author to report a validator bug,
    losing the defect they were fixing."""
    ep = _sample_endpoint({"$ref": "#/$defs/Missing", "examples": [1]})
    ep["endpoint_id"] = "WRONG NAME"
    findings = validator.validate_document(ep, expect_crash=True)
    crashed = [f for f in findings if is_guard_finding(f)]
    assert len(crashed) == 1, findings
    assert crashed[0]["validator"] == "embedded-schema-example", crashed
    assert GUARD_DEFAULT_BLAME not in crashed[0]["message"], crashed[0]
    # The unrelated defect survives it.
    assert any(f["validator"] == "endpoint-id-locator" for f in findings), findings


#: Deep enough to exhaust the walk, shallow enough for `json.dumps` to write
#: the fixture: the encoder recurses too, and its own limit is the lower of the
#: two on some interpreters. Should a future one walk this without running out,
#: the screen's `expect_crash=True` fails rather than passing quietly.
_EXHAUSTING_DEPTH = 400


def _deeply_nested_schema(depth: int = _EXHAUSTING_DEPTH) -> dict:
    root = node = {"type": "object"}
    for _ in range(depth):
        node["properties"] = {"x": {"type": "object"}}
        node = node["properties"]["x"]
    return root


def test_a_document_too_deep_to_walk_is_not_the_authors_bug_to_file(validator):
    """Every walk here recurses on the document's own nesting, and the walk
    that runs out of stack first is whichever one reaches the depth — often
    one no guard wraps, since the contract models walk before any check does.
    Landing there, the default blame would send the author to file a bug about
    a crash their own document caused.

    So the exception TYPE decides: running out of stack says what the cause
    was regardless of which walk was holding it, and regardless of what the
    caller expected to go wrong."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["items"] = (
        _deeply_nested_schema())
    findings = validator.validate_document(ep, expect_crash=True)
    crashed = [f for f in findings if is_guard_finding(f)]
    assert len(crashed) == 1, findings
    # WHICH guard catches it is not asserted, and cannot be: the walk that
    # runs out of stack is whichever reached the depth, which is the whole
    # reason the type decides the blame rather than the call site. What every
    # one of them must not say is that this is a bug to report.
    assert GUARD_DEFAULT_BLAME not in crashed[0]["message"], crashed[0]
    assert "nests deeper" in crashed[0]["message"], crashed[0]
    # The type, and no more of the exception than that: a RecursionError
    # raised while unwinding names whichever frame happened to be innermost,
    # which is not the walk that filled the stack and reads to an author as
    # though it were.
    assert "RecursionError" in crashed[0]["message"], crashed[0]


def test_a_document_that_only_the_fold_recurses_on_lands_on_the_guard(validator):
    """The other shape that exhausts the stack, and the one the contract models
    deliberately stopped absorbing.

    `_deeply_nested_schema` is deep STRUCTURALLY, so the structural walks give
    out. This document is flat — a `$defs` map of siblings — and only the fold
    following the chain recurses. That raise leaves the contract models by
    design, so what matters is that the layer above turns it into a finding
    rather than a traceback in an author's terminal."""
    depth = 1500
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["$defs"] = {f"D{i}": {"$ref": f"#/$defs/D{i + 1}"}
                       for i in range(depth)}
    schema["$defs"][f"D{depth}"] = {
        "type": "string", "native_type": "date-time",
        "arrow_type": "Timestamp(MICROSECOND, UTC)"}
    schema["items"]["properties"]["a"] = {
        "$ref": "#/$defs/D0", "examples": ["2024-01-02T03:04:05"]}
    findings = validator.validate_document(ep, expect_crash=True)
    crashed = [f for f in findings if is_guard_finding(f)]
    assert len(crashed) == 1, findings
    assert "nests deeper" in crashed[0]["message"], crashed[0]


def test_the_cause_a_crash_names_is_chosen_by_what_went_wrong(validator):
    """Which of the three causes a crash reports, at the level it is decided.

    End to end only ever exercises whichever walk crashed first, so the
    branches are driven here. What each must not do is describe a defect in
    this tool as something the author wrote: a caller's reading of its own
    failure modes is a reading of what a DOCUMENT can do to it, and a
    `TypeError` is not that however confident the caller was."""
    from analitiq.validator._core import _run_guarded

    def _deep():
        raise RecursionError
    def _bug():
        raise TypeError("not a size problem")
    def _content():
        raise OverflowError("int too large")

    # Out of room: depth is the usual cause, so the default says so — but a
    # caller that follows references knows the other one, since a `$ref`
    # leading back to itself exhausts the stack on a shallow document and
    # "flatten the nesting" fixes nothing there.
    unnamed_room = _run_guarded(_deep, vid="document", path="/")
    assert GUARD_RESOURCE_BLAME in unnamed_room[0]["message"], unnamed_room
    named_room = _run_guarded(_deep, vid="document", path="/", blame="mine")
    assert "mine" in named_room[0]["message"], named_room
    assert GUARD_RESOURCE_BLAME not in named_room[0]["message"], named_room

    # A defect in this tool: likewise not the caller's to reinterpret.
    ours = _run_guarded(_bug, vid="document", path="/", blame="mine")
    assert GUARD_DEFAULT_BLAME in ours[0]["message"], ours
    assert "mine" not in ours[0]["message"], ours

    # Brought down by what it was reading, which is the one the caller knows
    # more about than the type does.
    theirs = _run_guarded(_content, vid="document", path="/", blame="mine")
    assert "mine" in theirs[0]["message"], theirs
    assert GUARD_DEFAULT_BLAME not in theirs[0]["message"], theirs
    # And with no caller wording, it falls back rather than inventing one.
    unnamed = _run_guarded(_content, vid="document", path="/")
    assert GUARD_DEFAULT_BLAME in unnamed[0]["message"], unnamed


def test_the_rendered_cause_is_the_one_a_finding_carries(validator):
    """The plugin README renders `GUARD_RESOURCE_CAUSE` as the literal text a
    reader matches a finding against, and findings carry
    `GUARD_RESOURCE_BLAME`. Today an f-string holds them together, and this is
    the tripwire against inlining the text instead: a rewording of BLAME that
    stopped opening with CAUSE would render a clause no finding contains, and
    the block check compares the file to the renderer, so both would move
    together and nothing else would go red."""
    from analitiq.validator import GUARD_RESOURCE_CAUSE

    assert GUARD_RESOURCE_BLAME.startswith(GUARD_RESOURCE_CAUSE), (
        "the README renders the cause clause on its own; a finding must open "
        "with the same words or the reader is matching against text no "
        "finding carries"
    )


def test_the_scope_a_crash_names_survives_whatever_the_cause_was(validator):
    """Which slot was being checked and what else survived is the caller's
    alone, and true whichever way the check went wrong.

    Folded into the cause it was conditional on the caller's guess being
    right: a coding defect reported as "this document nests too deep" ALSO
    lost the only line naming which endpoint it happened on."""
    from analitiq.validator._core import _run_guarded

    for boom in (RecursionError, TypeError, OverflowError):
        def _raise(exc=boom):
            raise exc("x")
        out = _run_guarded(_raise, vid="document", path="/",
                           blame="mine", scope="Endpoint a.json was not checked.")
        assert "Endpoint a.json was not checked." in out[0]["message"], out


@pytest.mark.parametrize("crashing_check", [
    "_model_findings",
    "_endpoint_locator_findings",
    "_endpoint_transport_ref_findings",
    "_type_map_coverage_findings",
])
def test_one_endpoint_that_cannot_be_checked_costs_only_that_endpoint(
    tmp_path, connector_base, validator, monkeypatch, crashing_check,
):
    """A connector's endpoints are checked in one loop, and a raise anywhere in
    it escapes to the dispatch — which discards every finding on every OTHER
    endpoint too. The guarded unit is therefore the endpoint, not one check
    inside it.

    Driven by making each check raise in turn rather than by a document deep
    enough to exhaust it, because which walk gives out first is a property of
    the document: on this tree the embedded-schema walk goes first, at a depth
    the JSON writer building the fixture cannot itself reach on every
    interpreter. What is under test is the containment, and the containment is
    the same whichever check it was.
    """
    from analitiq.validator import connectors

    def _boom(*_args, **_kwargs):
        raise RuntimeError("brought down by this endpoint")

    monkeypatch.setattr(connectors, crashing_check, _boom)
    bad = _endpoint("STRING", "Utf8")
    bad["endpoint_id"] = "badone"
    other = _endpoint("STRING", "Utf8")
    other["endpoint_id"] = "WRONG NAME"
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
                {"badone.json": bad, "widgets.json": other})
    findings = validator.check_coverage(
        connector_base, tmp_path / "connector.json", expect_crash=True)
    crashed = [f for f in findings if is_guard_finding(f)]
    # Every endpoint meets the same broken check, so every endpoint reports —
    # which is the containment: the first crash did not stop the loop reaching
    # the second. Each names its own file, because `path` is `/` and without
    # the name the author is told a connector somewhere has an endpoint they
    # cannot find.
    #
    # Not a count: `_model_findings` is also how the sibling type map is read,
    # so that parametrisation reports for the map as well — contained the same
    # way, one document earlier.
    scoped = " ".join(f["message"] for f in crashed)
    assert "badone.json" in scoped and "widgets.json" in scoped, crashed


def test_a_long_exception_keeps_both_ends(validator):
    """The bounded detail is cut in the middle, not at the tail.

    `jsonschema`'s referencing errors read "PointerToNowhere: '/$defs/X' does
    not exist within {…the whole schema root…}", so the pointer — the only
    actionable word — is at the front and the filler is behind it. A cut that
    always drops one end drops that word for whichever implementation puts its
    subject there."""
    from analitiq.validator._core import _GUARD_DETAIL_MAX, _detail

    exc = ValueError("HEAD" + ("x" * _GUARD_DETAIL_MAX * 2) + "TAIL")
    detail = _detail(exc)
    assert len(detail) <= _GUARD_DETAIL_MAX + 1, len(detail)
    assert "HEAD" in detail and "TAIL" in detail, detail


def test_a_crash_the_document_did_not_cause_still_blames_this_tool(validator):
    """The other direction of the same wording. Several tests assert the
    default blame is ABSENT from a document-caused crash, and an absence
    assertion against a string nothing emits is quiet rather than red — so one
    test has to require it where it belongs."""
    from analitiq.validator._core import _run_guarded

    def _boom():
        raise KeyError("x")

    emitted = _run_guarded(_boom, vid="contract-model")[0]
    assert is_guard_finding(emitted), emitted
    assert GUARD_DEFAULT_BLAME in emitted["message"], emitted


def test_a_crash_finding_does_not_carry_a_whole_schema(validator):
    """`jsonschema`'s referencing errors put the entire schema root in their
    repr. Unbounded, one finding an agent has to read is kilobytes of JSON —
    and the exception text is the half this tool does not write."""
    from analitiq.validator._core import _GUARD_DETAIL_MAX, _run_guarded

    def _boom():
        raise ValueError("x" * 5000)

    emitted = _run_guarded(_boom, vid="contract-model")[0]
    assert len(emitted["message"]) < _GUARD_DETAIL_MAX + 400, len(emitted["message"])
    assert emitted["message"].endswith(GUARD_DEFAULT_BLAME), emitted


def test_a_non_string_dialect_below_the_root_is_malformed_not_another_draft(
    validator,
):
    """Nothing switches dialect on a number, so naming it a draft mismatch
    sends the author to fix something that is not there — and the `continue`
    behind that finding hides the metaschema error that names the real one."""
    ep = _sample_endpoint({"$schema": 7, "type": "string", "examples": ["ok"]})
    errors = _errors(validator.validate_document(ep))
    messages = [e["message"] for e in errors
                if e["validator"] == "embedded-json-schema"]
    assert messages, errors
    assert any("is not of type 'string'" in m for m in messages), messages
    assert not any("another draft" in m for m in messages), messages


def test_a_wrong_root_draft_still_reports_the_metaschema_errors(validator):
    """`check_schema` grades against this class's own metaschema whatever the
    document declares, so its findings stand after the `$schema` is fixed and
    withholding them costs a rerun."""
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["items"]["properties"]["a"] = {"type": "array", "minItems": "no"}
    messages = [f["message"] for f in validator.validate_document(ep)
                if f["validator"] == "embedded-json-schema"]
    assert any("declares $schema" in m for m in messages), messages
    assert any("not a valid JSON Schema Draft 2020-12" in m
               for m in messages), messages


def test_a_wrong_root_draft_still_reports_a_nested_one(validator):
    """The nested scan is a text walk over node pointers and never touches
    `evolve`, so the reason the root branch withholds sample grading does not
    reach it. Stopping at the root would report one draft, take a fix, and
    report the other on the next run."""
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["items"]["properties"]["a"] = {
        "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"}
    messages = [f["message"] for f in validator.validate_document(ep)
                if f["validator"] == "embedded-json-schema"]
    assert any("declares $schema" in m for m in messages), messages
    assert any("declares another draft at" in m for m in messages), messages


def test_a_wrong_root_draft_withholds_the_sample_grading(validator):
    """`evolve` reads `$schema` off the node it is handed, and for the ROOT
    that node is the schema itself — so a foreign draft there is picked up and
    every sample in the document is graded under it. A `prefixItems` violation
    goes unreported under draft-07, which has no such keyword: the author would
    be told their sample is fine in a dialect the contract does not run."""
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["items"]["properties"]["a"] = {
        "type": "array", "prefixItems": [{"type": "integer"}],
        "examples": [["x"]]}
    graded = {f["validator"] for f in validator.validate_document(ep)}
    assert "embedded-schema-example" in graded, graded

    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    withheld = {f["validator"] for f in validator.validate_document(ep)}
    assert "embedded-schema-example" not in withheld, withheld
    assert "embedded-json-schema" in withheld, withheld


def test_a_nested_draft_does_not_withhold_the_metaschema_findings(validator):
    """A nested `$schema` changes which draft `evolve` reads a SUBTREE in, so
    the sample grading is withheld. It does not reach `check_schema`, which
    grades against this class's own metaschema — so withholding those findings
    too would cost the author a rerun for nothing."""
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["items"]["properties"]["a"] = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "array", "minItems": "notanumber"}
    messages = [f["message"] for f in validator.validate_document(ep)
                if f["validator"] == "embedded-json-schema"]
    assert any("another draft" in m for m in messages), messages
    assert any("not a valid JSON Schema Draft 2020-12" in m
               for m in messages), messages


def test_a_crash_while_grading_costs_this_check_and_nothing_else(validator):
    """Grading an instance runs arbitrary keyword logic, and no gate ahead of
    it makes that total: `multipleOf` against a 400-digit sample raises out of
    `iter_errors`, and the metaschema has no opinion about it. Unguarded, the
    dispatch-level guard answers that by replacing every finding on the
    document — so the author loses the defects they were fixing and is told to
    report a bug."""
    ep = _sample_endpoint({
        "type": "number", "multipleOf": 0.5, "examples": [int("9" * 400)]})
    ep["endpoint_id"] = "WRONG NAME"
    findings = validator.validate_document(ep, expect_crash=True)
    crashed = [f for f in findings if is_guard_finding(f)]
    assert len(crashed) == 1, findings
    assert crashed[0]["validator"] == "embedded-schema-example", crashed
    # What brought it down came out of the author's document, so the finding
    # must not send them to report a validator bug, and it must name the node
    # it was grading rather than the document as a whole.
    assert GUARD_DEFAULT_BLAME not in crashed[0]["message"], crashed[0]
    assert "/properties/a" in crashed[0]["message"], crashed[0]
    # The sample, not the schema: the guard is per-sample, so the author is
    # pointed at the one value that brought the grading down.
    assert crashed[0]["path"].endswith("/properties/a/examples/0"), crashed[0]
    # The unrelated defect is still reported beside it.
    assert any(f["validator"] == "endpoint-id-locator" for f in findings), findings


def _order_mismatch(expected: list, got: list) -> str:
    """Where two orderings first part, and what sits there.

    Not the whole lists and not their first elements: a run that diverges at
    element 5 shares elements 0-4, so printing element 0 prints the same line
    twice and slices the signal away. This guard catches a nondeterminism
    nobody reproduces locally without setting `PYTHONHASHSEED`, so what it
    prints is the whole of what a maintainer gets.
    """
    for index, (a, b) in enumerate(zip(expected, got)):
        if a != b:
            return (f"orderings part at index {index}:\n"
                    f"  expected {a!r}\n  got      {b!r}\n"
                    f"  full: {expected}\n        {got}")
    return f"orderings differ in length:\n  {expected}\n  {got}"


def test_findings_come_back_in_the_same_order_every_run(validator):
    """`iter_schema_nodes` reaches nodes through frozensets, so unsorted it
    ordered every consumer's findings by the interpreter's hash seed: an author
    reran the validator on an unchanged document and got the same findings in a
    different order. Subprocesses, because a seed is fixed for the life of one.

    Three things the document has to be, each learned the hard way. Several
    members of one bucket on ONE node — with one member per bucket the loop has
    no order to vary and every sort here is unpinned. Several that actually
    PRODUCE a finding: `not` is exempt from grading, so a bucket holding `not`
    and one other left nothing to reorder and this reddened on some hash seeds
    and not others. And no crash: a document
    the model layer aborts on yields a single guard finding, which compares
    equal across every seed while measuring nothing, and a liveness assert on a
    non-empty list is satisfied by it.
    """
    import textwrap

    sample = {"type": "string", "native_type": "STRING", "arrow_type": "Utf8",
              "examples": [1]}
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["records"] = {"ref": "response.body"}
    ep["operations"]["read"]["response"]["schema"]["items"] = {
        "type": "object",
        "properties": {"p": dict(sample)},
        "patternProperties": {"^x": dict(sample)},
        "$defs": {"D": dict(sample)},
        "oneOf": [dict(sample)],
        "anyOf": [dict(sample)],
        "not": dict(sample),
        "contains": dict(sample),
        "additionalProperties": dict(sample),
        "then": dict(sample),
    }

    # The fixture refuses a guard finding, so what comes back is verdicts.
    baseline = validator.validate_document(ep)
    assert len(baseline) > 1, baseline

    program = textwrap.dedent(
        """
        import json, sys
        import analitiq.validator as v
        doc = json.loads(sys.argv[1])
        json.dump([f["path"] for f in v.validate_document(doc)], sys.stdout)
        """
    )
    runs = []
    for seed in ("0", "1", "2", "3", "4", "5", "6", "7"):
        proc = subprocess.run(
            [sys.executable, "-c", program, json.dumps(ep)],
            capture_output=True, text=True, check=True,
            env=cli_env(PYTHONHASHSEED=seed),
        )
        runs.append(json.loads(proc.stdout))
    assert len(runs[0]) == len(baseline), (runs[0], baseline)
    differing = [run for run in runs if run != runs[0]]
    assert not differing, _order_mismatch(runs[0], differing[0])


def test_a_non_string_root_dialect_is_malformed_not_another_draft(validator):
    """`{"$schema": 7}` at the root: nothing switches dialect on a number, and
    the `continue` behind a draft-mismatch finding hides the metaschema error
    that names the real defect. The nested branch reads it the same way."""
    ep = _endpoint("STRING", "Utf8")
    ep["operations"]["read"]["response"]["schema"]["$schema"] = 7
    messages = [e["message"] for e in _errors(validator.validate_document(ep))
                if e["validator"] == "embedded-json-schema"]
    assert messages, "no embedded-json-schema finding at all"
    assert any("is not of type 'string'" in m for m in messages), messages
    assert not any("requires JSON Schema Draft" in m for m in messages), messages


def test_a_sample_is_graded_with_the_whole_document_in_scope(validator):
    """`root.evolve(schema=node)` keeps the document's resolution scope, so a
    `#/$defs/...` inside the node still resolves. Built on the node alone, the
    reference is unresolvable and every sample under it becomes a crash finding
    — the author is told the tool broke instead of what their sample says."""
    ep = _endpoint("STRING", "Utf8")
    schema = ep["operations"]["read"]["response"]["schema"]
    schema["$defs"] = {"S": {"type": "string"}}
    schema["items"]["properties"]["a"] = {
        "type": "object", "properties": {"x": {"$ref": "#/$defs/S"}},
        "examples": [{"x": 1}]}
    findings = validator.validate_document(ep)
    assert any("is not of type 'string'" in f["message"] for f in findings), findings


def test_a_bad_reference_does_not_suppress_samples_elsewhere(validator):
    """A reference the contract refuses must not suppress the sample findings
    around it. Skipping the schema it sits in costs the author every other
    sample there, and they meet the rest in a second wave once the reference is
    fixed — which is `test_every_recorded_sample_is_graded` one scope up."""
    ep = _endpoint("STRING", "Utf8")
    props = ep["operations"]["read"]["response"]["schema"]["items"]["properties"]
    good = {"type": "string", "native_type": "STRING", "arrow_type": "Utf8",
            "examples": [1]}
    props["a"] = dict(good)
    # WITH `examples`, so the bad node is actually graded and actually raises.
    # Without them it is never reached, and this test passed while proving
    # nothing — which is how the per-schema guard survived a round.
    props["b"] = {"$ref": "#/$defs/Missing", "examples": [1]}
    props["c"] = dict(good)
    # `b` raises on purpose; the point is that `a` and `c` still report.
    findings = validator.validate_document(ep, expect_crash=True)
    graded = {f["path"] for f in findings
              if f["validator"] == "embedded-schema-example"}
    for field in ("a", "b", "c"):
        assert any(f"/properties/{field}/examples/0" in p for p in graded), (
            field, sorted(graded))
    assert any("does not resolve" in f["message"] for f in findings), findings


def test_another_dialect_declared_below_the_root_is_refused(validator):
    """A jsonschema implementation switches dialect for that subtree, so the
    sample would be graded under a draft neither this contract nor the engine
    reads. RULE-ENDP-048's obligation, at a node instead of the root."""
    ep = _sample_endpoint({
        "$schema": "http://json-schema.org/draft-07/schema#",
        "dependencies": {"a": ["b"]},
        "examples": [{"a": 1}]})
    errors = _errors(validator.validate_document(ep))
    assert any(
        e["validator"] == "embedded-json-schema" and "another draft" in e["message"]
        for e in errors), errors
    assert not any(e["validator"] == "embedded-schema-example" for e in errors), errors


def test_the_documents_own_dialect_repeated_below_the_root_is_not(validator):
    """Redundant, not wrong. Refusing it would be an obligation no rule
    states — RULE-ENDP-048 forbids declaring a DIFFERENT draft, and the
    rendered reference an author satisfies says exactly that."""
    ep = _sample_endpoint({
        "$schema": JS, "type": "string", "native_type": "STRING",
        "arrow_type": "Utf8", "examples": ["fine"]})
    errors = _errors(validator.validate_document(ep))
    assert not any(e["validator"] == "embedded-json-schema" for e in errors), errors


def test_samples_are_not_graded_against_a_malformed_schema(validator):
    """A schema `check_schema` rejects is reported once, as the malformation it
    is — not a second time in the vocabulary of whichever sample met it."""
    ep = _sample_endpoint({
        "type": "string", "native_type": "STRING", "arrow_type": "Utf8",
        # `minLength` must be a non-negative integer. Grading an instance
        # against this node does not report a bad sample — it compares a length
        # to a string and raises out of the validator entirely.
        "minLength": "notanumber", "examples": ["fine"]})
    errors = _errors(validator.validate_document(ep))
    assert any(e["validator"] == "embedded-json-schema" for e in errors), errors
    assert not any(e["validator"] == "embedded-schema-example" for e in errors), errors


@pytest.fixture
def connector_base():
    # A real, model-valid api connector (hand-crafting the exact Connector
    # shape is error-prone; the corpus copy is the source of truth).
    return json.loads((CORPUS / "valid_connector.json").read_text())


def test_coverage_passes_when_map_covers_endpoints(tmp_path, connector_base, validator):
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    findings = validator.validate_document(connector_base, doc_path=tmp_path / "connector.json")
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_coverage_passes_with_lowercase_exact_matcher(tmp_path, connector_base, validator):
    # A lowercase `exact` native the runtime resolves fine must not be
    # reported as uncovered. The endpoint declares `varchar`; the runtime
    # normalizes both sides and matches, so coverage must too.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "varchar", "canonical": "Utf8"}],
                {"widgets.json": _endpoint("varchar", "Utf8")})
    findings = validator.validate_document(connector_base, doc_path=tmp_path / "connector.json")
    assert not _errors(findings), [e["message"] for e in _errors(findings)]


def test_coverage_flags_uncovered_native(tmp_path, connector_base, validator):
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
                {"widgets.json": _endpoint("BIGINT", "Int64")})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("no matching rule" in e["message"] for e in errors)


def test_coverage_flags_arrow_mismatch(tmp_path, connector_base, validator):
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Int64")})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("resolves to" in e["message"] and "Int64" in e["message"] for e in errors)


def test_coverage_flags_missing_read_map(tmp_path, connector_base, validator):
    (tmp_path / "connector.json").write_text(json.dumps(connector_base))
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("type-map-read.json" in e["message"] for e in errors)


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
                [{"match": "exact", "native": "JSONB", "canonical": "Json"}],
                {"widgets.json": _object_endpoint()})
    assert not _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))


def test_coverage_json_narrowing_is_narrow(tmp_path, connector_base, validator):
    # ...but `Json` does NOT satisfy a scalar like `Int64` (the allowance is narrow).
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "JSONB", "canonical": "Json"}],
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
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],  # no WEIRDTYPE rule
                {"widgets.json": ep})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("WEIRDTYPE" in e["message"] and "no matching rule" in e["message"] for e in errors)


def test_coverage_exact_match_normalizes_both_sides(validator):
    # Mirrors the runtime reader: an `exact` rule's `native` is
    # normalized the same way as the probe — trim, collapse internal whitespace
    # runs, uppercase — on BOTH sides. So a lowercase or extra-spaced matcher
    # covers the (normalized) endpoint native, exactly as the runtime resolves
    # it — the validator is no longer stricter than the runtime.
    assert validator._render_canonical("STRING", [{"match": "exact", "native": "string", "canonical": "Utf8"}]) == "Utf8"
    assert validator._render_canonical("STRING", [{"match": "exact", "native": "STRING", "canonical": "Utf8"}]) == "Utf8"
    # Whitespace: a two-space matcher covers a single-space native.
    assert validator._render_canonical("character varying", [{"match": "exact", "native": "CHARACTER  VARYING", "canonical": "Utf8"}]) == "Utf8"
    # A genuinely different native is still uncovered.
    assert validator._render_canonical("STRING", [{"match": "exact", "native": "BIGINT", "canonical": "Int64"}]) is None


def test_normalize_native_is_the_canonical(validator):
    # The validator imports the single source of truth — it does not
    # reimplement it — so coverage normalizes exactly as every reader does.
    from analitiq.contracts.type_map import normalize_native_type
    assert validator.connectors._normalize_native is normalize_native_type
    # strip → collapse internal whitespace runs → uppercase.
    assert normalize_native_type("  character  varying ") == "CHARACTER VARYING"
    assert normalize_native_type("varchar") == "VARCHAR"
    assert normalize_native_type("Timestamp\tWith Time  Zone") == "TIMESTAMP WITH TIME ZONE"


def test_canonical_eq_normalizes_separators_not_identifiers(validator):
    eq = validator._canonical_eq
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
                [{"match": "regex", "native": r"NUMERIC\((?<p>[1-9]|[12]\d|3[0-8]),\s*(?<s>\d|[12]\d|3[0-8])\)",
                  "canonical": "Decimal128(${p}, ${s})"}],
                {"widgets.json": _endpoint("NUMERIC(38,9)", "Decimal128(38, 9)")})
    assert not _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))


def test_coverage_flags_duplicate_endpoint_id(tmp_path, connector_base, validator):
    # Two endpoint files sharing an endpoint_id are flagged
    # as a duplicate (spec: endpoint_id unique within the connector release),
    # not only obliquely as a filename mismatch.
    ep = _endpoint("STRING", "Utf8", endpoint_id="dup", path="/dup")
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
                {"dup.json": ep, "other.json": ep})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any(e["validator"] == "endpoint-id-unique" and "dup" in e["message"] for e in errors)


def test_coverage_distinct_endpoint_ids_pass(tmp_path, connector_base, validator):
    # Two endpoints with distinct ids (matching filenames + paths) raise no error.
    a = _endpoint("STRING", "Utf8", endpoint_id="alpha", path="/alpha")
    b = _endpoint("STRING", "Utf8", endpoint_id="beta", path="/beta")
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
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
    # does NOT collide with the pure-param sibling (Codex P3).
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
    assert errs and errs[0]["validator"] == "endpoint-id-locator"
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
        assert findings[0]["validator"] == "endpoint-id-locator"
        assert "must equal" not in findings[0]["message"]      # no fabricated id
        assert "cannot derive" in findings[0]["message"]


def test_coverage_non_dict_endpoint_file_no_crash(tmp_path, connector_base, validator):
    # A JSON-array endpoint file is a recorded model error, NOT a generic
    # "validator bug" crash from the coverage walk calling .get() on a list (Codex P3).
    (tmp_path / "endpoints").mkdir(parents=True)
    (tmp_path / "connector.json").write_text(json.dumps(connector_base))
    (tmp_path / "type-map-read.json").write_text(
        json.dumps([{"match": "exact", "native": "STRING", "canonical": "Utf8"}]))
    (tmp_path / "endpoints" / "widgets.json").write_text("[]")  # array, not object
    errs = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert errs
    assert not any(GUARD_DEFAULT_BLAME in e["message"] for e in errs)


def test_coverage_flags_endpoint_id_locator_mismatch(tmp_path, connector_base, validator):
    # End-to-end: a model-valid endpoint whose id doesn't encode its versioned path
    # is gated (filename still matches the id; only the locator rule catches it).
    ep = _endpoint("STRING", "Utf8", endpoint_id="widgets", path="/v1/widgets")
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
                {"widgets.json": ep})
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any(e["validator"] == "endpoint-id-locator" for e in errors)


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
    assert any(e["validator"] == "endpoint-id-locator" and "public__orders" in e["message"]
               for e in errs)
    # Catalog + schemaless variants are gated the same way (derived id passes).
    assert not _errors(validator.validate_document(
        _db_endpoint(derive_db_endpoint_id("wh", "public", "orders"), schema="public", catalog="wh")))
    assert not _errors(validator.validate_document(
        _db_endpoint(derive_db_endpoint_id(None, None, "orders"), schema=None)))


# --- coverage matrix (check_coverage isolates file-behavior from model validity) ---

def _min_connector(kind: str):
    return {"kind": kind, "transports": {}}


def test_coverage_database_requires_write_map(tmp_path, validator):
    (tmp_path / "type-map-read.json").write_text('[{"match":"exact","native":"BIGINT","canonical":"Int64"}]')
    (tmp_path / "connector.json").write_text("{}")
    errors = _errors(validator.check_coverage(_min_connector("database"), tmp_path / "connector.json"))
    assert any("type-map-write.json" in e["message"] for e in errors)


def test_coverage_api_rejects_write_map(tmp_path, validator):
    (tmp_path / "type-map-read.json").write_text('[{"match":"exact","native":"STRING","canonical":"Utf8"}]')
    (tmp_path / "type-map-write.json").write_text('[{"match":"exact","canonical":"Utf8","native":"TEXT"}]')
    (tmp_path / "endpoints").mkdir()
    (tmp_path / "endpoints" / "w.json").write_text("{}")
    (tmp_path / "connector.json").write_text("{}")
    errors = _errors(validator.check_coverage(_min_connector("api"), tmp_path / "connector.json"))
    assert any("must not ship" in e["message"] for e in errors)


def test_coverage_flags_legacy_type_map(tmp_path, validator):
    (tmp_path / "type-map.json").write_text('[{"match":"exact","native":"X","canonical":"Utf8"}]')
    (tmp_path / "type-map-read.json").write_text('[{"match":"exact","native":"X","canonical":"Utf8"}]')
    (tmp_path / "endpoints").mkdir()
    (tmp_path / "connector.json").write_text("{}")
    errors = _errors(validator.check_coverage(_min_connector("api"), tmp_path / "connector.json"))
    assert any("pre-split name" in e["message"] for e in errors)


@pytest.mark.parametrize("kind", ["database", "nosql", "document"])
def test_coverage_database_family_requires_write_map(tmp_path, kind, validator):
    # nosql/document are database-family kinds — same read+write map requirement.
    (tmp_path / "type-map-read.json").write_text('[{"match":"exact","native":"BIGINT","canonical":"Int64"}]')
    (tmp_path / "connector.json").write_text("{}")
    errors = _errors(validator.check_coverage({"kind": kind, "transports": {}}, tmp_path / "connector.json"))
    assert any("type-map-write.json" in e["message"] for e in errors)


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
    assert not any(e["validator"] == "endpoint-filename" for e in errors)


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
    assert any(e["validator"] == "endpoint-filename" for e in errors)
    # Correctly named -> no filename error.
    right = ep_dir / f"{eid}.json"
    right.write_text(json.dumps(db))
    assert not any(e["validator"] == "endpoint-filename"
                   for e in _errors(validator.validate_document(db, doc_path=right)))


def test_database_endpoint_filename_not_checked_when_unanchored(validator, tmp_path):
    # A staged single-doc path not yet at its final `definition/endpoints/` home
    # carries no stem contract, so the gate stays silent.
    eid = derive_db_endpoint_id(None, "public", "orders")
    db = _db_endpoint(eid)
    p = tmp_path / "orders.json"  # bare staged file, wrong stem, no endpoints/ parent
    p.write_text(json.dumps(db))
    errors = _errors(validator.validate_document(db, doc_path=p))
    assert not any(e["validator"] == "endpoint-filename" for e in errors)


def test_endpoint_filename_findings_public_helper(validator):
    # The filename gate is exported so a bundle-assembling consumer —
    # which validates filename-less in-memory docs via validate_pipeline_bundle and
    # so cannot reach the gate there — calls ONE shared implementation instead of
    # reimplementing the ~4-line check, keeping the invariant define-once.
    eid = derive_db_endpoint_id(None, "public", "orders")
    db = _db_endpoint(eid)
    # Mismatched stem -> exactly one endpoint-filename error.
    mismatch = _errors(validator.endpoint_filename_findings(db, "orders.json"))
    assert [e["validator"] for e in mismatch] == ["endpoint-filename"]
    # Correct {endpoint_id}.json -> no findings.
    assert validator.endpoint_filename_findings(db, f"{eid}.json") == []
    # Missing/unusable endpoint_id -> a warning (can't verify), not an error.
    no_id = validator.endpoint_filename_findings({"database_object": {"name": "orders"}}, "orders.json")
    assert [(f["validator"], f["severity"]) for f in no_id] == [("endpoint-filename", "warning")]


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


def test_type_map_direction_from_schema_url(validator, tmp_path):
    # A write map from a generic filename is validated as write when --schema-url
    # points at type-map-write (backward-compatible direction hint).
    write_rules = [{"match": "regex", "canonical": r"^Decimal128\((?<p>\d+),(?<s>\d+)\)",
                    "native": "NUMERIC(${p}, ${s})"}]
    p = tmp_path / "generic.json"
    as_read = _errors(validator.validate_document(write_rules, doc_path=p))
    as_write = _errors(validator.validate_document(
        write_rules, doc_path=p, schema_url="https://schemas.analitiq.ai/type-map-write/latest.json"))
    assert as_read and not as_write  # rejected as read, accepted as write


def test_coverage_flags_nested_endpoint_file(tmp_path, connector_base, validator):
    # A nested endpoints/**/x.json must be flagged (matches the registry gate,
    # which rejects non-flat endpoint paths) rather than silently ignored.
    _write_tree(tmp_path, connector_base,
                [{"match": "exact", "native": "STRING", "canonical": "Utf8"}],
                {"widgets.json": _endpoint("STRING", "Utf8")})
    nested = tmp_path / "endpoints" / "v1"
    nested.mkdir()
    (nested / "buried.json").write_text(json.dumps(_endpoint("STRING", "Utf8")))
    errors = _errors(validator.validate_document(connector_base, doc_path=tmp_path / "connector.json"))
    assert any("nested" in e["message"] for e in errors)


def test_coverage_flags_unparseable_read_map(tmp_path, validator):
    (tmp_path / "type-map-read.json").write_text("{ not json")
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
    rules = [{"match": "exact", "native": "STRING", "canonical": "Utf8"},
             {"match": "exact", "native": "STRING", "canonical": "Utf8"}]
    warns = _warnings(validator.validate_document(rules))
    assert any("duplicate" in w["message"] for w in warns)


def test_duplicate_exact_read_rule_warns_across_case_and_whitespace(validator):
    # Two exact READ rules differing only by case/whitespace collapse to one
    # matcher at runtime (first wins), so the second is unreachable — the dedup
    # must normalize the same way the reader does and flag it, even when the
    # rules map to DIFFERENT canonicals (a real, if rare, authoring bug).
    rules = [{"match": "exact", "native": "character varying", "canonical": "Utf8"},
             {"match": "exact", "native": "CHARACTER  VARYING", "canonical": "LargeUtf8"}]
    warns = _warnings(validator.validate_document(rules))
    assert any("duplicate" in w["message"] for w in warns)


@pytest.mark.parametrize("native,warns", [
    # A named BACKREFERENCE contributes no literal text: `\\k<t>` must be
    # dropped whole, not unescaped into the literal `k<t>`.
    (r"^A(?<t>[0-9])B\k<t>$", False),
    # A character CLASS is a set, not a lowercase literal — its contents are
    # dropped too, which is why the backref strip is added to the class strip
    # rather than swapped for a strip that keeps class contents.
    (r"^FOO(?<x>[A-Za-z]+)$", False),
    # A genuine lowercase literal outside any class or capture: dead against
    # uppercased natives, and the whole reason the check exists.
    (r"^varchar\((?<n>\d+)\)$", True),
    # The blind spot both strips share, recorded rather than fixed: a
    # lowercase-ONLY class really is dead, and the class strip hides it.
    (r"^[a-z]+$", False),
])
def test_regex_lowercase_literal_warning_truth_table(validator, tmp_path, native, warns):
    findings = validator.validate_document(
        [{"match": "regex", "native": native, "canonical": "Utf8"}],
        doc_path=tmp_path / "type-map-read.json")
    dead = [w for w in _warnings(findings)
            if w["validator"] == "type-map-rule" and "can never match" in w["message"]]
    assert bool(dead) is warns, findings


def test_write_vocabulary_gap_warns(validator, tmp_path):
    # A write map missing whole canonical families → advisory warning.
    p = tmp_path / "type-map-write.json"
    findings = validator.validate_document([{"match": "exact", "canonical": "Utf8", "native": "TEXT"}],
                                           doc_path=p)
    assert any(w["validator"] == "type-map-write-coverage" for w in _warnings(findings))


def test_write_vocabulary_probes_bare_container_markers(validator, tmp_path):
    # The engine probes the write map with a destination column's `arrow_type`
    # verbatim, and API-sourced documents carry the bare `Object`/`List` shape
    # markers — a map without rules for them hard-errors the stream at
    # configuration. The coverage warning must name both.
    p = tmp_path / "type-map-write.json"
    findings = validator.validate_document([{"match": "exact", "canonical": "Utf8", "native": "TEXT"}],
                                           doc_path=p)
    # StopIteration here is the failure signal working, not a case to guard:
    # no coverage warning at all means the probe stopped running.
    gap = next(  # skipcq: PTC-W0063
        w for w in _warnings(findings) if w["validator"] == "type-map-write-coverage"
    )
    assert "'Object'" in gap["message"] and "'List'" in gap["message"]

    covered = [{"match": "exact", "canonical": "Utf8", "native": "TEXT"},
               {"match": "exact", "canonical": "Object", "native": "JSONB"},
               {"match": "exact", "canonical": "List", "native": "JSONB"}]
    findings = validator.validate_document(covered, doc_path=p)
    # Covering the two markers must narrow the warning, not silence it — the map
    # still lacks rules for other probes. So StopIteration here is the failure
    # signal working: it means the warning vanished entirely, which would make
    # the assertion below pass for the wrong reason.
    gap = next(  # skipcq: PTC-W0063
        w for w in _warnings(findings) if w["validator"] == "type-map-write-coverage"
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
        {"match": "exact", "canonical": c, "native": n}
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
        {"match": "regex", "canonical": r"^Decimal(128|256)\((?<p>\d+),\s*(?<s>\d+)\)$",
         "native": "NUMERIC(${p}, ${s})"},
        {"match": "regex", "canonical": r"^Time(32|64)\([A-Z]+\)$", "native": "TIME"},
        {"match": "regex", "canonical": r"^Timestamp\([A-Z]+\)$", "native": "TIMESTAMP"},
        {"match": "regex", "canonical": r"^Duration\([A-Z]+\)$", "native": "INTERVAL"},
    ]
    findings = validator.validate_document(full_map, doc_path=tmp_path / "type-map-write.json")
    coverage = [f for f in findings if f["validator"] == "type-map-write-coverage"]
    assert not coverage, coverage


# --- CLI / exit-code contract (the integration surface consumers depend on) ---

def test_cli_valid_doc_exit0(tmp_path):
    # Name the file after its endpoint_id so the filename↔id check is satisfied.
    doc = json.loads((CORPUS / "valid_read.json").read_text())
    r = run_cli(tmp_path, doc, filename=f"{doc['endpoint_id']}.json")
    assert r.returncode == 0, r.stdout
    out = json.loads(r.stdout)
    assert out["passed"] is True and isinstance(out["findings"], list)


def test_cli_invalid_doc_exit1(tmp_path):
    r = run_cli(tmp_path, json.loads((CORPUS / "invalid_write_from_input.json").read_text()))
    assert r.returncode == 1
    assert json.loads(r.stdout)["passed"] is False


def test_cli_unreadable_document_exit1(tmp_path):
    # A directory path: read raises IsADirectoryError → must still emit JSON + exit 1.
    r = run_cli_argv("--document", str(tmp_path))
    assert r.returncode == 1
    assert json.loads(r.stdout)["passed"] is False


def test_cli_missing_arg_exit2():
    r = run_cli_argv()
    assert r.returncode == 2
