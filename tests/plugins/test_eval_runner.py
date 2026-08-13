"""The eval runner's grading logic, graded.

`test_eval_scenarios.py` checks the scenario *files*. This checks the *code that
reads them*, and the two are not interchangeable: a harness whose `dig` always
returns MISSING, whose `check_assertions` returns early, or whose `equals` is
`True` still satisfies every scenario-shape assertion, because none of them runs
the runner. A harness that can be arbitrarily broken while the build stays green
is exactly the failure this repo builds machinery to prevent — and it is worse
here than anywhere else, because a broken eval reports `passed: true` and the
number it writes to the results log is the thing anyone later reasons from.

Every test below is a behaviour a mutation can break. The bias throughout is
toward the *silent* direction: an operator that passes on the wrong type, a path
that resolves to a sentinel, a check that examines nothing. Those are the states
in which an eval measures nothing and says so in the same voice as one finding
nothing wrong.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "evals" / "run_evals.py"


def _runner():
    spec = importlib.util.spec_from_file_location("run_evals", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _runner()

DOC = {
    "status": "draft",
    "streams": ["a", "b"],
    "destinations": [{"write": {"mode": "upsert", "conflict_keys": ["id"]}}],
    "columns": [{"name": "id"}, {"name": "amount"}],
    "nulled": None,
}


# ---------------------------------------------------------------------------
# dig: absent leaf vs unwalkable path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("status", "draft"),
    ("destinations[0].write.mode", "upsert"),
    ("nulled", None),
])
def test_dig_resolves_a_live_path(path, expected):
    assert runner.dig(DOC, path) == expected


def test_dig_separates_an_absent_leaf_from_an_unwalkable_path():
    """The distinction the whole `absent` operator rests on.

    A single sentinel for both is how `absent: true` stops measuring: rename the
    parent and the assertion keeps passing while the field it names no longer
    exists to be absent from.
    """
    assert runner.dig(DOC, "schedule") is runner.MISSING
    assert runner.dig(DOC, "destinations[0].execution") is runner.MISSING

    assert runner.dig(DOC, "nope.nope") is runner.UNREACHABLE
    assert runner.dig(DOC, "schedule.type") is runner.UNREACHABLE
    assert runner.dig(DOC, "status.type") is runner.UNREACHABLE      # key into a scalar
    assert runner.dig(DOC, "status[0]") is runner.UNREACHABLE        # index into a scalar
    assert runner.dig(DOC, "destinations[9].write") is runner.UNREACHABLE


def test_dig_rejects_a_path_it_cannot_parse():
    with pytest.raises(ValueError):
        runner.dig(DOC, "destinations[x]y")


# ---------------------------------------------------------------------------
# Operators: the wrong type is a failure, never a pass
# ---------------------------------------------------------------------------

def test_absent_is_satisfied_only_by_a_missing_leaf():
    absent = runner.OPS["absent"]
    assert absent(runner.MISSING, True)
    assert not absent(runner.UNREACHABLE, True), (
        "an unwalkable path must fail rather than read as absence")
    assert not absent(None, True), "an explicit JSON null is a value the document chose"
    assert not absent("draft", True)


def test_present_rejects_both_sentinels():
    present = runner.OPS["present"]
    assert present("draft", True)
    assert not present(runner.MISSING, True)
    assert not present(runner.UNREACHABLE, True)


def test_not_matches_needs_a_string_to_negate():
    """The asymmetry that made every negative assertion unfalsifiable.

    `matches` fails closed on a missing field; `not_matches` used to pass open,
    so a typo'd path turned a negative assertion into a permanent green.
    """
    not_matches = runner.OPS["not_matches"]
    assert not_matches("aiomysql", "psycopg")
    assert not not_matches("psycopg2", "psycopg")
    for wrong in (runner.MISSING, runner.UNREACHABLE, 5, ["psycopg"], None, {}):
        assert not not_matches(wrong, "psycopg"), f"{wrong!r} is not a string to negate"


def test_one_of_is_membership_not_substring():
    one_of = runner.OPS["one_of"]
    assert one_of("insert", ["insert", "upsert"])
    assert not one_of("in", ["insert", "upsert"])
    assert not one_of("in", "insert"), "a bare string would match substrings"


def test_min_length_counts_a_sequence_only():
    min_length = runner.OPS["min_length"]
    assert min_length(["a"], 1)
    assert not min_length([], 1)
    assert not min_length({"a": 1}, 1), "a mapping is not the array being asked about"
    assert not min_length("ab", 1)
    assert not min_length(runner.MISSING, 1)


def test_equals_does_not_conflate_booleans_with_numbers():
    equals = runner.OPS["equals"]
    assert equals(5432, 5432)
    assert not equals(True, 1)
    assert not equals(1, True)
    assert not equals("5432", 5432), "the JSON type is part of what a contract input declares"


@pytest.mark.parametrize("op,bad", [
    ("one_of", "insert"), ("one_of", []),
    ("min_length", "1"), ("min_length", True),
    ("absent", "yes"), ("present", 1),
    ("matches", "([unclosed"),
])
def test_operator_arguments_are_validated(op, bad):
    assert not runner.OP_ARGUMENT[op](bad)


def test_every_operator_declares_an_argument_check():
    assert set(runner.OPS) == set(runner.OP_ARGUMENT), (
        "a new operator without an argument check accepts anything a scenario writes")


# ---------------------------------------------------------------------------
# Scenario validation: a scenario that grades nothing must not load
# ---------------------------------------------------------------------------

BUILD = {
    "id": "s", "plugin": "analitiq-pipeline-builder", "why": "w", "prompt": "p",
    "expect": "build",
    "validate": [{"glob": "a.json", "entity": "pipeline"}],
    "docs": {"d": {"glob": "a.json"}},
    "assert": [{"doc": "d", "path": "status", "absent": True, "rule": "RULE-SHRD-004"}],
}


def _problems(overrides: dict, drop: tuple = ()):
    scenario = {**BUILD, **overrides}
    for key in drop:
        scenario.pop(key, None)
    return runner._scenario_problems(scenario, Path("s.json"))


def test_a_well_formed_build_scenario_has_no_problems():
    assert _problems({}) == []


@pytest.mark.parametrize("drop", ["validate", "assert"])
def test_a_build_scenario_must_grade_something(drop):
    """Every grading key is read with `.get`, so a dropped one deletes a whole
    half of the grading and the run still reports pass."""
    assert _problems({}, drop=(drop,)), f"dropping {drop!r} must be refused"


def test_a_mistyped_grading_key_is_refused():
    """`artifact` instead of `artifacts` silently grades nothing."""
    problems = _problems({"artifact": [{"glob": "x", "rule": "RULE-PKG-002"}]})
    assert any("unknown key" in p for p in problems)


def test_expect_must_be_one_of_the_two_shapes():
    assert any("expect must be" in p for p in _problems({"expect": "refuse"}))


def test_a_refusal_must_name_what_it_expects_to_be_cited():
    refusal = {"id": "s", "plugin": "analitiq-connector-builder", "why": "w", "prompt": "p",
               "expect": "refusal", "files": ["**/connector.json"]}
    assert any("cites" in p or "rule ids" in p
               for p in runner._scenario_problems(refusal, Path("s.json")))


def test_an_unknown_rule_id_is_refused():
    problems = _problems({"assert": [
        {"doc": "d", "path": "status", "absent": True, "rule": "RULE-NOPE-999"}]})
    assert any("RULE-NOPE-999" in p for p in problems)


def test_intent_is_accepted_as_a_rule_but_not_as_coverage():
    assert _problems({"assert": [
        {"doc": "d", "path": "status", "absent": True, "rule": runner.INTENT}]}) == []
    counted = runner.asserted_rules([{**BUILD, "assert": [
        {"doc": "d", "path": "status", "absent": True, "rule": runner.INTENT}]}])
    assert runner.INTENT not in counted


def test_a_rule_cannot_be_both_graded_and_merely_exercised():
    problems = _problems({"also_covers": ["RULE-SHRD-004"]})
    assert any("also_covers" in p for p in problems)


def test_an_assertion_must_read_a_document_docs_resolves():
    problems = _problems({"assert": [
        {"doc": "absent-name", "path": "status", "absent": True, "rule": "RULE-SHRD-004"}]})
    assert any("absent-name" in p for p in problems)


def test_a_validate_spec_names_exactly_one_selector():
    both = {"glob": "a.json", "entity": "pipeline", "schema_url": "https://x/y.json"}
    assert any("exactly one" in p for p in _problems({"validate": [both]}))
    assert any("exactly one" in p for p in _problems({"validate": [{"glob": "a.json"}]}))


def test_a_seed_source_that_does_not_exist_is_refused():
    problems = _problems({"seed": [{"from": "no/such/file.json", "to": "a.json"}]})
    assert any("does not exist" in p for p in problems)


def test_a_seed_target_cannot_escape_the_working_directory():
    problems = _problems({"seed": [
        {"from": "conftest.py", "to": "../outside.json"}]})
    assert any("escapes" in p for p in problems)


# ---------------------------------------------------------------------------
# Grading passes: each check must be able to fail
# ---------------------------------------------------------------------------

def test_check_assertions_reports_a_violation():
    docs = {"d": {"status": "draft"}}
    scenario = {"assert": [{"doc": "d", "path": "status", "absent": True, "rule": "R"}]}
    assert runner.check_assertions(scenario, docs)


def test_check_assertions_reports_an_unresolved_document():
    scenario = {"assert": [{"doc": "d", "path": "status", "absent": True, "rule": "R"}]}
    assert runner.check_assertions(scenario, {})


def test_pluck_on_a_non_list_is_a_failure_not_a_skip():
    docs = {"d": {"columns": {"id": {}}}}
    scenario = {"assert": [{"doc": "d", "path": "columns", "pluck": "name",
                            "equals": ["id"], "rule": "R"}]}
    assert runner.check_assertions(scenario, docs)


def test_pluck_reduces_a_list_of_objects(tmp_path):
    docs = {"d": DOC}
    scenario = {"assert": [{"doc": "d", "path": "columns", "pluck": "name",
                            "equals": ["id", "amount"], "rule": "R"}]}
    assert runner.check_assertions(scenario, docs) == []


def test_check_artifacts_reports_both_directions(tmp_path):
    (tmp_path / "present.txt").write_text("x")
    assert runner.check_artifacts({"artifacts": [{"glob": "gone.txt", "rule": "R"}]}, tmp_path)
    assert runner.check_artifacts(
        {"artifacts": [{"glob": "present.txt", "absent": True, "rule": "R"}]}, tmp_path)
    assert runner.check_artifacts({"artifacts": [{"glob": "present.txt", "rule": "R"}]},
                                  tmp_path) == []


def test_check_text_requires_exactly_one_file(tmp_path):
    scenario = {"text_assert": [{"file": "*.txt", "matches": "x", "rule": "R"}]}
    assert runner.check_text(scenario, tmp_path), "zero matches must fail, not pass"
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    assert runner.check_text(scenario, tmp_path), "an ambiguous glob must fail"


def test_resolve_docs_requires_exactly_one_and_honours_where(tmp_path):
    (tmp_path / "one.json").write_text(json.dumps({"connector_id": "a", "v": 1}))
    (tmp_path / "two.json").write_text(json.dumps({"connector_id": "b", "v": 2}))

    docs, problems = runner.resolve_docs({"docs": {"d": {"glob": "*.json"}}}, tmp_path)
    assert problems and not docs, "an ambiguous glob must be reported, not silently narrowed"

    docs, problems = runner.resolve_docs(
        {"docs": {"d": {"glob": "*.json", "where": {"connector_id": "b"}}}}, tmp_path)
    assert not problems and docs["d"]["v"] == 2

    docs, problems = runner.resolve_docs(
        {"docs": {"d": {"glob": "*.json", "where": {"connector_id": "zzz"}}}}, tmp_path)
    assert problems and not docs


def test_run_validator_reports_a_glob_that_matched_nothing(tmp_path):
    failures = runner.run_validator(tmp_path, {"glob": "nope/*.json", "entity": "pipeline"}, 60)
    assert failures and "no document was written" in failures[0]


def test_check_refusal_needs_the_reason_as_well_as_the_silence(tmp_path):
    scenario = {"files": ["**/connector.json"], "cites": ["RULE-CTOR-037"]}

    assert runner.check_refusal(scenario, tmp_path, "declined under RULE-CTOR-037") == []

    silent = runner.check_refusal(scenario, tmp_path, "")
    assert silent, "an empty session must not read as a refusal"

    (tmp_path / "connector.json").write_text("{}")
    wrote = runner.check_refusal(scenario, tmp_path, "declined under RULE-CTOR-037")
    assert any("authored" in f for f in wrote)


# ---------------------------------------------------------------------------
# Seeding, cleanup, recording
# ---------------------------------------------------------------------------

def test_seed_patch_replaces_and_refuses_to_insert(tmp_path):
    source = REPO_ROOT / "plugins/analitiq-pipeline-builder/skills/connection-spec/examples/db.example.json"
    rel = source.relative_to(REPO_ROOT).as_posix()

    runner.seed({"id": "s", "seed": [
        {"from": rel, "to": "c.json", "patch": {"connector_id": "patched"}}]}, tmp_path)
    assert json.loads((tmp_path / "c.json").read_text())["connector_id"] == "patched"

    with pytest.raises(SystemExit):
        runner.seed({"id": "s", "seed": [
            {"from": rel, "to": "d.json", "patch": {"connectorId": "typo"}}]}, tmp_path)


def test_discard_reports_what_it_could_not_remove(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    assert runner.discard(live) is None
    assert not live.exists()
    assert runner.discard(tmp_path / "never-existed") is not None


def test_record_appends_one_line_per_run(tmp_path):
    log = tmp_path / "results.jsonl"
    runner.record(log, {"scenario": "a", "passed": True})
    runner.record(log, {"scenario": "a", "passed": False})
    lines = log.read_text().strip().splitlines()
    assert [json.loads(line)["passed"] for line in lines] == [True, False]


def test_one_run_records_a_harness_error_instead_of_propagating(monkeypatch):
    """A crash mid-grade must cost this run, not the queue behind it."""
    monkeypatch.setattr(runner, "invoke", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    failures = runner.one_run({"id": "s", "plugin": "analitiq-pipeline-builder",
                               "expect": "build", "prompt": "p"}, keep=False, timeout=1)
    assert any("boom" in f for f in failures)


def test_one_run_records_a_session_that_did_not_finish(monkeypatch):
    monkeypatch.setattr(runner, "invoke", lambda *a, **k: (False, "timed out"))
    failures = runner.one_run({"id": "s", "plugin": "analitiq-pipeline-builder",
                               "expect": "build", "prompt": "p"}, keep=False, timeout=1)
    assert any("did not complete" in f for f in failures)


def test_the_agent_does_not_inherit_the_graders_environment(monkeypatch, tmp_path):
    """The agent must resolve the validator its users would resolve.

    `_bootstrap.py` short-circuits on ANALITIQ_VALIDATOR_FROM_SOURCE, so an
    agent handed the grader's environment validates its own work against
    in-repo source — hiding exactly the pin gap a plugin eval exists to find.
    Asserted on the environment actually handed to the subprocess, because the
    leak is a keyword argument and no reading of the file can settle it.
    """
    seen = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen[cmd[0]] = kwargs.get("env")
        return Result()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.delenv("ANALITIQ_VALIDATOR_FROM_SOURCE", raising=False)

    runner.invoke({"id": "s", "plugin": "analitiq-pipeline-builder", "prompt": "p"}, tmp_path, 5)
    agent_env = seen["claude"] or dict(os.environ)
    assert "ANALITIQ_VALIDATOR_FROM_SOURCE" not in agent_env
    assert str(REPO_ROOT / "packages") not in (agent_env.get("PYTHONPATH") or "")

    (tmp_path / "a.json").write_text("{}")
    runner.run_validator(tmp_path, {"glob": "a.json", "entity": "pipeline"}, 5)
    grader_env = seen[sys.executable]
    assert grader_env["ANALITIQ_VALIDATOR_FROM_SOURCE"] == "1", (
        "the grader keeps the in-repo source it grades against")


# ---------------------------------------------------------------------------
# Coverage arithmetic
# ---------------------------------------------------------------------------

def test_unenforced_rules_comes_from_the_registry_not_a_text_scan():
    ids = runner.unenforced_rules()
    assert ids
    assert ids < runner.known_rule_ids(), "some rules do have a validator"
    for rule_id in ids:
        assert (REPO_ROOT / "rules" / "records" / f"{rule_id}.yaml").is_file()


def test_also_covers_is_counted_as_exercised_never_as_graded():
    counted = runner.asserted_rules([{**BUILD, "also_covers": ["RULE-STRM-019"]}])
    assert counted["RULE-STRM-019"] == 0
    assert counted["RULE-SHRD-004"] == 1


def test_a_refusals_cited_rules_count_as_graded():
    counted = runner.asserted_rules([
        {"expect": "refusal", "cites": ["RULE-CTOR-037"], "files": ["x"]}])
    assert counted["RULE-CTOR-037"] == 1
