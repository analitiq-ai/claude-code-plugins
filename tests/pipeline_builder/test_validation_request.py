"""The request builder the validator agents run: a package or workspace request
carries every file under the directory except what sits inside a directory
whose name starts with `.`, and an entry it cannot carry refuses the request."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS = REPO_ROOT / "plugins"


def _grade():
    spec = importlib.util.spec_from_file_location("eval_grade", REPO_ROOT / "evals" / "grade.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grade = _grade()


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for key, content in files.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def _build(*argv) -> dict:
    """Build, holding `arguments` to the request model its tool takes."""
    built = grade.builder.build([str(a) for a in argv])
    grade.request(built)
    return built


def _refused(*argv) -> str:
    with pytest.raises(grade.builder.RequestError) as refused:
        grade.builder.build([str(a) for a in argv])
    return str(refused.value)


@pytest.mark.parametrize("path", ["scripts/validation_request.py", ".mcp.json"])
def test_each_plugin_ships_the_same_copy(path):
    # Each plugin is installed on its own, so each carries a copy.
    assert ((PLUGINS / "analitiq-connector-builder" / path).read_bytes()
            == (PLUGINS / "analitiq-pipeline-builder" / path).read_bytes())


_TREE = {
    "connection.json": "{}",
    "definition/type-map.json": "{}",
    "notes.md": "n",
    ".gitignore": "x",
    "src/pkg/__init__.py": "",
    ".secrets/credentials.json": '{"token": "x"}',
    ".venv/lib/site.json": "{}",
    "src/.cache/blob.bin": b"\xff",
}
_SENT = [".gitignore", "connection.json", "definition/type-map.json", "notes.md", "src/pkg/__init__.py"]


def test_a_package_request_carries_every_file_outside_a_dot_directory(tmp_path):
    _write(tmp_path, _TREE)
    assert _build("package", tmp_path, "connection") == {
        "tool": "validate_package",
        "arguments": {"package_kind": "connection",
                      "documents": {key: _TREE[key] for key in _SENT}}}


def test_a_workspace_request_carries_every_file_outside_a_dot_directory(tmp_path):
    _write(tmp_path, {f"connections/c/{key}": content for key, content in _TREE.items()})
    assert _build("workspace", tmp_path) == {
        "tool": "validate_workspace",
        "arguments": {"documents": {f"connections/c/{key}": _TREE[key] for key in _SENT}}}


def test_nothing_inside_a_dot_directory_is_read(tmp_path):
    _write(tmp_path, {"connection.json": "{}"})
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "link").symlink_to(tmp_path / "missing")
    assert list(_build("package", tmp_path, "connection")["arguments"]["documents"]) == ["connection.json"]


def test_a_document_is_carried_as_the_text_on_disk(tmp_path):
    document = tmp_path / "pipeline.json"
    document.write_text('{"a": "é"}')
    assert _build("document", document, "pipeline") == {
        "tool": "validate_single_document",
        "arguments": {"document": '{"a": "é"}', "document_kind": "pipeline"}}


def _link(root: Path) -> None:
    (root / "definition").mkdir()
    (root / "definition" / "type-map.json").symlink_to(root / "connection.json")


def _linked_directory(root: Path) -> None:
    (root / "elsewhere").mkdir()
    (root / "definition").symlink_to(root / "elsewhere")


def _not_utf8(root: Path) -> None:
    (root / "logo.png").write_bytes(b"\x89PNG\xff")


def _fifo(root: Path) -> None:
    os.mkfifo(root / "pipe")


@pytest.mark.parametrize("make,entry,reason", [
    (_link, "definition/type-map.json", "a link, never followed"),
    (_linked_directory, "definition", "a link, never followed"),
    (_not_utf8, "logo.png", "not UTF-8 text"),
    (_fifo, "pipe", "not a regular file"),
])
def test_an_entry_the_request_cannot_carry_refuses_it(tmp_path, make, entry, reason):
    (tmp_path / "connection.json").write_text("{}")
    make(tmp_path)
    assert _refused("package", tmp_path, "connection") == f"{tmp_path / entry}: {reason}"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
@pytest.mark.parametrize("entry", ["connection.json", "definition"])
def test_an_unreadable_entry_refuses_the_request(tmp_path, entry):
    _write(tmp_path, {"connection.json": "{}", "definition/type-map.json": "{}"})
    (tmp_path / entry).chmod(0)
    try:
        refusal = _refused("package", tmp_path, "connection")
    finally:
        (tmp_path / entry).chmod(0o755)
    assert refusal == f"{tmp_path / entry}: unreadable: Permission denied"


@pytest.mark.parametrize("content,link", [(None, False), (b"\xff", False), ("{}", True)])
def test_a_single_document_that_cannot_be_carried_is_refused(tmp_path, content, link):
    document = tmp_path / "pipeline.json"
    if link:
        (tmp_path / "target.json").write_text(content)
        document.symlink_to(tmp_path / "target.json")
    elif content is not None:
        document.write_bytes(content)
    assert _refused("document", document, "pipeline").startswith(f"{document}: ")


@pytest.mark.parametrize("argv", [
    [], ["check"], ["document", "f"], ["package", "d"],
    ["package", "d", "connection", "x"], ["workspace"], ["workspace", "d", "p"],
])
def test_an_argument_list_no_mode_takes_is_refused(argv):
    assert _refused(*argv).startswith("usage:")


@pytest.mark.parametrize("argv", [["package", "{}", "connection"], ["workspace", "{}"]])
def test_a_target_that_is_not_a_directory_is_refused(tmp_path, argv):
    (tmp_path / "real").mkdir()
    (tmp_path / "linked").symlink_to(tmp_path / "real")
    for target in (tmp_path / "missing", tmp_path / "linked"):
        assert _refused(*[str(target) if a == "{}" else a for a in argv]) == (
            f"{target}: not a directory, or a link")
