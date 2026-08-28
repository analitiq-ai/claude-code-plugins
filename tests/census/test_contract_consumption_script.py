"""The exit-code contract of ``scripts/render_contract_consumption.py``.

The script prints the same report the census test asserts on; what is its
own is the verdict it maps that report to. Pinned here: a clean census is
exit 0 with the completion line, a finding is exit 1, a usage error is
exit 2, and a check that cannot run — the vendored manifest refused, a
root the tree does not hold (a ``claims`` or ``opaque`` key naming an
unknown model is an exit-1 finding) — is exit 2 with a "could not run"
line, never the exit-1 remediation.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "render_contract_consumption.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("render_contract_consumption", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_on_the_live_tree_is_complete_and_current():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "complete and current" in result.stdout


def test_a_finding_is_exit_1(monkeypatch, capsys):
    module = _load_script()
    import census.consumption.reachability as reachability

    real = reachability.census_report

    def with_a_finding(manifest, dispositions):
        report = real(manifest, dispositions)
        return type(report)(
            **{
                **report.__dict__,
                "unread_without_disposition": (("analitiq.contracts.x.Y", "z"),),
            }
        )

    monkeypatch.setattr(reachability, "census_report", with_a_finding)
    assert module.main(["render_contract_consumption.py", "check"]) == 1
    assert "analitiq.contracts.x.Y.z" in capsys.readouterr().out


def test_bad_argv_is_exit_2(capsys):
    module = _load_script()
    assert module.main(["render_contract_consumption.py", "write"]) == 2
    assert "usage" in capsys.readouterr().err


def test_a_manifest_the_envelope_check_refuses_is_exit_2(monkeypatch, capsys):
    module = _load_script()
    import census.consumption.pin as pin

    def refuse(path=None):
        raise ValueError("`roots` must be a non-empty list")

    monkeypatch.setattr(pin, "load_manifest", refuse)
    assert module.main(["render_contract_consumption.py", "check"]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "roots" in err


def test_a_root_the_tree_does_not_hold_is_exit_2(
    monkeypatch, capsys
):
    module = _load_script()
    import census.consumption.pin as pin

    real = pin.load_manifest

    def with_a_missing_root(path=None):
        manifest = real() if path is None else real(path)
        return {**manifest, pin.ROOTS_KEY: ["analitiq.contracts.stream.Gone"]}

    monkeypatch.setattr(pin, "load_manifest", with_a_missing_root)
    assert module.main(["render_contract_consumption.py", "check"]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "Gone" in err
