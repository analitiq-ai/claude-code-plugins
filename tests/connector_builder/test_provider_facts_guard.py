"""Pin the researcher's fact fragments to the shapes they defer to.

Two guards live here. The second is below, under "the param shape is the
contract's"; this docstring's subject is the first.

Pin the researcher's grounding instructions to the ProviderFacts fragment.

`io-contracts.md` owns the ProviderFacts JSON Schema fragment;
`connector-provider-researcher.md` restates several of its database-branch
field names as grounding instructions ("report ... `sqlalchemy_driver` ...").
Nothing else ties their field names together, so a partial rename would leave
the researcher grounding fields the fragment no longer names — the
`async_sqlalchemy_driver` → `sqlalchemy_driver` rename happened to land in both
files consistently, but only by care, not by any check.

Convention this guard enforces: inside the researcher's `- For databases:`
hard-rule bullets, a backticked snake_case token (`` `like_this` ``) or dotted
path (`` `tls.supported_modes` ``) is a ProviderFacts field reference and must
resolve in the fragment (dotted paths resolve through nested object
`properties`). Backticked text that is not a bare snake_case identifier or
dotted path (`dialect+driver`, `COPY FROM stdin`, `mysql+aiomysql`) is prose,
not a field reference, and is ignored. The researcher prose carries a
maintainer comment pointing back here.

Pure text-vs-text: no contract packages involved, so no `_pins` skip guard —
this always runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from analitiq.contracts import endpoints

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
    """Dotted property paths from the top level plus the database branch.

    Nested object `properties` contribute dotted paths (`tls` and
    `tls.supported_modes` are both known), so a rename inside a sub-object is
    caught the same as a branch-level one.
    """
    fields: set[str] = set()

    def walk(props: dict, prefix: str) -> None:
        for name, sub in props.items():
            fields.add(prefix + name)
            if isinstance(sub, dict):
                walk(sub.get("properties", {}), f"{prefix}{name}.")

    walk(schema.get("properties", {}), "")
    for branch in schema.get("oneOf", []):
        props = branch.get("properties", {})
        if props.get("kind", {}).get("const") == "database":
            walk(props, "")
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
    vacuously on an empty or shrunken token set."""
    bullets = _database_bullets()
    # Exact count, not a floor: with a floor, one of N>2 bullets could be
    # reworded away and its tokens would silently leave the guard. Adding or
    # removing a `- For databases:` bullet is a recorded decision — update
    # this count with it.
    assert len(bullets) == 3, (
        f"expected exactly 3 '- For databases:' bullets under '## Hard rules' "
        f"in {RESEARCHER.relative_to(REPO_ROOT)}, found {len(bullets)} — if the "
        "prose restructured deliberately, update this count."
    )
    tokens = {t for b in bullets for t in _FIELD_TOKEN.findall(b)}
    assert tokens, "no backticked field tokens extracted from the bullets"
    # Canaries: the field renamed from `async_sqlalchemy_driver`, and a dotted
    # nested path (proves dotted extraction works). If either renames again,
    # all three sites (fragment, prose, these literals) move together as a
    # recorded decision.
    assert "sqlalchemy_driver" in tokens
    assert "tls.supported_modes" in tokens


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
    """The reverse anchor: the fragment's database branch keeps the fields the
    pipeline depends on by name — including the nested TLS mode carrier, which
    three prose files reference (`spec-tls.md`, `db-connector-creator.md`,
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
                "sql_write_path.identifier_limits"}
    missing = sorted(expected - known)
    assert not missing, (
        f"ProviderFacts database branch lost field(s) {missing} — if the "
        "rename/removal is intentional, update the fragment in "
        f"{IO_CONTRACTS.relative_to(REPO_ROOT)}, the grounding bullets in "
        f"{RESEARCHER.relative_to(REPO_ROOT)}, the per-fact sources in "
        "plugins/analitiq-connector-builder/agents/db-connector-creator.md, "
        "and this expectation together."
    )


# ---------------------------------------------------------------------------
# The param shape is the contract's
# ---------------------------------------------------------------------------


def _endpoint_facts_schema() -> dict:
    """The first ```json fenced block inside the `## EndpointFacts` section."""
    text = IO_CONTRACTS.read_text(encoding="utf-8")
    section = re.search(
        r"^## EndpointFacts.*?$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    assert section, f"{IO_CONTRACTS}: no '## EndpointFacts' heading"
    fence = re.search(r"^```json\n(.*?)^```", section.group(1), re.MULTILINE | re.DOTALL)
    assert fence, f"{IO_CONTRACTS}: no ```json block inside the EndpointFacts section"
    return json.loads(fence.group(1))


API_ENDPOINT_LATEST = REPO_ROOT / "schemas" / "api-endpoint" / "latest.json"


def test_request_params_defers_to_the_contract_param_shape():
    """A researched request parameter IS an api-endpoint param, by reference.

    Enumerating a param's keys here is a hand-maintained copy of `Param`, and
    it was one: it shipped naming a subset of the model's fields, and each one
    it omitted surfaced as a separate review finding — a placement, a type, a
    serialization pair — because a creator authoring only from these facts
    cannot declare what they do not carry. The shape has ONE owner, so this
    fragment references it.

    Structural, not semantic (`.claude/rules/guards.md`): the fenced block is
    parsed and the pointer is resolved against the rendered schema. Re-adding
    an enumeration fails on the missing `$ref`; renaming the definition the
    pointer names fails on resolution.
    """
    props = _endpoint_facts_schema()["properties"]
    assert "$ref" in props["idempotency"], (
        "EndpointFacts.idempotency must reference the api-endpoint idempotency "
        "block rather than describe one — the block is closed, so a described "
        f"copy is one an author can fill with a key it refuses. Got: {props['idempotency']!r}")
    _resolves(props["idempotency"]["$ref"])

    assert "$ref" in props["read_filters"], (
        "EndpointFacts.read_filters must reference the api-endpoint filter map "
        "rather than describe one — the operator vocabulary and the "
        "record-field key shape are constraints the contract declares, and a "
        "copy of them accepts a typo here that the endpoint then fails on. "
        f"Got: {props['read_filters']!r}")
    _resolves(props["read_filters"]["$ref"])

    assert "$ref" in props["read_request"], (
        "EndpointFacts.read_request must reference the api-endpoint read-request "
        "shape rather than describe one — method, path, a body value's position, "
        "a search body's content type and a path placeholder's binding are all "
        f"fields of it, and an account of them is always short. Got: {props['read_request']!r}")
    _resolves(props["read_request"]["$ref"])

    entry = props["request_params"]
    value = entry.get("additionalProperties")
    assert isinstance(value, dict) and "$ref" in value, (
        "EndpointFacts.request_params must reference the api-endpoint param "
        "definition rather than enumerate a param's keys — a curated copy of "
        f"`Param` goes stale the moment the contract gains a field. Got: {value!r}"
    )
    _resolves(value["$ref"])


def _resolves(ref: str) -> None:
    """The pointer names the published api-endpoint schema and resolves in it.

    Resolution is the contract's own `resolve_local_pointer`, not a walk written
    here: it is the RFC 6901 reading this repo already settled — tokens
    percent-decoded then pointer-unescaped, array indices per §4 — and its
    docstring exists to say a re-implementation gets those wrong. A hand walk
    would red on a pointer through an array or one carrying an escaped `/`,
    which is a guard failing on a correct reference.
    """
    url, _, fragment = ref.partition("#")
    assert url == "https://schemas.analitiq.ai/api-endpoint/latest.json", (
        f"the reference must name the published api-endpoint schema; got {url!r}")

    root = json.loads(API_ENDPOINT_LATEST.read_text(encoding="utf-8"))
    node = endpoints.resolve_local_pointer(root, f"#{fragment}")
    assert node is not endpoints._MISSING, (
        f"{ref} does not resolve in the rendered api-endpoint schema. The "
        "definition was renamed or moved, so the fragment points at nothing "
        "and the researcher has no shape to fill.")
    assert isinstance(node, dict) and node, (
        f"{ref} resolves to nothing usable: {node!r}")
