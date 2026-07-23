"""Pin the researcher's grounding instructions to the ProviderFacts fragment.

`io-contracts.md` owns the ProviderFacts JSON Schema fragment;
`connector-provider-researcher.md` restates several of its database-branch
field names as grounding instructions ("report ... `sqlalchemy_driver` ...").
Nothing else ties the two files together, so a partial rename would leave the
researcher grounding fields the fragment no longer names — #70's
`async_sqlalchemy_driver` → `sqlalchemy_driver` rename happened to land
consistently, but only by care, not by any check (issue #72 item 4).

Convention this guard enforces: inside the researcher's `- For databases:`
hard-rule bullets, a backticked snake_case token (`` `like_this` ``) is a
ProviderFacts field reference and must exist in the fragment. Tokens that are
not bare snake_case identifiers (`dialect+driver`, `COPY FROM stdin`,
`tls.supported_modes`) are prose, not field references, and are ignored.

Pure text-vs-text: no contract packages involved, so no `_pins` skip guard —
this always runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "analitiq-connector-builder"
IO_CONTRACTS = PLUGIN_ROOT / "skills" / "connector-builder" / "references" / "io-contracts.md"
RESEARCHER = PLUGIN_ROOT / "agents" / "connector-provider-researcher.md"

_FIELD_TOKEN = re.compile(r"`([a-z][a-z0-9_]*)`")


def _provider_facts_schema() -> dict:
    """The first ```json fenced block under the `## ProviderFacts` heading."""
    text = IO_CONTRACTS.read_text(encoding="utf-8")
    match = re.search(
        r"^## ProviderFacts.*?^```json\n(.*?)^```", text, re.MULTILINE | re.DOTALL
    )
    assert match, f"{IO_CONTRACTS}: no ```json block under '## ProviderFacts'"
    return json.loads(match.group(1))


def _known_fields(schema: dict) -> set[str]:
    """Top-level property names plus the database branch's property names."""
    fields = set(schema.get("properties", {}))
    for branch in schema.get("oneOf", []):
        props = branch.get("properties", {})
        if props.get("kind", {}).get("const") == "database":
            fields |= set(props)
            return fields
    pytest.fail(f"{IO_CONTRACTS}: ProviderFacts has no kind=database oneOf branch")


def _database_bullets() -> list[str]:
    """Each `- For databases:` bullet in ## Hard rules, continuations joined."""
    text = RESEARCHER.read_text(encoding="utf-8")
    section = re.search(r"^## Hard rules$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert section, f"{RESEARCHER}: no '## Hard rules' section"

    bullets: list[str] = []
    current: list[str] | None = None
    for line in section.group(1).splitlines():
        if line.startswith("- "):
            if current:
                bullets.append("\n".join(current))
            current = [line]
        elif current and line.startswith("  "):
            current.append(line)
        else:
            if current:
                bullets.append("\n".join(current))
            current = None
    if current:
        bullets.append("\n".join(current))
    return [b for b in bullets if b.startswith("- For databases:")]


def test_extraction_finds_the_grounding_bullets() -> None:
    """Guard the extraction itself: if the researcher prose restructures its
    database bullets, this fails loudly instead of the field check passing
    vacuously on an empty token set."""
    bullets = _database_bullets()
    assert len(bullets) >= 2, (
        f"expected >= 2 '- For databases:' bullets under '## Hard rules' in "
        f"{RESEARCHER.name}, found {len(bullets)} — update _database_bullets() "
        "if the prose restructured."
    )
    tokens = {t for b in bullets for t in _FIELD_TOKEN.findall(b)}
    assert tokens, "no backticked snake_case field tokens extracted from the bullets"
    # Canary: the very field #70 renamed. If it renames again, all three sites
    # (fragment, prose, this literal) move together as a recorded decision.
    assert "sqlalchemy_driver" in tokens


def test_prose_grounded_fields_exist_in_provider_facts() -> None:
    """Every field the researcher prose instructs grounding must exist in the
    ProviderFacts fragment — a rename landing in only one file fails here in
    both directions (prose keeps the old name, or prose moves ahead of the
    fragment)."""
    known = _known_fields(_provider_facts_schema())
    tokens = {t for b in _database_bullets() for t in _FIELD_TOKEN.findall(b)}
    unknown = sorted(tokens - known)
    assert not unknown, (
        f"researcher prose grounds field(s) {unknown} that the ProviderFacts "
        f"fragment in {IO_CONTRACTS.name} does not define. Either the fragment "
        "renamed a field without the prose following, or the prose references "
        "a field that was never added — fix whichever file is stale."
    )


def test_provider_facts_database_branch_still_names_the_driver_fields() -> None:
    """The reverse anchor: the fragment's database branch keeps the
    driver-selection fields the pipeline depends on by name. Removing one from
    the fragment without touching the prose would otherwise only fail once the
    prose is next edited."""
    known = _known_fields(_provider_facts_schema())
    expected = {"adbc_driver_package", "flight_sql_endpoint", "bulk_load_protocol",
                "sqlalchemy_driver", "tls"}
    missing = sorted(expected - known)
    assert not missing, (
        f"ProviderFacts database branch lost field(s) {missing} — if the "
        "rename/removal is intentional, update the researcher prose bullets "
        "and this expectation together."
    )
