"""The request builder the validator agents run: which files a request carries,
selected by the published location tables (read here from the tree this
checkout renders)."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from analitiq.contracts.validation_requests import (
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
    ValidateWorkspaceRequest,
)


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


_REQUEST_MODELS = {
    "validate_single_document": ValidateSingleDocumentRequest,
    "validate_package": ValidatePackageRequest,
    "validate_workspace": ValidateWorkspaceRequest,
}


def _build(*argv) -> dict:
    """Build, holding `arguments` to the request model its tool takes."""
    built = builder.build([str(a) for a in argv], rendered)
    _REQUEST_MODELS[built["tool"]].model_validate(built["arguments"])
    return built


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
        "connections/c/connection.json", "pipelines/manifest.json", "pipelines/p/pipeline.json"]


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
    assert built["left_out"] == [{"key": "definition/", "reason": "a link, never followed"}]


def test_a_document_that_is_not_utf8_is_reported(tmp_path):
    _write(tmp_path, {"connection.json": "{}", "definition/type-map.json": b"\xff\xfe"})
    built = _build("package", tmp_path, "connection")
    assert sorted(built["arguments"]["documents"]) == ["connection.json"]
    assert built["left_out"] == [{"key": "definition/type-map.json", "reason": "not UTF-8 text"}]


def test_every_package_kind_has_a_location_table_where_the_builder_reads_it():
    from analitiq.contracts.validation_requests import PACKAGE_KINDS
    for kind in PACKAGE_KINDS:
        assert rendered(f"{builder.SCHEMA_HOST}/{kind}-package/latest.json")["patternProperties"]


def test_a_connector_package_is_rooted_where_its_table_locates_it(tmp_path):
    _write(tmp_path, {"definition/connector.json": "{}", "definition/endpoints/e.json": "{}",
                      "connector.py": "", "README.md": "r"})
    assert sorted(_build("package", tmp_path, "connector")["arguments"]["documents"]) == [
        "definition/connector.json", "definition/endpoints/e.json"]


def test_a_linked_directory_no_location_reaches_is_not_reported(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    package = tmp_path / "pkg"
    _write(package, {"connection.json": "{}", ".venv/lib/x.py": ""})
    (package / ".venv" / "lib64").symlink_to(outside, target_is_directory=True)
    (package / "definition").mkdir()
    (package / "definition" / "cache").symlink_to(outside, target_is_directory=True)
    assert _build("package", package, "connection")["left_out"] == []


def test_a_linked_directory_under_another_pipeline_is_not_reported(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "ws"
    _write(workspace, {"pipelines/p/pipeline.json": "{}", "pipelines/q/pipeline.json": "{}"})
    (workspace / "pipelines" / "q" / "streams").symlink_to(outside, target_is_directory=True)
    assert _build("workspace", workspace, "p")["left_out"] == []


def test_a_linked_package_directory_in_a_workspace_is_reported(tmp_path):
    outside = tmp_path / "outside"
    _write(outside, {"connection.json": "{}"})
    workspace = tmp_path / "ws"
    _write(workspace, {"pipelines/p/pipeline.json": "{}"})
    (workspace / "connections").mkdir()
    (workspace / "connections" / "c").symlink_to(outside, target_is_directory=True)
    assert _build("workspace", workspace)["left_out"] == [
        {"key": "connections/c/", "reason": "a link, never followed"}]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_an_unreadable_directory_a_location_reaches_is_reported(tmp_path):
    _write(tmp_path, {"connection.json": "{}", "definition/endpoints/e.json": "{}"})
    endpoints = tmp_path / "definition" / "endpoints"
    endpoints.chmod(0)
    try:
        built = _build("package", tmp_path, "connection")
    finally:
        endpoints.chmod(0o755)
    assert built["left_out"] == [{"key": "definition/endpoints/", "reason": "unreadable: Permission denied"}]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_document_is_reported(tmp_path):
    _write(tmp_path, {"connection.json": "{}"})
    (tmp_path / "connection.json").chmod(0)
    try:
        built = _build("package", tmp_path, "connection")
    finally:
        (tmp_path / "connection.json").chmod(0o644)
    assert built["left_out"] == [{"key": "connection.json", "reason": "unreadable: Permission denied"}]


def test_a_located_entry_that_is_not_a_regular_file_is_reported(tmp_path):
    _write(tmp_path, {"connection.json": "{}"})
    os.mkfifo(tmp_path / "definition-type-map.fifo")
    (tmp_path / "definition").mkdir()
    os.mkfifo(tmp_path / "definition" / "type-map.json")
    assert _build("package", tmp_path, "connection")["left_out"] == [
        {"key": "definition/type-map.json", "reason": "not a regular file"}]


@pytest.mark.parametrize("argv", [
    [],
    ["validate"],
    ["package"],
    ["package", "."],
    ["document", "x.json"],
    ["workspace", ".", "p", "extra"],
    ["package", ".", "connection", "extra"],
])
def test_an_argument_list_no_mode_takes_is_refused(tmp_path, argv):
    with pytest.raises(builder.RequestError, match="usage"):
        builder.build(argv, rendered)


@pytest.mark.parametrize("mode,extra", [("package", ["connection"]), ("workspace", [])])
def test_a_missing_directory_is_refused(tmp_path, mode, extra):
    with pytest.raises(builder.RequestError):
        builder.build([mode, str(tmp_path / "absent"), *extra], rendered)


def test_a_linked_target_directory_is_refused(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(builder.RequestError):
        builder.build(["package", str(tmp_path / "link"), "connection"], rendered)


@pytest.mark.parametrize("pipeline", ["typo", "", ".", "..", "p/streams", "notes"])
def test_a_pipeline_the_workspace_does_not_hold_is_refused(tmp_path, pipeline):
    _write(tmp_path, {
        "pipelines/p/pipeline.json": "{}",
        "pipelines/p/streams/s.json": "{}",
        "pipelines/notes/notes.json": "{}",
    })
    with pytest.raises(builder.RequestError):
        builder.build(["workspace", str(tmp_path), pipeline], rendered)


def test_a_pipeline_whose_document_is_left_out_is_carried_as_reported(tmp_path):
    _write(tmp_path, {"pipelines/p/pipeline.json": b"\xff"})
    assert _build("workspace", tmp_path, "p")["left_out"] == [
        {"key": "pipelines/p/pipeline.json", "reason": "not UTF-8 text"}]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_a_named_pipeline_whose_directory_is_unreadable_is_reported(tmp_path):
    _write(tmp_path, {"pipelines/p/pipeline.json": "{}"})
    held = tmp_path / "pipelines" / "p"
    held.chmod(0)
    try:
        left_out = _build("workspace", tmp_path, "p")["left_out"]
    finally:
        held.chmod(0o755)
    assert left_out == [{"key": "pipelines/p/", "reason": "unreadable: Permission denied"}]


def test_a_named_pipeline_under_a_linked_directory_is_reported(tmp_path):
    _write(tmp_path / "outside", {"p/pipeline.json": "{}"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "pipelines").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert _build("workspace", workspace, "p")["left_out"] == [
        {"key": "pipelines/", "reason": "a link, never followed"}]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
@pytest.mark.parametrize("mode,expected", [
    (0, [{"key": "pipelines/", "reason": "unreadable: Permission denied"}]),
    # Readable but not searchable: its names list, nothing beneath opens.
    (0o644, [{"key": "pipelines/p/", "reason": "unreadable: Permission denied"}]),
])
def test_a_named_pipeline_under_an_unreadable_pipelines_directory_is_reported(tmp_path, mode, expected):
    _write(tmp_path, {"pipelines/p/pipeline.json": "{}"})
    pipelines = tmp_path / "pipelines"
    pipelines.chmod(mode)
    try:
        left_out = _build("workspace", tmp_path, "p")["left_out"]
    finally:
        pipelines.chmod(0o755)
    assert left_out == expected


def test_a_named_pipeline_behind_a_link_is_reported_whatever_the_link_holds(tmp_path):
    (tmp_path / "outside").mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "pipelines").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert _build("workspace", workspace, "p")["left_out"] == [
        {"key": "pipelines/", "reason": "a link, never followed"}]


def test_a_directory_at_a_document_location_is_reported_not_walked(tmp_path):
    _write(tmp_path, {"connection.json": "{}", "definition/type-map.json/inner.json": "{}"})
    assert _build("package", tmp_path, "connection")["left_out"] == [
        {"key": "definition/type-map.json", "reason": "not a regular file"}]


def test_a_directory_link_at_a_document_location_is_reported_not_followed(tmp_path):
    (tmp_path / "outside").mkdir()
    package = tmp_path / "pkg"
    _write(package, {"connection.json": "{}", "definition/.keep": ""})
    (package / "definition" / "type-map.json").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert _build("package", package, "connection")["left_out"] == [
        {"key": "definition/type-map.json", "reason": "a link, never followed"}]


@pytest.mark.parametrize("content,link", [(None, False), (b"\xff", False), ("{}", True)])
def test_a_single_document_that_cannot_be_submitted_is_refused(tmp_path, content, link):
    target = tmp_path / "s.json"
    if link:
        (tmp_path / "real.json").write_text(content)
        target.symlink_to(tmp_path / "real.json")
    elif content is not None:
        target.write_bytes(content)
    with pytest.raises(builder.RequestError):
        builder.build(["document", str(target), "stream"], rendered)
