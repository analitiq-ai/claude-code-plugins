"""`write` commits the cascade's decision as a bump record; `check` and
`bump-check` verify records offline, with no model call and no secret."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts", "render_schemas")

import render_schemas  # noqa: E402
import schema_bump_cascade as cascade  # noqa: E402
from schema_diff import diff_lines, diff_sha256  # noqa: E402

RESOURCE = "workspace"
FIXTURES = Path(__file__).parent / "fixtures" / "schema_bumps"


def _jev(choice: str) -> dict:
    body = json.loads((FIXTURES / "jev_answer.json").read_text())
    body["answers"]["bump"]["choice"] = choice
    return body


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A schemas tree whose published 1.0.0 carries a keyword the models no
    longer render, so the next `write` has a change to classify."""
    monkeypatch.setattr(render_schemas, "SCHEMAS_ROOT", tmp_path / "schemas")
    monkeypatch.setattr(render_schemas, "BUMP_RECORDS_ROOT", tmp_path / "schema-bumps")
    monkeypatch.setattr(render_schemas, "_refresh_document_schemas", lambda: None)
    monkeypatch.setattr(render_schemas, "_refresh_contracts_version", lambda: None)
    resource = render_schemas.get_resource(RESOURCE)
    for name, doc in (
        ("1.0.0.json", render_schemas.render_pinned(resource, "1.0.0")),
        ("latest.json", render_schemas.render_latest(resource, "1.0.0")),
    ):
        doc["minProperties"] = 0
        render_schemas.write_json(resource.dir() / name, doc)
    render_schemas.write_json(resource.dir() / "index.json", render_schemas.build_index(resource))
    base = tmp_path / "base-latest.json"
    base.write_text((resource.dir() / "latest.json").read_text())
    return resource, base


@pytest.fixture
def models(monkeypatch):
    """Jev, answering `answers` in turn; the requests it saw."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    answers: list[str] = []

    def post(url, payload):
        assert url == cascade.JEV_URL
        return 200, _jev(answers.pop(0))

    monkeypatch.setattr(cascade, "openrouter_post", lambda api_key: post)
    return answers


def _record(resource, version) -> dict:
    return json.loads(render_schemas.bump_record_path(resource, version).read_text())


def _bump_check(base: Path) -> int:
    return render_schemas.main(["bump-check", "--resource", RESOURCE, "--previous", str(base)])


def test_write_records_the_decision_and_both_gates_accept_it(tree, models):
    resource, base = tree
    models.append("major")
    assert render_schemas.main(["write", "--resource", RESOURCE]) == 0
    record = _record(resource, "2.0.0")
    head = json.loads((resource.dir() / "latest.json").read_text())
    assert record == {
        "resource": RESOURCE,
        "from": "1.0.0",
        "to": "2.0.0",
        "diff_sha256": diff_sha256(diff_lines(json.loads(base.read_text()), head)),
        "stage1": {
            "model": "typesafe/jev-1.13-20260917",
            "choice": "major",
            "confidence": 0.93,
            "probabilities": {"major": 0.05, "minor": 0.93, "patch": 0.02},
        },
        "stage2": None,
        "override": None,
        "final": "major",
    }
    assert render_schemas.main(["check", "--resource", RESOURCE]) == 0
    assert _bump_check(base) == 0


@pytest.mark.parametrize(("models_say", "override", "version"), [("minor", "major", "2.0.0"), ("major", "minor", "1.1.0")])
def test_an_override_wins_in_either_direction_and_keeps_the_model_output(tree, models, models_say, override, version):
    resource, base = tree
    models.append(models_say)
    argv = ["write", "--resource", RESOURCE, "--bump", override, "--reason", "policy call"]
    assert render_schemas.main(argv) == 0
    record = _record(resource, version)
    assert record["override"] == {"bump": override, "reason": "policy call"}
    assert record["final"] == override
    assert record["stage1"]["choice"] == models_say
    assert _bump_check(base) == 0


@pytest.mark.parametrize("argv", [["--bump", "major"], ["--reason", "why"]])
def test_an_override_needs_both_the_bump_and_the_reason(tree, models, argv):
    resource, _ = tree
    assert render_schemas.main(["write", "--resource", RESOURCE, *argv]) == 2
    assert not render_schemas.bump_record_path(resource, "2.0.0").exists()


def test_write_without_a_key_writes_nothing(tree, monkeypatch):
    resource, _ = tree
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert render_schemas.main(["write", "--resource", RESOURCE]) == 2
    assert render_schemas.list_published_versions(resource) == ["1.0.0"]


def test_a_failed_classification_writes_nothing(tree, monkeypatch):
    resource, _ = tree
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(cascade, "openrouter_post", lambda api_key: lambda url, payload: (503, {}))
    assert render_schemas.main(["write", "--resource", RESOURCE]) == 2
    assert render_schemas.list_published_versions(resource) == ["1.0.0"]
    assert not (render_schemas.BUMP_RECORDS_ROOT / RESOURCE).exists()


def _publish(tree, models, bump="major") -> tuple:
    resource, base = tree
    models.append(bump)
    assert render_schemas.main(["write", "--resource", RESOURCE]) == 0
    version = render_schemas.load_latest(resource)["version"]
    return resource, base, render_schemas.bump_record_path(resource, version)


def _edit(path: Path, **fields) -> None:
    render_schemas.write_json(path, {**json.loads(path.read_text()), **fields})


def test_bump_check_fails_without_a_record(tree, models):
    _, base, record = _publish(tree, models)
    record.unlink()
    assert _bump_check(base) == 1


def test_bump_check_fails_when_the_schema_moved_after_the_record(tree, models):
    resource, base, _ = _publish(tree, models)
    latest = resource.dir() / "latest.json"
    _edit(latest, required=["edited-after-the-record"])
    assert _bump_check(base) == 1


def test_bump_check_fails_when_the_version_does_not_follow_from_the_record(tree, models):
    _, base, record = _publish(tree, models)
    _edit(record, final="minor", stage1={**json.loads(record.read_text())["stage1"], "choice": "minor"})
    assert _bump_check(base) == 1


def test_bump_check_fails_when_final_disagrees_with_the_stages(tree, models):
    _, base, record = _publish(tree, models)
    _edit(record, stage1={**json.loads(record.read_text())["stage1"], "choice": "minor"})
    assert _bump_check(base) == 1


def test_bump_check_fails_on_an_override_without_a_reason(tree, models):
    _, base, record = _publish(tree, models)
    _edit(record, override={"bump": "major", "reason": " "})
    assert _bump_check(base) == 1


def test_bump_check_fails_on_a_change_under_an_unchanged_version(tree):
    resource, base = tree
    _edit(resource.dir() / "latest.json", required=["new"])
    assert _bump_check(base) == 1


def test_bump_check_passes_an_unchanged_schema(tree):
    _, base = tree
    assert _bump_check(base) == 0


def test_check_fails_on_a_record_that_does_not_match_its_pinned_versions(tree, models):
    _, _, record = _publish(tree, models)
    _edit(record, diff_sha256="0" * 64)
    assert render_schemas.main(["check", "--resource", RESOURCE]) == 1


def test_check_fails_on_a_record_with_no_pinned_versions(tree, models):
    resource, _, record = _publish(tree, models)
    stray = render_schemas.bump_record_path(resource, "9.0.0")
    stray.write_text(record.read_text())
    assert render_schemas.main(["check", "--resource", RESOURCE]) == 1
