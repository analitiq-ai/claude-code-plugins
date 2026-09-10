"""Tests for the plugin's dependency bootstrap
(plugins/analitiq-pipeline-builder/scripts/_bootstrap.py).

The bootstrap guarantees the pinned validator is importable, building a managed
virtualenv and re-entering the calling script under it when it is not. Both halves
run on every platform the plugins are used on, and until this file existed neither
was reachable by any test: the repo-root `conftest.py` sets
`ANALITIQ_VALIDATOR_FROM_SOURCE=1` for the whole suite, which returns from
`ensure_deps_or_reexec` before the venv is looked at. Every test here unsets it
first, and none of them touch the network — the venv build and the install are
faked, and what is asserted is the shape of what would have been run.

The one exception is `test_the_managed_interpreter_is_one_that_runs`, which builds
a real virtualenv. It passes trivially where the layout is already right, and is
the whole point of the file on a platform where it is not.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "plugins" / "analitiq-pipeline-builder"
sys.path.insert(0, str(ROOT / "scripts"))
import _bootstrap as B  # noqa: E402


@pytest.fixture
def unbootstrapped(monkeypatch, tmp_path):
    """A run that believes the pinned validator is absent, with its managed venv
    redirected into `tmp_path`.

    Both halves are needed. Without the first, the source-on-path short-circuit
    returns before anything under test runs; without the second, a test would
    write into the caller's real cache directory.
    """
    monkeypatch.delenv("ANALITIQ_VALIDATOR_FROM_SOURCE", raising=False)
    monkeypatch.delenv(B._REEXEC_SENTINEL, raising=False)
    monkeypatch.setattr(B, "_importable", lambda _version: False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(sys, "argv", ["validate.py", "--entity", "pipeline"])
    # A managed interpreter that already carries the pin, so these tests reach the
    # re-entry without building anything. The path is never executed — every child
    # is faked — so its shape is deliberately not the platform's.
    monkeypatch.setattr(B, "_managed_venv_python", lambda: "/managed/python")
    monkeypatch.setattr(B, "_venv_has_pin", lambda _py, _version: True)
    return tmp_path


def _fake_runs(monkeypatch, returncode=0, record=None):
    """Stand in for every child process the bootstrap starts, recording argv."""
    def run(cmd, **kwargs):
        if record is not None:
            record.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, returncode)

    monkeypatch.setattr(B.subprocess, "run", run)


def test_the_bootstrap_returns_the_childs_exit_code_rather_than_ending_the_process(
        unbootstrapped, monkeypatch):
    """Re-entering the managed interpreter is a child process whose exit code the
    caller returns.

    It cannot be `sys.exit`: the adapter's outermost guard catches `Exception`, and
    `SystemExit` is not one — it is the single case the adapter documents as
    escaping uncontained, where the driving agent sees no Diagnostics JSON at all.
    So the bootstrap hands the code back and the caller returns it.
    """
    calls = []
    _fake_runs(monkeypatch, returncode=3, record=calls)
    assert B.ensure_deps_or_reexec("/somewhere/validate.py") == 3
    # The last child is the re-entry, and it carries the script and the argv.
    argv, _kwargs = calls[-1]
    assert argv[0] == "/managed/python"
    assert str(Path("/somewhere/validate.py")) in " ".join(argv)
    assert argv[-2:] == ["--entity", "pipeline"]


def test_the_bootstrap_returns_none_when_the_pin_is_already_importable(monkeypatch):
    """Nothing to do is not an exit code. `None` is what lets a caller carry on in
    the interpreter it is already in."""
    monkeypatch.delenv("ANALITIQ_VALIDATOR_FROM_SOURCE", raising=False)
    monkeypatch.setattr(B, "_importable", lambda _version: True)
    assert B.ensure_deps_or_reexec("/somewhere/validate.py") is None


def test_the_source_short_circuit_still_returns_none(monkeypatch, capsys):
    """The in-repo escape hatch is unchanged by the return contract: it says so on
    stderr and hands back nothing, so the caller proceeds in this interpreter."""
    monkeypatch.setenv("ANALITIQ_VALIDATOR_FROM_SOURCE", "1")
    assert B.ensure_deps_or_reexec("/somewhere/validate.py") is None
    assert "NOT the pinned" in capsys.readouterr().err


def test_the_child_carries_the_sentinel_that_breaks_a_re_entry_loop(
        unbootstrapped, monkeypatch):
    """The loop-breaker used to reach the new process because `execv` inherits the
    environment. A child inherits it too, but only because it is set before the
    spawn — so that ordering is what this asserts."""
    calls = []
    _fake_runs(monkeypatch, returncode=0, record=calls)
    B.ensure_deps_or_reexec("/somewhere/validate.py")
    _argv, kwargs = calls[-1]
    env = kwargs.get("env") or B.os.environ
    assert env.get(B._REEXEC_SENTINEL) == "1"


def test_a_second_entry_with_the_sentinel_set_refuses_rather_than_re_entering(
        unbootstrapped, monkeypatch):
    """The child could not import the pin either. Re-entering again would spawn an
    unbounded chain, so the sentinel turns the second attempt into a stated
    failure the caller reports."""
    monkeypatch.setenv(B._REEXEC_SENTINEL, "1")
    _fake_runs(monkeypatch, returncode=0)
    with pytest.raises(RuntimeError, match="not importable after bootstrap"):
        B.ensure_deps_or_reexec("/somewhere/validate.py")


def test_the_child_is_not_given_its_own_stdin(unbootstrapped, monkeypatch):
    """One helper reads stdin after the bootstrap returns and is documented as
    stdin-driven. Under the old re-exec the descriptor simply stayed open; a child
    must inherit it, so nothing may be passed for it."""
    calls = []
    _fake_runs(monkeypatch, returncode=0, record=calls)
    B.ensure_deps_or_reexec("/somewhere/validate.py")
    _argv, kwargs = calls[-1]
    assert "stdin" not in kwargs, kwargs


def test_the_managed_interpreter_is_one_that_runs(tmp_path, monkeypatch):
    """The managed interpreter is resolved the way the standard library lays a
    virtualenv out, not by a written-down directory and file name.

    Built for real rather than asserted against a second copy of the layout rule:
    a test that recomputed the path the same way the code does would agree with it
    on every platform, including one where both are wrong.
    """
    venv = tmp_path / "cache" / "analitiq" / "pipeline-validator" / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                   capture_output=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    resolved = B._managed_venv_python()
    assert resolved is not None, f"no interpreter found under {venv}"
    assert Path(resolved).exists(), resolved
    proof = subprocess.run([str(resolved), "-c", "print('ran')"],
                           capture_output=True, text=True, check=False)
    assert proof.returncode == 0, proof.stderr
    assert proof.stdout.strip() == "ran"


#: A system codepage that encodes none of the punctuation this repo's prose
#: favours. Chosen because it is the discriminating case, not the common one:
#: Windows encodes a redirected stream with the ANSI codepage of the system
#: locale, and the Western one maps an em dash fine — so a Western machine would
#: pass this test while a Japanese-locale one crashed.
_UNFORGIVING_CODEPAGE = "cp932"


def test_no_diagnostic_needs_a_codepage_to_carry_it():
    """The driving agent captures stderr, and a captured stream is encoded with
    the system's codepage rather than UTF-8.

    A character that codepage cannot represent raises `UnicodeEncodeError` from
    the write itself — a crash in place of the message that was being reported,
    which is the worst possible moment to lose one."""
    for line in (ROOT / "scripts" / "_bootstrap.py").read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        try:
            line.encode(_UNFORGIVING_CODEPAGE)
        except UnicodeEncodeError:
            pytest.fail(f"a diagnostic this file can emit is unencodable there: {line.strip()}")


def test_a_re_entered_run_prints_nothing_of_its_own(tmp_path, monkeypatch, capsys):
    """The child writes the Diagnostics JSON to the inherited stdout, so the parent
    adds nothing to it and returns the code it was handed.

    Two objects on one stream is not something a caller can parse, and the agent
    contract is one object."""
    import validate as V

    monkeypatch.delenv("ANALITIQ_VALIDATOR_FROM_SOURCE", raising=False)
    monkeypatch.setattr(V, "ensure_deps_or_reexec", lambda _script: 7)
    document = tmp_path / "pipeline.json"
    document.write_text("{}")
    assert V.main(["--entity", "pipeline", "--document", str(document)]) == 7
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("script,argv", [
    ("endpoint_id", ["--schema", "public", "--name", "orders"]),
    ("type_map_gaps", ["--direction", "read", "--map", "type-map-read.json"]),
], ids=["endpoint_id", "type_map_gaps"])
def test_every_helper_returns_the_code_rather_than_carrying_on(
        script, argv, monkeypatch, capsys):
    """The other two helpers, which the issue does not name.

    Each imports the pinned packages lazily AFTER the bootstrap, so one that
    carried on in an interpreter the child was spawned for would reach those
    imports unbootstrapped — an uncaught ImportError in both, since each catches
    only RuntimeError."""
    module = __import__(script)
    monkeypatch.delenv("ANALITIQ_VALIDATOR_FROM_SOURCE", raising=False)
    monkeypatch.setattr(module, "ensure_deps_or_reexec", lambda _script: 5)
    assert module.main(argv) == 5
    assert capsys.readouterr().out == ""


def test_a_missing_venv_is_built_and_the_pin_installed_into_it(
        unbootstrapped, monkeypatch):
    """The build path, which nothing could reach before this file existed.

    Faked at the process boundary rather than run: what is asserted is that a
    virtualenv is created at the managed root with the interpreter running now,
    and that the pin is installed with the interpreter that venv provides."""
    monkeypatch.setattr(B, "_venv_has_pin", lambda _py, _version: False)
    calls = []
    _fake_runs(monkeypatch, returncode=0, record=calls)
    assert B.ensure_deps_or_reexec("/somewhere/validate.py") == 0

    build, install, reentry = (argv for argv, _kwargs in calls)
    assert build[:3] == [sys.executable, "-m", "venv"]
    assert build[3].endswith("venv"), build
    assert install[:4] == ["/managed/python", "-m", "pip", "install"]
    assert B.VALIDATOR_PIN in install
    assert reentry[0] == "/managed/python"


def test_a_venv_that_yields_no_interpreter_is_a_stated_failure(
        unbootstrapped, monkeypatch):
    """`shutil.which` answering `None` after a build means the venv is not usable,
    and there is nothing to run the script with. Saying so beats spawning `None`."""
    monkeypatch.setattr(B, "_venv_has_pin", lambda _py, _version: False)
    monkeypatch.setattr(B, "_managed_venv_python", lambda: None)
    _fake_runs(monkeypatch, returncode=0)
    with pytest.raises(RuntimeError, match="no interpreter was found"):
        B.ensure_deps_or_reexec("/somewhere/validate.py")
