"""Every rule id a plugin's prose cites must be readable inside that plugin.

`test_advisory_sync.py::test_prose_rule_citations_resolve` already pins that a
cited id names a live rule. That is the author's half. This file pins the
reader's half:

An agent runs from the plugin cache. It has the plugin tree and nothing else —
no registry source, no PyPI, no network. When prose says "…must not be renamed
(`ADV-ENDP-043`)" and nothing in that plugin carries `ADV-ENDP-043`, the id is a
dead end: the agent learns there is a rule and cannot learn what it says. That
is strictly worse than the restatement the citation replaced, because a
restatement at least carried the rule.

Each plugin renders the rules its records name it as an owner of, so citing an
id and rendering it are the same decision made once, in the record. What is left
to check is that no prose cites outside that set — an id belonging to somebody
else's plugin.

**This test asks each renderer what it rendered.** It does not search the
generated markdown for row-shaped text. A scan like that measures the renderer's
output format rather than its content: when a cell once carried a newline and
split every row in two, the scan still found the ids and reported the prose
healthy. The renderer holds the answer already; taking it from there means a
change to the table shape cannot silently disarm this guard.

Fixing a failure means one of:
  * the rule genuinely binds this plugin — add the plugin to `owners` in
    `rules/adv/<id>.yaml` and recompile;
  * it does not — the citation is wrong, and the prose should name the rule
    that does bind here, or state the boundary without an id.
Never by deleting the assertion: a dangling-for-the-reader id is the failure
mode this whole registry exists to remove.
"""

from __future__ import annotations

import collections
import importlib.util
import re
import sys
from pathlib import Path

from _pins import require_contract_models

require_contract_models("analitiq.contracts")

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS = REPO_ROOT / "plugins"

#: A citation anywhere in the prose. The one pattern this file runs over text,
#: and it matches an identifier the registry mints — a fixed shape with no
#: English in it — never a sentence about a rule.
CITED = re.compile(r"ADV-[A-Z]+-\d+")


def _load(path: Path, name: str):
    """Import a renderer by path — neither `scripts/` nor a plugin is a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def _renderers() -> dict[str, object]:
    """Each plugin's renderer, keyed by the plugin directory it writes into."""
    return {
        "analitiq-connector-builder": _load(
            REPO_ROOT / "scripts" / "render_advisory.py", "render_advisory"
        ),
        "analitiq-pipeline-builder": _load(
            PLUGINS / "analitiq-pipeline-builder" / "scripts" / "gen_contract_docs.py",
            "gen_contract_docs",
        ),
    }


def _cited(plugin: Path) -> dict[str, set[str]]:
    """Every id this plugin's prose cites, and the files citing it."""
    found: dict[str, set[str]] = collections.defaultdict(set)
    for path in sorted(plugin.rglob("*.md")):
        if path.name == "CHANGELOG.md":
            continue
        where = str(path.relative_to(plugin))
        for rule_id in CITED.findall(path.read_text(encoding="utf-8")):
            found[rule_id].add(where)
    return found


def test_every_cited_rule_is_readable_in_the_plugin_that_cites_it() -> None:
    unreadable: dict[str, dict[str, list[str]]] = {}
    for name, renderer in _renderers().items():
        rendered = renderer.rendered_ids()
        cited = _cited(PLUGINS / name)
        missing = {i: sorted(s) for i, s in sorted(cited.items()) if i not in rendered}
        if missing:
            unreadable[name] = missing
    assert not unreadable, (
        "prose cites rule ids the plugin does not render, so an agent reading "
        f"the citation cannot look the rule up: {unreadable}. Add the plugin to "
        "that rule's `owners` in rules/adv/<id>.yaml and recompile — or cite "
        "the rule that does bind here."
    )


def test_each_plugin_cites_and_renders() -> None:
    """Non-vacuity, per plugin.

    The check above passes trivially over a plugin whose prose cites nothing,
    and over one whose renderer has stopped claiming any rule at all.
    """
    for name, renderer in _renderers().items():
        assert _cited(PLUGINS / name), f"{name}: no ADV-* citations found in its prose"
        assert renderer.rendered_ids(), f"{name}: its renderer claims no rules"


def test_every_rule_reaches_the_plugins_that_own_it() -> None:
    """`owners` is what routes a rule, so every owner must actually render it.

    Without this, a renderer could narrow what it emits — a scope map that stops
    claiming something — and the citations test would stay green by there simply
    being no prose citing the dropped rule *yet*. The record says an author here
    has to know the rule; this is what makes that true of the shipped tree.
    """
    from analitiq.contracts.shared.advisory import all_rules

    owner_of = {
        "analitiq-connector-builder": "connector-plugin",
        "analitiq-pipeline-builder": "pipeline-plugin",
    }
    dropped: dict[str, list[str]] = {}
    for name, renderer in _renderers().items():
        rendered = renderer.rendered_ids()
        owed = {r.id for r in all_rules() if owner_of[name] in r.owners}
        if owed - rendered:
            dropped[name] = sorted(owed - rendered)
    assert not dropped, (
        f"records name these plugins as owners but the plugin renders nothing "
        f"for them: {dropped}. Either the renderer's placement map has a gap, "
        "or the record should not name that owner."
    )
