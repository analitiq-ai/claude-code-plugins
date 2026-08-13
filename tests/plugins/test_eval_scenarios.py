"""Every eval scenario is still runnable and still names things that exist.

An eval is expensive: it spends minutes and tokens, and some scenarios reach the
network, so nothing runs them on a pull request. That is exactly why a scenario
rots quietly. A renamed rule id, a worked example moved to another skill, an
assertion with no operator — each of those turns into a confusing failure hours
later on a nightly, or worse, into a scenario that reports a pass because the
document it meant to grade was never resolved.

This module is the cheap half: it loads every scenario and checks the parts that
can be checked without running anything. It never invokes an agent.

Locating, not deciding (`.claude/rules/guards.md`): every assertion here
resolves an identifier — a path on disk, a rule id against the registry, a key
against the format the runner implements. Whether a scenario is a *good* eval is
a reader's judgment and stays in the scenario's own `why`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVALS = REPO_ROOT / "evals"
RULE_RECORDS = REPO_ROOT / "rules" / "records"


def _runner():
    spec = importlib.util.spec_from_file_location("run_evals", EVALS / "run_evals.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _runner()
SCENARIOS = sorted(EVALS / "scenarios" / p.name for p in (EVALS / "scenarios").glob("*.json"))
IDS = [p.stem for p in SCENARIOS]


def test_there_are_scenarios_to_grade():
    """A green run must mean the scenarios hold, never that none were found."""
    assert SCENARIOS, f"no scenario in {(EVALS / 'scenarios').relative_to(REPO_ROOT)}"


def test_the_loader_accepts_every_scenario():
    """`load_scenarios` is what the runner uses; disagreeing with it is the bug."""
    assert len(runner.load_scenarios()) == len(SCENARIOS)


@pytest.mark.parametrize("path", SCENARIOS, ids=IDS)
def test_scenario_is_well_formed(path: Path):
    scenario = json.loads(path.read_text(encoding="utf-8"))

    assert scenario["id"] == path.stem
    assert scenario["why"].strip(), "a scenario says what it exists to catch"
    assert scenario["expect"] in ("build", "refusal")
    assert (REPO_ROOT / "plugins" / scenario["plugin"]).is_dir()

    if scenario["expect"] == "refusal":
        assert scenario.get("files"), "a refusal names the globs that must stay empty"
        assert not scenario.get("assert"), "a refusal writes nothing, so there is nothing to assert"
    else:
        assert scenario.get("assert"), "a build scenario that asserts nothing grades nothing"


@pytest.mark.parametrize("path", SCENARIOS, ids=IDS)
def test_every_seed_source_exists(path: Path):
    """Seeds are derived from documents an existing gate already validates.

    Copying from `examples/` rather than committing a fixture tree is what keeps
    a connector nobody re-checks out of this directory. It only holds while the
    source is really there.
    """
    scenario = json.loads(path.read_text(encoding="utf-8"))
    for item in scenario.get("seed", []):
        assert (REPO_ROOT / item["from"]).is_file(), f"seed source missing: {item['from']}"
        assert not Path(item["to"]).is_absolute() and ".." not in Path(item["to"]).parts, (
            f"seed target escapes the working directory: {item['to']}")


@pytest.mark.parametrize("path", SCENARIOS, ids=IDS)
def test_every_rule_id_resolves(path: Path):
    """A rule id is either a live record or the literal marking user intent."""
    scenario = json.loads(path.read_text(encoding="utf-8"))
    cited = [item["rule"] for key in ("assert", "artifacts", "text_assert")
             for item in scenario.get(key, [])]
    cited += scenario.get("also_covers", [])
    assert cited, "nothing cited, so this scenario contributes no coverage"
    for rule in cited:
        if rule == runner.INTENT:
            continue
        assert (RULE_RECORDS / f"{rule}.yaml").is_file(), f"{path.name} cites unknown rule {rule}"


@pytest.mark.parametrize("path", SCENARIOS, ids=IDS)
def test_every_assertion_names_one_operator(path: Path):
    """An assertion with no operator would raise mid-run, hours in."""
    scenario = json.loads(path.read_text(encoding="utf-8"))
    for item in scenario.get("assert", []):
        ops = [key for key in runner.OPS if key in item]
        assert len(ops) == 1, f"{path.name}: {item['path']} names operators {ops}"
        assert item["doc"] in scenario.get("docs", {}), (
            f"{path.name}: {item['path']} reads document {item['doc']!r}, which `docs` does not "
            "resolve — the run would report it unresolved rather than grade it")
    for item in scenario.get("text_assert", []):
        ops = [key for key in ("matches", "not_matches") if key in item]
        assert len(ops) == 1, f"{path.name}: {item['file']} names operators {ops}"


def test_coverage_report_runs():
    """`coverage` reads the registry; a shape change there breaks it silently."""
    assert runner.unenforced_rules(), "no unenforced rule found — the scan stopped measuring"
    counted = runner.asserted_rules(runner.load_scenarios())
    assert counted, "no scenario cites a rule, so coverage would report nothing"
    assert runner.INTENT not in counted, "intent is not a registry id and must not be counted"
