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


@pytest.mark.parametrize(
    "agent", [a for a in _AGENTS if "/scripts/validation_request.py" in a.read_text(encoding="utf-8")],
    ids=lambda a: f"{a.parents[1].name}/{a.stem}")
def test_an_agent_running_the_builder_allowlists_every_tool_it_names(agent):
    (prefix,) = _tool_prefixes(agent.parents[1])
    assert {prefix + tool for tool in grade._TOOLS} <= set(_allowed_tools(agent))
