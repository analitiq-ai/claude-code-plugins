#!/usr/bin/env python3
"""Build a local wheelhouse for `analitiq-contract-models` and
`analitiq-validator`, for testing a downstream consumer against source
changes without cutting a PyPI release.

Stages each package via its own `scripts/build.py` — which runs that
package's structural guards — then builds one wheel from the staged tree via
`wheel_build.stage_and_build`, the same stage-then-build sequence both release
workflows use. Runs neither package's own test suite, builds no sdist, and
never publishes: a narrower, faster path than a real release, meant only to
hand a downstream consumer something to install locally.

Both wheels land in one `--outdir` — a directory of wheels ("wheelhouse") a
consumer installs directly by path:

    python packages/build_local.py --outdir /tmp/analitiq-wheelhouse

The run prints the exact `pip install --force-reinstall <wheel> <wheel>`
command to use, naming the two files it just wrote rather than the directory:
`--outdir` is a wheelhouse meant to be reused across invocations, so a glob
over it (`*.whl`) can just as easily pick up a different-version wheel a
previous run left behind, and pip refuses to install two wheels of the same
distribution in one invocation. `--force-reinstall` matters too — without it,
pip treats an already-installed same-version package as satisfied and skips
it even though this wheel's contents changed underneath that version number.

Never commits, never touches PyPI, never changes a consumer's pinned
manifest — point a consumer's environment at the output directly and revert
by reinstalling its normal pin when done.

Needs everything `pip install -r requirements-dev.txt` provides: contract-models'
own `scripts/build.py` actually imports its staged tree under this interpreter
(an import guard, not just a static scan), so its runtime dependencies must
already be importable here. Also needs the `build` package the release
workflows also require (`pip install build`); this script fails loud if
`build` itself is missing rather than installing it silently.
"""

from __future__ import annotations

import argparse
import importlib.util
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

PACKAGES_DIR = Path(__file__).resolve().parent

# `packages/`, where wheel_build.py (the stage-then-build helper this file and
# both release workflows share) lives.
sys.path.insert(0, str(PACKAGES_DIR))
from wheel_build import stage_and_build  # noqa: E402

CONTRACT_MODELS_DIR = PACKAGES_DIR / "contract-models"
VALIDATOR_DIR = PACKAGES_DIR / "validator"


def _require_build_package() -> None:
    if importlib.util.find_spec("build") is None:
        raise SystemExit(
            "build_local: the `build` package is not installed in this "
            f"interpreter ({sys.executable}). Install it with `pip install "
            "build` and re-run."
        )


def _build_wheel(package_dir: Path, staged_dir: Path, build_dir: Path) -> Path:
    """Build the one wheel `wheel_build.stage_and_build` produces from
    `package_dir` into the fresh `build_dir`, and return it — without
    touching the wheelhouse.

    Copying into `--outdir` is `main()`'s job, done only after every
    package's wheel has built: if this raised after already copying one
    package's wheel in, a failure on the other package would leave the
    wheelhouse holding a mismatched pair instead of the pair it started with.
    """
    built = stage_and_build(package_dir, staged_dir, build_dir, wheel_only=True)
    if len(built) != 1:
        raise SystemExit(
            f"build_local: expected exactly one wheel built from {staged_dir}, "
            f"got {[p.name for p in built]}"
        )
    return built[0]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--outdir",
        required=True,
        type=lambda s: Path(s).resolve(),
        help="Wheelhouse directory both wheels are written to.",
    )
    args = ap.parse_args()

    _require_build_package()
    args.outdir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="analitiq-build-local-") as tmp:
        tmp_path = Path(tmp)
        # Building either package's wheel does not require the other
        # installed; only INSTALLING the validator wheel does, since it
        # declares an exact `==` dependency on contract-models.
        wheels = [
            _build_wheel(CONTRACT_MODELS_DIR, tmp_path / "cm-src", tmp_path / "cm-dist"),
            _build_wheel(VALIDATOR_DIR, tmp_path / "v-src", tmp_path / "v-dist"),
        ]
        # Both builds succeeded before either touches outdir, so a failure
        # above never leaves outdir holding a mismatched pair.
        built = []
        for wheel in wheels:
            dest = args.outdir / wheel.name
            shutil.copy2(wheel, dest)
            built.append(dest)

    print(f"build_local: wrote {len(built)} wheel(s) to {args.outdir}:")
    for wheel in built:
        print(f"  {wheel.name}")
    command = shlex.join(["pip", "install", "--force-reinstall", *(str(wheel) for wheel in built)])
    print(f"\nInstall them with:\n  {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
