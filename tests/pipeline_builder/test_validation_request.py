"""The request builder the validator agents run: which files a request carries,
selected by the published location tables (read here from the tree this
checkout renders)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "plugins" / "analitiq-pipeline-builder" / "scripts" / "validation_request.py"
CONNECTOR_COPY = REPO_ROOT / "plugins" / "analitiq-connector-builder" / "scripts" / "validation_request.py"


def _load():
    spec = importlib.util.spec_from_file_location("validation_request", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load()


def rendered(url: str) -> dict:
    return json.loads((REPO_ROOT / "schemas" / url.removeprefix(builder.SCHEMA_HOST + "/")).read_text())


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for key, content in files.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def _build(*argv) -> dict:
    return builder.build([str(a) for a in argv], rendered)


def test_the_connector_plugin_ships_the_same_builder():
    # Each plugin is installed on its own, so each carries a copy.
    assert CONNECTOR_COPY.read_bytes() == BUILDER.read_bytes()


def test_a_package_request_carries_only_located_documents(tmp_path):
    _write(tmp_path, {
        "connection.json": "{}",
        "definition/type-map.json": "{}",
        ".secrets/credentials.json": '{"token": "x"}',
        ".venv/lib/site.json": "{}",
        "notes.md": "n",
    })
    built = _build("package", tmp_path, "connection")
    assert built["tool"] == "validate_package"
    assert built["arguments"]["package_kind"] == "connection"
    assert sorted(built["arguments"]["documents"]) == ["connection.json", "definition/type-map.json"]
    assert built["left_out"] == []


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
    built = _build("workspace", tmp_path)
    assert built["tool"] == "validate_workspace"
    assert sorted(built["arguments"]["documents"]) == [
        "connections/c/connection.json",
        "connectors/k/definition/connector.json",
        "pipelines/manifest.json",
        "pipelines/p/pipeline.json",
    ]


def test_a_workspace_request_naming_a_pipeline_carries_no_other_pipeline(tmp_path):
    _write(tmp_path, {
        "pipelines/p/pipeline.json": "{}",
        "pipelines/q/pipeline.json": "{}",
        "pipelines/manifest.json": "{}",
        "connections/c/connection.json": "{}",
    })
    assert sorted(_build("workspace", tmp_path, "p")["arguments"]["documents"]) == [
        "connections/c/connection.json", "pipelines/p/pipeline.json"]


def test_a_document_is_carried_as_the_text_on_disk(tmp_path):
    text = '{\n  "connection_id": "c"\n}\n'
    _write(tmp_path, {"connection.json": text})
    assert _build("package", tmp_path, "connection")["arguments"]["documents"] == {"connection.json": text}


def test_a_single_document_request_names_its_kind(tmp_path):
    _write(tmp_path, {"s.json": '{"a": 1}'})
    assert _build("document", tmp_path / "s.json", "stream") == {
        "tool": "validate_single_document",
        "arguments": {"document": '{"a": 1}', "document_kind": "stream"},
        "left_out": []}


def test_a_linked_file_is_reported_not_followed(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    package = tmp_path / "pkg"
    _write(package, {"connection.json": "{}"})
    (package / "definition").mkdir()
    (package / "definition" / "type-map.json").symlink_to(outside)
    built = _build("package", package, "connection")
    assert sorted(built["arguments"]["documents"]) == ["connection.json"]
    assert built["left_out"] == [{"key": "definition/type-map.json", "reason": "a link, never followed"}]


def test_a_linked_directory_is_reported_not_followed(tmp_path):
    outside = tmp_path / "outside"
    _write(outside, {"type-map.json": "{}"})
    package = tmp_path / "pkg"
    _write(package, {"connection.json": "{}"})
    (package / "definition").symlink_to(outside, target_is_directory=True)
    built = _build("package", package, "connection")
    assert sorted(built["arguments"]["documents"]) == ["connection.json"]
    assert built["left_out"] == [{"key": "definition", "reason": "a linked directory, never followed"}]


def test_a_document_that_is_not_utf8_is_reported(tmp_path):
    _write(tmp_path, {"connection.json": "{}", "definition/type-map.json": b"\xff\xfe"})
    built = _build("package", tmp_path, "connection")
    assert sorted(built["arguments"]["documents"]) == ["connection.json"]
    assert built["left_out"] == [{"key": "definition/type-map.json", "reason": "not UTF-8 text"}]


def test_every_package_kind_has_a_location_table_where_the_builder_reads_it():
    from analitiq.contracts.validation_requests import PACKAGE_KINDS
    for kind in PACKAGE_KINDS:
        assert rendered(f"{builder.SCHEMA_HOST}/{kind}-package/latest.json")["patternProperties"]
