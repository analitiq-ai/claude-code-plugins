"""The single-document rule fixture corpus, shipped with the package.

A rule whose record names a ``fixture_model`` carries documents under
``fixtures/<RULE-ID>/valid/*.json`` and ``fixtures/<RULE-ID>/invalid/*.json``,
one bare JSON payload per file. A valid fixture is one the model accepts; an
invalid fixture is one the model rejects with that rule among the rules its
enforcers raised.

:func:`rule_fixtures` loads the corpus and :func:`fixture_mismatch` grades one
fixture against the installed models, so a consumer checks the models it pins
with the grading this repo's own suite runs rather than a second copy of it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ValidationError

from .rules import rule_by_id, violated_rule_ids

FIXTURES_DIR = Path(__file__).with_name("fixtures")

Verdict = Literal["valid", "invalid"]


@dataclass(frozen=True)
class RuleFixture:
    """One fixture: the rule it grades, the verdict it expects, its file stem,
    the model named by the rule's ``fixture_model``, and the parsed document."""

    rule_id: str
    verdict: Verdict
    name: str
    model: type[BaseModel]
    document: Any


def rule_fixtures() -> tuple[RuleFixture, ...]:
    """Every fixture in the corpus, sorted by rule id, verdict and name.

    Raises ``ValueError`` for a corpus that cannot be graded as laid out: a
    directory for a rule whose record names no ``fixture_model``, a group
    other than a verdict, a file that is not ``*.json``, or a
    ``fixture_model`` that names no contract class or more than one.
    """
    fixtures: list[RuleFixture] = []
    for rule_dir in sorted(FIXTURES_DIR.iterdir()):
        model = _fixture_model(rule_dir.name)
        for group_dir in sorted(rule_dir.iterdir()):
            if group_dir.name not in get_args(Verdict):
                raise ValueError(
                    f"{group_dir}: a fixture group is one of {get_args(Verdict)}")
            for path in sorted(group_dir.iterdir()):
                if path.suffix != ".json":
                    raise ValueError(f"{path}: a fixture is a *.json file")
                fixtures.append(RuleFixture(
                    rule_id=rule_dir.name,
                    verdict=group_dir.name,
                    name=path.stem,
                    model=model,
                    document=json.loads(path.read_text(encoding="utf-8")),
                ))
    return tuple(fixtures)


def fixture_mismatch(fixture: RuleFixture) -> str | None:
    """``None`` when the models agree with the fixture, else why they do not."""
    label = f"{fixture.rule_id}/{fixture.verdict}/{fixture.name}"
    try:
        fixture.model.model_validate(fixture.document)
    except ValidationError as exc:
        if fixture.verdict == "valid":
            return f"{label}: {fixture.model.__name__} rejected a valid fixture: {exc}"
        if fixture.rule_id not in violated_rule_ids(exc):
            return (
                f"{label}: {fixture.model.__name__} rejected the fixture, but "
                f"{fixture.rule_id} is not among the rules it raised: {exc}")
        return None
    if fixture.verdict == "invalid":
        return f"{label}: {fixture.model.__name__} accepted an invalid fixture"
    return None


def _fixture_model(rule_id: str) -> type[BaseModel]:
    try:
        name = rule_by_id(rule_id).fixture_model
    except KeyError:
        raise ValueError(f"fixtures for {rule_id!r}, which no record defines") from None
    if name is None:
        raise ValueError(f"fixtures for {rule_id!r}, which names no fixture_model")
    return _model_named(name)


def _model_named(name: str) -> type[BaseModel]:
    """The one contract class called ``name``.

    ``introspect`` is imported here, not at module level: it imports every
    module under ``analitiq.contracts``, this one included.
    """
    from .introspect import contract_classes

    matches = [cls for cls in contract_classes() if cls.__name__ == name]
    if len(matches) != 1:
        raise ValueError(
            f"fixture_model {name!r} must name exactly one contract class, "
            f"found {sorted(cls.__module__ for cls in matches)}")
    return matches[0]
