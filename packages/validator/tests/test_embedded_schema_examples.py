"""`embedded-schema-example` — a recorded sample must satisfy the node declaring it.

Every other check over an endpoint compares one declaration with another, so a
node whose declared type contradicts what the provider actually sends passes all
of them. A value under `examples` is copied off the wire, which makes it the one
thing in the document those checks can be graded against; these tests drive that
grading through both entry points — a single endpoint document, and the
connector-anchored walk that labels findings with the endpoint's filename.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from conftest import CLI_DEADLINE_SECONDS

from analitiq.contracts.endpoints import WRITE_MODES
from analitiq.contracts.shared.json_schema import (
    JSON_SCHEMA_LIST_OF_SCHEMA_KEYS,
    JSON_SCHEMA_SINGLE_SCHEMA_KEYS,
    JSON_SCHEMA_SUBSCHEMA_KEYS,
)

API = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JS = "https://json-schema.org/draft/2020-12/schema"

CORPUS_CONNECTOR = "valid_connector.json"


def _read_endpoint(properties, endpoint_id="widgets", defs=None, dialect=JS):
    """A model-valid read endpoint whose response items carry `properties`.

    `defs` lands at the root of the embedded schema, because that is what a
    `$ref` of `#/$defs/<name>` names — the reference is rooted at the embedded
    document, not at the node carrying it."""
    schema = {"$schema": dialect, "type": "array",
              "items": {"type": "object", "properties": properties}}
    if defs is not None:
        schema["$defs"] = defs
    return {
        "$schema": API, "endpoint_id": endpoint_id,
        "operations": {"read": {
            "request": {"method": "GET", "path": "/widgets"}, "params": {},
            "response": {"records": {"ref": "response.body"}, "schema": schema},
            }}}


def _write_endpoint(properties, modes=("insert",), endpoint_id="widgets"):
    return {
        "$schema": API, "endpoint_id": endpoint_id,
        "operations": {"write": {
            mode: {
                "request": {"method": "POST", "path": "/widgets",
                            "body": {"r": {"from_input": "record"}}},
                "params": {},
                "input": {"schema": {"$schema": JS, "type": "object",
                                     "properties": properties}},
            } for mode in modes
        }}}


def _model_errors(doc):
    """Whether the api-endpoint contract model itself rejects `doc` — checked
    directly against the model rather than by filtering `validate_document`'s
    findings, since a model rejection carries no category of its own to filter
    on and a rule id only when a `rules.violation` raised it."""
    from pydantic import TypeAdapter, ValidationError
    from analitiq.contracts.endpoints import ApiEndpointDoc
    try:
        TypeAdapter(ApiEndpointDoc).validate_python(doc)
        return []
    except ValidationError as exc:
        return exc.errors()


def _sample_findings(findings):
    return [f for f in findings if f.get("rule") == "RULE-ENDP-063"]


def _errors(findings):
    return [f for f in findings if f["severity"] == "error"]


# The declaration/wire disagreement the check exists to catch: a provider that
# sends its flags as the strings "0" and "1" under a node declaring boolean.
STRING_FLAG = {"type": ["boolean", "null"], "native_type": "BOOLEAN",
               "arrow_type": "Boolean", "examples": ["0", "1"]}


def test_string_flag_under_a_boolean_node_errors(validator):
    doc = _read_endpoint({"paid": STRING_FLAG})
    findings = validator.validate_document(doc)
    errors = _sample_findings(findings)
    assert len(errors) == 2, findings
    assert all(e["severity"] == "error" for e in errors)
    assert [e["path"] for e in errors] == [
        "/operations/read/response/schema/items/properties/paid/examples/0",
        "/operations/read/response/schema/items/properties/paid/examples/1",
    ]
    assert "'0'" in errors[0]["message"]
    assert "is not of type" in errors[0]["message"]


@pytest.mark.parametrize("mode", WRITE_MODES)
def test_a_write_input_node_is_graded_in_every_mode(mode, validator):
    """Driven from the contract's own mode vocabulary, so a mode it gains is
    graded here or this test goes red.

    A mode requiring a companion key says so by rejecting the document; which
    mode that is belongs to the model, not to this file. The document must end
    up model-clean, or the grading below would be reading a document the
    contract already refused.
    """
    doc = _write_endpoint({"paid": STRING_FLAG}, modes=(mode,))
    if _model_errors(doc):
        doc["operations"]["write"][mode]["conflict_keys"] = ["paid"]
    assert not _model_errors(doc), _model_errors(doc)
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 2, errors
    assert all(e["path"].startswith(f"/operations/write/{mode}/input/schema") for e in errors)


def test_every_write_mode_of_one_endpoint_is_graded(validator):
    doc = _write_endpoint({"paid": STRING_FLAG}, modes=("insert", "truncate_insert"))
    errors = _sample_findings(validator.validate_document(doc))
    assert {e["path"].split("/")[3] for e in errors} == {"insert", "truncate_insert"}


def test_a_satisfied_sample_produces_nothing(validator):
    doc = _read_endpoint({"paid": {"type": ["boolean", "null"], "examples": [True, None]}})
    findings = validator.validate_document(doc)
    assert not _errors(findings), findings   # clean for the right reason
    assert not _sample_findings(findings)


def test_a_node_with_no_samples_is_graded_on_nothing(validator):
    """Samples stay optional at every depth — silence is never disagreement."""
    doc = _read_endpoint({
        "paid": {"type": "boolean"},
        "nested": {"type": "object", "properties": {"deep": {"type": "integer"}}},
    })
    findings = validator.validate_document(doc)
    assert not _errors(findings), findings
    assert not _sample_findings(findings)


def test_an_empty_samples_list_produces_nothing(validator):
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": []}})
    findings = validator.validate_document(doc)
    assert not _errors(findings), findings
    assert not _sample_findings(findings)


def test_each_entry_is_graded_and_located_separately(validator):
    """The satisfied entries are silent and each contradicting one is reported
    at its own index — a finding naming only the node would not say which."""
    doc = _read_endpoint({"n": {"type": "integer", "examples": [1, "two", 3, "four"]}})
    errors = _sample_findings(validator.validate_document(doc))
    assert [e["path"].rsplit("/", 1)[-1] for e in errors] == ["1", "3"]


def test_a_node_reached_through_defs_and_composition_is_graded(validator):
    """Grading descends where the contract's own walkers descend."""
    doc = _read_endpoint(
        {"paid": {"$ref": "#/$defs/flag"},
         "meta": {"allOf": [{"type": "object", "properties": {
             "n": {"type": "integer", "examples": ["nope"]}}}]}},
        defs={"flag": {"type": "boolean", "examples": ["0"]}},
    )
    paths = {e["path"] for e in _sample_findings(validator.validate_document(doc))}
    assert paths == {
        "/operations/read/response/schema/$defs/flag/examples/0",
        "/operations/read/response/schema/items/properties/meta/allOf/0/properties/n/examples/0",
    }


