"""Fixture corpus for the path-free document-set API (`analitiq.validator
.document_set`). `validate_doc`'s type-map gap-resolution mode is implemented,
so its cases run for real; the cases for a route that raises
`NotImplementedError` — both package routes and `validate_doc`'s ordinary
document mode — carry `xfail(strict=True, raises=NotImplementedError)`.
Implementing one of those turns its cases from `xfail` to passing by replacing
the stub body and removing that case's marker; `strict=True` means a case that
starts passing while its marker is still on it fails the suite, so a marker can
never survive its own fix by accident.

Two corpora already committed for the path-based routes are reused here
rather than re-authored: `packages/validator/tests/corpus/` (a connector
package) and `tests/pipeline_builder/test_validate.py`'s `_build_bundle`
layout (a pipeline bundle) — both are content this suite already keeps
model-valid, so the document-set versions built from them are testing the
document-set mechanism, not guessing at contract shapes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import get_args

import pytest

from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.contracts.shared.rule_record import DOCUMENT_ARTIFACT_KINDS

CORPUS = Path(__file__).resolve().parent / "corpus"

_PLUGIN_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "plugins" / "analitiq-pipeline-builder" / "scripts"
)
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))


def _xfail(fn_name: str):
    return pytest.mark.xfail(
        strict=True, raises=NotImplementedError,
        reason=f"{fn_name} is not implemented (analitiq.validator.document_set)")


# ---------------------------------------------------------------------------
# Entity — drift guard against DOCUMENT_ARTIFACT_KINDS (not xfail: this is a
# type-contract fact settled now, independent of any function body below
# being implemented).
# ---------------------------------------------------------------------------

def test_entity_matches_document_artifact_kinds(validator):
    assert set(get_args(validator.Entity)) == set(DOCUMENT_ARTIFACT_KINDS)


# ---------------------------------------------------------------------------
# Finding — drift guard against the keys `analitiq.validator.finding` actually
# produces (not xfail: a type-contract fact settled now). `finding()`'s own
# docstring is the source: `rule` only when given, `severity` only for a
# `fail` kind, everything else unconditional.
# ---------------------------------------------------------------------------

def test_finding_matches_the_keys_finding_builder_produces(validator):
    from typing import get_type_hints

    from analitiq.validator.document_set import Finding

    with_rule_and_severity = validator.finding(
        rule="RULE-PKG-030", message_id="m", kind="fail", path="p", message="msg")
    without_rule_or_severity = validator.finding(
        message_id="m", kind="notApplicable", path="p", message="msg")
    possible_keys = set(with_rule_and_severity) | set(without_rule_or_severity)
    always_present = set(with_rule_and_severity) & set(without_rule_or_severity)
    hints = get_type_hints(Finding)
    # `direction` is the one key `finding()` itself never sets — only
    # `validate_doc`'s type-map gap-resolution mode adds it, on top of a
    # `finding()`-built dict — so it is the sole declared key excluded from
    # this pin.
    assert set(hints) - possible_keys == {"direction"}
    # Required vs. optional tracks what these two real calls actually agreed
    # on: a key both included is one `finding()` always sets; a key only one
    # included (`rule`, `severity`) is conditional — as is `direction`, which
    # neither call included at all.
    assert Finding.__required_keys__ == always_present
    assert Finding.__optional_keys__ == (possible_keys - always_present) | {"direction"}

    # `kind`: pinned directly against `finding()`'s own vocabulary constant,
    # not just against the members this test happens to try — a member
    # landing in one and not the other fails here rather than staying invisible.
    from analitiq.validator._core import _KINDS
    assert set(get_args(hints["kind"])) == set(_KINDS)
    for k in _KINDS:
        assert validator.finding(message_id="m", kind=k, path="p", message="msg")["kind"] == k
    with pytest.raises(ValueError):
        validator.finding(message_id="m", kind="not-a-real-kind", path="p", message="msg")

    # `severity`: only ever set on a `fail` finding, derived from the named
    # rule's own severity. RULE-PKG-030 is error-tier, RULE-CTOR-043 is
    # warning-tier — real records, not hand-picked strings — and RULE-CTOR-032
    # (info-tier) proves the third real severity a rule record can declare
    # never reaches a Finding's severity field at all (finding() itself
    # refuses to report an info-tier violation as `kind: fail`).
    warning_finding = validator.finding(
        rule="RULE-CTOR-043", message_id="m", kind="fail", path="p", message="msg")
    observed_severities = {with_rule_and_severity["severity"], warning_finding["severity"]}
    assert observed_severities == set(get_args(hints["severity"]))
    with pytest.raises(ValueError):
        validator.finding(rule="RULE-CTOR-032", message_id="m", kind="fail", path="p", message="msg")


# ---------------------------------------------------------------------------
# Fixtures: a connector-tree DocumentSet and a pipeline-tree DocumentSet, each
# built from content this suite already keeps model-valid.
# ---------------------------------------------------------------------------

def _connector_tree_documents(*, native="STRING", arrow="Utf8") -> dict:
    """A model-valid, coverage-clean connector package as a `DocumentSet`:
    `connector.json` (`corpus/valid_connector.json`, kind=api), a sibling read
    map covering the one native/arrow pair below, and one endpoint
    (`corpus/valid_read.json`) declaring it."""
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": native, "arrow_type": arrow},
    }
    return {
        "connector.json": connector,
        "type-map-read.json": [{"match": "exact", "native_type": native, "arrow_type": arrow}],
        "endpoints/v1__records.json": endpoint,
    }


def _uncovered_endpoint_document(*, endpoint_id="v2__widgets", request_path="/v2/widgets",
                                  native="BOOLEAN", arrow="Boolean") -> dict:
    """A model-valid endpoint declaring a native type `_connector_tree_documents`'s
    read map does not cover — a real, distinguishable `native-type-unresolved`
    finding (RULE-PKG-033). Used where a test must tell "this document was
    reached and validated" apart from "this document was never reached" — a
    clean fixture can't distinguish the two, since both look like zero
    findings — or must show more than one real finding to make an
    order-sensitive comparison non-vacuous."""
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["endpoint_id"] = endpoint_id
    endpoint["operations"]["read"]["request"]["path"] = request_path
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "b": {"type": "string", "native_type": native, "arrow_type": arrow},
    }
    return endpoint


_H = "https://schemas.analitiq.ai"
_SRC, _DST, _PID, _SID = (
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "11111111-1111-4111-8111-111111111111",
    "44444444-4444-4444-8444-444444444444",
)
_EID = derive_db_endpoint_id(None, "public", "orders")
_DBOBJ = build_database_object(None, "public", "orders")

_CONN_WISE = {
    "$schema": f"{_H}/connection/latest.json", "connection_id": _SRC, "connector_id": "wise",
    "display_name": "Wise", "parameters": {"environment": "live"},
    "secret_refs": {"api_token": "env:ANALITIQ_WISE_API_TOKEN"},
}
_CONN_PG = {
    "$schema": f"{_H}/connection/latest.json", "connection_id": _DST, "connector_id": "postgresql",
    "display_name": "Prod Postgres",
    "parameters": {"host": "db.example.com", "port": 5432, "database": "analytics", "ssl_mode": "verify-full"},
    "secret_refs": {"password": "env:ANALITIQ_POSTGRESQL_PASSWORD"},
}
_PIPELINE = {
    "$schema": f"{_H}/pipeline/latest.json", "pipeline_id": _PID, "display_name": "Wise to Postgres",
    "connections": {"source": _SRC, "destinations": [_DST]}, "streams": [_SID],
    "schedule": {"type": "manual", "timezone": "UTC"}, "status": "draft",
}
_STREAM = {
    "$schema": f"{_H}/stream/latest.json", "stream_id": _SID, "pipeline_id": _PID, "display_name": "orders",
    "source": {
        "endpoint_ref": {"scope": "connector", "connection_id": _SRC, "endpoint_id": "transfers"},
        "replication": {"method": "incremental", "cursor_field": "updated_at"},
    },
    "destinations": [{
        "endpoint_ref": {"scope": "connection", "connection_id": _DST, "endpoint_id": _EID,
                         "database_object": _DBOBJ},
        "write": {"mode": "upsert", "conflict_keys": ["id"]},
    }],
    "status": "draft",
}
_DB_ENDPOINT = {
    "$schema": f"{_H}/database-endpoint/latest.json", "endpoint_id": _EID, "display_name": "public.orders",
    "database_object": _DBOBJ,
    "columns": [
        {"name": "id", "native_type": "bigint", "arrow_type": "Int64", "nullable": False, "ordinal_position": 1},
        {"name": "updated_at", "native_type": "timestamptz", "arrow_type": "Timestamp(MICROSECOND, UTC)",
         "nullable": False, "ordinal_position": 2},
    ],
    "primary_keys": ["id"],
}
# A connection's `connector_id` must resolve among the bundle's connector
# identities for the bundle's own referential check to pass at all, so the
# equivalence fixture's embedded connectors below are fully model-valid and
# coverage-clean documents, not identity-only stand-ins: closing the
# embedded-connector coverage gap must not make `validate_pipeline_tree`
# report findings against them that the path-based route never produces.
_CONNECTOR_WISE = {
    "$schema": f"{_H}/connector/latest.json", "connector_id": "wise", "kind": "api",
    "display_name": "Wise",
    "description": "Cross-border money transfer platform.",
    "documentation_url": "https://docs.wise.com/api-docs",
    "version": "1.0.0", "default_transport": "api",
    "transports": {"api": {
        "transport_type": "http", "base_url": "https://api.wise.com",
        "headers": {"Accept": "application/json",
                    "Authorization": {"template": "Bearer ${secrets.api_token}"}},
        "timeout_seconds": 30}},
    "auth": {"type": "api_key"},
    "connection_contract": {
        "inputs": {"api_token": {
            "source": "user", "phase": "pre_auth", "storage": "secrets",
            "type": "string", "required": True, "secret": True,
            "ui": {"label": "API Token", "widget": "password", "help_text": "Wise API token."}}},
        "required_for_activation": ["secrets.api_token"]},
}
_CONNECTOR_WISE_TYPE_MAP_READ = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
_WISE_TRANSFERS_ENDPOINT = {
    "$schema": f"{_H}/api-endpoint/latest.json", "endpoint_id": "transfers",
    "operations": {"read": {
        "request": {"method": "GET", "path": "/transfers"}, "params": {},
        "response": {
            "records": {"ref": "response.body"},
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "array",
                "items": {"type": "object", "properties": {
                    "id": {"type": "string", "native_type": "STRING", "arrow_type": "Utf8"}}}}}}},
}
_CONNECTOR_PG = {
    "$schema": f"{_H}/connector/latest.json", "kind": "database", "connector_id": "postgresql",
    "display_name": "PostgreSQL",
    "description": "Relational database.",
    "version": "1.0.0", "default_transport": "database",
    "transports": {"database": {
        "transport_type": "sqlalchemy", "driver": "postgresql+psycopg",
        "dsn": {
            "kind": "url_template",
            "template": "postgresql+psycopg://{username}:{password}@{host}:{port}/{database}",
            "bindings": {
                "username": {"value": {"ref": "connection.parameters.username"}, "encoding": "url_userinfo"},
                "password": {"value": {"ref": "secrets.password"}, "encoding": "url_userinfo"},
                "host": {"value": {"ref": "connection.parameters.host"}, "encoding": "host"},
                "port": {"value": {"ref": "connection.parameters.port"}, "encoding": "raw"},
                "database": {"value": {"ref": "connection.parameters.database"}, "encoding": "url_path_segment"},
            }}}},
    "auth": {"type": "db"},
    "connection_contract": {"inputs": {
        "host": {"source": "user", "phase": "pre_auth", "storage": "connection.parameters",
                 "type": "string", "required": True},
        "port": {"source": "user", "phase": "pre_auth", "storage": "connection.parameters",
                 "type": "integer", "required": True, "default": 5432},
        "database": {"source": "user", "phase": "pre_auth", "storage": "connection.parameters",
                     "type": "string", "required": True},
        "username": {"source": "user", "phase": "auth", "storage": "connection.parameters",
                     "type": "string", "required": True},
        "password": {"source": "user", "phase": "auth", "storage": "secrets",
                     "type": "string", "required": True, "secret": True},
    }},
    "resource_discovery": {
        "strategy": "information_schema", "transport_ref": "database",
        "implementation": {"type": "builtin"},
        "produces": ["connection.endpoints", "connection.type_map"],
        "triggers": {"list_resources": "on_activation", "describe_resource": "on_resource_selected"}},
}
_CONNECTOR_PG_TYPE_MAP_READ = [{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}]
_CONNECTOR_PG_TYPE_MAP_WRITE = [
    {"match": "exact", "native_type": "bigint", "arrow_type": "Int64"},
    {"match": "regex", "native_type": "TEXT", "arrow_type": ".*"},
]
def _pipeline_core_documents() -> dict:
    """The connection, stream, pipeline, and destination-endpoint documents a
    pipeline-tree `DocumentSet` carries regardless of what its embedded
    `connectors/` subtree looks like."""
    return {
        "connections/wise/connection.json": _CONN_WISE,
        "connections/postgresql/connection.json": _CONN_PG,
        f"connections/postgresql/definition/endpoints/{_EID}.json": _DB_ENDPOINT,
        "pipelines/p/streams/orders.json": _STREAM,
        "pipelines/p/pipeline.json": _PIPELINE,
    }


def _pipeline_tree_documents() -> dict:
    """A model-valid draft pipeline bundle as a `DocumentSet`, laid out at the
    same relative paths `_assemble_bundle`
    (`plugins/analitiq-pipeline-builder/scripts/validate.py`) already resolves
    from a filesystem root — so the document-set route and that function's
    on-disk route are given byte-identical content, just supplied two
    different ways. `wise`'s and `postgresql`'s embedded
    `connectors/<slug>/definition/...` subtrees are fully model-valid and
    coverage-clean: this is the fixture the equivalence test uses, so
    resolving an embedded subtree through `validate_connector_tree` must
    contribute no findings the path-based route doesn't already produce."""
    return {
        **_pipeline_core_documents(),
        "connectors/wise/definition/connector.json": _CONNECTOR_WISE,
        "connectors/wise/definition/type-map-read.json": _CONNECTOR_WISE_TYPE_MAP_READ,
        "connectors/wise/definition/endpoints/transfers.json": _WISE_TRANSFERS_ENDPOINT,
        "connectors/postgresql/definition/connector.json": _CONNECTOR_PG,
        "connectors/postgresql/definition/type-map-read.json": _CONNECTOR_PG_TYPE_MAP_READ,
        "connectors/postgresql/definition/type-map-write.json": _CONNECTOR_PG_TYPE_MAP_WRITE,
    }


