"""`finding()` and `_passed()` — the shape and verdict every check shares.

`rules/SCHEMA.md`'s Findings section is the contract these pin: a `fail`
finding's severity is derived from the rule it names, never accepted as a
literal; a `notApplicable`/`informational` finding never carries one; and the
overall verdict fails closed on an unchecked `error`-tier rule rather than
reading silence as a pass.
"""
import pytest

from analitiq.contracts.shared.rules import all_rules
from analitiq.validator._core import _passed, finding


def _rule(severity: str, *, bound: bool = True):
    """Any rule of the given severity, bound to a real validator when asked."""
    return next(
        r for r in all_rules()
        if r.severity == severity and (bool(r.validator) if bound else True)
    )


def test_fail_finding_derives_severity_from_its_rule():
    error_rule = _rule("error")
    warning_rule = _rule("warning")
    assert finding(
        "document", rule=error_rule.id, message_id="m", kind="fail",
        path="/", message="x")["severity"] == "error"
    assert finding(
        "document", rule=warning_rule.id, message_id="m", kind="fail",
        path="/", message="x")["severity"] == "warning"


def test_fail_finding_refuses_an_info_tier_rule():
    """An info-tier violation is kind: informational, never kind: fail — a
    fail finding may only report error or warning. Guards the invariant
    validator-verdict-stability.md states as fact: a finding never carries
    severity: info."""
    info_rule = _rule("info", bound=False)
    with pytest.raises(ValueError, match="informational"):
        finding(
            "document", rule=info_rule.id, message_id="m", kind="fail",
            path="/", message="x")


def test_fail_finding_with_no_rule_costs_error():
    # The two framework cases (rules/SCHEMA.md's case table): a rejection or
    # an unrecognized document, with no record to derive a lesser cost from.
    result = finding("document", message_id="m", kind="fail", path="/", message="x")
    assert result["severity"] == "error"
    assert "rule" not in result


def test_not_applicable_and_informational_never_carry_severity():
    rule = _rule("error")
    for kind in ("notApplicable", "informational"):
        result = finding(
            "document", rule=rule.id, message_id="m", kind=kind, path="/", message="x")
        assert "severity" not in result


def test_finding_rejects_an_unknown_validator_kind_or_rule():
    with pytest.raises(ValueError, match="validator"):
        finding("not-a-validator-id", message_id="m", kind="fail", path="/", message="x")
    with pytest.raises(ValueError, match="kind"):
        finding("document", message_id="m", kind="not-a-kind", path="/", message="x")
    with pytest.raises(ValueError, match="rule"):
        finding(
            "document", rule="RULE-NOT-A-REAL-ID", message_id="m", kind="fail",
            path="/", message="x")


def test_passed_fails_on_a_fail_finding_at_error_severity():
    rule = _rule("error")
    findings = [finding(
        "document", rule=rule.id, message_id="m", kind="fail", path="/", message="x")]
    assert not _passed(findings)


def test_passed_holds_on_a_fail_finding_at_warning_severity():
    rule = _rule("warning")
    findings = [finding(
        "document", rule=rule.id, message_id="m", kind="fail", path="/", message="x")]
    assert _passed(findings)


def test_passed_fails_closed_on_an_unchecked_error_tier_rule():
    """The amended verdict this stack introduces: an error-tier rule that a
    check could not evaluate is not a rule that held."""
    rule = _rule("error")
    findings = [finding(
        "document", rule=rule.id, message_id="m", kind="notApplicable",
        path="/", message="x")]
    assert not _passed(findings)


def test_passed_holds_on_an_unchecked_warning_tier_rule():
    rule = _rule("warning")
    findings = [finding(
        "document", rule=rule.id, message_id="m", kind="notApplicable",
        path="/", message="x")]
    assert _passed(findings)


def test_passed_fails_closed_on_a_notapplicable_naming_no_rule():
    """Even blinder than a known-rule miss: nothing here says what went
    unchecked, so it can never clear the bar."""
    findings = [finding(
        "document", message_id="m", kind="notApplicable", path="/", message="x")]
    assert not _passed(findings)


def test_passed_ignores_informational_findings():
    rule = _rule("error")
    findings = [finding(
        "document", rule=rule.id, message_id="m", kind="informational",
        path="/", message="x")]
    assert _passed(findings)
