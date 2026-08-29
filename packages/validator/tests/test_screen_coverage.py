"""The crash screen covers every path a finding reaches a test by.

`_screen._ScreenedValidator` wraps the entry points that return findings, so
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
`_screen.run_cli_argv`, which every CLI test here goes through.

What none of them reaches is a test outside this package's own tree. Those
suites call the validator to assert an example is clean, where a crash fails
them anyway.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from analitiq.validator._core import _guard_opening
from _screen import (
    SCREENED_ENTRY_POINTS,
    _ScreenedValidator,
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
    modules = set()
    for path in package.rglob("*.py"):
        parts = path.relative_to(package).with_suffix("").parts
        # A subpackage is importable under its OWN dotted name too, and that
        # name is spelled by its `__init__.py` — dropping every `__init__`
        # outright leaves `analitiq.validator.sub` unreachable while
        # `analitiq.validator.sub.mod` is covered.
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            modules.add("analitiq.validator." + ".".join(parts))
    assert modules, (
        f"no modules found under {package} — this walk is reading nothing and "
        "would report agreement over an empty set"
    )
    return frozenset(modules | {"analitiq.validator"})


_VALIDATOR_MODULES = _validator_modules()


def _returns_findings(name: str) -> bool:
    """Whether importing this name hands a caller an unscreened finding list.

    The entry points by name, and every other finding-returning function
    by the shape of its name — a per-kind `_validate_*`, or anything ending
    `_findings`. Both shapes are conventions this package keeps, and a
    function that returns findings under some third name is the reader's to
    catch; that is said here rather than left for someone to discover.
    """
    return name in SCREENED_ENTRY_POINTS or any(
        shape in name for shape in SCREENED_NAME_SHAPES)


def _reaches_an_entry_point(tree: ast.AST) -> set[str]:
    """Screened names this module can call without going through the fixture.

    Two kinds of reach, and both are decided by shape rather than by intent.

    A NAME import binds the function itself, so importing it is the whole of
    the hazard. A MODULE import is not — a module is imported to patch it as
    often as to call through it, and `monkeypatch.setattr(connectors, ...)` is
    a legitimate reason this suite has. So a bound module counts only where the
    file also CALLS a screened name on it: `connectors._embedded_schema_findings(...)`
    is a call site, and passing the module to something else is not. That is a
    question about the shape of an expression, not about what a test means by
    it — `.claude/rules/guards.md` draws the line there, and a waiver list
    beside this check would be the admission that a reader was deciding.
    """
    bound: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in _VALIDATOR_MODULES:
                bound |= {a.name for a in node.names if _returns_findings(a.name)}
            # `from analitiq import validator`, and `from analitiq.validator
            # import connectors` — a module rather than a name. Recorded, and
            # judged below by whether anything is called on it.
            for alias in node.names:
                if f"{node.module}.{alias.name}" in _VALIDATOR_MODULES:
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _VALIDATOR_MODULES:
                    # `import a.b.c` with no alias binds `a`, and the call is
                    # written out in full — so the name to watch is the whole
                    # dotted path, not its first component.
                    modules.add(alias.asname or alias.name)
    # A bound module reaches a screened name only through a call on it.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and _returns_findings(node.func.attr)):
            continue
        prefix = _dotted(node.func.value)
        if prefix is not None and prefix in modules:
            bound.add(f"{prefix}.{node.func.attr}()")
    return bound


def _dotted(node: ast.AST) -> str | None:
    """The dotted name an expression spells, or None if it spells none.

    `analitiq.validator.validate_document(...)` reaches the entry point
    through an `Attribute` chain, not a bare `Name` — reading only the base
    made the check blind to the plainest module spelling there is.
    """
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def test_every_screened_entry_point_has_a_wrapper_that_screens_it():
    """The tuple is read by two things, and only one of them was enforced.

    A name added to it whose spelling matches no `SCREENED_NAME_SHAPES` gets
    handed back raw by `__getattr__`, while this module goes on reporting the
    import channel covered — which is the state `validate_pipeline_bundle` was
    in before it was noticed. Closing the instance leaves the class open.
    """
    unwrapped = sorted(
        name for name in SCREENED_ENTRY_POINTS
        if name not in vars(_ScreenedValidator)
        and not any(shape in name for shape in SCREENED_NAME_SHAPES)
    )
    assert not unwrapped, (
        f"{unwrapped} are named as screened but nothing screens them: they "
        "have no wrapper method on _ScreenedValidator and their names match "
        "no shape __getattr__ wraps, so a caller gets the raw function"
    )

    # Existing is not screening. Each wrapper is driven with a finding list
    # carrying a crash, and must refuse it — a wrapper that forgot `_screen`
    # and returned the raw call satisfies the check above exactly as well.
    class _Crashing:
        def __getattr__(self, name):
            def _one(*_args, **_kwargs):
                return [{"validator": "document", "severity": "error", "path": "/",
                         "message": _guard_opening("document") + " (X); why"}]
            return _one

        @staticmethod
        def is_guard_finding(item):
            from analitiq.validator import is_guard_finding
            return is_guard_finding(item)

    screened = _ScreenedValidator(_Crashing())
    for name in SCREENED_ENTRY_POINTS:
        with pytest.raises(AssertionError, match="a check crashed"):
            getattr(screened, name)()

    # And the OTHER channel. The names above all have explicit methods, so a
    # loop over them alone never drives `__getattr__`'s wrapper — which covers
    # every `_validate_*` / `*_findings` helper and is the larger surface.
    for name in ("_validate_api_endpoint", "_embedded_schema_findings"):
        assert any(shape in name for shape in SCREENED_NAME_SHAPES), name
        with pytest.raises(AssertionError, match="a check crashed"):
            getattr(screened, name)()


@pytest.mark.parametrize("source, caught", [
    ("from analitiq.validator import validate_document", True),
    ("from analitiq.validator._core import validate_document", True),
    ("from analitiq.validator.connectors import _validate_api_endpoint", True),
    # A bound module, judged by whether a screened name is CALLED on it.
    ("import analitiq.validator\nanalitiq.validator.validate_document({})", True),
    ("import analitiq.validator.connectors\n"
     "analitiq.validator.connectors._embedded_schema_findings({})", True),
    ("import analitiq.validator as v\nv.validate_document({})", True),
    ("from analitiq import validator\nvalidator.check_coverage({})", True),
    ("from analitiq.validator import connectors\n"
     "connectors._embedded_schema_findings({})", True),
    # Bound to patch, not to call through — the reason the call shape decides.
    ("from analitiq.validator import connectors\n"
     "monkeypatch.setattr(connectors, 'x', y)", False),
    ("import analitiq.validator\nprint(analitiq.validator.__file__)", False),
    # Not this package.
    ("from json import loads\nloads('{}')", False),
])
def test_the_import_scan_sees_every_spelling_that_reaches_one(source, caught):
    """The detector's own positive control.

    On this tree it finds nothing, because no test reaches an entry point that
    way — which is indistinguishable from a detector that has stopped looking.
    It was: widening it to bound modules silently dropped
    `import analitiq.validator` followed by a dotted call, the plainest
    spelling of all, and the suite stayed green because nothing writes it.
    """
    found = _reaches_an_entry_point(ast.parse(source))
    assert bool(found) is caught, found


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