def _pipeline_tree_documents_with_two_findings() -> dict:
    """`_pipeline_tree_documents` with two additional connection ids on the
    pipeline's own `connections.destinations` that no bundled connection
    document resolves — two real, distinguishable `connection-ref-unresolved`
    findings (one per missing id) that `validate_pipeline_bundle` reports
    identically down either route, so the byte-identical equivalence
    comparison below is checking real, order-sensitive content instead of two
    empty findings lists."""
    documents = _pipeline_tree_documents()
    pipeline = {**documents["pipelines/p/pipeline.json"]}
    pipeline["connections"] = {
        **pipeline["connections"],
        "destinations": [*pipeline["connections"]["destinations"], "missing-connection-a", "missing-connection-b"],
    }
    return {**documents, "pipelines/p/pipeline.json": pipeline}


def _pipeline_tree_documents_with_embedded_connectors() -> dict:
    """`_pipeline_core_documents` plus `wise`'s and `postgresql`'s own
    model-valid `connector.json` (the same documents the equivalence fixture
    ships fully covered) shipped alone — no sibling type-map or `endpoints/`
    directory — so a resolver that closes the embedded-connector coverage gap
    has something real to report for each: today's plugin reads only a
    connection's connector identity (for the referential check that its
    `connector_id` is bundled) and `wise`'s endpoint ids (for stream-ref
    resolution) from an embedded `connectors/<slug>/definition/...` subtree,
    never `check_coverage`'s own findings against it. `wise`'s own
    RULE-PKG-030/035 findings prove the gap is closed, and `postgresql`'s own
    RULE-PKG-030 finding proves a crash isolated to `wise`'s subtree still
    leaves the rest of the walk checkable."""
    return {
        **_pipeline_core_documents(),
        "connectors/wise/definition/connector.json": _CONNECTOR_WISE,
        "connectors/postgresql/definition/connector.json": _CONNECTOR_PG,
    }


def _write_tree(root: Path, documents: dict) -> None:
    for rel, doc in documents.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))


# ---------------------------------------------------------------------------
# validate_doc, type-map gap-resolution mode (`probes` given): never raises,
# reports through the same ValidationEnvelope every function in this module
# uses.
# ---------------------------------------------------------------------------

#: A minimal valid read map, covering "STRING" and nothing else — so "BIGINT"
#: is a probe it genuinely does not resolve.
_READ_RULES = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]


