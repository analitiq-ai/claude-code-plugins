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
