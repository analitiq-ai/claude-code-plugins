"""`validate_tree` — one path-free entry point over a keyed document tree.

The path-based entry points (`validate_document(..., doc_path=...)`, the
`analitiq-validate` CLI) and the tree entry point are two readers over the same
checks, so the first thing pinned here is that they agree byte for byte: every
fixture this repo ships, staged on disk and handed over as a tree, yields the
same envelope by both routes — finding order and `path` strings included. A
consumer that receives a tree over the wire is then graded exactly as the
author's own checkout is.

The rest pins what a tree can do that a bare document cannot (the
whole-connector checks), what happens to a key whose text does not parse, that
one crashing stage costs exactly one finding, and which trees are refused.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
#: Generous, because the assertion is that the read returns at all: a FIFO
#: with no writer never does, so any finite wait separates the two outcomes.
READ_DEADLINE_SECONDS = 30.0
CORPUS = Path(__file__).resolve().parent / "corpus"
RULE_FIXTURES = REPO_ROOT / "packages" / "contract-models" / "tests" / "fixtures" / "rules"
CONNECTOR_EXAMPLES = REPO_ROOT / "plugins" / "analitiq-connector-builder" / "skills"
API_EXAMPLE = CONNECTOR_EXAMPLES / "connector-spec-api" / "examples" / "api-key"
DB_EXAMPLE = CONNECTOR_EXAMPLES / "connector-spec-db" / "examples" / "postgresql"

SRC = "22222222-2222-4222-8222-222222222222"
DST = "33333333-3333-4333-8333-333333333333"
PID = "11111111-1111-4111-8111-111111111111"
SID = "44444444-4444-4444-8444-444444444444"
H = "https://schemas.analitiq.ai"


def _fixture_files() -> list[Path]:
    files = sorted(CORPUS.glob("*.json")) + sorted(RULE_FIXTURES.rglob("*.json"))
    assert files, "no fixtures found — the corpus or the rule fixtures moved"
    return files


def _text_tree(root: Path) -> dict[str, str]:
    """Every file under `root`, keyed by its POSIX path relative to `root`."""
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _canonical(envelope: dict) -> str:
    return json.dumps(envelope, sort_keys=True)


def _stage_connector(example_dir: Path, dest: Path) -> Path:
    """An example connector laid out as `definition/` — the registry layout."""
    definition = dest / "definition"
    definition.mkdir(parents=True)
    [body] = example_dir.glob("*.example.json")
    shutil.copy(body, definition / "connector.json")
    for name in ("type-map-read.json", "type-map-write.json"):
        if (example_dir / name).exists():
            shutil.copy(example_dir / name, definition / name)
    if (example_dir / "endpoints").is_dir():
        shutil.copytree(example_dir / "endpoints", definition / "endpoints")
    return definition / "connector.json"


# ---------------------------------------------------------------------------
# Byte identity: the path route and the tree route are one set of checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", _fixture_files(),
                         ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_every_fixture_grades_the_same_by_path_and_by_tree(validator, tmp_path, fixture):
    doc_path = tmp_path / "definition" / "connector.json"
    doc_path.parent.mkdir()
    shutil.copy(fixture, doc_path)
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    by_path = validator.diagnostics(validator.validate_document(doc, doc_path=doc_path))
    by_tree = validator.validate_tree(_text_tree(tmp_path))
    assert _canonical(by_path) == _canonical(by_tree)


@pytest.mark.parametrize("example", [API_EXAMPLE, DB_EXAMPLE], ids=lambda p: p.name)
def test_connector_tree_grades_the_same_by_path_by_cli_and_by_tree(
        validator, validator_cli, tmp_path, example):
    doc_path = _stage_connector(example, tmp_path)
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    by_path = validator.diagnostics(validator.validate_document(doc, doc_path=doc_path))
    by_tree = validator.validate_tree(_text_tree(tmp_path))
    result = validator_cli.run("--document", str(doc_path))
    assert result.returncode == 0, result.stderr
    assert _canonical(by_path) == _canonical(by_tree)
    assert _canonical(json.loads(result.stdout)) == _canonical(by_tree)


# ---------------------------------------------------------------------------
# A connector tree runs the whole-connector checks; a bare document cannot
# ---------------------------------------------------------------------------

def _api_tree(tmp_path: Path) -> dict[str, str]:
    _stage_connector(API_EXAMPLE, tmp_path)
    return _text_tree(tmp_path)


def _ids(envelope: dict, severity: str | None = None) -> list[str]:
    return [f["validator"] for f in envelope["findings"]
            if severity is None or f["severity"] == severity]


def test_connector_tree_is_clean_when_the_shipped_example_is(validator, tmp_path):
    assert validator.validate_tree(_api_tree(tmp_path))["passed"]


def _first_endpoint_key(tree: dict[str, str]) -> str:
    return min(k for k in tree if k.startswith("definition/endpoints/"))


def test_connector_tree_reports_a_duplicate_endpoint_id(validator, tmp_path):
    tree = _api_tree(tmp_path)
    original = _first_endpoint_key(tree)
    tree["definition/endpoints/copy.json"] = tree[original]
    envelope = validator.validate_tree(tree)
    assert not envelope["passed"]
    assert "endpoint-id-unique" in _ids(envelope, "error"), envelope["findings"]


def test_connector_tree_reports_a_misnamed_endpoint_file(validator, tmp_path):
    tree = _api_tree(tmp_path)
    original = _first_endpoint_key(tree)
    tree["definition/endpoints/misnamed.json"] = tree.pop(original)
    envelope = validator.validate_tree(tree)
    assert "endpoint-filename" in _ids(envelope, "error"), envelope["findings"]


def test_connector_tree_reports_a_missing_read_map(validator, tmp_path):
    tree = _api_tree(tmp_path)
    del tree["definition/type-map-read.json"]
    envelope = validator.validate_tree(tree)
    assert "type-map-coverage" in _ids(envelope, "error"), envelope["findings"]


def test_bare_connector_document_still_reports_coverage_skipped(validator):
    [body] = API_EXAMPLE.glob("*.example.json")
    findings = validator.validate_document(json.loads(body.read_text(encoding="utf-8")))
    skipped = [f for f in findings if f["validator"] == "type-map-coverage"]
    assert [(f["severity"], f["path"]) for f in skipped] == [("warning", "/")], findings


# ---------------------------------------------------------------------------
# A pipeline tree
# ---------------------------------------------------------------------------

CONN_WISE = {
    "$schema": f"{H}/connection/latest.json", "connection_id": SRC, "connector_id": "wise",
    "display_name": "Wise", "parameters": {"environment": "live"},
    "secret_refs": {"api_token": "env:ANALITIQ_WISE_API_TOKEN"},
}
CONN_PG = {
    "$schema": f"{H}/connection/latest.json", "connection_id": DST, "connector_id": "postgresql",
    "display_name": "Prod Postgres",
    "parameters": {"host": "db.example.com", "port": 5432, "database": "analytics",
                   "ssl_mode": "verify-full"},
    "secret_refs": {"password": "env:ANALITIQ_POSTGRESQL_PASSWORD"},
}
PIPELINE = {
    "$schema": f"{H}/pipeline/latest.json", "pipeline_id": PID, "display_name": "Wise to Postgres",
    "connections": {"source": SRC, "destinations": [DST]}, "streams": [SID],
    "schedule": {"type": "manual", "timezone": "UTC"}, "status": "draft",
}


def _db_endpoint():
    from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
    eid = derive_db_endpoint_id(None, "public", "orders")
    obj = build_database_object(None, "public", "orders")
    doc = {
        "$schema": f"{H}/database-endpoint/latest.json", "endpoint_id": eid,
        "display_name": "public.orders", "database_object": obj,
        "columns": [{"name": "id", "native_type": "bigint", "arrow_type": "Int64",
                     "nullable": False, "ordinal_position": 1}],
        "primary_keys": ["id"],
    }
    return eid, obj, doc


def _pipeline_tree() -> dict[str, str]:
    eid, obj, endpoint = _db_endpoint()
    stream = {
        "$schema": f"{H}/stream/latest.json", "stream_id": SID, "pipeline_id": PID,
        "display_name": "orders",
        "source": {
            "endpoint_ref": {"scope": "connector", "connection_id": SRC, "endpoint_id": "transfers"},
            "replication": {"method": "incremental", "cursor_field": "updated_at"},
        },
        "destinations": [{
            "endpoint_ref": {"scope": "connection", "connection_id": DST,
                             "endpoint_id": eid, "database_object": obj},
            "write": {"mode": "upsert", "conflict_keys": ["id"]},
        }],
        "status": "draft",
    }
    docs = {
        "pipeline.json": PIPELINE,
        "streams/orders.json": stream,
        "connections/wise/connection.json": CONN_WISE,
        "connections/postgresql/connection.json": CONN_PG,
        f"connections/postgresql/definition/endpoints/{eid}.json": endpoint,
        "connectors/wise/definition/connector.json": {"connector_id": "wise", "kind": "api"},
        "connectors/wise/definition/endpoints/transfers.json": {"endpoint_id": "transfers"},
        "connectors/postgresql/definition/connector.json": {"connector_id": "postgresql",
                                                            "kind": "database"},
    }
    return {key: json.dumps(doc) for key, doc in docs.items()}


def test_pipeline_tree_draft_bundle_passes(validator):
    envelope = validator.validate_tree(_pipeline_tree())
    assert envelope["passed"], envelope["findings"]
    assert not any(f["path"] == "/pipeline/status" for f in envelope["findings"])


def test_pipeline_tree_runs_the_referential_checks(validator):
    tree = _pipeline_tree()
    stream = json.loads(tree["streams/orders.json"])
    stream["source"]["endpoint_ref"]["connection_id"] = "99999999-9999-4999-8999-999999999999"
    tree["streams/orders.json"] = json.dumps(stream)
    envelope = validator.validate_tree(tree)
    assert "bundle-connection-ref" in _ids(envelope, "error"), envelope["findings"]


def test_pipeline_tree_warns_on_an_unpublished_connector_endpoint(validator):
    tree = _pipeline_tree()
    stream = json.loads(tree["streams/orders.json"])
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"
    tree["streams/orders.json"] = json.dumps(stream)
    envelope = validator.validate_tree(tree)
    warned = [f for f in envelope["findings"] if f["validator"] == "connector-endpoint-ref"]
    assert [(f["severity"], f["path"]) for f in warned] == [("warning", "/streams/0/source/endpoint_ref")]
    assert "transfers" in warned[0]["message"]
    assert envelope["passed"]


def test_pipeline_tree_rejects_the_pre_split_type_map_name(validator):
    tree = _pipeline_tree()
    tree["connections/postgresql/definition/type-map.json"] = "[]"
    envelope = validator.validate_tree(tree)
    legacy = [f for f in envelope["findings"] if f["validator"] == "connection-type-map"]
    assert [(f["severity"], f["path"]) for f in legacy] == [
        ("error", "connections/postgresql/definition/type-map.json")]


def test_pipeline_tree_active_status_requires_runnability(validator):
    tree = _pipeline_tree()
    tree["pipeline.json"] = json.dumps({**PIPELINE, "status": "active"})
    envelope = validator.validate_tree(tree)
    assert not envelope["passed"]
    assert "bundle-pipeline" in _ids(envelope, "error"), envelope["findings"]


# ---------------------------------------------------------------------------
# Text that does not parse is a finding naming its key
# ---------------------------------------------------------------------------

def test_malformed_stream_text_is_a_finding_naming_the_key(validator):
    tree = _pipeline_tree()
    tree["streams/orders.json"] = "{ not valid json"
    envelope = validator.validate_tree(tree)
    bad = [f for f in envelope["findings"] if f["validator"] == "document"]
    assert [(f["severity"], f["path"]) for f in bad] == [("error", "streams/orders.json")]
    assert "orders.json" in bad[0]["message"]
    assert not envelope["passed"]


def test_malformed_connector_sibling_is_reported_under_the_reading_check(validator, tmp_path):
    tree = _api_tree(tmp_path)
    tree["definition/type-map-read.json"] = "[ not valid json"
    envelope = validator.validate_tree(tree)
    assert "type-map-coverage" in _ids(envelope, "error"), envelope["findings"]


def test_malformed_root_document_is_a_finding_naming_the_key(validator, tmp_path):
    tree = _api_tree(tmp_path)
    tree["definition/connector.json"] = "{ not valid json"
    envelope = validator.validate_tree(tree)
    assert [(f["validator"], f["severity"], f["path"]) for f in envelope["findings"]] == [
        ("document", "error", "definition/connector.json")]


def test_a_parsed_value_is_taken_as_the_document(validator):
    tree = _pipeline_tree()
    tree["pipeline.json"] = json.loads(tree["pipeline.json"])
    assert validator.validate_tree(tree)["passed"]


def test_bytes_are_the_files_text(validator):
    tree = {key: text.encode("utf-8") for key, text in _pipeline_tree().items()}
    assert validator.validate_tree(tree)["passed"]


@pytest.mark.parametrize("key", ["pipeline.json", "streams/orders.json"])
def test_text_nested_past_the_parser_limit_is_a_finding_naming_the_key(validator, key):
    tree = _pipeline_tree()
    tree[key] = "[" * 100_000
    envelope = validator.validate_tree(tree)
    reads = [(f["validator"], f["severity"], f["path"]) for f in envelope["findings"]
             if f["validator"] in ("document", "adapter-crash")]
    assert reads == [("document", "error", key)], envelope["findings"]


def test_an_unreadable_member_is_reported_at_its_key_and_withholds_the_referential_pass(validator):
    tree = _pipeline_tree()
    tree["connections/postgresql/connection.json"] = validator.Unreadable("Is a directory")
    tree["connections/postgresql/definition/type-map.json"] = "[]"
    envelope = validator.validate_tree(tree)
    ids = _ids(envelope)
    assert [f["path"] for f in envelope["findings"] if f["validator"] == "document"] == [
        "connections/postgresql/connection.json"], envelope["findings"]
    # the member was there: its directory still counts, so the map beside it is graded
    assert "connection-type-map" in ids, envelope["findings"]
    # and could not be taken: no referential verdict over a bundle short of it, and no crash
    assert not any(vid.startswith("bundle-") for vid in ids), envelope["findings"]
    assert "adapter-crash" not in ids, envelope["findings"]


def test_the_two_trees_answer_the_same_at_the_root(validator, tmp_path):
    # the root is the one directory every tree has and no key names, so it is
    # where an asymmetry between the readers hides longest
    from analitiq.validator._tree import DiskTree, MemoryTree

    (tmp_path / "pipeline.json").write_text("{}", encoding="utf-8")
    (tmp_path / "streams").mkdir()
    (tmp_path / "streams" / "orders.json").write_text("{}", encoding="utf-8")
    memory = MemoryTree({"pipeline.json": "{}", "streams/orders.json": "{}"})
    disk = DiskTree(tmp_path)
    for tree in (memory, disk):
        assert tree.files("") == ["pipeline.json"], type(tree).__name__
        assert tree.files("", recursive=True) == ["pipeline.json",
                                                  "streams/orders.json"], type(tree).__name__
        assert tree.is_dir("") is True, type(tree).__name__


@pytest.mark.parametrize("occupant", ["directory", "dangling symlink"])
def test_a_key_something_unreadable_occupies_is_present_in_both_trees(
        validator, tmp_path, occupant):
    # what a check gates on is whether the author put something at the key; a
    # reader that answers "absent" for a directory or a dangling symlink turns
    # a file the author can see into a check that says nothing
    from analitiq.validator import Unreadable
    from analitiq.validator._tree import DiskTree, MemoryTree

    key = "definition/type-map-read.json"
    target = tmp_path / key
    target.parent.mkdir(parents=True)
    if occupant == "directory":
        target.mkdir()
    else:
        target.symlink_to(tmp_path / "gone.json")
    memory = MemoryTree({key: Unreadable("occupied")})
    for tree in (memory, DiskTree(tmp_path)):
        assert tree.occupied(key) is True, type(tree).__name__
        doc, error = tree.read(key)
        assert doc is None and error, type(tree).__name__


def test_a_json_key_nothing_readable_occupies_is_still_enumerated_by_both_readers(
        validator, tmp_path):
    # enumeration is what hands a directory's members to the checks that grade
    # them, so a reader dropping an occupant grades the tree short of a file
    # its author can see sitting there — and a directory holding nothing else
    # reads as empty rather than as holding one broken document
    from analitiq.validator import Unreadable
    from analitiq.validator._tree import DiskTree, MemoryTree

    endpoints = tmp_path / "definition" / "endpoints"
    endpoints.mkdir(parents=True)
    (endpoints / "orders.json").write_text("{}", encoding="utf-8")
    (endpoints / "broken.json").symlink_to(tmp_path / "gone.json")
    memory = MemoryTree({
        "definition/endpoints/orders.json": "{}",
        "definition/endpoints/broken.json": Unreadable("dangling symlink"),
    })
    expected = ["definition/endpoints/broken.json",
                "definition/endpoints/orders.json"]
    for tree in (memory, DiskTree(tmp_path)):
        assert tree.files("definition/endpoints") == expected, type(tree).__name__
        assert tree.files("definition", recursive=True) == expected, type(tree).__name__


#: Every awkward thing an author can leave at a document's key. The contract
#: is that the reader is the only thing that differs, so each of these is
#: staged on disk and handed over as a tree, and every question is put to both.
_OCCUPANTS = {
    "parsing document": (b'{"a": 1}', None),
    "text that does not parse": (b"{not json", None),
    "directory under a document name": (None, "a directory is here"),
    "dangling symlink": (None, "the target is gone"),
    "utf-16 text with a byte-order mark": ('{"a": "\u00e9"}'.encode("utf-16"), None),
}


def _stage(occupant: str, target):
    payload, _ = _OCCUPANTS[occupant]
    if occupant == "directory under a document name":
        target.mkdir()
        (target / "inside.txt").write_text("x", encoding="utf-8")
    elif occupant == "dangling symlink":
        target.symlink_to(target.parent / "gone.json")
    else:
        target.write_bytes(payload)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_named_pipe_at_a_documents_key_is_unreadable_rather_than_a_wait(
        validator, tmp_path):
    """A FIFO where a document belongs answers, and answers promptly.

    Driven in a child process with a deadline because the regression is a read
    that never returns: a plain open of a FIFO waits for a writer, and an
    authoring checkout has no reason to ever provide one. Asserting this
    in-process would hang the suite instead of failing it.
    """
    fifo = tmp_path / "type-map-read.json"
    os.mkfifo(fifo)
    program = (
        "import json, sys\n"
        "from analitiq.validator import read_document\n"
        "print(json.dumps(read_document(sys.argv[1])))\n"
    )
    # The child gets this process's own import path, so it reads the same
    # source trees the suite is grading rather than a second statement of
    # where they are.
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    try:
        result = subprocess.run([sys.executable, "-c", program, str(fifo)],
                                capture_output=True, text=True, env=env,
                                check=False, timeout=READ_DEADLINE_SECONDS)
    except subprocess.TimeoutExpired:
        pytest.fail(f"reading a FIFO did not return within "
                    f"{READ_DEADLINE_SECONDS}s — it waited for a writer")
    assert result.returncode == 0, result.stderr
    doc, problem = json.loads(result.stdout)
    assert doc is None
    assert "regular file" in problem, problem


def test_a_key_that_is_both_a_document_and_a_directory_is_refused(validator):
    # No filesystem can hold this, so no disk reader can ever be handed it.
    # Accepting it is the one way the two readers could be asked to grade
    # trees that are not the same tree: the walk reads the literal document
    # and never sees what sits beneath the same name.
    tree = dict(_pipeline_tree())
    literal = next(k for k in tree if k.startswith("streams/"))
    tree[f"{literal}/inside.json"] = "{}"
    envelope = validator.validate_tree(tree)
    assert envelope["passed"] is False
    assert envelope["findings"][0]["validator"] == "document"
    assert literal in envelope["findings"][0]["message"]


def test_a_directory_named_like_a_document_is_one_occupant_to_both_readers(
        validator, tmp_path):
    # The in-memory tree can only know this directory from a key beneath it,
    # which is the shape a single staged occupant cannot express: nothing in
    # the mapping spells `bad.json` out. Both readers still have to enumerate
    # it, or the walk that grades a directory's members grades one fewer here
    # than it does on disk and the tree passes where the checkout fails.
    from analitiq.validator._tree import DiskTree, MemoryTree

    inside = tmp_path / "streams" / "bad.json" / "inside.json"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"{}")
    memory = MemoryTree({"streams/bad.json/inside.json": b"{}"})
    for tree in (memory, DiskTree(tmp_path)):
        name = type(tree).__name__
        assert tree.occupied("streams/bad.json") is True, name
        assert tree.is_dir("streams/bad.json") is True, name
        assert tree.files("streams") == ["streams/bad.json"], name
        assert tree.files("streams", recursive=True) == [
            "streams/bad.json", "streams/bad.json/inside.json"], name
        doc, error = tree.read("streams/bad.json")
        assert doc is None and error, name


@pytest.mark.parametrize("occupant", sorted(_OCCUPANTS), ids=lambda o: o)
def test_both_readers_answer_every_question_alike_for_each_occupant(
        validator, tmp_path, occupant):
    # One table instead of a test per question, because the questions fail
    # together: a predicate that decides what counts as present is written
    # once per reader and every question that consults it inherits the answer.
    # Reasons are allowed to differ — they name the store — so `read` is
    # compared on whether a document came back, not on the wording.
    from analitiq.validator import Unreadable
    from analitiq.validator._tree import DiskTree, MemoryTree

    key = "definition/doc.json"
    target = tmp_path / key
    target.parent.mkdir(parents=True)
    _stage(occupant, target)

    payload, unreadable = _OCCUPANTS[occupant]
    memory = MemoryTree({key: Unreadable(unreadable) if unreadable else payload})
    answers = []
    for tree in (memory, DiskTree(tmp_path)):
        doc, error = tree.read(key)
        answers.append({
            "occupied": tree.occupied(key),
            "is_dir(parent)": tree.is_dir("definition"),
            "files(parent)": tree.files("definition"),
            "files(parent, recursive)": tree.files("definition", recursive=True),
            "read gave a document": doc is not None,
            "read gave a reason": bool(error),
        })
    assert answers[0] == answers[1], (
        f"{occupant}: MemoryTree and DiskTree disagree")


@pytest.mark.parametrize("payload,parses", [
    (b'{"a": 1}', True),
    ('{"a": "\u00e9"}'.encode("utf-16"), True),
    ('{"a": "\u00e9"}'.encode("utf-32"), True),
    (b"\xef\xbb\xbf" + b'{"a": 1}', True),
    (b"{not json", False),
    (b"\xff\xfe\x00nonsense", False),
], ids=["utf-8", "utf-16 with BOM", "utf-32 with BOM", "utf-8 with BOM",
        "text that does not parse", "bytes that decode to nothing"])
def test_a_document_is_read_by_what_it_declares_not_by_the_host(
        validator, tmp_path, payload, parses):
    # The encoding a file declares decides how it parses. Decoding to text
    # first would apply whatever the host defaults to, so the same file would
    # read one way in a tree handed over in memory and another off disk.
    from analitiq.validator import read_document

    path = tmp_path / "doc.json"
    path.write_bytes(payload)
    doc, problem = read_document(path)
    assert (problem is None) is parses, problem
    assert (doc is not None) is parses


def test_nesting_past_the_parsers_limit_is_a_reason_not_a_raised_error(
        validator, tmp_path):
    # The parser raises `RecursionError` here, which is not a `ValueError` and
    # so escapes a caller catching only parse errors. A document this deep is
    # unreadable the way malformed text is, and every reader must say so
    # rather than let the failure out as a crash.
    from analitiq.validator import parse_document, read_document

    deep = b"[" * 20_000 + b"]" * 20_000
    path = tmp_path / "deep.json"
    path.write_bytes(deep)
    for doc, problem in (read_document(path), parse_document(deep)):
        assert doc is None
        assert "RecursionError" in problem


def test_a_document_holding_null_is_read_without_a_reason(validator, tmp_path):
    # `None` is what a document holding `null` parses to, so a caller gating
    # on the document rather than on the reason calls a good file unreadable.
    from analitiq.validator import read_document

    path = tmp_path / "doc.json"
    path.write_bytes(b"null")
    assert read_document(path) == (None, None)


def test_a_directory_is_occupied_and_a_directory_in_both_trees(validator, tmp_path):
    # a directory key is a question either reader may be asked, so they answer
    # it together — the in-memory reader has no entry of its own to consult
    from analitiq.validator._tree import DiskTree, MemoryTree

    (tmp_path / "definition" / "endpoints").mkdir(parents=True)
    (tmp_path / "definition" / "endpoints" / "a.json").write_text("{}", encoding="utf-8")
    memory = MemoryTree({"definition/endpoints/a.json": "{}"})
    for tree in (memory, DiskTree(tmp_path)):
        assert tree.occupied("definition/endpoints") is True, type(tree).__name__
        assert tree.is_dir("definition/endpoints") is True, type(tree).__name__


def test_a_directory_holding_no_file_is_no_directory_in_either_tree(validator, tmp_path):
    # a tree of documents has no empty directories; an in-memory tree cannot
    # represent one, so a scaffolded-but-empty directory on disk must not read
    # as present either, or the same layout earns two different diagnostics
    from analitiq.validator._tree import DiskTree, MemoryTree

    (tmp_path / "definition" / "endpoints").mkdir(parents=True)
    (tmp_path / "definition" / "connector.json").write_text("{}", encoding="utf-8")
    memory = MemoryTree({"definition/connector.json": "{}"})
    for tree in (memory, DiskTree(tmp_path)):
        assert tree.is_dir("definition/endpoints") is False, type(tree).__name__


def test_validating_a_tree_does_not_write_into_the_documents_it_was_given(validator):
    # a pre-parsed document is a documented input shape, and the in-memory
    # reader hands back the caller's own object — so a stage stamping a field
    # onto it would reach out of the call and edit the caller's state
    eid, _, endpoint = _db_endpoint()
    tree = {key: json.loads(text) for key, text in _pipeline_tree().items()}
    tree[f"connections/postgresql/definition/endpoints/{eid}.json"] = endpoint
    before = json.dumps(endpoint, sort_keys=True)
    validator.validate_tree(tree)
    assert json.dumps(endpoint, sort_keys=True) == before


@pytest.mark.parametrize("key", ["../escape.json", "/etc/passwd", "a/../../b.json"])
def test_a_disk_tree_refuses_a_key_that_is_not_a_tree_key(validator, tmp_path, key):
    # the reader joins a key onto its root, so a key that steps out of the tree
    # would read a file outside it; `validate_tree` screens its own input, and
    # this is the same refusal one level down, for a caller that does not
    from analitiq.validator._tree import DiskTree

    with pytest.raises(ValueError, match="not a tree key"):
        DiskTree(tmp_path).read(key)


def test_an_unreadable_needs_a_reason(validator):
    # the reason is what the finding reports; an empty one leaves a message
    # ending in a dangling colon with nothing behind it
    from analitiq.validator import Unreadable

    with pytest.raises(ValueError, match="reason"):
        Unreadable("")


def test_a_bundle_type_map_a_directory_occupies_is_reported_not_skipped(validator):
    # the check gates on presence, so the key must survive as present even
    # though nothing can be read from it
    from analitiq.validator import Unreadable

    tree = _pipeline_tree()
    key = "connections/postgresql/definition/type-map-read.json"
    tree[key] = Unreadable("Is a directory")
    envelope = validator.validate_tree(tree)
    bad = [(f["severity"], f["path"]) for f in envelope["findings"]
           if f["validator"] == "connection-type-map"]
    assert bad == [("error", key)], envelope["findings"]


def test_an_absent_key_reads_as_a_failure_in_both_trees(validator, tmp_path):
    from analitiq.validator._tree import DiskTree, MemoryTree

    for tree in (MemoryTree({"pipeline.json": "{}"}), DiskTree(tmp_path)):
        doc, error = tree.read("streams/absent.json")
        assert doc is None and error, type(tree).__name__


def test_a_connector_endpoint_is_known_by_its_declared_id_as_well_as_its_stem(validator):
    # a well-formed connector keeps the two equal; the set records both so a
    # malformed one that let them diverge does not warn on a ref that resolves
    tree = _pipeline_tree()
    key = "connectors/wise/definition/endpoints/transfers.json"
    tree[key] = json.dumps({"endpoint_id": "transfers_v2"})
    stream = json.loads(tree["streams/orders.json"])
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transfers_v2"
    tree["streams/orders.json"] = json.dumps(stream)
    envelope = validator.validate_tree(tree)
    assert "connector-endpoint-ref" not in _ids(envelope), envelope["findings"]


def test_connector_endpoints_without_a_connector_document_still_verify_refs(validator):
    # the endpoint set is keyed by the directory slug; the connector document
    # supplies only the id alias, so its absence costs the alias, not the check
    tree = _pipeline_tree()
    del tree["connectors/wise/definition/connector.json"]
    stream = json.loads(tree["streams/orders.json"])
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"
    tree["streams/orders.json"] = json.dumps(stream)
    envelope = validator.validate_tree(tree)
    ids = _ids(envelope)
    assert "adapter-crash" not in ids, envelope["findings"]
    assert "connector-endpoint-ref" in ids, envelope["findings"]


# ---------------------------------------------------------------------------
# One crashing stage costs exactly one finding
# ---------------------------------------------------------------------------

def test_one_crashing_stage_is_one_finding_and_the_rest_still_report(validator, monkeypatch):
    from analitiq.validator import trees

    tree = _pipeline_tree()
    tree["connections/postgresql/definition/type-map.json"] = "[]"  # decided first
    stream = json.loads(tree["streams/orders.json"])
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"  # decided last
    tree["streams/orders.json"] = json.dumps(stream)

    original = trees._connection_type_map_findings

    def boom(tree_, slug, findings):
        if slug == "wise":
            raise TypeError("simulated crash")
        return original(tree_, slug, findings)

    monkeypatch.setattr(trees, "_connection_type_map_findings", boom)
    envelope = validator.validate_tree(tree)
    crashes = [f for f in envelope["findings"] if f["validator"] == "adapter-crash"]
    assert [(f["severity"], f["path"]) for f in crashes] == [("error", "connections/wise")]
    assert "TypeError" in crashes[0]["message"] and "simulated crash" in crashes[0]["message"]
    ids = _ids(envelope)
    assert "connection-type-map" in ids, envelope["findings"]
    assert "connector-endpoint-ref" in ids, envelope["findings"]


class _Silent(Exception):
    """An exception whose `str()` is empty, like a bare `MemoryError()`."""


class _BrokenStr(Exception):
    def __str__(self):
        raise RuntimeError("broken __str__")


@pytest.mark.parametrize("exc,message", [(_Silent(), "_Silent"), (_BrokenStr(), "_BrokenStr")])
def test_a_crash_finding_names_the_type_when_there_is_no_detail(
        validator, monkeypatch, exc, message):
    # the containment finding is built inside a guard, so an exception with no
    # detail must not leave a dangling ": " and one whose own __str__ raises
    # must not become a second crash the guard cannot contain
    from analitiq.validator import trees

    def boom(tree_, slug, findings):
        raise exc

    monkeypatch.setattr(trees, "_connection_type_map_findings", boom)
    envelope = validator.validate_tree(_pipeline_tree())
    crashes = [f["message"] for f in envelope["findings"] if f["validator"] == "adapter-crash"]
    assert crashes and all(c == message for c in crashes), envelope["findings"]


def test_a_crash_that_excludes_a_member_skips_the_referential_pass(validator, monkeypatch):
    from analitiq.validator import _tree

    tree = _pipeline_tree()
    original = _tree.MemoryTree.read

    def boom(self, key):
        if key == "streams/orders.json":
            raise TypeError("simulated crash")
        return original(self, key)

    monkeypatch.setattr(_tree.MemoryTree, "read", boom)
    envelope = validator.validate_tree(tree)
    crashes = [f["path"] for f in envelope["findings"] if f["validator"] == "adapter-crash"]
    assert crashes == ["streams/orders.json", "pipeline"], envelope["findings"]
    assert "bundle-stream-ref" not in _ids(envelope), envelope["findings"]


# ---------------------------------------------------------------------------
# Which trees are refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["/definition/connector.json", "definition/../connector.json",
                                 "", "definition//connector.json", "./pipeline.json"])
def test_a_bad_key_refuses_the_whole_tree(validator, key):
    envelope = validator.validate_tree({key: "{}", "pipeline.json": "{}"})
    assert [(f["validator"], f["severity"], f["path"]) for f in envelope["findings"]] == [
        ("document", "error", "")]
    assert repr(key) in envelope["findings"][0]["message"]


def test_a_tree_matching_neither_layout_is_refused(validator):
    envelope = validator.validate_tree({"connector.json": "{}"})
    assert [(f["validator"], f["severity"]) for f in envelope["findings"]] == [("document", "error")]


def test_a_tree_matching_both_layouts_is_refused(validator):
    envelope = validator.validate_tree({"pipeline.json": "{}", "definition/connector.json": "{}"})
    assert [(f["validator"], f["severity"]) for f in envelope["findings"]] == [("document", "error")]


# ---------------------------------------------------------------------------
# The envelope, entity routing and gap resolution
# ---------------------------------------------------------------------------

def test_diagnostics_passes_only_without_an_error(validator):
    warning = validator.finding("document", "warning", "", "w")
    error = validator.finding("document", "error", "", "e")
    assert validator.diagnostics([]) == {"passed": True, "findings": []}
    assert validator.diagnostics(iter([warning]))["passed"] is True
    assert validator.diagnostics([warning, error])["passed"] is False


def test_entities_are_the_six_routes(validator):
    assert validator.ENTITIES == ("pipeline", "stream", "connection", "database_endpoint",
                                  "type_map_read", "type_map_write")


def test_entity_routing_reaches_the_model_without_the_discriminating_key(validator):
    # No `connector_id`: shape detection would call this an unrecognised
    # document; the entity route grades it as the connection it was meant to be.
    findings = validator.validate_document({"$schema": f"{H}/connection/latest.json"},
                                           entity="connection")
    assert findings and all(f["validator"] == "contract-model" for f in findings), findings
    assert any(f["path"] == "/connector_id" for f in findings), findings


def test_entity_routing_grades_a_type_map_in_its_own_direction(validator, tmp_path):
    write_rules = [{"match": "regex", "arrow_type": r"^Decimal(128|256)\((?<p>\d+),\s*(?<s>\d+)\)$",
                    "native_type": "NUMERIC(${p}, ${s})"}]
    path = tmp_path / "type-map-write.json"
    assert not validator.validate_document(write_rules, doc_path=path, entity="type_map_write")
    misnamed = validator.validate_document(write_rules, doc_path=tmp_path / "type-map.json",
                                           entity="type_map_write")
    assert [f["validator"] for f in misnamed] == ["connection-type-map"], misnamed
    assert not any(f["validator"] == "type-map-write-coverage"
                   for f in validator.validate_document(write_rules, entity="type_map_write"))


def test_an_unknown_entity_is_a_caller_error(validator):
    with pytest.raises(ValueError, match="entity"):
        validator.validate_document({}, entity="connector")


READ_MAP = [
    {"match": "exact", "native_type": "CITEXT", "arrow_type": "Utf8"},
    {"match": "regex",
     "native_type": r"^NUMERIC\((?<precision>[1-9]|[12]\d|3[0-8]),\s*(?<scale>\d|[12]\d|3[0-8])\)$",
     "arrow_type": "Decimal128(${precision}, ${scale})"},
]
WRITE_MAP = [
    {"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"},
    {"match": "regex", "arrow_type": r"^Decimal(128|256)\((?<p>\d+),\s*(?<s>\d+)\)$",
     "native_type": "NUMERIC(${p}, ${s})"},
]


def test_resolve_type_map_gaps_read(validator):
    assert validator.resolve_type_map_gaps("read", ["citext", "vector(3)", "numeric(10,2)", "citext"],
                                           [READ_MAP]) == {
        "direction": "read",
        "resolved": {"citext": "Utf8", "vector(3)": None, "numeric(10,2)": "Decimal128(10, 2)"},
        "gaps": ["vector(3)"],
    }


def test_resolve_type_map_gaps_write_is_case_preserving_and_primary_first(validator):
    primary = [{"match": "exact", "arrow_type": "Utf8", "native_type": "CITEXT"}]
    assert validator.resolve_type_map_gaps("write", ["Utf8", "utf8", "Decimal128(20, 4)"],
                                           [primary, WRITE_MAP]) == {
        "direction": "write",
        "resolved": {"Utf8": "CITEXT", "utf8": None, "Decimal128(20, 4)": "NUMERIC(20, 4)"},
        "gaps": ["utf8"],
    }


@pytest.mark.parametrize("maps,match", [
    ([{"match": "exact"}], "not a JSON array"),
    ([[{"match": "exact", "native_type": "CITEXT"}]], "not a valid read type map"),
    ([WRITE_MAP], "not a valid read type map"),
])
def test_resolve_type_map_gaps_refuses_a_broken_map(validator, maps, match):
    with pytest.raises(ValueError, match=match):
        validator.resolve_type_map_gaps("read", ["citext"], maps)


def test_resolve_type_map_gaps_refuses_an_unknown_direction(validator):
    with pytest.raises(ValueError, match="direction"):
        validator.resolve_type_map_gaps("sideways", ["citext"], [READ_MAP])


def test_the_lifted_ids_are_registered(validator):
    assert {"adapter-crash", "connection-type-map", "connector-endpoint-ref"} <= validator.VALIDATOR_IDS


# ---------------------------------------------------------------------------
# Where each pipeline-tree guard sits: a crash costs exactly the unit it is in
# ---------------------------------------------------------------------------

def _memory_tree(validator, documents):
    from analitiq.validator._tree import MemoryTree
    return MemoryTree(documents)


def _crash_reading(monkeypatch, key):
    """Make the tree's read of `key` raise, the way a pathologically deep
    document would inside the parse."""
    from analitiq.validator import _tree

    original = _tree.MemoryTree.read

    def boom(self, key_):
        if key_ == key:
            raise TypeError("simulated crash")
        return original(self, key_)

    monkeypatch.setattr(_tree.MemoryTree, "read", boom)


def test_endpoint_read_crash_preserves_its_sibling_endpoint(validator, monkeypatch):
    from analitiq.validator import trees

    eid, _, _ = _db_endpoint()
    tree = _pipeline_tree()
    second = json.loads(tree[f"connections/postgresql/definition/endpoints/{eid}.json"])
    second["endpoint_id"] = "customers"
    tree["connections/postgresql/definition/endpoints/customers.json"] = json.dumps(second)
    _crash_reading(monkeypatch, f"connections/postgresql/definition/endpoints/{eid}.json")

    bundle, findings, complete, crashed = trees._assemble_bundle(
        _memory_tree(validator, tree), json.loads(tree["pipeline.json"]))
    assert not complete and crashed
    assert {e["endpoint_id"] for e in bundle["endpoints"]} == {"customers"}
    assert [f["path"] for f in findings if f["validator"] == "adapter-crash"] == [
        f"connections/postgresql/definition/endpoints/{eid}.json"]


def test_connector_read_crash_preserves_the_other_connectors_identity(validator, monkeypatch):
    from analitiq.validator import trees

    tree = _pipeline_tree()
    tree["connectors/wise/definition/connector.json"] = json.dumps(
        {"connector_id": "wise-live", "kind": "api"})
    _crash_reading(monkeypatch, "connectors/postgresql/definition/connector.json")

    bundle, findings, complete, crashed = trees._assemble_bundle(
        _memory_tree(validator, tree), json.loads(tree["pipeline.json"]))
    # the crash cost only the connector_id alias: the slug is recorded before
    # the guarded read, and a connection could still name the id, so assembly
    # is marked incomplete out of caution
    assert not complete and crashed
    assert [f["path"] for f in findings if f["validator"] == "adapter-crash"] == [
        "connectors/postgresql"]
    assert {"postgresql", "wise", "wise-live"} <= set(bundle["connectors"])


def test_stream_read_crash_preserves_siblings_and_later_sections(validator, monkeypatch):
    tree = _pipeline_tree()
    tree["streams/second.json"] = json.dumps(
        {**json.loads(tree["streams/orders.json"]), "stream_id": "55555555-5555-4555-8555-555555555555"})
    tree["connections/postgresql/definition/type-map.json"] = "[]"
    _crash_reading(monkeypatch, "streams/orders.json")

    envelope = validator.validate_tree(tree)
    ids = _ids(envelope)
    crashes = [f["path"] for f in envelope["findings"] if f["validator"] == "adapter-crash"]
    # the connections section, after the crashed stream, still decided its finding
    assert "connection-type-map" in ids, envelope["findings"]
    # the bundle is short the crashed stream the pipeline still references, so the
    # referential pass is skipped rather than blame a reference that never broke
    assert "bundle-stream-ref" not in ids, envelope["findings"]
    assert crashes == ["streams/orders.json", "pipeline"], envelope["findings"]


def test_type_map_direction_crash_preserves_legacy_finding_and_sibling_direction(validator, monkeypatch):
    from analitiq.validator import trees

    tree = _pipeline_tree()
    site = "connections/postgresql/definition"
    tree[f"{site}/type-map.json"] = "[]"
    tree[f"{site}/type-map-read.json"] = json.dumps(READ_MAP)
    tree[f"{site}/type-map-write.json"] = json.dumps(
        [{"match": "exact", "native_type": "citext", "arrow_type": "utf8"}])  # invalid casing
    original = trees._validate_connection_type_map

    def boom(direction, doc, where, schema_url=None):
        if direction == "read":
            raise TypeError("simulated crash")
        return original(direction, doc, where, schema_url)

    monkeypatch.setattr(trees, "_validate_connection_type_map", boom)
    envelope = validator.validate_tree(tree)
    crashes = [f["path"] for f in envelope["findings"] if f["validator"] == "adapter-crash"]
    assert crashes == [f"{site}/type-map-read.json"], envelope["findings"]
    legacy = [f for f in envelope["findings"]
              if f["validator"] == "connection-type-map" and f["path"] == f"{site}/type-map.json"]
    assert legacy, envelope["findings"]  # decided before the crash, still present
    bad_write = [f for f in envelope["findings"] if f["validator"] == "contract-model"
                 and f["path"].startswith(f"{site}/type-map-write.json")]
    assert bad_write, envelope["findings"]  # processed after the crash, still got its turn


def test_filename_gate_crash_keeps_the_endpoint_in_the_bundle(validator, monkeypatch):
    from analitiq.validator import trees

    eid, _, _ = _db_endpoint()
    tree = _pipeline_tree()
    tree["connections/postgresql/definition/type-map.json"] = "[]"
    original = trees.endpoint_filename_findings

    def boom(endpoint, filename):
        if filename == f"{eid}.json":
            raise TypeError("simulated crash")
        return original(endpoint, filename)

    monkeypatch.setattr(trees, "endpoint_filename_findings", boom)
    envelope = validator.validate_tree(tree)
    ids = _ids(envelope)
    assert "adapter-crash" in ids, envelope["findings"]
    # the endpoint kept its place -> no false bundle-endpoint-ref for the
    # stream's legitimate reference to it; the connection's trailing type-map
    # check still ran
    assert "bundle-endpoint-ref" not in ids, envelope["findings"]
    assert "connection-type-map" in ids, envelope["findings"]


def test_connector_endpoint_sets_crash_isolated_to_one_connector(validator, monkeypatch):
    tree = _pipeline_tree()
    tree["connectors/postgresql/definition/endpoints/orders.json"] = json.dumps({"endpoint_id": "orders"})
    stream = json.loads(tree["streams/orders.json"])
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"
    tree["streams/orders.json"] = json.dumps(stream)
    _crash_reading(monkeypatch, "connectors/postgresql/definition/endpoints/orders.json")

    envelope = validator.validate_tree(tree)
    crashes = [f["path"] for f in envelope["findings"] if f["validator"] == "adapter-crash"]
    assert crashes == ["connectors/postgresql/definition/endpoints"], envelope["findings"]
    # wise's connector-endpoint-ref check still ran despite postgresql's crash
    warned = [f for f in envelope["findings"] if f["validator"] == "connector-endpoint-ref"]
    assert len(warned) == 1 and "transfers" in warned[0]["message"], envelope["findings"]


def test_referential_pass_crash_preserves_the_connector_ref_warning(validator, monkeypatch):
    from analitiq.validator import trees

    tree = _pipeline_tree()
    stream = json.loads(tree["streams/orders.json"])
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"
    tree["streams/orders.json"] = json.dumps(stream)

    def boom(*a, **kw):
        raise TypeError("simulated crash")

    monkeypatch.setattr(trees, "validate_pipeline_bundle", boom)
    envelope = validator.validate_tree(tree)
    crashes = [f["path"] for f in envelope["findings"] if f["validator"] == "adapter-crash"]
    assert crashes == ["pipeline"], envelope["findings"]
    assert "connector-endpoint-ref" in _ids(envelope), envelope["findings"]


def test_one_ref_crash_preserves_the_warning_decided_before_it(validator, monkeypatch):
    import difflib

    tree = _pipeline_tree()
    first = json.loads(tree["streams/orders.json"])
    first["source"]["endpoint_ref"]["endpoint_id"] = "transferz"  # resolves first
    second = {**first, "stream_id": "55555555-5555-4555-8555-555555555555",
              "source": {**first["source"],
                         "endpoint_ref": {**first["source"]["endpoint_ref"], "endpoint_id": "wiring"}}}
    tree["streams/orders.json"] = json.dumps(first)
    tree["streams/second.json"] = json.dumps(second)
    pipeline = json.loads(tree["pipeline.json"])
    pipeline["streams"].append(second["stream_id"])
    tree["pipeline.json"] = json.dumps(pipeline)
    original = difflib.get_close_matches

    def boom(word, possibilities, *a, **kw):
        if word == "wiring":
            raise TypeError("simulated crash")
        return original(word, possibilities, *a, **kw)

    monkeypatch.setattr(difflib, "get_close_matches", boom)
    envelope = validator.validate_tree(tree)
    assert "adapter-crash" in _ids(envelope), envelope["findings"]
    warned = [f for f in envelope["findings"] if f["validator"] == "connector-endpoint-ref"]
    assert any("transfers" in w["message"] for w in warned), envelope["findings"]