def test_gap_resolution_reports_unreadable_map_without_raising(validator):
    result = validator.validate_doc(
        doc={"type-map-read.json": "not json"}, direction="read", probes=["STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == [
        "type-map-unreadable", "gap-resolution-skipped"]
    assert result["findings"][0]["kind"] == "fail"
    assert result["findings"][0]["severity"] == "error"
    assert "direction" not in result["findings"][0]
    # The parser already knows where the text stopped being JSON; a map reported
    # only as "unreadable" leaves its author bisecting a file to find out.
    assert "line 1" in result["findings"][0]["message"], result["findings"][0]


def test_gap_resolution_reports_a_model_error_against_the_rule_that_claims_it(validator):
    # A map that parses but fails its model is checked by the same
    # `_type_map_findings` every other type-map check in the package goes
    # through, so the verdict arrives naming the record it violates, at the
    # position inside the map that violates it — not flattened into one
    # ruleless finding of this mode's own.
    rule = {"match": "exact", "native_type": "VARCHAR${", "arrow_type": "Utf8"}
    result = validator.validate_doc(
        doc={"m.json": [rule]}, direction="write", probes=["Utf8"])
    assert result["passed"] is False
    malformed = [f for f in result["findings"] if f.get("rule") == "RULE-TMAP-008"]
    assert len(malformed) == 1, result["findings"]
    assert malformed[0]["severity"] == "error"
    # Re-pathed onto the key it was supplied under: more than one map can be in
    # hand, and the model's own path alone would not say which.
    assert malformed[0]["path"] == "m.json/0/exact"


def test_gap_resolution_direction_selects_the_matching_model(validator):
    # Valid under TypeMapReadDoc (native_type is a bare matcher, unvalidated for
    # placeholders) but invalid under TypeMapWriteDoc (native_type is the write
    # side's render template, and `${` with no closing `}` is malformed there) —
    # so direction alone decides which model this rule is checked against.
    rule = {"match": "exact", "native_type": "VARCHAR${", "arrow_type": "Utf8"}
    read_result = validator.validate_doc(
        doc={"m.json": [rule]}, direction="read", probes=["VARCHAR${"])
    assert read_result == {"passed": True, "findings": []}
    write_result = validator.validate_doc(
        doc={"m.json": [rule]}, direction="write", probes=["Utf8"])
    assert any(f.get("rule") == "RULE-TMAP-008" for f in write_result["findings"]), write_result


def test_gap_resolution_advisory_map_findings_still_contribute_their_rules(validator):
    # A duplicate rule is a warning: the map is imperfect but entirely usable,
    # so it resolves probes and gaps are not withheld. Only a map that cannot
    # contribute its rules withholds them.
    rule = {"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}
    result = validator.validate_doc(
        doc={"type-map-read.json": [rule, rule]}, direction="read", probes=["STRING"])
    assert result["passed"] is True
    assert [f.get("rule") for f in result["findings"]] == ["RULE-TMAP-022"]
    assert not any(f["message_id"] == "gap-resolution-skipped" for f in result["findings"])


def test_gap_resolution_reports_an_unresolved_probe_as_informational(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING", "BIGINT"])
    gaps = [f for f in result["findings"] if f["message_id"] == "type-map-gap"]
    assert len(gaps) == 1, result["findings"]
    assert gaps[0]["kind"] == "informational" and "severity" not in gaps[0]
    assert gaps[0]["direction"] == "read"
    # `path` is a required, consumer-facing field, so the convention it carries
    # for this finding is pinned rather than left to an implementation: a gap
    # is a fact about a probe against the whole supplied set, not about any one
    # map, so there is no key to name and the probe travels in the message.
    assert gaps[0]["path"] == ""
    assert "BIGINT" in gaps[0]["message"]
    # An informational type-map-gap never costs a pass on its own.
    assert result["passed"] is True


def test_gap_resolution_validates_every_map_even_when_no_probe_needs_it(validator):
    # The first map already covers every probe, and the second is malformed.
    # An implementation that resolved probes first and stopped once they were
    # all covered would never read the second map, report nothing, and pass —
    # so this pins that every supplied map is checked against the direction's
    # model regardless of what resolution needed, the way `type_map_gaps.py`'s
    # own `_load_rules` runs over every `--map` before any probe is resolved.
    maps = {
        "type-map-covering.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        "type-map-broken.json": [{"native_type": "BIGINT"}],
    }
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING"])
    assert result["passed"] is False
    assert any(f["path"].startswith("type-map-broken.json") and f["severity"] == "error"
               for f in result["findings"] if "severity" in f), result["findings"]


def test_gap_resolution_with_no_probes_still_validates_the_maps(validator):
    # The same claim with resolution removed entirely: an empty probe list is a
    # check of the maps, not a trivially-passing no-op.
    maps = {"type-map-broken.json": [{"native_type": "BIGINT"}]}
    result = validator.validate_doc(doc=maps, direction="read", probes=[])
    assert result["passed"] is False
    assert [f["path"] for f in result["findings"] if "severity" in f] == ["type-map-broken.json/0"]


def test_gap_resolution_fully_covered_reports_no_findings(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING"])
    assert result == {"passed": True, "findings": []}


def test_gap_resolution_falls_through_to_a_later_map_for_a_probe_the_first_does_not_cover(validator):
    # Neither map alone covers every probe: a probe the first key's map does
    # not render must still resolve via the second key's map rather than
    # being reported as a gap just because the first key didn't cover it.
    # (This mode reports only findings wrapped in a ValidationEnvelope, never
    # a resolved value, so this is the one multi-map behaviour it can
    # observe — WHICH map's value wins on a genuine conflict is not visible
    # here.)
    maps = {
        "type-map-primary.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        "type-map-fallback.json": [{"match": "exact", "native_type": "BIGINT", "arrow_type": "Int64"}],
    }
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING", "BIGINT"])
    assert result == {"passed": True, "findings": []}, result


def test_gap_resolution_missing_direction_reports_invalid_direction(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.validate_doc(doc=maps, probes=["STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-direction"]
    assert result["findings"][0]["kind"] == "fail"
    assert result["findings"][0]["severity"] == "error"
    assert "direction" not in result["findings"][0]


def test_gap_resolution_bad_direction_value_reports_invalid_direction(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.validate_doc(doc=maps, direction="sideways", probes=["STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-direction"]
    assert "direction" not in result["findings"][0]


def test_gap_resolution_bad_direction_short_circuits_before_doc_is_checked(validator):
    # `doc` here is independently invalid (not a list of rules) — if direction
    # were validated after doc normalization, this call would also report
    # type-map-unreadable. It must not: direction is checked first, so the bad
    # doc is never reached.
    maps = {"type-map-read.json": "not a list"}
    result = validator.validate_doc(doc=maps, direction="sideways", probes=["STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-direction"]


def test_gap_resolution_non_list_probes_reports_invalid_probes(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.validate_doc(doc=maps, direction="read", probes="STRING")
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-probes"]


def test_gap_resolution_non_str_probe_element_reports_invalid_probes(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.validate_doc(doc=maps, direction="read", probes=[1, "STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-probes"]


def test_gap_resolution_empty_doc_reports_missing_type_map(validator):
    result = validator.validate_doc(doc={}, direction="read", probes=["STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["missing-type-map"]


@pytest.mark.parametrize("bad_doc", [[], "not-a-mapping", 42, ("a", "b")])
def test_gap_resolution_non_mapping_doc_reports_missing_type_map(validator, bad_doc):
    result = validator.validate_doc(doc=bad_doc, direction="read", probes=["STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["missing-type-map"]
    assert result["findings"][0]["kind"] == "fail"
    assert result["findings"][0]["severity"] == "error"
    assert "direction" not in result["findings"][0]
    # The message names what was wrong with `doc`, not just that a map is
    # absent: a caller who passed a list did supply maps, in the wrong shape.
    assert type(bad_doc).__name__ in result["findings"][0]["message"]


def test_gap_resolution_explicit_none_probes_selects_gap_resolution_mode(validator):
    # probes=None is NOT the same as omitting probes: the default is a private
    # sentinel, so an explicit None still selects gap-resolution mode and is
    # rejected there as an invalid probes value (None is not a list) — proving
    # the sentinel, not None itself, is what selects ordinary mode.
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.validate_doc(doc=maps, direction="read", probes=None)
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-probes"]


def test_gap_resolution_reports_a_read_filename_used_with_write_direction(validator):
    # Scoped, not a bare top-level key: a match comparing the whole key against
    # the literal filename would silently pass this scoped key through
    # unchecked. The key's final segment is the load-bearing filename, so a
    # scoped key is what distinguishes a basename match from a whole-key one.
    maps = {"connections/foo/type-map-read.json": [
        {"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.validate_doc(doc=maps, direction="write", probes=["Utf8"])
    assert result["passed"] is False
    # The mismatch, and then `missing-type-map` rather than a gap: refusing the
    # only map leaves nothing for the probe to have been measured against, so
    # reporting `Utf8` as a gap would say the supplied write maps lack a rule
    # for it when no write map was supplied at all.
    assert [f["message_id"] for f in result["findings"]] == [
        "direction-filename-mismatch", "missing-type-map"]
    assert result["findings"][0]["path"] == "connections/foo/type-map-read.json"
    assert result["findings"][0]["direction"] == "write"
    assert not any(f["message_id"] == "type-map-gap" for f in result["findings"])


def test_gap_resolution_reports_a_write_filename_used_with_read_direction(validator):
    maps = {"connections/foo/type-map-write.json": [
        {"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == [
        "direction-filename-mismatch", "missing-type-map"]
    assert result["findings"][0]["path"] == "connections/foo/type-map-write.json"
    assert result["findings"][0]["direction"] == "read"


def test_gap_resolution_refusing_every_map_is_not_a_gap(validator):
    # The refusal empties the set, so nothing was consulted. A write map does
    # resolve a read probe if it is allowed to — it renders through rules
    # authored for the opposite mapping — so "refused" is not "could not have
    # matched", and the honest report is that this set supplies no read map.
    maps = {
        "type-map-write.json": [{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}],
        "nested/type-map-write.json": [
            {"match": "exact", "arrow_type": "Int64", "native_type": "BIGINT"}],
    }
    result = validator.validate_doc(doc=maps, direction="read", probes=["TEXT", "BIGINT"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == [
        "direction-filename-mismatch", "direction-filename-mismatch", "missing-type-map"]
    assert "read map" in result["findings"][-1]["message"]


def test_gap_resolution_losing_a_wrong_direction_key_still_reports_a_real_gap(validator):
    # The lost key is a write map's, and a read run would have refused it
    # anyway — so losing it costs this call nothing and must not withhold the
    # genuine BIGINT gap the usable read map leaves.
    maps = {
        "type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        "type-map-write.json": [{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}],
        "./type-map-write.json": [{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}],
    }
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING", "BIGINT"])
    ids = [f["message_id"] for f in result["findings"]]
    assert "normalized-key-collision" in ids
    assert "gap-resolution-skipped" not in ids
    gaps = [f for f in result["findings"] if f["message_id"] == "type-map-gap"]
    assert len(gaps) == 1 and "BIGINT" in gaps[0]["message"], result["findings"]


def test_gap_resolution_both_halves_of_a_connector_still_report_a_real_gap(validator):
    # The obvious thing a caller does: hand over a connector's own two maps and
    # ask about the read direction. The write map is refused, which is correct,
    # and STRING resolves from the read map — so BIGINT going unresolved is a
    # genuine gap and must be reported rather than swallowed by the refusal.
    maps = {
        "type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        "type-map-write.json": [{"match": "exact", "arrow_type": "Utf8", "native_type": "TEXT"}],
    }
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING", "BIGINT"])
    gaps = [f for f in result["findings"] if f["message_id"] == "type-map-gap"]
    assert len(gaps) == 1, result["findings"]
    assert "BIGINT" in gaps[0]["message"]


@_xfail("validate_doc")
def test_validate_doc_with_document_set_shaped_doc_and_no_probes_uses_ordinary_mode(validator):
    # `doc` here is shaped exactly like the DocumentSet gap-resolution mode
    # takes (path-like keys, list-of-rule values) but `probes` is omitted —
    # mode selection must still land on ordinary document mode, proving it
    # reads only whether `probes` was passed, never `doc`'s own shape.
    doc = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    expected_findings = validator.validate_document(doc)
    expected = {"passed": not any(validator.finding_costs_a_pass(f) for f in expected_findings),
                "findings": expected_findings}
    assert json.dumps(validator.validate_doc(doc)) == json.dumps(expected)


# ---------------------------------------------------------------------------
# Key handling, shared by every function below that takes a DocumentSet
# (validate_connector_tree's and validate_pipeline_tree's `documents`,
# validate_doc's `doc` in its type-map gap-resolution mode). Normalization and
# rejection (leading `./`, invalid keys, key/path conflicts) are exercised
# through all three entry points below, since each could independently
# mishandle the same input; validate_connector_tree's own coverage-specific
# behavior (finding order, partial-failure isolation) is exercised there
# alone — those aren't claims about the shared key contract.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_tree")
def test_leading_dot_slash_is_normalized_away(validator):
    with_prefix = validator.validate_connector_tree({f"./{k}": v for k, v in _connector_tree_documents().items()})
    without_prefix = validator.validate_connector_tree(_connector_tree_documents())
    assert with_prefix == without_prefix


@_xfail("validate_pipeline_tree")
def test_leading_dot_slash_is_normalized_away_for_pipeline_tree(validator):
    with_prefix = validator.validate_pipeline_tree({f"./{k}": v for k, v in _pipeline_tree_documents().items()})
    without_prefix = validator.validate_pipeline_tree(_pipeline_tree_documents())
    assert with_prefix == without_prefix


@pytest.mark.parametrize("spelling", [
    "./type-map-read.json", "././type-map-read.json", "a/../type-map-read.json",
    "./a/./../type-map-read.json",
])
def test_key_spellings_that_name_one_document_are_normalized_for_gap_resolution_mode(
        validator, spelling):
    # Compared against a literal envelope, not against another call: two runs
    # that both normalize to nothing usable would agree with each other while
    # normalizing nothing. The map is deliberately model-invalid so the key
    # reaches a finding whose `path` is the canonical spelling — an equality
    # of two empty envelopes grades neither.
    result = validator.validate_doc(
        doc={spelling: [{"match": "exact", "native_type": "STRING"}]},
        direction="read", probes=["STRING"])
    paths = {f.get("path", "") for f in result["findings"]}
    assert any(p.startswith("type-map-read.json") for p in paths), result["findings"]
    assert not any(p.startswith((".", "a/")) for p in paths), result["findings"]


@pytest.mark.parametrize("escaping_key", [
    # Each satisfies every rejection test as written and fails one as resolved,
    # which is why the tests are applied to the canonical form.
    "a/../../b.json", "a/..", "./", "a/b/../../../c.json",
])
def test_keys_that_only_escape_once_resolved_are_rejected_for_gap_resolution_mode(
        validator, escaping_key):
    result = validator.validate_doc(
        doc={"type-map-read.json": _READ_RULES, escaping_key: _READ_RULES},
        direction="read", probes=["STRING"])
    assert result["passed"] is False
    assert any(f["message_id"] == "invalid-key" and f["path"] == escaping_key
               for f in result["findings"]), result["findings"]


@pytest.mark.parametrize("bad_key", ["/connector.json", "../connector.json", ""])
@_xfail("validate_connector_tree")
def test_invalid_keys_are_reported_not_raised(validator, bad_key):
    documents = {**_connector_tree_documents(), bad_key: {}}
    result = validator.validate_connector_tree(documents)
    assert any(f["message_id"] == "invalid-key" and f["path"] == bad_key for f in result["findings"])
    assert result["passed"] is False


@pytest.mark.parametrize("bad_key", ["/pipelines/p/pipeline.json", "../pipeline.json", ""])
@_xfail("validate_pipeline_tree")
def test_invalid_keys_are_reported_not_raised_for_pipeline_tree(validator, bad_key):
    documents = {**_pipeline_tree_documents(), bad_key: {}}
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "invalid-key" and f["path"] == bad_key for f in result["findings"])
    assert result["passed"] is False


@pytest.mark.parametrize("bad_key", ["/type-map-read.json", "../type-map-read.json", ""])
def test_invalid_keys_are_reported_not_raised_for_gap_resolution_mode(validator, bad_key):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}], bad_key: []}
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING"])
    assert any(f["message_id"] == "invalid-key" and f["path"] == bad_key for f in result["findings"])
    assert result["passed"] is False


@_xfail("validate_connector_tree")
def test_key_that_is_both_document_and_directory_prefix_conflicts(validator):
    documents = {**_connector_tree_documents(), "endpoints/v1__records.json/extra.json": {}}
    result = validator.validate_connector_tree(documents)
    assert any(f["message_id"] == "key-path-conflict" for f in result["findings"])


@_xfail("validate_pipeline_tree")
def test_key_that_is_both_document_and_directory_prefix_conflicts_for_pipeline_tree(validator):
    documents = {**_pipeline_tree_documents(), "connectors/wise/definition/connector.json/extra.json": {}}
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "key-path-conflict" for f in result["findings"])


def test_key_that_is_both_document_and_directory_prefix_conflicts_for_gap_resolution_mode(validator):
    maps = {
        "type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        "type-map-read.json/extra.json": [],
    }
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING"])
    assert any(f["message_id"] == "key-path-conflict" for f in result["findings"])


@_xfail("validate_connector_tree")
def test_normalized_key_collision_is_reported_not_silently_overwritten(validator):
    # "connector.json" and "./connector.json" are distinct dict keys that both
    # normalize to "connector.json" — a collision the ./-stripping itself
    # creates, not one present in the raw input's own key set.
    documents = {**_connector_tree_documents(), "./connector.json": {"kind": "database"}}
    result = validator.validate_connector_tree(documents)
    assert any(f["message_id"] == "normalized-key-collision" for f in result["findings"])
    assert result["passed"] is False


@_xfail("validate_pipeline_tree")
def test_normalized_key_collision_is_reported_not_silently_overwritten_for_pipeline_tree(validator):
    documents = {**_pipeline_tree_documents(), "./pipelines/p/pipeline.json": {"kind": "pipeline"}}
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "normalized-key-collision" for f in result["findings"])
    assert result["passed"] is False


def test_normalized_key_collision_is_reported_not_silently_overwritten_for_gap_resolution_mode(validator):
    maps = {
        "m.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}],
        "./m.json": [{"match": "exact", "native_type": "BIGINT", "arrow_type": "Int64"}],
    }
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING"])
    assert any(f["message_id"] == "normalized-key-collision" for f in result["findings"])
    assert result["passed"] is False


@_xfail("validate_connector_tree")
def test_output_finding_order_is_independent_of_input_mapping_order(validator):
    # Two distinct uncovered endpoints, not the clean tree: comparing two
    # empty findings lists cannot detect order-sensitivity at all.
    documents = {
        **_connector_tree_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        "endpoints/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    forward = validator.validate_connector_tree(documents)
    assert len(forward["findings"]) >= 2, forward  # non-vacuous: order genuinely matters below
    reversed_documents = dict(reversed(list(documents.items())))
    backward = validator.validate_connector_tree(reversed_documents)
    assert forward == backward


@_xfail("validate_pipeline_tree")
def test_output_finding_order_is_independent_of_input_mapping_order_for_pipeline_tree(validator):
    # The pipeline-tree root is resolved by pattern match over documents'
    # keys, unlike the connector tree's fixed-key lookup — so this route has
    # its own chance to let iteration order leak into which key resolves the
    # root, or into finding order generally, and needs its own test proving
    # it doesn't.
    documents = _pipeline_tree_documents_with_two_findings()
    forward = validator.validate_pipeline_tree(documents)
    assert len(forward["findings"]) >= 2, forward  # non-vacuous: order genuinely matters below
    reversed_documents = dict(reversed(list(documents.items())))
    backward = validator.validate_pipeline_tree(reversed_documents)
    assert forward == backward


@_xfail("validate_connector_tree")
def test_one_invalid_key_does_not_block_validating_the_rest(validator):
    documents = {
        **_connector_tree_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(),
        "../escape.json": {},
    }
    result = validator.validate_connector_tree(documents)
    assert any(f["message_id"] == "invalid-key" for f in result["findings"])
    assert result["passed"] is False
    # The rest of the tree was still validated: the second endpoint's own
    # real coverage finding — which only exists if that document was reached
    # and checked — is present alongside the invalid-key finding.
    assert any(f["message_id"] == "native-type-unresolved" for f in result["findings"]), result["findings"]


# ---------------------------------------------------------------------------
# Value handling, shared by every function below that takes a DocumentSet
# (validate_connector_tree's and validate_pipeline_tree's `documents`,
# validate_doc's `doc` in its type-map gap-resolution mode) — exercised
# through all three entry points below, since each could independently
# mishandle the same input.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_tree")
def test_bytes_value_is_decoded_as_utf8_with_bom_stripped(validator):
    documents = _connector_tree_documents()
    text_result = validator.validate_connector_tree(documents)
    as_bytes = dict(documents)
    as_bytes["connector.json"] = ("\ufeff" + json.dumps(documents["connector.json"])).encode("utf-8")
    bytes_result = validator.validate_connector_tree(as_bytes)
    assert bytes_result == text_result


@_xfail("validate_pipeline_tree")
def test_bytes_value_is_decoded_as_utf8_with_bom_stripped_for_pipeline_tree(validator):
    documents = _pipeline_tree_documents()
    text_result = validator.validate_pipeline_tree(documents)
    as_bytes = dict(documents)
    as_bytes["pipelines/p/pipeline.json"] = (
        "\ufeff" + json.dumps(documents["pipelines/p/pipeline.json"])).encode("utf-8")
    bytes_result = validator.validate_pipeline_tree(as_bytes)
    assert bytes_result == text_result


def test_bytes_value_is_decoded_as_utf8_with_bom_stripped_for_gap_resolution_mode(validator):
    maps = {"type-map-read.json": [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]}
    text_result = validator.validate_doc(doc=maps, direction="read", probes=["STRING"])
    as_bytes = {"type-map-read.json": ("\ufeff" + json.dumps(maps["type-map-read.json"])).encode("utf-8")}
    bytes_result = validator.validate_doc(doc=as_bytes, direction="read", probes=["STRING"])
    assert bytes_result == text_result


@_xfail("validate_connector_tree")
def test_non_str_bytes_object_value_is_invalid_not_raised(validator):
    documents = {**_connector_tree_documents(), "connector.json": 42}
    result = validator.validate_connector_tree(documents)
    assert any(f["message_id"] == "invalid-value" and f["path"] == "connector.json"
               for f in result["findings"])
    assert result["passed"] is False


@_xfail("validate_pipeline_tree")
def test_non_str_bytes_object_value_is_invalid_not_raised_for_pipeline_tree(validator):
    documents = {**_pipeline_tree_documents(), "pipelines/p/pipeline.json": 42}
    result = validator.validate_pipeline_tree(documents)
    assert any(f["message_id"] == "invalid-value" and f["path"] == "pipelines/p/pipeline.json"
               for f in result["findings"])
    assert result["passed"] is False


def test_invalid_value_applies_to_gap_resolution_maps_too(validator):
    result = validator.validate_doc(doc={"type-map-read.json": 42}, direction="read", probes=["STRING"])
    assert any(f["message_id"] == "invalid-value" for f in result["findings"])
    assert result["passed"] is False


# ---------------------------------------------------------------------------
# Package kind is declared by the caller, not detected. There is no function
# here that inspects a document set's shape to decide whether it is a
# connector package or a pipeline bundle — a caller (or the wire-schema
# wrapper in front of this module) already knows which it is sending and
# calls validate_connector_tree or validate_pipeline_tree directly.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_tree")
def test_validate_connector_tree_validates_its_own_root_shape_directly(validator):
    result = validator.validate_connector_tree(_connector_tree_documents())
    assert result["passed"] is True


@_xfail("validate_connector_tree")
def test_validate_connector_tree_reports_a_missing_root_document(validator):
    documents = {k: v for k, v in _connector_tree_documents().items() if k != "connector.json"}
    result = validator.validate_connector_tree(documents)
    assert result["passed"] is False
    assert any(f["message_id"] == "missing-package-root" and f["path"] == "connector.json"
               for f in result["findings"])


@_xfail("validate_connector_tree")
def test_validate_connector_tree_reports_a_missing_root_document_for_an_empty_set(validator):
    result = validator.validate_connector_tree({})
    assert result["passed"] is False
    assert any(f["message_id"] == "missing-package-root" for f in result["findings"])


@_xfail("validate_connector_tree")
def test_validate_connector_tree_rejects_a_present_but_wrong_kind_root(validator):
    # `connector.json` is present, so missing-package-root does not fire — but
    # its content is a model-valid pipeline document, not a connector one.
    documents = {**_connector_tree_documents(), "connector.json": _PIPELINE}
    result = validator.validate_connector_tree(documents)
    assert result["passed"] is False


@_xfail("validate_pipeline_tree")
def test_validate_pipeline_tree_validates_its_own_root_shape_directly(validator):
    result = validator.validate_pipeline_tree(_pipeline_tree_documents())
    assert result["passed"] is True


@_xfail("validate_pipeline_tree")
def test_validate_pipeline_tree_reports_a_missing_root_document(validator):
    documents = {k: v for k, v in _pipeline_tree_documents().items() if k != "pipelines/p/pipeline.json"}
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False
    assert any(f["message_id"] == "missing-package-root" and f["path"] == "pipelines/*/pipeline.json"
               for f in result["findings"])


@_xfail("validate_pipeline_tree")
def test_validate_pipeline_tree_reports_a_missing_root_document_for_an_empty_set(validator):
    result = validator.validate_pipeline_tree({})
    assert result["passed"] is False
    assert any(f["message_id"] == "missing-package-root" and f["path"] == "pipelines/*/pipeline.json"
               for f in result["findings"])


@_xfail("validate_pipeline_tree")
def test_validate_pipeline_tree_rejects_a_present_but_wrong_kind_root(validator):
    # pipelines/p/pipeline.json is present, so missing-package-root does not
    # fire — but its content is a model-valid connector document, not a
    # pipeline one. _CONNECTOR_WISE also carries none of _PIPELINE's fields
    # (pipeline_id, connections, streams), so swapping it in breaks this
    # bundle's own referential checks (the stream's pipeline_id ref, the
    # connections' connection-id refs) too — passed is False either way, so a
    # bare passed-is-False assertion can't tell "the root's own content was
    # rejected" apart from "something else in the bundle broke while an
    # implementation silently accepted the root". The finding must be scoped
    # to the root's own key.
    documents = {**_pipeline_tree_documents(), "pipelines/p/pipeline.json": _CONNECTOR_WISE}
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False
    assert any(f["path"] == "pipelines/p/pipeline.json" for f in result["findings"]), result["findings"]


@_xfail("validate_pipeline_tree")
def test_validate_pipeline_tree_reports_ambiguous_root_for_more_than_one_match(validator):
    # Two keys both matching pipelines/<slug>/pipeline.json — resolving this
    # by iteration order would be exactly the silent-pick hazard
    # normalized-key-collision exists to prevent for a fixed key; a pattern
    # root gets no exemption from that rule.
    documents = {**_pipeline_tree_documents(), "pipelines/q/pipeline.json": _PIPELINE}
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False
    assert any(f["message_id"] == "ambiguous-package-root"
               and f["path"] == "pipelines/p/pipeline.json,pipelines/q/pipeline.json"
               for f in result["findings"]), result["findings"]


@_xfail("validate_pipeline_tree")
def test_embedded_connector_subtree_gets_its_own_coverage_findings(validator):
    """`connectors/wise/definition/connector.json` (kind=api) ships no sibling
    type-map or `endpoints/` directory — today's plugin never notices, because
    `_connector_endpoint_sets` only reads endpoint ids for stream-ref
    resolution. `validate_pipeline_tree` must resolve this subtree by calling
    `validate_connector_tree` on it directly — there is no package-kind
    detection step to route through — and report its OWN coverage findings
    (RULE-PKG-030 missing read map, RULE-PKG-035 missing endpoints/), scoped
    under the subtree's key prefix."""
    result = validator.validate_pipeline_tree(_pipeline_tree_documents_with_embedded_connectors())
    scoped = [f for f in result["findings"] if f["path"].startswith("connectors/wise/")]
    assert any(f["rule"] == "RULE-PKG-030" for f in scoped), result["findings"]
    assert any(f["rule"] == "RULE-PKG-035" for f in scoped), result["findings"]


# ---------------------------------------------------------------------------
# Failure isolation, including the recursive embedded-package
# case. A self-referential dict is used as the crash trigger: whatever the
# implementation's internal walk turns out to be (recursive traversal,
# re-serialization for the equivalence contract, deep comparison), a cyclic
# structure is a canonical way to force it to fail rather than merely report
# an ordinary shape defect.
# ---------------------------------------------------------------------------

def _cyclic_dict() -> dict:
    node: dict = {"endpoint_id": "widgets"}
    node["self"] = node
    return node


@_xfail("validate_connector_tree")
def test_one_document_crash_is_isolated_to_its_key(validator):
    documents = {
        **_connector_tree_documents(),
        "endpoints/widgets.json": _cyclic_dict(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(),
    }
    result = validator.validate_connector_tree(documents)
    assert result["passed"] is False
    crashed = [f for f in result["findings"] if f["path"] == "endpoints/widgets.json"]
    # notApplicable, not fail: a crash settled nothing about whether the
    # document it was reading is valid, and `_run_guarded` classifies a
    # crashing check the same way. Naming no rule, it still costs the pass
    # asserted above — one `message_id` cannot mean two kinds across the
    # routes of one module.
    assert any(f["message_id"] == "internal-error" and f["kind"] == "notApplicable"
               for f in crashed), result["findings"]
    assert all("severity" not in f and "rule" not in f
               for f in crashed if f["message_id"] == "internal-error")
    # The rest of the tree was still validated — a real, distinguishable finding
    # for the OTHER (well-formed but deliberately uncovered) endpoint is
    # present, proving it was reached rather than silently dropped once one key
    # crashed.
    assert any(f["message_id"] == "native-type-unresolved" for f in result["findings"]), result["findings"]


@_xfail("validate_pipeline_tree")
def test_embedded_package_crash_is_isolated_to_its_subtree_prefix(validator):
    documents = {
        **_pipeline_tree_documents_with_embedded_connectors(),
        "connectors/wise/definition/connector.json": _cyclic_dict(),
    }
    result = validator.validate_pipeline_tree(documents)
    assert result["passed"] is False
    assert any(f["path"].startswith("connectors/wise/") and f["message_id"] == "internal-error"
               for f in result["findings"]), result["findings"]
    # The rest of the pipeline is still reported: `postgresql`'s embedded
    # connector is itself coverage-dirty (no type-map, same as `wise`), so its
    # own real RULE-PKG-030 finding proves the walk continued past the crash
    # rather than merely not reporting on the crashed subtree twice.
    assert any(f["path"].startswith("connectors/postgresql/") and f["rule"] == "RULE-PKG-030"
               for f in result["findings"]), result["findings"]


class _ExplodingRules(list):
    """A rule list that is a `list` by every shape check and raises when read.

    Gap resolution's crash trigger is not `_cyclic_dict`: a cyclic structure is
    an ordinary shape defect on this route and gets reported as one — a bare
    cyclic dict is not a list, so it is `type-map-unreadable`, and one wrapped
    in a list fails the type-map model, so it is reported against the record
    that model violation belongs to. Neither reaches the crash guard. What does
    is a value that passes the shape check and its model, and then fails while
    being read — which is what the guard exists for: a defect in the walk
    itself, not in the document.
    """

    def __iter__(self):
        raise RuntimeError("rule list cannot be read")


class _ExplodingMapping(dict):
    """A `DocumentSet` that is a `dict` by every shape check and raises when
    its entries are read — the shape a caller backed by remote storage rather
    than a local dict literal can genuinely hand over."""

    def items(self):
        raise RuntimeError("document set cannot be enumerated")




def test_gap_resolution_map_crash_is_isolated_to_its_key(validator):
    # The second map is itself invalid, so it has a real, distinguishable
    # finding of its own: that is what proves the walk continued past the
    # crash rather than stopping at it. A clean second map could not — once a
    # map is dropped no probe is resolved, so a clean map contributes nothing
    # observable either way.
    maps = {
        "type-map-a.json": _ExplodingRules([{"match": "exact", "native_type": "STRING",
                                             "arrow_type": "Utf8"}]),
        "type-map-b.json": [{"native_type": "BIGINT"}],
    }
    result = validator.validate_doc(doc=maps, direction="read", probes=["STRING"])
    assert result["passed"] is False
    crashed = [f for f in result["findings"] if f["path"] == "type-map-a.json"]
    # notApplicable, not fail: the crash settled nothing about whether that map
    # is valid, which is the distinction `_run_guarded` draws for exactly this
    # case. Naming no rule, it still costs the pass asserted above.
    assert any(f["message_id"] == "internal-error" and f["kind"] == "notApplicable"
               for f in crashed), result["findings"]
    assert all("severity" not in f and "rule" not in f
               for f in crashed if f["message_id"] == "internal-error")
    assert any(f["path"].startswith("type-map-b.json") for f in result["findings"]), \
        result["findings"]
    # A crash means a map was lost, so no probe is resolved and the envelope
    # says which question went undecided rather than reporting full coverage.
    assert any(f["message_id"] == "gap-resolution-skipped" for f in result["findings"])


# ---------------------------------------------------------------------------
# validate_doc, ordinary document mode (`probes` omitted) — dispatches to
# validate_document, wrapping its result in one ValidationEnvelope shape.
# A document is validated by validate_doc; a package is validated by
# validate_connector_tree or validate_pipeline_tree, whichever the caller
# declares it is sending — there is no auto-detecting function that guesses
# which one a caller meant, at either level. A caller (or the wire-schema
# wrapper in front of this module) already knows which it is sending and
# calls the matching function directly.
# ---------------------------------------------------------------------------

@_xfail("validate_doc")
def test_validate_doc_dispatches_to_validate_document(validator):
    document = json.loads((CORPUS / "valid_connector.json").read_text())
    expected_findings = validator.validate_document(document)
    expected = {"passed": not any(validator.finding_costs_a_pass(f) for f in expected_findings),
                "findings": expected_findings}
    assert json.dumps(validator.validate_doc(document)) == json.dumps(expected)


@_xfail("validate_doc")
def test_ordinary_mode_forwards_schema_url_to_validate_document(validator):
    # No doc_path in this path-free route, so schema_url is the only direction
    # hint validate_document has for a type-map array's read/write model
    # selection — passing it through must actually change that selection, not
    # just be accepted and dropped.
    type_map = [{"match": "exact", "native_type": "VARCHAR${", "arrow_type": "Utf8"}]
    write_schema_url = f"{_H}/type-map-write/latest.json"
    without = validator.validate_document(type_map)
    with_url = validator.validate_document(type_map, schema_url=write_schema_url)
    assert without != with_url  # non-vacuous: schema_url must change the outcome
    expected = {"passed": not any(validator.finding_costs_a_pass(f) for f in with_url),
                "findings": with_url}
    result = validator.validate_doc(type_map, schema_url=write_schema_url)
    assert json.dumps(result) == json.dumps(expected)


@_xfail("validate_doc")
def test_ordinary_mode_type_map_entity_without_direction_reports_invalid_direction(validator):
    # `diagnostics_for` required `direction` whenever `entity == "type-map"`,
    # raising ValueError on a caller that omitted it; validate_doc keeps the
    # pairing but reports it as the same invalid-direction finding gap
    # resolution mode uses, rather than raising.
    type_map = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
    result = validator.validate_doc(type_map, entity="type-map")
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-direction"]
    assert "direction" not in result["findings"][0]


@_xfail("validate_doc")
def test_ordinary_mode_type_map_entity_bad_direction_reports_invalid_direction(validator):
    type_map = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
    result = validator.validate_doc(type_map, entity="type-map", direction="sideways")
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-direction"]


@_xfail("validate_doc")
def test_ordinary_mode_direction_with_non_type_map_entity_reports_invalid_direction(validator):
    # `diagnostics_for` also rejected `direction` for any entity other than
    # "type-map" — kept here too, since only entity="type-map" documents have
    # a read/write model for `direction` to select between.
    document = json.loads((CORPUS / "valid_connector.json").read_text())
    result = validator.validate_doc(document, entity="connector", direction="read")
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-direction"]


# ---------------------------------------------------------------------------
# Acceptance — equivalence: the path-based route and the document-set route
# produce byte-identical results for the same content, per package kind.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_tree")
def test_connector_tree_equivalence_with_the_path_based_route(validator, tmp_path):
    # Two distinct uncovered endpoints, not just a clean tree: a route
    # producing zero findings would make "findings order included" vacuous.
    documents = {
        **_connector_tree_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        "endpoints/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    _write_tree(tmp_path, documents)
    path_based = validator.validate_document(documents["connector.json"], doc_path=tmp_path / "connector.json")
    assert len(path_based) >= 2, path_based  # non-vacuous: order genuinely matters below
    tree_based = validator.validate_connector_tree(documents)
    expected = {"passed": not any(validator.finding_costs_a_pass(f) for f in path_based),
                "findings": path_based}
    assert json.dumps(tree_based) == json.dumps(expected)


@_xfail("validate_pipeline_tree")
def test_pipeline_tree_equivalence_with_the_path_based_route(validator, tmp_path):
    import validate as pipeline_adapter  # plugins/analitiq-pipeline-builder/scripts/validate.py

    documents = _pipeline_tree_documents_with_two_findings()
    _write_tree(tmp_path, documents)
    path_based = pipeline_adapter.diagnostics_for(
        "pipeline", tmp_path / "pipelines" / "p" / "pipeline.json", bundle_root=tmp_path)
    assert len(path_based["findings"]) >= 2, path_based  # non-vacuous: order genuinely matters below
    tree_based = validator.validate_pipeline_tree(documents)
    assert json.dumps(tree_based) == json.dumps(path_based)


def test_gap_resolution_probe_crash_is_isolated_to_that_probe(validator, monkeypatch):
    # The probe guard covers the one step validating the maps never performs:
    # running a matcher against a probe. No document reaches it, because every
    # value the matcher reads was already read while the map was validated — so
    # the failure is injected at the resolver rather than contrived into a map,
    # which is the behaviour under test either way.
    from analitiq.validator import connectors

    def explode_on_one(probe, rules):
        if probe == "STRING":
            raise RuntimeError("matcher cannot be run")
        return None

    read = connectors._DIRECTIONS["read"]
    monkeypatch.setitem(connectors._DIRECTIONS, "read", read._replace(resolve=explode_on_one))
    result = validator.validate_doc(
        doc={"type-map-read.json": _READ_RULES}, direction="read", probes=["STRING", "BIGINT"])
    assert result["passed"] is False
    crashed = [f for f in result["findings"] if f["message_id"] == "internal-error"]
    assert len(crashed) == 1, result["findings"]
    assert crashed[0]["kind"] == "notApplicable"
    assert "severity" not in crashed[0] and "rule" not in crashed[0]
    # `path` is empty and `direction` is set: a probe is asked of the whole set,
    # so no key is implicated, but the direction it was asked in is known.
    assert crashed[0]["path"] == "" and crashed[0]["direction"] == "read"
    assert "STRING" in crashed[0]["message"]
    # The probe after the crash still got its verdict.
    assert any(f["message_id"] == "type-map-gap" and "BIGINT" in f["message"]
               for f in result["findings"]), result["findings"]


def test_gap_resolution_reports_a_crash_enumerating_the_document_set(validator):
    # Key canonicalization is the first code to touch caller-controlled data,
    # so it is inside the guard too: a mapping that raises when read is a
    # reported finding, not an exception out of validate_doc.
    result = validator.validate_doc(
        doc=_ExplodingMapping({"type-map-read.json": []}), direction="read", probes=["STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["internal-error"]
    assert result["findings"][0]["kind"] == "notApplicable"
    assert result["findings"][0]["path"] == ""


#: Each way a map this direction would have consulted is lost before its rules
#: reach the pool, paired with a `doc` whose probe would otherwise resolve or
#: report a gap. Gaps are withheld for all of them, so a probe unresolved only
#: because a map went missing is never reported as a fact about the maps that
#: remain. A map refused for declaring the other direction is not here: it
#: would not have been consulted, so nothing was lost — the
#: `direction-filename-mismatch` tests above own that case.
_LOST_MAP_CASES = {
    "invalid-key": {"/type-map-read.json": _READ_RULES},
    "normalized-key-collision": {"m.json": _READ_RULES, "./m.json": _READ_RULES},
    "key-path-conflict": {"m.json": _READ_RULES, "m.json/nested.json": _READ_RULES},
    "invalid-value": {"m.json": 42},
    "not-utf8": {"m.json": b"\xff\xfe\x00rubbish"},
    "unparseable": {"m.json": "not json"},
    "over-limit": {"m.json": "[" * 100_000 + "]" * 100_000},
    "not-an-array": {"m.json": {"rules": []}},
    "model-error": {"m.json": [{"native_type": "STRING"}]},
    "map-crash": {"m.json": _ExplodingRules([{"match": "exact", "native_type": "STRING",
                                              "arrow_type": "Utf8"}])},
}


@pytest.mark.parametrize("case", sorted(_LOST_MAP_CASES))
def test_gap_resolution_withholds_gaps_whenever_a_map_is_lost(validator, case):
    result = validator.validate_doc(
        doc=_LOST_MAP_CASES[case], direction="read", probes=["STRING", "BIGINT"])
    assert result["passed"] is False
    assert not any(f["message_id"] == "type-map-gap" for f in result["findings"]), result
    # And the envelope says so, rather than leaving the absence of gaps to read
    # like full coverage.
    skipped = [f for f in result["findings"] if f["message_id"] == "gap-resolution-skipped"]
    assert len(skipped) == 1, result["findings"]
    assert skipped[0]["kind"] == "notApplicable"
    assert "severity" not in skipped[0] and "rule" not in skipped[0]
    assert skipped[0]["path"] == ""


def test_gap_resolution_invalid_probes_is_checked_before_doc_is_touched(validator):
    # `doc` is independently broken, so a run that reached it would say so.
    result = validator.validate_doc(doc="not a mapping", direction="read", probes="STRING")
    assert [f["message_id"] for f in result["findings"]] == ["invalid-probes"]
    assert result["findings"][0]["kind"] == "fail"
    assert result["findings"][0]["severity"] == "error"
    assert "direction" not in result["findings"][0]


def test_gap_resolution_empty_doc_names_the_direction_it_found_no_map_for(validator):
    result = validator.validate_doc(doc={}, direction="write", probes=["Utf8"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["missing-type-map"]
    assert result["findings"][0]["kind"] == "fail"
    assert result["findings"][0]["severity"] == "error"
    assert "direction" not in result["findings"][0]
    # A set holding no map and one holding only the other direction's maps
    # reach this same finding, so it names which direction went unserved.
    assert "write map" in result["findings"][0]["message"]


def test_gap_resolution_direction_mismatch_is_checked_before_the_map_is_read(validator):
    # The map's content is unreadable, so a run that read it before checking
    # the filename would report that instead.
    result = validator.validate_doc(
        doc={"type-map-write.json": "not json"}, direction="read", probes=["STRING"])
    ids = [f["message_id"] for f in result["findings"]]
    assert "direction-filename-mismatch" in ids, result["findings"]
    assert "type-map-unreadable" not in ids, result["findings"]
    mismatch = result["findings"][0]
    assert mismatch["kind"] == "fail" and mismatch["severity"] == "error"


def test_gap_resolution_reports_non_utf8_bytes_as_an_invalid_value(validator):
    result = validator.validate_doc(
        doc={"type-map-read.json": b"\xff\xfe[]"}, direction="read", probes=["STRING"])
    assert result["passed"] is False
    invalid = [f for f in result["findings"] if f["message_id"] == "invalid-value"]
    assert len(invalid) == 1, result["findings"]
    assert invalid[0]["path"] == "type-map-read.json"
    # The decode failure is named: bytes ARE a supported value, so a message
    # saying only that the value was unreadable points the author at the wrong
    # thing — what failed is the encoding.
    assert "utf-8" in invalid[0]["message"].lower()


def test_gap_resolution_reports_a_parsed_non_array_distinctly_from_unparseable_text(validator):
    result = validator.validate_doc(
        doc={"type-map-read.json": {"rules": []}}, direction="read", probes=["STRING"])
    unreadable = [f for f in result["findings"] if f["message_id"] == "type-map-unreadable"]
    assert len(unreadable) == 1, result["findings"]
    assert unreadable[0]["path"] == "type-map-read.json"
    # A document that parsed is not a syntax error, and saying so would send
    # the author looking for one.
    assert "dict" in unreadable[0]["message"]
    assert "not JSON" not in unreadable[0]["message"]


def test_gap_resolution_strips_a_bom_from_a_str_value(validator):
    result = validator.validate_doc(
        doc={"type-map-read.json": "\ufeff" + json.dumps(_READ_RULES)},
        direction="read", probes=["STRING"])
    assert result == {"passed": True, "findings": []}


def test_gap_resolution_reports_a_probe_once_however_often_it_is_asked_for(validator):
    result = validator.validate_doc(
        doc={"type-map-read.json": _READ_RULES}, direction="read",
        probes=["BIGINT", "BIGINT", "BIGINT"])
    gaps = [f for f in result["findings"] if f["message_id"] == "type-map-gap"]
    assert len(gaps) == 1, result["findings"]


def test_gap_resolution_repaths_a_whole_document_finding_onto_the_bare_key(validator):
    # A model error about the document itself carries path "/", which composes
    # to the key alone — not "m.json/", which is a path no document has.
    result = validator.validate_doc(
        doc={"m.json": []}, direction="read", probes=["STRING"])
    assert result["passed"] is False
    assert any(f.get("path") == "m.json" for f in result["findings"]), result["findings"]
    assert not any(f.get("path", "").endswith("/") for f in result["findings"])


def test_gap_resolution_collision_uses_neither_spelling(validator):
    # The point of the finding: not just that the collision is reported, but
    # that no arbitrary winner is picked. STRING is the probe either spelling's
    # rule would resolve, so a chosen winner shows up as the set answering it;
    # BIGINT is the probe no supplied map covers, so a set still willing to
    # answer at all shows up as a gap against it. Neither may appear, and
    # `gap-resolution-skipped` is the observable that separates "no winner was
    # picked" from "a winner was picked and covered both".
    result = validator.validate_doc(
        doc={"m.json": _READ_RULES, "./m.json": _READ_RULES},
        direction="read", probes=["STRING", "BIGINT"])
    collisions = [f for f in result["findings"]
                  if f["message_id"] == "normalized-key-collision"]
    assert len(collisions) == 1 and collisions[0]["path"] == "m.json"
    assert "'m.json'" in collisions[0]["message"] and "'./m.json'" in collisions[0]["message"]
    assert not any(f["message_id"] == "type-map-gap" for f in result["findings"])
    assert any(f["message_id"] == "gap-resolution-skipped" for f in result["findings"]), \
        result["findings"]
    assert result["passed"] is False


def test_gap_resolution_path_conflict_drops_the_nested_key_not_its_parent(validator):
    # The parent is a real document and the nested key is the one no filesystem
    # could hold, so the parent survives to be read and report its own content.
    result = validator.validate_doc(
        doc={"m.json": [{"native_type": "STRING"}], "m.json/nested.json": _READ_RULES},
        direction="read", probes=["STRING"])
    conflicts = [f for f in result["findings"] if f["message_id"] == "key-path-conflict"]
    assert len(conflicts) == 1 and conflicts[0]["path"] == "m.json/nested.json"
    assert "'m.json'" in conflicts[0]["message"]
    # The surviving parent was read: its model error is reported against it.
    assert any(f.get("path", "").startswith("m.json/") and f["message_id"] != "key-path-conflict"
               for f in result["findings"]), result["findings"]


def test_gap_resolution_conflict_report_order_does_not_follow_caller_iteration(validator):
    # Supplied deepest-first; the report is the same either way, so a caller
    # cannot change which conflict is named by reordering its own dict.
    deep_first = {
        "a/b.json/c.json/d.json": _READ_RULES,
        "a/b.json/c.json": _READ_RULES,
        "a/b.json": _READ_RULES,
    }
    shallow_first = dict(reversed(list(deep_first.items())))
    one = validator.validate_doc(doc=deep_first, direction="read", probes=["STRING"])
    other = validator.validate_doc(doc=shallow_first, direction="read", probes=["STRING"])
    assert one == other, (one, other)
    conflicts = [(f["path"], f["message"]) for f in one["findings"]
                 if f["message_id"] == "key-path-conflict"]
    assert len(conflicts) == 2, one["findings"]


def test_gap_resolution_invalid_key_message_carries_the_reason(validator):
    result = validator.validate_doc(
        doc={"type-map-read.json": _READ_RULES, "/abs.json": _READ_RULES},
        direction="read", probes=["STRING"])
    bad = [f for f in result["findings"] if f["message_id"] == "invalid-key"]
    assert len(bad) == 1 and bad[0]["path"] == "/abs.json"
    assert "relative, never absolute" in bad[0]["message"]


@pytest.mark.parametrize("bad_direction", [[], {}, ("read",), 0, None, "Read"])
def test_gap_resolution_reports_an_unusable_direction_rather_than_raising(
        validator, bad_direction):
    # `Literal["read", "write"]` is a type-checker-only promise; a wire wrapper
    # that hands over `["read"]` must get the finding, not a TypeError.
    result = validator.validate_doc(
        doc={"type-map-read.json": _READ_RULES}, direction=bad_direction, probes=["STRING"])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["invalid-direction"]


@pytest.mark.parametrize("document", [
    pytest.param("[" * 100_000 + "]" * 100_000, id="past-the-recursion-limit"),
    pytest.param("[" + "9" * 5000 + "]", id="past-the-integer-digit-limit",
                 marks=pytest.mark.skipif(
                     not hasattr(sys, "get_int_max_str_digits"),
                     reason="this interpreter enforces no integer conversion limit")),
])
def test_gap_resolution_reports_json_it_cannot_build_a_value_from_distinctly(validator, document):
    # Well-formed JSON the interpreter refuses: not a syntax error, and not a
    # validator bug either — so neither message is used for it. Both refusals
    # the reason covers are exercised, and only one of them is universal: the
    # integer conversion limit arrived mid-3.10, which this package still
    # supports, so a suite pinned to it alone would fail on a supported runtime.
    result = validator.validate_doc(
        doc={"m.json": document}, direction="read", probes=["STRING"])
    assert result["passed"] is False
    unreadable = [f for f in result["findings"] if f["message_id"] == "type-map-unreadable"]
    assert len(unreadable) == 1, result["findings"]
    assert "will not build a value from" in unreadable[0]["message"]
    assert "not JSON" not in unreadable[0]["message"]
    assert not any(f["message_id"] == "internal-error" for f in result["findings"])


def test_gap_resolution_names_a_probe_whose_repr_refuses_and_still_answers_it(validator):
    # A probe is caller data all the way into the message. Rendering it must
    # not become the failure, and degrading the name must not cost the verdict:
    # the probe is genuinely unresolved, so the gap is still reported.
    class _Unrepresentable(str):
        def __repr__(self):
            raise RuntimeError("repr refused")

    result = validator.validate_doc(
        doc={"type-map-read.json": _READ_RULES}, direction="read",
        probes=[_Unrepresentable("nope")])
    gaps = [f for f in result["findings"] if f["message_id"] == "type-map-gap"]
    assert len(gaps) == 1, result["findings"]
    # The fallback exists to name the probe by its type when its own `repr`
    # will not, so a constant reading "unrepresentable" and nothing else is
    # the degradation this guards against, not the one it accepts.
    assert "_Unrepresentable" in gaps[0]["message"], gaps[0]["message"]
    assert result["passed"] is True


def test_gap_resolution_reports_rather_than_raises_for_an_argument_no_check_anticipated(validator):
    # The closure guarantee is not a list of cases someone thought of:
    # `probes` passes every stated check and still breaks the dedupe, which
    # no inner guard wraps. It must arrive as a finding.
    class _Unhashable(str):
        def __hash__(self):
            raise RuntimeError("hash refused")

    result = validator.validate_doc(
        doc={"type-map-read.json": _READ_RULES}, direction="read",
        probes=[_Unhashable("nope")])
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["internal-error"]
    assert result["findings"][0]["kind"] == "notApplicable"
    assert "hash refused" in result["findings"][0]["message"]


class _HostileText(str):
    """A `str` subclass whose `removeprefix` is the caller's code and refuses.

    A `DocumentSetValue` typed `str` is a `str` subclass as readily as a `str`,
    so BOM-stripping runs caller code before the parser is ever reached."""

    def __new__(cls, value, raising=None):
        self = super().__new__(cls, value)
        self._raising = raising or ValueError("removeprefix refused")
        return self

    def removeprefix(self, _prefix):
        raise self._raising


def test_gap_resolution_blames_the_validator_not_the_document_for_a_crash_while_reading(validator):
    # The unreadable reasons name what is wrong with the *document*: not JSON,
    # not UTF-8, past a limit this interpreter enforces. A crash in the reading
    # itself is none of those, and reporting it as one sends the author
    # hunting a defect their document does not have.
    result = validator.validate_doc(
        doc={"m.json": _HostileText("[]")}, direction="read", probes=["STRING"])
    assert [f["message_id"] for f in result["findings"]] == [
        "internal-error", "gap-resolution-skipped"], result["findings"]
    assert result["findings"][0]["path"] == "m.json"
    assert "ValueError" in result["findings"][0]["message"]


def test_gap_resolution_survives_an_exception_that_cannot_describe_itself(validator):
    # Rendering the exception is itself caller code: a crash report that
    # crashes replaces the failure it was reporting. The degraded report must
    # still be attributed to the key that produced it, and must not swallow
    # the rest of the run — which is what a crash escaping to the outermost
    # guard would do.
    class _Nasty(Exception):
        def __str__(self):
            raise RuntimeError("str refused")

    result = validator.validate_doc(
        doc={"m.json": _HostileText("[]", raising=_Nasty())},
        direction="read", probes=["STRING"])
    assert [f["message_id"] for f in result["findings"]] == [
        "internal-error", "gap-resolution-skipped"], result["findings"]
    assert result["findings"][0]["path"] == "m.json"
    assert "unreportable" in result["findings"][0]["message"]


def test_gap_resolution_names_a_non_string_key_rather_than_crashing_on_it(validator):
    # A key of the wrong type has no name to match a direction against, so the
    # relevance test must treat it as possibly the map this call needed rather
    # than reading a filename off it. Reporting it as `internal-error` would
    # blame the validator for the caller's own mapping and lose the key.
    result = validator.validate_doc(
        doc={42: _READ_RULES, "type-map-read.json": _READ_RULES},
        direction="read", probes=["STRING"])
    assert [f["message_id"] for f in result["findings"]] == [
        "invalid-key", "gap-resolution-skipped"], result["findings"]
    assert result["findings"][0]["path"] == "42"
    assert "42" in result["findings"][0]["message"]


def test_gap_resolution_path_conflict_records_the_dropped_key_not_the_surviving_parent(validator):
    # Which name reached the lost-key list decides whether the loss is
    # relevant: the parent's filename declares the other direction and would
    # not have been consulted, while the nested key's declares nothing and
    # would have been. Recording the parent would withhold nothing and report
    # a set that answered the question when it did not.
    result = validator.validate_doc(
        doc={"type-map-write.json": _READ_RULES,
             "type-map-write.json/nested.json": _READ_RULES},
        direction="read", probes=["STRING"])
    assert [f["message_id"] for f in result["findings"]] == [
        "key-path-conflict", "direction-filename-mismatch",
        "gap-resolution-skipped"], result["findings"]


@pytest.mark.parametrize("direction", ["read", "write"])
def test_gap_resolution_refuses_the_pre_split_type_map_filename(validator, direction):
    # RULE-PKG-030 rejects that filename outright rather than reading it as a
    # read map, because a package still carrying it has a write direction
    # nobody has separated out. Consulting it here would answer a probe
    # through rules whose direction nothing declares — the silent wrong answer
    # the mismatch check exists to prevent, reached through the one filename
    # the path-based route already refuses.
    result = validator.validate_doc(
        doc={"type-map.json": _READ_RULES}, direction=direction, probes=["STRING"])
    assert [f["message_id"] for f in result["findings"]] == [
        "direction-filename-mismatch", "missing-type-map"], result["findings"]
    assert result["passed"] is False
    assert not any(f["message_id"] == "type-map-gap" for f in result["findings"])


_HUGE = "x" * 100_000


class _HugeRepr:
    def __repr__(self):
        return _HUGE


class _HugeStr(Exception):
    def __str__(self):
        return _HUGE


class _RaisingRepr(str):
    def __repr__(self):
        raise RuntimeError("repr refused")


def _every_caller_text_route(validator):
    """One call per way caller-supplied text reaches a finding message."""
    huge_type = type(_HUGE, (), {})
    return {
        "direction": lambda: validator.validate_doc(
            doc={"m.json": _READ_RULES}, direction=_HUGE, probes=["STRING"]),
        "probes": lambda: validator.validate_doc(
            doc={"m.json": _READ_RULES}, direction="read", probes=_HugeRepr()),
        "probe": lambda: validator.validate_doc(
            doc={"type-map-read.json": _READ_RULES}, direction="read",
            probes=[_HUGE]),
        "probe-repr-refused": lambda: validator.validate_doc(
            doc={"type-map-read.json": _READ_RULES}, direction="read",
            probes=[type(_HUGE, (_RaisingRepr,), {})("nope")]),
        "key": lambda: validator.validate_doc(
            doc={f"../{_HUGE}.json": _READ_RULES}, direction="read", probes=["STRING"]),
        "value-type": lambda: validator.validate_doc(
            doc={"m.json": huge_type()}, direction="read", probes=["STRING"]),
        "content-type": lambda: validator.validate_doc(
            doc={"m.json": {"rules": []}}, direction="read", probes=["STRING"]),
        "doc-type": lambda: validator.validate_doc(
            doc=huge_type(), direction="read", probes=["STRING"]),
        "parse-error": lambda: validator.validate_doc(
            doc={"m.json": '["' + _HUGE + '"'}, direction="read", probes=["STRING"]),
        "crash": lambda: validator.validate_doc(
            doc={"m.json": _HostileText("[]", raising=_HugeStr())},
            direction="read", probes=["STRING"]),
        "collision": lambda: validator.validate_doc(
            doc={f"s{i:06d}/../m.json": _READ_RULES for i in range(2000)},
            direction="read", probes=["STRING"]),
    }


def test_no_finding_message_echoes_caller_text_back_unbounded(validator):
    # A message is explanation, so every piece of caller text in one is a
    # borrowed diagnostic and is clipped. Unclipped, a finding's size is a
    # caller-chosen multiple of the input it complains about. Asserting this
    # over the *envelope* rather than each message is what previously let the
    # clip be applied to `path` too, where it does not belong.
    for name, call in _every_caller_text_route(validator).items():
        result = call()
        assert result["findings"], f"{name} produced no finding to check"
        for f in result["findings"]:
            assert len(f["message"]) < 2_000, (name, len(f["message"]), f["message"][:120])


def test_a_findings_path_names_the_key_whole_however_long_it_is(validator):
    # `path` identifies the document, so it is never clipped: two keys sharing
    # a long prefix must not arrive as one finding, and a consumer holding the
    # envelope must be able to look the key back up in the set it sent.
    pre = "d/" + "a" * 250
    k1, k2 = f"{pre}/one.json", f"{pre}/two.json"
    result = validator.validate_doc(
        doc={k1: "nope", k2: "nope"}, direction="read", probes=["STRING"])
    unreadable = [f for f in result["findings"] if f["message_id"] == "type-map-unreadable"]
    assert {f["path"] for f in unreadable} == {k1, k2}, [f["path"] for f in unreadable]
    assert all(f["path"] in {k1, k2} for f in unreadable)


def test_a_model_findings_path_keeps_the_whole_key_in_front_of_its_pointer(validator):
    # `_at_key` splices the key onto the model's own pointer within the
    # document. A clipped key would graft `/0/exact` onto a name no consumer
    # can resolve, and collapse two documents onto one path.
    pre = "d/" + "a" * 250
    bad = [{"match": "exact", "native_type": "VARCHAR${", "arrow_type": "Utf8"}]
    k1, k2 = f"{pre}/one/type-map-write.json", f"{pre}/two/type-map-write.json"
    result = validator.validate_doc(
        doc={k1: bad, k2: bad}, direction="write", probes=["Utf8"])
    inside = [f for f in result["findings"] if f.get("rule") == "RULE-TMAP-008"]
    assert {f["path"] for f in inside} == {f"{k1}/0/exact", f"{k2}/0/exact"}, \
        [f["path"] for f in inside]


def test_a_collision_names_the_spellings_without_growing_with_them(validator):
    # The finding has to show the caller which spellings collided; it does not
    # have to show all of them. Naming every one of 2000 is not a sentence.
    result = validator.validate_doc(
        doc={f"s{i:06d}/../m.json": _READ_RULES for i in range(2000)},
        direction="read", probes=["STRING"])
    collisions = [f for f in result["findings"]
                  if f["message_id"] == "normalized-key-collision"]
    assert len(collisions) == 1, result["findings"]
    assert "and 1995 more" in collisions[0]["message"], collisions[0]["message"]
    assert "'s000000/../m.json'" in collisions[0]["message"]
