"""`release` publishes what the models changed, with a bump record per bump;
`check` verifies the committed tree offline; `release-guard` keeps every other
PR off the paths only a release writes. No test here calls the network."""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts", "render_schemas")

import render_schemas  # noqa: E402
from schema_bump import cascade, evaluation  # noqa: E402

NAMES = ("workspace", "credentials")


def _jev(choice: str) -> tuple[str, int, dict]:
    answer = {"choice": choice, "confidence": 0.93, "probabilities": {choice: 0.93}}
    return cascade.JEV_URL, 200, {"model": "jev", "answers": {"bump": answer}, "usage": {"cost": 0.001}}


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


def _publish_stale(resource) -> None:
    """Release 1.0.0 of `resource` carrying a keyword the models no longer render."""
    for name, doc in (
        ("1.0.0.json", render_schemas.render_pinned(resource, "1.0.0")),
        ("latest.json", render_schemas.render_latest(resource, "1.0.0")),
    ):
        doc["minProperties"] = 0
        render_schemas.write_json(resource.dir() / name, doc)
    render_schemas.write_json(resource.dir() / "index.json", render_schemas.build_index(resource))


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Two released resources, each with a change the next release publishes."""
    monkeypatch.setattr(render_schemas, "SCHEMAS_ROOT", tmp_path / "schemas")
    monkeypatch.setattr(render_schemas, "BUMP_RECORDS_ROOT", tmp_path / "schema-bumps")
    monkeypatch.setattr(render_schemas, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(render_schemas, "DOCUMENT_SCHEMAS_PATH", tmp_path / "document_schemas.json")
    monkeypatch.setattr(render_schemas, "ARROW_TYPES_PATH", tmp_path / "schemas" / "arrow-types.json")
    monkeypatch.setattr(render_schemas, "CONTRACTS_VERSION_PATH", tmp_path / "schemas" / "contracts-version.json")
    resources = _register(monkeypatch, [render_schemas.get_resource(name) for name in NAMES])
    for resource in resources:
        _publish_stale(resource)
    return resources


def _register(monkeypatch, resources):
    """Install `resources` and commit the document_schemas.json they render, as a registering PR does."""
    monkeypatch.setattr(render_schemas, "RESOURCES", resources)
    assert render_schemas.main(["document-schemas"]) == 0
    return resources


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _release() -> int:
    return render_schemas.main(["release"])


def _check(*argv: str) -> int:
    return render_schemas.main(["check", "--resource", NAMES[0], *argv])


def _override(resource, **override) -> Path:
    path = render_schemas.override_path(resource)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(override))
    return path


def _record(resource, version) -> dict:
    return json.loads(render_schemas.bump_record_path(resource, version).read_text())


def test_a_release_publishes_every_change_with_its_record_and_the_released_check_accepts_it(tree, models):
    models.extend([_jev("minor"), _jev("major")])
    assert _release() == 0
    workspace, credentials = tree
    assert render_schemas.list_published_versions(workspace) == ["1.0.0", "1.1.0"]
    assert render_schemas.list_published_versions(credentials) == ["1.0.0", "2.0.0"]
    record = _record(workspace, "1.1.0")
    assert (record["from"], record["to"], record["final"], record["override"]) == ("1.0.0", "1.1.0", "minor", None)
    assert render_schemas.load_latest(workspace) == render_schemas.render_latest(workspace, "1.1.0")
    assert _check() == 0
    assert _check("--released") == 0


def test_a_release_with_nothing_changed_writes_nothing(tree, models, tmp_path):
    models.extend([_jev("minor"), _jev("minor")])
    assert _release() == 0
    before = _files(tmp_path)
    assert _release() == 0
    assert _files(tmp_path) == before


@pytest.mark.parametrize(("models_say", "override", "version"), [("minor", "major", "2.0.0"), ("major", "patch", "1.0.1")])
def test_an_override_wins_in_either_direction_is_recorded_and_consumed(tree, models, models_say, override, version):
    workspace = tree[0]
    path = _override(workspace, bump=override, reason="policy")
    models.extend([_jev(models_say), _jev("minor")])
    assert _release() == 0
    record = _record(workspace, version)
    assert record["stage1"]["choice"] == models_say
    assert (record["override"], record["final"]) == ({"bump": override, "reason": "policy"}, override)
    assert not path.exists()
    assert _check("--released") == 0


@pytest.mark.parametrize(
    "override",
    [{"bump": "major"}, {"bump": "major", "reason": " "}, {"bump": "huge", "reason": "r"}, {"bump": "major", "reason": "r", "x": 1}],
)
def test_a_malformed_override_fails_the_release_before_any_call(tree, models, tmp_path, override):
    _override(tree[1], **override)
    before = _files(tmp_path)
    assert _release() == 2
    assert _files(tmp_path) == before
    assert _check() == 0
    assert render_schemas.main(["check", "--resource", NAMES[1]]) == 1


def test_an_override_on_an_unchanged_resource_fails_the_release(tree, models, tmp_path):
    models.extend([_jev("minor"), _jev("minor")])
    assert _release() == 0
    _override(tree[0], bump="major", reason="r")
    before = _files(tmp_path)
    assert _release() == 2
    assert _files(tmp_path) == before


def test_a_release_with_nothing_to_publish_still_renders_the_versionless_documents(tree, models):
    models.extend([_jev("minor"), _jev("minor")])
    assert _release() == 0
    render_schemas.ARROW_TYPES_PATH.unlink()
    render_schemas.CONTRACTS_VERSION_PATH.unlink()
    assert _release() == 0
    assert render_schemas.check_arrow_types(released=True)[0]
    assert render_schemas.check_contracts_version(released=True)[0]


def test_a_new_resource_is_first_released_at_1_0_0_without_a_record_or_a_key(tree, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    [fresh] = _register(monkeypatch, [render_schemas.get_resource("stream")])
    assert _check_of(fresh) == 0
    assert _check_of(fresh, "--released") == 1
    assert _release() == 0
    assert render_schemas.list_published_versions(fresh) == ["1.0.0"]
    assert not (render_schemas.BUMP_RECORDS_ROOT / fresh.name).exists()
    assert _check_of(fresh, "--released") == 0


def test_an_override_on_a_new_resource_fails_the_release(tree, monkeypatch):
    [fresh] = _register(monkeypatch, [render_schemas.get_resource("stream")])
    _override(fresh, bump="major", reason="r")
    assert _check_of(fresh) == 1
    assert _release() == 2
    assert render_schemas.list_published_versions(fresh) == []


@pytest.mark.parametrize("where", ["workspce/override.json", "override.json"])
def test_an_override_outside_a_registered_resource_fails_the_check_the_release_and_the_guard(tree, models, tmp_path, where):
    path = render_schemas.BUMP_RECORDS_ROOT / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bump": "major", "reason": "r"}))
    before = _files(tmp_path)
    assert _check() == 1
    assert _release() == 2
    assert _files(tmp_path) == before
    shown = path.relative_to(tmp_path).as_posix()
    assert render_schemas.release_only_paths([shown]) == [shown]


def _check_of(resource, *argv: str) -> int:
    return render_schemas.main(["check", "--resource", resource.name, *argv])


def test_a_release_without_a_key_writes_nothing(tree, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    before = _files(tmp_path)
    assert _release() == 2
    assert _files(tmp_path) == before


def test_a_failed_classification_writes_nothing_even_for_resources_already_decided(tree, models, tmp_path):
    models.extend([_jev("minor"), (cascade.JEV_URL, 503, {"error": {"code": 503}})])
    before = _files(tmp_path)
    assert _release() == 2
    assert _files(tmp_path) == before


def _raise_value_error(_resources):
    raise ValueError("a root model's `$schema` also accepts another resource's URL")


@pytest.mark.parametrize("fault", ["stale", "unrenderable"])
def test_a_document_schemas_fault_fails_the_release_before_any_write(tree, models, tmp_path, monkeypatch, fault):
    """The request model reads document_schemas.json, so the release verifies it and never writes it."""
    models.extend([_jev("minor"), _jev("minor")])
    if fault == "stale":
        render_schemas.DOCUMENT_SCHEMAS_PATH.write_text('{"document_schemas": []}\n')
    else:
        monkeypatch.setattr(render_schemas, "document_schema_names", _raise_value_error)
    before = _files(tmp_path)
    assert _release() == 2
    assert _files(tmp_path) == before


@pytest.mark.parametrize("fault", ["latest missing", "latest below the highest pin"])
def test_an_inconsistent_committed_tree_fails_the_release_before_any_write(tree, models, tmp_path, fault):
    """The release plans from latest.json, so it must refuse a tree `check` rejects, or it overwrites a pin."""
    models.extend([_jev("minor"), _jev("minor")])
    resource = tree[0]
    if fault == "latest missing":
        (resource.dir() / "latest.json").unlink()
    else:
        render_schemas.write_json(resource.dir() / "1.1.0.json", render_schemas.render_pinned(resource, "1.1.0"))
    before = _files(tmp_path)
    assert _release() == 2
    assert _files(tmp_path) == before


def test_an_unreleased_model_change_passes_the_check_and_fails_the_released_check(tree):
    assert _check() == 0
    assert _check("--released") == 1


def test_the_check_fails_when_latest_does_not_mirror_the_highest_pinned_version(tree):
    workspace = tree[0]
    latest = render_schemas.load_latest(workspace)
    latest["title"] = "edited"
    render_schemas.write_json(workspace.dir() / "latest.json", latest)
    assert _check() == 1


def test_the_check_fails_when_a_higher_version_is_pinned_than_latest_names(tree):
    workspace = tree[0]
    render_schemas.write_json(workspace.dir() / "1.1.0.json", render_schemas.render_pinned(workspace, "1.1.0"))
    render_schemas.write_json(workspace.dir() / "index.json", render_schemas.build_index(workspace))
    assert _check() == 1


def test_the_check_fails_on_a_stale_index(tree):
    render_schemas.write_json(tree[0].dir() / "index.json", {NAMES[0]: {"latest": "1.0.0", "versions": []}})
    assert _check() == 1


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda r: r.update(final="major"), id="final-disagrees-with-the-stages"),
        pytest.param(lambda r: r.update(diff_sha256="0" * 64), id="different-diff"),
        pytest.param(lambda r: r.update(**{"from": "0.9.0"}), id="unpinned-from"),
    ],
)
def test_the_check_fails_on_a_record_that_does_not_justify_its_versions(tree, models, edit):
    models.extend([_jev("minor"), _jev("minor")])
    assert _release() == 0
    path = render_schemas.bump_record_path(tree[0], "1.1.0")
    record = json.loads(path.read_text())
    edit(record)
    path.write_text(json.dumps(record))
    assert _check() == 1


def test_the_check_fails_on_an_override_of_an_unchanged_resource(tree, models):
    models.extend([_jev("minor"), _jev("minor")])
    assert _release() == 0
    _override(tree[0], bump="major", reason="r")
    assert _check() == 1
    assert _check("--released") == 1


@pytest.mark.parametrize(
    ("path", "blocked"),
    [
        ("schemas/workspace/1.1.0.json", True),
        ("schemas/workspace/latest.json", True),
        ("schema-bumps/workspace/1.1.0.json", True),
        ("schema-bumps/workspace/override.json", False),
        ("schemas/arrow-types.json", True),
        ("schemas/contracts-version.json", True),
        ("schemas/workspace-extra/latest.json", False),
        ("packages/contract-models/src/analitiq/contracts/workspace.py", False),
    ],
)
def test_only_a_release_may_change_a_published_tree_or_a_record(tree, path, blocked):
    assert render_schemas.release_only_paths([path]) == ([path] if blocked else [])


@pytest.mark.parametrize(("paths", "code"), [("packages/x.py\n", 0), ("packages/x.py\nschemas/workspace/latest.json\n", 1)])
def test_the_release_guard_reads_changed_paths_from_stdin(tree, monkeypatch, paths, code):
    monkeypatch.setattr(sys, "stdin", io.StringIO(paths))
    assert render_schemas.main(["release-guard"]) == code


def test_the_evaluation_corpus_loads_from_the_published_history():
    """What the paid evaluation reads, checked for free: every label names a
    consecutive pinned pair and every scored pair has a change."""
    assert evaluation.historical_cases(
        render_schemas.SCHEMAS_ROOT, REPO_ROOT / "evals" / "schema_bumps" / "labels.json")


def test_the_release_workflows_run_at_the_tool_pin():
    """`uses:` cannot read the pin, so each caller repeats the SHA the tool is
    installed at; a workflow at another SHA runs a tool it was not written for."""
    pin = re.search(r"analitiq-ai/\.github@([0-9a-f]{40})#subdirectory=tools/schema-bump",
                    (REPO_ROOT / "requirements-dev.txt").read_text())
    assert pin, "requirements-dev.txt pins no schema-bump SHA"
    refs = {
        (path.name, ref)
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
        for ref in re.findall(r"uses: analitiq-ai/\.github/\.github/workflows/schema-[\w-]+\.yml@(\S+)",
                              path.read_text())
    }
    assert {name for name, _ in refs} == {"schema-release.yml", "schema-bump-eval.yml"}
    assert all(ref == pin[1] for _, ref in refs), refs
