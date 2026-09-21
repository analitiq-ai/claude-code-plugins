"""Grading by declared kind and by published package.

A caller names what it holds: one document and the kind it is written against,
or a package and the published package schema it forms. Nothing reads a
document's content to decide what it is. A package's root and the kind of each
document come from `PACKAGE_MODELS[package]` alone; a key no location matches is
not part of the package and is not graded.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analitiq.contracts.connection import CONNECTION_SCHEMA_URL
from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.contracts.endpoints import DATABASE_ENDPOINT_SCHEMA_URL
from analitiq.contracts.pipelines.config import PIPELINE_SCHEMA_URL
from analitiq.contracts.stream import STREAM_SCHEMA_URL
from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL
from analitiq.contracts.validation_requests import (
    PACKAGE_MODELS,
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
)

CORPUS = Path(__file__).resolve().parent / "corpus"

_SRC, _DST, _STREAM_ID, _PIPELINE_ID = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
)
_EID = derive_db_endpoint_id(None, "public", "orders")


def _type_map(**sections) -> dict:
    return {"$schema": TYPE_MAP_SCHEMA_URL, **sections}


def _api_endpoint(endpoint_id="v1__records", path="/v1/records", native="STRING", arrow="Utf8") -> dict:
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["endpoint_id"] = endpoint_id
    endpoint["operations"]["read"]["request"]["path"] = path
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": native, "arrow_type": arrow}}
    return endpoint


def _connector_package() -> dict:
    return {
        "definition/connector.json": json.loads((CORPUS / "valid_connector.json").read_text()),
        "definition/type-map.json": _type_map(
            read=[{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]),
        "definition/endpoints/v1__records.json": _api_endpoint(),
    }


def _db_endpoint(endpoint_id=_EID) -> dict:
    return {
        "$schema": DATABASE_ENDPOINT_SCHEMA_URL, "endpoint_id": endpoint_id,
        "database_object": build_database_object(None, "public", "orders"),
        "columns": [{"name": "id", "native_type": "bigint", "arrow_type": "Int64",
                     "nullable": False, "ordinal_position": 1}],
    }


def _connection_package() -> dict:
    return {
        "connection.json": {"$schema": CONNECTION_SCHEMA_URL, "connector_id": "stripe"},
        f"definition/endpoints/{_EID}.json": _db_endpoint(),
    }


def _stream() -> dict:
    return {
        "$schema": STREAM_SCHEMA_URL, "stream_id": _STREAM_ID, "pipeline_id": _PIPELINE_ID,
        "source": {"endpoint_ref": {"scope": "connector", "connection_id": f"{_SRC}_v1",
                                    "endpoint_id": "transfers"}},
        "destinations": [{"endpoint_ref": {"scope": "connector", "connection_id": f"{_DST}_v1",
                                           "endpoint_id": "orders"},
                          "write": {"mode": "insert"}}],
    }


def _pipeline_package() -> dict:
    return {
        "pipeline.json": {
            "$schema": PIPELINE_SCHEMA_URL, "pipeline_id": _PIPELINE_ID,
            "connections": {"source": f"{_SRC}_v1", "destinations": [f"{_DST}_v1"]},
            "streams": [f"{_STREAM_ID}_v2"]},
        "streams/orders.json": _stream(),
    }


_PACKAGES = {
    "connector-package": _connector_package,
    "connection-package": _connection_package,
    "pipeline-package": _pipeline_package,
}


def _request(package: str, documents: dict) -> ValidatePackageRequest:
    return ValidatePackageRequest(
        package=package, documents={key: json.dumps(doc) for key, doc in documents.items()})


def _write(root: Path, documents: dict) -> None:
    for key, doc in documents.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))


def _at(findings: list, message_id: str) -> list[str]:
    return [f["path"] for f in findings if f["message_id"] == message_id]


# ---------------------------------------------------------------------------
# The kind vocabulary is the kinds the published packages locate.
# ---------------------------------------------------------------------------

def test_the_graded_kinds_are_the_kinds_packages_locate(validator):
    from analitiq.validator._core import document_kinds

    located = {kind for model in PACKAGE_MODELS.values() for kind in model.LOCATIONS.values()}
    assert document_kinds() == located


def test_every_package_has_a_package_check(validator):
    from analitiq.validator.document_set import _PACKAGE_CHECKS

    assert set(_PACKAGE_CHECKS) == set(PACKAGE_MODELS)


def test_an_unknown_kind_is_the_callers_error(validator):
    with pytest.raises(ValueError, match="pipeline-bundle"):
        validator.validate_document({}, "pipeline-bundle")


def test_a_document_is_graded_as_the_kind_its_caller_names(validator):
    """No detection: a stream graded as a connector is graded against the
    connector model and fails it, where graded as a stream it passes."""
    as_stream = validator.validate_document(_stream(), "stream")
    as_connector = validator.validate_document(_stream(), "connector")
    assert not any(validator.finding_costs_a_pass(f) for f in as_stream), as_stream
    assert any(validator.finding_costs_a_pass(f) for f in as_connector), as_connector


def test_a_single_connector_is_graded_without_siblings(validator):
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    assert validator.validate_document(connector, "connector") == []


def test_single_document_request_grades_as_its_entity(validator):
    result = validator.validate_single_document(
        ValidateSingleDocumentRequest(document=json.dumps(_stream()), entity="connector"))
    assert result["passed"] is False
    assert result["findings"] == validator.validate_document(_stream(), "connector")


def test_single_document_text_the_parser_refuses_is_a_finding_not_a_raise(
        validator, text_refused_outside_jsondecodeerror):
    """Document content is what this API judges, so text the parser refuses —
    with a `JSONDecodeError` or otherwise — comes back as a finding; a raise is
    reserved for a defect in this package."""
    for text in ("{not json", text_refused_outside_jsondecodeerror):
        result = validator.validate_single_document(
            ValidateSingleDocumentRequest(document=text, entity="connector"))
        assert [f["message_id"] for f in result["findings"]] == ["unreadable-document"], result


def test_entry_points_are_annotated_with_their_request_models(validator):
    """The request models are imported under `TYPE_CHECKING` and no type
    checker runs over this repo, so a misspelled model, a deferred import of a
    module that does not exist, or a parameter beside `request` would otherwise
    reach a release unnoticed."""
    import ast
    import importlib
    import inspect
    from typing import get_type_hints

    document_set = validator.document_set
    # The namespace is built by executing the module's OWN deferred imports:
    # a namespace this test chose would resolve the annotations whatever that
    # block says.
    source = Path(document_set.__file__).read_text(encoding="utf-8")
    deferred = [statement
                for node in ast.parse(source).body
                if isinstance(node, ast.If)
                and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                for statement in node.body if isinstance(statement, ast.ImportFrom)]
    assert deferred, "no `if TYPE_CHECKING:` import resolves these annotations"
    namespace = {}
    for statement in deferred:
        module = importlib.import_module(statement.module)
        for alias in statement.names:
            namespace[alias.asname or alias.name] = getattr(module, alias.name)

    for entry_point, request_model in (
        (document_set.validate_single_document, ValidateSingleDocumentRequest),
        (document_set.validate_package, ValidatePackageRequest),
    ):
        hints = get_type_hints(entry_point, localns=namespace)
        assert hints["request"] is request_model, entry_point.__name__
        assert hints["return"] is document_set.ValidationEnvelope, entry_point.__name__
        assert tuple(inspect.signature(entry_point).parameters) == ("request",), entry_point.__name__


def test_finding_matches_the_keys_finding_builder_produces(validator):
    from typing import get_args, get_type_hints

    from analitiq.validator._core import _KINDS
    from analitiq.validator.document_set import Finding

    # Every call `finding()` admits — each kind, with no rule and with rules of
    # each severity a `fail` finding can report — rather than sampled calls: a
    # key set only on a branch no sample visits would be invisible below.
    produced = [validator.finding(rule=rule, message_id="m", kind=kind, path="p", message="msg")
                for kind in _KINDS for rule in (None, "RULE-PKG-030", "RULE-CTOR-043")]
    possible_keys = set().union(*(set(f) for f in produced))
    always_present = set.intersection(*(set(f) for f in produced))
    hints = get_type_hints(Finding)
    assert set(hints) == possible_keys
    assert Finding.__required_keys__ == always_present
    assert Finding.__optional_keys__ == possible_keys - always_present

    assert set(get_args(hints["kind"])) == set(_KINDS)
    with pytest.raises(ValueError):
        validator.finding(message_id="m", kind="not-a-real-kind", path="p", message="msg")

    # `severity` is set only on a `fail` finding, from the named rule's own
    # severity: RULE-PKG-030 is error-tier, RULE-CTOR-043 warning-tier, and an
    # info-tier rule (RULE-CTOR-032) is refused as `kind: fail` altogether.
    observed = {validator.finding(rule=rule, message_id="m", kind="fail", path="p", message="msg")["severity"]
                for rule in ("RULE-PKG-030", "RULE-CTOR-043")}
    assert observed == set(get_args(hints["severity"]))
    with pytest.raises(ValueError):
        validator.finding(rule="RULE-CTOR-032", message_id="m", kind="fail", path="p", message="msg")


# ---------------------------------------------------------------------------
# Packages: the published model decides root and locations.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("package", sorted(_PACKAGES))
def test_a_package_its_model_accepts_passes(validator, package):
    documents = _PACKAGES[package]()
    PACKAGE_MODELS[package].model_validate(documents)
    assert validator.validate_package(_request(package, documents)) == {"passed": True, "findings": []}


@pytest.mark.parametrize("package", sorted(_PACKAGES))
def test_the_disk_route_grades_what_the_request_route_grades(validator, package, tmp_path):
    documents = _PACKAGES[package]()
    documents[f"{PACKAGE_MODELS[package].ROOT}.bak"] = {"not": "located"}
    _write(tmp_path, documents)
    assert validator.validate_package_at(tmp_path, package) == validator.validate_package(
        _request(package, documents))


@pytest.mark.parametrize("package", sorted(_PACKAGES))
def test_a_package_without_its_root_fails_at_the_package(validator, package):
    documents = _PACKAGES[package]()
    del documents[PACKAGE_MODELS[package].ROOT]
    result = validator.validate_package(_request(package, documents))
    assert result["passed"] is False
    assert [f["path"] for f in result["findings"] if validator.finding_costs_a_pass(f)] == [""], result


def test_a_set_that_is_another_package_fails_as_the_declared_one(validator):
    result = validator.validate_package(_request("connector-package", _pipeline_package()))
    assert result["passed"] is False
    assert [f["path"] for f in result["findings"]] == [""], result


def test_an_unlocated_key_is_not_graded(validator):
    documents = {**_connector_package(), "notes/readme.json": {"anything": 1}}
    assert validator.validate_package(_request("connector-package", documents))["passed"] is True


def test_an_unparseable_located_document_is_a_finding_at_its_key(validator):
    texts = {key: json.dumps(doc) for key, doc in _connector_package().items()}
    texts["definition/endpoints/v2 x.json"] = "{not json"
    result = validator.validate_package(
        ValidatePackageRequest(package="connector-package", documents=texts))
    assert _at(result["findings"], "unreadable-document") == ["definition/endpoints/v2%20x.json#"]


def test_an_unreadable_file_is_a_finding_at_its_key(validator, tmp_path):
    documents = _connector_package()
    _write(tmp_path, documents)
    (tmp_path / "definition/endpoints/v1__records.json").write_bytes(b"\xff\xfe")
    result = validator.validate_package_at(tmp_path, "connector-package")
    assert _at(result["findings"], "unreadable-document") == ["definition/endpoints/v1__records.json#"]


def test_a_crashed_package_check_keeps_the_document_findings(validator, monkeypatch):
    """A package check that raises costs the pass as one `check-crashed`
    finding about the package; the documents graded before it keep theirs."""
    from analitiq.validator import document_set

    def crashed(_documents, _unread):
        raise TypeError("a package-check defect")

    monkeypatch.setitem(document_set._PACKAGE_CHECKS, "connector-package", crashed)
    texts = _texts(_connector_package())
    texts["definition/endpoints/v2 x.json"] = "{not json"
    result = validator.validate_package(
        ValidatePackageRequest(package="connector-package", documents=texts))
    assert not result["passed"]
    assert _at(result["findings"], "check-crashed") == [""], result["findings"]
    assert _at(result["findings"], "unreadable-document") == ["definition/endpoints/v2%20x.json#"]


def test_an_unknown_package_on_disk_is_the_callers_error(validator, tmp_path):
    with pytest.raises(ValueError):
        validator.validate_package_at(tmp_path, "pipeline-bundle")


@pytest.mark.parametrize("unlisted", ["definition", "definition/endpoints"])
def test_a_directory_the_walk_cannot_list_raises(validator, tmp_path, refuse, unlisted):
    """What an unlisted directory holds is unknown, and no finding about a
    document can say so: grading on would pass a package on the strength of
    what the walk never saw."""
    _write(tmp_path, _connector_package())
    refuse(tmp_path / unlisted, 0o300)
    with pytest.raises(PermissionError):
        validator.validate_package_at(tmp_path, "connector-package")


def test_a_located_document_whose_lookup_is_refused_is_unreadable(validator, tmp_path, refuse):
    """A directory that can be listed but not searched names its entries and
    refuses every lookup of them. A refused lookup is not an absent file: the
    document is there and cannot be read."""
    _write(tmp_path, _connector_package())
    refuse(tmp_path / "definition/endpoints", 0o600)
    findings = validator.validate_package_at(tmp_path, "connector-package")["findings"]
    assert _at(findings, "unreadable-document") == ["definition/endpoints/v1__records.json#"]


def test_an_entry_whose_lookup_is_refused_raises_where_it_could_hold_documents(
        validator, tmp_path, refuse):
    """An unlocated entry the walk cannot look up may be a directory holding
    documents, and no finding about a document can say what it holds."""
    _write(tmp_path, _connector_package())
    refuse(tmp_path / "definition", 0o600)
    with pytest.raises(PermissionError):
        validator.read_package(tmp_path, "connector-package")


@pytest.mark.parametrize("link", ["definition/endpoints/v2__records.json", "definition/notes"])
@pytest.mark.parametrize("target", ["nowhere", "self"])
def test_a_link_that_leads_to_nothing_is_skipped(validator, tmp_path, link, target):
    """A dangling or looping link holds nothing to grade, located or not."""
    documents = _connector_package()
    _write(tmp_path, documents)
    (tmp_path / link).symlink_to(tmp_path / link if target == "self" else tmp_path / target)
    assert sorted(validator.read_package(tmp_path, "connector-package")) == sorted(documents)


def test_a_located_name_that_is_not_a_regular_file_is_not_read(validator, tmp_path):
    """A FIFO at a document's location would block a read forever; a name that
    holds no file holds no document."""
    import os
    import signal

    documents = _connector_package()
    del documents["definition/type-map.json"]
    _write(tmp_path, documents)
    os.mkfifo(tmp_path / "definition/type-map.json")

    def _stalled(_signum, _frame):
        raise AssertionError("the walk opened a FIFO")

    previous = signal.signal(signal.SIGALRM, _stalled)
    signal.alarm(10)
    try:
        findings = validator.validate_package_at(tmp_path, "connector-package")["findings"]
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert _at(findings, "read-map-missing") == ["definition/connector.json#"], findings


@pytest.mark.parametrize("linked", ["definition", "definition/endpoints"])
def test_a_symlinked_directory_is_read_under_the_path_the_package_gives_it(
        validator, tmp_path, linked):
    """Where a document sits is its path from the package root, whatever the
    filesystem stores behind a directory on that path."""
    documents = _connector_package()
    package = tmp_path / "package"
    _write(package, documents)
    moved = tmp_path / "elsewhere"
    (package / linked).rename(moved)
    (package / linked).symlink_to(moved, target_is_directory=True)
    assert set(validator.read_package(package, "connector-package")) == set(documents)


def test_a_symlink_cycle_ends_the_walk(validator, tmp_path):
    """A directory reached again through a link holds nothing the walk has not
    already read. Two links back to one ancestor double the paths at every
    level, so a walk that followed them would not finish."""
    import signal

    documents = _connector_package()
    _write(tmp_path, documents)
    for name in ("a", "b"):
        (tmp_path / "definition/endpoints" / name).symlink_to(
            tmp_path / "definition", target_is_directory=True)

    def _stalled(_signum, _frame):
        raise AssertionError("the walk followed a symlink cycle")

    previous = signal.signal(signal.SIGALRM, _stalled)
    signal.alarm(10)
    try:
        keys = set(validator.read_package(tmp_path, "connector-package"))
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert keys == set(documents)


# ---------------------------------------------------------------------------
# Connector package checks, each at the document it is about.
# ---------------------------------------------------------------------------

def _connector_findings(validator, documents: dict) -> list:
    return validator.validate_package(_request("connector-package", documents))["findings"]


def test_a_connector_without_a_type_map_is_reported_at_the_connector(validator):
    documents = _connector_package()
    del documents["definition/type-map.json"]
    assert _at(_connector_findings(validator, documents), "read-map-missing") == [
        "definition/connector.json#"]


def test_an_unreadable_type_map_does_not_also_report_it_missing(validator):
    texts = {key: json.dumps(doc) for key, doc in _connector_package().items()}
    texts["definition/type-map.json"] = "{not json"
    findings = validator.validate_package(
        ValidatePackageRequest(package="connector-package", documents=texts))["findings"]
    assert _at(findings, "unreadable-document") == ["definition/type-map.json#"]
    assert not _at(findings, "read-map-missing"), findings


def test_an_api_connector_without_endpoints_is_reported(validator):
    documents = _connector_package()
    del documents["definition/endpoints/v1__records.json"]
    assert _at(_connector_findings(validator, documents), "endpoints-missing") == [
        "definition/connector.json#"]


def test_an_endpoint_named_apart_from_its_id_is_reported_at_it(validator):
    documents = _connector_package()
    documents["definition/endpoints/other.json"] = documents.pop("definition/endpoints/v1__records.json")
    paths = [f["path"] for f in _connector_findings(validator, documents) if f.get("rule") == "RULE-PKG-031"]
    assert paths and all(p.startswith("definition/endpoints/other.json#") for p in paths), paths


def test_duplicate_endpoint_ids_are_reported(validator):
    documents = {**_connector_package(), "definition/endpoints/copy.json": _api_endpoint()}
    assert [f for f in _connector_findings(validator, documents) if f.get("rule") == "RULE-PKG-032"]


def test_an_unresolved_transport_ref_is_reported_at_the_endpoint(validator):
    documents = _connector_package()
    endpoint = documents["definition/endpoints/v1__records.json"]
    endpoint["operations"]["read"]["request"]["transport_ref"] = "nowhere"
    paths = [f["path"] for f in _connector_findings(validator, documents) if f.get("rule") == "RULE-ENDP-047"]
    assert paths and all(p.startswith("definition/endpoints/v1__records.json#") for p in paths), paths


def test_an_uncovered_native_type_is_reported_at_the_endpoint(validator):
    documents = {**_connector_package(),
                 "definition/endpoints/v2__w.json": _api_endpoint("v2__w", "/v2/w", "BOOLEAN", "Boolean")}
    assert _at(_connector_findings(validator, documents), "native-type-unresolved") == [
        "definition/endpoints/v2__w.json#/operations/read/response/schema/items/properties/a"]


def _broken_connector_packages() -> dict:
    """Connector packages each carrying at least one finding, between them
    about the connector, the map, an endpoint, a key that needs encoding, and a
    document that never parsed. Values are documents, or text sent verbatim."""
    base = _connector_package()
    uncovered = _api_endpoint("v2__widgets", "/v2/widgets", native="BOOLEAN", arrow="Boolean")
    return {
        "mistyped connector, uncovered endpoint": {
            **base, "definition/connector.json": {**base["definition/connector.json"], "display_name": 7},
            "definition/endpoints/v2__widgets.json": uncovered},
        "key needing encoding": {**base, "definition/endpoints/v2 widgets.json": uncovered},
        "map missing": {k: v for k, v in base.items() if k != "definition/type-map.json"},
        "endpoints missing": {k: v for k, v in base.items() if not k.startswith("definition/endpoints/")},
        "map unparseable": {**base, "definition/type-map.json": "{not json"},
        "connector unparseable": {**base, "definition/connector.json": "{not json"},
    }


def _texts(documents: dict) -> dict:
    return {key: doc if isinstance(doc, str) else json.dumps(doc) for key, doc in documents.items()}


@pytest.mark.parametrize("name", sorted(_broken_connector_packages()))
def test_every_package_finding_names_a_submitted_document_or_the_package(validator, name):
    from urllib.parse import unquote

    texts = _texts(_broken_connector_packages()[name])
    findings = validator.validate_package(
        ValidatePackageRequest(package="connector-package", documents=texts))["findings"]
    assert findings, "no findings: nothing was measured"
    for f in findings:
        reference, sep, pointer = f["path"].partition("#")
        assert (not sep and not reference) or unquote(reference) in texts, f
        assert pointer == "" or (pointer.startswith("/") and pointer != "/"), f


@pytest.mark.parametrize("name", sorted(_broken_connector_packages()))
def test_findings_do_not_depend_on_the_order_documents_arrive_in(validator, name):
    texts = _texts(_broken_connector_packages()[name])
    forward = validator.validate_package(
        ValidatePackageRequest(package="connector-package", documents=texts))
    backward = validator.validate_package(ValidatePackageRequest(
        package="connector-package", documents=dict(reversed(list(texts.items())))))
    assert forward == backward


# ---------------------------------------------------------------------------
# Connection and pipeline package checks.
# ---------------------------------------------------------------------------

def test_a_database_endpoint_named_apart_from_its_id_is_reported(validator):
    documents = _connection_package()
    documents["definition/endpoints/other.json"] = documents.pop(f"definition/endpoints/{_EID}.json")
    result = validator.validate_package(_request("connection-package", documents))
    paths = [f["path"] for f in result["findings"] if f.get("rule") == "RULE-PKG-031"]
    assert paths and all(p.startswith("definition/endpoints/other.json#") for p in paths), result


def test_a_pipeline_naming_a_stream_the_package_lacks_is_reported(validator):
    documents = _pipeline_package()
    documents["pipeline.json"]["streams"].append("55555555-5555-4555-8555-555555555555_v1")
    findings = validator.validate_package(_request("pipeline-package", documents))["findings"]
    assert [f["path"] for f in findings if f["kind"] == "fail"] == ["pipeline.json#/streams/1"], findings


def test_a_stream_of_another_pipeline_is_reported_at_the_stream(validator):
    documents = _pipeline_package()
    documents["streams/orders.json"]["pipeline_id"] = "66666666-6666-4666-8666-666666666666"
    findings = validator.validate_package(_request("pipeline-package", documents))["findings"]
    assert "streams/orders.json#/pipeline_id" in [f["path"] for f in findings if f["kind"] == "fail"], findings


# ---------------------------------------------------------------------------
# CLI: the caller names the kind or the package.
# ---------------------------------------------------------------------------

def test_cli_grades_a_document_as_its_named_kind(validator_cli, tmp_path):
    path = tmp_path / "anything.json"
    path.write_text(json.dumps(_stream()))
    assert validator_cli.run("--document", str(path), "--kind", "stream").returncode == 0
    assert validator_cli.run("--document", str(path), "--kind", "connector").returncode == 1


def test_cli_grades_a_package(validator_cli, tmp_path):
    _write(tmp_path, _connector_package())
    result = validator_cli.run("--package", str(tmp_path), "--kind", "connector-package")
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout) == {"passed": True, "findings": []}


@pytest.mark.parametrize("argv", [
    ("--document", "x.json"),
    ("--document", "x.json", "--kind", "connector-package"),
    ("--package", ".", "--kind", "connector"),
])
def test_cli_refuses_a_kind_outside_the_vocabulary(validator_cli, argv):
    result = validator_cli.run(*argv)
    assert result.returncode == 2, result


@pytest.mark.parametrize("target", ["document", "package"])
def test_cli_reports_only_the_read_as_unreadable(validator, tmp_path, monkeypatch, target):
    """A defect raised while grading is this package's own, and propagates:
    caught beside the read, it would tell the author their document cannot be
    read."""
    from analitiq.validator import _core, document_set

    def defect(*_args, **_kwargs):
        raise ValueError("a grading defect")

    if target == "document":
        (tmp_path / "stream.json").write_text(json.dumps(_stream()))
        argv = ["--document", str(tmp_path / "stream.json"), "--kind", "stream"]
        monkeypatch.setattr(_core, "validate_document", defect)
    else:
        _write(tmp_path, _connector_package())
        argv = ["--package", str(tmp_path), "--kind", "connector-package"]
        monkeypatch.setattr(document_set, "grade_package", defect)
    monkeypatch.setattr("sys.argv", ["analitiq-validate", *argv])
    with pytest.raises(ValueError, match="a grading defect"):
        _core.main()
