"""Pin the researcher's grounding instructions to the ProviderFacts fragment.

`io-contracts.md` owns the ProviderFacts JSON Schema fragment;
`connector-provider-researcher.md` restates several of its api-branch and
database-branch field names as grounding instructions ("report ...
`sqlalchemy_driver` ..."). Nothing else ties their field names together, so a
partial rename would leave the researcher grounding fields the fragment no
longer names — the `async_sqlalchemy_driver` → `sqlalchemy_driver` rename
happened to land in both files consistently, but only by care, not by any
check.

Convention this guard enforces: inside the researcher's `- For APIs:` and
`- For databases:` hard-rule bullets, a backticked snake_case token
(`` `like_this` ``) or dotted path (`` `tls.supported_modes` ``) is a
ProviderFacts field reference and must resolve in the fragment (dotted paths
resolve through nested object `properties`). Backticked text that is not a
bare snake_case identifier or dotted path (`dialect+driver`, `COPY FROM
stdin`, `mysql+aiomysql`) is prose, not a field reference, and is ignored. The
researcher prose carries a maintainer comment pointing back here.

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

_FIELD_TOKEN = re.compile(r"`([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)`")


def _provider_facts_schema() -> dict:
    """The first ```json fenced block inside the `## ProviderFacts` section.

    The search is bounded at the next `## ` heading so a missing fence fails
    here with a message about the fragment, instead of silently matching a
    later section's block (EndpointFacts) and misdiagnosing downstream.
    """
    text = IO_CONTRACTS.read_text(encoding="utf-8")
    section = re.search(
        r"^## ProviderFacts.*?$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    assert section, f"{IO_CONTRACTS}: no '## ProviderFacts' heading"
    fence = re.search(r"^```json\n(.*?)^```", section.group(1), re.MULTILINE | re.DOTALL)
    assert fence, f"{IO_CONTRACTS}: no ```json block inside the ProviderFacts section"
    return json.loads(fence.group(1))


def _known_fields(schema: dict) -> set[str]:
    """Dotted property paths from the top level plus both kind branches.

    Nested object `properties` contribute dotted paths (`tls` and
    `tls.supported_modes` are both known), so a rename inside a sub-object is
    caught the same as a branch-level one. Api and database branch field
    names don't collide, so merging both into one set loses nothing a
    per-branch lookup would have caught.
    """
    fields: set[str] = set()

    def walk(props: dict, prefix: str) -> None:
        for name, sub in props.items():
            fields.add(prefix + name)
            if isinstance(sub, dict):
                walk(sub.get("properties", {}), f"{prefix}{name}.")

    walk(schema.get("properties", {}), "")
    seen_kinds = set()
    for branch in schema.get("oneOf", []):
        props = branch.get("properties", {})
        kind = props.get("kind", {}).get("const")
        if kind in ("api", "database"):
            seen_kinds.add(kind)
            walk(props, "")
    missing_kinds = {"api", "database"} - seen_kinds
    if missing_kinds:
        pytest.fail(f"{IO_CONTRACTS}: ProviderFacts has no kind={sorted(missing_kinds)} oneOf branch")
    return fields


def _grounding_bullets(prefix: str) -> list[str]:
    """Each `<prefix>` bullet in ## Hard rules, continuations joined."""
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
    return [b for b in bullets if b.startswith(prefix)]


def _api_bullets() -> list[str]:
    return _grounding_bullets("- For APIs:")


def _database_bullets() -> list[str]:
    return _grounding_bullets("- For databases:")


