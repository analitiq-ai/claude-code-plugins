"""The cross-document rule case corpus, graded through the functions it ships with.

`analitiq.validator.rule_cases` is what a consumer of the wheel calls to check
the validator it pins, so this suite calls the same `rule_cases` and
`case_mismatch` rather than a loader of its own.

Deliberately one-directional: no record field claims validator cases, so
nothing here requires a validator-bound rule to carry them, and deleting a
rule's case directory removes its coverage without failing this suite.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from analitiq.contracts.shared.rules import all_rules
from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL
from analitiq.validator import TYPE_MAP_FILENAME
import analitiq.validator.rule_cases as corpus
from analitiq.validator.rule_cases import RuleCase, case_mismatch, rule_cases

CORPUS = Path(__file__).resolve().parent / "corpus"


def test_the_corpus_module_is_reachable_by_its_dotted_name():
    """`import analitiq.validator.rule_cases as m` binds the package attribute
    of that name, so a package-level name shadowing the module hands the
    consumer something other than the module."""
    assert isinstance(corpus, ModuleType)


def test_the_corpus_is_not_empty():
    assert rule_cases()


@pytest.mark.parametrize("case", rule_cases(), ids=lambda c: f"{c.rule_id}/{c.verdict}/{c.name}")
def test_case_matches_enforcement(case):
    assert case_mismatch(case) is None


def test_every_rule_with_cases_has_two_of_each_verdict():
    cases = rule_cases()
    for rule_id in sorted({c.rule_id for c in cases}):
        for verdict in ("valid", "invalid"):
            found = [c for c in cases if c.rule_id == rule_id and c.verdict == verdict]
            assert len(found) >= 2, f"{rule_id}: {len(found)} {verdict} cases, need >= 2"


# --- Grading ----------------------------------------------------------------


def _case(rule_id: str, verdict: str) -> RuleCase:
    case = next(
        (c for c in rule_cases() if c.rule_id == rule_id and c.verdict == verdict),
        None,
    )
    assert case is not None, f"no {verdict} case for {rule_id} in the corpus"
    return case


def test_a_valid_verdict_on_a_violating_case_is_a_mismatch():
    invalid = _case("RULE-PIPE-011", "invalid")
    assert case_mismatch(RuleCase("RULE-PIPE-011", "valid", invalid.name, invalid.root))


def test_an_invalid_verdict_on_a_clean_case_is_a_mismatch():
    valid = _case("RULE-PIPE-011", "valid")
    assert case_mismatch(RuleCase("RULE-PIPE-011", "invalid", valid.name, valid.root))


def test_a_valid_case_failing_another_rule_is_a_mismatch():
    """A valid case must pass as a whole, not merely stay silent about its rule."""
    other = _case("RULE-STRM-033", "invalid")
    assert case_mismatch(RuleCase("RULE-PIPE-011", "valid", other.name, other.root))


@pytest.mark.parametrize("verdict", ["valid", "invalid"])
def test_a_finding_naming_the_rule_without_failing_is_a_mismatch(monkeypatch, verdict):
    """An informational finding costs no pass, so only its rule id grades it:
    an invalid case needs a fail, and a valid case may not name its rule at all."""
    informational = {"rule": "RULE-PIPE-011", "kind": "informational", "message": "probe"}
    monkeypatch.setattr(corpus, "case_findings", lambda case: [informational])
    assert case_mismatch(_case("RULE-PIPE-011", verdict))


def _connector_package(root: Path, *, covered: bool) -> Path:
    """A connector package whose read map covers its endpoint's native type or not."""
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": "STRING", "arrow_type": "Utf8"},
    }
    native = "STRING" if covered else "TEXT"
    type_map = {
        "$schema": TYPE_MAP_SCHEMA_URL,
        "read": [{"match": "exact", "native_type": native, "arrow_type": "Utf8"}],
    }
    (root / "endpoints").mkdir(parents=True)
    (root / "connector.json").write_text(json.dumps(connector))
    (root / TYPE_MAP_FILENAME).write_text(json.dumps(type_map))
    (root / "endpoints" / f"{endpoint['endpoint_id']}.json").write_text(json.dumps(endpoint))
    return root


def test_a_connector_case_is_graded_with_its_sibling_files(tmp_path):
    """`connector.json` is validated at its path, so the sibling read map and
    endpoints are what coverage reads — without them no coverage rule could
    fail, and a valid case would not pass."""
    covered = _connector_package(tmp_path / "covered", covered=True)
    uncovered = _connector_package(tmp_path / "uncovered", covered=False)
    assert case_mismatch(RuleCase("RULE-PKG-033", "valid", "covered", covered)) is None
    assert case_mismatch(RuleCase("RULE-PKG-033", "invalid", "uncovered", uncovered)) is None


# --- Layout refusals ----------------------------------------------------------


def _write_case(root: Path, rule_id: str, group: str, name: str, *entries: str) -> None:
    case_root = root / rule_id / group / name
    case_root.mkdir(parents=True)
    for entry in entries:
        (case_root / entry).write_text("{}")


def _load_from(monkeypatch, root: Path):
    monkeypatch.setattr(corpus, "CASES_DIR", root)
    return corpus.rule_cases()


def test_a_directory_for_an_unknown_rule_is_refused(monkeypatch, tmp_path):
    _write_case(tmp_path, "RULE-NONE-999", "valid", "a", "bundle.json")
    with pytest.raises(ValueError, match="RULE-NONE-999"):
        _load_from(monkeypatch, tmp_path)


def test_a_rule_not_enforced_by_a_validator_check_is_refused(monkeypatch, tmp_path):
    rule = next(
        (
            r for r in all_rules()
            if not (r.validator or "").startswith("analitiq.validator.")
        ),
        None,
    )
    assert rule is not None, (
        "every rule is enforced by a validator check, so there is no rule "
        "this refusal could be shown on — this test grades nothing"
    )
    _write_case(tmp_path, rule.id, "valid", "a", "bundle.json")
    with pytest.raises(ValueError, match=rule.id):
        _load_from(monkeypatch, tmp_path)


def test_a_group_other_than_a_verdict_is_refused(monkeypatch, tmp_path):
    _write_case(tmp_path, "RULE-PIPE-011", "maybe", "a", "bundle.json")
    with pytest.raises(ValueError, match="maybe"):
        _load_from(monkeypatch, tmp_path)


@pytest.mark.parametrize("entries", [(), ("bundle.json", "connector.json")], ids=["neither", "both"])
def test_a_case_needs_exactly_one_entry_file(monkeypatch, tmp_path, entries):
    _write_case(tmp_path, "RULE-PIPE-011", "valid", "a", *entries)
    with pytest.raises(ValueError, match="entry file"):
        _load_from(monkeypatch, tmp_path)


def test_a_bundle_case_holding_another_file_is_refused(monkeypatch, tmp_path):
    """A bundle is validated from `bundle.json` alone, so a document beside it
    would grade nothing."""
    _write_case(tmp_path, "RULE-PIPE-011", "valid", "a", "bundle.json")
    stray = tmp_path / "RULE-PIPE-011" / "valid" / "a" / "endpoints" / "x.json"
    stray.parent.mkdir()
    stray.write_text("{}")
    with pytest.raises(ValueError, match="endpoints/x.json"):
        _load_from(monkeypatch, tmp_path)
