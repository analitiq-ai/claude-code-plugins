"""Where a finding says it applies (`rules/SCHEMA.md`, "Findings", `path`).

A package has no one validated document, so each of its findings names the
document it concerns by key in front of the pointer: `<key>#<pointer>`, the key
percent-encoded as a relative reference. A single document's finding is the bare
pointer. The package cases grade an api connector package through
`validate_package` and assert the whole `path` of the one finding the defect
produces.
"""
import json
from pathlib import Path

import pytest

from analitiq.contracts.validation_requests import (
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
)

CORPUS = Path(__file__).resolve().parent / "corpus"
_H = "https://schemas.analitiq.ai"
_PROPERTY = "/operations/read/response/schema/items/properties/a"
_TYPE_MAP = "definition/type-map.json"
_ENDPOINT = "definition/endpoints/v1__records.json"


def _endpoint(native="STRING", arrow="Utf8", endpoint_id="v1__records", path="/v1/records"):
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["endpoint_id"] = endpoint_id
    endpoint["operations"]["read"]["request"]["path"] = path
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": native, "arrow_type": arrow}}
    return endpoint


def _type_map(**sections):
    return {"$schema": f"{_H}/type-map/latest.json",
            **(sections or {"read": [
                {"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]})}


def _package(*, type_map=None, endpoint=None) -> dict:
    """A clean api connector package, key → document or text carried as is,
    with `type_map` / `endpoint` replacing the clean ones."""
    return {"definition/connector.json": json.loads((CORPUS / "valid_connector.json").read_text()),
            _TYPE_MAP: _type_map() if type_map is None else type_map,
            _ENDPOINT: _endpoint() if endpoint is None else endpoint}


def _package_findings(validator, documents: dict) -> list[dict]:
    request = ValidatePackageRequest(package_kind="connector", documents={
        key: doc if isinstance(doc, str) else json.dumps(doc) for key, doc in documents.items()})
    return validator.validate_package(request)["findings"]


def _graded(validator, documents: dict, message_id: str) -> list[dict]:
    return [f for f in _package_findings(validator, documents) if f["message_id"] == message_id]


def _single(validator, document, document_kind: str) -> list[dict]:
    return validator.validate_single_document(ValidateSingleDocumentRequest(
        document=json.dumps(document), document_kind=document_kind))["findings"]


# ---------------------------------------------------------------------------
# One case per class of finding a connector package raises about a document
# other than its connector.
# ---------------------------------------------------------------------------

def test_a_type_map_model_finding_is_located_in_the_map(validator):
    [found] = _graded(validator, _package(type_map=_type_map(read="not a list")), "list_type")
    assert found["path"] == f"{_TYPE_MAP}#/read", found
    # The document is named by `path`; the message does not repeat it.
    assert "type-map.json" not in found["message"], found


def test_an_endpoint_model_finding_is_located_in_the_endpoint(validator):
    endpoint = _endpoint()
    del endpoint["endpoint_id"]
    [found] = _graded(validator, _package(endpoint=endpoint), "missing")
    assert found["path"] == f"{_ENDPOINT}#/endpoint_id", found


def test_an_uncovered_native_type_is_located_at_the_declaring_column(validator):
    [found] = _graded(validator, _package(endpoint=_endpoint("BIGINT", "Int64")),
                      "native-type-unresolved")
    assert found["path"] == f"{_ENDPOINT}#{_PROPERTY}", found
    # The read section the native failed to resolve through is part of the
    # complaint, so it stays in the message.
    assert "'read' section of the package's type map" in found["message"], found


def test_an_arrow_mismatch_is_located_at_the_declaring_column(validator):
    [found] = _graded(validator, _package(endpoint=_endpoint("STRING", "Int64")),
                      "native-type-arrow-mismatch")
    assert found["path"] == f"{_ENDPOINT}#{_PROPERTY}", found


def test_an_unparseable_type_map_is_located_at_the_whole_document(validator):
    [found] = _graded(validator, _package(type_map="{not json"), "unreadable-document")
    assert found["path"] == f"{_TYPE_MAP}#", found


def test_a_package_shape_finding_is_located_on_the_connector(validator):
    # The obligation is the connector's: there is no map to point into.
    documents = _package()
    del documents[_TYPE_MAP]
    [found] = _graded(validator, documents, "read-map-missing")
    assert found["path"] == "definition/connector.json#", found


def test_an_endpoint_finding_names_no_file_in_its_message(validator):
    # An embedded schema that is not Draft 2020-12 (RULE-ENDP-048). `path`
    # carries the document; a message repeating it is a second copy of the
    # location.
    endpoint = _endpoint()
    endpoint["operations"]["read"]["response"]["schema"]["type"] = 7
    [found] = [f for f in _package_findings(validator, _package(endpoint=endpoint))
               if f.get("rule") == "RULE-ENDP-048"]
    assert found["path"] == f"{_ENDPOINT}#/operations/read/response/schema", found
    assert "v1__records.json" not in found["message"], found


# ---------------------------------------------------------------------------
# A finding about a whole document has the empty pointer: `/` would point at
# a member whose key is the empty string.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("graded,message_id", [
    # A model error at the document root: pydantic's `loc` is empty.
    (lambda v: _single(
        v, [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}], "type-map"),
     "model_type"),
])
def test_a_whole_document_finding_has_the_empty_pointer(validator, graded, message_id):
    [found] = [f for f in graded(validator) if f["message_id"] == message_id]
    assert found["path"] == "", found


def test_a_model_error_escapes_the_keys_on_its_pointer(validator):
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    connector["transports"]["api"]["headers"]["A/b~c"] = 7
    paths = [f["path"] for f in _single(validator, connector, "connector")]
    assert "/transports/api/headers/A~1b~0c" in paths, paths


def test_a_rejected_mapping_key_is_located_at_its_member(validator):
    # pydantic marks a key that failed validation with a trailing `[key]`,
    # which names no member.
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    connector["transports"]["api"]["headers"]["A b"] = "x"
    assert [f["path"] for f in _single(validator, connector, "connector")
            if f["message_id"] == "string_pattern_mismatch"] == ["/transports/api/headers/A b"]


def test_an_element_past_a_tuples_fixed_items_is_located_at_its_index(validator):
    # `key_attrs` is a variadic tuple: every element after its fixed items is
    # validated by the one repeated item schema.
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    connector["error_map"] = {"key_attrs": ["code", "1bad"], "codes": {"x": "auth"}}
    assert [f["path"] for f in _single(validator, connector, "connector")
            if f["message_id"] == "string_pattern_mismatch"] == ["/error_map/key_attrs/1"]


def test_a_model_error_names_no_union_tag_on_its_pointer(validator):
    # The connector root is a union discriminated on `kind`; its tag is where
    # the model walk went, not a member of the document.
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    connector["display_name"] = 7
    assert [f["path"] for f in _single(validator, connector, "connector")
            if f["message_id"] == "string_type"] == ["/display_name"]


def test_a_union_tag_that_is_also_a_member_name_is_not_read_as_the_member(validator):
    # Offset pagination is tagged `offset` and carries an `offset` member, so
    # the error in `limit` is where the tag and the document disagree.
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["operations"]["read"]["pagination"] = {
        "type": "offset", "offset": {"param": "skip", "initial": 0},
        "limit": {"param": 7, "default": 50}, "stop_when": {"empty": {"ref": "response.body"}}}
    assert [f["path"] for f in _single(validator, endpoint, "api-endpoint")
            if f["message_id"] == "string_type"] == ["/operations/read/pagination/limit/param"]


def _write_mode(endpoint, mode, request):
    endpoint["operations"]["write"] = {mode: {"request": request}}
    return endpoint


def test_an_unresolved_transport_ref_escapes_its_write_mode(validator):
    endpoint = _write_mode(_endpoint(), "a/b", {"transport_ref": "nope", "path": "/v1/records"})
    [found] = _graded(validator, _package(endpoint=endpoint), "transport-ref-undeclared")
    assert found["path"] == f"{_ENDPOINT}#/operations/write/a~1b/request/transport_ref", found


def test_an_unstable_locator_escapes_its_write_mode(validator):
    endpoint = _write_mode(_endpoint(), "a/b", {"path": "/records.json"})
    del endpoint["operations"]["read"]
    [found] = [f for f in _single(validator, endpoint, "api-endpoint")
               if f["message_id"] == "locator-unstable"]
    assert found["path"] == "/operations/write/a~1b/request/path", found


# ---------------------------------------------------------------------------
# The reference a package finding names its document by.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,expected", [
    ("definition/endpoints/a.json", "definition/endpoints/a.json"),
    # `#` would otherwise end the reference early; `%` and a space are not
    # URI characters; a `:` in a first segment would read as a scheme.
    ("definition/endpoints/a#b.json", "definition/endpoints/a%23b.json"),
    ("definition/endpoints/100% done.json", "definition/endpoints/100%25%20done.json"),
    ("a:b.json", "a%3Ab.json"),
])
def test_a_reference_is_percent_encoded(key, expected):
    from analitiq.validator.document_set import relative_reference
    assert relative_reference(key) == expected


