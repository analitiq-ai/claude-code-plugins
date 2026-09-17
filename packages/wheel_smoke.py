#!/usr/bin/env python3
"""What both release workflows check about a built distribution.

Run with the smoke venv's interpreter, after installing the wheel, against the
staged tree it was built from:

    python packages/wheel_smoke.py --module analitiq.validator \\
        --staged "$RUNNER_TEMP/v-src/src/analitiq/validator" --dist "$RUNNER_TEMP/v-dist"

Two things are asserted, and both are the same question for either package, so
they are asked here once rather than in two copies of an inline script that
drift apart:

- every data file the staged tree carries reached the installed package. It is
  DERIVED from the staged tree rather than a list of filenames, so a corpus file
  committed later is covered without anyone remembering this step. `build.py`
  stages every tracked file whatever `package-data` says, so only an install of
  the actual wheel shows what the declaration kept — and a published rc is an
  immutable, burned version.
- no build-host bytecode is in the archives. The INSTALLED tree carries the
  bytecode pip compiles, so the archives are the only place the difference shows.

Whether the shipped data is *readable* — a corpus that loads and grades — is
package-specific and stays in each workflow.
"""

from __future__ import annotations

import argparse
import importlib
import pathlib
import tarfile
import zipfile


def data_files(staged: pathlib.Path) -> list[pathlib.Path]:
    """Every non-Python file under the staged package, relative to it."""
    return sorted(
        path.relative_to(staged)
        for path in staged.rglob("*")
        if path.is_file()
        and path.suffix not in (".py", ".pyc")
        and "__pycache__" not in path.parts
    )


def _member_names(archive: pathlib.Path) -> list[str]:
    """Member names of one built archive, opened by its extension.

    A wheel is a zip and an sdist a tarball; `archived_names` asks the same
    question of both, and this is the only place the two formats differ.
    """
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as zf:
            return zf.namelist()
    with tarfile.open(archive) as tf:
        return tf.getnames()


def archived_names(dist: pathlib.Path) -> list[str]:
    """Every member name of every wheel and sdist in `dist`.

    An empty `dist` is a stop, not an empty answer: the checks below read as
    passing when there is nothing to read, which is exactly what a mistyped
    path produces.
    """
    archives = sorted(dist.glob("*.whl")) + sorted(dist.glob("*.tar.gz"))
    if not archives:
        raise SystemExit(f"smoke: no wheel or sdist in {dist} — nothing was checked")
    return [name for archive in archives for name in _member_names(archive)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--module", required=True, help="Installed package to locate, e.g. analitiq.validator.")
    ap.add_argument("--staged", required=True, type=pathlib.Path, help="Staged tree the distribution was built from.")
    ap.add_argument("--dist", required=True, type=pathlib.Path, help="Directory holding the built wheel and sdist.")
    args = ap.parse_args()

    installed = pathlib.Path(importlib.import_module(args.module).__file__).parent

    expected = data_files(args.staged)
    if not expected:
        raise SystemExit(
            f"smoke: the staged tree at {args.staged} carries no data files — "
            "the walk stopped matching, since neither package ships none"
        )
    missing = [str(rel) for rel in expected if not (installed / rel).is_file()]
    if missing:
        raise SystemExit(f"smoke: the wheel dropped staged data files: {missing}")
    print(f"data files shipped: {len(expected)}")

    bytecode = [n for n in archived_names(args.dist) if n.endswith(".pyc") or "__pycache__" in n]
    if bytecode:
        raise SystemExit(f"smoke: build-host bytecode shipped: {bytecode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
