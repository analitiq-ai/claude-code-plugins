"""Prove the canonical-types renderer's drift guards FIRE — not merely pass.

`build_canonical_types_doc` in `scripts/render_schemas.py` is the wall between
the vendored engine grammar and the published vocabulary: its grouping check is
what turns a pin bump that adds a family into a loud render failure instead of
a silently narrower published document, and its example check keeps the
description's examples honest. CI's `render_schemas.py check` only exercises
the clean path — it stays green if either guard is deleted. Same charter as
test_render_schemas.py next door: inject the violation, assert the guard trips.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts", "render_schemas")

import render_schemas  # noqa: E402

from analitiq.contracts import arrow_grammar  # noqa: E402


def test_clean_build_succeeds():
    doc = render_schemas.build_canonical_types_doc()
    assert set(doc["$defs"]) >= {"canonical_type", "canonical_type_or_template"}


def test_missing_family_in_grouping_fails_loudly(monkeypatch):
    """A pin bump that adds an engine family must fail the render until the
    display grouping names it — otherwise the published vocabulary silently
    narrows relative to the pattern."""
    trimmed = tuple(
        (name, title, desc, tuple(m for m in members if m != "Utf8"))
        for name, title, desc, members in render_schemas._CANONICAL_GROUPS
    )
    monkeypatch.setattr(render_schemas, "_CANONICAL_GROUPS", trimmed)
    with pytest.raises(RuntimeError, match="out of sync with the vendored"):
        render_schemas.build_canonical_types_doc()


def test_duplicated_family_in_grouping_fails_loudly(monkeypatch):
    doubled = render_schemas._CANONICAL_GROUPS + (
        ("extra_type", "Extra", "duplicate member", ("Utf8",)),
    )
    monkeypatch.setattr(render_schemas, "_CANONICAL_GROUPS", doubled)
    with pytest.raises(RuntimeError, match="out of sync with the vendored"):
        render_schemas.build_canonical_types_doc()


def test_stale_example_fails_loudly(monkeypatch):
    monkeypatch.setattr(
        render_schemas,
        "_CANONICAL_EXAMPLES",
        render_schemas._CANONICAL_EXAMPLES + ("Interval(YEAR_MONTH)",),
    )
    with pytest.raises(RuntimeError, match="does not match the"):
        render_schemas.build_canonical_types_doc()


def test_check_reports_builder_failure_instead_of_truncating(monkeypatch):
    """A builder failure participates in the aggregate `check` as a normal
    (False, message) result — it must not abort the run mid-way."""
    monkeypatch.setattr(
        render_schemas,
        "_CANONICAL_EXAMPLES",
        render_schemas._CANONICAL_EXAMPLES + ("Interval(YEAR_MONTH)",),
    )
    ok, msg = render_schemas.check_canonical_types()
    assert not ok and "cannot render" in msg


def _representative(family: str) -> str:
    """One canonical string per family, derived from its param specs — so this
    test extends itself when a pin bump adds a family."""
    spec = arrow_grammar.FAMILIES[family]
    params = spec.get("params")
    if not params:
        return family
    args = []
    for param in params:
        if param.get("optional"):
            continue
        if param["kind"] == "int":
            args.append(str(param["min"]))
        elif param["kind"] == "unit":
            args.append(param["allowed"][0])
        else:  # pragma: no cover - no required timezone param exists today
            raise AssertionError(f"unhandled required param kind {param['kind']!r}")
    return f"{family}({', '.join(args)})"


def test_every_manifest_family_is_accepted_by_the_published_vocabulary():
    """Derived parity: a representative canonical per family must validate
    against the built document's strict `canonical_type` — catching a grouping
    or pattern regression that drops a family, without hand-listed fixtures
    that predate future families."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    doc = render_schemas.build_canonical_types_doc()
    registry = Registry().with_resource(
        "canonical-types.json", Resource(contents=doc, specification=DRAFT202012)
    )
    validator = Draft202012Validator(
        {"$ref": "canonical-types.json#/$defs/canonical_type"}, registry=registry
    )
    pattern = re.compile(arrow_grammar.ARROW_TYPE_PATTERN)
    for family in arrow_grammar.FAMILY_NAMES:
        rep = _representative(family)
        assert pattern.fullmatch(rep), f"{rep!r} not accepted by ARROW_TYPE_PATTERN"
        errs = list(validator.iter_errors(rep))
        assert not errs, f"{rep!r} rejected by published canonical_type: {errs}"
