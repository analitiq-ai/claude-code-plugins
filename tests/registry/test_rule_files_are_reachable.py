"""Every rule file is named by a SKILL.md or agent definition, and no SKILL.md outgrew its budget.

The rule reference is a set of per-scope files now. That buys progressive
disclosure — a file costs nothing until it is read — at the price of a new way
to fail silently: a file nothing links to is a file no agent opens, and it
fails no other guard here. `test_rule_reference_sync` proves the set matches the
registry; this proves the set is *reachable*, which is the half that decides
whether an agent ever meets the rules in it.

Both checks are authoring-standard properties rather than contract facts:

* **One level deep.** A reference linked from a file that is itself linked from
  SKILL.md may be read partially — an agent previews a nested file with
  `head -100` rather than reading it whole, so a rule below the fold is a rule
  it never sees. Every rule file must therefore be named by a SKILL.md or an
  agent definition — a file an agent starts from — never only by a document
  one of those points at.
* **Under 500 lines.** Claude Code's skill-authoring guidance
  (<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices>)
  puts SKILL.md's budget there; keeping rule tables out of SKILL.md is what
  keeps every SKILL.md under it.

Skips cleanly when the contract packages are absent, like the other guards.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from _pins import require_contract_models

require_contract_models("analitiq.contracts")

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS = REPO_ROOT / "plugins"

#: The published guidance's SKILL.md budget. Not a contract fact — a limit on
#: how much an agent is asked to hold before it has decided what it is doing.
SKILL_MAX_LINES = 500


def _renderer():
    path = REPO_ROOT / "scripts" / "render_rule_reference.py"
    spec = importlib.util.spec_from_file_location("render_rule_reference", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skill_docs(plugin: Path) -> list[Path]:
    return sorted(plugin.rglob("SKILL.md"))


def _agent_docs(plugin: Path) -> list[Path]:
    agents = plugin / "agents"
    return sorted(agents.glob("*.md")) if agents.is_dir() else []


@pytest.mark.parametrize("owner", ["connector-plugin", "pipeline-plugin"])
def test_every_rule_file_is_named_by_a_skill_document(owner: str) -> None:
    """No rule file may be reachable only through another reference.

    The failure this catches is not a broken link — it is a file that renders
    correctly, stays in sync, and is never opened, because the only thing
    naming it is a document the agent reads part of.
    """
    renderer = _renderer()
    root = renderer.OUTPUT_DIRS[owner]
    plugin = root.parents[3]
    assert plugin.parent == PLUGINS, f"unexpected layout for {owner}: {plugin}"

    named: set[str] = set()
    for doc in [*_skill_docs(plugin), *_agent_docs(plugin)]:
        text = doc.read_text(encoding="utf-8")
        for path in renderer.render_all(owner):
            # Match on the file name inside the rules directory: prose reaches
            # it by several spellings (${CLAUDE_PLUGIN_ROOT}-anchored, plugin
            # relative, sibling-skill relative) and pinning one would grade the
            # spelling rather than the reachability.
            if f"rules/{path.name}" in text:
                named.add(path.name)

    unreachable = sorted(
        p.name for p in renderer.render_all(owner) if p.name not in named
    )
    assert not unreachable, (
        f"{owner}: these rule files are named by no SKILL.md or agent "
        f"definition, so nothing sends an agent to them: {unreachable}. Link "
        "each from the skill whose agents author that document — one level "
        "deep, never through another reference."
    )


@pytest.mark.parametrize("owner", ["connector-plugin", "pipeline-plugin"])
def test_every_named_rule_file_still_renders(owner: str) -> None:
    """The reverse direction: prose must not name a file the set stopped
    rendering.

    Buckets come and go with the registry — a scope falling under the floor
    folds into `shared.md` and its file is deleted — while the SKILL.md
    indexes and agent reading lists naming the files are hand-written. This
    is what fails when a bucket disappears and a pointer to it is left
    behind, sending an agent to a file that no longer exists.
    """
    import re

    renderer = _renderer()
    root = renderer.OUTPUT_DIRS[owner]
    plugin = root.parents[3]
    rendered = {p.name for p in renderer.render_all(owner)}

    dangling: dict[str, list[str]] = {}
    for doc in [*_skill_docs(plugin), *_agent_docs(plugin)]:
        text = doc.read_text(encoding="utf-8")
        for name in set(re.findall(r"rules/([a-z0-9-]+\.md)", text)):
            if name not in rendered:
                dangling.setdefault(doc.relative_to(PLUGINS).as_posix(), []).append(name)
    assert not dangling, (
        f"{owner}: prose names rule files the renderer no longer produces: "
        f"{dangling}. Remove the pointer, or re-check the record scoping that "
        "made the bucket disappear."
    )


@pytest.mark.parametrize("owner", ["connector-plugin", "pipeline-plugin"])
def test_rule_files_are_not_orphaned_by_an_empty_set(owner: str) -> None:
    """Non-vacuity: the check above passes over a plugin rendering no files."""
    assert _renderer().render_all(owner), f"{owner}: renders no rule files at all"


def test_no_skill_document_exceeds_its_line_budget() -> None:
    oversized = {
        str(doc.relative_to(REPO_ROOT)): len(doc.read_text(encoding="utf-8").splitlines())
        for plugin in sorted(PLUGINS.iterdir())
        if plugin.is_dir()
        for doc in _skill_docs(plugin)
        if len(doc.read_text(encoding="utf-8").splitlines()) > SKILL_MAX_LINES
    }
    assert not oversized, (
        f"SKILL.md documents over {SKILL_MAX_LINES} lines: {oversized}. A "
        "SKILL.md is loaded whole the moment its skill triggers, so it is the "
        "one document whose length is paid before the agent knows what it is "
        "doing. Move the detail into a reference file and link it."
    )
