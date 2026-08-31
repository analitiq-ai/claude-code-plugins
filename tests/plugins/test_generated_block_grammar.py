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

import importlib
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
    """Each capture is line-anchored, so a marker written after prose on the
    same line captures no indent and is rewritten exactly as it was. Without
    the anchor the space separating it from that prose reads as this block's
    indent, and is inserted in front of the other marker."""
    text = ("prose <!-- BEGIN GENERATED: demo -->\n"
            "stale\n"
            "<!-- END GENERATED: demo -->\n")

    assert render_text(text, "<test>", RENDERERS) == (
        "prose <!-- BEGIN GENERATED: demo -->\nrendered\n"
        "<!-- END GENERATED: demo -->\n")


def test_a_drifted_end_marker_is_pulled_back_to_its_begin():
    """Column 0 is an indent BEGIN has, not an absence of one.

    Told apart by emptiness rather than by participation, the two read alike
    and every top-level block took the "keep your own" branch — so an END that
    picked up stray whitespace by hand edit or merge kept it, idempotently,
    with every gate green.

    Four columns past its container's content column, that marker stops
    starting an HTML block, and what it becomes depends on the line above it:
    swallowed into a paragraph as inline HTML, or — after a fenced code close
    or a table — an indented code block, rendering the marker as literal text
    in a document that ships verbatim to users.
    """
    text = ("<!-- BEGIN GENERATED: demo -->\n"
            "stale\n"
            "    <!-- END GENERATED: demo -->\n")

    assert render_text(text, "<test>", RENDERERS) == (
        "<!-- BEGIN GENERATED: demo -->\nrendered\n"
        "<!-- END GENERATED: demo -->\n")


def test_an_end_marker_keeps_its_own_indent_when_begin_has_none_to_give():
    """The same CommonMark defect, arrived at from the other side.

    A block opened after prose on the same line captures no indent, so there
    is nothing to pass down — and re-emitting END at nothing moves it to
    column 0, which ends the list item exactly as the unfixed grammar did.
    Where BEGIN has no indent, END keeps the one it was written with."""
    text = ("- prose <!-- BEGIN GENERATED: demo -->\n"
            "  stale\n"
            "  <!-- END GENERATED: demo -->\n")

    assert render_text(text, "<test>", RENDERERS) == (
        "- prose <!-- BEGIN GENERATED: demo -->\nrendered\n"
        "  <!-- END GENERATED: demo -->\n")


@pytest.mark.parametrize("indent", ["", "   ", "\t"])
def test_rendering_is_idempotent_at_every_indent(indent):
    """The second render reads back what the first wrote. An END marker
    emitted at an indent the pattern cannot then match would grow the block
    on every run, or drop out of rendering entirely."""
    text = (f"{indent}<!-- BEGIN GENERATED: demo -->\n"
            f"{indent}stale\n<!-- END GENERATED: demo -->\n")
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


@pytest.mark.parametrize("module", ["gen_pipeline_docs", "render_validator_claims"])
def test_every_rendered_body_ends_on_a_non_blank_line(module):
    """The premise the indent normalisation is argued from, asserted.

    What a drifted END marker costs is decided by the line above it, which the
    reasoning in `scripts/_generated_blocks.py` takes for granted is not
    blank. It happens to be true of every renderer in both tables — and
    nothing said so, which made it the kind of premise a reader checks, finds
    unstated, and treats as an assumption rather than a property.

    A renderer returning a trailing blank line would put an indented code
    block one hand edit away on every document it feeds, with every gate
    green: the generators compare their output against themselves, so a body
    that ends differently is simply the new expected output.

    Asserted over the renderers' OUTPUT SHAPE, never over what they say — the
    text is the contract's to write and this has no opinion about it.
    """
    generator = importlib.import_module(module)
    assert generator.RENDERERS, f"{module} renders nothing — this asserts nothing"

    for block_id, renderer in sorted(generator.RENDERERS.items()):
        body = renderer()
        assert body.endswith("\n"), f"{module}:{block_id} body does not end in a newline"
        # `splitlines` on the whole body, not on `body[:-1]`: a body ending
        # "text\n\n" leaves "text" as the last line once the final newline is
        # stripped, so stripping it first hides exactly the trailing blank
        # line this exists to catch.
        assert body.splitlines()[-1].strip(), (
            f"{module}:{block_id} ends on a blank line, so its END marker "
            "follows one — four spaces of drift there is an indented code "
            "block, and the marker renders as literal text"
        )


@pytest.mark.parametrize("module", ["gen_pipeline_docs", "render_validator_claims"])
def test_both_generators_rewrite_through_the_one_grammar(module, monkeypatch):
    """The property this file exists for: neither generator may re-grow its
    own copy of the substitution.

    Driven through each generator's own `render_text` rather than compared by
    identity — re-exporting the shared pattern under the right name proves
    nothing about what the rewrite uses, and a generator that kept the
    re-export while growing a private pattern beside it would pass an
    identity check. Neither tree's own suite would catch it either: each
    compares its documents against its own renderer, so a grammar both agree
    on is a grammar neither grades.
    """
    generator = importlib.import_module(module)
    monkeypatch.setattr(generator, "RENDERERS", RENDERERS)

    out = generator.render_text(
        "   <!-- BEGIN GENERATED: demo -->\n   stale\n"
        "   <!-- END GENERATED: demo -->\n", "<test>")

    assert out == ("   <!-- BEGIN GENERATED: demo -->\nrendered\n"
                   "   <!-- END GENERATED: demo -->\n"), repr(out)
    # Beside it, not instead of it. Driving one input grades the rewrite; a
    # generator that grew a private but equivalent pattern would still pass.
    # And the re-exported name is read on its own, outside `render_text`: the
    # connector generator scans documents with it, and the pipeline plugin's
    # suite reaches it through that module. So a generator that kept
    # delegating while re-growing the name would leave those readers on a
    # second pattern.
    assert generator.BLOCK_RE is BLOCK_RE
