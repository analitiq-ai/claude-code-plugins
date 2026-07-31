"""Pin the authoring rules that are the only path from a user's words to a shape.

`test_prose_vocabulary.py` polices closed vocabularies and generated-block
presence. It is blind to a *routing rule* — a mapper row, or a hard rule in an
agent's prose — because those are single-member statements, not full-set
restatements. Deleting the `truncate_insert` row from `WriteModeMapper` or the
`kind`-discriminator rule from `stream-creator.md` left the whole suite green
(measured on this PR); the contract tests keep passing because the contract is
unchanged. What breaks is the plugin: the mode becomes unreachable, or the agent
authors documents every validator rejects.

Per the repo's own rule, agent prose is behaviour. These are the behaviours
`rc19` added or changed, so they get tests.

**What a prose gate can and cannot do.** Each test keys off a fact read from the
contract models and then asserts the prose still carries the corresponding rule.
That catches *deletion* — the measured failure mode — and it catches the prose
falling behind the contract, because the expected set is derived, never typed.
It does not catch *corruption*: rewording the guidance around a token, or
inverting the advice while keeping the token, passes. Anchoring harder (a hash
of the section) would fire on every unrelated wording edit and train people to
re-baseline without reading, which is the trade-off
`test_prose_vocabulary.py`'s docstring already argues at length. Same call here.
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


def _prose_paragraphs(text: str) -> list[str]:
    """Blank-line-separated blocks, with markdown table rows removed.

    Every assertion here scopes to the block that carries the rule rather than
    to the whole document. Document-wide matching is how a prose gate goes
    quietly dead: unrelated prose supplies the token and the assertion can no
    longer fail for the reason it names.

    Table rows are dropped because a mapper table is one blank-line-delimited
    block that names every mode, so it would qualify as "the paragraph about
    `truncate_insert`" for any guidance assertion — and a bolded aside in an
    unrelated row of it would stand in for the guardrail. A table states the
    routing; the guidance about that routing is always prose beside it.
    """
    blocks = []
    for block in re.split(r"\n\s*\n", text):
        prose = "\n".join(
            line for line in block.splitlines() if not line.lstrip().startswith("|")
        )
        if prose.strip():
            blocks.append(prose)
    return blocks


def _bullets(text: str) -> list[str]:
    """Split a markdown list into one entry per top-level `- ` bullet."""
    return re.split(r"(?m)^(?=- )", text)


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


def test_write_mode_mapper_requires_asking_before_a_destructive_route():
    """The ask-first guardrail on the table-emptying mode is load-bearing.

    `truncate_insert` empties the destination. Without this instruction an agent
    can route an append-only event stream there — cursorless and keyless looks
    exactly like a full-refresh source — and wipe accumulated history on every
    run. Nothing else in the plugin stops that; the contract accepts the
    document, and the engine performs the truncation as asked.

    Hand-anchored on the mode name rather than derived: "is destructive" is not a
    property the contract models express, so there is nothing to read it from.

    Scoped to a PROSE paragraph that names the mode. Two narrower spellings of
    this assertion were measurably satisfiable without the guardrail: a
    section-wide search matched the unrelated bolded "ask the user" about
    `upsert` conflict keys, and a paragraph-scoped one matched a bolded aside
    added to any row of the mapper table, which names every mode in a single
    block. `_prose_paragraphs` drops table rows for that reason.
    """
    section = _section(ENUM_MAPPERS, "WriteModeMapper")
    assert "truncate_insert" in section, "the destructive mode left this section"
    guarded = [
        block
        for block in _prose_paragraphs(section)
        if "truncate_insert" in block or "empties" in block
        if re.search(r"\*\*[^*]*\bask\b[^*]*\*\*", block, re.I)
    ]
    assert guarded, (
        "enum-mappers.md: no paragraph that names `truncate_insert` (or an "
        "emptied table) still instructs the agent, in bold, to ask the user "
        "before routing there. An agent may now silently choose a mode that "
        "wipes an append-only destination."
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


def test_stream_creator_teaches_token_array_get_paths():
    """The `get` path is an array of segments, and the prose must say so.

    The other breaking half of rc19. A dotted string was the rc18 shape and is
    the intuitive one, so the rule has to be stated explicitly — an agent
    reverting to `"address.city"` produces documents the contract rejects.
    Guarded by the contract's own annotation: if `path` ever goes back to a
    scalar, this test fails and the prose rule is what should be deleted.

    The two assertions below have different reach, which is worth stating
    because the weaker one reads like the stronger. "Not a dotted string" is
    document-wide, so either of the rule's two statements (Process step 6, Hard
    rules) satisfies it. The worked example is bullet-scoped and in practice
    only the Hard-rules copy satisfies it — step 6 writes `path` unticked and
    is not split into its own bullet. That is the harmless direction: stricter
    than described, not laxer.
    """
    assert typing.get_origin(GetExpression.model_fields["path"].annotation) is list, (
        "GetExpression.path is no longer a list — the token-array rule in "
        "stream-creator.md is now wrong and should be removed with this test."
    )
    text = STREAM_CREATOR.read_text()
    assert "dotted string" in text, (
        "stream-creator.md no longer rules out the dotted-string `path` an agent "
        "would otherwise default to."
    )
    # The worked example must sit in a bullet that is ABOUT `get`/`path`. A
    # document-wide search silently passes forever: `write.conflict_keys` is
    # illustrated as `["org_id", "external_id"]` under Hard rules, which
    # satisfies the array regex while saying nothing about source paths.
    multi_segment = re.compile(r"`\[\s*\"[^\"]+\"\s*,\s*\"[^\"]+\"\s*\]`")
    assert any(
        multi_segment.search(bullet)
        for bullet in _bullets(text)
        if "`path`" in bullet or "path:" in bullet
        if "get" in bullet
    ), (
        "no `get`/`path` rule in stream-creator.md carries a multi-segment "
        'token-array example (e.g. `["address", "city"]`) — the rule reads as '
        "a bare assertion an agent has to take on faith."
    )