def _at_position(key, node):
    """`node` placed at recursion position `key`, with the pointer segment that
    position implies. Shaped by which bucket the contract puts the key in, never
    by a list of key names written here."""
    if key in JSON_SCHEMA_SUBSCHEMA_KEYS:
        return {key: {"x": node}}, f"{key}/x"
    if key in JSON_SCHEMA_LIST_OF_SCHEMA_KEYS:
        return {key: [node]}, f"{key}/0"
    return {key: node}, key


@pytest.mark.parametrize("key", sorted(
    JSON_SCHEMA_SUBSCHEMA_KEYS | JSON_SCHEMA_LIST_OF_SCHEMA_KEYS | JSON_SCHEMA_SINGLE_SCHEMA_KEYS))
def test_every_recursion_position_the_contract_declares_is_graded(key, validator):
    """Every position the contract can hold a sub-schema in, graded.

    The keyword inventory is the contract's, and so is the descent over it:
    `TestOneStructuralWalk` in
    `packages/contract-models/tests/unit/test_embedded_schema_refs.py` holds
    every consumer to the positions that one walk yields. What it cannot see is
    whether GRADING reaches a position the walk does reach — a check that
    visits a node and looks at nothing there satisfies it. So the positions
    here are driven from the imported sets: a keyword the contract adds is
    graded here or this test goes red.

    `propertyNames` grades property names, which are strings — hence a
    string-typed node, so every position carries a contradiction of its own kind
    rather than one that only some positions could express.
    """
    node = ({"type": "string", "examples": [1]} if key == "propertyNames"
            else {"type": "integer", "examples": ["nope"]})
    placed, segment = _at_position(key, node)
    doc = _read_endpoint({"f": {"type": "object", **placed}})
    errors = _sample_findings(validator.validate_document(doc))
    assert [e["path"] for e in errors] == [
        f"/operations/read/response/schema/items/properties/f/{segment}/examples/0"
    ], errors


def test_a_name_carrying_pointer_syntax_is_escaped(validator):
    """The finding's `path` is a JSON Pointer a consumer resolves, and the names
    in it are the provider's. A raw `/` reads as another segment and a raw `~`
    opens an escape, so an unescaped name locates the wrong node or none.

    Asserted by resolving the pointer back to the node it names, rather than by
    comparing it to an escaped string written here — a wrong pointer written the
    same way in both places would agree with itself.
    """
    doc = _read_endpoint({"a/b": {"type": "integer", "examples": ["nope"]},
                          "c~d": {"type": "integer", "examples": ["nope"]}})
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 2, errors
    for err in errors:
        node = doc
        for raw in err["path"].split("/")[1:]:
            segment = raw.replace("~1", "/").replace("~0", "~")
            node = node[int(segment)] if isinstance(node, list) else node[segment]
        assert node == "nope", err["path"]


def test_data_shaped_like_a_schema_is_not_walked(validator):
    """A payload under `default`, `const`, `enum` or another `examples` is data.
    Each carries an object that would be a contradicting node if walked."""
    contradicted = {"type": "integer", "examples": ["not an integer"]}
    doc = _read_endpoint({
        "a": {"type": "object", "default": {"properties": {"x": contradicted}}},
        "b": {"type": "object", "const": {"properties": {"x": contradicted}}},
        "c": {"type": "object", "enum": [{"properties": {"x": contradicted}}]},
        "d": {"type": "object", "examples": [{"properties": {"x": contradicted}}]},
    })
    findings = validator.validate_document(doc)
    assert not _errors(findings), findings
    assert not _sample_findings(findings)


def test_a_schema_the_meta_check_rejects_is_not_graded(validator):
    """In a document that is not a valid schema, a misspelled keyword is simply
    not applied — grading would report the sample for the author's typo."""
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0"]}})
    doc["operations"]["read"]["response"]["schema"]["items"]["required"] = "paid"
    findings = validator.validate_document(doc)
    assert not _sample_findings(findings)
    assert [f.get("rule") for f in _errors(findings)] == ["RULE-ENDP-048"]


