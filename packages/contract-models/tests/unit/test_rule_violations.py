"""Which rules a rejection names, read off the raised objects rather than the text.

Pydantic renders every raised `ValueError` into one message string, so an id
inside that string proves only that some sentence mentioned it: a statement,
a detail or a sibling rule's complaint can cite an id no enforcer raised.
`rule_violations` unpacks the `RuleViolation` objects pydantic keeps at
`ctx.error`, and `violated_rule_ids` reduces a whole `ValidationError` to them.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError, model_validator

from analitiq.contracts.shared.rules import (
    MultiRuleViolation,
    rule_violations,
    unattributed_violation,
    violated_rule_ids,
    violation,
)


class _RaisesOne(BaseModel):
    @model_validator(mode="after")
    def _one(self):
        # The detail cites a second id, which the text match counted as raised.
        raise violation("RULE-HTTP-001", "probe", "detail citing RULE-HTTP-002")


class _RaisesMany(BaseModel):
    @model_validator(mode="after")
    def _many(self):
        raise MultiRuleViolation([
            violation("RULE-HTTP-002", "probe", "first"),
            unattributed_violation("second"),
            violation("RULE-HTTP-003", "probe", "third"),
        ])


class _FieldConstraint(BaseModel):
    count: int


def _rejection(model: type[BaseModel], payload: dict) -> ValidationError:
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    return exc.value


def test_a_single_violation_names_only_its_own_rule():
    exc = _rejection(_RaisesOne, {})
    [err] = exc.errors()
    assert [v.rule_id for v in rule_violations(err)] == ["RULE-HTTP-001"]
    assert violated_rule_ids(exc) == frozenset({"RULE-HTTP-001"})


def test_a_multi_violation_unpacks_every_entry_in_order():
    exc = _rejection(_RaisesMany, {})
    [err] = exc.errors()
    assert [v.rule_id for v in rule_violations(err)] == ["RULE-HTTP-002", None, "RULE-HTTP-003"]
    assert violated_rule_ids(exc) == frozenset({"RULE-HTTP-002", "RULE-HTTP-003"})


def test_a_field_constraint_carries_no_violation():
    exc = _rejection(_FieldConstraint, {"count": "not a number"})
    [err] = exc.errors()
    assert rule_violations(err) == ()
    assert violated_rule_ids(exc) == frozenset()
