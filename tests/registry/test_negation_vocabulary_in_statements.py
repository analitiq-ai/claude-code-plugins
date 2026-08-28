"""Two rule statements enumerate the negating positions; the contract owns them.

RULE-ENDP-063 and RULE-ENDP-064 both carve out a node reached through a
position whose subschema does not describe the instance, and both name the
positions — `not`, `if`, `propertyNames` — because naming only the class left
an author with a sample under `if` concluding, correctly, that the exemption
did not reach them.

That enumeration is a hand copy of `JSON_SCHEMA_NEGATED_SCHEMA_KEYS`, and it
ships twice: into the wheel as `shared/rules.json`, and into the plugin as
`references/rules/api-endpoint.md`. A member landing on that frozenset leaves
both statements teaching an exemption narrower than the one enforced, and no
renderer reads statement text.

This locates rather than decides (`.claude/rules/guards.md`): it asks whether
each member the contract owns appears backticked in the statement. Whether the
sentence around it still reads correctly is the author's half.
"""
from __future__ import annotations

import pytest

CARVE_OUT_RULES = ("RULE-ENDP-063", "RULE-ENDP-064")


@pytest.mark.parametrize("rule_id", CARVE_OUT_RULES)
def test_the_statement_names_every_negating_position(rule_id):
    from analitiq.contracts.endpoints import JSON_SCHEMA_NEGATED_SCHEMA_KEYS
    from analitiq.contracts.shared.rules import all_rules

    statement = next(r.statement for r in all_rules() if r.id == rule_id)
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