def test_a_qualified_path_splits_at_the_first_hash(validator):
    # RFC 6901 does not escape `#`, so a pointer may carry one; the reference
    # never does, so the first `#` is always the separator.
    from analitiq.validator._core import qualified
    found = validator.finding(message_id="x", kind="informational", path="/a#b", message="m")
    assert qualified(found, "endpoints/a%23b.json")["path"] == "endpoints/a%23b.json#/a#b"


def test_only_a_bare_pointer_is_qualified(validator):
    from analitiq.validator._core import qualified
    found = validator.finding(message_id="x", kind="informational", path="/a", message="m")
    with pytest.raises(ValueError):
        qualified(qualified(found, "a.json"), "b.json")


# ---------------------------------------------------------------------------
# A model finding's pointer locates its node, across every model a rule fixture
# names: each fixture is graded with one dict member at a time made the wrong
# type, so the errors reach every union, mapping and list the fixtures exercise.
# ---------------------------------------------------------------------------

def _node(doc, pointer: str):
    """The node `pointer` locates in `doc`; raises `LookupError` when none."""
    node = doc
    for token in pointer.split("/")[1:] if pointer else ():
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            raise LookupError(pointer)
    return node


def _with_each_member_mistyped(doc, at=()):
    if isinstance(doc, dict):
        for key, value in doc.items():
            yield at + (key,), {**doc, key: [[]]}
            for where, inner in _with_each_member_mistyped(value, at + (key,)):
                yield where, {**doc, key: inner}
    elif isinstance(doc, list):
        for i, value in enumerate(doc):
            for where, inner in _with_each_member_mistyped(value, at + (i,)):
                yield where, [*doc[:i], inner, *doc[i + 1:]]


def _located(doc, f) -> bool:
    # A `missing` error names the absent member under a node that exists.
    try:
        _node(doc, f["path"])
        return True
    except LookupError:
        parent, _, _ = f["path"].rpartition("/")
        if f["message_id"] != "missing":
            return False
        try:
            _node(doc, parent)
            return True
        except LookupError:
            return False


def test_every_model_finding_locates_its_node():
    from pydantic import TypeAdapter
    from analitiq.contracts.shared.rule_fixtures import rule_fixtures
    from analitiq.validator._core import _model_findings
    adapters: dict = {}
    unlocated = []
    for fixture in rule_fixtures():
        adapter = adapters.setdefault(fixture.model, TypeAdapter(fixture.model))
        graded = [((), fixture.document), *_with_each_member_mistyped(fixture.document)]
        for where, doc in graded:
            unlocated += [(fixture.rule_id, fixture.name, where, f["path"], f["message_id"])
                          for f in _model_findings(doc, adapter) if not _located(doc, f)]
    assert not unlocated, unlocated[:10]
