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

This is one of three channels, and the three are screened by one statement of
which names matter (`SCREENED_ENTRY_POINTS` and `SCREENED_NAME_SHAPES`): an
import is caught here, an attribute access on the fixture is wrapped by
`_ScreenedValidator.__getattr__`, and the out-of-process path is screened at
`conftest.run_cli_argv`, which every CLI test here goes through.

What none of them reaches is a test outside this package's own tree. Those
suites call the validator to assert an example is clean, where a crash fails
them anyway.
"""
from __future__ import annotations

import ast
from pathlib import Path

from conftest import (
    SCREENED_ENTRY_POINTS,
    SCREENED_NAME_SHAPES,
    VALIDATOR_SRC_ROOT,
)

TESTS_DIR = Path(__file__).resolve().parent


def _validator_modules() -> frozenset[str]:
    """Every module path a finding-returning function is reachable through.

    Derived, not listed. `analitiq.validator` re-exports the entry points, and
    each finding-returning function also lives in the module that defines it —
    so a hand-kept list settles the spellings someone happened to think of, and
    reports the same green when a new module joins the package. `pipelines`
    already defines one this list would not have named.

    Read off the filesystem rather than by importing the package: an `import
    analitiq.validator` here is a statement this module's own check would see
    and report, correctly, since it cannot tell locating a package from
    calling into one.
    """
    package = VALIDATOR_SRC_ROOT / "analitiq" / "validator"
    # `rglob`: a subpackage is as importable as a top-level module, and the
    # non-empty guard below cannot see one missing — it is satisfied by the
    # flat modules that will always be there.
    modules = {
        "analitiq.validator." + ".".join(
            path.relative_to(package).with_suffix("").parts)
        for path in package.rglob("*.py") if path.stem != "__init__"
    }
    assert modules, (
        f"no modules found under {package} — this walk is reading nothing and "
        "would report agreement over an empty set"
    )
    return frozenset(modules | {"analitiq.validator"})


_VALIDATOR_MODULES = _validator_modules()


def _returns_findings(name: str) -> bool:
    """Whether importing this name hands a caller an unscreened finding list.

    The two entry points by name, and every other finding-returning function
    by the shape of its name — a per-kind `_validate_*`, or anything ending
    `_findings`. Both shapes are conventions this package keeps, and a
    function that returns findings under some third name is the reader's to
    catch; that is said here rather than left for someone to discover.
    """
    return name in SCREENED_ENTRY_POINTS or any(
        shape in name for shape in SCREENED_NAME_SHAPES)


def _reaches_an_entry_point(tree: ast.AST) -> set[str]:
    """Screened names this module can call without going through the fixture.

    Every import spelling, because they are equally effective and only one of
    them is the obvious one: `from analitiq.validator import validate_document`
    binds the name; `from analitiq import validator` and `import
    analitiq.validator as v` bind the module and reach the same function
    through an attribute; and both live in a defining module of their own,
    which `analitiq.validator` only re-exports.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in _VALIDATOR_MODULES:
                bound |= {a.name for a in node.names if _returns_findings(a.name)}
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
