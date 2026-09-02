"""`embedded-schema-example` — a recorded sample must satisfy the node declaring it.

Every other check over an endpoint compares one declaration with another, so a
node whose declared type contradicts what the provider actually sends passes all
of them. A value under `examples` is copied off the wire, which makes it the one
thing in the document those checks can be graded against; these tests drive that
grading through both entry points — a single endpoint document, and the
connector-anchored walk that labels findings with the endpoint's filename.
"""
import json

import pytest

from analitiq.contracts.endpoints import (
    JSON_SCHEMA_LIST_OF_SCHEMA_KEYS,
    JSON_SCHEMA_SINGLE_SCHEMA_KEYS,
    JSON_SCHEMA_SUBSCHEMA_KEYS,
    WRITE_MODES,
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


def _model_errors(validator, doc):
    return [f for f in validator.validate_document(doc)
            if f["validator"] == "contract-model"]


def _sample_findings(findings):
    return [f for f in findings if f["validator"] == "embedded-schema-example"]


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
    if _model_errors(validator, doc):
        doc["operations"]["write"][mode]["conflict_keys"] = ["paid"]
    assert not _model_errors(validator, doc), _model_errors(validator, doc)
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
    """Acceptance 5, behaviourally.

    The keyword inventory is the contract's and is pinned member-for-member
    elsewhere, but a walker body reading a hardcoded subset of it satisfies that
    pin and grades nothing at the positions it skipped. So the positions are
    driven from the imported sets: a keyword the contract adds is graded here or
    this test goes red.

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


@pytest.mark.parametrize("key", sorted(
    JSON_SCHEMA_SUBSCHEMA_KEYS | JSON_SCHEMA_LIST_OF_SCHEMA_KEYS | JSON_SCHEMA_SINGLE_SCHEMA_KEYS))
def test_the_type_pair_walk_reaches_the_same_positions(key, validator):
    """The other consumer of the shared walk.

    Sample grading and the `native_type`/`arrow_type` pair collection descend
    through one generator, so a position one reaches is a position the other
    reaches — and the pointer each reports is built once. Asserted from both
    ends, because that shared generator is a choice a later change could undo
    silently.
    """
    from analitiq.validator import connectors

    placed, segment = _at_position(key, {"native_type": "STRING", "arrow_type": "Utf8"})
    doc = _read_endpoint({"f": {"type": "object", **placed}})
    assert connectors._collect_native_arrow_pairs(doc) == [
        ("STRING", "Utf8",
         f"/operations/read/response/schema/items/properties/f/{segment}")
    ]


def test_a_ref_node_is_graded_against_what_it_points_at(validator):
    """The sample sits on the referring node, so grading it means resolving the
    reference against the whole embedded document rather than the node alone."""
    doc = _read_endpoint(
        {"paid": {"$ref": "#/$defs/flag", "examples": ["0"]}},
        defs={"flag": {"type": "boolean"}},
    )
    errors = _sample_findings(validator.validate_document(doc))
    assert len(errors) == 1, errors
    assert errors[0]["path"] == (
        "/operations/read/response/schema/items/properties/paid/examples/0")
    # The keyword verdict, not merely a finding: an unresolved reference also
    # produces exactly one finding at this path, and asserting the path alone
    # cannot tell the two apart.
    assert "is not of type 'boolean'" in errors[0]["message"]
    assert "could not grade" not in errors[0]["message"]


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
    assert [f["validator"] for f in _errors(findings)] == ["embedded-json-schema"]


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
    assert "could not grade" in by_path["gone/examples/0"]
    assert "$ref" in by_path["gone/examples/0"]
    # An oversized sample is truncated into the message rather than pasted whole.
    assert "chars)" in by_path["big/examples/0"]
    assert "0" * 200 not in by_path["big/examples/0"]
    assert [f for f in findings if f["validator"] == "contract-model"], findings


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
    assert "embedded-json-schema" in {f["validator"] for f in findings}
    crashes = _sample_findings(findings)
    assert len(crashes) == 1 and "crashed unexpectedly" in crashes[0]["message"]


def test_a_node_stating_no_json_schema_type_is_not_graded(validator):
    """The boundary of what a sample settles, made executable.

    Grading is against the node's JSON Schema statement. A node carrying only
    the contract's `native_type`/`arrow_type` pair states nothing a value can
    contradict, so the sample is graded against nothing and the check is silent.
    Deciding it would take a JSON-kind to Arrow-family table — cast semantics
    this repo does not own and could not pin.
    """
    doc = _read_endpoint({"paid": {
        "native_type": "BOOLEAN", "arrow_type": "Boolean", "examples": ["0"]}})
    assert not _sample_findings(validator.validate_document(doc))


def test_a_schema_declaring_another_draft_is_not_graded(validator):
    """A document declaring a draft the contract does not read is reported by
    the meta-check and skipped here. Grading it under 2020-12 would report
    keywords that were never going to apply to it."""
    doc = _read_endpoint({"paid": {"type": "boolean", "examples": ["0"]}},
                         dialect="http://json-schema.org/draft-07/schema#")
    findings = validator.validate_document(doc)
    assert not _sample_findings(findings)
    assert [f["validator"] for f in _errors(findings)] == ["embedded-json-schema"]


def test_grading_is_never_invoked_without_the_check_that_reports_its_skips(validator):
    """The skip is safe only because the meta-check runs beside it.

    `_embedded_schema_example_findings` silently passes over any document
    `_unreadable_as_2020_12` rejects, on the understanding that
    `_embedded_schema_findings` reports that document as an error. Nothing in
    either function enforces the pairing — it is a property of the call sites,
    so it is asserted here. Located lexically: a call by name inside the
    function that encloses it.
    """
    import ast
    from pathlib import Path as _Path

    from analitiq.validator import connectors

    tree = ast.parse(_Path(connectors.__file__).read_text(encoding="utf-8"))

    def called_names(node):
        return {getattr(c.func, "id", None) for c in ast.walk(node)
                if isinstance(c, ast.Call)} | {
            a.id for c in ast.walk(node) if isinstance(c, ast.Call)
            for a in c.args if isinstance(a, ast.Name)}

    callers = [fn for fn in ast.walk(tree)
               if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
               and "_embedded_schema_example_findings" in called_names(fn)]
    assert callers, (
        "no function calls `_embedded_schema_example_findings` — the check was "
        "removed or renamed, and this assertion stopped matching rather than "
        "the pairing stopping to hold"
    )
    unpaired = [fn.name for fn in callers
                if "_embedded_schema_findings" not in called_names(fn)]
    assert not unpaired, (
        "sample grading is invoked without `_embedded_schema_findings` in: "
        f"{unpaired}. Grading skips every schema the meta-check rejects, so "
        "without it a document that is not a valid JSON Schema passes clean."
    )


def test_the_connector_walk_labels_findings_with_the_endpoint_filename(tmp_path, validator):
    """The other entry point: a connector package, where a finding must name the
    endpoint file it came from."""
    from pathlib import Path

    corpus = Path(__file__).resolve().parent / "corpus" / CORPUS_CONNECTOR
    connector = json.loads(corpus.read_text())
    (tmp_path / "endpoints").mkdir(parents=True)
    (tmp_path / "connector.json").write_text(json.dumps(connector))
    (tmp_path / "type-map-read.json").write_text(json.dumps(
        [{"match": "exact", "native": "BOOLEAN", "canonical": "Boolean"}]))
    (tmp_path / "endpoints" / "widgets.json").write_text(
        json.dumps(_read_endpoint({"paid": STRING_FLAG})))

    findings = validator.validate_document(
        connector, doc_path=tmp_path / "connector.json")
    errors = _sample_findings(findings)
    assert len(errors) == 2, findings
    assert all("widgets.json/operations/read/" in e["message"] for e in errors)
