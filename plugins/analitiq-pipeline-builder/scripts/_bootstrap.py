"""Shared dependency bootstrap for the plugin's Python helpers.

Both `validate.py` and `endpoint_id.py` consume the published
`analitiq-validator` (which pulls `analitiq-contract-models`). This module
guarantees that package is importable: if the current interpreter lacks the
pinned version it installs it into a managed virtualenv and runs the calling
script again under it, returning that run's exit code. A venv sidesteps PEP-668
externally-managed interpreters; pip output is routed to stderr so a caller's
stdout stays clean.

Two things here are platform-shaped and neither is decided by hand. The managed
interpreter is located by searching the directory names a virtualenv can use and
letting `shutil.which` apply the platform's own rules for an executable, so
`Scripts/python.exe` is found without that spelling appearing below. And the
script is re-entered as a CHILD rather than through `os.execv`, which on Windows
does not replace the calling process: the caller would be handed an exited
process, no stdout, and an exit code belonging to nothing.

That child is also why `ensure_deps_or_reexec` RETURNS a code rather than ending
the process. `sys.exit` would raise `SystemExit`, which the adapter's outermost
guard does not catch - it catches `Exception` - and that is the one path the
adapter documents as escaping with no Diagnostics JSON on stdout at all. So the
code comes back and each caller returns it.

Every diagnostic here stays ASCII. The driving agent captures stderr, and a
captured stream is encoded with the system's codepage rather than UTF-8; a
character that codepage cannot represent raises from the write itself, losing the
message at the moment it was being reported.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Single source of the validator pin — the PUBLISHED release both plugins
# self-install at runtime. Nothing else may restate this version; the connector
# plugin's validator agent carries the one unavoidable copy (prose, not code)
# and is pinned to it by tests/pipeline_builder/test_contract_enforcement.py,
# which also holds the pin at or behind packages/validator/pyproject.toml.
# requirements-dev.txt deliberately does NOT carry it — installing the wheel
# would shadow the in-repo source.
VALIDATOR_PIN = "analitiq-validator==1.0.0rc24"

_REEXEC_SENTINEL = "ANALITIQ_PIPELINE_VALIDATOR_BOOTSTRAPPED"

# Set by this repo's root conftest.py. In the monorepo the validator is SOURCE
# (packages/validator/src) on sys.path, not an installed distribution, so
# `importlib.metadata` finds nothing and the bootstrap below would build a venv,
# install the published wheel, and run the whole pytest command again under
# process mid-run and testing the published release instead of the source.
#
# Deliberately an explicit opt-in rather than "is it importable?": end users have
# no checkout, and the version-exactness guarantee for them must not soften into
# a heuristic. Absent this variable, behaviour is unchanged.
_FROM_SOURCE = "ANALITIQ_VALIDATOR_FROM_SOURCE"


def _pinned_version() -> str:
    return VALIDATOR_PIN.split("==", 1)[1]


def _importable(version: str) -> bool:
    try:
        from importlib.metadata import PackageNotFoundError, version as _v
    except Exception:  # pragma: no cover
        return False
    try:
        return _v("analitiq-validator") == version
    except PackageNotFoundError:
        return False


#: The directory names a virtualenv puts its interpreter in. Both are searched
#: rather than one being chosen: `shutil.which` then decides, using the
#: platform's own rules for what counts as an executable, so neither the choice
#: nor the `.exe` suffix is written down anywhere. `sysconfig` names the right
#: one directly through its `venv` scheme, but only from 3.11 - and the pinned
#: package supports 3.10, where asking would raise instead of answering.
_VENV_SCRIPT_DIRS = ("bin", "Scripts")


def _managed_venv_root() -> Path:
    cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return cache / "analitiq" / "pipeline-validator" / "venv"


def _managed_venv_python() -> str | None:
    """The managed virtualenv's interpreter, or `None` when there is not one yet.

    `None` is also the answer for a half-built venv, which is what makes it the
    only existence check here: a separate `exists()` on a written-down path could
    disagree with what actually runs.
    """
    root = _managed_venv_root()
    search = os.pathsep.join(str(root / name) for name in _VENV_SCRIPT_DIRS)
    return shutil.which("python", path=search)


def _venv_has_pin(py: str, version: str) -> bool:
    probe = (
        "import sys; from importlib.metadata import version as v;"
        f"sys.exit(0 if v('analitiq-validator') == {version!r} else 1)"
    )
    return subprocess.run([py, "-c", probe], check=False,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def ensure_deps_or_reexec(script_path: str) -> int | None:
    """Guarantee the pinned validator is importable, running `script_path` again
    under a managed venv if the current interpreter lacks it.

    Returns `None` when this interpreter already has the pin and the caller should
    carry on in it, and an EXIT CODE when a child has already done the work - the
    child inherited this process's streams, so its output is what the caller's
    caller reads and there is nothing left to print. A caller returns that code
    unchanged.

    Raises RuntimeError if the managed-venv install fails (no network / pip
    unavailable) or the package is still missing in the child.
    """
    if os.environ.get(_FROM_SOURCE):
        import importlib.util
        try:
            spec = importlib.util.find_spec("analitiq.validator")
        except ModuleNotFoundError:
            # `find_spec` RAISES rather than returning None when the parent
            # `analitiq` namespace is itself absent - which is the most likely
            # breakage this branch exists to diagnose, so it must not escape as
            # a bare traceback.
            spec = None
        # `find_spec` alone is satisfied by a BARE DIRECTORY: a leftover or
        # half-deleted `analitiq/validator/` resolves as a namespace package with
        # `origin` None, so this would report success and the real failure would
        # surface much later as an opaque ImportError inside validate.py.
        if spec is None or spec.origin in (None, "namespace"):
            raise RuntimeError(
                f"{_FROM_SOURCE} is set but `analitiq.validator` has no importable "
                f"source (spec={spec!r}). Expected the in-repo tree on sys.path: "
                f"see the repo-root conftest.py, or unset {_FROM_SOURCE} to install "
                f"the pinned {VALIDATOR_PIN}.")
        # Say so. This branch disables the version-exactness guarantee, and a
        # variable inherited from a parent process would otherwise do that
        # invisibly — an arbitrary installed build would satisfy it and every
        # validation would still report "passed".
        print(f"[analitiq] {_FROM_SOURCE}=1: using {spec.origin}, "
              f"NOT the pinned {VALIDATOR_PIN}", file=sys.stderr)
        return
    version = _pinned_version()
    if _importable(version):
        return None
    py = _managed_venv_python()
    if py is None or not _venv_has_pin(py, version):
        root = _managed_venv_root()
        try:
            root.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, "-m", "venv", str(root)],
                           check=True, stdout=sys.stderr, stderr=sys.stderr)
            py = _managed_venv_python()
            if py is None:
                raise RuntimeError(
                    f"a virtualenv was built at {root} but no interpreter was found "
                    f"in it; install the pin manually with: "
                    f"pip install --pre {VALIDATOR_PIN}")
            subprocess.run([py, "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check", "--pre", VALIDATOR_PIN],
                           check=True, stdout=sys.stderr, stderr=sys.stderr)
        except (subprocess.CalledProcessError, OSError) as exc:
            raise RuntimeError(
                f"could not install {VALIDATOR_PIN} into a managed venv ({exc}); "
                f"install it manually with: pip install --pre {VALIDATOR_PIN}") from exc
    if os.environ.get(_REEXEC_SENTINEL):
        raise RuntimeError(
            "analitiq-validator is not importable after bootstrap; install it "
            f"manually with: pip install --pre {VALIDATOR_PIN}")
    # Set before the spawn, because that is the only way it reaches the child now:
    # inheriting this process's environment is what breaks an unbounded chain of
    # re-entries when the child cannot import the pin either.
    os.environ[_REEXEC_SENTINEL] = "1"
    # skipcq: BAN-B603 - argv is the managed interpreter and the caller's own
    # script. A CHILD rather than `os.execv`, which does not replace the calling
    # process on Windows. Streams are inherited and untouched: one helper reads
    # stdin after this returns, and every helper's output is read from stdout.
    return subprocess.run([py, os.path.abspath(script_path), *sys.argv[1:]],
                          check=False).returncode
