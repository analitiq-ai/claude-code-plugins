"""Repo-root conftest: put the in-repo package source on sys.path.

This repo is the SOURCE of `analitiq-contract-models` and `analitiq-validator`.
Tests must therefore exercise `packages/*/src`, never a wheel that happens to be
installed in the environment.

Note what this CANNOT do: `analitiq/contracts/` has no `__init__.py` in source
(it is generated at build time), so it is a namespace *portion*, while an
installed wheel ships the generated file and is a *regular* package — which wins
regardless of sys.path. So this works only when nothing is installed, which is
why requirements-dev.txt forbids it and `_pins.assert_pinned_versions()` checks
provenance rather than trusting the path order.

A root conftest is the only place this works. `packages/validator/tests/conftest.py`
puts both source roots on `sys.path`, but pytest collects
`packages/contract-models/tests/` first: those modules import `analitiq.contracts`
before that conftest ever runs, the installed distribution lands in
`sys.modules`, and every later import gets the stale copy regardless of
`sys.path`. Root conftests are imported before any collection, so this wins.

`analitiq` is a PEP 420 namespace spanning both source trees, which is why both
roots go on the path rather than one package importing the other.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent

# The contract models bind DOMAIN at import time for the `$schema` host Literal.
# The published wheel pins it; from source the ambient value wins, so set the
# public host before the first import.
os.environ.setdefault("DOMAIN", "analitiq.ai")

PACKAGE_SRC_ROOTS = (REPO_ROOT / "packages" / "contract-models" / "src",
                     REPO_ROOT / "packages" / "validator" / "src")

for _src in (*PACKAGE_SRC_ROOTS,
             # `census/` holds this repo's catalogues of the contract's own surface —
             # maintenance machinery, deliberately outside `src/` so it stays
             # out of the wheel, and on the path because the suite reads it.
             REPO_ROOT,
             # The renderers: a test asking what the contract publishes renders
             # it, because the committed tree changes only in a release.
             REPO_ROOT / "scripts"):
    # Fail loudly. Skipping a missing root would leave the suite importing
    # whatever happens to be installed while every downstream `importorskip`
    # turned green — a merge gate passing having validated nothing.
    if not _src.is_dir():
        raise RuntimeError(
            f"{_src} is missing. The suite must exercise in-repo source, not an "
            "installed wheel; run pytest from a complete checkout.")
    _p = str(_src)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The pipeline plugin's helpers bootstrap the PUBLISHED validator into a managed
# venv and `os.execv` into it when `importlib.metadata` can't find the pin. Source
# on sys.path has no metadata, so without this the bootstrap would replace the
# pytest process mid-run. See `_FROM_SOURCE` in
# plugins/analitiq-pipeline-builder/scripts/_bootstrap.py.
os.environ["ANALITIQ_VALIDATOR_FROM_SOURCE"] = "1"


@pytest.fixture
def refuse():
    """`refuse(path, mode)` sets `path`'s mode for this test and restores it after.

    How a test makes the kernel refuse a lookup, a listing or a read. Root is
    refused nothing, so a test asking for this skips there.
    """
    if os.geteuid() == 0:
        pytest.skip("root is refused no lookup, listing or read")
    changed: list[tuple[Path, int]] = []

    def set_mode(path: Path, mode: int) -> None:
        changed.append((path, stat.S_IMODE(path.lstat().st_mode)))
        path.chmod(mode)

    yield set_mode
    for path, mode in reversed(changed):
        path.chmod(mode)


@pytest.fixture(scope="session")
def ascii_locale_env():
    """Environment overrides that run a child Python on this repo's package
    source with ASCII as its locale encoding: how a test stands in for a host
    whose default encoding is not UTF-8, which the parent process cannot become.

    The child is asked what it decodes with, so a platform that coerces the
    locale anyway fails here rather than passing every test using this vacuously.
    """
    env = {
        "LC_ALL": "C", "LANG": "C",
        "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
        # Empty, not absent: CPython ignores an empty value, and an inherited
        # one would set the child's stdio encoding behind the locale's back.
        "PYTHONIOENCODING": "",
        "PYTHONPATH": os.pathsep.join(str(root) for root in PACKAGE_SRC_ROOTS),
    }
    probe = subprocess.run(
        [sys.executable, "-c",
         "import locale, sys; print(locale.getpreferredencoding(False)); print(sys.stdin.encoding)"],
        capture_output=True, text=True, env={**os.environ, **env}, check=True)
    for encoding in probe.stdout.split():
        if "utf" in encoding.lower():
            pytest.fail(f"the child still decodes with {encoding} under {env}")
    return env