def test_an_ungradeable_sample_is_reported_and_costs_only_itself(validator):
    """`multipleOf` against an oversized number raises out of the keyword, and a
    `$ref` resolving to nothing raises before any keyword runs. Both are
    reported, and neither costs the remaining entries their verdict."""
    doc = _read_endpoint({
        "big": {"type": "number", "multipleOf": 0.5, "examples": [int("1" + "0" * 400)]},
        "gone": {"$ref": "#/$defs/missing", "examples": [1]},
        "paid": {"type": "boolean", "examples": ["0"]},
        # A defect this check does not own, to show the crash costs it nothing.
        "other": {"native_type": "STRING", "arrow_type": "NotAnArrowType"},
    })
    findings = validator.validate_document(doc)
    errors = _sample_findings(findings)
    by_path = {e["path"].split("/properties/")[1]: e["message"] for e in errors}
    assert set(by_path) == {"big/examples/0", "gone/examples/0", "paid/examples/0"}
    assert "OverflowError" in by_path["big/examples/0"]
    assert "could not be resolved" in by_path["gone/examples/0"]
    assert "not in the sample" in by_path["gone/examples/0"]
    # The reference itself, not the resource it was searched for in.
    assert "'/$defs/missing'" in by_path["gone/examples/0"]
    # ...while the oversized sample is named as the sample's own defect.
    assert "could not grade" in by_path["big/examples/0"]
    # An oversized sample is bounded into the message rather than pasted whole.
    assert "0" * 200 not in by_path["big/examples/0"]
    assert "..." in by_path["big/examples/0"]
    assert [f for f in findings if f.get("rule") is None and f["kind"] == "fail"], findings


def test_a_crash_in_the_check_costs_no_other_check(validator, monkeypatch):
    """The check-level guard. Any failure the per-entry guard does not reach —
    the walk itself — must not take the endpoint's other findings with it."""
    from analitiq.validator import connectors

    def boom(*_args, **_kwargs):
        raise RuntimeError("walk exploded")

    monkeypatch.setattr(connectors, "_walk_schema_nodes", boom)
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0"]}})
    # An unrelated defect on a schema this check never reaches: a readable read
    # schema is what makes the walk run at all, so the defect goes on a write
    # input instead.
    doc["operations"]["write"] = {"insert": {
        "request": {"method": "POST", "path": "/widgets",
                    "body": {"r": {"from_input": "record"}}},
        "params": {},
        "input": {"schema": {"$schema": JS, "type": "object", "required": "paid"}},
    }}
    findings = validator.validate_document(doc)
    assert "RULE-ENDP-048" in {f.get("rule") for f in findings}
    crashes = [f for f in findings if f.get("message_id") == "check-crashed"]
    assert len(crashes) == 1 and "crashed unexpectedly" in crashes[0]["message"]
    # The crashed check is bound to exactly one rule — the crash finding
    # stays routable to it rather than losing attribution to the crash.
    assert crashes[0]["rule"] == "RULE-ENDP-063"


def test_a_node_asserting_nothing_is_graded_against_nothing(validator):
    """The boundary of what a sample settles, made executable.

    Grading is against the node's JSON Schema assertions, and the contract's
    `native_type`/`arrow_type` pair is not one of them — a node carrying only
    that pair asserts nothing a value can fail, so the check is silent. Deciding
    it would take a JSON-kind to Arrow-family table: cast semantics this repo
    does not own and could not pin.

    An absent `type` is not the boundary, though — the companion case below is
    what keeps this test from being read as one.
    """
    doc = _read_endpoint({"paid": {
        "native_type": "BOOLEAN", "arrow_type": "Boolean", "examples": ["0"]}})
    assert not _sample_findings(validator.validate_document(doc))


def test_a_node_asserting_without_a_type_is_still_graded(validator):
    """`type` is one assertion among many. A node constraining a value by
    `const`, `enum` or a bound states something a sample can contradict, and is
    graded on it."""
    doc = _read_endpoint({
        "country": {"const": "US", "examples": ["CA"]},
        "status": {"enum": ["open", "closed"], "examples": ["paid"]},
        "size": {"maximum": 10, "examples": [11]},
    })
    errors = _sample_findings(validator.validate_document(doc))
    assert {e["path"].split("/properties/")[1] for e in errors} == {
        "country/examples/0", "status/examples/0", "size/examples/0"}


def test_a_schema_declaring_another_draft_is_not_graded(validator):
    """A document declaring a draft the contract does not read is reported by
    the meta-check and skipped here. Grading it under 2020-12 would report
    keywords that were never going to apply to it."""
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0"]}},
                         dialect="http://json-schema.org/draft-07/schema#")
    findings = validator.validate_document(doc)
    assert not _sample_findings(findings)
    assert [f.get("rule") for f in _errors(findings)] == ["RULE-ENDP-048"]


def test_a_remote_ref_is_refused_without_reaching_the_network(validator):
    """Validation is offline by contract, and grading must not be the exception.

    Left to its default reference registry, the grader FETCHES an `http(s)`
    `$ref` — an authored endpoint would make the validator issue a request to
    an address its author chose and wait for the answer. The host here is
    TEST-NET-1, which is unroutable, so a fetch would stall until it timed out.

    The VERDICT is what separates the two, not the elapsed time: a refusal names
    the reference, while a fetch that stalled would be abandoned by the sample
    budget and say so. Time cannot separate them, because the budget bounds both.
    """
    doc = _read_endpoint({"x": {"$ref": "http://192.0.2.1/nothing.json",
                                "examples": [1]}})
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 1, errors
    assert "could not be resolved" in errors[0]["message"]
    assert "budget" not in errors[0]["message"], "grading stalled instead of refusing"


# Every way 2020-12 lets a reference name something inside its own document.
# Refusing retrieval must refuse none of them — a registry holding the document
# under one base could resolve the plain pointer and lose the rest, and the
# symptom would be a finding blaming the author's sample for it.
IN_DOCUMENT_REFS = {
    "json pointer": ({"flag": {"type": "boolean"}}, "#/$defs/flag", {}),
    "under a root $id": ({"flag": {"type": "boolean"}}, "#/$defs/flag",
                         {"$id": "https://ref.example.test/schema"}),
    "by a nested $id": ({"flag": {"$id": "https://ref.example.test/flag",
                                  "type": "boolean"}},
                        "https://ref.example.test/flag", {}),
    "by $anchor": ({"flag": {"$anchor": "flag", "type": "boolean"}}, "#flag", {}),
}


