"""The generated-block grammar, stated once for every generator that reads it.

A generated block is a markdown span between paired markers, whose body is
re-rendered from the contract rather than authored:

    <!-- BEGIN GENERATED: <block-id> -->
    …rendered body…
    <!-- END GENERATED: <block-id> -->

Two generators write these — one over the connector plugin, one over the
pipeline plugin — and each carried its own copy of the pattern and its own
substitution. The copies drifted: a rendering defect fixed in one went on
shipping from the other, and the trees being disjoint is what kept it quiet.
The pattern and the rewrite live here; each generator supplies only its own
renderer table.
"""
from __future__ import annotations

import re
from typing import Callable, Mapping

#: `indent` is whatever a marker is written after — spaces when the block sits
#: inside a list item, nothing at top level. It is captured so END can be
#: re-emitted at the SAME indent as its BEGIN: under CommonMark an HTML comment
#: at column 0 after a blank line ends the enclosing list, so a block opened at
#: column 3 and closed at column 0 closes the list item too, and everything
#: after it in that item renders as a top-level paragraph.
#:
#: Both indents are captured, and each marker is re-emitted at an indent of
#: its own: BEGIN keeps what it had, and END takes BEGIN's — including when
#: BEGIN's is column 0, which is how an END that drifted out to an indent of
#: its own is pulled back. A rendered body always ends on a non-blank line, so
#: a drifted END is not read as a block-level HTML comment any more: it is
#: swallowed by the paragraph above it as inline HTML, and the marker pair
#: stops delimiting a block at container level. Normalising to BEGIN is what
#: heals that, and healing is only possible because the render is idempotent —
#: a marker pair the renderer leaves where it drifted stays drifted, and every
#: gate stays green over it.
#:
#: END keeps its own only where BEGIN has none TO give — a block opened after
#: prose on the same line (a table cell, a sentence). Re-emitting END at
#: nothing there would move it to column 0 and close the list anyway, which is
#: the defect this exists to prevent, arrived at from the other side.
#:
#: Those two cases are told apart by whether the group PARTICIPATED, not by
#: whether it is empty: `(?:^…)?` participates exactly at a line start, so
#: `None` means "not at a line start" and `""` means "at one, with no
#: whitespace". Written as an alternation with an empty branch, both read as
#: `""` and every column-0 block took the fallback — freezing a drifted END
#: instead of healing it, idempotently, with every gate green.
#:
#: Both captures are line-anchored, so each is the marker's OWN indent and
#: never whatever whitespace happens to precede it. Without the anchor the
#: space separating a marker from prose written before it reads as indent and
#: is inserted in front of the other marker, editing a line nothing asked to
#: have edited.
#:
#: The id admits `:` because one generator namespaces its blocks (`claim:<id>`)
#: and the other does not; one pattern that admits both is what lets there be
#: one pattern.
BLOCK_RE = re.compile(
    r"(?:^(?P<indent>[^\S\n]*))?"
    r"(?P<begin><!-- BEGIN GENERATED: (?P<id>[a-z0-9][a-z0-9:-]*) -->\n)"
    r"(?P<body>.*?)"
    r"(?:^(?P<indent_end>[^\S\n]*))?"
    r"(?P<end><!-- END GENERATED: (?P=id) -->)",
    re.DOTALL | re.MULTILINE,
)


class UnknownBlock(KeyError):
    """A doc references a block id no renderer produces — fail loud, never skip."""


def render_text(text: str, source: str,
                renderers: Mapping[str, Callable[[], str]]) -> str:
    """`text` with every generated block re-rendered from `renderers`.

    A body is rendered without its markers and ends in exactly one newline.
    Each marker is re-emitted at an indent: BEGIN keeps its own, and END takes
    BEGIN's so the pair cannot straddle a list boundary — column 0 included,
    which is what pulls a drifted END back. END keeps its own only where BEGIN
    starts no line of its own and so has none to give, since dropping it there
    would close the list this exists to keep open.
    """
    def _sub(match: re.Match) -> str:
        block_id = match.group("id")
        try:
            renderer = renderers[block_id]
        except KeyError:
            raise UnknownBlock(
                f"{source}: no renderer for generated block {block_id!r}; "
                f"known blocks: {', '.join(sorted(renderers))}"
            ) from None
        indent = match.group("indent")
        end_indent = (match.group("indent_end") or ""
                      if indent is None else indent)
        return ((indent or "") + match.group("begin") + renderer()
                + end_indent + match.group("end"))

    return BLOCK_RE.sub(_sub, text)
