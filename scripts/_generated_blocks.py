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
#: The capture is line-anchored, so it is the marker's OWN indent and never
#: whatever whitespace happens to precede it: a marker written after prose on
#: the same line (a table cell, a sentence) captures nothing and is rewritten
#: exactly as it was. Without the anchor the space separating it from that
#: prose reads as indent and is inserted before END, editing a line nothing
#: asked to have edited.
#:
#: The id admits `:` because one generator namespaces its blocks (`claim:<id>`)
#: and the other does not; one pattern that admits both is what lets there be
#: one pattern.
BLOCK_RE = re.compile(
    r"(?P<indent>^[^\S\n]*|)"
    r"(?P<begin><!-- BEGIN GENERATED: (?P<id>[a-z0-9][a-z0-9:-]*) -->\n)"
    r"(?P<body>.*?)"
    r"[^\S\n]*(?P<end><!-- END GENERATED: (?P=id) -->)",
    re.DOTALL | re.MULTILINE,
)


class UnknownBlock(KeyError):
    """A doc references a block id no renderer produces — fail loud, never skip."""


def render_text(text: str, source: str,
                renderers: Mapping[str, Callable[[], str]]) -> str:
    """`text` with every generated block re-rendered from `renderers`.

    A body is rendered without its markers and ends in exactly one newline;
    the indent its BEGIN was written at is put back in front of its END.
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
        return (match.group("indent") + match.group("begin") + renderer()
                + match.group("indent") + match.group("end"))

    return BLOCK_RE.sub(_sub, text)
