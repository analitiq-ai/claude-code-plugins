"""Where a finding says it applies (`rules/SCHEMA.md`, "Findings", `path`).

A check that reads a document beside the one validated reports what it finds
there with a pointer into that sibling. The pointer alone would read as a node
of the validated document, so the finding names the sibling in front of it:
`<reference>#<pointer>`, the reference relative to the validated document's
directory. Each case here grades a package on disk from its `connector.json`
(or, for the standalone endpoint route, from the endpoint) and asserts the
whole `path` of the one finding the defect produces.
"""
import json
from pathlib import Path, PurePosixPath

import pytest

CORPUS = Path(__file__).resolve().parent / "corpus"
_H = "https://schemas.analitiq.ai"
_PROPERTY = "/operations/read/response/schema/items/properties/a"


def _endpoint(native="STRING", arrow="Utf8", endpoint_id="v1__records", path="/v1/records"):
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["endpoint_id"] = endpoint_id
    endpoint["operations"]["read"]["request"]["path"] = path
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": native, "arrow_type": arrow}}
    return endpoint


def _read_map(rules=None):
    return {"$schema": f"{_H}/type-map-read/latest.json", "direction": "read",
            "rules": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
            if rules is None else rules}


def _package(root: Path, *, read_map=None, endpoints=None) -> dict:
    """A clean api connector package on disk, with `read_map` / `endpoints`
    (name → document, or text written as is) replacing the clean ones."""
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    files = {"connector.json": connector,
             "type-map-read.json": _read_map() if read_map is None else read_map}
    for name, doc in (endpoints or {"v1__records.json": _endpoint()}).items():
        files[f"endpoints/{name}"] = doc
    for key, doc in files.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return connector


def _graded(validator, root: Path, message_id: str) -> list[dict]:
    connector = json.loads((root / "connector.json").read_text())
    findings = validator.validate_document(connector, doc_path=root / "connector.json")
    return [f for f in findings if f["message_id"] == message_id]


# ---------------------------------------------------------------------------
# One case per class of finding a connector's coverage walk raises about a
# sibling document.
# ---------------------------------------------------------------------------

def test_a_type_map_model_finding_is_located_in_the_map(tmp_path, validator):
    _package(tmp_path, read_map={**_read_map(), "rules": "not a list"})
    [found] = _graded(validator, tmp_path, "list_type")
    assert found["path"] == "type-map-read.json#/rules", found
    # The file is named by `path`; the message does not repeat it.
    assert "type-map-read.json" not in found["message"], found


def test_an_endpoint_model_finding_is_located_in_the_endpoint(tmp_path, validator):
    endpoint = _endpoint()
    del endpoint["endpoint_id"]
    _package(tmp_path, endpoints={"v1__records.json": endpoint})
    [found] = _graded(validator, tmp_path, "missing")
    assert found["path"] == "endpoints/v1__records.json#/endpoint_id", found


def test_a_duplicate_endpoint_id_is_located_in_the_second_declaration(tmp_path, validator):
    endpoint = _endpoint(endpoint_id="dup", path="/dup")
    _package(tmp_path, endpoints={"dup.json": endpoint, "other.json": endpoint})
    [found] = _graded(validator, tmp_path, "duplicate-endpoint-id")
    assert found["path"] == "endpoints/other.json#/endpoint_id", found


def test_an_uncovered_native_type_is_located_at_the_declaring_column(tmp_path, validator):
    _package(tmp_path, endpoints={"v1__records.json": _endpoint("BIGINT", "Int64")})
    [found] = _graded(validator, tmp_path, "native-type-unresolved")
    assert found["path"] == f"endpoints/v1__records.json#{_PROPERTY}", found
    # The read map the native failed to resolve through is part of the
    # complaint, so it stays in the message.
    assert "type-map-read.json" in found["message"], found


def test_an_arrow_mismatch_is_located_at_the_declaring_column(tmp_path, validator):
    _package(tmp_path, endpoints={"v1__records.json": _endpoint("STRING", "Int64")})
    [found] = _graded(validator, tmp_path, "native-type-arrow-mismatch")
    assert found["path"] == f"endpoints/v1__records.json#{_PROPERTY}", found


def test_an_unreadable_endpoint_is_located_at_the_whole_file(tmp_path, validator):
    _package(tmp_path, endpoints={"v1__records.json": "{not json"})
    [found] = _graded(validator, tmp_path, "endpoint-file-unreadable")
    assert found["path"] == "endpoints/v1__records.json#", found


def test_a_nested_endpoint_is_located_at_the_whole_file(tmp_path, validator):
    _package(tmp_path, endpoints={"v1__records.json": _endpoint(),
                                  "extra/v1__records.json": _endpoint()})
    [found] = _graded(validator, tmp_path, "endpoint-file-nested")
    assert found["path"] == "endpoints/extra/v1__records.json#", found


