"""Nothing under `plugins/` imports a `_`-prefixed name from the published packages.

A plugin script runs against the pinned release of `analitiq-validator` and
`analitiq-contract-models`, installed into a user's managed venv. A private
name is one the package may rename without notice, so a script reaching for it
works on the day it is written and breaks on the next pin bump — for every
user, at once, with no test here to notice, because the suite grades the
in-repo source that moved in lockstep. Whatever a script needs from a package
is public API or it is not there yet.

Lexical throughout: an AST walk over every `import` in every tracked script,
judged on the imported name's spelling alone.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS = REPO_ROOT / "plugins"
PACKAGES = ("analitiq.validator", "analitiq.contracts")


def _private_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(PACKAGES):
            segments = node.module.split(".")
            hits.extend(f"from {node.module} import {alias.name}" for alias in node.names
                        if alias.name.startswith("_"))
            if any(s.startswith("_") for s in segments):
                hits.append(f"from {node.module} import …")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGES) and any(
                        s.startswith("_") for s in alias.name.split(".")):
                    hits.append(f"import {alias.name}")
    return hits


def test_plugin_scripts_import_only_public_package_names():
    scripts = sorted(PLUGINS.rglob("*.py"))
    assert scripts, f"no Python under {PLUGINS} — the scan is reading nothing"
    offending = {
        path.relative_to(REPO_ROOT).as_posix(): hits
        for path in scripts if (hits := _private_imports(path))
    }
    assert not offending, (
        "plugin scripts importing private package names — lift what they need "
        f"into the package's public surface instead: {offending}")