def test_extraction_finds_the_grounding_bullets() -> None:
    """Guard the extraction itself: if the researcher prose restructures its
    api or database bullets, this fails loudly instead of the field check
    passing vacuously on an empty or shrunken token set."""
    api_bullets = _api_bullets()
    db_bullets = _database_bullets()
    # Exact count, not a floor: with a floor, one of N>2 bullets could be
    # reworded away and its tokens would silently leave the guard. Adding or
    # removing a `- For APIs:` / `- For databases:` bullet is a recorded
    # decision — update the matching count with it.
    assert len(api_bullets) == 1, (
        f"expected exactly 1 '- For APIs:' bullet under '## Hard rules' "
        f"in {RESEARCHER.relative_to(REPO_ROOT)}, found {len(api_bullets)} — if "
        "the prose restructured deliberately, update this count."
    )
    assert len(db_bullets) == 4, (
        f"expected exactly 4 '- For databases:' bullets under '## Hard rules' "
        f"in {RESEARCHER.relative_to(REPO_ROOT)}, found {len(db_bullets)} — if "
        "the prose restructured deliberately, update this count."
    )
    tokens = {t for b in api_bullets + db_bullets for t in _FIELD_TOKEN.findall(b)}
    assert tokens, "no backticked field tokens extracted from the bullets"
    # Canaries: one per branch, plus a dotted nested path (proves dotted
    # extraction works). If any renames again, all sites (fragment, prose,
    # these literals) move together as a recorded decision.
    assert "documented_http_errors" in tokens
    assert "sqlalchemy_driver" in tokens
    assert "tls.supported_modes" in tokens


def test_prose_grounded_fields_exist_in_provider_facts() -> None:
    """Every field the researcher prose instructs grounding must exist in the
    ProviderFacts fragment — a rename landing in only one file fails here in
    both directions (prose keeps the old name, or prose moves ahead of the
    fragment)."""
    known = _known_fields(_provider_facts_schema())
    tokens = {t for b in _api_bullets() + _database_bullets() for t in _FIELD_TOKEN.findall(b)}
    unknown = sorted(tokens - known)
    assert not unknown, (
        f"researcher prose grounds field(s) {unknown} that the ProviderFacts "
        f"fragment in {IO_CONTRACTS.name} does not define. Either the fragment "
        "renamed a field without the prose following, or the prose references "
        "a field that was never added — fix whichever file is stale."
    )


def test_provider_facts_still_names_the_grounded_fields() -> None:
    """The reverse anchor: the fragment keeps the fields the pipeline depends
    on by name, across both branches — including the nested TLS mode carrier,
    which three prose files reference (`spec-tls.md`, `db-connector-creator.md`,
    `connector-provider-researcher.md`). Removing one from the fragment
    without touching the prose would otherwise only fail once the prose is
    next edited."""
    known = _known_fields(_provider_facts_schema())
    expected = {"adbc_driver_package", "flight_sql_endpoint", "bulk_load_protocol",
                "sqlalchemy_driver", "tls", "tls.supported_modes",
                # The RESEARCHED subset of the sql_capabilities inputs —
                # `bulk_load` and `stage.schema` are authoring decisions with no
                # carrier here, by design. The creator declares the rest to an
                # engine that refuses rather than guesses, so losing a carrier
                # would silently push it back to assuming them.
                "sql_write_path", "sql_write_path.upsert_grammar",
                "sql_write_path.catalog_model",
                "sql_write_path.qualified_statement_targeting",
                "sql_write_path.temp_table_support",
                "sql_write_path.transactional_ddl",
                "sql_write_path.identifier_limits",
                # The RESEARCHED subset of error_map's inputs — the category
                # mapping itself is the creator's decision, never the
                # researcher's, so it carries no field here. database-branch
                # (error_signals) and api-branch (documented_http_errors).
                "error_signals", "error_signals.native_code_attrs",
                "error_signals.documented_codes",
                "documented_http_errors"}
    missing = sorted(expected - known)
    assert not missing, (
        f"ProviderFacts lost field(s) {missing} — if the rename/removal is "
        f"intentional, update the fragment in {IO_CONTRACTS.relative_to(REPO_ROOT)}, "
        f"the grounding bullets in {RESEARCHER.relative_to(REPO_ROOT)}, the "
        "per-fact sources in "
        "plugins/analitiq-connector-builder/agents/db-connector-creator.md and "
        "plugins/analitiq-connector-builder/agents/api-connector-creator.md, "
        "and this expectation together."
    )
