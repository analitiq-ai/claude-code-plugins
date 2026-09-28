"""Guards every guarantee build_local.py's own docstring states.

`_build_wheel` delegates staging and building to `wheel_build.stage_and_build`,
mocked here at that boundary — `wheel_build.py`'s own stage-then-build sequence
is guarded separately in test_wheel_build.py.
"""
import importlib.util
import shlex
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BUILD_LOCAL = Path(__file__).resolve().parents[1] / "build_local.py"


def _module():
    spec = importlib.util.spec_from_file_location("build_local", BUILD_LOCAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_require_build_package_fails_loud_when_absent():
    build_local = _module()
    with (
        patch.object(importlib.util, "find_spec", return_value=None),
        pytest.raises(SystemExit, match="pip install build"),
    ):
        build_local._require_build_package()


def test_require_build_package_passes_when_present():
    build_local = _module()
    with patch.object(importlib.util, "find_spec", return_value=object()):
        build_local._require_build_package()  # must not raise


def _fake_stage_and_build(name: str, version: str, calls: list | None = None):
    """A `stage_and_build` side effect standing in for the real staging + wheel
    build: records every call it was given (when `calls` is passed) and drops
    one wheel file into whatever `build_dir` it was given — the only part of
    the real call's effect `_build_wheel` inspects afterward."""

    def stage_and_build(package_dir, staged_dir, build_dir, *, wheel_only):
        if calls is not None:
            calls.append((package_dir, staged_dir, build_dir, wheel_only))
        build_dir.mkdir(parents=True, exist_ok=True)
        wheel = build_dir / f"{name}-{version}-py3-none-any.whl"
        wheel.write_bytes(b"wheel bytes")
        return [wheel]

    return stage_and_build


def test_build_wheel_returns_the_one_wheel_it_built_without_touching_outdir(tmp_path):
    """`_build_wheel` only builds; copying into the wheelhouse is `main()`'s
    job, done only once every package's wheel has built (see
    test_main_leaves_outdir_untouched_when_the_second_build_fails)."""
    build_local = _module()
    build_dir = tmp_path / "build"
    package_dir = tmp_path / "pkg"
    staged_dir = tmp_path / "staged"
    calls: list = []

    with patch.object(build_local, "stage_and_build", side_effect=_fake_stage_and_build("pkg", "1.0.0", calls)):
        wheel = build_local._build_wheel(package_dir, staged_dir, build_dir)

    assert wheel == build_dir / "pkg-1.0.0-py3-none-any.whl"
    assert wheel.read_bytes() == b"wheel bytes"
    assert len(calls) == 1
    called_package_dir, called_staged_dir, called_build_dir, wheel_only = calls[0]
    assert called_package_dir == package_dir
    assert called_staged_dir == staged_dir
    assert called_build_dir == build_dir
    assert wheel_only is True


def test_build_wheel_refuses_zero_wheels_produced(tmp_path):
    build_local = _module()

    with (
        patch.object(build_local, "stage_and_build", return_value=[]),
        pytest.raises(SystemExit, match="expected exactly one wheel"),
    ):
        build_local._build_wheel(tmp_path / "pkg", tmp_path / "staged", tmp_path / "build")


def test_build_wheel_refuses_more_than_one_wheel_produced(tmp_path):
    build_local = _module()
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    wheels = [build_dir / "pkg-1.0.0-py3-none-any.whl", build_dir / "pkg-1.0.0-py3-none-any2.whl"]
    for wheel in wheels:
        wheel.write_bytes(b"bytes")

    with (
        patch.object(build_local, "stage_and_build", return_value=wheels),
        pytest.raises(SystemExit, match="expected exactly one wheel"),
    ):
        build_local._build_wheel(tmp_path / "pkg", tmp_path / "staged", build_dir)


def _wheel_name_for(build_local, package_dir: Path) -> str:
    """The one place a package directory maps to its wheel's distribution
    name — both `_fake_build_wheel` and the real-`_build_wheel` test below
    call this rather than each keeping its own copy of the mapping."""
    return "analitiq_contract_models" if package_dir == build_local.CONTRACT_MODELS_DIR else "analitiq_validator"


def _fake_build_wheel(build_local):
    """A `_build_wheel` side effect standing in for the real per-package build:
    writes one wheel, named via `_wheel_name_for`, into the given `build_dir`
    — the only part of `_build_wheel`'s real effect main()'s copy-and-report
    block inspects afterward. Shared by every test that patches `_build_wheel`
    with it, so the block's guarantees are graded against one fixture, not one
    per test."""

    def build_wheel(package_dir, staged_dir, build_dir):
        build_dir.mkdir(parents=True, exist_ok=True)
        wheel = build_dir / f"{_wheel_name_for(build_local, package_dir)}-1.0.0-py3-none-any.whl"
        wheel.write_bytes(b"wheel")
        return wheel

    return build_wheel


def test_main_leaves_outdir_untouched_when_the_second_build_fails(tmp_path):
    """The bug this guards: copying each package's wheel into outdir as soon
    as it builds would leave outdir holding contract-models' wheel alone if
    validator's build then failed — a mismatched, half-written wheelhouse."""
    build_local = _module()
    outdir = tmp_path / "wheelhouse"
    outdir.mkdir()
    fake = _fake_build_wheel(build_local)

    def fake_build_wheel(package_dir, staged_dir, build_dir):
        if package_dir == build_local.VALIDATOR_DIR:
            raise SystemExit("validator build failed")
        return fake(package_dir, staged_dir, build_dir)

    with (
        patch.object(sys, "argv", ["build_local.py", "--outdir", str(outdir)]),
        patch.object(build_local, "_require_build_package"),
        patch.object(build_local, "_build_wheel", side_effect=fake_build_wheel),
        pytest.raises(SystemExit, match="validator build failed"),
    ):
        build_local.main()

    assert list(outdir.iterdir()) == []


def test_main_reports_only_the_wheels_this_run_built(tmp_path, capsys):
    """The bug this guards: a wheelhouse is reused across invocations, so a
    wheel an earlier run left in outdir must never be counted, named, or
    installed by this run's printed output."""
    build_local = _module()
    outdir = tmp_path / "wheelhouse"
    outdir.mkdir()
    stale = outdir / "analitiq_contract_models-0.9.0-py3-none-any.whl"
    stale.write_bytes(b"stale wheel")

    with (
        patch.object(sys, "argv", ["build_local.py", "--outdir", str(outdir)]),
        patch.object(build_local, "_require_build_package"),
        patch.object(build_local, "_build_wheel", side_effect=_fake_build_wheel(build_local)),
    ):
        assert build_local.main() == 0

    out = capsys.readouterr().out
    assert "wrote 2 wheel(s)" in out
    assert stale.name not in out
    built = [
        outdir / "analitiq_contract_models-1.0.0-py3-none-any.whl",
        outdir / "analitiq_validator-1.0.0-py3-none-any.whl",
    ]
    expected = shlex.join(["pip", "install", "--force-reinstall", str(built[0]), str(built[1])])
    assert expected in out
    assert str(stale) not in out


def test_main_prints_a_shell_safe_command_for_an_outdir_with_a_space(tmp_path, capsys):
    """The bug this guards: a naive space-join prints a command a shell splits
    into nonexistent paths whenever --outdir contains a space."""
    build_local = _module()
    outdir = tmp_path / "my wheel house"

    with (
        patch.object(sys, "argv", ["build_local.py", "--outdir", str(outdir)]),
        patch.object(build_local, "_require_build_package"),
        patch.object(build_local, "_build_wheel", side_effect=_fake_build_wheel(build_local)),
    ):
        assert build_local.main() == 0

    out = capsys.readouterr().out
    built = [
        outdir / "analitiq_contract_models-1.0.0-py3-none-any.whl",
        outdir / "analitiq_validator-1.0.0-py3-none-any.whl",
    ]
    expected = shlex.join(["pip", "install", "--force-reinstall", str(built[0]), str(built[1])])
    assert expected in out
    assert f"--force-reinstall {built[0]} " not in out  # unescaped would split the path in two


def test_main_resolves_a_relative_outdir_to_an_absolute_path(tmp_path, capsys, monkeypatch):
    """The printed install command is meant to be pasted into a downstream
    consumer's own directory, so it must not depend on the cwd build_local.py
    happened to run from — a relative --outdir would only resolve against
    that invocation directory."""
    build_local = _module()
    monkeypatch.chdir(tmp_path)

    with (
        patch.object(sys, "argv", ["build_local.py", "--outdir", "wheelhouse"]),
        patch.object(build_local, "_require_build_package"),
        patch.object(build_local, "_build_wheel", side_effect=_fake_build_wheel(build_local)),
    ):
        assert build_local.main() == 0

    out = capsys.readouterr().out
    assert str((tmp_path / "wheelhouse").resolve()) in out


def test_main_creates_outdir_when_it_does_not_exist_yet(tmp_path, capsys):
    """Exercises the real `_build_wheel`, not a fake standing in for it, so
    this fails if `main()`'s `args.outdir.mkdir()` is removed: neither
    `_build_wheel` nor its post-build copy into `outdir` ever creates it."""
    build_local = _module()
    outdir = tmp_path / "wheelhouse"
    assert not outdir.exists()

    def fake_stage_and_build(package_dir, staged_dir, build_dir, *, wheel_only):
        build_dir.mkdir(parents=True, exist_ok=True)
        wheel = build_dir / f"{_wheel_name_for(build_local, package_dir)}-1.0.0-py3-none-any.whl"
        wheel.write_bytes(b"wheel")
        return [wheel]

    with (
        patch.object(sys, "argv", ["build_local.py", "--outdir", str(outdir)]),
        patch.object(build_local, "_require_build_package"),
        patch.object(build_local, "stage_and_build", side_effect=fake_stage_and_build),
    ):
        assert build_local.main() == 0

    assert outdir.is_dir()
    assert sorted(p.name for p in outdir.glob("*.whl")) == [
        "analitiq_contract_models-1.0.0-py3-none-any.whl",
        "analitiq_validator-1.0.0-py3-none-any.whl",
    ]
