"""The generated-block grammar, graded once for both generators that read it.

Two generators rewrite these blocks — one over each plugin tree — and each
carried its own copy of the pattern and its own substitution. The copies
drifted: the marker pair keeping one indent was fixed in the connector
generator, and the pipeline one went on emitting END at column 0. The trees are
disjoint, so nothing failed; the defect simply waited for the first indented
marker under the other plugin.

`scripts/_generated_blocks.py` is the one copy now, and this is where it is
graded. The generators' own suites compare each tree against its own renderer,
so neither would notice the grammar losing a property both depend on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _generated_blocks import BLOCK_RE, UnknownBlock, render_text  # noqa: E402

RENDERERS = {"demo": lambda: "rendered\n"}


@pytest.mark.parametrize("indent", ["", "   ", "\t"])
def test_the_end_marker_keeps_its_begin_marker_indent(indent):
    """An HTML comment at column 0 after a blank line ends the enclosing list
    under CommonMark. A block opened inside a list item and closed at column 0
    therefore closes the ITEM, and everything after it in that item renders as
    a top-level paragraph — which is what shipped in a plugin README.

    Graded at the grammar rather than at either tree: a document with an
    indented marker exists under one plugin today and under neither tomorrow,
    and the property is what both generators depend on.
    """
    text = (f"{indent}<!-- BEGIN GENERATED: demo -->\n"
            f"{indent}stale\n"
            f"<!-- END GENERATED: demo -->\n")

    out = render_text(text, "<test>", RENDERERS)

    assert out == (f"{indent}<!-- BEGIN GENERATED: demo -->\n"
                   "rendered\n"
                   f"{indent}<!-- END GENERATED: demo -->\n"), repr(out)


def test_a_marker_with_text_before_it_is_left_where_it_was():
    """`indent` is horizontal whitespace only, so a marker inside a table cell
    or after prose captures none and is rewritten exactly as it was. Without
    this the grammar would move markers it does not own."""
    text = ("prose <!-- BEGIN GENERATED: demo -->\n"
            "stale\n"
            "<!-- END GENERATED: demo -->\n")

    assert render_text(text, "<test>", RENDERERS) == (
        "prose <!-- BEGIN GENERATED: demo -->\nrendered\n"
        "<!-- END GENERATED: demo -->\n")


def test_rendering_is_idempotent_at_every_indent():
    """The second render reads back what the first wrote. An END marker
    emitted at an indent the pattern cannot then match would grow the block
    on every run, or drop out of rendering entirely."""
    text = "   <!-- BEGIN GENERATED: demo -->\n   stale\n<!-- END GENERATED: demo -->\n"
    once = render_text(text, "<test>", RENDERERS)
    assert render_text(once, "<test>", RENDERERS) == once
    assert len(BLOCK_RE.findall(once)) == 1, once


def test_a_namespaced_block_id_still_parses():
    """One generator namespaces its ids (`claim:<id>`) and the other does not.
    One pattern admitting both is what lets there be one pattern — narrowing it
    to the unnamespaced form would silently stop matching every claim block,
    and a block that does not match is never re-rendered."""
    text = ("<!-- BEGIN GENERATED: claim:some-id -->\nstale\n"
            "<!-- END GENERATED: claim:some-id -->\n")

    assert render_text(text, "<test>", {"claim:some-id": lambda: "rendered\n"}) == (
        "<!-- BEGIN GENERATED: claim:some-id -->\nrendered\n"
        "<!-- END GENERATED: claim:some-id -->\n")


def test_an_unknown_block_id_raises_rather_than_skipping():
    """A block nothing renders is a stale body shipping as though it were
    generated — the failure the markers exist to prevent."""
    with pytest.raises(UnknownBlock, match="no-such-block"):
        render_text("<!-- BEGIN GENERATED: no-such-block -->\nstale\n"
                    "<!-- END GENERATED: no-such-block -->\n", "<test>", RENDERERS)


def test_both_generators_read_the_one_grammar():
    """The property this file exists for: neither generator may re-grow its
    own copy. Each imports the shared pattern, so a fix lands in both."""
    import gen_pipeline_docs
    import render_validator_claims

    assert gen_pipeline_docs.BLOCK_RE is BLOCK_RE
    assert render_validator_claims.BLOCK_RE is BLOCK_RE
