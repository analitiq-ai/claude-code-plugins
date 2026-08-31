"""The record half of the reachability census.

Two halves, asserted in the order the census defines them:

1. **The live gate**: every active rule record whose ``targets``/``fields``
   intersect the pinned manifest's unread set carries a current
   ``RecordAffirmation``, and no affirmation outlives its record. This is
   the test CI feels when a rationale is edited, a pin bump moves the
   unread set, or a record starts governing an unread field.
2. **The guard itself**: each finding kind of
   :func:`census.consumption.records.record_report`, on synthetic data, so
   a regression in the guard is distinguishable from a regression in the
   registry.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from census.consumption import pin
from census.consumption.record_affirmations import AFFIRMATIONS
from census.consumption.records import (
    RecordAffirmation,
    governed_unread,
    load_rules,
    rationale_sha256,
    record_report,
)

# ---------------------------------------------------------------------------
# 1. The live gate
# ---------------------------------------------------------------------------


def test_every_record_governing_an_unread_field_is_affirmed_and_current():
    report = record_report(pin.load_manifest(), load_rules(), AFFIRMATIONS)
    assert report.ok, report.render()
    assert "complete and current" in report.render()


def test_affirmations_are_ordered_by_rule_id():
    """One canonical place per record, findable by the id a finding names."""
    ids = [entry.rule_id for entry in AFFIRMATIONS]
    assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# 2. The guard, on synthetic data
# ---------------------------------------------------------------------------

_LOCAL = "tests.census.test_record_affirmations"


class Widget(BaseModel):
    seen: str
    ignored: str
    also_ignored: str


_WIDGET = f"{_LOCAL}.Widget"


def _manifest() -> dict:
    return {
        "version": "0.0.0",
        "contract_models_version": "0.0.0",
        "roots": [_WIDGET],
        "claims": {_WIDGET: {"seen": ["src.a:1"]}},
        "opaque": {},
        "kit_reads": {},
        "transport": [],
    }


def _rule(**overrides) -> dict:
    rule = {
        "id": "RULE-TEST-001",
        "status": "active",
        "targets": ["Widget"],
        "fields": ["ignored"],
        "rationale": "the reading a reader judged",
    }
    rule.update(overrides)
    return rule


_REF = f"{_WIDGET}.ignored"


def _affirmation(**overrides) -> RecordAffirmation:
    values = {
        "rule_id": "RULE-TEST-001",
        "refs": (_REF,),
        "rationale_sha256": rationale_sha256("the reading a reader judged"),
    }
    values.update(overrides)
    return RecordAffirmation(**values)


def test_governed_unread_intersects_targets_and_field_heads():
    governed = governed_unread(
        _manifest(),
        (_rule(fields=["ignored[].x", "seen", "missing"]),),
    )
    # `seen` is claimed and `missing` is not a field; only the head of the
    # expression over the unread field survives.
    assert governed == {"RULE-TEST-001": (_REF,)}


def test_a_record_governing_only_read_fields_is_not_governed():
    assert governed_unread(_manifest(), (_rule(fields=["seen"]),)) == {}


def test_an_inactive_record_is_not_governed():
    assert governed_unread(_manifest(), (_rule(status="retired"),)) == {}


def test_a_complete_affirmation_set_is_ok():
    report = record_report(_manifest(), (_rule(),), (_affirmation(),))
    assert report.ok, report.render()


def test_a_governing_record_without_affirmation_is_a_finding():
    report = record_report(_manifest(), (_rule(),), ())
    assert report.unaffirmed == (("RULE-TEST-001", (_REF,)),)
    assert not report.ok
    assert "no affirmation" in report.render()


def test_moved_refs_are_a_finding():
    stale = _affirmation(refs=(f"{_WIDGET}.also_ignored", _REF))
    report = record_report(_manifest(), (_rule(),), (stale,))
    assert report.stale_refs == (("RULE-TEST-001", stale.refs, (_REF,)),)
    assert not report.ok


def test_an_edited_rationale_is_a_finding():
    report = record_report(
        _manifest(), (_rule(rationale="reworded since"),), (_affirmation(),)
    )
    assert report.stale_rationale == ("RULE-TEST-001",)
    assert not report.ok


def test_an_affirmation_of_a_non_governing_record_is_orphaned():
    for rules in ((), (_rule(status="retired"),), (_rule(fields=["seen"]),)):
        report = record_report(_manifest(), rules, (_affirmation(),))
        assert report.orphaned == ("RULE-TEST-001",)
        assert not report.ok


def test_duplicate_affirmations_are_a_finding():
    report = record_report(
        _manifest(), (_rule(),), (_affirmation(), _affirmation())
    )
    assert report.duplicates == ("RULE-TEST-001",)
    assert not report.ok


def test_an_affirmation_refuses_empty_or_unsorted_refs():
    with pytest.raises(ValueError):
        _affirmation(refs=())
    with pytest.raises(ValueError):
        _affirmation(refs=(f"{_WIDGET}.z", f"{_WIDGET}.a"))
    with pytest.raises(ValueError):
        _affirmation(refs=(_REF, _REF))
