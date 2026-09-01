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
    manifest = pin.load_manifest()
    rules = load_rules()
    # The non-vacuity floor (`.claude/rules/guards.md`): a clean diff proves
    # nothing if the extractor located nothing to diff. Should the unread set
    # ever legitimately empty, this assertion and the affirmations registry
    # empty together, under a reader's change — not silently.
    assert governed_unread(manifest, rules), (
        "the record census located no governing records — extractor "
        "stopped measuring, not a clean census"
    )
    report = record_report(manifest, rules, AFFIRMATIONS)
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


class Base(BaseModel):
    ignored: str


class Widget(Base):
    seen: str
    also_ignored: str


class Cousin(Base):
    pass


_WIDGET = f"{_LOCAL}.Widget"


_COUSIN = f"{_LOCAL}.Cousin"


def _manifest() -> dict:
    return {
        "version": "0.0.0",
        "contract_models_version": "0.0.0",
        "roots": [_WIDGET, _COUSIN],
        "claims": {
            _WIDGET: {"seen": ["src.a:1"]},
            _COUSIN: {"ignored": ["src.a:2"]},
        },
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
        (_rule(fields=["ignored[].x", "seen"]),),
    )
    # `seen` is claimed; only the head of the expression over the unread
    # field survives.
    assert governed == {"RULE-TEST-001": (_REF,)}


def test_a_field_no_matched_model_declares_is_a_finding_not_a_silence():
    report = record_report(
        _manifest(), (_rule(fields=["ignored", "missing"]),), (_affirmation(),)
    )
    assert report.unresolved_fields == (("RULE-TEST-001", "missing"),)
    assert not report.ok
    assert "could not resolve" in report.render()


def test_an_empty_or_all_retired_registry_refuses_to_run(monkeypatch, tmp_path):
    import json
    import analitiq.contracts.shared.rule_record as rule_record

    for rules in ([], [_rule(status="retired")]):
        path = tmp_path / "rules.json"
        path.write_text(json.dumps({"rules": rules}))
        monkeypatch.setattr(rule_record, "RULES_PATH", path)
        with pytest.raises(ValueError, match="no binding records"):
            load_rules()


def test_empty_coverage_with_no_affirmations_refuses_to_run():
    """The script gate holds the same floor the live pytest gate asserts:
    located nothing and holding nothing to diff is a refusal, never a clean
    census. Located nothing while affirmations exist reports as orphans."""
    with pytest.raises(ValueError, match="no governing records"):
        record_report(_manifest(), (_rule(fields=["seen"]),), ())


def test_a_base_class_target_binds_through_the_mro_of_each_carrier():
    """`targets` binds through the MRO the way the registry defines it: a
    rule on the shared base governs each reachable subclass, and only the
    carriers on which the field is unread contribute refs — `Cousin`
    inherits the same field, claimed there."""
    governed = governed_unread(_manifest(), (_rule(targets=["Base"]),))
    assert governed == {"RULE-TEST-001": (_REF,)}


def test_a_target_no_reachable_mro_carries_is_coverage_not_a_finding():
    rules = (_rule(id="RULE-TEST-000", targets=["Nowhere"]), _rule())
    report = record_report(_manifest(), rules, (_affirmation(),))
    assert report.ok, report.render()
    assert governed_unread(_manifest(), rules) == {"RULE-TEST-001": (_REF,)}


def test_a_record_governing_only_read_fields_is_not_governed():
    assert governed_unread(_manifest(), (_rule(fields=["seen"]),)) == {}


def test_a_deprecated_record_still_binds_and_is_governed():
    """A deprecated rule still binds while authors are moved off it, per the
    lifecycle the registry schema defines, so its rationale is still graded."""
    governed = governed_unread(_manifest(), (_rule(status="deprecated"),))
    assert governed == {"RULE-TEST-001": (_REF,)}


def test_a_non_binding_record_is_not_governed():
    for status in ("draft", "retired"):
        assert governed_unread(_manifest(), (_rule(status=status),)) == {}


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
    for rules in (
        (_rule(status="retired"),),
        (_rule(fields=["seen"]),),
    ):
        report = record_report(_manifest(), rules, (_affirmation(),))
        assert report.orphaned == ("RULE-TEST-001",)
        assert not report.ok


def test_duplicate_affirmations_are_a_finding():
    report = record_report(
        _manifest(), (_rule(),), (_affirmation(), _affirmation())
    )
    assert report.duplicates == ("RULE-TEST-001",)
    assert not report.ok


def test_a_record_naming_no_fields_is_listed_but_never_gates():
    """The fields-naming boundary made visible: a record over a carrier
    with unread fields that names no `fields:` is reported for the reviewer
    and gates nothing."""
    probe = _rule(id="RULE-TEST-000", fields=[])
    report = record_report(_manifest(), (probe, _rule()), (_affirmation(),))
    assert report.unlocated == ("RULE-TEST-000",)
    assert report.ok, report.render()
    assert "not gating" in report.render()
    # and with no unread field on the carrier, it is not even listed
    quiet = record_report(
        _manifest(),
        (_rule(id="RULE-TEST-000", targets=["Cousin"], fields=[]), _rule()),
        (_affirmation(),),
    )
    assert quiet.unlocated == ()


def test_an_affirmation_refuses_empty_or_unsorted_refs():
    with pytest.raises(ValueError):
        _affirmation(refs=())
    with pytest.raises(ValueError):
        _affirmation(refs=(f"{_WIDGET}.z", f"{_WIDGET}.a"))
    with pytest.raises(ValueError):
        _affirmation(refs=(_REF, _REF))
