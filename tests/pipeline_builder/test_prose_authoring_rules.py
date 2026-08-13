"""Pin the authoring rules that are the only path from a user's words to a shape.

`test_prose_vocabulary.py` polices closed vocabularies and generated-block
presence. It is blind to a *routing rule* — a mapper row, or a hard rule in an
agent's prose — because those are single-member statements, not full-set
restatements. Deleting the `truncate_insert` row from `WriteModeMapper` or the
`kind`-discriminator rule from `stream-creator.md` left the whole suite green —
measured, by deleting each and running it. The contract tests keep passing
because the contract is unchanged. What breaks is the plugin: the mode becomes
unreachable, or the agent authors documents every validator rejects.

Per the repo's own rule, agent prose is behaviour. These are the behaviours
`rc19` added or changed, so they get tests.

**What a prose gate can and cannot do.** Each test keys off a fact read from the
contract models. Where the prose half can be checked by a TOKEN the contract
owns — `` `kind` ``, a variant tag, a mode name — the test asserts the document
still carries it, which catches *deletion*, the measured failure mode, and
catches the prose falling behind the contract because the expected set is
derived rather than typed. It does not catch *corruption*: rewording the
guidance around a token, or inverting the advice while keeping it, passes.
Anchoring harder (a hash of the section) would fire on every unrelated wording
edit and train people to re-baseline without reading, which is the trade-off
`test_prose_vocabulary.py`'s docstring already argues at length. Same call here.

Where the prose half needs a SENTENCE read — does this paragraph still forbid
the dotted path, does it still instruct the agent to ask first — the test
asserts the contract fact alone and stops. Those judgments are
`.claude/rules/plugin-prose.md`'s, per `.claude/rules/guards.md`, and
the tests that reach only that far say so in their own docstrings.
"""
from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "plugins" / "analitiq-pipeline-builder"

pytest.importorskip("analitiq.contracts",
                    reason="requires: pip install -r requirements-dev.txt")

from analitiq.contracts.stream import (  # noqa: E402
    _DB_WRITE_MODES,
    ConstantAssignmentValue,
    ExpressionAssignmentValue,
    GetExpression,
)

ENUM_MAPPERS = ROOT / "skills" / "pipeline-builder" / "references" / "enum-mappers.md"
STREAM_CREATOR = ROOT / "agents" / "stream-creator.md"


def _section(doc: Path, heading: str) -> str:
    """Return the text under `## <heading>`, up to the next column-0 heading."""
    text = doc.read_text()
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^#|\Z)", text, re.M | re.S
    )
    assert match, f"{doc.name}: no `## {heading}` section — the doc was restructured"
    return match.group(1)


# ---------------------------------------------------------------------------
# WriteModeMapper — the only route from a user's phrasing to `write.mode`
# ---------------------------------------------------------------------------


def test_write_mode_mapper_routes_to_every_database_write_mode():
    """Every SQL write mode is the target of some `| database |` mapper row.

    A mode the contract implements but the mapper never names cannot be
    authored: the user says "re-sync", the orchestrator has no row to match, and
    the feature is dead while every contract test stays green. Derived from
    `_DB_WRITE_MODES`, so adding a mode to the contract fails here until the
    mapper learns a phrasing for it.

    What this measures is the target column, not the quality of the phrasings
    that reach it — rerouting a row's user-input cell to nonsense keeps this
    green. Reachability in the stronger sense is not testable from prose.
    """
    section = _section(ENUM_MAPPERS, "WriteModeMapper")
    routed = {
        mode
        for line in section.splitlines()
        if line.startswith("| database")
        for mode in _DB_WRITE_MODES
        if re.search(rf"`{mode}`", line.split("→", 1)[-1])
    }
    assert routed == set(_DB_WRITE_MODES), (
        "WriteModeMapper drift — add a `| database | …` row routing user "
        "phrasing to each mode, or drop the mode from the contract. "
        f"unrouted={sorted(set(_DB_WRITE_MODES) - routed)}"
    )


