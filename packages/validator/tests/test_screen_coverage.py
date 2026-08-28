"""The crash screen covers every path a finding reaches a test by.

`conftest._ScreenedValidator` wraps the entry points that return findings, so
a crash cannot satisfy a count, an ordering or a liveness assertion while
measuring nothing. The wrap only holds for calls that go through the fixture:
a module doing `from analitiq.validator import validate_document` calls the
unscreened function, and the crash it may be measuring looks exactly like the
verdict it thinks it measured.

Nothing about that is visible at the call site, so it is decided here instead —
lexically, over the import statements, which is a question about which name a
module binds and not about what any of its tests mean.
"""
from __future__ import annotations

import ast
from pathlib import Path

from conftest import SCREENED_ENTRY_POINTS

TESTS_DIR = Path(__file__).resolve().parent


def _direct_imports(tree: ast.AST) -> set[str]:
    """Screened names this module binds straight from the validator package."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "analitiq.validator":
            bound |= {a.name for a in node.names} & set(SCREENED_ENTRY_POINTS)
    return bound


def test_no_test_module_calls_an_entry_point_out_from_under_the_screen():
    modules = sorted(p for p in TESTS_DIR.glob("test_*.py"))
    assert modules, (
        f"no test modules found under {TESTS_DIR} — this walk has stopped "
        "measuring rather than found nothing to report"
    )
    offenders = {
        path.name: sorted(names)
        for path in modules
        if (names := _direct_imports(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert not offenders, (
        "these test modules import a screened entry point directly, so their "
        "calls bypass the crash screen and a crash there reads as a verdict: "
        f"{offenders}. Take the `validator` fixture instead, which re-exports "
        "every other symbol unchanged."
    )
