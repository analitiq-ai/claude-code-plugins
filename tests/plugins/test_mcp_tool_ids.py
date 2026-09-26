"""An agent reaches an MCP tool by the id Claude Code derives from its plugin's
name, the server key in the plugin's `.mcp.json` and the tool's name; a rename
of any of them leaves a hand-written id pointing at nothing."""
from __future__ import annotations

import json
import re

import pytest

from _eval_grade import REPO_ROOT, grade  # noqa: E402  (pytest puts this dir on sys.path)

_AGENTS = sorted((REPO_ROOT / "plugins").glob("*/agents/*.md"))


def _tool_prefixes(plugin) -> set[str]:
    name = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    servers = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    return {f"mcp__plugin_{name}_{server}__" for server in servers}


def _allowed_tools(agent) -> list[str]:
    frontmatter = agent.read_text(encoding="utf-8").split("---", 2)[1]
    line = re.search(r"^tools:(.*)$", frontmatter, re.MULTILINE)
    return [t.strip() for t in line.group(1).split(",")] if line else []


@pytest.mark.parametrize("agent", _AGENTS, ids=lambda a: f"{a.parents[1].name}/{a.stem}")
def test_every_mcp_tool_names_a_server_its_plugin_declares(agent):
    prefixes = _tool_prefixes(agent.parents[1])
    stray = [t for t in _allowed_tools(agent)
             if t.startswith("mcp__") and not any(t.startswith(p) for p in prefixes)]
    assert stray == []


_BUILDER_MODE = re.compile(r'/scripts/validation_request\.py"\s+(\w+)')


def _builder_tool(mode, tmp_path) -> str:
    (tmp_path / "doc.json").write_text("{}", encoding="utf-8")
    argv = {"document": [tmp_path / "doc.json", "kind"], "package": [tmp_path, "kind"],
            "workspace": [tmp_path]}[mode]
    return grade.builder.build([mode, *map(str, argv)])["tool"]


_BACKEND_TOOLS = re.compile(r"^## Backend tools\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def _backend_tools(body: str) -> set[str]:
    section = _BACKEND_TOOLS.search(body)
    return set(re.findall(r"`([a-z_]+)`", section.group(1))) if section else set()


@pytest.mark.parametrize("agent", _AGENTS, ids=lambda a: f"{a.parents[1].name}/{a.stem}")
def test_an_agent_allowlists_exactly_the_server_tools_it_calls(agent, tmp_path):
    body = agent.read_text(encoding="utf-8").split("---", 2)[2]
    called = {_builder_tool(mode, tmp_path) for mode in _BUILDER_MODE.findall(body)}
    called |= _backend_tools(body)
    allowed = {t.split("__")[-1] for t in _allowed_tools(agent) if t.startswith("mcp__")}
    assert allowed == called


def test_both_kinds_of_call_site_are_located():
    bodies = [a.read_text(encoding="utf-8").split("---", 2)[2] for a in _AGENTS]
    assert any(_BUILDER_MODE.search(b) for b in bodies)
    assert any(_backend_tools(b) for b in bodies)
