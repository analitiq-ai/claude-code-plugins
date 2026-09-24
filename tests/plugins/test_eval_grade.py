"""The eval grader's file selection: what a file holder puts in a request."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _grade():
    spec = importlib.util.spec_from_file_location("eval_grade", REPO_ROOT / "evals" / "grade.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grade = _grade()


def _write(root: Path, files: dict[str, str]) -> None:
    for key, text in files.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_a_package_request_carries_only_located_documents(tmp_path):
    _write(tmp_path, {
        "connection.json": "{}",
        "definition/type-map.json": "{}",
        ".secrets/credentials.json": '{"token": "x"}',
        ".venv/lib/site.json": "{}",
        "notes.md": "n",
    })
    request = grade.package_request(tmp_path, "connection")
    assert sorted(request.documents.root) == ["connection.json", "definition/type-map.json"]


def test_a_workspace_request_carries_only_located_documents(tmp_path):
    _write(tmp_path, {
        "pipelines/p/pipeline.json": "{}",
        "pipelines/manifest.json": "{}",
        "connections/c/connection.json": "{}",
        "connections/c/.secrets/credentials.json": '{"token": "x"}',
        "connectors/k/definition/connector.json": "{}",
        "connectors/k/connector.py": "",
        "README.md": "r",
    })
    request = grade.workspace_request(tmp_path)
    assert sorted(request.documents.root) == [
        "connections/c/connection.json",
        "connectors/k/definition/connector.json",
        "pipelines/manifest.json",
        "pipelines/p/pipeline.json",
    ]


def test_a_document_is_carried_as_the_text_on_disk(tmp_path):
    text = '{\n  "connection_id": "c"\n}\n'
    _write(tmp_path, {"connection.json": text})
    assert grade.package_request(tmp_path, "connection").documents.root == {"connection.json": text}


def test_a_linked_file_is_not_followed(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    package = tmp_path / "pkg"
    _write(package, {"connection.json": "{}"})
    (package / "definition").mkdir()
    (package / "definition" / "type-map.json").symlink_to(outside)
    assert sorted(grade.package_request(package, "connection").documents.root) == ["connection.json"]


def test_main_prints_the_envelope_and_exits_by_the_verdict(tmp_path, capsys):
    _write(tmp_path, {"connection.json": "not json"})
    assert grade.main(["package", str(tmp_path), "connection"]) == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["passed"] is False and envelope["findings"]