@pytest.mark.parametrize("shape", sorted(IN_DOCUMENT_REFS), ids=list(sorted(IN_DOCUMENT_REFS)))
def test_an_in_document_ref_still_resolves_under_the_offline_registry(shape, validator):
    """The other half of refusing retrieval: the references the contract allows
    must all still resolve, and be graded on what they point at."""
    defs, ref, extra = IN_DOCUMENT_REFS[shape]
    doc = _read_endpoint({"paid": {"$ref": ref, "examples": ["0"]}}, defs=defs)
    doc["operations"]["read"]["response"]["schema"].update(extra)
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 1, errors
    assert errors[0]["path"] == (
        "/operations/read/response/schema/items/properties/paid/examples/0")
    # The keyword verdict, not merely a finding: an UNresolved reference also
    # produces exactly one finding at this path, so asserting the path alone
    # cannot tell a resolved reference from a broken one.
    assert "is not of type 'boolean'" in errors[0]["message"]
    assert "could not be resolved" not in errors[0]["message"]


def test_an_unresolved_reference_does_not_paste_the_schema_into_the_finding(validator):
    """An unresolved-reference error renders the whole resource it searched, so
    interpolating it would put the embedded schema into the message once per
    recorded sample.

    A length bound would be a number to keep updating; that the message does not
    GROW with the document is the property, so the same defect is reported in a
    small schema and a large one and the two messages are compared.
    """
    def message(field_count):
        fields = {f"filler_{i}": {"type": "string"} for i in range(field_count)}
        fields["gone"] = {"$ref": "#/$defs/missing", "examples": [1]}
        doc = _read_endpoint(fields)
        errors = _sample_findings(validator.validate_document(doc))
        assert len(errors) == 1, errors
        return errors[0]["message"]

    small, large = message(1), message(60)
    assert small == large, "the finding grew with the document it was found in"
    assert "filler_" not in large


