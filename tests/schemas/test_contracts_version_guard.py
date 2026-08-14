"""Pin the wiring and verdict semantics of scripts/check_contracts_version_pin.py.

The guard's network half runs only in CI (`contracts-version-guard` job), so
its verdict logic — published-vs-committed divergence, the pin comparison,
strict-vs-warn windows, missing-object handling — would otherwise only ever
execute against live healthy data, where an inverted comparison is a
permanent false green. Same charter as test_engine_grammar_guard.py next
door: every verdict branch offline, with the fetch monkeypatched out, plus
the readers against the real working tree and the CI job's wiring.
"""
from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_contracts_version_pin.py"
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"


@pytest.fixture(scope="module")
def guard():
    spec = spec_from_file_location("check_contracts_version_pin", _SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _plain_env(monkeypatch):
    # No ambient strictness (CI pytest runs carry no
    # CONTRACTS_VERSION_GUARD_STRICT, but a dev shell might), and no Actions
    # annotations: on a real runner GITHUB_STEP_SUMMARY exists and
    # `_surface_warning` would append to the live job summary — pytest
    # captures stdout, not file writes.
    monkeypatch.delenv("CONTRACTS_VERSION_GUARD_STRICT", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.fixture(autouse=True)
def _no_real_polling(guard, monkeypatch):
    # Strict-mode tests exercise the poll loop's LOGIC; a real 8-minute
    # deadline or 30-second sleep would hang the suite on the branches that
    # deliberately never converge.
    monkeypatch.setattr(guard, "POLL_DEADLINE_SECONDS", 0.0)
    monkeypatch.setattr(guard, "POLL_INTERVAL_SECONDS", 0.0)


def _fact(guard, version: str) -> bytes:
    return json.dumps({guard.CONTRACTS_VERSION_KEY: version}).encode()


def _stub_fetch(guard, monkeypatch, responses: list) -> list:
    """Serve `responses` in order (bytes returned, exceptions raised; the
    last one repeats); record every call."""
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        item = responses[min(len(calls), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(guard, "_fetch", fetch)
    return calls


# --- the readers, against the real working tree ---------------------------


def test_committed_stamp_and_pyproject_agree_on_the_real_tree(guard):
    # The render check owns this equality; the guard re-derives it as its
    # baseline. Green here means the guard can run at all on this tree.
    assert guard.read_committed_stamp() == guard.read_shipped_version()


def test_reads_the_pin_from_its_single_source(guard):
    # `_bootstrap.py` owns the pin; the guard references it by regex. If that
    # file is refactored so the regex misses, this fails here instead of the
    # guard erroring in CI.
    assert guard.read_pin_version() != ""


# --- verdicts, fetch stubbed ----------------------------------------------


def test_healthy_publication_passes(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, [_fact(guard, guard.read_committed_stamp())])
    assert guard.main() == 0
    assert "OK: published == committed == pin" in capsys.readouterr().out


def test_published_mismatch_warns_on_ordinary_prs(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, [_fact(guard, "0.0.0")])
    assert guard.main() == 0
    out = capsys.readouterr().out
    assert "WINDOW:" in out and "0.0.0" in out


def test_published_mismatch_fails_strict_after_the_poll_window(
    guard, monkeypatch, capsys
):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    _stub_fetch(guard, monkeypatch, [_fact(guard, "0.0.0")])
    assert guard.main() == 1
    assert "DIVERGENCE" in capsys.readouterr().err


def test_strict_polls_past_a_publish_in_flight(guard, monkeypatch, capsys):
    """The push that bumps the stamp races its own schemas publish; the poll
    is what turns that race into a wait instead of a false red."""
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    monkeypatch.setattr(guard, "POLL_DEADLINE_SECONDS", 30.0)
    committed = guard.read_committed_stamp()
    calls = _stub_fetch(
        guard, monkeypatch, [_fact(guard, "0.0.0"), _fact(guard, committed)]
    )
    assert guard.main() == 0
    assert len(calls) == 2, "the second fetch is the poll retry"
    assert "OK: published == committed == pin" in capsys.readouterr().out


def test_unpublished_object_warns_on_ordinary_prs(guard, monkeypatch, capsys):
    # The bootstrap state: the change introducing the stamp has not reached
    # main yet, so the CDN has no object to serve. CloudFront reports that as
    # 403 or 404 depending on bucket policy; both arrive here as NotPublished.
    _stub_fetch(guard, monkeypatch, [guard.NotPublished("HTTP 403")])
    assert guard.main() == 0
    assert "not published" in capsys.readouterr().out


def test_unpublished_object_fails_strict(guard, monkeypatch, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    _stub_fetch(guard, monkeypatch, [guard.NotPublished("HTTP 404")])
    assert guard.main() == 1
    assert "schemas-publish" in capsys.readouterr().err


def test_pin_lag_fails_strict_naming_the_catch_up(guard, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    stale_pin = tmp_path / "_bootstrap.py"
    stale_pin.write_text('VALIDATOR_PIN = "analitiq-validator==0.0.1"\n')
    monkeypatch.setattr(guard, "PIN_SOURCE", stale_pin)
    _stub_fetch(guard, monkeypatch, [_fact(guard, guard.read_committed_stamp())])
    assert guard.main() == 1
    err = capsys.readouterr().err
    assert "VALIDATOR_PIN" in err and "catch-up" in err


def test_pin_lag_warns_on_ordinary_prs(guard, monkeypatch, tmp_path, capsys):
    stale_pin = tmp_path / "_bootstrap.py"
    stale_pin.write_text('VALIDATOR_PIN = "analitiq-validator==0.0.1"\n')
    monkeypatch.setattr(guard, "PIN_SOURCE", stale_pin)
    _stub_fetch(guard, monkeypatch, [_fact(guard, guard.read_committed_stamp())])
    assert guard.main() == 0
    assert "WINDOW:" in capsys.readouterr().out


def test_warning_is_annotated_on_actions(guard, monkeypatch, tmp_path, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _stub_fetch(guard, monkeypatch, [_fact(guard, "0.0.0")])
    assert guard.main() == 0
    assert "::warning" in capsys.readouterr().out
    assert summary.read_text().startswith("⚠️")


# --- infrastructure failures are exit 2, never a verdict -------------------


def test_malformed_published_json_is_a_guard_error(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, [b"not json"])
    assert guard.main() == 2
    assert "GUARD ERROR" in capsys.readouterr().err


def test_published_object_without_the_key_is_a_guard_error(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, [b'{"something": "else"}'])
    assert guard.main() == 2
    assert guard.CONTRACTS_VERSION_KEY in capsys.readouterr().err


def test_fetch_failure_is_a_guard_error(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, [guard.GuardError("fetch failed: timeout")])
    assert guard.main() == 2
    assert "GUARD ERROR" in capsys.readouterr().err


def test_committed_vs_pyproject_disagreement_is_a_guard_error(
    guard, monkeypatch, tmp_path, capsys
):
    # The render check owns that equality; the guard must refuse to mint any
    # verdict about the published copy from a baseline the repo itself
    # cannot agree on — and must not touch the network doing so.
    diverged = tmp_path / "pyproject.toml"
    diverged.write_text('[project]\nversion = "999.0.0"\n')
    monkeypatch.setattr(guard, "PYPROJECT_PATH", diverged)
    calls = _stub_fetch(guard, monkeypatch, [_fact(guard, "999.0.0")])
    assert guard.main() == 2
    assert "render_schemas.py contracts-version" in capsys.readouterr().err
    assert not calls, "no verdict input may be fetched for an unusable baseline"


def test_unrecognized_strict_value_is_a_guard_error(guard, monkeypatch, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "true")
    _stub_fetch(guard, monkeypatch, [_fact(guard, guard.read_committed_stamp())])
    assert guard.main() == 2
    assert "not recognized" in capsys.readouterr().err


def test_unexpected_exception_is_a_guard_error_not_a_verdict(
    guard, monkeypatch, capsys
):
    def boom(*_args, **_kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(guard, "fetch_published", boom)
    assert guard.main() == 2
    assert "unexpected" in capsys.readouterr().err


def test_fetch_refuses_a_url_outside_the_pinned_base(guard):
    """The refusal is what the urlopen suppression rests on — pin it.

    Every other test stubs `_fetch` wholesale, so without this the branch
    could be deleted and nothing would notice. The lookalike host covers the
    sharp edge: a bare startswith(BASE_URL) would admit a host that merely
    begins with the pinned one.
    """
    for url in (
        "https://evil.example/contracts-version.json",
        "https://schemas.analitiq.ai.evil.example/contracts-version.json",
        "http://schemas.analitiq.ai/contracts-version.json",
    ):
        with pytest.raises(guard.GuardError, match="refusing non-"):
            guard._fetch(url)


# --- CI wiring --------------------------------------------------------------


def test_ci_job_runs_the_guard_with_the_strictness_key():
    workflow = _WORKFLOW.read_text()
    assert "contracts-version-guard:" in workflow
    assert "check_contracts_version_pin.py" in workflow
    assert "CONTRACTS_VERSION_GUARD_STRICT" in workflow
    # Strict exactly where pinned-validator-guard is strict: pushes (a stale
    # published fact on main is live divergence) and release-please branches.
    strict_line = next(
        line for line in workflow.splitlines()
        if "CONTRACTS_VERSION_GUARD_STRICT:" in line
    )
    assert "github.event_name == 'push'" in strict_line
    assert "release-please--" in strict_line
