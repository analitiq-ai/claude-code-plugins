"""Pin the verdict semantics of `scripts/check_contract_consumption_pin.py`.

The guard's network half runs only in CI (`contract-consumption-pin-guard`
job), so its direction logic — newer publish = notice, lagging pointer =
failure, malformed anything = GuardError — would otherwise only ever execute
against live healthy data, where an inverted comparison is a permanent false
green. Same charter as test_engine_grammar_guard.py: every verdict branch
offline, with the fetch monkeypatched out.
"""
from __future__ import annotations

import hashlib
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_contract_consumption_pin.py"
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"


@pytest.fixture(scope="module")
def guard():
    spec = spec_from_file_location("check_contract_consumption_pin", _SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.pin is not None, f"pin module failed to import: {module._IMPORT_ERROR}"
    return module


def _vendored_bytes(guard):
    return guard.pin.MANIFEST_PATH.read_bytes()


def _urls(guard):
    p = guard.pin
    base = guard.BASE_URL
    return {
        "object": f"{base}/{p.CONSUMPTION_RESOURCE}/v{p.CONSUMPTION_VERSION}/{p.CONSUMPTION_FILENAME}",
        "latest": f"{base}/{p.CONSUMPTION_RESOURCE}/latest.json",
    }


def _stub_fetch(guard, monkeypatch, *, latest, object_bytes=None, latest_bytes=None):
    """Healthy by default: the published object IS the vendored bytes and the
    pointer names the pin. `object_bytes` expresses a divergent republish —
    step 2 is the check the whole guard exists for, so it must be expressible
    here; `latest_bytes` expresses a malformed pointer document."""
    urls = _urls(guard)
    responses = {
        urls["object"]: _vendored_bytes(guard) if object_bytes is None else object_bytes,
        urls["latest"]: (
            json.dumps({"version": latest}).encode()
            if latest_bytes is None
            else latest_bytes
        ),
    }
    monkeypatch.setattr(guard, "_fetch", lambda url: responses[url])


def test_healthy_publication_passes(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, latest=guard.pin.CONSUMPTION_VERSION)
    assert guard.main([]) == 0
    out = capsys.readouterr().out
    assert "engine has published" not in out
    # The full-run banner names everything the network half certified — it is
    # the only signal a CI reader gets about which checks actually executed.
    assert "published object + hash + shape + declared version + latest pointer" in out


def test_newer_engine_publication_is_a_notice_not_a_failure(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, latest="9.0.0")
    assert guard.main([]) == 0
    out, err = capsys.readouterr()
    assert "engine has published v9.0.0" in out
    # A notice must not read as the exit-1 remediation.
    assert "re-vendor the published object (and re-run" not in err


def test_newer_publication_notice_is_annotated_on_actions(guard, monkeypatch, capsys):
    """On Actions the notice must reach the checks UI, not only the job log —
    a plain print in a green job is read by no one."""
    _stub_fetch(guard, monkeypatch, latest="9.0.0")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert guard.main([]) == 0
    assert "::warning title=contract-consumption pin::" in capsys.readouterr().out


def test_lagging_latest_pointer_is_diagnosed_as_stale_not_unpublished(
    guard, monkeypatch, capsys
):
    """latest.json names a version BELOW the pin. "The pin is ahead of what
    the engine published" is provably wrong whenever this branch is
    reachable: step 2 of the SAME run already fetched the pinned immutable
    object (a genuinely unpublished pin raises GuardError at that fetch, exit
    2, and never reaches step 3). The truthful reading is a mutable pointer
    lagging a published object. Still exit 1, with the pointer's remediation
    — re-vendoring cannot fix it."""
    _stub_fetch(guard, monkeypatch, latest="0.0.1")
    assert guard.main([]) == 1
    err = capsys.readouterr().err
    assert "lags" in err
    assert "stale latest.json" in err
    assert "Re-check after the TTL" in err
    assert "re-vendoring does not fix the pointer" in err


def test_published_object_differing_from_the_vendored_copy_fails(
    guard, monkeypatch, capsys
):
    """Step 2 — the check the whole guard exists for. A divergent republish (or
    a tampered vendored file) must fail."""
    _stub_fetch(
        guard,
        monkeypatch,
        latest=guard.pin.CONSUMPTION_VERSION,
        object_bytes=_vendored_bytes(guard) + b"\n",
    )
    assert guard.main([]) == 1
    assert "differs from the vendored copy" in capsys.readouterr().err


def test_missing_published_object_is_a_guard_error(guard, monkeypatch, capsys):
    """A pin naming an unpublished version: the CDN answers 403/404, which
    `_guard_lib.fetch` raises as ObjectMissing — a GuardError, exit 2. Never
    exit 1: "re-vendor" cannot fetch an object that does not exist."""
    urls = _urls(guard)

    def _fetch(url):
        raise guard.ObjectMissing(f"{url} → HTTP 404")

    monkeypatch.setattr(guard, "_fetch", _fetch)
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err
    assert urls["object"] in err


def test_fetch_failure_is_a_guard_error(guard, monkeypatch, capsys):
    def _fetch(url):
        raise guard.GuardError(f"fetch failed for {url}: timed out")

    monkeypatch.setattr(guard, "_fetch", _fetch)
    assert guard.main([]) == 2
    assert "could not run" in capsys.readouterr().err


def test_malformed_pointer_json_is_a_guard_error(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, latest="unused", latest_bytes=b"{not json")
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "not valid JSON" in err


def test_pointer_that_is_not_an_object_is_a_guard_error(guard, monkeypatch, capsys):
    _stub_fetch(guard, monkeypatch, latest="unused", latest_bytes=b'["0.3.0"]')
    assert guard.main([]) == 2
    assert "expected object" in capsys.readouterr().err


@pytest.mark.parametrize("version", [None, 3, ["0.3.0"]])
def test_pointer_without_string_version_is_a_guard_error(
    guard, monkeypatch, capsys, version
):
    _stub_fetch(
        guard,
        monkeypatch,
        latest="unused",
        latest_bytes=json.dumps({"version": version}).encode(),
    )
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "has no string `version`" in err


def test_malformed_latest_version_is_a_guard_error(guard, monkeypatch, capsys):
    # "0.3" would compare (0,3) < (0,3,0) and fabricate a lagging-pointer
    # verdict if it were parsed leniently.
    _stub_fetch(guard, monkeypatch, latest="0.3")
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "unparseable version" in err


def test_unexpected_exception_is_a_guard_error_not_a_divergence(
    guard, monkeypatch, capsys
):
    """Anything unclassified must be exit 2. Exit 1 is the definite verdict
    "the manifest diverged; re-vendor" — a confident, wrong instruction for a
    fault that is not a divergence."""
    def _boom(_failures):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(guard, "check_published", _boom)
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "RuntimeError" in err


def test_a_guard_error_still_reports_divergences_already_found(
    guard, monkeypatch, capsys
):
    """The published object differs from the vendored copy (a definite
    divergence, step 2) AND the pointer is malformed (a GuardError, step 3).
    Exit 2 dominates, but the divergence must still be printed — otherwise
    the one check this guard exists for reports as an infra flake that a
    retry will never clear."""
    _stub_fetch(
        guard,
        monkeypatch,
        latest="0.3",
        object_bytes=_vendored_bytes(guard) + b"\n",
    )
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err
    assert "differs from the vendored copy" in err


def test_offline_hash_mismatch_fails(guard, monkeypatch, capsys):
    monkeypatch.setattr(guard.pin, "CONSUMPTION_SHA256", "0" * 64)
    assert guard.main(["--offline"]) == 1
    assert "must move together" in capsys.readouterr().err


def test_offline_self_declared_version_mismatch_fails(guard, monkeypatch, capsys):
    """The vendored bytes hash correctly but the pin names a different version
    than the manifest declares. Near-miss version (a patch away), and both
    operands asserted: only a near-miss kills a prefix comparison, and a
    swapped message reads plausibly."""
    real = guard.pin.CONSUMPTION_VERSION
    near_miss = real[:-1] + str(int(real[-1]) + 1)
    monkeypatch.setattr(guard.pin, "CONSUMPTION_VERSION", near_miss)
    assert guard.main(["--offline"]) == 1
    err = capsys.readouterr().err
    assert f"declares version {real!r}" in err
    assert f"pin says {near_miss!r}" in err


def test_offline_vendored_file_the_census_cannot_walk_is_a_guard_error(
    guard, monkeypatch, capsys, tmp_path
):
    """The sha256 pin MATCHES the bytes but the loader refuses the shape (no
    `roots`): the pin was minted against a malformed object, a state neither
    re-vendoring (the exit-1 remediation) nor a retry fixes — exit 2."""
    malformed = json.dumps(
        {
            "version": guard.pin.CONSUMPTION_VERSION,
            "contract_models_version": "0.0.0",
            "claims": {},
        }
    ).encode()
    path = tmp_path / guard.pin.CONSUMPTION_FILENAME
    path.write_bytes(malformed)
    monkeypatch.setattr(guard.pin, "MANIFEST_PATH", path)
    monkeypatch.setattr(guard.pin, "CONSUMPTION_SHA256", hashlib.sha256(malformed).hexdigest())
    assert guard.main(["--offline"]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "roots" in err


def test_offline_missing_vendored_file_is_a_guard_error(
    guard, monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(guard.pin, "MANIFEST_PATH", tmp_path / "absent.json")
    assert guard.main(["--offline"]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "cannot read the vendored manifest" in err


def test_offline_healthy_passes_and_never_touches_the_network(
    guard, monkeypatch, capsys
):
    """The offline success path: exit 0, and `--offline` genuinely suppresses
    the fetch rather than merely being documented to. Recorded rather than
    raised: `main` turns every exception into exit 2, which would swallow a
    sentinel AssertionError."""
    attempted: list[str] = []
    monkeypatch.setattr(guard, "_fetch", lambda url: attempted.append(url) or b"")
    assert guard.main(["--offline"]) == 0
    assert not attempted, f"--offline must not fetch, but tried {attempted}"
    out = capsys.readouterr().out
    assert "offline hash + shape + declared version" in out


def _job_block(workflow: str, job: str) -> list[str]:
    """The lines of one top-level job: from its `<job>:` key to the next
    key at the same indentation."""
    lines = workflow.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(f"  {job}:")]
    assert len(starts) == 1, f"job {job!r} not found exactly once"
    indent = "  "
    block = [lines[starts[0]]]
    for line in lines[starts[0] + 1:]:
        if line.startswith(indent) and not line.startswith(indent + " ") and line.strip():
            break
        block.append(line)
    return block


def test_ci_job_is_wired():
    block = _job_block(_WORKFLOW.read_text(), "contract-consumption-pin-guard")
    assert any("check_contract_consumption_pin.py" in line for line in block)
    # The one flag that makes the guard not run is the one this test has to
    # forbid: `--offline` is the obvious "fix" when the CDN flakes, and it
    # would leave the job permanently green having verified nothing about the
    # engine's published truth. Checked over every non-comment line of this
    # job's block rather than the guard's own `run:` line, so a `run: |`
    # block form cannot slip past; other guards' jobs are their own tests'.
    invocations = [
        line
        for line in block
        if "--offline" in line and not line.lstrip().startswith("#")
    ]
    assert not invocations, f"--offline must never run in CI: {invocations}"


def test_hash_mismatch_online_fails_without_fetching(guard, monkeypatch, capsys):
    """Step 1 fails, so steps 2-3 never run: a vendored file the pin does not
    account for is exit 1 on its own, and nothing is fetched to compare it
    against."""
    attempted: list[str] = []
    monkeypatch.setattr(guard, "_fetch", lambda url: attempted.append(url) or b"")
    monkeypatch.setattr(guard.pin, "CONSUMPTION_SHA256", "0" * 64)
    assert guard.main([]) == 1
    assert not attempted, f"a failed step 1 must not fetch, but tried {attempted}"
    assert "must move together" in capsys.readouterr().err


def test_pin_import_failure_is_a_guard_error(guard, monkeypatch, capsys):
    monkeypatch.setattr(guard, "pin", None)
    monkeypatch.setattr(guard, "_IMPORT_ERROR", ImportError("no pin"))
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "no pin" in err


def test_an_unclassified_exception_still_reports_divergences_already_found(
    guard, monkeypatch, capsys
):
    """Step 2 finds a divergence, then the pointer fetch raises something no
    check classified. Exit 2, and the divergence is still printed."""
    urls = _urls(guard)
    divergent = _vendored_bytes(guard) + b"\n"

    def _fetch(url):
        if url == urls["object"]:
            return divergent
        raise ValueError("unknown url type")

    monkeypatch.setattr(guard, "_fetch", _fetch)
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err
    assert "differs from the vendored copy" in err


def test_malformed_pin_version_online_is_a_guard_error(
    guard, monkeypatch, capsys, tmp_path
):
    """The pin constant itself is malformed, and the vendored file declares the
    same malformed version so step 1 passes. Step 3 must refuse to compare it
    against the pointer rather than fabricate a direction verdict."""
    manifest = json.loads(_vendored_bytes(guard))
    manifest[guard.pin.ARTIFACT_VERSION_KEY] = "0.3"
    raw = json.dumps(manifest).encode()
    path = tmp_path / guard.pin.CONSUMPTION_FILENAME
    path.write_bytes(raw)
    monkeypatch.setattr(guard.pin, "MANIFEST_PATH", path)
    monkeypatch.setattr(guard.pin, "CONSUMPTION_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(guard.pin, "CONSUMPTION_VERSION", "0.3")
    _stub_fetch(guard, monkeypatch, latest="0.3.0", object_bytes=raw)
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "unparseable version" in err


def test_fetch_refuses_a_url_outside_the_pinned_base(guard):
    """Every other test stubs `_fetch` wholesale, so without this the branch
    the urlopen suppression rests on could be deleted unnoticed. The lookalike
    host covers the sharp edge: a bare startswith(BASE_URL) would admit a host
    that merely begins with the pinned one."""
    urls = _urls(guard)
    base = guard.BASE_URL
    scheme, _, host = base.partition("://")
    for url in (
        urls["latest"].replace(base, f"{scheme}://evil.example"),
        urls["latest"].replace(base, f"{scheme}://{host}.evil.example"),
        urls["latest"].replace(f"{scheme}://", "http://"),
    ):
        with pytest.raises(guard.GuardError, match="refusing non-"):
            guard._fetch(url)
