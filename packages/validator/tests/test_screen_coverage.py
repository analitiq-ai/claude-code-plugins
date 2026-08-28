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

What it does not reach: a test outside this package's own tree, and a
subprocess. The out-of-process path has its own screen at `conftest.run_cli`,
which every CLI test here goes through; the other suites under `tests/` call
the validator to assert an example is clean, where a crash fails them anyway.
"""
from __future__ import annotations

import ast
from pathlib import Path

from conftest import SCREENED_ENTRY_POINTS

TESTS_DIR = Path(__file__).resolve().parent


#: Every module path an entry point is reachable through. `analitiq.validator`
#: re-exports both, and each also lives in the module that defines it, so a
#: check reading only the package name settles one spelling of several.
_VALIDATOR_MODULES = frozenset({
    "analitiq.validator",
    "analitiq.validator._core",
    "analitiq.validator.connectors",
})


def _reaches_an_entry_point(tree: ast.AST) -> set[str]:
    """Screened names this module can call without going through the fixture.

    Every import spelling, because they are equally effective and only one of
    them is the obvious one: `from analitiq.validator import validate_document`
    binds the name; `from analitiq import validator` and `import
    analitiq.validator as v` bind the module and reach the same function
    through an attribute; and both live in a defining module of their own,
    which `analitiq.validator` only re-exports.
    """
    screened = set(SCREENED_ENTRY_POINTS)
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in _VALIDATOR_MODULES:
                bound |= {a.name for a in node.names} & screened
            # `from analitiq import validator` — the module itself, from which
            # an attribute access reaches either entry point.
            if node.module == "analitiq" and any(
                    a.name == "validator" for a in node.names):
                bound.add("analitiq.validator (as a module)")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _VALIDATOR_MODULES:
                    bound.add(f"{alias.name} (as a module)")
    return bound


def test_no_test_module_calls_an_entry_point_out_from_under_the_screen():
    modules = sorted(p for p in TESTS_DIR.rglob("test_*.py"))
    assert modules, (
        f"no test modules found under {TESTS_DIR} — this walk has stopped "
        "measuring rather than found nothing to report"
    )
    offenders = {
        path.name: sorted(names)
        for path in modules
        if (names := _reaches_an_entry_point(
            ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert not offenders, (
        "these test modules reach a screened entry point without the fixture, "
        "so their calls bypass the crash screen and a crash there reads as a "
        f"verdict: {offenders}. Take the `validator` fixture instead, which "
        "re-exports every other symbol unchanged."
    )
