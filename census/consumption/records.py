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
``.claude/rules/reachability-dispositions.md``; :func:`record_report`
carries every finding the guard can reach.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

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
    rules = tuple(document["rules"])
    if not any(rule["status"] == "active" for rule in rules):
        # The non-vacuity floor (`.claude/rules/guards.md`): a registry with
        # nothing active means the extractor has stopped measuring, and must
        # not report in the same voice as a census finding nothing wrong.
        raise ValueError(
            "compiled registry holds no active records — refusing a "
            "vacuously clean record census"
        )
    return rules


def rationale_sha256(rationale: str) -> str:
    return hashlib.sha256(rationale.encode("utf-8")).hexdigest()


def governed_unread(
    manifest: dict[str, Any], rules: tuple[dict[str, Any], ...]
) -> dict[str, tuple[str, ...]]:
    """Per active record, the unread fields its ``targets``/``fields`` govern.

    A target names a model class bare (``Param``), and binds through the
    MRO the way the registry defines ``targets``: it is matched against the
    pydantic class names in every reachable model's ``__mro__``, every
    match counting — a rule over a union governs each branch, and a rule
    binding a shared base governs each reachable subclass inheriting its
    fields. A field expression's head (before any ``[]`` or ``.``) is the
    field name matched; a tail names structure inside another model, which
    this census covers as that model's own fields, never through the
    expression. Records governing no unread field do not appear, and a
    record naming no ``fields:`` is outside the guard — unlocated, not
    affirmed. A target no reachable model's MRO carries is coverage, not
    silence — the field census walks only what the manifest's roots reach —
    but a head that lands on NO matched model is a name this census could
    not resolve, and :func:`record_report` reports it rather than dropping
    it.
    """
    governed, _, _ = _resolve(manifest, rules)
    return governed


def _resolve(
    manifest: dict[str, Any], rules: tuple[dict[str, Any], ...]
) -> tuple[
    dict[str, tuple[str, ...]], tuple[tuple[str, str], ...], tuple[str, ...]
]:
    unread = classify(manifest)["unread"]
    models = reachable_models(manifest)
    by_class: dict[str, list[str]] = {}
    for name, cls in models.items():
        for base in cls.__mro__:
            if issubclass(base, BaseModel) and base is not BaseModel:
                by_class.setdefault(base.__name__, []).append(name)

    governed: dict[str, tuple[str, ...]] = {}
    unresolved: list[tuple[str, str]] = []
    unlocated: list[str] = []
    for rule in rules:
        if rule["status"] != "active":
            continue
        matched = [m for target in rule["targets"] for m in by_class.get(target, ())]
        if not matched:
            continue
        if not rule["fields"]:
            if any((m, f) in unread for m in matched for f in models[m].model_fields):
                unlocated.append(rule["id"])
            continue
        refs = set()
        for field in rule["fields"]:
            head = field.split("[]")[0].split(".")[0]
            if not any(head in models[m].model_fields for m in matched):
                unresolved.append((rule["id"], field))
                continue
            refs |= {
                f"{m.removeprefix(_PREFIX)}.{head}"
                for m in matched
                if (m, head) in unread
            }
        if refs:
            governed[rule["id"]] = tuple(sorted(refs))
    return governed, tuple(sorted(set(unresolved))), tuple(sorted(unlocated))


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
    #: An active record whose target is reachable but whose field expression
    #: names a field no matched model declares — a name this census could
    #: not resolve, reported rather than silently exempted.
    unresolved_fields: tuple[tuple[str, str], ...]
    #: Informational, never gating: active records whose matched carriers
    #: hold unread fields but which name no ``fields:`` — outside the guard
    #: by the fields-naming boundary, listed so the reviewer obligation in
    #: ``.claude/rules/reachability-dispositions.md`` has something to read.
    unlocated: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (
            self.unaffirmed
            or self.stale_refs
            or self.stale_rationale
            or self.orphaned
            or self.duplicates
            or self.unresolved_fields
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
            f"current {', '.join(item[2])}",
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
        group(
            "field expressions this census could not resolve on any matched "
            "model — a renamed field, or a record naming one the model does "
            "not declare",
            self.unresolved_fields,
            lambda item: f"{item[0]}: {item[1]!r}",
        )
        verdict = (
            "record affirmations are complete and current"
            if not lines
            else "\n".join(lines).rstrip()
        )
        if self.unlocated:
            verdict += (
                "\n\nnot gating — records over carriers with unread fields "
                "that name no fields: (outside the guard by the "
                f"fields-naming boundary) ({len(self.unlocated)}):\n    "
                + ", ".join(self.unlocated)
            )
        return verdict


def record_report(
    manifest: dict[str, Any],
    rules: tuple[dict[str, Any], ...],
    affirmations: tuple[RecordAffirmation, ...],
) -> RecordReport:
    governed, unresolved, unlocated = _resolve(manifest, rules)
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
        unresolved_fields=unresolved,
        unlocated=unlocated,
    )
