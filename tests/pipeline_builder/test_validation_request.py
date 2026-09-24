"""The request builder the validator agents run: a package or workspace request
carries every file under the directory with an allowed extension, leaving out
every entry whose name starts with `.`, and a file it would send but cannot
carry refuses the request."""
from __future__ import annotations

import importlib.util
import os
import re
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
    "definition/endpoints/orders.json": "{}",
    "notes.md": "n",
    "src/pkg/__init__.py": "",
    "src/pkg/__pycache__/m.pyc": b"\xff",
    "logo.png": b"\x89PNG\xff",
    ".env.json": "{}",
    ".secrets/credentials.json": '{"token": "x"}',
    ".venv/lib/site.json": "{}",
    "src/.cache/blob.json": "{}",
}
_SENT = ["connection.json", "definition/endpoints/orders.json", "definition/type-map.json"]


def test_a_package_request_carries_every_allowed_file_outside_a_dot_entry(tmp_path):
    _write(tmp_path, _TREE)
    assert _build("package", tmp_path, "connection") == {
        "tool": "validate_package",
        "arguments": {"package_kind": "connection",
                      "documents": {key: _TREE[key] for key in _SENT}}}


def test_a_workspace_request_carries_every_allowed_file_outside_a_dot_entry(tmp_path):
    _write(tmp_path, {f"connections/c/{key}": content for key, content in _TREE.items()})
    assert _build("workspace", tmp_path) == {
        "tool": "validate_workspace",
        "arguments": {"documents": {f"connections/c/{key}": _TREE[key] for key in _SENT}}}


def test_an_added_extension_is_picked_up(tmp_path, monkeypatch):
    _write(tmp_path, {"connection.json": "{}", "src/connector.py": "x = 1", "notes.md": "n"})
    monkeypatch.setattr(grade.builder, "VALIDATOR_ALLOWED_EXTENSIONS", [".json", ".py"])
    assert sorted(grade.builder.build(["package", str(tmp_path), "connection"])["arguments"]["documents"]) == [
        "connection.json", "src/connector.py"]


def test_every_location_the_validator_grades_has_an_allowed_extension():
    from analitiq.contracts.validation_requests import PACKAGE_KINDS
    from analitiq.contracts.workspace import _DOCUMENT_LOCATIONS

    endings = tuple(re.escape(extension) + "$" for extension in grade.builder.VALIDATOR_ALLOWED_EXTENSIONS)
    locations = [pattern for model in PACKAGE_KINDS.values() for pattern in model.LOCATIONS]
    locations += list(_DOCUMENT_LOCATIONS)
    for pattern in locations:
        assert pattern.endswith(endings), pattern


def test_every_secret_location_sits_under_a_dot_entry():
    """The dot-entry exclusion is what keeps secrets out of every request."""
    from analitiq.contracts.validation_requests import PACKAGE_KINDS

    secrets = [pattern for model in PACKAGE_KINDS.values() for pattern in model.SECRET_LOCATIONS]
    assert secrets
    for pattern in secrets:
        assert pattern.startswith("^\\."), pattern


def _file_link(package: Path) -> None:
    (package / "definition").mkdir()
    (package / "definition" / "type-map.json").symlink_to(package / "connection.json")


def _fifo(package: Path) -> None:
    os.mkfifo(package / "pipe.json")


def _directory_link(package: Path) -> None:
    locked = package.parent / "elsewhere" / "locked"
    _write(locked, {"type-map.json": "{}"})
    (package / "definition").symlink_to(locked)
    locked.parent.chmod(0)


def _unsent_link(package: Path) -> None:
    (package / "linked.md").symlink_to(package / "missing")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
@pytest.mark.parametrize("make", [_file_link, _fifo, _directory_link, _unsent_link])
def test_an_entry_that_is_not_a_regular_file_or_directory_is_left_out(tmp_path, make):
    package = tmp_path / "package"
    _write(package, {"connection.json": "{}"})
    make(package)
    try:
        documents = _build("package", package, "connection")["arguments"]["documents"]
    finally:
        if (tmp_path / "elsewhere").exists():
            (tmp_path / "elsewhere").chmod(0o755)
    assert list(documents) == ["connection.json"]


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


def test_a_file_that_is_not_utf8_refuses_the_request(tmp_path):
    (tmp_path / "connection.json").write_text("{}")
    (tmp_path / "latin1.json").write_bytes(b'{"a": "\xe9"}')
    assert _refused("package", tmp_path, "connection") == f"{tmp_path / 'latin1.json'}: not UTF-8 text"


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


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
@pytest.mark.parametrize("argv", [
    ["document", "{}/pipeline.json", "pipeline"], ["package", "{}/package", "connection"], ["workspace", "{}/package"],
])
def test_a_target_under_an_unreadable_directory_is_refused(tmp_path, argv):
    locked = tmp_path / "locked"
    _write(locked, {"pipeline.json": "{}", "package/connection.json": "{}"})
    locked.chmod(0)
    try:
        refusal = _refused(*[a.format(locked) for a in argv])
    finally:
        locked.chmod(0o755)
    assert refusal == f"{argv[1].format(locked)}: unreadable: Permission denied"
