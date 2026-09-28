#!/usr/bin/env python3
"""Stage a package via its own `scripts/build.py`, then build from the staged
tree with `python -m build` — the two-step sequence `build_local.py` and both
release workflows each need, decided once so a change to how staging composes
with `python -m build` reaches every caller instead of drifting between a
Python caller and two YAML ones.

Runnable as a script so a GitHub Actions `run:` step can call it directly,
without a caller needing its own Python glue:

    python packages/wheel_build.py packages/contract-models \\
        --staged /tmp/cm-src --outdir /tmp/cm-dist [--wheel-only]

Needs `build` importable under this interpreter; making it so is the caller's
job, not this script's.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def stage_and_build(
    package_dir: Path, staged_dir: Path, dist_dir: Path, *, wheel_only: bool
) -> list[Path]:
    """Stage `package_dir` into `staged_dir` via its own `scripts/build.py`,
    build from the staged tree into `dist_dir`, and return what was built.

    `wheel_only` selects `python -m build`'s own `--wheel` flag: a release
    publishes both the sdist and the wheel it built from, while a caller that
    only ever installs the result — `build_local.py`, and the companion
    package a release's own smoke test installs beside it — has no use for an
    sdist it never uploads.

    What was built is read back as the whole of `dist_dir`, so a `dist_dir`
    already holding files is refused before anything runs: a stale artifact
    there would otherwise be returned, installed and published as this build's.
    """
    if dist_dir.exists() and any(dist_dir.iterdir()):
        raise SystemExit(f"wheel_build: {dist_dir} already holds files; pass an empty or new directory")
    subprocess.run(
        [sys.executable, str(package_dir / "scripts" / "build.py"), "--dist", str(staged_dir)],
        check=True,
    )
    cmd = [sys.executable, "-m", "build"]
    if wheel_only:
        cmd.append("--wheel")
    cmd += ["--outdir", str(dist_dir), str(staged_dir)]
    subprocess.run(cmd, check=True)
    built = sorted(dist_dir.iterdir())
    if not built:
        raise SystemExit(f"wheel_build: `python -m build` produced nothing in {dist_dir}")
    return built


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package_dir", type=Path, help="Package root containing scripts/build.py.")
    ap.add_argument("--staged", required=True, type=Path, help="Directory the package is staged into.")
    ap.add_argument("--outdir", required=True, type=Path, help="Directory python -m build writes into.")
    ap.add_argument(
        "--wheel-only",
        action="store_true",
        help="Pass python -m build's own --wheel flag; omit to build sdist + wheel.",
    )
    args = ap.parse_args()

    built = stage_and_build(args.package_dir, args.staged, args.outdir, wheel_only=args.wheel_only)
    for artifact in built:
        print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
