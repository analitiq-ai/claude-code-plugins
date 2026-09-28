"""Guards the stage-then-build sequence build_local.py and both release
workflows delegate to: staging always runs before the build, the sdist/wheel
choice passes straight through to `python -m build`, and a build that
produces nothing fails loud instead of returning an empty list silently.
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

WHEEL_BUILD = Path(__file__).resolve().parents[1] / "wheel_build.py"


def _module():
    spec = importlib.util.spec_from_file_location("wheel_build", WHEEL_BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_build(filenames: list[str], calls: list | None = None):
    """A `subprocess.run` side effect standing in for the staging script and
    `python -m build`: records every command it was given (when `calls` is
    passed) and, for the build invocation, drops the named files into
    whatever `--outdir` it was given."""

    def run(cmd, check):
        if calls is not None:
            calls.append(cmd)
        if cmd[1:3] == ["-m", "build"]:
            outdir = Path(cmd[cmd.index("--outdir") + 1])
            outdir.mkdir(parents=True, exist_ok=True)
            for filename in filenames:
                (outdir / filename).write_bytes(b"bytes")

    return run


def test_stage_and_build_stages_before_building(tmp_path):
    wheel_build = _module()
    package_dir = tmp_path / "pkg"
    staged_dir = tmp_path / "staged"
    dist_dir = tmp_path / "dist"
    calls: list = []

    with patch.object(
        wheel_build.subprocess, "run", side_effect=_fake_build(["pkg-1.0.0-py3-none-any.whl"], calls)
    ):
        built = wheel_build.stage_and_build(package_dir, staged_dir, dist_dir, wheel_only=True)

    assert built == [dist_dir / "pkg-1.0.0-py3-none-any.whl"]
    assert len(calls) == 2
    staging_call, build_call = calls[0], calls[1]
    assert staging_call == [
        sys.executable,
        str(package_dir / "scripts" / "build.py"),
        "--dist",
        str(staged_dir),
    ]
    assert build_call == [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir), str(staged_dir)]


def test_stage_and_build_omits_wheel_only_flag_for_sdist_plus_wheel(tmp_path):
    wheel_build = _module()
    dist_dir = tmp_path / "dist"
    calls: list = []

    with patch.object(
        wheel_build.subprocess,
        "run",
        side_effect=_fake_build(["pkg-1.0.0-py3-none-any.whl", "pkg-1.0.0.tar.gz"], calls),
    ):
        built = wheel_build.stage_and_build(tmp_path / "pkg", tmp_path / "staged", dist_dir, wheel_only=False)

    assert sorted(p.name for p in built) == ["pkg-1.0.0-py3-none-any.whl", "pkg-1.0.0.tar.gz"]
    assert len(calls) == 2
    build_call = calls[1]
    assert "--wheel" not in build_call


def test_stage_and_build_refuses_when_build_produces_nothing(tmp_path):
    wheel_build = _module()
    dist_dir = tmp_path / "dist"

    def run(cmd, check):
        if cmd[1:3] == ["-m", "build"]:
            dist_dir.mkdir(parents=True, exist_ok=True)  # `python -m build` creates it; the fake must too

    with (
        patch.object(wheel_build.subprocess, "run", side_effect=run),
        pytest.raises(SystemExit, match="produced nothing"),
    ):
        wheel_build.stage_and_build(tmp_path / "pkg", tmp_path / "staged", dist_dir, wheel_only=True)


def test_main_prints_every_built_artifact(tmp_path, capsys):
    wheel_build = _module()
    dist_dir = tmp_path / "dist"
    built = [dist_dir / "pkg-1.0.0-py3-none-any.whl", dist_dir / "pkg-1.0.0.tar.gz"]

    with (
        patch.object(
            sys,
            "argv",
            ["wheel_build.py", str(tmp_path / "pkg"), "--staged", str(tmp_path / "staged"), "--outdir", str(dist_dir)],
        ),
        patch.object(wheel_build, "stage_and_build", return_value=built),
    ):
        assert wheel_build.main() == 0

    out = capsys.readouterr().out
    assert str(built[0]) in out
    assert str(built[1]) in out


def test_main_passes_the_wheel_only_flag_through(tmp_path):
    wheel_build = _module()
    calls: dict = {}

    def fake_stage_and_build(package_dir, staged_dir, dist_dir, *, wheel_only):
        calls["wheel_only"] = wheel_only
        return [dist_dir / "pkg-1.0.0-py3-none-any.whl"]

    with (
        patch.object(
            sys,
            "argv",
            [
                "wheel_build.py",
                str(tmp_path / "pkg"),
                "--staged",
                str(tmp_path / "staged"),
                "--outdir",
                str(tmp_path / "dist"),
                "--wheel-only",
            ],
        ),
        patch.object(wheel_build, "stage_and_build", side_effect=fake_stage_and_build),
    ):
        assert wheel_build.main() == 0

    assert calls["wheel_only"] is True
