"""A rule statement that carves out the negating positions names all of them.

The rules bound to the functions applying the exemption each name those
positions rather than only the class — because naming only the class left an
author with a sample under one of them concluding, correctly, that the
exemption did not reach it.

That enumeration is a hand copy of `JSON_SCHEMA_NEGATED_SCHEMA_KEYS`, and it
ships into the wheel as `shared/rules.json` and into the plugin as
`references/rules/api-endpoint.md`. A member landing on that frozenset leaves
every such statement teaching an exemption narrower than the one enforced, and
no renderer reads statement text.

This locates rather than decides (`.claude/rules/guards.md`): it asks whether
each member the contract owns appears backticked in the statement, and whether
any single-schema keyword the contract does NOT own appears there — both
directions, because a member leaving the set is as silent as one landing on it.
Whether the sentence around the names still reads correctly is the author's
half, and that half is not small: the reason attached to the enumeration has to
hold for every member, and it did not when it was first written.
"""
from __future__ import annotations

import pytest

#: The functions that APPLY the exemption — the two that gate on
#: `JSON_SCHEMA_NEGATED_SCHEMA_KEYS`. A rule bound to one of them exempts
#: negating positions, so its statement owes the enumeration.
#:
#: Selected this way and not by a phrase in the statement: a phrase decides
#: which sentence gets graded, so rewording one drops it out of grading with a
#: green build, and the failure would ask the author to put the wording back.
#: `.claude/rules/guards.md` denies that shape the anchor exemption by name.
_CARVE_OUT_ENFORCERS = (
    "analitiq.contracts.endpoints::_validate_examples_zone",
    "analitiq.validator.connectors::_sample_findings",
)


def _carve_out_rules():
    from analitiq.contracts.shared.rules import all_rules

    found = sorted(r.id for r in all_rules()
                   if r.validator in _CARVE_OUT_ENFORCERS)
    assert found, (
        f"no rule binds any of {_CARVE_OUT_ENFORCERS} — either they were "
        "renamed, in which case this guard is reading nothing and reporting "
        "agreement, or the exemption is gone and this file goes with it"
    )
    return found


@pytest.mark.parametrize("rule_id", _carve_out_rules())
def test_the_statement_names_every_negating_position(rule_id):
    from analitiq.contracts.endpoints import JSON_SCHEMA_NEGATED_SCHEMA_KEYS
    from analitiq.contracts.shared.rules import all_rules

    assert JSON_SCHEMA_NEGATED_SCHEMA_KEYS, (
        "the contract owns no negating positions — both comprehensions below "
        "would be empty and this would report agreement while the statements "
        "go on teaching an exemption nothing applies"
    )

    by_id = {r.id: r for r in all_rules()}
    assert rule_id in by_id, (
        f"{rule_id} is not in the compiled registry — this guard would "
        "otherwise report agreement over a rule that no longer exists"
    )
    statement = by_id[rule_id].statement
    missing = sorted(
        key for key in JSON_SCHEMA_NEGATED_SCHEMA_KEYS
        if f"`{key}`" not in statement
    )
    assert not missing, (
        f"{rule_id}'s statement carves out the negating positions and does not "
        f"name {missing} — the contract owns that set, and this statement is "
        "read by an author deciding whether the exemption reaches their node. "
        "It renders into the wheel and into the plugin reference."
    )

    # The other direction. A member LEAVING the set leaves both statements
    # teaching an exemption broader than the one enforced — an author reads
    # that their sample under that position is exempt, and it is graded.
    from analitiq.contracts.endpoints import JSON_SCHEMA_SINGLE_SCHEMA_KEYS

    overreach = sorted(
        key for key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS
        - JSON_SCHEMA_NEGATED_SCHEMA_KEYS
        if f"`{key}`" in statement
    )
    assert not overreach, (
        f"{rule_id}'s statement names {overreach} among the exempt positions, "
        "and the contract does not — so it teaches an exemption the validator "
        "does not apply, and the sample is graded after all"
    )