def test_the_destructive_mode_this_section_must_guard_is_still_routed_here():
    """`truncate_insert` is still a mode the mapper section has to cover.

    The ask-first guardrail beside it is load-bearing: `truncate_insert` empties
    the destination, and an agent can route an append-only event stream there —
    cursorless and keyless looks exactly like a full-refresh source — wiping
    accumulated history on every run. Nothing else in the plugin stops that; the
    contract accepts the document and the engine truncates as asked.

    Whether the prose still CARRIES that instruction is not asserted here. Doing
    so meant deciding, from `**…ask…**` in a paragraph, that a sentence
    instructs the reader — a judgment about English, which
    `.claude/rules/guards.md` forbids in a test and
    `.claude/rules/plugin-prose.md` lists among the things a reader must check.
    "Is destructive" is not a property the contract models express, so there is
    nothing to derive the guardrail from either.

    What is decidable is that the mode still exists and still belongs to this
    section: if `truncate_insert` leaves the contract, the guardrail is moot and
    the prose about it should go in the same change.
    """
    assert "truncate_insert" in _DB_WRITE_MODES, (
        "`truncate_insert` is no longer a database write mode — the ask-first "
        "guardrail in enum-mappers.md is now about nothing and should be "
        "removed with this test."
    )
    section = _section(ENUM_MAPPERS, "WriteModeMapper")
    assert "truncate_insert" in section, (
        "enum-mappers.md's WriteModeMapper section no longer names "
        "`truncate_insert`, so nothing routes the destructive mode and nothing "
        "warns about it. Restore the row and its ask-first instruction."
    )


# ---------------------------------------------------------------------------
# stream-creator.md — the breaking half of rc19
# ---------------------------------------------------------------------------


def test_stream_creator_teaches_the_assignment_value_discriminator():
    """`kind` and both variant tags must survive somewhere in the agent's prose.

    This is a breaking contract change: an agent that loses the rule authors the
    old two-optional-fields shape, which every rc19 validator rejects. The tags
    are read off the discriminated union, so renaming a variant in the contract
    fails here rather than silently invalidating the prose.

    The property is "the file still teaches this", not "this particular
    sentence survives" — the rule is stated twice (Process step 6 and Hard
    rules) and deleting either copy alone leaves the agent correctly taught.
    """
    text = STREAM_CREATOR.read_text()
    tags = {
        typing.get_args(variant.model_fields["kind"].annotation)[0]
        for variant in (ExpressionAssignmentValue, ConstantAssignmentValue)
    }
    assert "`kind`" in text, (
        "stream-creator.md no longer names the `kind` discriminator — the agent "
        "will author the pre-rc19 shape, which the contract rejects."
    )
    missing = {tag for tag in tags if f'`"{tag}"`' not in text}
    assert not missing, (
        f"stream-creator.md does not teach the assignment-value variant(s) "
        f"{sorted(missing)} — an agent cannot author what it is not told exists."
    )


def test_the_token_array_path_rule_is_still_a_contract_fact():
    """`get` paths are arrays of segments, so the prose rule is worth teaching.

    Only the contract half is asserted here. Whether `stream-creator.md` still
    teaches it is a judgment about what a document says, and the two ways this
    test used to make that judgment both failed in both directions:

    - `"dotted string" in text` pins ENGLISH. Rewriting the rule more clearly
      ("a path is a list of segments, never `a.b`") reddens the build on prose
      that improved, and the remedy is to reword it back. It also cannot read
      polarity — a document saying a dotted string is FINE satisfies it.
    - The bullet-scoped worked-example regex pinned markdown formatting on top
      of that: which lines are bullets, and where the backticks fall.

    Both are the class `plugin-prose.md` now covers, and a reader applying it
    catches the rewording a substring match cannot. What survives here is the
    part a mechanism can decide: if `path` ever goes back to a scalar this
    fails, and the prose rule is then what should be deleted.
    """
    assert typing.get_origin(GetExpression.model_fields["path"].annotation) is list, (
        "GetExpression.path is no longer a list — the token-array rule in "
        "stream-creator.md is now wrong and should be removed with this test."
    )