def test_a_reference_ring_costs_only_the_entry_that_walks_into_it(validator):
    """Refusing a ring is a question about references, not about samples, and is
    not this check's to answer. Surviving one is: grading is the first thing here
    that resolves a reference, so a ring reaches it before anything else, and the
    entries after it must still get their verdict."""
    doc = _read_endpoint(
        {"ring": {"$ref": "#/$defs/a", "examples": [1]},
         "after": {"type": "boolean", "examples": ["0"]}},
        defs={"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}},
    )
    errors = _sample_findings(validator.validate_document(doc))
    by_field = {e["path"].split("/properties/")[1]: e["message"] for e in errors}
    assert set(by_field) == {"ring/examples/0", "after/examples/0"}
    assert "could not be resolved" in by_field["ring/examples/0"]
    assert "ran out of stack" in by_field["ring/examples/0"]
    assert "is not of type 'boolean'" in by_field["after/examples/0"]


@pytest.mark.parametrize("node,label", [
    ({"type": "integer", "examples": ["x" * 10_000]}, "an oversized instance"),
    ({"enum": [f"value_{i}" for i in range(300)], "examples": ["nope"]}, "a long enum"),
])
def test_a_finding_does_not_grow_with_what_the_keyword_echoes(node, label, validator):
    """Bounding the sample is half of it.

    `jsonschema` renders the failing instance AND the failing keyword's own value
    into its message, so the same unbounded text arrives by the other route — and
    once per recorded entry. Both routes are bounded, so a finding stays a
    finding rather than a copy of the document that produced it.
    """
    doc = _read_endpoint({"p": node})
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 1, errors
    assert len(errors[0]["message"]) < 1000, f"{label}: {len(errors[0]['message'])} chars"


@pytest.mark.parametrize("node", [
    {"$ref": "#/" + "x" * 10_000, "examples": [1]},
    {"additionalProperties": {"type": "integer"}, "examples": [{"k" * 10_000: "no"}]},
], ids=["an unresolved reference", "a path inside the sample"])
def test_no_authored_string_defeats_the_finding_size_bound(node, validator):
    """Every route by which authored text reaches a message is bounded — the
    sample, the keyword's echo, the reference, and the path within the sample.
    An unbounded one is enough on its own to make a finding larger than the
    document it was found in."""
    errors = _sample_findings(validator.validate_document(_read_endpoint({"a": node})))
    assert len(errors) == 1, errors
    assert len(errors[0]["message"]) < 1000, len(errors[0]["message"])


@pytest.mark.parametrize("node", [
    {"type": "integer", "examples": ["no"]},
    {"$ref": "#/$defs/missing", "examples": [1]},
], ids=["a rejected sample", "an unresolved reference"])
def test_an_oversized_property_name_does_not_defeat_the_bound(node, validator):
    """A property name is authored too, and the pointer carrying it appears in
    the message twice. The finding's `path` keeps the exact pointer, because a
    consumer resolves it; the message does not."""
    doc = _read_endpoint({"k" * 10_000: node})
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 1, errors
    assert len(errors[0]["message"]) < 1000, len(errors[0]["message"])
    # ...and `path` is still the exact pointer a consumer can resolve.
    assert errors[0]["path"].count("k" * 10_000) == 1


def test_the_connector_walk_labels_findings_with_the_endpoint_filename(tmp_path, validator):
    """The other entry point: a connector package, where a finding must name the
    endpoint file it came from."""
    from pathlib import Path

    corpus = Path(__file__).resolve().parent / "corpus" / CORPUS_CONNECTOR
    connector = json.loads(corpus.read_text())
    (tmp_path / "endpoints").mkdir(parents=True)
    (tmp_path / "connector.json").write_text(json.dumps(connector))
    (tmp_path / "type-map-read.json").write_text(json.dumps(
        [{"match": "exact", "native_type": "BOOLEAN", "arrow_type": "Boolean"}]))
    (tmp_path / "endpoints" / "widgets.json").write_text(
        json.dumps(_read_endpoint({"paid": STRING_FLAG})))

    findings = validator.validate_document(
        connector, doc_path=tmp_path / "connector.json")
    errors = _sample_findings(findings)
    assert len(errors) == 2, findings
    assert all("widgets.json/operations/read/" in e["message"] for e in errors)


# ---------------------------------------------------------------------------
# The evaluation budget
# ---------------------------------------------------------------------------
#
# Every guard around grading is an `except` clause, so between them they contain
# every way an evaluation raises and none of the ways it does not come back.
# `pattern` is evaluated by Python's `re`, which backtracks exponentially on an
# ambiguous pattern paired with a subject that nearly matches; the pair below
# does not finish in any time a caller will wait for.
#
# So what these tests assert is that grading RETURNS, and they obtain the
# findings from a child process to do it: a regression here does not produce a
# wrong answer, it produces no answer, and a direct call would hang the suite
# instead of failing it.

_RUNAWAY_PATTERN = "^(a+)+$"
_NEAR_MISS = "a" * 32 + "X"
_RUNAWAY_NODE = {"type": "string", "pattern": _RUNAWAY_PATTERN,
                 "examples": [_NEAR_MISS]}

def _sample_findings_via_cli(validator_cli, doc, filename="doc.json"):
    """This document's `embedded-schema-example` findings, from a child process.

    A regression in the bound does not make this check answer wrongly, it makes it
    not answer, so a direct call would hang the suite where the CLI fixture fails
    it. The deadline and the child's import path are the fixture's.
    """
    result = validator_cli.on_document(doc, filename)
    assert result.returncode in (0, 1), result.stderr
    return _sample_findings(json.loads(result.stdout)["findings"])


def test_a_sample_past_the_budget_is_reported_and_costs_only_itself(validator_cli):
    """The shape every other ungradeable entry already has: the sample is named,
    the verdict says nothing was decided, and the entries beside it still get
    theirs."""
    doc = _read_endpoint({"before": {"type": "integer", "examples": ["one"]},
                          "code": dict(_RUNAWAY_NODE),
                          "paid": {"type": "boolean", "examples": ["0"]}})
    errors = _sample_findings_via_cli(validator_cli, doc)
    by_field = {e["path"].split("/properties/")[1]: e["message"] for e in errors}
    assert set(by_field) == {"before/examples/0", "code/examples/0", "paid/examples/0"}
    # Graded on the worker generation the breach then killed.
    assert "is not of type 'integer'" in by_field["before/examples/0"]
    assert "was not graded" in by_field["code/examples/0"]
    assert "budget" in by_field["code/examples/0"]
    assert "is not of type 'boolean'" in by_field["paid/examples/0"]
    assert all(e["kind"] in ("fail", "notApplicable") for e in errors), errors


@pytest.mark.parametrize("node", [
    {"patternProperties": {_RUNAWAY_PATTERN: {"type": "integer"}},
     "examples": [{_NEAR_MISS: 1}]},
    {"propertyNames": {"pattern": _RUNAWAY_PATTERN}, "examples": [{_NEAR_MISS: 1}]},
], ids=["patternProperties", "propertyNames"])
def test_the_budget_bounds_every_keyword_not_only_pattern(node, validator_cli):
    """`pattern` is the reachable instance, not the class. Both keywords below
    apply the author's regex to the sample's KEYS, and a bound that reached only
    the keyword named in the report would leave them running."""
    errors = _sample_findings_via_cli(validator_cli, _read_endpoint({"bag": node}))
    assert len(errors) == 1, errors
    assert "was not graded" in errors[0]["message"]


def test_the_connector_walk_bounds_a_pathological_sample(tmp_path, validator_cli):
    """The second entry point. It reaches the same grading through a different
    caller, so a bound applied at the single-document call site would leave a
    connector package unprotected."""
    from pathlib import Path

    corpus = Path(__file__).resolve().parent / "corpus" / CORPUS_CONNECTOR
    connector = json.loads(corpus.read_text())
    (tmp_path / "endpoints").mkdir(parents=True)
    (tmp_path / "connector.json").write_text(json.dumps(connector))
    (tmp_path / "type-map-read.json").write_text(json.dumps(
        [{"match": "exact", "native_type": "BOOLEAN", "arrow_type": "Boolean"}]))
    (tmp_path / "endpoints" / "widgets.json").write_text(
        json.dumps(_read_endpoint({"code": dict(_RUNAWAY_NODE)})))

    errors = _sample_findings_via_cli(validator_cli, connector, "connector.json")
    assert len(errors) == 1, errors
    assert "widgets.json/operations/read/" in errors[0]["message"]
    assert "was not graded" in errors[0]["message"]


def test_a_document_with_no_samples_starts_no_worker(validator, monkeypatch):
    """The cost of the bound is paid where the risk is. A document recording
    nothing has no value to grade, so it must not pay for a grading process."""
    from analitiq.validator import _sample_budget

    def refuse(*_args, **_kwargs):
        raise AssertionError("a document with no samples started a grading worker")

    monkeypatch.setattr(_sample_budget.subprocess, "Popen", refuse)
    doc = _read_endpoint({"paid": {"type": "boolean"}})
    assert not _sample_findings(validator.validate_document(doc))


# ---------------------------------------------------------------------------
# Everything that is not a verdict
# ---------------------------------------------------------------------------
#
# Grading in another process adds ways for a sample to come back ungraded that
# an in-process call did not have: a budget spent, a worker that cannot start,
# one that dies, one that answers something that is not a verdict, a value that
# cannot be encoded. Each is a way for the check to decide nothing, and the
# whole point of bounding the evaluation was that deciding nothing must never
# read as a pass. So each is graded here for the same three properties — the
# sample is named, the severity is `error`, and the samples around it keep their
# verdicts.

#: A worker whose behaviour the test chooses, so the parent's handling of a
#: worker that misbehaves can be graded without waiting for one that does.
_FAKE_WORKER = '''\
import json, os, sys

mode = os.environ["FAKE_GRADING_WORKER"]
if mode == "nostart":
    sys.stderr.write("the fake worker refused to start\\n")
    raise SystemExit(1)
marker = os.environ["FAKE_GRADING_WORKER_MARKER"]
with open(marker + ".gen", "a") as fh:
    fh.write("x")
generation = os.path.getsize(marker + ".gen")
if generation > 1:
    import time
    if mode == "stall-on-restart":
        time.sleep(3600)
    if mode == "slow-restart":
        time.sleep(1.2)
for line in sys.stdin:
    job = json.loads(line)
    if job["op"] == "ping":
        sys.stdout.write('{"v": "pong"}\\n')
        sys.stdout.flush()
        continue
    if job["op"] != "grade":
        continue
    if mode == "die-once" and not os.path.exists(marker):
        open(marker, "w").close()
        raise SystemExit(1)
    if mode in ("stall-on-restart", "slow-restart"):
        raise SystemExit(1)
    if mode == "garbage":
        sys.stdout.write("this is not a verdict\\n")
    elif mode == "alien":
        sys.stdout.write('{"v": "a kind from the future"}\\n')
    else:
        sys.stdout.write('{"v": "graded", "error": null}\\n')
    sys.stdout.flush()
'''


@pytest.fixture
def fake_worker(monkeypatch, tmp_path):
    """Point the grader at `_FAKE_WORKER`, in the mode the test asks for."""
    from analitiq.validator import _sample_budget

    script = tmp_path / "fake_grading_worker.py"
    script.write_text(_FAKE_WORKER)
    monkeypatch.setattr(_sample_budget, "_WORKER_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_GRADING_WORKER_MARKER", str(tmp_path / "died"))

    def use(mode):
        monkeypatch.setenv("FAKE_GRADING_WORKER", mode)

    return use


def test_a_spent_document_budget_reports_rather_than_passes(validator, monkeypatch):
    """A spent budget is not a verdict.

    Once the document's budget is gone the remaining samples are never attempted,
    and each must still be an error naming itself. Reporting nothing would let a
    document whose samples were never graded through the gate that grading exists
    to be — which is worse than the stall, because it is silent."""
    from analitiq.validator import connectors
    from analitiq.validator._sample_budget import BudgetedGrader

    monkeypatch.setattr(connectors, "BudgetedGrader",
                        lambda: BudgetedGrader(document_budget=0.0))
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0"]},
                          "n": {"type": "integer", "examples": ["two"]}})
    errors = _sample_findings(validator.validate_document(doc))
    assert {e["path"].split("/properties/")[1] for e in errors} == {
        "paid/examples/0", "n/examples/0"}
    assert all(e["kind"] == "notApplicable" for e in errors), errors
    assert all("was not graded" in e["message"] for e in errors), errors
    assert all("budget was already spent" in e["message"] for e in errors), errors


