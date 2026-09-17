"""The cross-document rule case corpus, shipped with the package.

A rule enforced by a check in this package can carry cases under
``cases/<RULE-ID>/valid/<case-name>/`` and ``cases/<RULE-ID>/invalid/<case-name>/``.
A case directory is a document set written to disk (`document_set.DocumentSet`):
each file's path relative to the case root is its key. The entry file at the
case root decides how the case is validated, and a case root holds exactly one:

- ``connector.json`` — a connector package, validated at its path so its
  sibling type maps and ``endpoints/*.json`` are read beside it;
- ``bundle.json`` — a pipeline bundle, validated as the CLI validates one.

:func:`rule_cases` loads the corpus and :func:`case_mismatch` grades one case
against the installed validator, so a consumer checks the validator it pins
with the grading this repo's own suite runs rather than a second copy of it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ._core import finding_costs_a_pass, validate_document

CASES_DIR = Path(__file__).with_name("cases")

Verdict = Literal["valid", "invalid"]

_CONNECTOR_ENTRY = "connector.json"
_BUNDLE_ENTRY = "bundle.json"


@dataclass(frozen=True)
class RuleCase:
    """One case: the rule it grades, the verdict it expects, its directory name,
    and the case root holding its documents."""

    rule_id: str
    verdict: Verdict
    name: str
    root: Path


def rule_cases() -> tuple[RuleCase, ...]:
    """Every case in the corpus, sorted by rule id, verdict and name.

    Raises ``ValueError`` for a corpus that cannot be graded as laid out: a
    directory for an id no record defines or for a rule no check in
    ``analitiq.validator`` enforces, a group other than a verdict, or a case
    root without exactly one entry file.
    """
    cases: list[RuleCase] = []
    for rule_dir in sorted(CASES_DIR.iterdir()):
        _require_validator_check(rule_dir.name)
        for group_dir in sorted(rule_dir.iterdir()):
            if group_dir.name not in get_args(Verdict):
                raise ValueError(f"{group_dir}: a case group is one of {get_args(Verdict)}")
            for root in sorted(group_dir.iterdir()):
                _entry_file(root)
                cases.append(RuleCase(
                    rule_id=rule_dir.name, verdict=group_dir.name, name=root.name, root=root))
    return tuple(cases)


def case_findings(case: RuleCase) -> list[dict]:
    """The findings the validator reports for the case's documents."""
    entry = _entry_file(case.root)
    document = json.loads(entry.read_text(encoding="utf-8"))
    if entry.name == _CONNECTOR_ENTRY:
        return validate_document(document, doc_path=entry)
    return validate_document(document)


def case_mismatch(case: RuleCase) -> str | None:
    """``None`` when the validator agrees with the case, else why it does not.

    An invalid case needs a ``fail`` finding naming its rule. A valid case
    needs no finding naming its rule and no finding that costs the pass: a
    document set failing on another rule is not one the rule holds for.
    """
    label = f"{case.rule_id}/{case.verdict}/{case.name}"
    findings = case_findings(case)
    named = [f for f in findings if f.get("rule") == case.rule_id]
    if case.verdict == "invalid":
        if any(f["kind"] == "fail" for f in named):
            return None
        return f"{label}: no fail finding names {case.rule_id}: {findings}"
    if named:
        return f"{label}: findings name {case.rule_id} for a valid case: {named}"
    costly = [f for f in findings if finding_costs_a_pass(f)]
    if costly:
        return f"{label}: the valid case does not pass: {costly}"
    return None


def _require_validator_check(rule_id: str) -> None:
    # Imported here for the reason `_core` gives: a module-level import of the
    # contract models would raise before the kinds' missing-dependency guard.
    from analitiq.contracts.shared.rules import rule_by_id

    try:
        validator = rule_by_id(rule_id).validator
    except KeyError:
        raise ValueError(f"cases for {rule_id!r}, which no record defines") from None
    if not (validator or "").startswith("analitiq.validator."):
        raise ValueError(
            f"cases for {rule_id!r}, whose validator {validator!r} is not a check "
            "in analitiq.validator")


def _entry_file(root: Path) -> Path:
    entries = [root / name for name in (_CONNECTOR_ENTRY, _BUNDLE_ENTRY) if (root / name).is_file()]
    if len(entries) != 1:
        raise ValueError(
            f"{root}: a case root holds exactly one entry file, "
            f"{_CONNECTOR_ENTRY} or {_BUNDLE_ENTRY}")
    return entries[0]
