"""The eval grader: the plugin's request, graded in-process."""
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


def test_main_prints_the_envelope_and_exits_by_the_verdict(tmp_path, capsys):
    (tmp_path / "connection.json").write_text("not json")
    assert grade.main(["package", str(tmp_path), "connection"]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["passed"] is False and printed["findings"]


def test_a_clean_package_passes(tmp_path, capsys):
    package = REPO_ROOT / "plugins" / "analitiq-connector-builder" / "skills" / "connector-spec-db" / "examples" / "postgresql"
    definition = tmp_path / "definition"
    definition.mkdir()
    (definition / "connector.json").write_text((package / "postgresql.example.json").read_text())
    (definition / "type-map.json").write_text((package / "type-map.json").read_text())
    assert grade.main(["package", str(tmp_path), "connector"]) == 0
