"""Pin the verdict semantics of scripts/check_engine_grammar_pin.py.

The guard's network half runs only in CI (`engine-grammar-pin-guard` job), so
its direction logic — newer publish = notice, pin-ahead = failure, malformed
anything = GuardError — would otherwise only ever execute against live healthy
data, where an inverted comparison is a permanent false green. Same charter as
test_validator_pin_guard.py: every verdict branch offline, with the fetch
monkeypatched out. The offline hash check itself is pinned by
packages/contract-models/tests/unit/test_arrow_grammar.py.
"""
from __future__ import annotations

import hashlib
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts")

_SCRIPT = REPO_ROOT / "scripts" / "check_engine_grammar_pin.py"
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"


@pytest.fixture(scope="module")
def guard():
    spec = spec_from_file_location("check_engine_grammar_pin", _SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.arrow_grammar is not None, "vendored grammar failed to import"
    return module


def _grammar_bytes(guard):
    return guard.arrow_grammar._GRAMMAR_PATH.read_bytes()


def _matrix_bytes(guard):
    """A synthetic 29x29 grid over exactly the grammar families, with the
    module's matrix pin pointed at it (monkeypatched per-test)."""
    families = list(guard.arrow_grammar.FAMILY_NAMES)
    grid = {row: {col: [] for col in families} for row in families}
    return json.dumps(grid).encode()


def _urls(guard):
    ag = guard.arrow_grammar
    base = guard.BASE_URL
    return {
        "grammar": f"{base}/{ag.ENGINE_GRAMMAR_RESOURCE}/v{ag.ENGINE_GRAMMAR_VERSION}/{ag.ENGINE_GRAMMAR_FILENAME}",
        "grammar_latest": f"{base}/{ag.ENGINE_GRAMMAR_RESOURCE}/latest.json",
        "matrix": f"{base}/{ag.CONVERSION_MATRIX_RESOURCE}/v{ag.CONVERSION_MATRIX_VERSION}/{ag.CONVERSION_MATRIX_FILENAME}",
        "matrix_latest": f"{base}/{ag.CONVERSION_MATRIX_RESOURCE}/latest.json",
    }


def _stub_fetch(guard, monkeypatch, *, matrix_bytes, grammar_latest, matrix_latest):
    urls = _urls(guard)
    responses = {
        urls["grammar"]: _grammar_bytes(guard),
        urls["matrix"]: matrix_bytes,
        urls["grammar_latest"]: json.dumps({"version": grammar_latest}).encode(),
        urls["matrix_latest"]: json.dumps({"version": matrix_latest}).encode(),
    }
    monkeypatch.setattr(guard, "_fetch", lambda url: responses[url])
    monkeypatch.setattr(
        guard.arrow_grammar,
        "CONVERSION_MATRIX_SHA256",
        hashlib.sha256(matrix_bytes).hexdigest(),
    )


def test_healthy_publication_passes(guard, monkeypatch, capsys):
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard),
        grammar_latest=guard.arrow_grammar.ENGINE_GRAMMAR_VERSION,
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 0
    assert "::notice::" not in capsys.readouterr().out


def test_newer_engine_publication_is_a_notice_not_a_failure(guard, monkeypatch, capsys):
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard),
        grammar_latest="9.0.0",
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 0
    assert "::notice::" in capsys.readouterr().out


def test_pin_ahead_of_published_latest_fails(guard, monkeypatch, capsys):
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard),
        grammar_latest="0.9.0",
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 1
    assert "AHEAD" in capsys.readouterr().err


def test_matrix_family_divergence_fails(guard, monkeypatch, capsys):
    families = list(guard.arrow_grammar.FAMILY_NAMES)[:-1] + ["Struct"]
    grid = {row: {col: [] for col in families} for row in families}
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(grid).encode(),
        grammar_latest=guard.arrow_grammar.ENGINE_GRAMMAR_VERSION,
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 1
    assert "family keys != grammar families" in capsys.readouterr().err


def test_malformed_matrix_is_a_guard_error(guard, monkeypatch, capsys):
    # sha256 pin matches the bytes, but the payload is not a dict-of-dicts —
    # must classify as "guard could not run" (2), never a divergence verdict.
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=b'"not a grid"',
        grammar_latest=guard.arrow_grammar.ENGINE_GRAMMAR_VERSION,
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    assert "could not run" in capsys.readouterr().err


def test_malformed_latest_version_is_a_guard_error(guard, monkeypatch, capsys):
    # "1.0" would compare (1,0) < (1,0,0) and fabricate a pin-AHEAD verdict if
    # it were parsed leniently.
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard),
        grammar_latest="1.0",
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "unparseable version" in err


def test_offline_hash_mismatch_fails(guard, monkeypatch, capsys):
    monkeypatch.setattr(guard.arrow_grammar, "ENGINE_GRAMMAR_SHA256", "0" * 64)
    assert guard.main(["--offline"]) == 1
    assert "must move together" in capsys.readouterr().err


def test_ci_job_is_wired():
    workflow = _WORKFLOW.read_text()
    assert "engine-grammar-pin-guard:" in workflow
    assert "check_engine_grammar_pin.py" in workflow
