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
# "mapping logic" exemption — and pins them here. The five required shape facts
# are pinned too: the spec teaches that a declared block is COMPLETE and a
# partial one is a config error, which is only true while these five are the
# required set. (`limits` stays out — it is the additive member.)
EXPECTED_SQL_CAPABILITY_FACTS = {
    "catalog",
    "session_targeting",
    "merge_form",
    "bulk_load",
    "stage",
}
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
# WriteOperation.idempotency `in` targets (api-endpoint ≥ 9.1.0,
# infrastructure#890) — restated in io-contracts.md EndpointFacts,
# endpoint-creator.md, connector-provider-researcher.md, and
# connector-spec-api/SKILL.md.
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
# io-contracts.md's `Diagnostics` enum and the connector-schema-validator agent's
# id table. Owned by `analitiq.validator.VALIDATOR_IDS`, minus the `bundle-*` ids,
# which only apply to pipeline bundles this plugin never validates.
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
# plugin's prose, and how many times (issue #58). No other site restates the
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
        "update the driver-selection guidance (enum-mappers.md, "
        "spec-driver-selection.md).",
    )


def test_sqlalchemy_driver_pattern_matches_schema(connector_schema: dict) -> None:
    pattern = _pattern_at(
        connector_schema, "$defs", "SqlAlchemyTransport", "properties", "driver"
    )
    fix = (
        "update the driver guidance (spec-driver-selection.md, "
        "spec-dsn-bindings.md, enum-mappers.md, db-connector-creator.md, "
        "connector-spec-db/SKILL.md, io-contracts.md), the canon extraction in "
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
        "update the Encoding values table in "
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
    "both archetypes under "
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
    rows = {
        found[0]
        for line in DRIVER_SELECTION_SPEC.read_text(encoding="utf-8").splitlines()
        if (m := _TABLE_ROW.match(line))
        and len(found := _BACKTICKED.findall(m.group(1))) == 1
    }
    expected = EXPECTED_SQL_BULK_MECHANISMS["sqlalchemy"]
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


def _set_diff_msg(label: str, found: set[str] | None, expected: set[str]) -> str:
    """Diff message for a NAME set (required list or property keys).

    Separate from `_diff_msg` because these are not enums: reporting "enum not
    found at the expected pointer" for a renamed `$def` sends the reader
    looking for the wrong thing. The missing-node wording is deliberately
    generic ("not found at that $def") so the same helper can serve both a
    `required` list and a `properties` key set without lying about which it
    read.
    """
    if found is None:
        return (
            f"{label}: not found at that $def — the contract was "
            f"restructured. {_WRITE_PATH_FIX}"
        )
    return (
        f"{label} drift — {_WRITE_PATH_FIX} "
        f"schema-only={sorted(found - expected)} "
        f"plugin-only={sorted(expected - found)}"
    )


def test_sql_capability_facts_match_schema(connector_schema: dict) -> None:
    """The five shape facts a declared `sql_capabilities` block must carry.

    `limits` is excluded deliberately — it is the additive member, and the
    prose says so. If it ever became required (or one of the five optional),
    "a declared block is complete" would stop being true.
    """
    required = _required_at(connector_schema, "SqlCapabilities")
    assert required == EXPECTED_SQL_CAPABILITY_FACTS, _set_diff_msg(
        "sql_capabilities required facts", required, EXPECTED_SQL_CAPABILITY_FACTS
    )


def test_sql_stage_facts_match_schema(connector_schema: dict) -> None:
    """Same claim, one level down: the stage block's three required fields.

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
    node = (connector_schema.get("$defs") or {}).get("SqlStageCapabilities")
    props = set((node.get("properties") or {})) if isinstance(node, dict) else None
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
    node = (connector_schema.get("$defs") or {}).get("SqlBulkLoad")
    families = set((node.get("properties") or {})) if isinstance(node, dict) else None
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
    node = (connector_schema.get("$defs") or {}).get("SqlLimits")
    caps = set((node.get("properties") or {})) if isinstance(node, dict) else None
    assert caps == EXPECTED_SQL_LIMIT_CAPS, _set_diff_msg(
        "sql_capabilities.limits caps", caps, EXPECTED_SQL_LIMIT_CAPS
    )


# --- the write-path pins, read from the PROSE side --------------------------
# Every other pin in this file is one-sided: contract -> a constant here. That
# leaves a hole exactly the shape of issue #95 — update the contract AND the
# constant, forget the spec table, and the suite stays green while the document
# an agent actually reads has gone stale. `test_slug_pattern_restatements_*`
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


def _table_blocks() -> list[list[str]]:
    """Maximal runs of consecutive `|`-leading lines — one per markdown table."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in WRITE_PATH_SPEC.read_text(encoding="utf-8").splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _labels_of_table_containing(label: str) -> set[str] | None:
    """The full label-column set of the table whose rows include `label`.

    Both directions, unlike a subset check: a contract fact with no prose row
    fails, and a prose row for a fact the contract dropped fails too. Without
    it the fact NAMES are pinned contract-side only — so making `limits`
    required would fail one test, the fixer would add it to the expected set,
    and the two prose files still claiming "all five shape facts" would stay
    green. That is issue #95's two-step, in miniature.
    """
    tables = [
        labels
        for block in _table_blocks()
        if label
        in (
            labels := {
                found[0]
                for row in block
                if (m := _TABLE_ROW.match(row))
                and len(found := _BACKTICKED.findall(m.group(1))) == 1
            }
        )
    ]
    return tables[0] if len(tables) == 1 else None


def _vocabulary_rows() -> list[tuple[str, set[str]]]:
    """Every (label, values) table row whose label cell is one backticked token."""
    rows = []
    for line in WRITE_PATH_SPEC.read_text(encoding="utf-8").splitlines():
        row = _TABLE_ROW.match(line)
        if not row:
            continue
        label = _BACKTICKED.findall(row.group(1))
        values = set(_BACKTICKED.findall(row.group(2)))
        if len(label) == 1 and values:
            rows.append((label[0], values))
    return rows


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


@pytest.mark.parametrize(
    "label, expected",
    [
        ("catalog", EXPECTED_SQL_CATALOG_MODES),
        ("session_targeting", EXPECTED_SQL_SESSION_TARGETING),
        ("merge_form", EXPECTED_SQL_MERGE_FORMS),
        ("scope", EXPECTED_SQL_STAGE_SCOPES),
        ("schema", EXPECTED_SQL_STAGE_SCHEMAS),
        ("sqlalchemy", EXPECTED_SQL_BULK_MECHANISMS["sqlalchemy"]),
        ("adbc", EXPECTED_SQL_BULK_MECHANISMS["adbc"]),
    ],
)
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
        # The declaration table lists the five shape facts AND `limits`; the
        # prose calls the latter optional, but it still needs a row.
        ("catalog", EXPECTED_SQL_CAPABILITY_FACTS | {"limits"}),
        ("scope", EXPECTED_SQL_STAGE_FACTS),
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
    """Pin the parser: its recall is what the seven checks above stand on.

    Recall first — a parser that matched nothing would leave every check above
    resting on its `is None` guard, which is a real failure but for the wrong
    reason, and a future "simplification" could pass the negative cases below
    while reading no rows at all.
    """
    rows = _vocabulary_rows()
    assert len(rows) >= 7, (
        f"the parser found only {len(rows)} vocabulary rows in "
        f"{WRITE_PATH_SPEC.relative_to(REPO_ROOT)} — it must read at least the "
        "seven the tests above pin, or those tests are resting on None-guards."
    )
    assert _documented_values("catalog") == EXPECTED_SQL_CATALOG_MODES

    # Precision: a Values cell that is prose, not vocabulary. `bulk_load` reads
    # "per-transport object" — no backticks, so it must come back None rather
    # than an empty set masquerading as a match.
    assert _documented_values("bulk_load") is None
    # A label that does not exist at all.
    assert _documented_values("no_such_field") is None
    # The label cell must match EXACTLY one backticked token, so a header row
    # cannot be mistaken for a vocabulary row.
    assert _documented_values("Fact") is None


def test_idempotency_targets_match_schema(api_endpoint_schema: dict) -> None:
    schema_set = _enum_at(
        api_endpoint_schema, "$defs", "Idempotency", "properties", "in"
    )
    assert schema_set == EXPECTED_IDEMPOTENCY_TARGETS, _diff_msg(
        "idempotency.in",
        schema_set,
        EXPECTED_IDEMPOTENCY_TARGETS,
        "update io-contracts.md EndpointFacts.idempotency, endpoint-creator.md, "
        "connector-provider-researcher.md, and connector-spec-api/SKILL.md.",
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


def test_write_coverage_probe_gaps_are_documented() -> None:
    """`spec-type-maps.md` names the families the write-coverage check misses.

    That warning is the only signal an author gets about write-map gaps, so the
    prose tells them which families it does NOT exercise. If a future validator
    starts probing one of them, the prose becomes a false warning about a check
    that now works — and if it stops probing another, the list is incomplete.
    Assert the documented gaps against the real probe set.
    """
    from analitiq.validator import connectors

    probes = set(getattr(connectors, "_WRITE_VOCABULARY_PROBES", ()))
    assert probes, (
        "_WRITE_VOCABULARY_PROBES not found — the validator was restructured; "
        "recheck the write-coverage guidance in "
        "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-type-maps.md."
    )

    # Families spec-type-maps.md tells authors to verify by hand.
    documented_gaps = {
        "FixedSizeBinary": lambda p: p.startswith("FixedSizeBinary"),
        "Time32": lambda p: p.startswith("Time32"),
        "tz-aware Timestamp": lambda p: p.startswith("Timestamp(") and "UTC" in p,
        "Decimal256": lambda p: p.startswith("Decimal256"),
    }
    now_probed = sorted(
        name for name, matches in documented_gaps.items() if any(matches(p) for p in probes)
    )
    assert not now_probed, (
        f"write-coverage now probes {now_probed}, which "
        "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-type-maps.md still lists as unprobed. "
        "Drop them from that list."
    )

    # The other direction: families the prose implies ARE probed. Without this,
    # the check only catches gaps closing, never new gaps opening — and the
    # prose would quietly become an incomplete list of what to verify by hand.
    expected_probed = {
        "Boolean": lambda p: p == "Boolean",
        "Json": lambda p: p == "Json",
        # The bare shape markers reach the write map verbatim from API-sourced
        # endpoint documents (issue #75) — the probe set must keep exercising
        # them or the spec's container-coverage rule loses its only automated
        # signal.
        "Object": lambda p: p == "Object",
        "List": lambda p: p == "List",
        "Decimal128": lambda p: p.startswith("Decimal128"),
        "bare Timestamp": lambda p: p.startswith("Timestamp(") and "UTC" not in p,
        "Utf8": lambda p: p == "Utf8",
    }
    stopped_probing = sorted(
        name for name, matches in expected_probed.items() if not any(matches(p) for p in probes)
    )
    assert not stopped_probing, (
        f"write-coverage no longer probes {stopped_probing}, so authors get no "
        "warning for those families. Add them to the by-hand list in "
        "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-type-maps.md."
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
        "plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md, the id table in "
        "plugins/analitiq-connector-builder/agents/connector-schema-validator.md, "
        "and the check list in plugins/analitiq-connector-builder/README.md § Validation.",
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
    `-` the contract rejects, which is how 5 of the pre-#58 copies were wrong.
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
    coverage — the exact blind spot the 5 pre-#58 loose copies lived in.
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