def test_a_worker_that_cannot_start_is_reported_per_sample_and_attempted_once(
        validator, monkeypatch):
    """An environment that cannot host a worker is stated, once.

    Every sample still earns a finding — nothing was decided about any of them —
    but the environment is asked once per document rather than once per sample,
    because the answer cannot change between two samples."""
    from analitiq.validator import _sample_budget

    attempts = []

    def refuse(*args, **_kwargs):
        attempts.append(args)
        raise OSError("cannot allocate memory")

    monkeypatch.setattr(_sample_budget.subprocess, "Popen", refuse)
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0", "1"]}})
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 2, errors
    assert all(e["kind"] == "notApplicable" for e in errors), errors
    assert all("cannot allocate memory" in e["message"] for e in errors), errors
    assert len(attempts) == 1, attempts


def test_a_worker_lost_mid_document_costs_only_the_sample_that_lost_it(
        validator, fake_worker):
    """A worker one sample killed says nothing about the samples after it.

    Giving up for the document would report, for every later sample, a failure it
    never had — the coarse-grained version of the stall this check was bounded to
    prevent. Only a worker that cannot START is a verdict about the environment."""
    fake_worker("die-once")
    doc = _read_endpoint({"first": {"type": "boolean", "examples": ["0"]},
                          "second": {"type": "boolean", "examples": ["1"]},
                          "third": {"type": "boolean", "examples": ["2"]}})
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 1, errors
    assert errors[0]["path"].endswith("/first/examples/0"), errors
    assert "was not graded" in errors[0]["message"]
    assert "the grading worker was lost" in errors[0]["message"]


@pytest.mark.parametrize("mode,expected", [
    ("garbage", "'this is not a verdict'"),
    ("alien", "a kind from the future"),
    ("nostart", "the fake worker refused to start"),
], ids=["a line that is not JSON", "a kind this build does not know",
        "a worker that never starts"])
def test_a_reply_that_is_not_a_verdict_is_refused_and_quoted(
        mode, expected, validator, fake_worker):
    """What came back is quoted, because it is the only evidence of what happened.

    None of these may be read as a verdict, and none may escape as an exception:
    this check runs inside the per-endpoint guard, so a raise here would replace
    every finding the document had earned with one generic validator-bug notice."""
    fake_worker(mode)
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0"]}})
    findings = validator.validate_document(doc)
    errors = _sample_findings(findings)
    assert len(errors) == 1, findings
    assert errors[0]["kind"] == "notApplicable"
    assert "was not graded" in errors[0]["message"]
    assert expected in errors[0]["message"], errors[0]["message"]
    assert "crashed unexpectedly" not in errors[0]["message"]


