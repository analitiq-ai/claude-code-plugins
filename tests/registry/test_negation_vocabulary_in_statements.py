"""A rule statement that carves out the negating positions names all of them.

Every rule in `_CARVE_OUT_RULES` exempts a node reached through a position whose
subschema does not describe the instance, and each names those positions rather
than only the class — because naming only the class left an author with a
sample under one of them concluding, correctly, that the exemption did not
reach it.

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

#: Which rules are expected to carry the carve-out, pinned. A test's assertion
#: target, which is the one copy `.claude/rules/no-drift-surfaces.md` permits —
#: and the only thing that makes membership SHRINKING loud. Locating alone
#: cannot: a statement reworded until it names none of the positions simply
#: leaves the located set, and an assertion that the set is merely non-empty
#: stays green on the rule that is left. A rule joining or leaving this list is
#: a deliberate edit here, beside the reason.
_CARVE_OUT_RULES = ("RULE-ENDP-063", "RULE-ENDP-064")


def _carve_out_rules():
    """The rules whose statements carve out the negating positions.

    Located by a name the CONTRACT owns — a backticked member of
    `JSON_SCHEMA_NEGATED_SCHEMA_KEYS` — never by an English phrase. A phrase
    ("does not describe the instance", which is how this was written) decides
    which sentence gets graded, so rewording it silently grades nothing while
    the guard reports agreement; `.claude/rules/guards.md` names that shape.
    The contract's own vocabulary cannot be reworded without the contract
    moving, and when it does the comparison below is what fails.
    """
    from analitiq.contracts.endpoints import JSON_SCHEMA_NEGATED_SCHEMA_KEYS
    from analitiq.contracts.shared.rules import all_rules

    found = sorted(
        r.id for r in all_rules()
        if any(f"`{key}`" in (r.statement or "")
               for key in JSON_SCHEMA_NEGATED_SCHEMA_KEYS)
    )
    assert found == sorted(_CARVE_OUT_RULES), (
        f"the rules whose statements name a negating position are {found}, and "
        f"{sorted(_CARVE_OUT_RULES)} were expected.\n"
        "A rule that DROPPED OUT still renders its statement into the wheel "
        "and into the plugin reference, where an author reads it to decide "
        "whether the exemption reaches their node — and the assertions below "
        "would no longer grade it.\n"
        "A rule that JOINED may be a third carve-out, in which case add it "
        "here; or it may backtick one of these keys for an unrelated reason — "
        "`if` and `not` are ordinary JSON Schema vocabulary — in which case "
        "this locator has to separate the two roles rather than admit it, "
        "because everything below would then demand it name all of them."
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
