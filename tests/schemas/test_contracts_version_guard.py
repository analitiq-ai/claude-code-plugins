"""Pin the wiring and verdict semantics of `scripts/check_contracts_version_pin.py`.

The guard's network half runs only in CI (`contracts-version-guard` job), so
its verdict logic — published-vs-committed byte equality, the pin comparison,
strict-vs-warn windows, missing-object handling and its sentinel
corroboration — would otherwise only ever execute against live healthy data,
where an inverted comparison is a permanent false green. Same charter as
`test_engine_grammar_guard.py` next door: every verdict branch offline, with
the fetch monkeypatched out, plus the readers against the real working tree
and the CI job's wiring.
"""
from __future__ import annotations

import io
import json
import re
import urllib.error
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_contracts_version_pin.py"
_SIBLING = REPO_ROOT / "scripts" / "check_validator_pin_contract.py"
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
    # `_surface_warning` would append to the live job summary — pytest
    # captures stdout, not file writes.
    monkeypatch.delenv("CONTRACTS_VERSION_GUARD_STRICT", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _fact(guard, version: str) -> bytes:
    return json.dumps({guard.CONTRACTS_VERSION_KEY: version}).encode()


def _redigested_stamp(guard) -> bytes:
    """The committed stamp with only its tree digest altered — the state a
    publish leaves behind when a render landed but the upload did not."""
    doc = json.loads(guard.COMMITTED_PATH.read_bytes())
    doc["tree_sha256"] = "0" * 64
    return json.dumps(doc).encode()


def _stub_fetch(guard, monkeypatch, stamp, sentinel=b"{}") -> list:
    """Stub `_fetch` per URL: `stamp` serves the stamp URL (bytes returned,
    exception raised), `sentinel` the sentinel URL. Returns the call log."""
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        item = stamp if url == guard.PUBLISHED_URL else sentinel
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(guard, "_fetch", fetch)
    return calls


def _pin_matching_stamp(guard, monkeypatch, tmp_path) -> None:
    """Point PIN_SOURCE at a pin equal to the committed stamp.

    The real `_bootstrap.py` pin legitimately lags the stamp on a
    package-release PR (root CLAUDE.md, "The contract, and the runtime pin"),
    and the offline suite must stay green through that window — so tests
    asserting the all-equal verdict must not read the real pin.
    """
    stamped = tmp_path / "_bootstrap.py"
    stamped.write_text(
        f'VALIDATOR_PIN = "analitiq-validator=={guard.read_committed_stamp()}"\n'
    )
    monkeypatch.setattr(guard, "PIN_SOURCE", stamped)


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


def test_pin_reader_agrees_with_the_validator_guard(guard):
    # The pin's location and textual shape are also read by
    # `check_validator_pin_contract.py`; the guard cannot import it without
    # coupling two self-contained CI scripts, so this pins the copies to one
    # behavior (`.claude/rules/no-drift-surfaces.md`).
    sibling = _load(_SIBLING)
    assert guard.PIN_SOURCE == sibling.PIN_SOURCE
    assert guard.read_pin_version() == sibling.read_pin_version()


# --- verdicts, fetch stubbed ----------------------------------------------


def test_healthy_publication_passes(guard, monkeypatch, tmp_path, capsys):
    _pin_matching_stamp(guard, monkeypatch, tmp_path)
    calls = _stub_fetch(guard, monkeypatch, guard.COMMITTED_PATH.read_bytes())
    assert guard.main() == 0
    assert "OK: published stamp == committed stamp" in capsys.readouterr().out
    assert calls == [guard.PUBLISHED_URL], "a served stamp needs no sentinel probe"


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
    _stub_fetch(guard, monkeypatch, _fact(guard, "0.0.0"))
    assert guard.main() == 1
    err = capsys.readouterr().err
    assert "DIVERGENCE" in err and "workflow_dispatch" in err


def test_same_release_stale_digest_fails_strict(guard, monkeypatch, capsys):
    # The state the digest half exists to expose: a render landed on main,
    # the publish did not, and the version half alone would read as green.
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    _stub_fetch(guard, monkeypatch, _redigested_stamp(guard))
    assert guard.main() == 1
    err = capsys.readouterr().err
    assert "tree digest" in err and "workflow_dispatch" in err


def test_same_release_stale_digest_warns_on_ordinary_prs(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, _redigested_stamp(guard))
    assert guard.main() == 0
    out = capsys.readouterr().out
    assert "WINDOW:" in out and "workflow_dispatch" not in out


def test_unpublished_stamp_warns_on_ordinary_prs(guard, monkeypatch, capsys):
    # The bootstrap state: the change introducing the stamp has not reached
    # main yet, so the CDN has no stamp to serve — corroborated by the
    # sentinel, which IS served.
    calls = _stub_fetch(guard, monkeypatch, guard.NotPublished("HTTP 403"))
    assert guard.main() == 0
    assert guard.PUBLISHED_URL in capsys.readouterr().out
    assert calls == [guard.PUBLISHED_URL, guard.SENTINEL_URL]


def test_unpublished_stamp_fails_strict(guard, monkeypatch, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    _stub_fetch(guard, monkeypatch, guard.NotPublished("HTTP 404"))
    assert guard.main() == 1
    assert "schemas-publish.yml" in capsys.readouterr().err


def test_missing_sentinel_is_a_guard_error_not_a_missing_stamp(
    guard, monkeypatch, capsys
):
    """A 403 on every key is an access fault. Believing the stamp-side 403
    would mint the divergence verdict with a re-run-the-publish remediation
    that cannot fix it — the exact conflation the sentinel exists to refuse.
    """
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    _stub_fetch(
        guard,
        monkeypatch,
        guard.NotPublished("HTTP 403"),
        sentinel=guard.NotPublished("HTTP 403"),
    )
    assert guard.main() == 2
    assert "GUARD ERROR" in capsys.readouterr().err


def test_pin_lag_fails_strict(guard, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONTRACTS_VERSION_GUARD_STRICT", "1")
    stale_pin = tmp_path / "_bootstrap.py"
    stale_pin.write_text('VALIDATOR_PIN = "analitiq-validator==0.0.1"\n')
    monkeypatch.setattr(guard, "PIN_SOURCE", stale_pin)
    _stub_fetch(guard, monkeypatch, guard.COMMITTED_PATH.read_bytes())
    assert guard.main() == 1
    err = capsys.readouterr().err
    # The strict arm cites the tolerance it deliberately tightens.
    assert "VALIDATOR_PIN" in err and "CLAUDE.md" in err


def test_pin_lag_warns_on_ordinary_prs(guard, monkeypatch, tmp_path, capsys):
    stale_pin = tmp_path / "_bootstrap.py"
    stale_pin.write_text('VALIDATOR_PIN = "analitiq-validator==0.0.1"\n')
    monkeypatch.setattr(guard, "PIN_SOURCE", stale_pin)
    _stub_fetch(guard, monkeypatch, guard.COMMITTED_PATH.read_bytes())
    assert guard.main() == 0
    out = capsys.readouterr().out
    assert "WINDOW:" in out and "VALIDATOR_PIN" in out and "CLAUDE.md" not in out


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
    # at all.
    monkeypatch.setattr(
        guard.urllib.request,
        "urlopen",
        lambda url, timeout: (_ for _ in ()).throw(_http_error(url, code)),
    )
    with pytest.raises(guard.NotPublished):
        guard._fetch(guard.PUBLISHED_URL)


def test_other_http_statuses_classify_as_guard_errors(guard, monkeypatch):
    monkeypatch.setattr(
        guard.urllib.request,
        "urlopen",
        lambda url, timeout: (_ for _ in ()).throw(
            _http_error(guard.PUBLISHED_URL, 500)
        ),
    )
    with pytest.raises(guard.GuardError):
        guard._fetch(guard.PUBLISHED_URL)


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


def test_committed_vs_pyproject_disagreement_is_a_guard_error(
    guard, monkeypatch, tmp_path, capsys
):
    # The render check owns that equality; the guard must refuse to mint any
    # verdict about the published copy from a baseline the repo itself
    # cannot agree on — and must not touch the network doing so.
    diverged = tmp_path / "pyproject.toml"
    diverged.write_text('[project]\nversion = "999.0.0"\n')
    monkeypatch.setattr(guard, "PYPROJECT_PATH", diverged)
    calls = _stub_fetch(guard, monkeypatch, _fact(guard, "999.0.0"))
    assert guard.main() == 2
    assert "render_schemas.py contracts-version" in capsys.readouterr().err
    assert not calls, "no verdict input may be fetched for an unusable baseline"


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
    strict_line = next(
        line for line in workflow.splitlines()
        if "CONTRACTS_VERSION_GUARD_STRICT:" in line
    )
    assert "github.event_name == 'push'" in strict_line
    assert "release-please--" in strict_line


def test_strictness_expression_is_identical_to_the_validator_guards():
    # The job comment promises the strict window matches
    # pinned-validator-guard's; without this pin, editing either expression
    # leaves the other — and the promise — behind silently.
    workflow = _WORKFLOW.read_text()

    def expression(env_key: str) -> str:
        line = next(l for l in workflow.splitlines() if f"{env_key}:" in l)
        return line.split(f"{env_key}:", 1)[1].strip()

    assert expression("CONTRACTS_VERSION_GUARD_STRICT") == expression(
        "VALIDATOR_PIN_GUARD_STRICT"
    )


def test_publish_workflow_serves_the_stamp_as_json():
    # The upload's Content-Type case arm carries the stamp's basename in a
    # file no other test reads; if the document is renamed or the arm edited,
    # the stamp silently ships as application/schema+json.
    publish = _PUBLISH_WORKFLOW.read_text()
    case_line = next(
        line for line in publish.splitlines() if 'ctype="application/json"' in line
    )
    assert "contracts-version.json" in case_line
