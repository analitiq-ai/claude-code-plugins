"""Pin the wiring and verdict semantics of `scripts/check_contracts_version_pin.py`.

The guard's network half runs only in CI (`contracts-version-guard` job), so
its verdict logic — published-vs-committed byte equality, the stamp-vs-package
comparison, strict-vs-warn windows, missing-object handling, its probe corroboration and
the strict-mode retry loop — would otherwise only ever execute against live
healthy data, where an inverted comparison is a permanent false green. Same
charter as `test_engine_grammar_guard.py` next door: every verdict branch
offline, with the fetch monkeypatched out and the retry loop's clock replaced
so no test sleeps in real time, plus the readers against the real working
tree and the CI job's wiring.
"""
from __future__ import annotations

import io
import json
import math
import re
import urllib.error
import urllib.request
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_contracts_version_pin.py"
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"
_PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "schemas-publish.yml"


def _load(path: Path):
    spec = spec_from_file_location(path.stem, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guard():
    return _load(_SCRIPT)


@pytest.fixture(autouse=True)
def _plain_env(monkeypatch):
    # No ambient strictness (CI pytest runs carry no
    # CONTRACTS_VERSION_GUARD_STRICT, but a dev shell might), and no Actions
    # annotations: on a real runner GITHUB_STEP_SUMMARY exists and
    # `surface_warning` would append to the live job summary — pytest
    # captures stdout, not file writes.
    monkeypatch.delenv("CONTRACTS_VERSION_GUARD_STRICT", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.fixture(autouse=True)
def _fake_clock(guard, monkeypatch):
    """Replace the retry loop's clock with one `_sleep` advances itself.

    Every strict-mode test that reaches `fetch_published_with_retry` runs
    the loop to one of its two ends — a match or the exhausted budget — and
    without this, a permanently-diverging stub would block each such test
    for RETRY_BUDGET_SECONDS of real `time.sleep`. The fake clock keeps the
    loop's own termination logic (compare against a real deadline, sleep the
    remaining budget on the last lap) exercised, just against simulated
    elapsed time instead of the wall clock.
    """
    clock = [0.0]
    monkeypatch.setattr(guard, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(guard, "_sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))


def _fact(guard, version: str) -> bytes:
    return json.dumps({guard.CONTRACTS_VERSION_KEY: version}).encode()


def _redigested_stamp(guard) -> bytes:
    """The committed stamp with ONLY its tree digest altered — the state a
    publish leaves behind when a render landed but the upload did not.

    Serialized exactly as the renderer writes (indent, key order, trailing
    newline) so the digest value is the sole differing byte range — anything
    looser and these tests would pass on any serialization difference
    without isolating the digest at all."""
    committed = guard.COMMITTED_PATH.read_bytes()
    doc = json.loads(committed)

    def render(d: dict) -> bytes:
        return (json.dumps(d, indent=2, sort_keys=True) + "\n").encode()

    # The round-trip pin: these serialization args are a copy of the
    # renderer's write format, and this is what fails if the renderer's
    # format ever moves — without it the fixture silently degrades into a
    # generic byte-mismatch and stops isolating the digest.
    assert render(doc) == committed, "fixture serialization drifted from the renderer's"
    doc["tree_sha256"] = "0" * 64
    return render(doc)


def _stub_fetch(guard, monkeypatch, stamp, probe=b"{}") -> list:
    """Stub `_fetch` per URL: `stamp` serves the stamp URL (bytes returned,
    exception raised), `probe` the probe URL. Returns the call log."""
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        item = stamp if url == guard.PUBLISHED_URL else probe
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(guard, "_fetch", fetch)
    return calls


# --- verdicts, fetch stubbed ----------------------------------------------


def test_healthy_publication_passes(guard, monkeypatch, capsys):
    calls = _stub_fetch(guard, monkeypatch, guard.COMMITTED_PATH.read_bytes())
    assert guard.main() == 0
    assert "OK: published stamp == committed stamp" in capsys.readouterr().out
    assert calls == [guard.PUBLISHED_URL], "a served stamp needs no second probe"


def test_published_mismatch_warns_on_ordinary_prs(guard, monkeypatch, capsys):
    calls = _stub_fetch(guard, monkeypatch, _fact(guard, "0.0.0"))
    assert guard.main() == 0
    out = capsys.readouterr().out
    assert "WINDOW:" in out and "0.0.0" in out
    # The warn arm must not carry the strict arm's remediation.
    assert "workflow_dispatch" not in out
    assert len(calls) == 1, "warn mode fetches once — no retry loop"


def test_published_mismatch_fails_strict(guard, monkeypatch, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    calls = _stub_fetch(guard, monkeypatch, _fact(guard, "0.0.0"))
    assert guard.main() == 1
    err = capsys.readouterr().err
    assert "DIVERGENCE" in err and "workflow_dispatch" in err
    # A permanently diverging stamp must be retried across the whole budget,
    # not failed on the first sample — otherwise this is the exact race the
    # loop exists to close.
    assert len(calls) > 1


def test_strict_retry_recovers_once_the_pointer_catches_up(guard, monkeypatch, capsys):
    # The state the loop exists for: the publish already landed, the CDN
    # just hadn't caught up yet when the first sample was taken. The match
    # sits BEFORE a still-diverging response, never consumed, so this proves
    # the loop stops as soon as the bytes agree rather than always taking a
    # fixed number of laps — a fixed-count bug would consume the trailing
    # diverging response too and fail the `calls` / exit-code assertions
    # below instead of stopping at two.
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    responses = iter(
        [_fact(guard, "0.0.0"), guard.COMMITTED_PATH.read_bytes(), _fact(guard, "0.0.0")]
    )
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return next(responses) if url == guard.PUBLISHED_URL else b"{}"  # skipcq: PTC-W0063

    monkeypatch.setattr(guard, "_fetch", fetch)
    assert guard.main() == 0
    assert calls == [guard.PUBLISHED_URL] * 2
    assert "OK: published stamp == committed stamp" in capsys.readouterr().out


def test_same_release_stale_digest_fails_strict(guard, monkeypatch, capsys):
    # The state the digest half exists to expose: a render landed on main,
    # the publish did not, and the version half alone would read as green.
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    _stub_fetch(guard, monkeypatch, _redigested_stamp(guard))
    assert guard.main() == 1
    err = capsys.readouterr().err
    assert "tree digest" in err and "workflow_dispatch" in err


def test_strict_retry_reports_progress_and_terminates(guard, monkeypatch, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    calls = _stub_fetch(guard, monkeypatch, _fact(guard, "0.0.0"))
    assert guard.main() == 1
    out = capsys.readouterr().out
    assert "retrying in" in out and "retry window" in out
    # Exact, not just bounded: an off-by-one in the deadline/loop arithmetic
    # (stopping one lap early, or looping one lap too many) must fail this
    # rather than slip through a padded inequality. The retry window is one
    # interval wider than the budget (fetch_published_with_retry's margin
    # against the CDN's own expiry boundary), and under the fake clock the
    # loop samples once per interval across that window plus the initial
    # sample.
    window = guard.RETRY_BUDGET_SECONDS + guard.RETRY_INTERVAL_SECONDS
    assert len(calls) == math.ceil(window / guard.RETRY_INTERVAL_SECONDS) + 1


def test_strict_retry_reports_the_final_sample_not_a_cached_first_one(
    guard, monkeypatch, capsys
):
    # Each call returns a distinct, still-diverging value — a bug that
    # cached the first response (while still making real, discarded fetches
    # each lap to keep the call count looking right) would report "0.0.0"
    # here instead of the last value actually sampled.
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return (
            _fact(guard, f"0.0.{len(calls) - 1}") if url == guard.PUBLISHED_URL else b"{}"
        )

    monkeypatch.setattr(guard, "_fetch", fetch)
    assert guard.main() == 1
    err = capsys.readouterr().err
    assert f"0.0.{len(calls) - 1}" in err


def test_same_release_stale_digest_warns_on_ordinary_prs(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, _redigested_stamp(guard))
    assert guard.main() == 0
    out = capsys.readouterr().out
    assert "WINDOW:" in out and "workflow_dispatch" not in out


def test_unpublished_stamp_warns_on_ordinary_prs(guard, monkeypatch, capsys):
    # The bootstrap state: the change introducing the stamp has not reached
    # main yet, so the CDN has no stamp to serve — corroborated by the
    # probe, which IS served.
    calls = _stub_fetch(guard, monkeypatch, guard.NotPublished("HTTP 403"))
    assert guard.main() == 0
    assert guard.PUBLISHED_URL in capsys.readouterr().out
    assert calls == [guard.PUBLISHED_URL, guard.PROBE_URL]


def test_unpublished_stamp_fails_strict(guard, monkeypatch, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    _stub_fetch(guard, monkeypatch, guard.NotPublished("HTTP 404"))
    assert guard.main() == 1
    assert "schemas-publish.yml" in capsys.readouterr().err


def test_missing_probe_is_a_guard_error_not_a_missing_stamp(
    guard, monkeypatch, capsys
):
    """A 403 on every key is an access fault. Believing the stamp-side 403
    would mint the divergence verdict with a re-run-the-publish remediation
    that cannot fix it — the exact conflation the probe exists to refuse.
    """
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    _stub_fetch(
        guard,
        monkeypatch,
        guard.NotPublished("HTTP 403"),
        probe=guard.NotPublished("HTTP 403"),
    )
    assert guard.main() == 2
    assert "GUARD ERROR" in capsys.readouterr().err


def test_warning_is_annotated_on_actions(guard, monkeypatch, tmp_path, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _stub_fetch(guard, monkeypatch, _fact(guard, "0.0.0"))
    assert guard.main() == 0
    assert "::warning" in capsys.readouterr().out
    assert summary.read_text().startswith("⚠️")


# --- the HTTP classification `_fetch` itself performs ----------------------


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "status", None, io.BytesIO(b""))


@pytest.mark.parametrize("code", [403, 404])
def test_missing_object_statuses_classify_as_not_published(
    guard, monkeypatch, code
):
    # Every verdict test stubs `_fetch` wholesale, so the 403/404 split —
    # which decides warn-and-pass vs GuardError on every ordinary PR until
    # the stamp reaches main — must be executed here or it is never executed
    # at all. Patched on urllib.request itself: the fetch lives in
    # _guard_lib, and both resolve the same module object.
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout: (_ for _ in ()).throw(_http_error(url, code)),
    )
    with pytest.raises(guard.NotPublished):
        guard._fetch(guard.PUBLISHED_URL)


def test_other_http_statuses_classify_as_guard_errors(guard, monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout: (_ for _ in ()).throw(
            _http_error(guard.PUBLISHED_URL, 500)
        ),
    )
    with pytest.raises(guard.GuardError) as exc_info:
        guard._fetch(guard.PUBLISHED_URL)
    # NotPublished subclasses GuardError, so the raises() alone would pass
    # even if a 5xx were misclassified as a missing object — which would turn
    # a CDN fault into warn-and-pass on PRs and a false divergence on strict
    # runs. The exclusion is the discriminating half of this test.
    assert not isinstance(exc_info.value, guard.NotPublished)


# --- infrastructure failures are exit 2, never a verdict -------------------


def test_malformed_published_json_is_a_guard_error(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, b"not json")
    assert guard.main() == 2
    assert "GUARD ERROR" in capsys.readouterr().err


def test_non_object_published_json_is_a_guard_error(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, b"[1, 2]")
    assert guard.main() == 2
    assert "GUARD ERROR" in capsys.readouterr().err


def test_published_object_without_the_key_is_a_guard_error(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, b'{"something": "else"}')
    assert guard.main() == 2
    assert guard.CONTRACTS_VERSION_KEY in capsys.readouterr().err


def test_fetch_failure_is_a_guard_error(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, guard.GuardError("fetch failed: timeout"))
    assert guard.main() == 2
    assert "GUARD ERROR" in capsys.readouterr().err


def _pyproject_ahead_of_the_stamp(guard, monkeypatch, tmp_path) -> None:
    """A package release merged and the schema release has not re-stamped yet."""
    ahead = tmp_path / "pyproject.toml"
    ahead.write_text('[project]\nversion = "999.0.0"\n')
    monkeypatch.setattr(guard, "PYPROJECT_PATH", ahead)
    _stub_fetch(guard, monkeypatch, guard.COMMITTED_PATH.read_bytes())


def test_a_stamp_behind_the_package_fails_strict(guard, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    _pyproject_ahead_of_the_stamp(guard, monkeypatch, tmp_path)
    assert guard.main() == 1
    assert "schema release" in capsys.readouterr().err


def test_a_stamp_behind_the_package_warns_on_ordinary_prs(guard, monkeypatch, tmp_path, capsys):
    _pyproject_ahead_of_the_stamp(guard, monkeypatch, tmp_path)
    assert guard.main() == 0
    assert "WINDOW" in capsys.readouterr().out


def test_unrecognized_strict_value_is_a_guard_error(guard, monkeypatch, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "true")
    _stub_fetch(guard, monkeypatch, guard.COMMITTED_PATH.read_bytes())
    assert guard.main() == 2
    assert "CONTRACTS_VERSION_GUARD_STRICT" in capsys.readouterr().err


def test_unexpected_exception_is_a_guard_error_with_a_traceback(
    guard, monkeypatch, capsys
):
    def boom(*_args, **_kwargs):
        raise ValueError("boom-sentinel")

    monkeypatch.setattr(guard, "fetch_published", boom)
    assert guard.main() == 2
    err = capsys.readouterr().err
    assert "boom-sentinel" in err
    assert "DIVERGENCE" not in err


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
        with pytest.raises(guard.GuardError, match=re.escape(guard.BASE_URL)):
            guard._fetch(url)


# --- CI wiring --------------------------------------------------------------


def test_ci_job_runs_the_guard_with_the_strictness_key():
    workflow = _WORKFLOW.read_text()
    assert "contracts-version-guard:" in workflow
    assert "check_contracts_version_pin.py" in workflow
    # A vanished anchor must red the build, not be handled — the StopIteration
    # IS the non-vacuity check on every bare next() in this module.
    strict_line = next(  # skipcq: PTC-W0063
        line for line in workflow.splitlines()
        if "CONTRACTS_VERSION_GUARD_STRICT:" in line
    )
    assert "github.event_name == 'push'" in strict_line
    assert "release-please--" in strict_line


def test_retry_budget_covers_the_publish_ttl(guard):
    # RETRY_BUDGET_SECONDS is a hand-maintained copy of the mutable-pointer
    # cache-control max-age schemas-publish.yml uploads with, so this reads
    # the owner back rather than trusting the docstring's claim: a budget
    # shorter than the TTL would still red every schemas-touching push on
    # the propagation window alone.
    publish = _PUBLISH_WORKFLOW.read_text()
    cache_line = next(  # skipcq: PTC-W0063
        line for line in publish.splitlines() if 'mutable_cache="public, max-age=' in line
    )
    match = re.search(r"max-age=(\d+)", cache_line)
    assert match, "schemas-publish.yml's mutable_cache line lost its max-age"
    ttl_seconds = int(match.group(1))
    assert guard.RETRY_BUDGET_SECONDS >= ttl_seconds


def test_publish_workflow_uploads_the_stamp_last_as_json():
    # The stamp is the tree-wide pointer: it must land only after every
    # other object, or a publish that dies mid-tree leaves a fresh stamp
    # over a half-uploaded bucket and the guard reads green over exactly
    # the state it exists to catch. Pinned here because no other test reads
    # this workflow: the loop's find must exclude the stamp, and the
    # dedicated upload after the count check must serve it as JSON.
    publish = _PUBLISH_WORKFLOW.read_text()
    lines = publish.splitlines()
    find_line = next(  # skipcq: PTC-W0063
        l for l in lines if "! -name 'contracts-version.json'" in l
    )
    assert "find ." in find_line
    stamp_cp = next(  # skipcq: PTC-W0063
        l for l in lines if "aws s3 cp ./contracts-version.json" in l
    )
    assert 'application/json' in stamp_cp
    assert lines.index(stamp_cp) > lines.index(find_line), (
        "the stamp upload must sit after the loop it is excluded from"
    )
