"""The explicit setuptools package list must cover the whole source tree.

``pyproject.toml`` enumerates ``[tool.setuptools] packages`` by hand (the
``analitiq`` namespace root is deliberately not a package, so autodiscovery is
off). A subpackage missing from that list ships a wheel that silently drops
it — the suite grades the in-repo source, so nothing else would notice until
a wheel consumer hits the ImportError. This pin fails the build the moment a
package directory exists on disk without a matching list entry (or vice
versa).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SRC = PACKAGE_ROOT / "src"


def _source_packages() -> set[str]:
    contracts = SRC / "analitiq" / "contracts"
    packages: set[str] = set()
    for directory in (contracts, *(p for p in contracts.rglob("*") if p.is_dir())):
        if directory.name == "__pycache__":
            continue
        if any(child.suffix == ".py" for child in directory.iterdir()):
            packages.add(".".join(directory.relative_to(SRC).parts))
    return packages


def test_setuptools_package_list_matches_the_source_tree():
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = set(pyproject["tool"]["setuptools"]["packages"])
    expected = _source_packages()
    missing = sorted(expected - declared)
    assert not missing, (
        "package directories the wheel would silently drop — add each to "
        f"[tool.setuptools] packages in pyproject.toml: {missing}"
    )
    phantom = sorted(declared - expected)
    assert not phantom, (
        "declared packages with no matching source directory (the build "
        f"would fail, or the list has rotted): {phantom}"
    )


def test_every_data_file_under_src_is_tracked():
    """A data file the wheel must ship has to be tracked, because tracking is
    what stages it.

    ``package-data`` keeps every file the staged tree carries and
    ``scripts/build.py`` stages from ``git ls-files``, so nothing declares a
    data file one by one — and the one way such a file goes missing from a wheel
    is by never being committed. It is a quiet way to fail: the suite reads the
    source tree, where the file is right there, so only a consumer on the code
    path that opens it would notice. Python modules are covered by the package
    list above; this is the rest of the tree.
    """
    import subprocess

    listing = subprocess.run(
        ["git", "-C", str(SRC), "ls-files", "-z", "--", "."],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tracked = {SRC / name for name in listing.split("\0") if name}
    on_disk = {
        path
        for path in SRC.rglob("*")
        if path.is_file()
        and path.suffix not in (".py", ".pyc")
        and "__pycache__" not in path.parts
    }
    assert on_disk, (
        f"{SRC} carries no data files at all — the vendored grammar and the "
        "compiled rule registry both live here, so this is a path that stopped "
        "matching rather than a tree with nothing in it"
    )
    untracked = sorted(str(p.relative_to(SRC)) for p in on_disk - tracked)
    assert not untracked, (
        "data files under src/ that the wheel would silently drop — the build "
        f"stages tracked files only, so commit each of these: {untracked}"
    )
