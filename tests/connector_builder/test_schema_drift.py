"""Drift-check CI for schema-owned enums the plugin restates as decision logic.

A handful of enums can't simply be deleted from the plugin: they ARE the
mapping logic (`enum-mappers.md` maps researched provider facts onto schema
enum values; `ProviderFacts` classifies into them; `README.md`'s Supported
kinds table documents the closed sets). Per the drift policy, anything that
must stay duplicated is pinned
to the contract here. If the contract's enum changes, the matching test fails
and names the divergence, so the prose + mappers are updated in the same change
instead of silently drifting.

This guard reads the enums straight from the **pinned contract models**
(`analitiq-contract-models`) — the same models the `connector-schema-validator`
agent validates against, and the same ones the published JSON Schemas are
generated from. Each document's JSON Schema is generated locally (pydantic
`json_schema()`) and its enum sets compared. That makes the guard **offline,
CDN-free, and self-consistent with the validator**: it pins the plugin's prose
to the exact contract the plugin enforces at authoring time, not a
separately-hosted copy that can drift, 403, or 404.

The suite exercises the in-repo source (whose version
`tests/connector_builder/_pins.py` mirrors); the `connector-schema-validator`
agent self-installs the published runtime pin (`VALIDATOR_PIN`), which trails
that version during a release window. When the package isn't importable the whole
module is skipped (offline-dev convenience) — except in CI, which sets
`DRIFT_REQUIRE_CONTRACT_MODELS=1` so a missing or broken package is a hard
failure there, never a green all-skipped gate. Run `-rs` to print skip reasons.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

# Read the SAME contract models the validator validates against. The shared guard
# skips an offline dev run but hard-fails in CI (DRIFT_REQUIRE_CONTRACT_MODELS=1),
# so this merge gate can never pass by skipping. (A renamed submodule errors at
# the imports below, which the parent-package guard does not cover — that
# asymmetry is intentional: both surface as red in CI.)
from _pins import REPO_ROOT, assert_pinned_versions, require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts")

from pydantic import TypeAdapter  # noqa: E402  (imports gated by the guard above)
from analitiq.contracts.connector import Connector  # noqa: E402
from analitiq.contracts.endpoints import WRITE_MODES, ApiEndpointDoc  # noqa: E402
from analitiq.contracts.shared.common import SLUG_PATTERN  # noqa: E402

PLUGIN_ROOT = REPO_ROOT / "plugins" / "analitiq-connector-builder"

# --- plugin-side expected sets ---------------------------------------------
# These mirror the schema-owned enums restated across README.md and
# enum-mappers.md. (io-contracts.md restates the pagination set verbatim and an
# auth `family` set that is intentionally a SUBSET — its API `auth_model.family`
# omits `db`, which never applies to an API.) When a test below fails, update
# BOTH the prose and the matching expected set here in the same change.

EXPECTED_AUTH_TYPES = {
    "api_key",
    "basic_auth",
    "oauth2_authorization_code",
    "oauth2_client_credentials",
    "jwt",
    "db",
    "credentials",
    "aws_iam",
    "none",
}
EXPECTED_ADBC_DRIVERS = {"postgresql", "snowflake", "bigquery"}
# SqlAlchemyTransport.driver is deliberately an OPEN `dialect+driver` pattern —
# no driver allow-list (sync and async DBAPIs are both authorable; dispatch is
# engine-side). The openness itself is what the prose restates as decision
# logic (spec-driver-selection.md §Constraints and its sync/async guidance,
# the db-connector-creator checklist), so pin the pattern: a re-tightening — e.g.
# a revert to the old async-only alternation — must fail here and move the
# prose in the same change.
EXPECTED_SQLALCHEMY_DRIVER_PATTERN = r"^[a-z][a-z0-9_]*\+[a-z][a-z0-9_]*$"
EXPECTED_DSN_ENCODINGS = {
    "raw",
    "host",
    "url_userinfo",
    "url_path_segment",
    "url_query_key",
    "url_query_value",
}
# The SQL write-path declaration (`sql_capabilities`, engine ADR §5). The
# creator agent must pick each value from researched provider facts, so
# spec-sql-write-path.md restates the vocabularies as decision logic — the
# "mapping logic" exemption — and pins them here. The required shape facts are
# pinned too: the spec teaches that a declared block is COMPLETE and a partial
# one is a config error, which is only true while exactly these are the required
# set. (`limits` stays out — it is the additive member.)
EXPECTED_SQL_CAPABILITY_FACTS = {
    "catalog",
    "session_targeting",
    "merge_form",
    "bulk_load",
    "stage",
}
# The full property set. Pinning `required` alone leaves a NEW OPTIONAL member
# invisible: it lands in the contract, reaches no prose, and the spec's
# "a declared block is complete" goes on advertising a table that no longer
# covers the model. Same reasoning as `EXPECTED_SQL_STAGE_PROPERTIES` below,
# one level up.
EXPECTED_SQL_CAPABILITY_PROPERTIES = EXPECTED_SQL_CAPABILITY_FACTS | {"limits"}
EXPECTED_SQL_CATALOG_MODES = {"none", "read", "full"}
EXPECTED_SQL_SESSION_TARGETING = {"per_statement", "session_default"}
EXPECTED_SQL_MERGE_FORMS = {
    "merge",
    "insert_on_conflict",
    "insert_on_duplicate_key",
    "none",
}
EXPECTED_SQL_STAGE_FACTS = {"scope", "schema", "transactional_ddl"}
# The full property set, i.e. the three above plus the conditional
# `dedicated_schema` — which is restated in the prose (the required-iff rule)
# but, being conditional, never appears in `required`.
EXPECTED_SQL_STAGE_PROPERTIES = EXPECTED_SQL_STAGE_FACTS | {"dedicated_schema"}
EXPECTED_SQL_STAGE_SCOPES = {"temp", "real"}
EXPECTED_SQL_STAGE_SCHEMAS = {"target", "dedicated"}
# Additive, so neither cap is in any `required` list — but both names are
# restated in prose, and `max_bind_params` appears in no example, so nothing
# else would notice a rename.
EXPECTED_SQL_LIMIT_CAPS = {"max_bind_params", "max_identifier_len"}
# Per-transport, not connector-wide: `adbc_ingest` is the ADBC backend's own
# landing and is unrepresentable under `sqlalchemy`. The split is what the
# prose teaches — declaring it "involves no dialect code" — so pin both
# families; a collapse back to one shared set must move the prose with it.
EXPECTED_SQL_BULK_MECHANISMS = {
    "sqlalchemy": {"copy_from", "load_data_local_infile", "load_job"},
    "adbc": {"adbc_ingest", "copy_from", "load_data_local_infile", "load_job"},
}
EXPECTED_PAGINATION_STYLES = {"offset", "page", "cursor", "link", "keyset"}
# WriteOperation.idempotency `in` targets. No prose site restates them: the
# plugin's documents cite `RULE-ENDP-039`, whose `literal_enum` mechanism prints
# the members off the live model into the rendered rule reference. This set is
# the assertion target that keeps the contract and that record's field in step.
EXPECTED_IDEMPOTENCY_TARGETS = {"header", "body"}
# `Operations.write` keys — the destination write-mode vocabulary, shared with
# database destinations. `endpoint-creator.md` restates the whole set as decision
# logic: it tells the author to key only `insert` / `upsert`, and names
# `truncate_insert` as the member the schema permits but an API destination has
# no meaning for. A fourth mode landing in the contract must reach that
# guidance, or the agent silently omits a writable operation the provider
# supports. Like every expected set in this file, this one is compared against
# the CONTRACT, not against the prose — the prose is reachable only through the
# failure message. `test_endpoint_creator_prose_names_every_write_mode` below is
# what actually reads the document.
EXPECTED_WRITE_MODES = {"insert", "upsert", "truncate_insert"}
# Bare-marker arrow_type vocabulary enforced by the contract's authored-shape
# rules (Object→properties, List→items, Json→neither). Owned by the
# contract's `CONTAINER_CANONICAL_HEADS` (the grammar-derived set the
# `arrow_type` pattern embeds); the contract model leaves the sibling-key
# contract open, so the validator enforces it — keep this set in lockstep with
# the prose.
EXPECTED_BARE_MARKER_ARROW_TYPES = {"Object", "List", "Json"}
# The kind + transport discriminators — the outputs of KindMapper /
# TransportTypeMapper in enum-mappers.md; the kinds are also restated in
# README.md's Supported kinds table. Each lives as a
# `properties.<field>.const` across the per-variant `$defs`. The contract admits
# `nosql` / `document` alongside the storage stubs; the plugin recognizes them
# in its vocabulary but authors none (KindMapper still routes document DBs →
# `database`), the same posture it holds for `file` / `s3` / `stdout`.
EXPECTED_KINDS = {"api", "database", "nosql", "document", "file", "s3", "stdout"}
EXPECTED_TRANSPORT_TYPES = {"http", "sqlalchemy", "adbc", "s3", "file", "stdout"}
# Validator ids a connector/endpoint/type-map finding may carry — restated in
# io-contracts.md's `Diagnostics` enum and README.md § Validation. Owned by
# `analitiq.validator.VALIDATOR_IDS`, minus the `bundle-*` ids, which only apply
# to pipeline bundles this plugin never validates.
EXPECTED_VALIDATOR_IDS = {
    "contract-model",
    "document",
    "type-map-coverage",
    "type-map-rule",
    "type-map-write-coverage",
    "endpoint-filename",
    "endpoint-id-unique",
    "endpoint-id-locator",
    "endpoint-transport-ref",
    "embedded-json-schema",
}
# Resolution scopes a `ref` / `${...}` placeholder may lead with — restated as the
# scope table in references/value-expressions.md.
EXPECTED_RESOLUTION_SCOPES = {
    "connector",
    "connection",
    "secrets",
    "auth",
    "stream",
    "state",
    "runtime",
    "request",
    "response",
}
# Where the slug pattern — the `connector_id` / `endpoint_id` charset, owned by
# `analitiq.contracts.shared.common.SLUG_PATTERN` — may appear hand-typed in the
# plugin's prose, and how many times. No other site restates the
# regex: the agent-consumed sites reference one of these, and the rest (README,
# the orchestrator's hard rules, definition-of-done) say "the slug pattern"
# without spelling it:
#   - metadata-and-versioning.md — the canonical `connector_id` statement (the
#     field table); the creator agents point here.
#   - endpoint-identity.md — the canonical `endpoint_id` statement (Invariants).
#   - io-contracts.md — two embedded JSON Schemas agents consume as machine
#     vocabulary (the `resources[].key` description and the
#     `endpoint_files[].endpoint_id` constraint), which need the literal.
# A new copy is a recorded decision: it fails the count test until listed here.
EXPECTED_SLUG_PATTERN_SITES = {
    "skills/connector-builder/references/endpoint-identity.md": 1,
    "skills/connector-builder/references/io-contracts.md": 2,
    "skills/connector-builder/references/metadata-and-versioning.md": 1,
}


# --- helpers ---------------------------------------------------------------


def _schema(model_or_type) -> dict:
    """Generate a contract document's JSON Schema from its pinned model.

    `ApiEndpointDoc` is a `BaseModel` subclass (has `model_json_schema`); the
    connector contract is a discriminated `Union` (not a class), so it goes
    through `TypeAdapter`. Same generation path that produces the published
    schema, so the `$defs` layout the extractors below walk is identical.
    """
    if isinstance(model_or_type, type):
        return model_or_type.model_json_schema()
    return TypeAdapter(model_or_type).json_schema()


def _enum_at(schema: dict, *path: str) -> set[str] | None:
    """Return the `enum` set at `$defs/.../<path>`, or None if the path/enum is absent.

    Tolerates a restructured schema: any missing key, a non-dict node
    mid-traversal, or an `enum` that isn't a list yields None (the caller turns
    that into an explicit "schema was restructured" failure).
    """
    node: object = schema
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if not isinstance(node, dict):
        return None
    enum = node.get("enum")
    if not isinstance(enum, list):
        return None
    return set(enum)


def _pattern_at(schema: dict, *path: str) -> str | None:
    """Return the `pattern` at `$defs/.../<path>`, looking through `anyOf`.

    Optional fields (`str | None`) carry their constraint inside an `anyOf`
    branch rather than on the property node itself. Same restructure
    tolerance as `_enum_at`: a missing path, a non-dict node, or no string
    `pattern` on the node or any branch yields None.
    """
    node: object = schema
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if not isinstance(node, dict):
        return None
    branches = node.get("anyOf")
    candidates: list[dict] = [node]
    if isinstance(branches, list):
        candidates += [b for b in branches if isinstance(b, dict)]
    for cand in candidates:
        pattern = cand.get("pattern")
        if isinstance(pattern, str):
            return pattern
    return None


def _property_names_enum(schema: dict, *path: str) -> set[str] | None:
    """Return the `propertyNames.enum` at `$defs/.../<path>`, looking through `anyOf`.

    A mode-keyed map (`dict[WriteMode, …]`) renders its closed key set as
    `propertyNames.enum` rather than as an `enum` on the property itself, and an
    optional map buries that under an `anyOf` branch beside the null one. Same
    restructure tolerance as `_pattern_at`.
    """
    node: object = schema
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if not isinstance(node, dict):
        return None
    branches = node.get("anyOf")
    candidates: list[dict] = [node]
    if isinstance(branches, list):
        candidates += [b for b in branches if isinstance(b, dict)]
    for cand in candidates:
        names = cand.get("propertyNames")
        if isinstance(names, dict) and isinstance(names.get("enum"), list):
            return set(names["enum"])
    return None


def _const_types(schema: dict, def_suffix: str, const_field: str = "type") -> set[str] | None:
    """Collect the `<const_field>` const across `$defs/*<suffix>` definitions.

    Auth families, pagination styles, connector kinds, and transport types are
    each modelled as one discriminated `$def` per variant (e.g. `ApiKeyAuth`,
    `CursorPagination`, `DatabaseConnector`, `AdbcTransport`), pinning
    `properties.<const_field>.const` — not a single flat enum. The discriminator
    field name varies: `type` for auth/pagination, `kind` for connectors,
    `transport_type` for transports.

    Matching is by `$def`-name suffix (pydantic derives `$def` keys from the
    variant class names), so this couples to generated-schema naming. Returns
    None when the suffix matches nothing, or no matched def carries the const —
    i.e. the per-variant modelling changed — so the caller routes it through the
    "contract was restructured" branch instead of misreporting total enum-drift
    (and it can never silently equal an empty expected set).
    """
    out: set[str] = set()
    for name, node in schema.get("$defs", {}).items():
        if name.endswith(def_suffix) and isinstance(node, dict):
            const_node = (node.get("properties") or {}).get(const_field) or {}
            if "const" in const_node:
                out.add(const_node["const"])
    return out or None


def _bare_marker_arrow_types() -> set[str] | None:
    """The authored-shape container markers, read from the pinned contract.

    `analitiq.contracts.arrow_grammar.CONTAINER_CANONICAL_HEADS` is the single
    definition of the container-canonical set (`{"Object", "List", "Json"}`),
    derived from the vendored engine grammar manifest — the same constant the
    type-map container guard keys off. Returns None if the symbol is gone
    (renamed) or reshaped away from a set of word tokens, so the caller
    surfaces a restructure failure rather than comparing garbage.
    """
    from analitiq.contracts import arrow_grammar

    raw = getattr(arrow_grammar, "CONTAINER_CANONICAL_HEADS", None)
    if not isinstance(raw, frozenset) or not all(
        isinstance(m, str) and re.fullmatch(r"\w+", m) for m in raw
    ):
        return None
    return set(raw)


# A slug-flavored charset literal: a character class opening with `a-z0-9`,
# plus any following classes / quantifiers / `$` anchor. Matches the exact
# SLUG_PATTERN and the realistic paraphrases of it (`[a-z0-9_-]+`, a bare
# `[a-z0-9]`) — a respelled class like `[0-9a-z_-]` evades it, the accepted
# limit of a lexical detector — while the `[0-9]+` / `[A-Z]+` classes in
# type-map regex examples are not matched. Line-based: a copy wrapped across
# lines yields a truncated match, which fails the equality test and surfaces
# the site anyway.
_SLUG_LITERAL_RE = re.compile(r"\^?\[a-z0-9[^]]*\](?:\[[^]]*\]|[*+$])*")


def _slug_literal_sites() -> list[tuple[str, int, str]]:
    """Every (relpath, lineno, literal) slug-charset occurrence in the plugin's prose."""
    return [
        (path.relative_to(PLUGIN_ROOT).as_posix(), lineno, match.group(0))
        for path in sorted(PLUGIN_ROOT.rglob("*.md"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for match in _SLUG_LITERAL_RE.finditer(line)
    ]


def _diff_msg(label: str, schema_set: set[str] | None, expected: set[str], fix: str) -> str:
    if schema_set is None:
        return (
            f"{label}: enum not found at the expected pointer — the contract was "
            f"restructured. {fix}"
        )
    return (
        f"{label} drift — {fix} "
        f"schema-only={sorted(schema_set - expected)} "
        f"plugin-only={sorted(expected - schema_set)}"
    )


# ---------------------------------------------------------------------------
# Contract-model drift checks (offline) — the enum sets are read from the
# pinned `analitiq-contract-models` package, generated fresh each run.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def connector_schema() -> dict:
    return _schema(Connector)


@pytest.fixture(scope="module")
def api_endpoint_schema() -> dict:
    return _schema(ApiEndpointDoc)


def test_installed_versions_are_pinned() -> None:
    """Guard the guards: every assertion below is only meaningful at the pin."""
    assert_pinned_versions()


def test_auth_types_match_schema(connector_schema: dict) -> None:
    schema_set = _const_types(connector_schema, "Auth")
    assert schema_set == EXPECTED_AUTH_TYPES, _diff_msg(
        "auth.type",
        schema_set,
        EXPECTED_AUTH_TYPES,
        "update the Supported kinds table in "
        "plugins/analitiq-connector-builder/README.md and AuthTypeMapper in "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/enum-mappers.md.",
    )


def test_adbc_drivers_match_schema(connector_schema: dict) -> None:
    schema_set = _enum_at(
        connector_schema, "$defs", "AdbcTransport", "properties", "driver"
    )
    assert schema_set == EXPECTED_ADBC_DRIVERS, _diff_msg(
        "AdbcTransport.driver",
        schema_set,
        EXPECTED_ADBC_DRIVERS,
        "the members render from `RULE-CTOR-016` (mechanism literal_enum) into "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/rules.md "
        "— re-render it, then re-read the tiered decision order in "
        "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-driver-selection.md, "
        "whose tiers reason from which drivers are members.",
    )


def test_sqlalchemy_driver_pattern_matches_schema(connector_schema: dict) -> None:
    pattern = _pattern_at(
        connector_schema, "$defs", "SqlAlchemyTransport", "properties", "driver"
    )
    fix = (
        "update the driver guidance (spec-driver-selection.md, "
        "spec-dsn-bindings.md, db-connector-creator.md, io-contracts.md, and "
        "the `redshift+redshift_connector` carve-out in enum-mappers.md "
        "§TransportTypeMapper), the canon extraction in "
        "scripts/check_validator_pin_contract.py, and "
        "EXPECTED_SQLALCHEMY_DRIVER_PATTERN together."
    )
    if pattern is None:
        pytest.fail(
            "SqlAlchemyTransport.driver: pattern not found at the expected "
            f"pointer — the contract was restructured. {fix}"
        )
    assert pattern == EXPECTED_SQLALCHEMY_DRIVER_PATTERN, (
        f"SqlAlchemyTransport.driver pattern drift — {fix} "
        f"schema={pattern!r} expected={EXPECTED_SQLALCHEMY_DRIVER_PATTERN!r}"
    )


def test_dsn_encodings_match_schema(connector_schema: dict) -> None:
    schema_set = _enum_at(
        connector_schema, "$defs", "DsnBinding", "properties", "encoding"
    )
    assert schema_set == EXPECTED_DSN_ENCODINGS, _diff_msg(
        "DsnBinding.encoding",
        schema_set,
        EXPECTED_DSN_ENCODINGS,
        "update the table under `## Choosing an encoding` in "
        "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-dsn-bindings.md.",
    )


# Every prose site restating a write-path vocabulary. `spec-driver-selection.md`
# is on the list because its tier-3 table names the mechanisms verbatim, and
# `io-contracts.md` because `qualified_statement_targeting` is a BOOLEAN carrier
# for the two-valued `session_targeting` — widening that enum leaves the
# ProviderFacts fragment unable to express the new value.
_WRITE_PATH_FIX = (
    "update the decision tables in "
    "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-sql-write-path.md, "
    "the mechanism table in "
    "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-driver-selection.md, "
    "the authoring order in "
    "plugins/analitiq-connector-builder/agents/db-connector-creator.md, "
    "every archetype under "
    "plugins/analitiq-connector-builder/skills/connector-spec-db/examples/, "
    "and the ProviderFacts carriers in "
    "plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md."
)


def test_driver_selection_mechanism_table_matches_the_contract() -> None:
    """`spec-driver-selection.md` maps a researched protocol onto the closed set.

    That mapping is the sanctioned decision-logic copy — the whole point of the
    tier-3 section after the per-system table was removed — so the names in it
    must be exactly the dialect-implemented mechanisms. `adbc_ingest` is
    excluded: it belongs to tier 1 and obliges no dialect code, which is why the
    SQLAlchemy family IS the dialect-implemented set.
    """
    expected = EXPECTED_SQL_BULK_MECHANISMS["sqlalchemy"]
    cells = _table_cells_containing("copy_from", DRIVER_SELECTION_SPEC)
    assert cells is not None, (
        f"no single table in {DRIVER_SELECTION_SPEC.relative_to(REPO_ROOT)} has "
        "a `copy_from` row — the mechanism table was restructured (or a second "
        "table now claims it), so this guard would have passed vacuously."
    )
    rows = set(cells)
    assert rows == expected, (
        f"the mechanism table in {DRIVER_SELECTION_SPEC.relative_to(REPO_ROOT)} "
        f"names different mechanisms than the contract — "
        f"prose-only={sorted(rows - expected)} "
        f"contract-only={sorted(expected - rows)}. {_WRITE_PATH_FIX}"
    )


def _required_at(schema: dict, def_name: str) -> set[str] | None:
    """The `required` set of a `$def`, or None when the def/list is gone.

    Deliberately not routed through `_enum_at`/`_diff_msg`'s enum wording: a
    required set is not an enum, and reporting "enum not found at the expected
    pointer" for a renamed `$def` sends the reader looking for the wrong thing.
    """
    node = (schema.get("$defs") or {}).get(def_name)
    if not isinstance(node, dict) or not isinstance(node.get("required"), list):
        return None
    return set(node["required"])


def _properties_at(schema: dict, def_name: str) -> set[str] | None:
    """The `properties` key set of a `$def`, or None when the def is gone.

    The sibling of `_required_at`, and the half that catches a member the
    contract added as OPTIONAL: such a member is in no `required` list, so it
    reaches no prose and quietly falsifies any "this is the whole block" claim.
    """
    node = (schema.get("$defs") or {}).get(def_name)
    if not isinstance(node, dict) or not isinstance(node.get("properties"), dict):
        return None
    return set(node["properties"])


def _set_diff_msg(
    label: str,
    found: set[str] | None,
    expected: set[str],
    fix: str = _WRITE_PATH_FIX,
) -> str:
    """Diff message for a NAME set (required list or property keys).

    Separate from `_diff_msg` because these are not enums: reporting "enum not
    found at the expected pointer" for a renamed `$def` sends the reader
    looking for the wrong thing. The missing-node wording is deliberately
    generic ("not found at that $def") so the same helper can serve both a
    `required` list and a `properties` key set without lying about which it
    read.

    `fix` names the documents whose prose goes false with this set. It defaults
    to the write-path group because most callers here read a write-path
    vocabulary; a caller reading a set some other document closes over passes
    its own, so the failure sends the reader to the document that must change
    rather than to the majority's.
    """
    if found is None:
        return (
            f"{label}: not found at that $def — the contract was "
            f"restructured. {fix}"
        )
    return (
        f"{label} drift — {fix} "
        f"schema-only={sorted(found - expected)} "
        f"plugin-only={sorted(expected - found)}"
    )


def test_sql_capability_facts_match_schema(connector_schema: dict) -> None:
    """The shape facts a declared `sql_capabilities` block must carry.

    `limits` is excluded deliberately — it is the additive member, and the
    prose says so. If it ever became required (or a required fact became
    optional), "a declared block is complete" would stop being true.

    The property assertion is the other half: `required` alone cannot see a
    NEW optional member, which reaches no prose and quietly falsifies the
    spec's claim that its declaration table is the whole block.
    """
    required = _required_at(connector_schema, "SqlCapabilities")
    assert required == EXPECTED_SQL_CAPABILITY_FACTS, _set_diff_msg(
        "sql_capabilities required facts", required, EXPECTED_SQL_CAPABILITY_FACTS
    )
    props = _properties_at(connector_schema, "SqlCapabilities")
    assert props == EXPECTED_SQL_CAPABILITY_PROPERTIES, _set_diff_msg(
        "sql_capabilities properties", props, EXPECTED_SQL_CAPABILITY_PROPERTIES
    )


def test_sql_stage_facts_match_schema(connector_schema: dict) -> None:
    """Same claim, one level down: the stage block's required fields.

    `spec-sql-write-path.md` presents `scope` / `schema` / `transactional_ddl`
    as the complete stage declaration and `db-connector-creator.md` checklists
    against it. Without this, making `transactional_ddl` optional (the fact an
    author is most likely to want to skip) leaves the whole plugin suite green.
    `dedicated_schema` is conditional, so it is never in `required`.
    """
    required = _required_at(connector_schema, "SqlStageCapabilities")
    assert required == EXPECTED_SQL_STAGE_FACTS, _set_diff_msg(
        "sql_capabilities.stage required facts", required, EXPECTED_SQL_STAGE_FACTS
    )
    # `dedicated_schema` is conditional, so `required` alone cannot catch it
    # being renamed out from under the prose's required-iff rule.
    props = _properties_at(connector_schema, "SqlStageCapabilities")
    assert props == EXPECTED_SQL_STAGE_PROPERTIES, _set_diff_msg(
        "sql_capabilities.stage properties", props, EXPECTED_SQL_STAGE_PROPERTIES
    )


def test_sql_catalog_modes_match_schema(connector_schema: dict) -> None:
    schema_set = _enum_at(
        connector_schema, "$defs", "SqlCapabilities", "properties", "catalog"
    )
    assert schema_set == EXPECTED_SQL_CATALOG_MODES, _diff_msg(
        "sql_capabilities.catalog", schema_set, EXPECTED_SQL_CATALOG_MODES,
        _WRITE_PATH_FIX,
    )


def test_sql_session_targeting_matches_schema(connector_schema: dict) -> None:
    schema_set = _enum_at(
        connector_schema, "$defs", "SqlCapabilities", "properties", "session_targeting"
    )
    assert schema_set == EXPECTED_SQL_SESSION_TARGETING, _diff_msg(
        "sql_capabilities.session_targeting", schema_set,
        EXPECTED_SQL_SESSION_TARGETING, _WRITE_PATH_FIX,
    )


def test_sql_merge_forms_match_schema(connector_schema: dict) -> None:
    """The upsert grammars — each obliging `merge_statement_sql` except `none`."""
    schema_set = _enum_at(
        connector_schema, "$defs", "SqlCapabilities", "properties", "merge_form"
    )
    assert schema_set == EXPECTED_SQL_MERGE_FORMS, _diff_msg(
        "sql_capabilities.merge_form", schema_set, EXPECTED_SQL_MERGE_FORMS,
        _WRITE_PATH_FIX,
    )


def test_sql_stage_vocabularies_match_schema(connector_schema: dict) -> None:
    scopes = _enum_at(
        connector_schema, "$defs", "SqlStageCapabilities", "properties", "scope"
    )
    assert scopes == EXPECTED_SQL_STAGE_SCOPES, _diff_msg(
        "sql_capabilities.stage.scope", scopes, EXPECTED_SQL_STAGE_SCOPES,
        _WRITE_PATH_FIX,
    )
    # Wire name, not the Python `schema_` alias.
    schemas = _enum_at(
        connector_schema, "$defs", "SqlStageCapabilities", "properties", "schema"
    )
    assert schemas == EXPECTED_SQL_STAGE_SCHEMAS, _diff_msg(
        "sql_capabilities.stage.schema", schemas, EXPECTED_SQL_STAGE_SCHEMAS,
        _WRITE_PATH_FIX,
    )


def test_sql_bulk_mechanisms_match_schema(connector_schema: dict) -> None:
    """Per-transport bulk mechanisms, pinned per family.

    Comparing a flattened union would let `adbc_ingest` migrate onto the
    SQLAlchemy family unnoticed — and the prose's "involves no dialect code"
    carve-out is stated for the ADBC family alone.
    """
    families = _properties_at(connector_schema, "SqlBulkLoad")
    # Iterating EXPECTED alone is one-directional: a REMOVED family surfaces (as
    # a restructure), an ADDED one is invisible. The prose states the family set
    # as closed, so pin the set itself.
    assert families == set(EXPECTED_SQL_BULK_MECHANISMS), _set_diff_msg(
        "sql_capabilities.bulk_load families", families, set(EXPECTED_SQL_BULK_MECHANISMS)
    )
    for family, expected in EXPECTED_SQL_BULK_MECHANISMS.items():
        schema_set = _enum_at(
            connector_schema, "$defs", "SqlBulkLoad", "properties", family
        )
        assert schema_set == expected, _diff_msg(
            f"sql_capabilities.bulk_load.{family}", schema_set, expected,
            _WRITE_PATH_FIX,
        )


def test_sql_limit_caps_match_schema(connector_schema: dict) -> None:
    """`limits` member names, restated in spec-sql-write-path.md + io-contracts.md.

    Both caps are optional, so neither appears in a `required` list, and
    `max_bind_params` appears in no shipped example — a rename would leave the
    plugin suite green with two prose sites naming a field that no longer
    exists.
    """
    caps = _properties_at(connector_schema, "SqlLimits")
    assert caps == EXPECTED_SQL_LIMIT_CAPS, _set_diff_msg(
        "sql_capabilities.limits caps", caps, EXPECTED_SQL_LIMIT_CAPS
    )


# --- the write-path pins, read from the PROSE side --------------------------
# Every other pin in this file is one-sided: contract -> a constant here. That
# leaves a hole: update the contract AND the constant, forget the spec table,
# and the suite stays green while the document an agent actually reads has gone
# stale. `test_slug_pattern_restatements_*`
# already closes that loop for the slug charset by reading prose; these do the
# same for the write-path vocabularies.

WRITE_PATH_SPEC = (
    PLUGIN_ROOT / "skills" / "connector-spec-db" / "spec-sql-write-path.md"
)
DRIVER_SELECTION_SPEC = (
    PLUGIN_ROOT / "skills" / "connector-spec-db" / "spec-driver-selection.md"
)

# `| `<label>` | `a` / `b` / `c` | …` — the label cell, then the values cell.
# Only the SECOND cell is read: the "How to choose" cell is full of backticked
# SQL and field names that are not vocabulary members.
_TABLE_ROW = re.compile(r"^\|([^|]*)\|([^|]*)\|")
_BACKTICKED = re.compile(r"`([^`]+)`")


def _table_blocks(spec: Path | None = None) -> list[list[str]]:
    """Maximal runs of consecutive `|`-leading lines — one per markdown table."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in (spec or WRITE_PATH_SPEC).read_text(encoding="utf-8").splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _row_cells(block: list[str]) -> dict[str, str]:
    """`{row label: Values-cell text}` for one markdown table.

    THE one label rule for this module. A row's label is the FIRST code span
    in its label cell, not its only one: requiring exactly one silently
    dropped any row whose label cell also carried a cross-reference, and a
    dropped row is invisible to every caller — so a fact could be added to a
    table a guard calls complete in a form that guard never reads. Header and
    separator rows carry no code span and still fall out.

    Every table reader below routes through this. They previously each
    inlined the same walk with two different label rules, which is the drift
    this module exists to prevent, one level up.
    """
    return {
        found[0]: m.group(2).strip()
        for row in block
        if (m := _TABLE_ROW.match(row)) and (found := _BACKTICKED.findall(m.group(1)))
    }


def _table_cells_containing(label: str, spec: Path | None = None) -> dict[str, str] | None:
    """`_row_cells` for the one table in `spec` whose rows include `label`.

    Both directions, unlike a subset check: a contract fact with no prose row
    fails, and a prose row for a fact the contract dropped fails too. Without
    it the fact NAMES are pinned contract-side only — so making `limits`
    required would fail one test, the fixer would add it to the expected set,
    and the prose claiming the block is complete would stay green. That is the
    contract-moves-prose-doesn't two-step, in miniature.

    None when no single table matches, so a restructure (or two tables both
    claiming the label) surfaces as an explicit failure rather than a pass
    against an empty set.
    """
    tables = [
        cells
        for block in _table_blocks(spec)
        if label in (cells := _row_cells(block))
    ]
    return tables[0] if len(tables) == 1 else None


def _labels_of_table_containing(label: str) -> set[str] | None:
    """Just the label column of `_table_cells_containing`."""
    cells = _table_cells_containing(label)
    return None if cells is None else set(cells)


def _vocabulary_rows(spec: Path | None = None) -> list[tuple[str, set[str]]]:
    """Every (label, values) row whose Values cell holds backticked tokens.

    Flattened across tables — callers key on the label, and `_documented_values`
    fails loudly when one label appears in more than one row.
    """
    return [
        (label, tokens)
        for block in _table_blocks(spec)
        for label, values in _row_cells(block).items()
        if (tokens := set(_BACKTICKED.findall(values)))
    ]


def _documented_values(label: str) -> set[str] | None:
    """Backticked tokens in the Values cell of the row labelled `label`.

    None when no such row exists — the table was restructured or the row
    renamed, which the caller turns into an explicit failure rather than a
    vacuous pass against an empty set.

    Collects ALL matching rows rather than returning the first: a summary
    table added above the real one could otherwise shadow it with a stale
    copy, and this guard exists precisely to never pass vacuously.
    """
    matches = [values for row_label, values in _vocabulary_rows() if row_label == label]
    if len(matches) != 1:
        return None
    return matches[0]


#: The write-path vocabularies the spec's tables must state, label -> members.
#: The recall pin below derives its floor from this rather than hand-stating a
#: length, so adding an entry raises the floor instead of leaving it stale.
PINNED_WRITE_PATH_VOCABULARIES = [
    ("catalog", EXPECTED_SQL_CATALOG_MODES),
    ("session_targeting", EXPECTED_SQL_SESSION_TARGETING),
    ("merge_form", EXPECTED_SQL_MERGE_FORMS),
    ("scope", EXPECTED_SQL_STAGE_SCOPES),
    ("schema", EXPECTED_SQL_STAGE_SCHEMAS),
    ("sqlalchemy", EXPECTED_SQL_BULK_MECHANISMS["sqlalchemy"]),
    ("adbc", EXPECTED_SQL_BULK_MECHANISMS["adbc"]),
]


@pytest.mark.parametrize("label, expected", PINNED_WRITE_PATH_VOCABULARIES)
def test_write_path_spec_tables_state_the_pinned_vocabularies(
    label: str, expected: set[str]
) -> None:
    """The spec's own tables must list exactly what the contract defines.

    Paired with the contract-side tests above, this makes the vocabularies
    genuinely single-sourced: contract -> constant -> prose, all three checked.
    """
    documented = _documented_values(label)
    assert documented is not None, (
        f"no `{label}` row with a backticked Values cell found in "
        f"{WRITE_PATH_SPEC.relative_to(REPO_ROOT)} — the table was "
        "restructured, so this guard would have passed vacuously. Restore the "
        "row or re-anchor the parser."
    )
    assert documented == expected, (
        f"`{label}` values in {WRITE_PATH_SPEC.relative_to(REPO_ROOT)} disagree "
        f"with the pinned contract set — "
        f"prose-only={sorted(documented - expected)} "
        f"contract-only={sorted(expected - documented)}. {_WRITE_PATH_FIX}"
    )


@pytest.mark.parametrize(
    "anchor, expected",
    [
        # A row per PROPERTY, optional and conditional ones included — the
        # prose calls `limits` additive and `dedicated_schema` conditional, but
        # each still needs a row. These expectations are hand-written literals,
        # so the protection is a CHAIN, not a derivation: a new optional member
        # first fails the contract-side properties pin, and widening that
        # literal — the only way to clear it — then fails this guard until the
        # table gains a row. Neither hop alone is enough.
        ("catalog", EXPECTED_SQL_CAPABILITY_PROPERTIES),
        ("scope", EXPECTED_SQL_STAGE_PROPERTIES),
        ("sqlalchemy", set(EXPECTED_SQL_BULK_MECHANISMS)),
    ],
)
def test_write_path_spec_tables_state_the_pinned_fact_names(
    anchor: str, expected: set[str]
) -> None:
    """The spec's tables must name exactly the facts the contract defines."""
    documented = _labels_of_table_containing(anchor)
    assert documented is not None, (
        f"no single table in {WRITE_PATH_SPEC.relative_to(REPO_ROOT)} has a "
        f"`{anchor}` row — the spec was restructured (or two tables now claim "
        "the same fact), so this guard would have passed vacuously."
    )
    assert documented == expected, (
        f"the `{anchor}` table in {WRITE_PATH_SPEC.relative_to(REPO_ROOT)} "
        f"names different facts than the contract — "
        f"prose-only={sorted(documented - expected)} "
        f"contract-only={sorted(expected - documented)}. {_WRITE_PATH_FIX}"
    )


def _prose_diff_msg(label: str, documented: set[str], expected: set[str]) -> str:
    """Failure text for a PROSE-side comparison.

    `_set_diff_msg` cannot serve: its `schema-only` / `plugin-only` labels
    assume the schema is the `found` argument, which is true of its
    contract-side callers and inverted here — it would tell the reader the
    plugin carries a fact the schema lacks when the prose is the side that
    dropped one.
    """
    return (
        f"{label} in {WRITE_PATH_SPEC.relative_to(REPO_ROOT)} disagrees with "
        f"the contract — prose-only={sorted(documented - expected)} "
        f"contract-only={sorted(expected - documented)}. {_WRITE_PATH_FIX}"
    )


@pytest.mark.parametrize(
    "anchor, table, required, properties",
    [
        (
            "catalog",
            "declaration",
            EXPECTED_SQL_CAPABILITY_FACTS,
            EXPECTED_SQL_CAPABILITY_PROPERTIES,
        ),
        (
            "scope",
            "stage",
            EXPECTED_SQL_STAGE_FACTS,
            EXPECTED_SQL_STAGE_PROPERTIES,
        ),
    ],
)
def test_write_path_spec_marks_exactly_the_optional_facts_optional(
    anchor: str, table: str, required: set[str], properties: set[str]
) -> None:
    """A table's required/optional split must match the contract's.

    This stands in for a count. The spec used to assert completeness as a
    cardinality ("all five shape facts are required"), which no guard could
    check: adding a sixth required fact updates the contract and
    `EXPECTED_SQL_CAPABILITY_FACTS` together, and the sentence stays green while
    saying something false. The prose now defers to each table's own
    required/optional marking, which is only meaningful while the split holds.

    Both tables, not just the declaration one. The stage table makes the same
    claim about `dedicated_schema` ("never in the block's required set"), and
    guarding one level and not the other is how the two-step this module exists
    to stop gets back in: the contract-side pin fails alone, the fixer widens
    the literal, and the table keeps advertising the old split.

    Note what this does NOT do: it reads the table, never the sentence above it.
    Restoring the count to that sentence leaves this green. What it buys is that
    the mechanism the sentence delegates to cannot rot silently —
    `test_write_path_spec_tables_state_the_pinned_fact_names` covers the row
    set, and this covers how those rows are marked.

    Both directions matter. A fact silently gaining "optional" in its Values
    cell would narrow the rule the agent applies; an additive member losing the
    marker would widen it into demanding a block the contract calls optional.
    """
    cells = _table_cells_containing(anchor)
    assert cells is not None, (
        f"no single table in {WRITE_PATH_SPEC.relative_to(REPO_ROOT)} has a "
        f"`{anchor}` row — the {table} table was restructured (or two tables "
        "now claim it), so this guard would have passed vacuously."
    )
    # "conditional" is the stage table's word for a member that is required
    # only under another field's value, so it is likewise never in `required`.
    documented_optional = {
        fact
        for fact, values in cells.items()
        if "optional" in values.lower() or "conditional" in values.lower()
    }
    documented_required = set(cells) - documented_optional
    assert documented_required == required, _prose_diff_msg(
        f"facts the {table} table leaves unmarked as optional",
        documented_required,
        required,
    )
    # Computed as properties-minus-required rather than spelled out, so the
    # optional set cannot be edited independently of the two sets that are
    # themselves pinned to the contract. Both are still literals — widening
    # them is the deliberate step that brings a new member here.
    expected_optional = properties - required
    assert documented_optional == expected_optional, _prose_diff_msg(
        f"facts the {table} table marks optional",
        documented_optional,
        expected_optional,
    )


def test_write_path_spec_example_matches_the_contract() -> None:
    """The spec's worked example must be a document the contract accepts.

    It is the ONLY artifact in the repo covering `insert_on_duplicate_key`, an
    empty `bulk_load`, and `transactional_ddl: false` — both shipped archetypes
    declare the Postgres-shaped combination — and an agent copies it verbatim.
    Nothing else validates a fenced code block.
    """
    from analitiq.contracts.connector import SqlCapabilities

    fences = re.findall(
        r"```json\n(.*?)```", WRITE_PATH_SPEC.read_text(encoding="utf-8"), re.S
    )
    examples = [
        block["sql_capabilities"]
        for fence in fences
        # The fence is an excerpt (`"sql_capabilities": {...}`), so wrap it back
        # into an object before parsing.
        if "sql_capabilities" in (block := json.loads("{" + fence.strip() + "}"))
    ]
    assert examples, (
        f"no `sql_capabilities` json fence found in "
        f"{WRITE_PATH_SPEC.relative_to(REPO_ROOT)} — the worked example moved "
        "or changed shape, so this guard is checking nothing."
    )
    for example in examples:
        SqlCapabilities.model_validate(example)


def test_write_path_spec_code_examples_parse() -> None:
    """The spec's Python examples must be syntactically valid.

    Same reasoning as the JSON guard above: agents copy these blocks
    verbatim, and a template that does not parse is worse than no template.
    Each block is a class-level excerpt, so the placeholder dialect name and
    the CDK imports are stubbed before parsing — this checks syntax, which is
    the part a reader cannot eyeball reliably, not resolvability.
    """
    blocks = re.findall(
        r"```python\n(.*?)```", WRITE_PATH_SPEC.read_text(encoding="utf-8"), re.S
    )
    assert blocks, (
        f"no python fences in {WRITE_PATH_SPEC.relative_to(REPO_ROOT)} — the "
        "worked examples moved, so this guard is checking nothing."
    )
    preamble = (
        "from collections.abc import Sequence\n"
        "def await_only(x): pass\n"
        "class SqlDialect: pass\n"
        "class TableAddress: pass\n"
    )
    for block in blocks:
        # `{Name}` is the spec's placeholder for the connector's class prefix.
        compile(preamble + block.replace("{Name}", "Example"), "<spec>", "exec")


def test_write_path_table_parser_reads_the_real_tables() -> None:
    """Pin the parsers: their recall is what the table checks above stand on.

    Recall first — a parser that matched nothing would leave the vocabulary
    checks resting on their `is None` guard, which is a real failure but for the
    wrong reason, and a future "simplification" could pass the negative cases
    below while reading no rows at all. (The required/optional check does not
    need this: both its assertions are equalities against non-empty sets, so a
    parser reading zero rows fails on its own.)
    """
    rows = _vocabulary_rows()
    floor = len(PINNED_WRITE_PATH_VOCABULARIES)
    assert len(rows) >= floor, (
        f"the parser found only {len(rows)} vocabulary rows in "
        f"{WRITE_PATH_SPEC.relative_to(REPO_ROOT)} — it must read at least the "
        f"{floor} the tests above pin, or those tests are resting on "
        "None-guards."
    )
    assert _documented_values("catalog") == EXPECTED_SQL_CATALOG_MODES

    # Precision: a Values cell that is prose, not vocabulary. `bulk_load` reads
    # "per-transport object" — no backticks, so it must come back None rather
    # than an empty set masquerading as a match.
    assert _documented_values("bulk_load") is None
    # A label that does not exist at all.
    assert _documented_values("no_such_field") is None
    # A header row carries no code span at all, so it is never a vocabulary row.
    assert _documented_values("Fact") is None

    # `_row_cells` keys on the FIRST code span, so a label cell carrying a
    # cross-reference stays visible — a dropped row is a fact the completeness
    # guard never sees. Call the real helper: building the same comprehension
    # here would grade a copy of the rule, and reverting `_row_cells` to the old
    # "exactly one code span" would leave this green.
    block = ["| `hints` (`spec-hints.md`) | object, optional | Tuning. |"]
    assert set(_row_cells(block)) == {"hints"}, (
        "a table row whose label cell carries a cross-reference must still be "
        "read, keyed on its first code span"
    )
    # No table claims a label that does not exist, and the caller turns that
    # None into an explicit failure rather than a vacuous pass.
    assert _table_cells_containing("no_such_fact") is None


# --- closure claims, pinned to the shapes they close over -------------------
# Prose that says "and nothing else" / "exactly these" is a claim about a
# CLOSED member set. It is strictly more falsifiable than a count — a count goes
# stale, a closure claim goes actively wrong. The member set each one closes
# over is compared to the contract below, and the failure names the document
# whose sentence goes false with it; whether that sentence still closes the set
# is a reader's call (`.claude/rules/no-cardinality-restatements.md` §Closure
# claims). Without the comparisons the sets are unowned: `Replication`,
# `ResourceDiscoveryTriggers` and the type-map rule keys appear in no other
# guard in this suite.

REPLICATION_SPEC = PLUGIN_ROOT / "skills" / "connector-spec-api" / "spec-replication.md"
DISCOVERY_SPEC = (
    PLUGIN_ROOT / "skills" / "connector-spec-db" / "spec-resource-discovery.md"
)
TYPE_MAPS_SPEC = PLUGIN_ROOT / "skills" / "connector-spec-db" / "spec-type-maps.md"
IO_CONTRACTS = (
    PLUGIN_ROOT / "skills" / "connector-builder" / "references" / "io-contracts.md"
)

EXPECTED_REPLICATION_KEYS = {"supported_methods", "cursor_mappings"}
EXPECTED_DISCOVERY_ACTIONS = {"list_resources", "describe_resource"}
EXPECTED_TYPE_MAP_RULE_KEYS = {"match", "native", "canonical"}


# Each of the three specs above closes its set with an exhaustive-enumeration
# sentence, and `_CLOSURE_FIX` is what sends a failing comparison back to the
# right one. Grading such a sentence stays with the reader: locating it takes an
# English anchor, which stops matching when the sentence is reworded, and
# grading it takes a closure phrase, which `.claude/rules/guards.md`
# keeps out of tests. What a mechanism decides is the contract's own member
# sets, asserted below, each failing with the document that must be re-read.
_CLOSURE_FIX = (
    "re-read the closure sentence in {spec} — it enumerates this set as "
    "exhaustive, and an authoring agent reads it that way."
)


def test_replication_keys_match_schema(api_endpoint_schema: dict) -> None:
    """The contract side of the `spec-replication.md` closure claim.

    Both `properties` and `required`: the spec's bullet says the block carries
    these keys AND that each is required. Pinning properties alone would let
    either move to optional with the "each required" claim still green — the
    same half-measure that left a new optional `SqlCapabilities` member
    invisible.
    """
    fix = _CLOSURE_FIX.format(spec=REPLICATION_SPEC.relative_to(REPO_ROOT))
    props = _properties_at(api_endpoint_schema, "Replication")
    assert props == EXPECTED_REPLICATION_KEYS, _set_diff_msg(
        "Replication properties", props, EXPECTED_REPLICATION_KEYS, fix
    )
    required = _required_at(api_endpoint_schema, "Replication")
    assert required == EXPECTED_REPLICATION_KEYS, _set_diff_msg(
        "Replication required", required, EXPECTED_REPLICATION_KEYS, fix
    )


def test_discovery_actions_match_schema(connector_schema: dict) -> None:
    """The contract side of the `spec-resource-discovery.md` closure claim."""
    props = _properties_at(connector_schema, "ResourceDiscoveryTriggers")
    assert props == EXPECTED_DISCOVERY_ACTIONS, _set_diff_msg(
        "ResourceDiscoveryTriggers properties",
        props,
        EXPECTED_DISCOVERY_ACTIONS,
        _CLOSURE_FIX.format(spec=DISCOVERY_SPEC.relative_to(REPO_ROOT)),
    )


def test_db_creator_step_accounts_for_every_shape_fact() -> None:
    """The authoring step must name every fact it tells the agent to declare.

    Removing the count left the sub-bullets as the only place this agent
    enumerates the fact set, and nothing read them: deleting the `merge_form`
    bullet outright kept the suite green while the agent stopped authoring a
    required fact. `_WRITE_PATH_FIX` names this file as somewhere to go fix,
    but a fix-hint is not a guard.

    Scoped to the ONE numbered step that authors `sql_capabilities`, so a fact
    mentioned elsewhere in the document cannot stand in for the guidance. A
    fact is "named" by its own bullet or by a dotted child (`stage.scope`
    accounts for `stage`), matching how the step is written.
    """
    doc = PLUGIN_ROOT / "agents" / "db-connector-creator.md"
    step = re.search(
        r"^\d+\.\s+\*\*SQL write-path capabilities\*\*(.*?)(?=^\d+\. )",
        doc.read_text(encoding="utf-8"),
        re.M | re.S,
    )
    assert step, (
        f"{doc.name}: no `SQL write-path capabilities` numbered step — the "
        "authoring order was restructured; re-scope this gate."
    )
    tokens = set(_BACKTICKED.findall(step.group(1)))
    missing = {
        fact
        for fact in EXPECTED_SQL_CAPABILITY_FACTS
        if not any(t == fact or t.startswith(f"{fact}.") for t in tokens)
    }
    assert not missing, (
        f"{doc.name}'s `sql_capabilities` step never names {sorted(missing)} — "
        "the agent is told the block must be complete but not what to put in "
        f"it. {_WRITE_PATH_FIX}"
    )


def test_cursor_mapping_variants_match_the_spec() -> None:
    """`spec-replication.md` enumerates the cursor-mapping variants by `$defs` id.

    `CursorMapping` is a discriminated union, so a third variant is a plausible
    contract change — and the page presents its list as the choice an author
    picks from. Read the union's members and compare to the ids the page cites.

    Scoped to that top-of-page bullet list, not the whole file: a passing
    mention deep in a gotcha paragraph would otherwise keep this green while
    the list an author actually chooses from lost a variant. Same reasoning as
    scoping the shape-fact guard to one numbered step.
    """
    from analitiq.contracts.endpoints import CursorMapping

    schema = TypeAdapter(CursorMapping).json_schema()
    variants = {
        ref.rsplit("/", 1)[-1]
        for branch in schema.get("oneOf") or schema.get("anyOf") or []
        if (ref := branch.get("$ref"))
    }
    assert variants, (
        "no `$ref` branches found for CursorMapping — the union was "
        "restructured, so this guard would have passed vacuously."
    )
    bullets = [
        line
        for line in REPLICATION_SPEC.read_text(encoding="utf-8").splitlines()
        if line.startswith("- `#/$defs/")
    ]
    assert bullets, (
        f"no `- #/$defs/…` choice bullets in "
        f"{REPLICATION_SPEC.relative_to(REPO_ROOT)} — the page was "
        "restructured, so this guard would have passed vacuously."
    )
    cited = {
        token.rsplit("/", 1)[-1]
        for token in _BACKTICKED.findall("\n".join(bullets))
        # The union itself is cited on the same list; only its members count.
        if token.startswith("#/$defs/")
        and token.endswith("CursorMapping")
        and token != "#/$defs/CursorMapping"
    }
    assert cited == variants, (
        f"{REPLICATION_SPEC.relative_to(REPO_ROOT)} cites different cursor "
        f"mapping variants than the contract — prose-only={sorted(cited - variants)} "
        f"contract-only={sorted(variants - cited)}. A new variant must reach "
        "the page that teaches an author how to choose one."
    )


def test_type_map_rule_keys_match_schema_and_prose() -> None:
    """`spec-type-maps.md` says a rule carries "exactly the keys named below".

    Both sides: the contract's rule keys, and the Key column of the table the
    sentence defers to. Nothing else in this suite reads either.

    EVERY variant, not just the read/exact one. The table the sentence points
    at carries a Write-map column, so the claim spans all four; checking one
    would leave a key added to a write variant — the likelier direction, since
    `canonical` is already overridden per variant — falsifying the sentence
    with the suite green.
    """
    from analitiq.contracts.type_map import (
        TypeMapReadExactRule,
        TypeMapReadRegexRule,
        TypeMapWriteExactRule,
        TypeMapWriteRegexRule,
    )

    for rule in (
        TypeMapReadExactRule,
        TypeMapReadRegexRule,
        TypeMapWriteExactRule,
        TypeMapWriteRegexRule,
    ):
        contract_keys = set(TypeAdapter(rule).json_schema()["properties"])
        assert contract_keys == EXPECTED_TYPE_MAP_RULE_KEYS, _set_diff_msg(
            f"{rule.__name__} keys",
            contract_keys,
            EXPECTED_TYPE_MAP_RULE_KEYS,
            _CLOSURE_FIX.format(spec=TYPE_MAPS_SPEC.relative_to(REPO_ROOT)),
        )
    # Whether the sentence above the table still CLOSES the set is a reader's
    # call, per `.claude/rules/no-cardinality-restatements.md`. The table it
    # defers to is structural, so that half is decidable and stays here.
    cells = _table_cells_containing("match", TYPE_MAPS_SPEC)
    assert cells is not None, (
        f"no single table in {TYPE_MAPS_SPEC.relative_to(REPO_ROOT)} has a "
        "`match` row — the rule-shape table was restructured (or two tables "
        "now claim it), so this guard would have passed vacuously."
    )
    documented = set(cells)
    assert documented == EXPECTED_TYPE_MAP_RULE_KEYS, (
        f"{TYPE_MAPS_SPEC.relative_to(REPO_ROOT)}: the rule-shape table names "
        f"different keys than the contract — "
        f"prose-only={sorted(documented - EXPECTED_TYPE_MAP_RULE_KEYS)} "
        f"contract-only={sorted(EXPECTED_TYPE_MAP_RULE_KEYS - documented)}."
    )


def test_idempotency_targets_match_schema(api_endpoint_schema: dict) -> None:
    schema_set = _enum_at(
        api_endpoint_schema, "$defs", "Idempotency", "properties", "in"
    )
    assert schema_set == EXPECTED_IDEMPOTENCY_TARGETS, _diff_msg(
        "idempotency.in",
        schema_set,
        EXPECTED_IDEMPOTENCY_TARGETS,
        "the placement members render from `RULE-ENDP-039` (mechanism "
        "literal_enum) into "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/rules.md "
        "— re-render it, then re-read the per-placement guidance in "
        "plugins/analitiq-connector-builder/agents/endpoint-creator.md, which "
        "tells the author what each placement means.",
    )


def test_write_modes_match_schema(api_endpoint_schema: dict) -> None:
    schema_set = _property_names_enum(
        api_endpoint_schema, "$defs", "Operations", "properties", "write"
    )
    assert schema_set == EXPECTED_WRITE_MODES, _diff_msg(
        "operations.write keys",
        schema_set,
        EXPECTED_WRITE_MODES,
        "update the write-mode guidance in endpoint-creator.md (which mode(s) to "
        "key, and which the schema permits but an API destination cannot "
        "perform) together with the pipeline plugin's WriteModeMapper.",
    )


def test_endpoint_creator_prose_names_every_write_mode() -> None:
    """The authoring step must account for every mode the contract admits.

    `test_write_modes_match_schema` pins the vocabulary but never opens the
    document, so on its own a mode could be added to the contract,
    `EXPECTED_WRITE_MODES` updated to match, and `endpoint-creator.md` left
    describing a smaller world — the agent would then omit a writable operation
    the provider supports and nothing would say so.

    Naming a mode is not endorsing it: `truncate_insert` is named precisely to
    tell the author not to key it. What this forbids is silence.

    Scoped to the ONE numbered step that authors `operations.write`, so a mode
    named anywhere else cannot stand in for the guidance. Two coarser spellings
    were each measurably bypassable: splitting the whole document left the last
    step running to end of file and swallowing `## Hard rules`, and matching
    every step that merely mentions `operations.write` also caught step 5
    ("at least one of read or write must be present"), so the guidance could be
    gutted and the vocabulary parked in that step instead.
    """
    doc = PLUGIN_ROOT / "agents" / "endpoint-creator.md"
    process = re.search(
        r"^##\s+Process\s*$(.*?)(?=^#|\Z)", doc.read_text(), re.M | re.S
    )
    assert process, (
        f"{doc.name}: no `## Process` section — the agent was restructured; "
        "re-scope this gate."
    )
    steps = [
        block
        for block in re.split(r"(?m)^(?=\d+\. )", process.group(1))
        if re.match(r"\d+\. Author\b.*\boperations\.write\b", block)
    ]
    assert steps, (
        f"{doc.name}: no numbered step authors `operations.write` any more — "
        "the agent was restructured; re-scope this gate."
    )
    step = "\n".join(steps)
    missing = [mode for mode in WRITE_MODES if f"`{mode}`" not in step]
    assert not missing, (
        f"endpoint-creator.md's write step does not mention {missing} — a mode "
        "the contract admits is invisible to the authoring agent. Say whether "
        "to key it or why not; do not leave it unmentioned."
    )


def test_pagination_styles_match_schema(api_endpoint_schema: dict) -> None:
    schema_set = _const_types(api_endpoint_schema, "Pagination")
    assert schema_set == EXPECTED_PAGINATION_STYLES, _diff_msg(
        "pagination style",
        schema_set,
        EXPECTED_PAGINATION_STYLES,
        "update io-contracts.md ProviderFacts and spec-pagination.md.",
    )


def test_pagination_prose_names_every_predicate_key() -> None:
    """spec-pagination.md's predicate-key list is the one agent-loadable copy.

    The `stop_when` / `success_when` operator vocabulary appears nowhere else
    an authoring agent can reach: `endpoint-creator.md` points at
    spec-pagination.md for it, and the agent has no fetch tool to recover a
    spelling from the published schema. Pin the hand-typed list to
    `_PRED_BRANCHES` — the contract module's own single source for the
    predicate union — so a new operator lands in the prose in the same change,
    and a reworded sentence that drops the list turns the build red instead of
    silently stranding the vocabulary.
    """
    from analitiq.contracts.endpoints import _PRED_BRANCHES

    doc = PLUGIN_ROOT / "skills" / "connector-spec-api" / "spec-pagination.md"
    match = re.search(
        r"Predicate keys \(closed set\):(?P<line>.+?)(?:\.\s|\.$)",
        doc.read_text(encoding="utf-8"),
        re.S,
    )
    assert match, (
        f"{doc.name}: the 'Predicate keys (closed set):' sentence is gone — "
        "restore it (or repoint this guard); it is the only copy of the "
        "predicate grammar's key set an authoring agent can load."
    )
    stated = set(re.findall(r"`([a-z_]+)`", match.group("line")))
    expected = {tag for tag, _ in _PRED_BRANCHES}
    assert stated == expected, _diff_msg(
        "predicate key",
        expected,
        stated,
        "update the closed-set sentence in spec-pagination.md §stop_when.",
    )


def test_bare_marker_arrow_types_match_schema() -> None:
    schema_set = _bare_marker_arrow_types()
    assert schema_set == EXPECTED_BARE_MARKER_ARROW_TYPES, _diff_msg(
        "authored_shape_type",
        schema_set,
        EXPECTED_BARE_MARKER_ARROW_TYPES,
        "update the container-shape guidance in "
        "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-type-maps.md.",
    )


def test_kinds_match_schema(connector_schema: dict) -> None:
    schema_set = _const_types(connector_schema, "Connector", const_field="kind")
    assert schema_set == EXPECTED_KINDS, _diff_msg(
        "connector.kind",
        schema_set,
        EXPECTED_KINDS,
        "update the Supported kinds table in "
        "plugins/analitiq-connector-builder/README.md and KindMapper in "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/enum-mappers.md.",
    )


def test_write_coverage_probes_derive_from_the_grammar_manifest() -> None:
    """Every canonical family is probed or explicitly excluded — no third state.

    The probe set is the one restatement of the Arrow vocabulary that used to
    stand on nothing: its coverage was compared against the prose describing
    its own omissions, so a family the engine added went unprobed with the
    suite green. Pin it to the manifest instead, in both directions — a family
    the manifest gains must be probed or named in the exclusion registry, and
    an exclusion naming no family is dead.
    """
    from analitiq.contracts import arrow_grammar
    from analitiq.validator import connectors

    probes = getattr(connectors, "_WRITE_VOCABULARY_PROBES", ())
    excluded = getattr(connectors, "_WRITE_PROBE_EXCLUDED_FAMILIES", None)
    assert probes and excluded is not None, (
        "_WRITE_VOCABULARY_PROBES / _WRITE_PROBE_EXCLUDED_FAMILIES not found — "
        "the validator was restructured; recheck the write-coverage guidance in "
        "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-type-maps.md."
    )

    probed = {probe.partition("(")[0] for probe in probes}
    families = set(arrow_grammar.FAMILY_NAMES)
    assert probed | set(excluded) == families, _diff_msg(
        "write-coverage family coverage",
        families,
        probed | set(excluded),
        "every family in the vendored engine grammar must be probed or carry an "
        "entry in _WRITE_PROBE_EXCLUDED_FAMILIES naming why it is not.",
    )
    assert not (probed & set(excluded)), (
        f"families both probed and excluded: {sorted(probed & set(excluded))}"
    )
    assert all(reason.strip() for reason in excluded.values()), (
        "every exclusion states its reason — an unexplained one is the "
        "by-omission list this guard replaced."
    )

    # Each probe is a real canonical, so a coverage gap is never an author
    # chasing a spelling the vocabulary would reject anyway.
    canonical = re.compile(arrow_grammar.ARROW_TYPE_PATTERN)
    unmatched = [probe for probe in probes if not canonical.fullmatch(probe)]
    assert not unmatched, f"probes are not canonical types: {unmatched}"

    # The optional-parameter policy the tz-aware Timestamp gap rests on.
    assert "Timestamp(SECOND)" in probes and not any(
        "UTC" in probe for probe in probes
    ), "a probe now carries an optional parameter — the spec's tz-aware gap moved"


def test_write_coverage_exclusions_are_named_in_the_spec() -> None:
    """The by-hand list an author reads must name exactly the excluded families.

    The exclusions are data in the validator now; this is the guard that keeps
    the prose deferring to them rather than drifting into a second list.
    """
    from analitiq.validator import connectors

    section = TYPE_MAPS_SPEC.read_text(encoding="utf-8")
    start = section.index("A clean warning is not proof of coverage")
    window = section[start:section.index("Mind precision survival", start)]
    missing = sorted(
        family for family in connectors._WRITE_PROBE_EXCLUDED_FAMILIES
        if f"`{family}`" not in window
    )
    assert not missing, (
        f"write-coverage exclusions {missing} are not named in the by-hand list "
        f"in {TYPE_MAPS_SPEC.relative_to(REPO_ROOT)} — an author gets no signal "
        "for them from the validator and none from the prose either."
    )


# --- io-contracts.md fragments, pinned at the site ---------------------------
# Each I/O envelope in that file is one fenced JSON Schema under its own `##`
# heading, and several carry a contract-owned vocabulary the plugin cannot
# reach any other way: the researcher and the orchestrator load this file and
# have no fetch tool, so a member missing here is a fact no agent can recover.
# The pins below read the fragment at its site rather than comparing the
# contract to a constant, which is what the constants above already do.


def _io_fragment(heading: str) -> dict:
    """The parsed `json` fence under io-contracts.md's `## <heading>` section.

    Locating is lexical throughout — the heading token, then the fence — and
    every verdict is handed to the contract by the caller. Both `assert`s are
    non-vacuity guards: a renamed heading or a fragment that stopped being a
    `json` fence turns the build red instead of grading nothing.
    """
    body = IO_CONTRACTS.read_text(encoding="utf-8")
    section = re.search(
        rf"^##\s+{re.escape(heading)}\b.*?$(.*?)(?=^##\s|\Z)", body, re.M | re.S
    )
    assert section, (
        f"{IO_CONTRACTS.relative_to(REPO_ROOT)}: no `## {heading}` section — "
        "the document was restructured, so this guard would have graded nothing."
    )
    fence = re.search(r"```json\n(.*?)```", section.group(1), re.S)
    assert fence, (
        f"{IO_CONTRACTS.relative_to(REPO_ROOT)} §{heading}: no `json` fence — "
        "the fragment moved or changed form, so this guard would have graded "
        "nothing."
    )
    return json.loads(fence.group(1))


def _provider_facts_api_branch() -> dict:
    """The `kind: "api"` branch of the `ProviderFacts` fragment.

    The fragment is a discriminated `oneOf`; the API branch is the one that
    carries the auth and pagination vocabularies. Selected by the branch's own
    `kind` const, so a reordered union does not silently move the grading onto
    the database branch.
    """
    branches = [
        branch
        for branch in _io_fragment("ProviderFacts").get("oneOf", [])
        if (branch.get("properties") or {}).get("kind", {}).get("const") == "api"
    ]
    assert len(branches) == 1, (
        f"{IO_CONTRACTS.relative_to(REPO_ROOT)} §ProviderFacts: expected one "
        f"`kind: \"api\"` branch, found {len(branches)} — the fragment was "
        "restructured, so this guard would have graded nothing."
    )
    return branches[0]


def test_provider_facts_auth_families_match_the_contract() -> None:
    """The researcher's `auth_model.family` targets, read off this file.

    Deliberately the contract set minus `db`: an API connector never authors
    database auth, so offering it would be a target the researcher can classify
    onto and no API creator can use. Everything else must be reachable — a
    family missing here is an auth flow the researcher has no name for, and
    `connector-provider-researcher.md` loads no other vocabulary file.
    """
    documented = set(
        _provider_facts_api_branch()["properties"]["auth_model"]["properties"][
            "family"
        ]["enum"]
    )
    expected = EXPECTED_AUTH_TYPES - {"db"}
    assert documented == expected, _diff_msg(
        "ProviderFacts auth_model.family",
        expected,
        documented,
        "update the `family` enum in "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md.",
    )


def test_provider_facts_pagination_styles_match_the_contract() -> None:
    """The one pagination-style copy the researcher can read offline.

    `EndpointFacts.pagination.style` defers to this block rather than repeating
    it, so this is the only enumeration; a style the contract admits and this
    list omits is a paginator the researcher cannot report.
    """
    documented = set(
        _provider_facts_api_branch()["properties"]["pagination"]["properties"][
            "style"
        ]["enum"]
    )
    assert documented == EXPECTED_PAGINATION_STYLES, _diff_msg(
        "ProviderFacts pagination.style",
        EXPECTED_PAGINATION_STYLES,
        documented,
        "update the `style` enum in "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md.",
    )


def _diagnostics_finding_item() -> dict:
    """The `findings[]` item schema of the `Diagnostics` fragment."""
    return _io_fragment("Diagnostics")["properties"]["findings"]["items"]


def test_diagnostics_enum_matches_the_package() -> None:
    """The id list an orchestrator routes findings by, read off this file.

    `test_validator_ids_match_package` grades the package against this module's
    constant and names io-contracts.md only in its fix text, so the fragment
    could drift to a stale id set with that gate green.
    """
    from analitiq.validator import VALIDATOR_IDS

    documented = set(_diagnostics_finding_item()["properties"]["validator"]["enum"])
    package_set = {vid for vid in VALIDATOR_IDS if not vid.startswith("bundle-")}
    assert documented == package_set, _diff_msg(
        "Diagnostics validator enum",
        package_set,
        documented,
        "update the `validator` enum in "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md.",
    )


def test_diagnostics_severities_are_the_ones_finding_accepts() -> None:
    """Every severity the fragment declares must be one `finding()` will emit.

    One-directional, and deliberately so. `finding()` is the sole construction
    point and holds its severity vocabulary as an inline tuple that nothing
    exports, so the set is not enumerable from outside the package: what is
    decidable here is that each severity the prose offers is accepted, and that
    a non-member is refused (without which the first half passes on a function
    that stopped checking at all). The direction this cannot see — the
    validator gaining a severity the fragment never names — needs
    `analitiq.validator` to export the vocabulary beside `VALIDATOR_IDS`
    first; until it does, that half is a reader's obligation on any change to
    `finding()`.
    """
    from analitiq.validator._core import finding

    documented = set(_diagnostics_finding_item()["properties"]["severity"]["enum"])
    assert documented, (
        f"{IO_CONTRACTS.relative_to(REPO_ROOT)} §Diagnostics: the `severity` "
        "enum is gone — an orchestrator branches on this value to decide "
        "whether to re-dispatch a creator."
    )
    for severity in sorted(documented):
        finding("document", severity, "/", "probe")
    with pytest.raises(ValueError):
        finding("document", "not-a-severity", "/", "probe")


def test_diagnostics_properties_match_the_finding_constructor() -> None:
    """The `Diagnostics` finding shape, pinned to the only thing that builds one.

    `finding()` is the sole construction point across every document kind and
    its signature is closed, so a property the fragment names that the
    signature does not is a field no consumer will ever see — which is how
    `rule_doc` survived in the prose. The enum inside is pinned separately by
    `test_validator_ids_match_package`; nothing pinned the property SET.
    """
    import inspect

    from analitiq.validator._core import finding

    item = _diagnostics_finding_item()
    stated = set(item["properties"])
    produced = set(inspect.signature(finding).parameters)
    assert stated == produced, _diff_msg(
        "Diagnostics finding properties",
        produced,
        stated,
        "update the Diagnostics fragment in "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md "
        "to match the finding() signature — a property nothing constructs is "
        "prose an agent will look for and never find.",
    )
    assert set(item["required"]) == produced, (
        f"Diagnostics `required` {sorted(item['required'])} does not match the "
        f"finding() signature {sorted(produced)} — every argument is positional "
        "and non-optional, so every property is required."
    )


def test_validator_ids_match_package() -> None:
    """The finding ids the plugin's prose enumerates must be the ones emitted.

    `bundle-*` ids are excluded: they belong to pipeline-bundle validation, which
    this plugin never invokes, so carrying them in an authoring reference would
    imply findings an author can never see.
    """
    from analitiq.validator import VALIDATOR_IDS

    package_set = {vid for vid in VALIDATOR_IDS if not vid.startswith("bundle-")}
    assert package_set == EXPECTED_VALIDATOR_IDS, _diff_msg(
        "validator ids",
        package_set,
        EXPECTED_VALIDATOR_IDS,
        "update the Diagnostics enum in "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md "
        "and the check list in "
        "plugins/analitiq-connector-builder/README.md § Validation.",
    )


def test_resolution_scopes_match_contract() -> None:
    from analitiq.contracts.value_expression import RESOLUTION_SCOPES

    package_set = set(RESOLUTION_SCOPES)
    assert package_set == EXPECTED_RESOLUTION_SCOPES, _diff_msg(
        "resolution scopes",
        package_set,
        EXPECTED_RESOLUTION_SCOPES,
        "update the scope table in "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/value-expressions.md.",
    )


def test_transport_types_match_schema(connector_schema: dict) -> None:
    schema_set = _const_types(connector_schema, "Transport", const_field="transport_type")
    assert schema_set == EXPECTED_TRANSPORT_TYPES, _diff_msg(
        "transport.transport_type",
        schema_set,
        EXPECTED_TRANSPORT_TYPES,
        "update TransportTypeMapper in "
        "plugins/analitiq-connector-builder/skills/connector-builder/references/enum-mappers.md.",
    )


def test_slug_pattern_restatements_match_contract() -> None:
    """Every hand-typed slug-charset literal must equal the contract's SLUG_PATTERN.

    Catches the contract's pattern moving out from under a prose copy, and a
    loose paraphrase being reintroduced — `[a-z0-9_-]+` accepts a leading `_` /
    `-` the contract rejects, which is how 5 of the hand-typed copies were
    wrong before these sites were consolidated and pinned.
    """
    wrong = [
        (rel, lineno, literal)
        for rel, lineno, literal in _slug_literal_sites()
        if literal != SLUG_PATTERN
    ]
    assert not wrong, (
        f"slug-pattern literals diverging from the contract's {SLUG_PATTERN!r}:\n"
        + "\n".join(
            f"  plugins/analitiq-connector-builder/{rel}:{lineno}  {literal!r}"
            for rel, lineno, literal in wrong
        )
        + "\nWrite the contract's exact pattern, or reference a canonical site "
        "in EXPECTED_SLUG_PATTERN_SITES instead of restating it."
    )


def test_slug_pattern_sites_are_pinned() -> None:
    """The set of files carrying the literal is a recorded decision, per file.

    Both directions: a new copy appearing anywhere in the plugin's prose fails
    until it is listed with a reason, and a canonical statement disappearing
    (consolidated away, or reworded past the detector) fails so the references
    pointing at it cannot silently dangle.
    """
    counts = dict(Counter(rel for rel, _lineno, _literal in _slug_literal_sites()))
    assert counts == EXPECTED_SLUG_PATTERN_SITES, (
        "slug-pattern occurrence counts changed (found vs. expected):\n"
        f"  found:    {counts}\n"
        f"  expected: {EXPECTED_SLUG_PATTERN_SITES}\n"
        "A hand-typed copy appeared or a canonical statement vanished — "
        "reference the canonical sites instead of adding copies, or update "
        "EXPECTED_SLUG_PATTERN_SITES if the move is deliberate."
    )


def test_slug_literal_detector_recall() -> None:
    """Pin the detector itself — its recall is what the two gates above stand on.

    A later "simplification" (say, to `re.escape(SLUG_PATTERN)`) would keep both
    gates green on the exact-pattern sites while silently dropping paraphrase
    coverage — the exact blind spot the 5 loose paraphrase copies that
    predated this pin lived in.
    """
    assert _SLUG_LITERAL_RE.fullmatch(SLUG_PATTERN), (
        "_SLUG_LITERAL_RE no longer fully matches SLUG_PATTERN itself — the "
        "canonical sites would surface as truncated/divergent literals."
    )
    paraphrases = ["[a-z0-9_-]+", "[a-z0-9]"]
    missed = [p for p in paraphrases if not _SLUG_LITERAL_RE.fullmatch(p)]
    assert not missed, (
        f"_SLUG_LITERAL_RE no longer catches {missed} — a reintroduced loose "
        "copy would pass both slug-pattern gates."
    )
    # Non-slug charsets that legitimately appear in type-map regex examples.
    non_slug = ["[0-9]+", "[A-Z]+", r"^Time(32|64)\([A-Z]+\)$"]
    false_hits = [s for s in non_slug if _SLUG_LITERAL_RE.search(s)]
    assert not false_hits, (
        f"_SLUG_LITERAL_RE now matches non-slug charsets {false_hits} — the "
        "type-map regex examples would start failing the equality gate."
    )


def test_slug_pattern_governs_the_restated_fields(
    connector_schema: dict, api_endpoint_schema: dict
) -> None:
    """SLUG_PATTERN must be the pattern the contract puts on the restated fields.

    The two tests above pin prose to the imported constant. If `connector_id` /
    `endpoint_id` ever stopped using SLUG_PATTERN, they would keep passing while
    pinning prose to a constant that no longer governs the fields it describes —
    so anchor the constant to the generated schemas here.
    """
    connector_id_patterns = {
        name: props["connector_id"].get("pattern")
        for name, node in connector_schema.get("$defs", {}).items()
        if isinstance(node, dict)
        and "connector_id" in (props := node.get("properties") or {})
    }
    assert connector_id_patterns, (
        "no $def carries a connector_id property — the connector contract was "
        "restructured; re-anchor this guard."
    )
    endpoint_id_pattern = (
        (api_endpoint_schema.get("properties") or {}).get("endpoint_id") or {}
    ).get("pattern")
    off = {
        field: pattern
        for field, pattern in {
            **{f"{name}.connector_id": p for name, p in connector_id_patterns.items()},
            "ApiEndpointDoc.endpoint_id": endpoint_id_pattern,
        }.items()
        if pattern != SLUG_PATTERN
    }
    assert not off, (
        f"fields no longer constrained by SLUG_PATTERN ({SLUG_PATTERN!r}): {off}. "
        "The prose pins above now reference the wrong constant — re-anchor them "
        "to whatever governs connector_id/endpoint_id."
    )


# --- the one-sided pins, closed from the prose side -------------------------
# Every pin above this section compares the contract to a constant in this
# file. That leaves the document an agent actually reads unchecked: renaming
# `url_userinfo` to `url_credentials` in the DSN table, measured, left the whole
# suite green while the table taught an encoding the contract rejects.
# The gap was invisible from either side: the constant matched the contract, so
# the guard was green, and the table was the only thing an agent read.
#
# These read the target column: the last cell of every data row in the table a
# section owns, which is where the mapper puts the member it maps onto. Both
# directions, so a member the contract gains must reach the table AND a member
# the table invents must exist.

ENUM_MAPPERS = (
    PLUGIN_ROOT / "skills" / "connector-builder" / "references" / "enum-mappers.md"
)
DSN_BINDINGS = (
    PLUGIN_ROOT / "skills" / "connector-spec-db" / "spec-dsn-bindings.md"
)
VALUE_EXPRESSIONS = (
    PLUGIN_ROOT / "skills" / "connector-builder" / "references" / "value-expressions.md"
)
REQUEST_BINDING_SPEC = (
    PLUGIN_ROOT / "skills" / "connector-spec-api" / "spec-request-binding.md"
)
TRANSPORT_SPEC = PLUGIN_ROOT / "skills" / "connector-spec-api" / "spec-transport.md"
RESOURCE_DISCOVERY_SPEC = (
    PLUGIN_ROOT / "skills" / "connector-spec-db" / "spec-resource-discovery.md"
)


def _section(doc: Path, heading: str) -> str:
    """The text under `## <heading>`, to the next column-0 heading."""
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^#|\Z)",
        doc.read_text(encoding="utf-8"), re.M | re.S,
    )
    assert match, (
        f"{doc.name}: no `## {heading}` section — the document was "
        "restructured, so this guard would have graded nothing."
    )
    return match.group(1)


_SEPARATOR_ROW = re.compile(r"^\|[\s:|-]+\|$")


def _target_column(section: str) -> set[str]:
    """Backticked tokens in the last cell of each table BODY row in `section`.

    The last cell is the mapper's output — the contract member the row maps
    onto.

    Body rows only, found by skipping to the separator: a header here names the
    field being mapped onto, in backticks (``Output `transport_type` ``), so
    reading every row would collect the field name as though it were one of its
    own members. Rows before the first separator are header; a section with no
    table yields nothing and the caller's equality fails, which is the right
    answer for a table that was restructured away.
    """
    found: set[str] = set()
    in_body = False
    for line in section.splitlines():
        if not line.startswith("|"):
            in_body = False          # a break in the run ends that table
            continue
        if _SEPARATOR_ROW.match(line.rstrip()):
            in_body = True
            continue
        if not in_body:
            continue
        cells = [c for c in line.split("|") if c.strip()]
        if cells:
            found |= set(_BACKTICKED.findall(cells[-1]))
    return found


@pytest.mark.parametrize(
    "doc_name, heading, expected, extra",
    [
        # KindMapper routes the storage stubs and names the two kinds this
        # plugin recognises but never authors in the prose below its table, so
        # the section is the unit rather than the table alone.
        ("enum-mappers", "KindMapper", EXPECTED_KINDS, {"nosql", "document"}),
        ("enum-mappers", "AuthTypeMapper", EXPECTED_AUTH_TYPES, set()),
        ("enum-mappers", "TransportTypeMapper", EXPECTED_TRANSPORT_TYPES, set()),
    ],
)
def test_mapper_target_columns_match_the_contract(
    doc_name: str, heading: str, expected: set[str], extra: set[str]
) -> None:
    """A mapper's output column must be exactly the contract's vocabulary.

    Without this the mapper is pinned only through a constant in this file, so
    a member could be renamed in the contract, updated in the constant, and
    left stale in the table an orchestrator classifies against — which is the
    one copy that decides what gets authored.
    """
    doc = {"enum-mappers": ENUM_MAPPERS}[doc_name]
    section = _section(doc, heading)
    documented = _target_column(section)
    if extra:
        documented |= {m for m in extra if f"`{m}`" in section}
    assert documented == expected, (
        f"{doc.relative_to(REPO_ROOT)} §{heading} maps onto different members "
        f"than the contract — prose-only={sorted(documented - expected)} "
        f"contract-only={sorted(expected - documented)}. The mapper is the only "
        "route from a researched fact to a schema value, so a member missing "
        "here cannot be authored at all."
    )


def test_dsn_encoding_table_matches_the_contract() -> None:
    """The DSN table's Encoding column must be exactly the contract's set.

    This table is decision logic — it maps a URL position onto the encoding
    that position needs — so the members legitimately stay hand-typed. What was
    missing is anything reading them: `test_dsn_encodings_match_schema` pins the
    contract to a constant and names this file only in its failure text.
    """
    section = _section(DSN_BINDINGS, "Choosing an encoding (`RULE-CTOR-018`)")
    documented = _target_column(section)
    assert documented == EXPECTED_DSN_ENCODINGS, (
        f"{DSN_BINDINGS.relative_to(REPO_ROOT)} §Choosing an encoding lists "
        f"different encodings than the contract — "
        f"prose-only={sorted(documented - EXPECTED_DSN_ENCODINGS)} "
        f"contract-only={sorted(EXPECTED_DSN_ENCODINGS - documented)}."
    )


def _row_labels(section: str) -> set[str]:
    """The label column of every table row in `section`.

    Routed through `_row_cells` — the module's one label rule — so a row whose
    label cell also carries a cross-reference stays visible, and header and
    separator rows fall out for carrying no code span.
    """
    return set(_row_cells([line for line in section.splitlines() if line.startswith("|")]))


def test_expression_kinds_table_matches_the_contract() -> None:
    """The Kind column is the only agent-loadable copy of the expression forms.

    `RULE-CTOR-035` renders no Values column — the forms are a union of models,
    not a field enum, and the rule reference fills that column only for a
    `literal_enum` record — so an agent cannot recover the set from `rules.md`,
    and it has no fetch tool. Pinned both directions: a form the contract gains
    must reach the table, and a form the table invents must exist.
    """
    from analitiq.contracts.endpoints import _EXPRESSION_KEYS

    documented = _row_labels(_section(VALUE_EXPRESSIONS, "Expression kinds"))
    expected = set(_EXPRESSION_KEYS)
    assert documented == expected, (
        f"{VALUE_EXPRESSIONS.relative_to(REPO_ROOT)} §Expression kinds names "
        f"different forms than the contract — "
        f"prose-only={sorted(documented - expected)} "
        f"contract-only={sorted(expected - documented)}."
    )


def test_scope_table_matches_the_contract() -> None:
    """The scope table is the one agent-loadable copy of RESOLUTION_SCOPES.

    `test_resolution_scopes_match_contract` pins the contract to a constant and
    names this file only in its failure hint, so the table an authoring agent
    reads was unowned. `RULE-CTOR-057` renders no Values column, so `rules.md`
    is not a substitute.

    Row labels are sub-scope-qualified (`connection.parameters.*`), so each is
    normalised to its leading token — which is the part the contract owns and
    the part an expression must lead with.
    """
    from analitiq.contracts.value_expression import RESOLUTION_SCOPES

    documented = {
        label.split(".", 1)[0].rstrip("*")
        for label in _row_labels(_section(VALUE_EXPRESSIONS, "Logical scopes"))
    }
    expected = set(RESOLUTION_SCOPES)
    assert documented == expected, (
        f"{VALUE_EXPRESSIONS.relative_to(REPO_ROOT)} §Logical scopes names "
        f"different scopes than the contract — "
        f"prose-only={sorted(documented - expected)} "
        f"contract-only={sorted(expected - documented)}."
    )


def test_function_catalog_matches_the_contract() -> None:
    """§Function catalog is the catalog other documents cite by name.

    `RULE-SHRD-007` carries the obligation but no member list (it is
    referential, so the rule reference renders no Values column), and the agent
    has no fetch tool. Pin the bullets to the union the contract models.

    The heading carries the rule id and is matched verbatim, so renaming it
    fails loudly through `_section` rather than grading nothing. Only the
    catalog's own `- \\`name\\` —` bullets are read: the planned-but-unregistered
    names below them are prose, not list items, and naming them here would be
    the opposite of what that paragraph teaches.
    """
    from analitiq.contracts.connector import DerivedValue

    expected = set(TypeAdapter(DerivedValue).json_schema()["discriminator"]["mapping"])
    section = _section(VALUE_EXPRESSIONS, "Function catalog (`RULE-SHRD-007`)")
    documented = {
        match.group(1)
        for line in section.splitlines()
        if (match := re.match(r"- `([a-z0-9_]+)`", line))
    }
    assert documented == expected, (
        f"{VALUE_EXPRESSIONS.relative_to(REPO_ROOT)} §Function catalog names "
        f"different functions than the contract — "
        f"prose-only={sorted(documented - expected)} "
        f"contract-only={sorted(expected - documented)}."
    )


def _bullet_citing(section: str, rule_id: str) -> str | None:
    """The one top-level list item in `section` that cites `rule_id`.

    A rule id is the lexical anchor a sentence cannot lose by being reworded:
    it is an immutable registry key, and a dangling one already fails the
    build. None when no item cites it or more than one does, so the caller
    turns a restructured section into an explicit failure instead of a pass
    against an empty item.
    """
    items = [
        item
        for item in re.split(r"(?m)^(?=- )", section)
        if item.startswith("- ") and rule_id in item
    ]
    return items[0] if len(items) == 1 else None


def test_request_binding_prose_names_every_expression_key() -> None:
    """spec-request-binding.md's expression-key list is the one agent-loadable copy.

    `RULE-ENDP-022` carries no `fields`, so the rendered rule reference prints
    no members for it, and `value-expressions.md` points here for the set —
    which makes this the site a new expression form has to reach. Pin the
    hand-typed list to `_ALL_EXPRESSION_KEYS`, located by the rule id the
    bullet cites rather than by its wording.
    """
    from analitiq.contracts.endpoints import _ALL_EXPRESSION_KEYS

    bullet = _bullet_citing(
        _section(REQUEST_BINDING_SPEC, "Binding rules"), "RULE-ENDP-022"
    )
    assert bullet is not None, (
        f"{REQUEST_BINDING_SPEC.relative_to(REPO_ROOT)} §Binding rules: no "
        "single list item cites `RULE-ENDP-022` — the section was restructured "
        "(or two items now claim it), so this guard would have passed "
        "vacuously. It is the only copy of the expression-key set an authoring "
        "agent can load."
    )
    stated = set(re.findall(r"`([a-z_]+)`", bullet))
    expected = set(_ALL_EXPRESSION_KEYS)
    assert stated == expected, _diff_msg(
        "expression key",
        expected,
        stated,
        "update the primary-key list in "
        "plugins/analitiq-connector-builder/skills/connector-spec-api/spec-request-binding.md "
        "§Binding rules. It reads every backticked lowercase token in that "
        "item, so do not add one that is not an expression key.",
    )


#: The merge layers `spec-transport.md` numbers, in the order it numbers them.
#: Each is the last backticked identifier on its numbered line.
EXPECTED_HEADER_LAYERS = (
    "transport_defaults.headers",
    "transports.<ref>.headers",
    "headers_remove",
    "headers",
)


def test_header_resolution_order_matches_the_contract() -> None:
    """`spec-transport.md` numbers the header merge layers; the contract runs them.

    Two halves, because either alone rots. The behavioural half exercises
    `build_effective_headers` — the function every runtime header map comes
    from — so a reordered merge or a case-sensitive removal fails here rather
    than in a connector nobody re-reads. The prose half reads the numbered list
    back out of the document that teaches it, keyed on the verbatim heading and
    the backticked identifier per line, so a list rewritten into a different
    order fails too. Nothing else in this suite reads either.

    The fixture is chosen so every layer is observable: `X-Default` survives
    from the defaults, `X-Transport` proves the transport overrides them,
    `X-Both` proves the operation overrides both, and `X-Gone` proves removal
    drops an inherited name matched in a different case.
    """
    from analitiq.contracts.value_expression import build_effective_headers

    effective = build_effective_headers(
        {"headers": {"X-Op": "op", "X-Both": "op"}, "headers_remove": ["x-gone"]},
        {},
        transport={"headers": {"X-Transport": "t", "X-Both": "t", "X-Gone": "t"}},
        transport_defaults={
            "headers": {"X-Default": "d", "X-Transport": "d", "X-Both": "d"}
        },
    )
    assert effective == {
        "X-Default": "d",
        "X-Transport": "t",
        "X-Both": "op",
        "X-Op": "op",
    }, (
        "the contract's header merge no longer runs defaults → transport → "
        "removal → operation with case-insensitive removal; re-read the "
        f"numbered list in {TRANSPORT_SPEC.relative_to(REPO_ROOT)}."
    )

    section = _section(TRANSPORT_SPEC, "Header resolution order")
    layers = tuple(
        tokens[-1]
        for line in section.splitlines()
        if re.match(r"^\d+\.\s", line) and (tokens := _BACKTICKED.findall(line))
    )
    assert layers == EXPECTED_HEADER_LAYERS, (
        f"{TRANSPORT_SPEC.relative_to(REPO_ROOT)} §Header resolution order "
        f"numbers the layers {list(layers)}; the contract merges "
        f"{list(EXPECTED_HEADER_LAYERS)}."
    )


def test_discovery_fallback_label_matches_the_contract() -> None:
    """The fallback a column takes when its provider type could not be read.

    `RULE-DBEP-012` obliges the recorded label to be the one `native_type`
    declares, and deliberately does not spell it — the label is the contract's,
    not the registry's. That leaves this bullet the only place an authoring
    agent can read which string to write, so pin it to the field description
    the published schema carries. Located by the rule id it cites, which a
    rewording cannot drop without failing the citation guard first.
    """
    from analitiq.contracts.endpoints import Column

    bullet = _bullet_citing(
        _section(RESOURCE_DISCOVERY_SPEC, "What discovery must record about each object"),
        "RULE-DBEP-012",
    )
    assert bullet is not None, (
        f"{RESOURCE_DISCOVERY_SPEC.relative_to(REPO_ROOT)}: no single list item "
        "cites `RULE-DBEP-012` under §What discovery must record about each "
        "object — the section was restructured (or two items claim it), so this "
        "guard would have passed vacuously while the only copy of the fallback "
        "label went unread."
    )
    described = Column.model_fields["native_type"].description or ""
    declared = set(re.findall(r"'([a-z_]+)'", described))
    stated = set(re.findall(r'`"([a-z_]+)"`', bullet))
    assert stated and stated == declared, (
        f"{RESOURCE_DISCOVERY_SPEC.relative_to(REPO_ROOT)} tells an author to "
        f"record {sorted(stated)}; `Column.native_type` declares "
        f"{sorted(declared)}. Update the bullet citing RULE-DBEP-012 — it reads "
        "every double-quoted backticked token in that item, so do not add one "
        "that is not the fallback label."
    )
