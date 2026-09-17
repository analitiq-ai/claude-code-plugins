"""What both package build scripts decide the same way.

`analitiq-contract-models` and `analitiq-validator` publish alike: the source
tree IS the package, git decides which of its files ship, and staging copies
them at their paths relative to the package root. That is one decision about
one repo, so it is written once here and imported by both
`packages/*/scripts/build.py` — two copies would answer differently the first
time either one is fixed, and the answer is what reaches PyPI.

Stdlib only, like its callers: it runs on a release host with nothing
installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def git_executable() -> str:
    """The `git` executable, resolved to a full path.

    Resolving first is what turns a missing `git` into the failure below rather
    than a `FileNotFoundError` from inside `subprocess`. It does not harden the
    lookup — `which` searches the same inherited `PATH` — so the value is the
    message and the stop: absent `git` is never a fallback to an unfiltered
    tree, because the point of asking git is that tracking, not presence on
    disk, decides what ships.
    """
    exe = shutil.which("git")
    if exe is None:
        raise SystemExit(
            "build: no `git` on PATH — the staged file list is taken from the "
            "index, so there is no safe way to continue without it."
        )
    return exe


def tracked_files(root: Path) -> list[Path]:
    """Every git-tracked file under `root` — exactly what `stage_tree()` copies.

    Tracking, not presence on disk, decides what ships. A tree filtered only by
    `__pycache__` publishes whatever happens to be sitting in it when the build
    runs — a merge `.orig`, a scratch dump, a parked `.env` — and a published
    version is immutable, so a file that reaches PyPI can be yanked but never
    removed. It is also where the decision belongs: `git add` puts the file in
    a diff a reviewer reads, which a line in `pyproject.toml` does not.

    This is what lets a wheel ship a whole tree without enumerating it.
    """
    listing = subprocess.run(
        [git_executable(), "-C", str(root), "ls-files", "-z", "--", "."],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = [name for name in listing.split("\0") if name]
    if not names:
        raise SystemExit(
            f"build: git reports no tracked files under {root} — a package is "
            "never empty, so this is a path that stopped matching or a tree "
            "that is not a checkout, not a package with nothing in it"
        )
    return sorted(root / name for name in names)


def stage_tree(source_root: Path, dest_root: Path) -> list[Path]:
    """Copy every tracked file under `source_root` to the same relative path
    under `dest_root`, and return what was copied.

    Relative paths, not names: both corpora are nested directories, so a copy
    that flattened them would lose the layout their loaders read back.
    """
    copied = tracked_files(source_root)
    for src_path in copied:
        dest = dest_root / src_path.relative_to(source_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest)
    return copied
