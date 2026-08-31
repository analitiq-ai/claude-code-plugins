"""The record half of the reachability census — rationales over unread fields.

A rule record's ``rationale`` ships to users inside the compiled registry and
routinely justifies the rule with what the engine does with the governed
field at run time. The field half of the census
(``census.consumption.reachability``) grades fields, not records, so a
rationale asserting a read the pinned manifest denies goes stale with
nothing turning red.

This module closes that gap the way every census here does — the guard
locates, a reader decides (``.claude/rules/guards.md``). Locating is
lexical: a record's ``targets`` and ``fields`` are resolved against the
reachable models and intersected with the manifest's unread set, with no
reading of the rationale's English. Deciding — whether the rationale is
honest about a field the manifest leaves unread — is the reader's, summoned
through a :class:`RecordAffirmation` in
``census.consumption.record_affirmations``: one entry per governing record,
pinning the refs it was judged against and the sha256 of the rationale
judged. A rationale edit, a pin bump that moves the unread set under a
record, and a record newly governing an unread field each break the pin and
fail the build until a reader re-affirms. What the reader holds the
rationale to is the record-affirmation section of
``.claude/rules/reachability-dispositions.md``.

:func:`record_report` computes every finding; the census test suite and
``scripts/render_contract_consumption.py check`` both assert on it, so the
lint and the tool can never disagree.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from census.consumption.reachability import classify, reachable_models

__all__ = [
    "RecordAffirmation",
    "RecordReport",
    "governed_unread",
    "load_rules",
    "rationale_sha256",
    "record_report",
]

_PREFIX = "analitiq.contracts."


def load_rules() -> tuple[dict[str, Any], ...]:
    """The compiled registry, as the wheel ships it.

    The compiled copy rather than the YAML records, so this census needs no
    YAML parser and can never read a registry the compiler refused;
    ``render_rules.py check`` holds the copy fresh.
    """
    from analitiq.contracts.shared.rule_record import RULES_PATH

    document = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return tuple(document["rules"])


def rationale_sha256(rationale: str) -> str:
    return hashlib.sha256(rationale.encode("utf-8")).hexdigest()


def governed_unread(
    manifest: dict[str, Any], rules: tuple[dict[str, Any], ...]
) -> dict[str, tuple[str, ...]]:
    """Per active record, the unread fields its ``targets``/``fields`` govern.

    A target names a model class bare (``Param``); it is matched against the
    trailing segment of every reachable model's qualified name, every match
    counting — a rule over a union governs each branch. A field expression's
    head (before any ``[]`` or ``.``) is the field name matched. Records
    governing no unread field do not appear.
    """
    unread = classify(manifest)["unread"]
    by_class: dict[str, list[str]] = {}
    for name in reachable_models(manifest):
        by_class.setdefault(name.rsplit(".", 1)[-1], []).append(name)

    governed: dict[str, tuple[str, ...]] = {}
    for rule in rules:
        if rule["status"] != "active":
            continue
        refs = {
            f"{model.removeprefix(_PREFIX)}.{head}"
            for target in rule["targets"]
            for model in by_class.get(target, ())
            for head in {f.split("[]")[0].split(".")[0] for f in rule["fields"]}
            if (model, head) in unread
        }
        if refs:
            governed[rule["id"]] = tuple(sorted(refs))
    return governed


@dataclass(frozen=True)
class RecordAffirmation:
    """One record whose rationale a reader judged against the unread set.

    ``refs`` are the ``analitiq.contracts``-relative ``model.field`` names
    the judgment covered, and ``rationale_sha256`` fingerprints the wording
    judged — the same wording-ratchet the prose census uses, so neither the
    rationale nor the ground it was judged on can move silently.
    """

    rule_id: str
    refs: tuple[str, ...]
    rationale_sha256: str

    def __post_init__(self) -> None:
        if not self.refs:
            raise ValueError(f"{self.rule_id}: an affirmation covers at least one ref")
        if tuple(sorted(set(self.refs))) != self.refs:
            raise ValueError(f"{self.rule_id}: refs must be sorted and unique")


@dataclass(frozen=True)
class RecordReport:
    """The governing records diffed against the affirmations.

    Every finding is a set or hash comparison; whether a rationale is honest
    is the reader's — the record-affirmation section of
    ``.claude/rules/reachability-dispositions.md`` — and a finding here is
    how that reader is summoned.
    """

    #: A record governs an unread field and no affirmation covers it.
    unaffirmed: tuple[tuple[str, tuple[str, ...]], ...]
    #: The unread set under a record moved since it was affirmed.
    stale_refs: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    #: The rationale was edited since it was affirmed.
    stale_rationale: tuple[str, ...]
    #: An affirmation of a record that no longer governs an unread field —
    #: retired, unknown, or its fields are now claimed.
    orphaned: tuple[str, ...]
    #: More than one affirmation for one record.
    duplicates: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (
            self.unaffirmed
            or self.stale_refs
            or self.stale_rationale
            or self.orphaned
            or self.duplicates
        )

    def render(self) -> str:
        lines: list[str] = []

        def group(title: str, items, line_of) -> None:
            if not items:
                return
            lines.append(f"{title} ({len(items)}):")
            lines.extend(f"    {line_of(item)}" for item in items)
            lines.append("")

        group(
            "records governing unread fields with no affirmation — the pinned "
            "manifest claims no read of fields these rules govern; re-read "
            "each rationale against that fact and add a RecordAffirmation",
            self.unaffirmed,
            lambda item: f"{item[0]}: {', '.join(item[1])}",
        )
        group(
            "affirmations whose refs the census no longer computes — the "
            "unread set under the record moved; re-read the rationale and "
            "re-affirm against the current refs",
            self.stale_refs,
            lambda item: f"{item[0]}: affirmed {', '.join(item[1])}; "
            f"current {', '.join(item[2]) or '(none)'}",
        )
        group(
            "affirmations whose rationale hash no longer matches — the "
            "wording moved; re-read and re-affirm",
            self.stale_rationale,
            lambda rule_id: rule_id,
        )
        group(
            "affirmations of records that govern no unread field — remove",
            self.orphaned,
            lambda rule_id: rule_id,
        )
        group(
            "duplicate affirmations",
            self.duplicates,
            lambda rule_id: rule_id,
        )
        if not lines:
            return "record affirmations are complete and current"
        return "\n".join(lines).rstrip()


def record_report(
    manifest: dict[str, Any],
    rules: tuple[dict[str, Any], ...],
    affirmations: tuple[RecordAffirmation, ...],
) -> RecordReport:
    governed = governed_unread(manifest, rules)
    rationales = {rule["id"]: rule["rationale"] for rule in rules}

    seen: dict[str, int] = {}
    for entry in affirmations:
        seen[entry.rule_id] = seen.get(entry.rule_id, 0) + 1
    duplicates = tuple(sorted(rid for rid, n in seen.items() if n > 1))

    affirmed = {entry.rule_id: entry for entry in affirmations}
    unaffirmed = tuple(
        (rid, refs) for rid, refs in sorted(governed.items()) if rid not in affirmed
    )
    stale_refs = tuple(
        (entry.rule_id, entry.refs, governed[entry.rule_id])
        for entry in affirmations
        if entry.rule_id in governed and entry.refs != governed[entry.rule_id]
    )
    stale_rationale = tuple(
        entry.rule_id
        for entry in affirmations
        if entry.rule_id in governed
        and entry.rationale_sha256 != rationale_sha256(rationales[entry.rule_id])
    )
    orphaned = tuple(
        sorted(entry.rule_id for entry in affirmations if entry.rule_id not in governed)
    )
    return RecordReport(
        unaffirmed=unaffirmed,
        stale_refs=stale_refs,
        stale_rationale=stale_rationale,
        orphaned=orphaned,
        duplicates=duplicates,
    )
