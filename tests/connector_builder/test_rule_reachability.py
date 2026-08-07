"""Every rule id a plugin's prose cites must be readable inside that plugin.

`test_advisory_sync.py::test_prose_rule_citations_resolve` already pins that a
cited id names a live rule. That is the author's half. This file pins the
reader's half, which is a different claim and was the one silently false:

An agent runs from the plugin cache. It has the plugin tree and nothing else —
no registry source, no PyPI, no network. When prose says "…must not be renamed
(`ADV-ENDP-043`)" and no file under that plugin lists `ADV-ENDP-043`, the id is
a dead end: the agent learns there is a rule and cannot learn what it says. That
is strictly worse than the restatement the citation replaced, because a
restatement at least carried the rule.

Both plugins render the registry into their own tree — the connector plugin
through `scripts/render_advisory.py`, the pipeline plugin through its
`gen_contract_docs.py` blocks — and both scope what they render to the
documents they author. So "cite an id" and "render that id here" are two
decisions made in different files, by different people, and nothing connected
them. This test is that connection.

Fixing a failure means one of:
  * the rule genuinely binds this plugin — widen the renderer's scope, or add
    the id to its declared borrowed/extra list, and regenerate;
  * it does not — the citation is wrong, and the prose should name the rule
    that does bind here, or state the boundary without an id.
Never by deleting the assertion: a dangling-for-the-reader id is the failure
mode this whole registry exists to remove.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

from _pins import require_contract_models

require_contract_models("analitiq.contracts")

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS = REPO_ROOT / "plugins"

#: A citation anywhere in the prose.
CITED = re.compile(r"ADV-[A-Z]+-\d+")

#: A rendered row: the id in a markdown table's first cell, and the row closed
#: on the same line. This is what an agent can actually read the rule off — an
#: id merely mentioned in another rule's text resolves nothing, and neither does
#: a row whose remaining cells landed on the next line.
#:
#: The closing `|` is load-bearing, not decoration. A cell carrying a raw
#: newline splits the row in two: the id stays on the first line and severity,
#: enforcement and values orphan onto the second, where markdown renders neither
#: as a table. Matching only the id would call that row readable.
RENDERED_ROW = re.compile(r"^\|\s*`?(ADV-[A-Z]+-\d+)`?\s*\|.*\|\s*$")


def _scan(plugin: Path) -> tuple[dict[str, set[str]], set[str]]:
    cited: dict[str, set[str]] = collections.defaultdict(set)
    rendered: set[str] = set()
    for path in sorted(plugin.rglob("*.md")):
        if path.name == "CHANGELOG.md":
            continue
        where = str(path.relative_to(plugin))
        for line in path.read_text(encoding="utf-8").splitlines():
            row = RENDERED_ROW.match(line)
            if row:
                rendered.add(row.group(1))
                continue
            for rule_id in CITED.findall(line):
                cited[rule_id].add(where)
    return cited, rendered


def _plugins() -> list[Path]:
    return sorted(p for p in PLUGINS.iterdir() if p.is_dir())


def test_every_cited_rule_is_readable_in_the_plugin_that_cites_it() -> None:
    unreadable: dict[str, dict[str, list[str]]] = {}
    for plugin in _plugins():
        cited, rendered = _scan(plugin)
        missing = {i: sorted(s) for i, s in sorted(cited.items()) if i not in rendered}
        if missing:
            unreadable[plugin.name] = missing
    assert not unreadable, (
        "prose cites rule ids no file in the same plugin renders, so an agent "
        "reading the citation cannot look the rule up: "
        f"{unreadable}. Widen that plugin's renderer scope (PLUGIN_RESOURCES / "
        "BORROWED_RULE_IDS in scripts/render_advisory.py, or "
        "_ADVISORY_BLOCKS in the pipeline plugin's gen_contract_docs.py) and "
        "regenerate — or cite the rule that does bind here."
    )


def test_both_plugins_cite_and_render() -> None:
    """Non-vacuity, per plugin.

    The check above passes trivially over a plugin whose prose cites nothing,
    and over one whose every markdown file has stopped matching the row regex —
    which is exactly what a change to the renderers' table shape would do,
    silently, while removing the reader's lookup entirely.
    """
    for plugin in _plugins():
        cited, rendered = _scan(plugin)
        assert cited, f"{plugin.name}: no ADV-* citations found in its prose"
        assert rendered, (
            f"{plugin.name}: no rendered rule rows found — the generated tables "
            "no longer start a line with the id, so nothing here is lookup-able"
        )
