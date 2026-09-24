"""The evaluation corpora load offline and every scored pair has something to classify.

The evaluation itself is paid and run by hand; a corpus defect found here costs
nothing, where found there it costs a run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts", "render_schemas")

import eval_schema_bumps  # noqa: E402
import schema_bump_cascade as cascade  # noqa: E402
from schema_diff import diff  # noqa: E402

CASES = eval_schema_bumps.historical_cases() + eval_schema_bumps.synthetic_cases()


@pytest.mark.parametrize("case", CASES, ids=[f"{c.corpus}:{c.name}" for c in CASES])
def test_every_scored_pair_differs_and_carries_a_bump_label(case):
    assert case.label in cascade.BUMPS
    assert diff(case.old, case.new)


def test_the_labels_file_names_only_consecutive_pinned_pairs():
    scored = {c.name for c in CASES if c.corpus == "historical"}
    assert "api-endpoint 16.0.0→16.1.0" in scored
    assert "stream 18.0.2→19.0.0" not in scored


def _answering(jev_confidence: float):
    luna = {
        "model": "l", "usage": {"cost": 0},
        "choices": [{"finish_reason": "stop", "message": {"content": '{"reasoning":"r","bump":"major"}'}}],
    }

    def post(url, payload):
        if url == cascade.JEV_URL:
            answer = {"choice": "minor", "confidence": jev_confidence, "probabilities": {}}
            return 200, {"model": "j", "answers": {"bump": answer}, "usage": {"cost": 0}}
        return 200, luna

    return post


@pytest.mark.parametrize(("confidence", "counted"), [(cascade.CONFIDENCE_FLOOR, True), (0.69, False)])
def test_a_stage1_miss_counts_against_the_floor_only_when_stage1_was_final(confidence, counted):
    case = eval_schema_bumps.Case("synthetic", "probe", {"type": "object"}, {"type": "string"}, "major")
    assert eval_schema_bumps._score(case, _answering(confidence))["stage1_confident_miss"] is counted


@pytest.mark.parametrize("runs", ["0", "-1"])
def test_a_run_count_below_one_is_refused_before_any_call(monkeypatch, runs):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(cascade, "openrouter_post", lambda api_key: pytest.fail("no call expected"))
    with pytest.raises(SystemExit) as refused:
        eval_schema_bumps.main(["--runs", runs])
    assert refused.value.code == 2