def test_a_document_that_is_not_json_reports_every_sample_rather_than_crashing(validator):
    """A recorded sample is a value read out of a JSON document, so handing it to
    another process costs nothing — but a caller that built the document in Python
    can hold a value no JSON document could.

    A sample sits inside the schema that records it, so such a value stops every
    sample under that schema, not just its own. What matters is that each says so:
    before the samples were named, this raised, and the guard around the check
    replaced every finding the endpoint had earned with one generic validator-bug
    notice."""
    from decimal import Decimal

    doc = _read_endpoint({"amount": {"type": "integer", "examples": [Decimal("1.5")]},
                          "paid": {"type": "boolean", "examples": ["0"]}})
    findings = validator.validate_document(doc)
    by_field = {e["path"].split("/properties/")[1]: e["message"]
                for e in _sample_findings(findings)}
    assert set(by_field) == {"amount/examples/0", "paid/examples/0"}
    assert all("was not graded" in m for m in by_field.values()), by_field
    assert all("not JSON data" in m for m in by_field.values()), by_field
    assert not [f for f in findings if "crashed unexpectedly" in f["message"]], findings


def test_the_worker_writes_only_verdicts_to_its_stdout():
    """The worker's stdout IS the protocol.

    Anything else in its process that writes there lands mid-verdict, and the
    parent then reads a reply that is not one for every remaining sample — the
    check turning itself off, one finding at a time, everywhere at once."""
    from analitiq.validator import _sample_budget

    proc = subprocess.run(
        [sys.executable, _sample_budget._WORKER_SCRIPT],
        input='{"op": "ping"}\n', capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)},
        check=False)
    assert proc.stdout == '{"v": "pong"}\n', proc.stdout


def test_a_budget_breach_leaves_no_traceback_on_stderr(validator_cli):
    """A breach is an ordinary outcome, so it must not look like a crash.

    The worker is killed with its pipes still buffered; left to finalization those
    raise, and the interpreter prints the traceback on the validator's own stderr
    — which is exactly where a caller looks when the JSON report is missing."""
    proc = validator_cli.on_document(_read_endpoint({"code": dict(_RUNAWAY_NODE)}))
    assert proc.returncode in (0, 1), proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "BrokenPipeError" not in proc.stderr, proc.stderr


@pytest.mark.parametrize("node,sample", [
    ({"type": "array", "items": {"type": "integer"}}, (1, 2)),
    ({"type": "object", "required": ["1"]}, {1: "x"}),
], ids=["a tuple written as an array", "a key written as a string"])
def test_a_value_json_would_normalise_is_reported_rather_than_converted(
        node, sample, validator):
    """Encoding succeeding is not proof the round trip was lossless.

    `json.dumps` normalises: a tuple is written as an array, a non-string mapping
    key as a string. Both samples below are rejected by the node that records them
    and are accepted once normalised, so trusting the encoder would turn a
    rejection into a silent pass — the one outcome bounding the evaluation exists
    to make impossible."""
    doc = _read_endpoint({"a": dict(node, examples=[sample])})
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 1, errors
    assert "was not graded" in errors[0]["message"]
    assert "not JSON data" in errors[0]["message"], errors[0]["message"]


def test_a_worker_whose_diagnostic_file_cannot_be_opened_is_reported(
        validator, monkeypatch):
    """Opening the file the worker's stderr goes to is part of starting one.

    An exhausted descriptor table or an unwritable temp directory is a reason the
    environment cannot host a worker, and it arrives before the spawn does. Left
    outside, it costs the endpoint every finding it had earned and reports a
    validator bug instead of the samples it could not grade."""
    from analitiq.validator import _sample_budget

    def refuse(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(_sample_budget.tempfile, "TemporaryFile", refuse)
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0", "1"]}})
    findings = validator.validate_document(doc)
    errors = _sample_findings(findings)
    assert len(errors) == 2, findings
    assert all("no space left on device" in e["message"] for e in errors), errors
    assert not [f for f in findings if "crashed unexpectedly" in f["message"]], findings


def test_a_worker_ends_itself_when_its_deadline_passes():
    """The parent kills a worker that overruns, but only while the parent is alive.

    Killed itself mid-evaluation — an outer timeout, a cancelled request — it would
    leave a child inside a match that never returns, holding a core for as long as
    the machine is up. So the worker watches its own deadline: driven here with no
    parent to kill it, it must end rather than run on.

    Nothing written in Python could do this. A runaway match holds the interpreter
    outright, so no other thread in that process runs while one is going, which is
    why the watchdog is a native one."""
    from analitiq.validator import _sample_budget

    node = {"type": "string", "pattern": _RUNAWAY_PATTERN}
    jobs = "".join(json.dumps(job) + "\n" for job in (
        {"op": "schema", "value": node},
        {"op": "node", "value": node},
        {"op": "grade", "value": _NEAR_MISS, "deadline": 0.1},
    ))
    proc = subprocess.run(
        [sys.executable, _sample_budget._WORKER_SCRIPT], input=jobs,
        capture_output=True, text=True, timeout=CLI_DEADLINE_SECONDS,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)},
        check=False)
    assert proc.returncode != 0, "the worker outlived its deadline"
    assert "Timeout" in proc.stderr, proc.stderr
    # The channel stays clean even as the process ends: a half-verdict would be
    # read as one.
    assert proc.stdout == "", proc.stdout