def test_an_unparseable_type_map_is_located_at_the_whole_file(tmp_path, validator):
    _package(tmp_path, read_map="{not json")
    [found] = _graded(validator, tmp_path, "type-map-unparseable")
    assert found["path"] == "type-map-read.json#", found


def test_two_maps_declaring_one_direction_locate_the_second_declaration(tmp_path, validator):
    _package(tmp_path)
    (tmp_path / "type-map-zz.json").write_text(json.dumps(_read_map()))
    [found] = _graded(validator, tmp_path, "type-map-direction-duplicated")
    assert found["path"] == "type-map-zz.json#/direction", found


def test_an_api_write_map_is_located_at_its_declared_direction(tmp_path, validator):
    _package(tmp_path)
    (tmp_path / "type-map-write.json").write_text(json.dumps({
        "$schema": f"{_H}/type-map-write/latest.json", "direction": "write", "rules": []}))
    [found] = _graded(validator, tmp_path, "write-map-not-allowed")
    assert found["path"] == "type-map-write.json#/direction", found


def test_a_package_shape_finding_stays_on_the_validated_document(tmp_path, validator):
    # No sibling to point at: the obligation is the connector's own.
    _package(tmp_path)
    (tmp_path / "type-map-read.json").unlink()
    [found] = _graded(validator, tmp_path, "read-map-missing")
    assert found["path"] == "", found


def test_an_endpoint_finding_names_no_file_in_its_message(tmp_path, validator):
    # An embedded schema that is not Draft 2020-12 (RULE-ENDP-048). `path`
    # carries the file; a message repeating it is a second copy of the
    # location.
    endpoint = _endpoint()
    endpoint["operations"]["read"]["response"]["schema"]["type"] = 7
    _package(tmp_path, endpoints={"v1__records.json": endpoint})
    connector = json.loads((tmp_path / "connector.json").read_text())
    findings = validator.validate_document(connector, doc_path=tmp_path / "connector.json")
    [found] = [f for f in findings if f.get("rule") == "RULE-ENDP-048"]
    assert found["path"] == "endpoints/v1__records.json#/operations/read/response/schema", found
    assert "v1__records.json" not in found["message"], found


# ---------------------------------------------------------------------------
# The other caller that reads a sibling: an endpoint validated on its own.
# ---------------------------------------------------------------------------

def test_a_standalone_endpoint_locates_its_unreadable_connector(tmp_path, validator):
    endpoint = _endpoint()
    endpoint["operations"]["read"]["request"]["transport_ref"] = "main"
    _package(tmp_path, endpoints={"v1__records.json": endpoint})
    (tmp_path / "connector.json").write_text("{not json")
    findings = validator.validate_document(
        endpoint, doc_path=tmp_path / "endpoints" / "v1__records.json")
    [found] = [f for f in findings if f["message_id"] == "sibling-connector-unreadable"]
    assert found["path"] == "../connector.json#", found
    # The check it could not run is the endpoint's own, so it stays unqualified.
    [skipped] = [f for f in findings
                 if f["message_id"] == "transport-ref-check-skipped-unparseable"]
    assert skipped["path"] == "", skipped


# ---------------------------------------------------------------------------
# A finding about a whole document has the empty pointer: `/` would point at
# a member whose key is the empty string.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("graded,message_id", [
    (lambda v: v.validate_document(42), "unrecognized-document"),
    # A model error at the document root: pydantic's `loc` is empty.
    (lambda v: v.type_map_findings_as_declared(
        [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]),
     "model_attributes_type"),
    (lambda v: v.validate_pipeline_bundle(["not", "a", "bundle"]), "bundle-not-a-mapping"),
])
def test_a_whole_document_finding_has_the_empty_pointer(validator, graded, message_id):
    [found] = [f for f in graded(validator) if f["message_id"] == message_id]
    assert found["path"] == "", found


# ---------------------------------------------------------------------------
# The reference: one computation, the same on disk and in memory.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target,seen_from,expected", [
    ("endpoints/a.json", "connector.json", "endpoints/a.json"),
    ("connector.json", "connector.json", "connector.json"),
    ("connector.json", "endpoints/a.json", "../connector.json"),
    # `#` would otherwise end the reference early; `%` and a space are not
    # URI characters; a `:` in a first segment would read as a scheme.
    ("endpoints/a#b.json", "connector.json", "endpoints/a%23b.json"),
    ("endpoints/100% done.json", "connector.json", "endpoints/100%25%20done.json"),
    ("a:b.json", "connector.json", "a%3Ab.json"),
])
def test_a_reference_is_relative_and_percent_encoded(tmp_path, target, seen_from, expected):
    from analitiq.validator._location import DISK, Location, MemoryTree, reference
    tree = MemoryTree({target: "{}", seen_from: "{}"})
    in_memory = reference(Location(PurePosixPath(target), tree),
                          seen_from=Location(PurePosixPath(seen_from), tree))
    on_disk = reference(Location(tmp_path / target, DISK),
                        seen_from=Location(tmp_path / seen_from, DISK))
    assert in_memory == on_disk == expected


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
