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


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _jev(choice: str, confidence: float = 0.93) -> tuple[str, int, dict]:
    body = _fixture("jev_answer")
    body["answers"]["bump"].update(choice=choice, confidence=confidence)
    return cascade.JEV_URL, 200, body


def _luna(bump: str) -> tuple[str, int, dict]:
    body = _fixture("luna_answer")
    body["choices"][0]["message"]["content"] = json.dumps({"reasoning": "r", "bump": bump})
    return cascade.CHAT_URL, 200, body


def confident(choice: str) -> list:
    return [_jev(choice)]


def below_the_floor(bump: str) -> list:
    return [_jev("patch", 0.5), _luna(bump)]


def oversized(bump: str) -> list:
    return [(cascade.JEV_URL, 400, _fixture("jev_oversized")), _luna(bump)]


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
    """OpenRouter, answering each queued (url, status, body) in turn."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    responses: list[tuple[str, int, dict]] = []

    def post(url, payload):
        expected_url, status, body = responses.pop(0)
        assert url == expected_url
        return status, body

    monkeypatch.setattr(cascade, "openrouter_post", lambda api_key: post)
    return responses


def _write(*argv: str) -> int:
    return render_schemas.main(["write", "--resource", RESOURCE, *argv])


def _record(resource, version) -> dict:
    return json.loads(render_schemas.bump_record_path(resource, version).read_text())


def _bump_check(base: Path) -> int:
    return render_schemas.main(["bump-check", "--resource", RESOURCE, "--previous", str(base)])


def _check() -> int:
    return render_schemas.main(["check", "--resource", RESOURCE])


def test_write_records_the_decision_and_both_gates_accept_it(tree, models):
    resource, base = tree
    models.extend(confident("major"))
    assert _write() == 0
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
    assert _check() == 0
    assert _bump_check(base) == 0


@pytest.mark.parametrize("answers", [below_the_floor, oversized])
def test_an_escalated_decision_is_recorded_and_both_gates_accept_it(tree, models, answers):
    resource, base = tree
    models.extend(answers("minor"))
    assert _write() == 0
    record = _record(resource, "1.1.0")
    assert record["stage2"] == {"model": "openai/gpt-6-luna-20260801", "bump": "minor", "reasoning": "r"}
    assert record["final"] == "minor"
    assert _check() == 0
    assert _bump_check(base) == 0


@pytest.mark.parametrize(("models_say", "override", "version"), [("minor", "major", "2.0.0"), ("major", "minor", "1.1.0")])
def test_an_override_wins_in_either_direction_and_keeps_the_model_output(tree, models, models_say, override, version):
    resource, base = tree
    models.extend(confident(models_say))
    assert _write("--bump", override, "--reason", "policy call") == 0
    record = _record(resource, version)
    assert record["override"] == {"bump": override, "reason": "policy call"}
    assert record["final"] == override
    assert record["stage1"]["choice"] == models_say
    assert _check() == 0
    assert _bump_check(base) == 0


@pytest.mark.parametrize(
    "argv",
    [["--bump", "major"], ["--reason", "why"], ["--bump", "major", "--reason", " "]],
    ids=["no-reason", "no-bump", "blank-reason"],
)
def test_write_refuses_an_override_a_record_cannot_carry(tree, models, argv):
    resource, _ = tree
    models.extend(confident("minor"))
    assert _write(*argv) == 2
    assert render_schemas.list_published_versions(resource) == ["1.0.0"]
    assert not (render_schemas.BUMP_RECORDS_ROOT / RESOURCE).exists()


def test_write_refuses_an_override_on_a_new_resource(tree, models):
    resource, _ = tree
    for path in resource.dir().iterdir():
        path.unlink()
    assert _write("--bump", "minor", "--reason", "why") == 2
    assert render_schemas.list_published_versions(resource) == []


def test_write_refuses_an_override_when_nothing_changed(tree, models):
    resource, _ = tree
    models.extend(confident("major"))
    assert _write() == 0
    assert _write("--bump", "minor", "--reason", "why") == 2
    assert render_schemas.list_published_versions(resource) == ["1.0.0", "2.0.0"]


def test_write_without_a_key_writes_nothing(tree, monkeypatch):
    resource, _ = tree
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert _write() == 2
    assert render_schemas.list_published_versions(resource) == ["1.0.0"]


def test_a_failed_classification_writes_nothing(tree, monkeypatch):
    resource, _ = tree
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(cascade, "openrouter_post", lambda api_key: lambda url, payload: (503, {}))
    assert _write() == 2
    assert render_schemas.list_published_versions(resource) == ["1.0.0"]
    assert not (render_schemas.BUMP_RECORDS_ROOT / RESOURCE).exists()


def test_rewriting_from_the_base_replaces_unmerged_versions_with_one_record(tree, models):
    resource, base = tree
    models.extend(confident("minor"))
    assert _write() == 0
    models.extend(confident("major"))
    assert _write("--previous", str(base)) == 0
    assert render_schemas.list_published_versions(resource) == ["1.0.0", "2.0.0"]
    assert sorted(p.name for p in (render_schemas.BUMP_RECORDS_ROOT / RESOURCE).iterdir()) == ["2.0.0.json"]
    assert _record(resource, "2.0.0")["from"] == "1.0.0"
    assert _check() == 0
    assert _bump_check(base) == 0


def _publish(tree, models, answers) -> tuple:
    resource, base = tree
    models.extend(answers)
    assert _write() == 0
    version = render_schemas.load_latest(resource)["version"]
    return resource, base, render_schemas.bump_record_path(resource, version)


def _edit(path: Path, **fields) -> None:
    render_schemas.write_json(path, {**json.loads(path.read_text()), **fields})


def test_bump_check_fails_without_a_record(tree, models):
    _, base, record = _publish(tree, models, confident("major"))
    record.unlink()
    assert _bump_check(base) == 1


def test_bump_check_fails_when_the_schema_moved_after_the_record(tree, models):
    resource, base, _ = _publish(tree, models, confident("major"))
    _edit(resource.dir() / "latest.json", required=["edited-after-the-record"])
    assert _bump_check(base) == 1


def test_bump_check_fails_when_the_version_does_not_follow_from_the_record(tree, models):
    _, base, record = _publish(tree, models, confident("major"))
    _edit(record, final="minor", stage1={**json.loads(record.read_text())["stage1"], "choice": "minor"})
    assert _bump_check(base) == 1


def test_bump_check_fails_when_final_disagrees_with_the_stages(tree, models):
    _, base, record = _publish(tree, models, confident("major"))
    _edit(record, stage1={**json.loads(record.read_text())["stage1"], "choice": "minor"})
    assert _bump_check(base) == 1


def _stage1(record: dict, **fields) -> dict:
    return {"stage1": {**record["stage1"], **fields}}


def _stage2(record: dict, **fields) -> dict:
    return {"stage2": {**record["stage2"], **fields}}


@pytest.mark.parametrize(
    ("answers", "edit"),
    [
        pytest.param(confident("major"), lambda r: _stage1(r, confidence=0.05), id="sub-floor-stage1-not-escalated"),
        pytest.param(
            confident("major"), lambda r: {"stage2": {"model": "m", "bump": "major", "reasoning": "r"}},
            id="confident-stage1-escalated",
        ),
        pytest.param(oversized("major"), lambda r: _stage2(r, model=None), id="stage2-without-model"),
        pytest.param(oversized("major"), lambda r: _stage2(r, reasoning=None), id="stage2-without-reasoning"),
        pytest.param(oversized("major"), lambda r: {"stage2": None}, id="skipped-stage1-not-escalated"),
        pytest.param(below_the_floor("major"), lambda r: _stage1(r, confidence=1.5), id="confidence-out-of-range"),
        pytest.param(confident("major"), lambda r: _stage1(r, extra=1), id="stage1-extra-key"),
        pytest.param(
            confident("major"), lambda r: {"override": {"bump": "major", "reason": " "}}, id="blank-override-reason",
        ),
    ],
)
def test_both_gates_reject_a_record_the_cascade_could_not_have_produced(tree, models, answers, edit):
    _, base, record = _publish(tree, models, answers)
    _edit(record, **edit(json.loads(record.read_text())))
    assert _bump_check(base) == 1
    assert _check() == 1


def test_bump_check_fails_on_a_change_under_an_unchanged_version(tree):
    resource, base = tree
    _edit(resource.dir() / "latest.json", required=["new"])
    assert _bump_check(base) == 1


def test_bump_check_passes_an_unchanged_schema(tree):
    _, base = tree
    assert _bump_check(base) == 0


def test_check_fails_on_a_record_that_does_not_match_its_pinned_versions(tree, models):
    _, _, record = _publish(tree, models, confident("major"))
    _edit(record, diff_sha256="0" * 64)
    assert _check() == 1


def test_check_fails_on_a_record_with_no_pinned_versions(tree, models):
    resource, _, record = _publish(tree, models, confident("major"))
    stray = render_schemas.bump_record_path(resource, "9.0.0")
    stray.write_text(record.read_text())
    assert _check() == 1