def test_a_restart_cannot_outlast_what_is_left_of_the_document_budget(
        validator, monkeypatch, fake_worker):
    """A replacement worker is started for the samples that are left, so it is
    bounded by the budget they have left.

    The FIRST start is setup and carries its own deadline, which is generous
    because a cold interpreter is. A restart is a cost the samples caused, and one
    that stalls under the same generous deadline overshoots the document cap by
    that whole deadline before any sample is told it ran out."""
    from analitiq.validator import connectors
    from analitiq.validator._sample_budget import BudgetedGrader

    fake_worker("stall-on-restart")
    monkeypatch.setattr(connectors, "BudgetedGrader",
                        lambda: BudgetedGrader(sample_budget=0.5, document_budget=3.0))
    doc = _read_endpoint({"one": {"type": "boolean", "examples": ["0"]},
                          "two": {"type": "boolean", "examples": ["1"]}})
    started = time.monotonic()
    errors = _sample_findings(validator.validate_document(doc))
    elapsed = time.monotonic() - started
    assert elapsed < 15.0, f"the stalled restart was not bounded ({elapsed:.1f}s)"
    assert len(errors) == 2, errors
    assert all("was not graded" in e["message"] for e in errors), errors


def test_a_value_too_deep_to_encode_is_the_documents_defect():
    """Nesting deep enough to exhaust the encoder's stack is a shape no JSON
    document could hold, so it is the document's defect rather than a lost worker.

    Graded here rather than through a document, because a document this deep
    cannot be written as JSON either — it reaches the check only from a caller
    that built it in Python, which is the same route the other unencodable values
    take. What matters is that the encoder's `RecursionError` is treated as they
    are: an answer about the value, not an exception escaping the protocol."""
    from analitiq.validator._sample_budget import _encoded

    deep = current = []
    for _ in range(12_000):
        nested = []
        current.append(nested)
        current = nested
    assert _encoded({"op": "grade", "value": deep}) is None
    assert _encoded({"op": "grade", "value": [1, 2]}) is not None


@pytest.mark.parametrize("holder,target,blowup", [
    ("subprocess", "Popen", ValueError("argv is not what Popen wanted")),
    ("queue", "Queue", MemoryError("out of memory")),
    ("threading", "Thread", RuntimeError("can't start new thread")),
], ids=["the spawn refuses", "a queue cannot be allocated", "a thread cannot be started"])
def test_a_host_that_cannot_host_a_worker_is_asked_once_and_said_once(
        holder, target, blowup, validator, monkeypatch):
    """Starting a worker touches a temporary file, a process, a queue, a thread
    and a pipe, and the host can refuse any of them.

    Enumerating them has repeatedly missed one, and every miss escapes to the
    endpoint's guard, which keeps no findings — so the endpoint loses every
    verdict it earned and reports a validator bug in their place. Each refusal
    must instead name the samples it could not grade, and be asked once for the
    document: the answer cannot change between two samples of it."""
    from analitiq.validator import _sample_budget

    attempts = []
    real_popen = _sample_budget.subprocess.Popen

    def refuse(*_args, **_kwargs):
        raise blowup

    def counted(*args, **kwargs):
        attempts.append(args)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(_sample_budget.subprocess, "Popen", counted)
    monkeypatch.setattr(getattr(_sample_budget, holder), target, refuse)
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0", "1"]}})
    findings = validator.validate_document(doc)
    errors = _sample_findings(findings)
    assert len(errors) == 2, findings
    assert all(e["kind"] == "notApplicable" for e in errors), errors
    assert all("was not graded" in e["message"] for e in errors), errors
    assert all(str(blowup) in e["message"] for e in errors), errors
    assert not [f for f in findings if "crashed unexpectedly" in f["message"]], findings
    # Asked once for the document, not once per sample. A refusal that lands
    # before the spawn counts none, which is the same claim.
    assert len(attempts) <= 1, attempts


def test_a_failure_while_grading_is_not_held_against_the_next_sample(
        validator, monkeypatch):
    """The other half, and the reason the two guards are not one.

    A failure while GRADING cannot tell a host that refused something from a
    defect in this module, and either way it says nothing about the samples after
    it — so it is reported against its own sample and the next one still gets a
    worker. Latching here would turn one bad sample into a failed connector."""
    from analitiq.validator import _sample_budget

    attempts = []
    real_popen = _sample_budget.subprocess.Popen
    real_encoded = _sample_budget._encoded

    def counted(*args, **kwargs):
        attempts.append(args)
        return real_popen(*args, **kwargs)

    def refuse_to_encode_a_grade(message):
        if message.get("op") == "grade":
            raise ValueError("the grade message could not be built")
        return real_encoded(message)

    monkeypatch.setattr(_sample_budget.subprocess, "Popen", counted)
    monkeypatch.setattr(_sample_budget, "_encoded", refuse_to_encode_a_grade)
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0", "1"]}})
    findings = validator.validate_document(doc)
    errors = _sample_findings(findings)
    assert len(errors) == 2, findings
    assert all("failed unexpectedly" in e["message"] for e in errors), errors
    assert all("could not be built" in e["message"] for e in errors), errors
    assert not [f for f in findings if "crashed unexpectedly" in f["message"]], findings
    # Not latched: the second sample was given a worker of its own.
    assert len(attempts) == 2, attempts


def test_a_restart_that_spends_the_budget_leaves_none_for_the_sample(
        validator, monkeypatch, fake_worker):
    """The budget is checked when a sample arrives, and a restart happens after
    that check and is charged to it.

    So a replacement worker slow enough to spend what was left would otherwise be
    followed by a full sample budget the document no longer has. Once the restart
    is paid for there may be nothing left, and that is the spent-budget outcome,
    not a licence to overrun."""
    from analitiq.validator import connectors
    from analitiq.validator._sample_budget import BudgetedGrader

    fake_worker("slow-restart")
    monkeypatch.setattr(connectors, "BudgetedGrader",
                        lambda: BudgetedGrader(sample_budget=0.5, document_budget=1.5))
    doc = _read_endpoint({"one": {"type": "boolean", "examples": ["0"]},
                          "two": {"type": "boolean", "examples": ["1"]}})
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 2, errors
    assert "the grading worker was lost" in errors[0]["message"], errors[0]["message"]
    assert "budget was already spent" in errors[1]["message"], errors[1]["message"]
