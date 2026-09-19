"""Tests for the validator adapter (plugins/analitiq-pipeline-builder/scripts/validate.py).

The adapter holds no validation logic — it dispatches to the published
`analitiq-validator` / `analitiq-contract-models` packages. These tests therefore
require those packages installed (CI: `pip install -r requirements-dev.txt`); the
whole module skips cleanly when they are absent so a bare `pytest` never fails
confusingly. Canonical documents are defined inline and written to `tmp_path`, so
there are no committed fixtures to drift from the contract.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "plugins" / "analitiq-pipeline-builder"
sys.path.insert(0, str(ROOT / "scripts"))
import validate as V  # noqa: E402

pytest.importorskip("analitiq.validator",
                    reason="requires: pip install -r requirements-dev.txt")
from analitiq.contracts.endpoint_identity import (  # noqa: E402
    build_database_object, derive_db_endpoint_id,
)
from analitiq.contracts.type_map import (  # noqa: E402
    TYPE_MAP_READ_SCHEMA_URL, TYPE_MAP_WRITE_SCHEMA_URL,
)

SRC = "22222222-2222-4222-8222-222222222222"
DST = "33333333-3333-4333-8333-333333333333"
PID = "11111111-1111-4111-8111-111111111111"
SID = "44444444-4444-4444-8444-444444444444"
EID = derive_db_endpoint_id(None, "public", "orders")
DBOBJ = build_database_object(None, "public", "orders")
H = "https://schemas.analitiq.ai"

# Every finding's identity, local or forwarded: this adapter's own `validator`
# category for a locally-minted finding (no `kind` key at all), or the `rule`
# a forwarded (published) finding names — absent for the framework's own
# ruleless cases, which carry no id here.
def _ids(findings) -> list:
    return [f.get("validator") if "kind" not in f else f.get("rule") for f in findings]


def _legacy_name_reported(findings) -> bool:
    return any(f.get("message_id") == "legacy-type-map-filename" for f in findings)


# The rule ids the deleted `bundle-connection-ref` / `bundle-endpoint-ref`
# categories dissolved into (packages/validator/src/analitiq/validator/pipelines.py).
_BUNDLE_CONNECTION_REF_RULES = {"RULE-PIPE-012", "RULE-PIPE-013", "RULE-STRM-033"}
_BUNDLE_ENDPOINT_REF_RULES = {"RULE-STRM-034", "RULE-STRM-042"}

CONN_WISE = {
    "$schema": f"{H}/connection/latest.json", "connection_id": SRC, "connector_id": "wise",
    "display_name": "Wise", "parameters": {"environment": "live"},
    "secret_refs": {"api_token": "env:ANALITIQ_WISE_API_TOKEN"},
}
CONN_PG = {
    "$schema": f"{H}/connection/latest.json", "connection_id": DST, "connector_id": "postgresql",
    "display_name": "Prod Postgres",
    "parameters": {"host": "db.example.com", "port": 5432, "database": "analytics", "ssl_mode": "verify-full"},
    "secret_refs": {"password": "env:ANALITIQ_POSTGRESQL_PASSWORD"},
}
PIPELINE = {
    "$schema": f"{H}/pipeline/latest.json", "pipeline_id": PID, "display_name": "Wise to Postgres",
    "connections": {"source": SRC, "destinations": [DST]}, "streams": [SID],
    "schedule": {"type": "manual", "timezone": "UTC"}, "status": "draft",
}
STREAM = {
    "$schema": f"{H}/stream/latest.json", "stream_id": SID, "pipeline_id": PID, "display_name": "orders",
    "source": {
        "endpoint_ref": {"scope": "connector", "connection_id": SRC, "endpoint_id": "transfers"},
        "replication": {"method": "incremental", "cursor_field": "updated_at"},
    },
    "destinations": [{
        "endpoint_ref": {"scope": "connection", "connection_id": DST, "endpoint_id": EID, "database_object": DBOBJ},
        "write": {"mode": "upsert", "conflict_keys": ["id"]},
    }],
    "status": "draft",
}
DB_ENDPOINT = {
    "$schema": f"{H}/database-endpoint/latest.json", "endpoint_id": EID, "display_name": "public.orders",
    "database_object": DBOBJ,
    "columns": [
        {"name": "id", "native_type": "bigint", "arrow_type": "Int64", "nullable": False, "ordinal_position": 1},
        {"name": "updated_at", "native_type": "timestamptz", "arrow_type": "Timestamp(MICROSECOND, UTC)",
         "nullable": False, "ordinal_position": 2},
    ],
    "primary_keys": ["id"],
}


def _write(root: Path, rel: str, doc: dict | list) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2))
    return p


@pytest.mark.parametrize("entity,doc", [
    ("connection", CONN_PG), ("connection", CONN_WISE),
    ("pipeline", PIPELINE), ("stream", STREAM), ("database-endpoint", DB_ENDPOINT),
])
def test_valid_single_document(tmp_path, entity, doc):
    diag = V.diagnostics_for(entity, _write(tmp_path, f"{entity}.json", doc))
    assert diag["passed"], diag["findings"]


@pytest.mark.parametrize("entity,doc,validator_id", [
    # legacy connection carrying a `values` envelope — no longer part of the contract
    ("connection",
     {"$schema": f"{H}/connection/latest.json", "connector_id": "postgresql", "values": {"host": "x"}},
     "contract-model"),
    # legacy stream: flat endpoint_ref (missing database_object) + list-of-lists conflict_keys
    ("stream",
     {"$schema": f"{H}/stream/latest.json", "pipeline_id": PID,
      "source": {"endpoint_ref": {"scope": "connection", "connection_id": SRC, "endpoint_id": "orders"}},
      "destinations": [{"endpoint_ref": {"scope": "connection", "connection_id": DST, "endpoint_id": "orders"},
                        "write": {"mode": "upsert", "conflict_keys": [["id"]]}}]},
     "contract-model"),
    # database endpoint whose id is not the derived handle
    ("database-endpoint",
     {"$schema": f"{H}/database-endpoint/latest.json", "endpoint_id": "public_orders",
      "database_object": DBOBJ, "columns": [{"name": "id", "native_type": "bigint", "arrow_type": "Int64"}]},
     "RULE-DBEP-011"),
])
def test_invalid_single_document(tmp_path, entity, doc, validator_id):
    # `connection`/`stream` route through this adapter's own local model check
    # (a `validator` category); `database-endpoint` is forwarded unchanged from
    # the published `analitiq.validator` (a `rule` id) — one assertion covers
    # both without the test needing to know which.
    diag = V.diagnostics_for(entity, _write(tmp_path, f"{entity}.json", doc))
    assert not diag["passed"]
    assert any(
        f.get("validator") == validator_id or f.get("rule") == validator_id
        for f in diag["findings"]
    ), diag["findings"]


def test_a_contract_model_finding_does_not_carry_the_document_back_whole(tmp_path):
    # A discriminated union's sentence renders the tag it was handed, so an
    # oversized one would arrive whole in a CI log. The tag is clipped the way
    # the validator clips it, and the accepted tags after it survive.
    oversized = "S" * 5000
    doc = json.loads(json.dumps(STREAM))
    doc["source"]["endpoint_ref"]["scope"] = oversized
    diag = V.diagnostics_for("stream", _write(tmp_path, "stream.json", doc))
    [tag] = [f for f in diag["findings"] if f["path"] == "/source/endpoint_ref"]
    assert oversized not in tag["message"] and len(tag["message"]) < 500, len(tag["message"])
    assert "'connection'" in tag["message"], tag["message"][-200:]


def test_active_pipeline_requires_stream_single_document(tmp_path):
    # the published pipeline contract enforces active => >=1 stream reference at the
    # single-document level; an active pipeline with empty streams is rejected without
    # needing the bundle
    doc = {**PIPELINE, "status": "active", "streams": []}
    diag = V.diagnostics_for("pipeline", _write(tmp_path, "pipeline.json", doc))
    assert not diag["passed"]
    assert any(f.get("validator") == "contract-model" and "stream" in f["message"].lower()
               for f in diag["findings"]), diag["findings"]


def _build_bundle(root: Path) -> Path:
    _write(root, "connectors/wise/definition/connector.json", {"connector_id": "wise", "kind": "api"})
    _write(root, "connectors/postgresql/definition/connector.json", {"connector_id": "postgresql", "kind": "database"})
    _write(root, "connections/wise/connection.json", CONN_WISE)
    _write(root, "connections/postgresql/connection.json", CONN_PG)
    _write(root, f"connections/postgresql/definition/endpoints/{EID}.json", DB_ENDPOINT)
    _write(root, "pipelines/p/streams/orders.json", STREAM)
    return _write(root, "pipelines/p/pipeline.json", PIPELINE)


def test_valid_draft_bundle(tmp_path):
    doc = _build_bundle(tmp_path)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert diag["passed"], diag["findings"]
    # a draft pipeline is not yet runnable by design; require_runnable=False suppresses
    # the runnability findings entirely — no /pipeline/status finding is emitted
    assert not any(f["path"] == "/pipeline/status" for f in diag["findings"]), diag["findings"]
    # a correctly-named endpoint yields no RULE-PKG-031 finding (error or notApplicable)
    assert not any(f.get("rule") == "RULE-PKG-031" for f in diag["findings"]), diag["findings"]


def test_bundle_referential_error(tmp_path):
    doc = _build_bundle(tmp_path)
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["connection_id"] = "99999999-9999-4999-8999-999999999999"
    stream_path.write_text(json.dumps(stream))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    assert any(f.get("rule") == "RULE-STRM-033" for f in diag["findings"]), diag["findings"]


def test_bundle_endpoint_filename_mismatch(tmp_path):
    # the engine locates a connection-scoped endpoint by filename stem; a file named
    # something other than <endpoint_id>.json registers under the wrong id at runtime.
    # The id inside the file is still correct (so the referential checks pass), but the
    # bundle assembler flags the filename mismatch as an error.
    doc = _build_bundle(tmp_path)
    ep_dir = tmp_path / "connections/postgresql/definition/endpoints"
    (ep_dir / f"{EID}.json").rename(ep_dir / "orders.json")
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    assert any(f.get("rule") == "RULE-PKG-031" and f.get("severity") == "error"
               for f in diag["findings"]), diag["findings"]
    # the filename guard is the *sole* error — a rename must not also break referential
    # resolution, which would mask a guard regression
    assert {f.get("rule") for f in diag["findings"] if f.get("severity") == "error"} == {"RULE-PKG-031"}


def test_bundle_endpoint_missing_id_warns(tmp_path):
    # a missing/unusable endpoint_id yields a RULE-PKG-031 *notApplicable* (the
    # shared gate can't verify the name), not an error; the malformed state is
    # still caught as an error referentially, never silently passed
    doc = _build_bundle(tmp_path)
    ep_dir = tmp_path / "connections/postgresql/definition/endpoints"
    data = json.loads((ep_dir / f"{EID}.json").read_text())
    data.pop("endpoint_id")
    (ep_dir / f"{EID}.json").unlink()
    (ep_dir / "whatever.json").write_text(json.dumps(data))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert any(f.get("rule") == "RULE-PKG-031" and f["kind"] == "notApplicable"
               for f in diag["findings"]), diag["findings"]
    assert not any(f.get("rule") == "RULE-PKG-031" and f.get("severity") == "error"
                   for f in diag["findings"]), diag["findings"]
    assert not diag["passed"]


def test_diagnostics_fails_closed_on_a_published_notapplicable_finding():
    """`_diagnostics` reduces published `analitiq.validator` findings through
    the published `analitiq.validator.finding_costs_a_pass`, not a second
    predicate that only ever knew about `severity`. A `notApplicable` naming an
    error-tier rule
    carries no `severity` at all — the bug this pins is that check reading its
    absence as "fine" instead of as "unchecked, and error-tier rules don't get
    that benefit of the doubt."""
    published = {
        "rule": "RULE-ENDP-047",
        "message_id": "transport-ref-check-skipped-no-sibling",
        "kind": "notApplicable", "path": "/", "message": "not checked",
    }
    # This adapter's own locally-minted findings carry no `kind` at all and
    # must still be graded exactly as before: severity: error costs.
    local_ok = V._finding("adapter-crash", "warning", "/", "harmless")
    local_bad = V._finding("adapter-crash", "error", "/", "boom")

    assert V._diagnostics([published])["passed"] is False
    assert V._diagnostics([local_ok])["passed"] is True
    assert V._diagnostics([local_bad])["passed"] is False


def _add_wise_endpoint(root: Path, endpoint_id: str = "transfers") -> None:
    # Give the `wise` API connector a downloaded endpoint set on disk, so the plugin's
    # scope='connector' verification has something to resolve against. (_build_bundle
    # deliberately omits it — the connector's endpoint set is then 'unknown' and skipped.)
    _write(root, f"connectors/wise/definition/endpoints/{endpoint_id}.json",
           {"endpoint_id": endpoint_id})


def test_bundle_connector_endpoint_ref_ok(tmp_path):
    # a scope='connector' ref that names a real connector endpoint is clean — no warning
    doc = _build_bundle(tmp_path)
    _add_wise_endpoint(tmp_path, "transfers")  # matches STREAM's source endpoint_id
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert diag["passed"], diag["findings"]
    assert not any(f.get("validator") == "connector-endpoint-ref" for f in diag["findings"]), diag["findings"]


def test_bundle_connector_endpoint_ref_missing_warns(tmp_path):
    # a scope='connector' ref to an endpoint the connector does not publish is a
    # WARNING (not an error — connectors are trusted, pinned at runtime), carrying a
    # closest-match alignment suggestion; the pipeline still passes
    doc = _build_bundle(tmp_path)
    _add_wise_endpoint(tmp_path, "transfers")
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"  # typo
    stream_path.write_text(json.dumps(stream))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    warn = [f for f in diag["findings"] if f.get("validator") == "connector-endpoint-ref"]
    assert len(warn) == 1, diag["findings"]
    assert warn[0]["severity"] == "warning"
    assert warn[0]["path"] == "/streams/0/source/endpoint_ref"
    assert "transfers" in warn[0]["message"]  # the suggested real endpoint name
    assert diag["passed"], "a warning must not fail validation"


def test_bundle_connector_endpoint_case_mismatch_suggests(tmp_path):
    # a case-only mismatch surfaces the correctly-cased connector endpoint as the
    # alignment target
    doc = _build_bundle(tmp_path)
    _add_wise_endpoint(tmp_path, "transfers")
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["endpoint_id"] = "Transfers"
    stream_path.write_text(json.dumps(stream))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    warn = [f for f in diag["findings"] if f.get("validator") == "connector-endpoint-ref"]
    assert len(warn) == 1 and "'transfers'" in warn[0]["message"], diag["findings"]


def test_bundle_connector_endpoint_no_close_match_still_warns(tmp_path):
    # a wrong ref against a KNOWN endpoint set must still warn even when no endpoint is
    # a close match — the suggestion is simply omitted. This pins the `suggestion=None`
    # branch so a "only append when there's a suggestion" refactor can't silently drop
    # warnings on the most-wrong refs.
    doc = _build_bundle(tmp_path)
    _add_wise_endpoint(tmp_path, "transfers")
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["endpoint_id"] = "zzz"  # no close match to 'transfers'
    stream_path.write_text(json.dumps(stream))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    warn = [f for f in diag["findings"] if f.get("validator") == "connector-endpoint-ref"]
    assert len(warn) == 1 and warn[0]["severity"] == "warning", diag["findings"]
    assert "Did you mean" not in warn[0]["message"], warn[0]["message"]
    assert diag["passed"]


def test_bundle_connector_endpoint_resolves_by_connector_id_not_dir_slug(tmp_path):
    # the connector's directory slug (wise-live) differs from the connector_id (wise)
    # the connection references; the endpoint set is keyed by connector_id too, so a
    # wrong ref still resolves the set and warns. If resolution regressed to dir-slug
    # only, the set would read as 'unknown' and the warning would vanish.
    doc = _build_bundle(tmp_path)
    (tmp_path / "connectors/wise").rename(tmp_path / "connectors/wise-live")
    _write(tmp_path, "connectors/wise-live/definition/endpoints/transfers.json",
           {"endpoint_id": "transfers"})  # connector.json still declares connector_id "wise"
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["endpoint_id"] = "nope"
    stream_path.write_text(json.dumps(stream))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert any(f.get("validator") == "connector-endpoint-ref" for f in diag["findings"]), diag["findings"]


def test_bundle_connector_endpoint_unknown_set_skips(tmp_path):
    # no downloaded endpoint set for the connector => 'unknown', not 'empty': the check
    # must skip rather than warn on a ref it cannot verify (false-positive guard)
    doc = _build_bundle(tmp_path)  # no connectors/wise/definition/endpoints/
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["endpoint_id"] = "does_not_exist"
    stream_path.write_text(json.dumps(stream))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not any(f.get("validator") == "connector-endpoint-ref" for f in diag["findings"]), diag["findings"]


def test_unreadable_document(tmp_path):
    diag = V.diagnostics_for("pipeline", tmp_path / "does_not_exist.json")
    assert not diag["passed"]
    assert diag["findings"][0]["validator"] == "document"


def test_cli_main_valid(tmp_path, capsys):
    p = _write(tmp_path, "pipeline.json", PIPELINE)
    rc = V.main(["--entity", "pipeline", "--document", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out)["passed"] is True  # stdout carries exactly one JSON object


def test_cli_main_invalid_exit_code(tmp_path, capsys):
    bad = {"$schema": f"{H}/connection/latest.json", "connector_id": "x", "values": {}}
    p = _write(tmp_path, "connection.json", bad)
    rc = V.main(["--entity", "connection", "--document", str(p)])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


def test_cli_usage_error(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        V.main(["--document", "x.json"])  # missing required --entity
    assert excinfo.value.code == 2


def test_endpoint_id_helper(capsys):
    import endpoint_id  # sibling of validate.py on sys.path
    rc = endpoint_id.main(["--schema", "public", "--name", "orders"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["endpoint_id"] == EID
    assert out["database_object"]["name"] == "orders"


def test_active_pipeline_not_runnable_stays_error(tmp_path):
    doc = _build_bundle(tmp_path)  # the bundled stream is draft
    pipe = json.loads(doc.read_text())
    pipe["status"] = "active"
    doc.write_text(json.dumps(pipe))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    # an active pipeline with no runnable stream is a real error — require_runnable is
    # True for an 'active' pipeline, so the runnability gate stays blocking
    assert not diag["passed"]
    assert any(f.get("rule") == "RULE-PIPE-014" and f.get("severity") == "error"
               for f in diag["findings"]), diag["findings"]


def test_active_pipeline_runnable_bundle_passes(tmp_path):
    # positive active path: an active pipeline whose referenced stream is itself
    # active is runnable, so require_runnable=True must accept it (no false reject)
    doc = _build_bundle(tmp_path)
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["status"] = "active"
    stream_path.write_text(json.dumps(stream))
    pipe = json.loads(doc.read_text())
    pipe["status"] = "active"
    doc.write_text(json.dumps(pipe))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert diag["passed"], diag["findings"]


def test_bundle_malformed_sibling(tmp_path):
    doc = _build_bundle(tmp_path)
    (tmp_path / "pipelines/p/streams/orders.json").write_text("{ not valid json")
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    assert any(f.get("validator") == "document" for f in diag["findings"]), diag["findings"]


def test_bundle_non_dict_sibling(tmp_path):
    doc = _build_bundle(tmp_path)
    # valid JSON but not an object → the "is not a JSON object" branch (connection path)
    (tmp_path / "connections/postgresql/connection.json").write_text("[]")
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    assert any(f.get("validator") == "document" and "not a JSON object" in f["message"]
               for f in diag["findings"]), diag["findings"]


# ---------------------------------------------------------------------------
# Connection-scoped type maps: the `type-map` entity, graded as the direction
# each document declares, plus the bundle's file-level checks. Every rule and
# envelope finding comes from the published validator; what the adapter owns
# here is which files it collects and where it reports them.
# ---------------------------------------------------------------------------

TYPE_MAP_READ = [
    {"match": "exact", "native_type": "CITEXT", "arrow_type": "Utf8"},
    # `Json` is the only container canonical a read rule can render;
    # the dimension capture is intentionally discarded (no `(` in the render).
    {"match": "regex", "native_type": "^VECTOR\\((?<n>[0-9]+)\\)$", "arrow_type": "Json"},
]
# Deliberately direction-ASYMMETRIC: the regex rule's canonical is a matcher
# pattern, a contract-model error under read grading. A fixture valid under
# either direction passes whichever model ran, so only an asymmetric one shows
# which direction the grading came from.
TYPE_MAP_WRITE = [
    {"match": "exact", "arrow_type": "Json", "native_type": "JSONB"},
    {"match": "regex", "arrow_type": "^Decimal(128|256)\\((?<p>\\d+),\\s*(?<s>\\d+)\\)$",
     "native_type": "NUMERIC(${p}, ${s})"},
]


def _tm(rules: list, direction: str) -> dict:
    schema_url = TYPE_MAP_READ_SCHEMA_URL if direction == "read" else TYPE_MAP_WRITE_SCHEMA_URL
    return {"$schema": schema_url, "direction": direction, "rules": rules}


@pytest.mark.parametrize("direction,fname,doc", [
    ("read", "type-map-read.json", _tm(TYPE_MAP_READ, "read")),
    ("write", "type-map-write.json", _tm(TYPE_MAP_WRITE, "write")),
])
def test_valid_type_map_entity(tmp_path, direction, fname, doc):
    diag = V.diagnostics_for("type-map", _write(tmp_path, fname, doc))
    assert diag["passed"], diag["findings"]


def test_type_map_entity_forwards_the_published_findings_verbatim(tmp_path):
    # the adapter holds no type-map judgment of its own: what the published
    # grader says at the declared direction and connection scope IS the output.
    # A reintroduced filter, re-shape or scope drift fails here.
    from analitiq.validator import type_map_findings
    # The map earns a finding at connection scope, so the equality has content:
    # over a clean document both sides are empty and a reintroduced filter passes.
    doc = _tm(TYPE_MAP_WRITE + [{"match": "exact", "arrow_type": "Json",
                                 "native_type": "JSON"}], "write")
    published = type_map_findings(doc, "write", scope="connection")
    assert published, "the document must earn a finding or this asserts nothing"
    diag = V.diagnostics_for("type-map", _write(tmp_path, "type-map-write.json", doc))
    assert diag["findings"] == published


def test_type_map_entity_grades_a_document_under_any_filename(tmp_path):
    # A name says nothing about direction, so a valid read map is a valid read
    # map wherever it sits. Where a document belongs is the bundle's question,
    # asked of the connection's directory and answered there.
    diag = V.diagnostics_for("type-map", _write(tmp_path, "some-map.json", _tm(TYPE_MAP_READ, "read")))
    assert diag["passed"], diag["findings"]


def test_write_shaped_rules_in_a_read_map_fail_the_read_model(tmp_path):
    # the declared direction selects the read model, so write-shaped rules fail
    # it as content — the write map's regex rule renders a matcher pattern where
    # the read model reads a canonical
    diag = V.diagnostics_for(
        "type-map", _write(tmp_path, "type-map-read.json", _tm(TYPE_MAP_WRITE, "read")))
    assert not diag["passed"]
    assert any(f.get("rule") == "RULE-TMAP-006" and f.get("path") == "/rules/1/regex"
               for f in diag["findings"]), diag["findings"]
    assert not any(f.get("path") in {"/direction", "/$schema"}
                   for f in diag["findings"]), diag["findings"]


@pytest.mark.parametrize("fname,rules,declared", [
    ("type-map-read.json", TYPE_MAP_WRITE, "write"),
    ("type-map-write.json", TYPE_MAP_READ, "read"),
])
def test_map_is_graded_as_what_it_declares_under_the_other_filename(
        tmp_path, fname, rules, declared):
    # Each map sits under the other direction's conventional name and is still
    # graded as the direction it declares, so both pass. Grading by the name
    # instead would reject the `$schema` and `direction` each correctly carries.
    diag = V.diagnostics_for("type-map", _write(tmp_path, fname, _tm(rules, declared)))
    assert diag["passed"], diag["findings"]


def test_the_filename_decides_none_of_what_is_reported(tmp_path):
    # A malformed read map under the write direction's conventional name. Its
    # defects are the rules', and the envelope it correctly declares earns
    # nothing — a name that still decided would add `/direction` and `/$schema`
    # here and bury the defect the author has to fix.
    doc = _tm([{"match": {"arrow_type": "string"}, "exact": "VARCHAR${"}], "read")
    diag = V.diagnostics_for("type-map", _write(tmp_path, "type-map-write.json", doc))
    assert not diag["passed"], diag["findings"]
    paths = {f.get("path") for f in diag["findings"]}
    assert any(p and p.startswith("/rules") for p in paths), diag["findings"]
    assert not {"/direction", "/$schema"} & paths, diag["findings"]


@pytest.mark.parametrize("doc,path,message_id", [
    (_tm([], "read"), "/rules", "too_short"),
    (_tm([{"match": "exact", "native_type": "citext", "arrow_type": "utf8"}], "read"),
     "/rules/0/exact/arrow_type", "string_pattern_mismatch"),
])
def test_invalid_type_map_content(tmp_path, doc, path, message_id):
    # the defect each fixture carries, not merely that something failed: an
    # envelope finding alone (`/direction` or `/$schema`) would satisfy a
    # `kind == "fail"` assertion without the model
    # ever reaching the rules
    diag = V.diagnostics_for("type-map", _write(tmp_path, "type-map-read.json", doc))
    assert not diag["passed"]
    assert any(f.get("rule") is None and f.get("kind") == "fail"
               and f.get("path") == path and f.get("message_id") == message_id
               for f in diag["findings"]), diag["findings"]


def test_bundle_with_valid_connection_type_maps(tmp_path):
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connections/postgresql/definition/type-map-read.json", _tm(TYPE_MAP_READ, "read"))
    _write(tmp_path, "connections/postgresql/definition/type-map-write.json", _tm(TYPE_MAP_WRITE, "write"))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert diag["passed"], diag["findings"]


@pytest.mark.parametrize("shape", ["regular file", "directory", "dangling symlink"])
def test_bundle_rejects_dead_type_map_filename(tmp_path, shape):
    # the engine never reads the pre-split name — a lingering entry is silently
    # inert at runtime, so the bundle pass rejects it with a migration finding.
    # What carries the name decides nothing: the name is what is refused, and
    # both scopes refuse it through the one published predicate.
    doc = _build_bundle(tmp_path)
    dead = tmp_path / "connections/postgresql/definition/type-map.json"
    dead.parent.mkdir(parents=True, exist_ok=True)
    if shape == "regular file":
        _write(tmp_path, "connections/postgresql/definition/type-map.json", TYPE_MAP_READ)
    elif shape == "directory":
        dead.mkdir()
    else:
        dead.symlink_to(dead.parent / "nothing-here.json")
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    migration = [f for f in diag["findings"] if f.get("message_id") == "legacy-type-map-filename"]
    assert migration, diag["findings"]
    assert migration[0]["severity"] == "error"
    assert migration[0]["path"].startswith("connections/postgresql/definition/type-map.json")
    assert "type-map-read.json" in migration[0]["message"]  # the migration direction


def test_bundle_flags_invalid_connection_type_map(tmp_path):
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connections/postgresql/definition/type-map-read.json",
                _tm([{"match": "exact", "native_type": "citext", "arrow_type": "utf8"}], "read"))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    bad = [f for f in diag["findings"] if f.get("rule") is None and f.get("kind") == "fail"]
    assert bad, diag["findings"]
    # findings are anchored to the owning file so a multi-connection bundle stays legible
    assert all(f["path"].startswith("connections/postgresql/definition/type-map-read.json")
               for f in bad), bad


def test_bundle_unreadable_connection_type_map(tmp_path):
    doc = _build_bundle(tmp_path)
    p = tmp_path / "connections/postgresql/definition/type-map-read.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[ not valid json")
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    assert any(f.get("message_id") == "type-map-unparseable"
               and f["path"].startswith("connections/postgresql/definition/type-map-read.json")
               for f in diag["findings"]), diag["findings"]


def test_type_map_entity_rejects_a_document_that_declares_no_direction(tmp_path):
    # The entity routing is what makes this fail. Handed to kind detection a
    # stray connection document matches the connection detector and passes
    # clean; held to the type-map models it declares no direction, and the union
    # keyed on `direction` answers on the discriminator.
    diag = V.diagnostics_for("type-map", _write(tmp_path, "type-map-read.json", CONN_PG))
    assert not diag["passed"]
    assert any(f.get("message_id") == "union_tag_not_found" and f.get("path") == "/direction"
               for f in diag["findings"]), diag["findings"]


def test_type_map_entity_rejects_bare_array(tmp_path):
    # the legacy pre-envelope shape: a bare rules array with no
    # {$schema, direction, rules} wrapper. It declares no direction and is no
    # object either, so it is refused as an envelope rather than sent back out
    # to kind detection to be called unrecognized.
    diag = V.diagnostics_for(
        "type-map",
        _write(tmp_path, "type-map-read.json",
               [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]))
    assert not diag["passed"]
    assert any(f.get("message_id") == "model_attributes_type" and f.get("path") == ""
               for f in diag["findings"]), diag["findings"]


def test_connection_write_map_is_not_held_to_the_connector_vocabulary(tmp_path):
    # RULE-TMAP-017 presumes a connector's full-vocabulary write map; a gap-only
    # connection map never satisfies it by design, so the adapter says which
    # scope it holds rather than filtering the finding back out — for the entity
    # run and the bundle alike
    diag = V.diagnostics_for(
        "type-map", _write(tmp_path, "type-map-write.json", _tm(TYPE_MAP_WRITE, "write")))
    assert diag["passed"], diag["findings"]
    assert not any(f.get("rule") == "RULE-TMAP-017" for f in diag["findings"])

    root = tmp_path / "bundle"
    doc = _build_bundle(root)
    _write(root, "connections/postgresql/definition/type-map-write.json", _tm(TYPE_MAP_WRITE, "write"))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=root)
    assert diag["passed"], diag["findings"]
    assert not any(f.get("rule") == "RULE-TMAP-017" for f in diag["findings"])


def test_bundle_flags_invalid_connection_write_type_map(tmp_path):
    # pins that the bundle loop reaches the WRITE entry too (a lowercase exact
    # canonical fails the Arrow pattern under write grading)
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connections/postgresql/definition/type-map-write.json",
           _tm([{"match": "exact", "arrow_type": "utf8", "native_type": "TEXT"}], "write"))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    bad = [f for f in diag["findings"] if f.get("rule") is None and f.get("kind") == "fail"]
    assert bad and all(
        f["path"].startswith("connections/postgresql/definition/type-map-write.json")
        for f in bad), diag["findings"]


@pytest.mark.parametrize("defective", ["type-map-read.json", "type-map-write.json"])
def test_bundle_rejects_two_connection_maps_declaring_one_direction(tmp_path, defective):
    # The same rule the published validator applies beside a connector, at the
    # site this adapter owns: a direction two documents declare has no map, so
    # the collision is reported and neither document is graded. Whichever of the
    # two carries a defect of its own, reporting it would say which one the
    # order happened to reach first or last.
    doc = _build_bundle(tmp_path)
    for name in ("type-map-read.json", "type-map-write.json"):
        rules = ([{"match": "exact", "arrow_type": "utf8", "native_type": "TEXT"}]
                 if name == defective else TYPE_MAP_WRITE)
        _write(tmp_path, f"connections/postgresql/definition/{name}", _tm(rules, "write"))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    collision = [f for f in diag["findings"] if "both declare direction" in f["message"]]
    assert collision, diag["findings"]
    assert collision[0]["path"].startswith(
        "connections/postgresql/definition/type-map-write.json"), collision[0]
    assert "type-map-read.json" in collision[0]["message"], collision[0]
    assert not [f for f in diag["findings"] if f.get("rule") is None
                and f.get("kind") == "fail" and "/rules/" in f["path"]], diag["findings"]


def test_bundle_grades_a_connection_map_under_any_collected_filename(tmp_path):
    # Selecting the candidates is the name's whole say, so a collected map under
    # an unconventional name is graded like any other rather than sitting beside
    # the connection unread.
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connections/postgresql/definition/type-map-natives.json",
           _tm([{"match": "exact", "native_type": "citext", "arrow_type": "utf8"}], "read"))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    assert any(f["path"].startswith(
        "connections/postgresql/definition/type-map-natives.json/rules")
        for f in diag["findings"]), diag["findings"]


# Text the parser refuses without raising a decode error: nesting deeper than
# it descends, and an integer longer than its digit limit.
_PARSER_REFUSALS = {
    "nested past the parser": "[" * 100_000 + "]" * 100_000,
    "integer past the digit limit": '{"n": 1' + "0" * 5_000 + "}",
}


def _plant(definition: Path, scenario: str) -> None:
    """Lay out one way a connection's or a connector's type-map siblings can be."""
    read = json.dumps(_tm(TYPE_MAP_READ, "read"))
    definition.mkdir(parents=True, exist_ok=True)
    if scenario == "duplicated":
        for name in ("type-map-read.json", "type-map-extra.json", "type-map-more.json"):
            (definition / name).write_text(read)
        return
    target = definition / "type-map-read.json"
    if scenario.startswith("legacy"):
        target.write_text(read)
        target = definition / "type-map.json"
        scenario = scenario.removeprefix("legacy ")
    if scenario == "unparseable":
        target.write_text("[ not valid json")
    elif scenario == "not utf-8":
        target.write_bytes(b"\xff\xfe")
    elif scenario in _PARSER_REFUSALS:
        target.write_text(_PARSER_REFUSALS[scenario])
    elif scenario == "no direction":
        target.write_text(json.dumps({"rules": TYPE_MAP_READ}))
    elif scenario == "no object":
        target.write_text(json.dumps(TYPE_MAP_READ))
    elif scenario == "directory":
        target.mkdir()
    elif scenario == "dangling symlink":
        target.symlink_to(definition / "nothing-here.json")
    elif scenario == "fifo":
        os.mkfifo(target)
    else:  # pragma: no cover - a scenario the parametrization does not carry
        raise AssertionError(scenario)


@pytest.mark.parametrize("scenario", [
    "duplicated", "unparseable", "not utf-8", *_PARSER_REFUSALS, "no direction",
    "no object", "directory", "dangling symlink", "fifo",
    "legacy directory", "legacy dangling symlink", "legacy no object",
])
def test_connection_type_maps_are_collected_as_a_connector_collects_its_own(tmp_path, scenario):
    # One directory of maps, one answer: the findings beside a connection are
    # the ones the published validator reports beside a connector, each rooted
    # at the file it concerns. A storage connector is the connector side
    # because it requires no direction, so every finding it reports here is
    # about the siblings themselves.
    from analitiq.validator import check_coverage
    definition = tmp_path / "connections/pg/definition"
    _plant(definition, scenario)
    connector = check_coverage({"kind": "file", "transports": {}}, definition / "connector.json")
    connection: list[dict] = []
    V._connection_type_map_findings(definition.parent, connection)
    assert connector, scenario
    site = "connections/pg/definition/type-map"
    assert all(f["path"].startswith(site) for f in connection), connection
    # The rule a finding cites is the scope's: the record binding a connector
    # package binds no connection.
    assert not any("rule" in f for f in connection), connection
    assert [{k: v for k, v in f.items() if k not in ("rule", "path")} for f in connection] == [
        {k: v for k, v in f.items() if k not in ("rule", "path")} for f in connector]
    # A finding about a whole file is addressed at the file: the pointer to a
    # document's root joined onto it would name a key "" inside it.
    assert not any(f["path"].endswith("/") for f in connection), connection
    # The connector's finding names the file from the directory both share,
    # so rooting it at the connection's directory is the adapter's whole job.
    assert [f["path"] for f in connection] == [
        _rooted_at("connections/pg/definition", f["path"]) for f in connector]


def _rooted_at(site: str, path: str) -> str:
    """A `<reference>#<pointer>` path as the adapter addresses it beside `site`."""
    reference, _, pointer = path.partition("#")
    return f"{site}/{reference}{'' if pointer in ('', '/') else pointer}"


@pytest.mark.parametrize("path, rooted", [
    ("", "connections/pg/definition/endpoints/a.json"),
    ("/", "connections/pg/definition/endpoints/a.json"),
    ("/endpoint_id", "connections/pg/definition/endpoints/a.json/endpoint_id"),
    ("../connector.json#", "connections/pg/definition/connector.json"),
    ("../connector.json#/transports", "connections/pg/definition/connector.json/transports"),
    ("b%23c.json#/x#y", "connections/pg/definition/endpoints/b#c.json/x#y"),
])
def test_a_finding_is_rooted_at_the_document_it_names(path, rooted):
    # A bare pointer is into the entry graded; a `<reference>#<pointer>` names
    # another document, relative to the entry's directory.
    [found] = V._at_site("connections/pg/definition/endpoints/a.json",
                         [{"message_id": "m", "kind": "fail", "path": path, "message": "x"}])
    assert found["path"] == rooted


def test_a_definition_directory_that_cannot_be_listed_is_reported_at_the_directory(tmp_path, refuse):
    # The finding is about the directory, not a file in it, so it is addressed
    # at the directory; otherwise it is the one a connector's collection reports.
    from analitiq.validator import check_coverage
    definition = tmp_path / "connections/pg/definition"
    _plant(definition, "no direction")
    refuse(definition, 0o300)
    connector = check_coverage({"kind": "file", "transports": {}}, definition / "connector.json")
    connection: list[dict] = []
    V._connection_type_map_findings(definition.parent, connection)
    assert [f["message_id"] for f in connector] == ["type-map-dir-unlisted"], connector
    assert [{k: v for k, v in f.items() if k not in ("rule", "path")} for f in connection] == [
        {k: v for k, v in f.items() if k not in ("rule", "path")} for f in connector]
    assert [f["path"] for f in connection] == ["connections/pg/definition"], connection


def test_a_map_that_cannot_be_read_is_reported_at_the_map(tmp_path, refuse):
    from analitiq.validator import check_coverage
    from analitiq.validator._core import _passed
    definition = tmp_path / "connections/pg/definition"
    _plant(definition, "no direction")
    refuse(definition / "type-map-read.json", 0o000)
    connector = check_coverage({"kind": "file", "transports": {}}, definition / "connector.json")
    connection: list[dict] = []
    V._connection_type_map_findings(definition.parent, connection)
    assert [f["message_id"] for f in connector] == ["type-map-unreadable"], connector
    assert [{k: v for k, v in f.items() if k not in ("rule", "path")} for f in connection] == [
        {k: v for k, v in f.items() if k not in ("rule", "path")} for f in connector]
    assert [f["path"] for f in connection] == ["connections/pg/definition/type-map-read.json"]
    assert not _passed(connection), connection


def test_bundle_flags_type_map_that_is_not_a_file(tmp_path):
    # a directory under a load-bearing name would validate clean and then fail at
    # the engine's loader — the bundle pass flags it instead
    doc = _build_bundle(tmp_path)
    (tmp_path / "connections/postgresql/definition/type-map-read.json").mkdir(parents=True)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    assert any(f.get("message_id") == "type-map-unparseable" and "not a regular file" in f["message"]
               for f in diag["findings"]), diag["findings"]


# ---------------------------------------------------------------------------
# Crash containment: any route in this script produces Diagnostics on stdout,
# distinguishable from a rejection by the `adapter-crash` validator id, rather
# than a bare traceback with an empty stdout.
# ---------------------------------------------------------------------------

def test_model_findings_crash_becomes_adapter_crash_finding(tmp_path, monkeypatch, capsys):
    # an exception type pydantic never converts to ValidationError (a bug in a
    # custom validator, a typo'd attribute access, ...) must not escape as a
    # bare traceback — it is contained at the CLI's outermost guard
    import analitiq.contracts.connection as connection_module

    def boom(cls, *a, **kw):
        raise TypeError("simulated crash")

    monkeypatch.setattr(connection_module.ConnectionInput, "model_validate", classmethod(boom))
    p = _write(tmp_path, "connection.json", CONN_PG)
    rc = V.main(["--entity", "connection", "--document", str(p)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["passed"] is False
    assert any(f.get("validator") == "adapter-crash" and f["severity"] == "error"
               for f in out["findings"]), out["findings"]


def test_bundle_per_connection_crash_preserves_earlier_findings(tmp_path, monkeypatch):
    # postgresql sorts before wise, so _assemble_bundle's connections loop
    # decides postgresql's findings first; force the LATER connection (wise) to
    # crash mid-processing and confirm postgresql's already-decided finding
    # survives instead of being discarded by one shared try/except.
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connections/postgresql/definition/type-map.json", TYPE_MAP_READ)

    original = V._connection_type_map_findings

    def boom(conn_dir, findings):
        if conn_dir.name == "wise":
            raise TypeError("simulated crash")
        return original(conn_dir, findings)

    monkeypatch.setattr(V, "_connection_type_map_findings", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    assert _legacy_name_reported(diag["findings"]), diag["findings"]  # postgresql's, decided first
    crash = [f for f in diag["findings"] if f.get("validator") == "adapter-crash"]
    assert len(crash) == 1, diag["findings"]
    assert crash[0]["path"] == "connections/wise"
    assert "TypeError" in crash[0]["message"] and "simulated crash" in crash[0]["message"]


def test_bundle_connector_endpoint_refs_crash_contained(tmp_path, monkeypatch):
    # the combined _check_connector_endpoint_refs/_connector_endpoint_sets call
    # is its own guarded unit — a crash there must not discard the referential
    # findings the OTHER guarded unit (validate_pipeline_bundle) already decided
    doc = _build_bundle(tmp_path)
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["connection_id"] = "99999999-9999-4999-8999-999999999999"
    stream_path.write_text(json.dumps(stream))

    def boom(*a, **kw):
        raise TypeError("simulated crash")

    monkeypatch.setattr(V, "_check_connector_endpoint_refs", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    validators = _ids(diag["findings"])
    assert _BUNDLE_CONNECTION_REF_RULES & set(validators), diag["findings"]  # the other unit's result
    assert "adapter-crash" in validators, diag["findings"]


def test_connector_endpoint_ref_crash_preserves_earlier_ref_warning(tmp_path, monkeypatch):
    # each connector-scope ref is its own independently-decidable unit inside
    # _check_connector_endpoint_refs — a crash checking one ref (e.g. a
    # validator regression while computing its alignment suggestion) must not
    # discard the warning already decided for a ref checked earlier in the
    # same loop, which building one local list and returning it once at the
    # end would risk losing entirely
    doc = _build_bundle(tmp_path)
    _add_wise_endpoint(tmp_path, "transfers")
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"  # typo, resolves first
    stream_path.write_text(json.dumps(stream))

    second_stream = {**STREAM, "stream_id": "55555555-5555-4555-8555-555555555555",
                     "source": {**STREAM["source"],
                                "endpoint_ref": {**STREAM["source"]["endpoint_ref"],
                                                 "endpoint_id": "wiring"}}}  # unrelated typo
    _write(tmp_path, "pipelines/p/streams/second.json", second_stream)
    pipeline_doc = json.loads(doc.read_text())
    pipeline_doc["streams"].append(second_stream["stream_id"])
    doc.write_text(json.dumps(pipeline_doc))

    import difflib
    original = difflib.get_close_matches

    def boom(word, possibilities, *a, **kw):
        if word == "wiring":
            raise TypeError("simulated crash")
        return original(word, possibilities, *a, **kw)

    monkeypatch.setattr(difflib, "get_close_matches", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "adapter-crash" in validators, diag["findings"]
    # the first ref's warning, decided before the crashing second ref, survives
    warnings = [f for f in diag["findings"] if f.get("validator") == "connector-endpoint-ref"]
    assert any("transfers" in w["message"] for w in warnings), diag["findings"]


def test_bundle_memory_error_yields_single_finding_no_dangling_colon(tmp_path, monkeypatch, capsys):
    # MemoryError re-raises through every per-stage guard so only the single
    # outermost guard in main() contains it — one finding, not one per
    # in-progress unit — and a bare MemoryError() (empty str()) must not leave
    # the message ending in a dangling ": ".
    doc = _build_bundle(tmp_path)

    def boom(conn_dir, findings):
        raise MemoryError()

    monkeypatch.setattr(V, "_connection_type_map_findings", boom)
    rc = V.main(["--entity", "pipeline", "--document", str(doc), "--bundle-root", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    crash = [f for f in out["findings"] if f.get("validator") == "adapter-crash"]
    assert len(crash) == 1, out["findings"]
    assert crash[0]["message"] == "MemoryError"
    assert not crash[0]["message"].endswith(": ")


def test_main_contains_a_crash_that_leaves_the_validator_unimportable(monkeypatch, capsys):
    # main()'s outermost guard builds its envelope literally rather than through
    # `_diagnostics`: a crash bootstrapping the dependencies can leave
    # `finding_costs_a_pass` unimportable, so reaching for it to report that
    # failure would raise a second, uncontained one.
    def boom(_path):
        raise RuntimeError("bootstrap failed")

    monkeypatch.setattr(V, "ensure_deps_or_reexec", boom)
    monkeypatch.setitem(sys.modules, "analitiq.validator", None)  # poisoned: import raises

    rc = V.main(["--entity", "pipeline", "--document", "x.json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["passed"] is False
    assert _ids(out["findings"]) == ["adapter-crash"], out["findings"]


def test_endpoint_route_crash_before_validate_document_contained(tmp_path, capsys):
    # the validator refuses a path it cannot locate by raising, ahead of
    # validate_document's own internal guard, so the adapter must still produce
    # Diagnostics on stdout. `link/..` is such a path: POSIX steps up from where
    # the link leads, so the document opened is not the one the names spell
    real = tmp_path / "real" / "inner"
    real.mkdir(parents=True)
    _write(tmp_path / "real", "database-endpoint.json", DB_ENDPOINT)
    (tmp_path / "spelled").mkdir()
    (tmp_path / "spelled" / "link").symlink_to(real)
    rc = V.main(["--entity", "database-endpoint", "--document",
                 str(tmp_path / "spelled" / "link" / ".." / "database-endpoint.json")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert any(f.get("validator") == "adapter-crash" for f in out["findings"]), out["findings"]


def test_bundle_type_map_crash_does_not_orphan_connection_from_referential_check(tmp_path, monkeypatch):
    # a crash in the type-map check for one connection must not cost that
    # connection its place in the bundle passed to the referential check —
    # otherwise a live, correctly-referenced connection reads as unresolved
    # and the adapter-crash finding is joined by false bundle-connection-ref /
    # bundle-endpoint-ref findings that would send the orchestrator chasing a
    # reference that was never actually broken
    doc = _build_bundle(tmp_path)

    def boom(conn_dir, findings):
        raise TypeError("simulated crash")

    monkeypatch.setattr(V, "_connection_type_map_findings", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "adapter-crash" in validators, diag["findings"]
    assert not _BUNDLE_CONNECTION_REF_RULES & set(validators), diag["findings"]
    assert not _BUNDLE_ENDPOINT_REF_RULES & set(validators), diag["findings"]


def test_type_map_entity_crash_preserves_legacy_finding_and_sibling_direction(tmp_path, monkeypatch):
    # each type-map direction is its own independently-decidable unit inside
    # _connection_type_map_findings — a crash grading type-map-read.json must
    # not discard the legacy-filename finding already decided just above it,
    # nor cost type-map-write.json its own turn later in the same loop
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connections/postgresql/definition/type-map.json", TYPE_MAP_READ)
    _write(tmp_path, "connections/postgresql/definition/type-map-read.json", _tm(TYPE_MAP_READ, "read"))
    _write(tmp_path, "connections/postgresql/definition/type-map-write.json",
           _tm([{"match": "exact", "native_type": "citext", "arrow_type": "utf8"}], "write"))  # invalid casing

    original = V._type_map_findings

    def boom(doc_):
        if doc_.get("direction") == "read":
            raise TypeError("simulated crash")
        return original(doc_)

    monkeypatch.setattr(V, "_type_map_findings", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "adapter-crash" in validators, diag["findings"]
    crash = [f for f in diag["findings"] if f.get("validator") == "adapter-crash"
             and f["path"].endswith("type-map-read.json")]
    assert crash, diag["findings"]
    assert _legacy_name_reported(diag["findings"]), diag["findings"]
    bad_write = [f for f in diag["findings"] if f.get("rule") is None and f.get("kind") == "fail"
                 and f["path"].startswith("connections/postgresql/definition/type-map-write.json")]
    assert bad_write, diag["findings"]  # processed after the crash, still got its turn


@pytest.mark.parametrize("text", list(_PARSER_REFUSALS.values()), ids=list(_PARSER_REFUSALS))
def test_type_map_the_parser_refuses_preserves_legacy_finding_and_sibling_direction(
        tmp_path, text):
    # A map the parser refuses is an unreadable map like any other: it costs the
    # legacy-name finding nothing, and the write map beside it is still graded.
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connections/postgresql/definition/type-map.json", TYPE_MAP_READ)
    (tmp_path / "connections/postgresql/definition/type-map-read.json").write_text(text)
    _write(tmp_path, "connections/postgresql/definition/type-map-write.json",
           _tm([{"match": "exact", "native_type": "citext", "arrow_type": "utf8"}], "write"))  # invalid casing
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not any(f.get("validator") == "adapter-crash" for f in diag["findings"]), diag["findings"]
    assert any(f.get("message_id") == "type-map-unparseable"
               and f["path"].startswith("connections/postgresql/definition/type-map-read.json")
               for f in diag["findings"]), diag["findings"]
    assert _legacy_name_reported(diag["findings"]), diag["findings"]
    bad_write = [f for f in diag["findings"] if f.get("rule") is None and f.get("kind") == "fail"
                 and f["path"].startswith("connections/postgresql/definition/type-map-write.json")]
    assert bad_write, diag["findings"]


@pytest.mark.parametrize("text", list(_PARSER_REFUSALS.values()), ids=list(_PARSER_REFUSALS))
def test_a_document_the_parser_refuses_is_reported_as_unreadable(tmp_path, text):
    (tmp_path / "connection.json").write_text(text)
    diag = V.diagnostics_for("connection", tmp_path / "connection.json")
    assert [(f["validator"], f["message"].split(":")[0]) for f in diag["findings"]] == [
        ("document", "Cannot read document")], diag["findings"]


@pytest.mark.parametrize("text", list(_PARSER_REFUSALS.values()), ids=list(_PARSER_REFUSALS))
@pytest.mark.parametrize("member", [
    "pipelines/p/streams/orders.json",
    "connectors/postgresql/definition/connector.json",
    "connectors/postgresql/definition/endpoints/orders.json",
])
def test_a_bundle_member_the_parser_refuses_costs_no_crash(tmp_path, text, member):
    # Each is read by the adapter itself, not the validator; a refusal it does
    # not catch ends the section reading it as an adapter crash. The connector
    # ships an endpoint so its connector.json is read for the endpoint's owner too.
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connectors/postgresql/definition/endpoints/orders.json", {"endpoint_id": "orders"})
    (tmp_path / member).write_text(text)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert "adapter-crash" not in _ids(diag["findings"]), diag["findings"]


def test_bundle_findings_crash_unrelated_to_exclusion_does_not_mislabel_it(tmp_path, monkeypatch):
    # an ordinary, already-reported read error (no guard fired) can exclude a
    # bundle member in the same run a completely unrelated guard elsewhere
    # (grading the type maps beside a connection) genuinely crashes in. The exclusion and
    # the crash are unrelated: `crashed` must reflect only the four
    # bundle-assembly sites that can actually exclude a member, not "any
    # adapter-crash finding anywhere", or the ordinary exclusion gets
    # mislabeled as caused by a containment guard that never touched it
    doc = _build_bundle(tmp_path)
    (tmp_path / "pipelines/p/streams/orphan.json").write_text("{not valid json")  # ordinary error
    _write(tmp_path, "connections/postgresql/definition/type-map-read.json", _tm(TYPE_MAP_READ, "read"))

    def boom(doc_):
        raise TypeError("simulated crash")

    monkeypatch.setattr(V, "_type_map_findings", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "document" in validators, diag["findings"]  # the orphaned malformed stream
    assert "adapter-crash" in validators, diag["findings"]  # the unrelated type-map crash
    assert not any(
        f.get("validator") == "adapter-crash"
        and "containment guard excluded" in f["message"]
        for f in diag["findings"]
    ), diag["findings"]  # the exclusion was an ordinary error, not caused by that crash


def test_bundle_endpoint_grading_crash_preserves_endpoint_and_siblings(tmp_path, monkeypatch):
    # a crash grading one endpoint document must not cost that endpoint its
    # place in the bundle passed to the referential check, nor the remaining
    # endpoints and the connection's trailing type-map check the per-connection
    # guard would otherwise discard as one shared unit
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connections/postgresql/definition/type-map.json", TYPE_MAP_READ)
    second_eid = derive_db_endpoint_id(None, "public", "customers")
    second_endpoint = {**DB_ENDPOINT, "endpoint_id": second_eid,
                        "database_object": build_database_object(None, "public", "customers")}
    _write(tmp_path, f"connections/postgresql/definition/endpoints/{second_eid}.json", second_endpoint)

    original = V._endpoint_findings

    def boom(endpoint, document_path):
        if document_path.name == f"{EID}.json":
            raise TypeError("simulated crash")
        return original(endpoint, document_path)

    monkeypatch.setattr(V, "_endpoint_findings", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "adapter-crash" in validators, diag["findings"]
    # the crashed endpoint still holds its place in the bundle -> no false
    # bundle-endpoint-ref for the stream's legitimate reference to it
    assert not _BUNDLE_ENDPOINT_REF_RULES & set(validators), diag["findings"]
    # the connection's trailing type-map check still ran despite the earlier
    # crash in this same per-connection unit
    assert _legacy_name_reported(diag["findings"]), diag["findings"]


def test_bundle_connector_loop_crash_preserves_other_connector_identity(tmp_path, monkeypatch):
    # a crash beyond the read errors _assemble_bundle's connectors loop already
    # handles (e.g. a pathologically deep document) is its own guarded unit —
    # it must not abort the loop before a later connector's identity is read
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connectors/wise/definition/connector.json",
           {"connector_id": "wise-live", "kind": "api"})

    original = V._read_json

    def boom(path):
        if path.name == "connector.json" and path.parent.parent.name == "postgresql":
            raise TypeError("simulated crash")
        return original(path)

    monkeypatch.setattr(V, "_read_json", boom)
    pipeline_doc = json.loads(doc.read_text())
    bundle, findings, complete, crashed = V._assemble_bundle(pipeline_doc, doc, tmp_path)
    # the crash cost only the connector_id alias (the slug is recorded before the
    # guarded read), but a connection could still name that id rather than the
    # slug, so assembly is marked incomplete out of caution
    assert not complete
    assert crashed
    validators = _ids(findings)
    assert "adapter-crash" in validators, findings
    crash = [f for f in findings if f.get("validator") == "adapter-crash"][0]
    assert crash["path"] == "connectors/postgresql"
    # postgresql's directory slug is recorded unconditionally, before the crash
    assert "postgresql" in bundle["connectors"], bundle["connectors"]
    # wise, processed after the crashed unit in loop order, still registers its
    # connector_id (which here differs from its directory slug)
    assert "wise-live" in bundle["connectors"], bundle["connectors"]


def test_connector_endpoint_sets_directory_probe_crash_isolated_to_one_connector(tmp_path, monkeypatch):
    # the is_dir() probe is inside the per-connector guard, not before it — a
    # crash there must cost only that connector's endpoint set, not every
    # connector processed after it in the same loop, and not (by extension)
    # every other connector's connector-endpoint-ref check
    doc = _build_bundle(tmp_path)
    _add_wise_endpoint(tmp_path, "transfers")
    # give postgresql a downloaded connector endpoint set too, so its
    # directory is actually globbed and its is_dir() probe actually runs
    _write(tmp_path, "connectors/postgresql/definition/endpoints/realid.json", {"endpoint_id": "realid"})
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"  # typo -> warning if wise's set survives
    stream_path.write_text(json.dumps(stream))

    original_is_dir = Path.is_dir

    def boom(self):
        if self.parent.parent.name == "postgresql":
            raise TypeError("simulated crash")
        return original_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "adapter-crash" in validators, diag["findings"]
    # wise, sorted after postgresql, still gets its endpoint set built and warns
    assert any(f.get("validator") == "connector-endpoint-ref" and "transfers" in f["message"]
               for f in diag["findings"]), diag["findings"]


def test_connector_endpoint_sets_enumeration_crash_returns_partial_result(tmp_path, monkeypatch):
    # the top-level enumeration (sorted(root.glob(...))) is its own guarded
    # unit, same as _assemble_bundle's sections — a filesystem failure there
    # must not escape every per-connector guard below it and abort the whole
    # function; it should return whatever it has (nothing, if the enumeration
    # itself never got going) instead of raising past its caller
    doc = _build_bundle(tmp_path)

    original_glob = Path.glob

    def boom(self, pattern):
        if pattern == "connectors/*/definition/endpoints":
            raise TypeError("simulated crash")
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", boom)
    findings: list = []
    sets = V._connector_endpoint_sets(tmp_path, findings)
    assert sets == {}
    crash = [f for f in findings if f.get("validator") == "adapter-crash" and f["path"] == "connectors"]
    assert crash, findings

    # confirmed the same way through the full pipeline: the crash is contained,
    # not left to propagate out of _bundle_findings
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "adapter-crash" in validators, diag["findings"]


def test_bundle_connections_section_crash_preserves_streams_and_reaches_connectors(tmp_path, monkeypatch):
    # a failure enumerating the connections/ directory itself (not a single
    # connection's own read) must not abort _assemble_bundle before it can
    # return what the streams section already decided, nor before the
    # connectors section gets its own turn afterward
    doc = _build_bundle(tmp_path)

    original_glob = Path.glob

    def boom(self, pattern):
        if self.name == "connections" and pattern == "*/connection.json":
            raise TypeError("simulated crash")
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", boom)
    pipeline_doc = json.loads(doc.read_text())
    bundle, findings, complete, crashed = V._assemble_bundle(pipeline_doc, doc, tmp_path)
    assert not complete
    assert crashed
    crash = [f for f in findings if f.get("validator") == "adapter-crash" and f["path"] == "connections"]
    assert crash, findings
    assert bundle["streams"], bundle["streams"]  # the earlier section's result survived
    assert bundle["connectors"], bundle["connectors"]  # the later section still ran


def test_bundle_pipeline_validator_crash_preserves_other_unit_result(tmp_path, monkeypatch):
    # the "pipeline" guarded unit (validate_pipeline_bundle) is not the only
    # unit _bundle_findings decides — a crash in it must not discard the
    # OTHER unit's (_check_connector_endpoint_refs) result
    doc = _build_bundle(tmp_path)
    _add_wise_endpoint(tmp_path, "transfers")
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"  # typo
    stream_path.write_text(json.dumps(stream))

    import analitiq.validator as validator_module

    def boom(*a, **kw):
        raise TypeError("simulated crash")

    monkeypatch.setattr(validator_module, "validate_pipeline_bundle", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"]
    validators = _ids(diag["findings"])
    assert "adapter-crash" in validators, diag["findings"]
    assert "connector-endpoint-ref" in validators, diag["findings"]  # the other unit's result


def test_bundle_stream_read_crash_preserves_sibling_stream_and_continues_assembly(tmp_path, monkeypatch):
    # one stream file is its own independently-decidable unit, same as one
    # connection or connector below it — a crash reading it must not discard a
    # sibling stream already appended, nor abort bundle assembly before the
    # connections loop that runs after it
    doc = _build_bundle(tmp_path)
    second_stream = {**STREAM, "stream_id": "55555555-5555-4555-8555-555555555555"}
    _write(tmp_path, "pipelines/p/streams/second.json", second_stream)
    _write(tmp_path, "connections/postgresql/definition/type-map.json", TYPE_MAP_READ)

    original = V._read_json

    def boom(path):
        if path.name == "orders.json":
            raise TypeError("simulated crash")
        return original(path)

    monkeypatch.setattr(V, "_read_json", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "adapter-crash" in validators, diag["findings"]
    crash = [f for f in diag["findings"] if f.get("validator") == "adapter-crash"][0]
    assert crash["path"] == "streams/orders.json"
    # the connections loop, which runs after the crashed streams loop, still
    # ran and decided its own finding
    assert _legacy_name_reported(diag["findings"]), diag["findings"]
    # PIPELINE.streams still names the crashed stream's id (it was never
    # re-authored to drop the reference) — the bundle is short that very
    # document, so the referential pass that would call this ref unresolved
    # is skipped rather than blame a reference that was never actually broken
    assert "RULE-PIPE-011" not in validators, diag["findings"]
    assert sum(1 for v in validators if v == "adapter-crash") == 2, diag["findings"]


def test_bundle_endpoint_read_crash_preserves_sibling_endpoint(tmp_path, monkeypatch):
    # a crash reading one endpoint file (e.g. pathologically deep JSON) is its
    # own per-endpoint unit — it must not abort the endpoints loop before an
    # independently-readable sibling endpoint reaches the bundle
    doc = _build_bundle(tmp_path)
    second_eid = derive_db_endpoint_id(None, "public", "customers")
    second_endpoint = {**DB_ENDPOINT, "endpoint_id": second_eid,
                        "database_object": build_database_object(None, "public", "customers")}
    _write(tmp_path, f"connections/postgresql/definition/endpoints/{second_eid}.json", second_endpoint)

    original = V._read_json

    def boom(path):
        if path.name == f"{EID}.json":
            raise TypeError("simulated crash")
        return original(path)

    monkeypatch.setattr(V, "_read_json", boom)
    pipeline_doc = json.loads(doc.read_text())
    bundle, findings, complete, crashed = V._assemble_bundle(pipeline_doc, doc, tmp_path)
    assert not complete
    assert crashed
    endpoint_ids = {e["endpoint_id"] for e in bundle["endpoints"]}
    assert second_eid in endpoint_ids, bundle["endpoints"]  # sibling survived the crash
    assert EID not in endpoint_ids, bundle["endpoints"]  # the crashed one did not
    crash = [f for f in findings if f.get("validator") == "adapter-crash"]
    assert len(crash) == 1, findings
    assert crash[0]["path"] == f"connections/postgresql/definition/endpoints/{EID}.json"


def test_bundle_type_map_validated_when_connection_json_unreadable(tmp_path):
    # the type-map check depends only on the connection's directory, never on
    # whether connection.json itself parsed — an unreadable connection.json
    # must not hide a genuinely malformed or legacy type-map file beside it
    doc = _build_bundle(tmp_path)
    _write(tmp_path, "connections/postgresql/definition/type-map.json", TYPE_MAP_READ)
    (tmp_path / "connections/postgresql/connection.json").write_text("{not valid json")
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "document" in validators, diag["findings"]  # connection.json itself unreadable
    assert _legacy_name_reported(diag["findings"]), diag["findings"]


def test_bundle_unrelated_malformed_stream_skips_referential_pass_without_crash_label(tmp_path):
    # an orphaned malformed stream file marks assembly incomplete (a document
    # never referenced by pipeline.streams can still exclude a member the
    # pipeline DOES reference — telling those apart would mean re-deriving the
    # published validator's own reference resolution locally), so the whole
    # referential pass is skipped, even though this particular defect could
    # not have been the cause of any bundle-*-ref finding. Nothing crashed —
    # _read_bundle_member handled the bad JSON normally — so no adapter-crash
    # finding is added on top of the "document" finding that already names it.
    doc = _build_bundle(tmp_path)
    (tmp_path / "pipelines/p/streams/orphan.json").write_text("{not valid json")
    # a second, referenced stream wired to the WRONG source connection — a
    # genuine, unrelated referential defect the skipped pass does not surface
    bad_stream = {**STREAM, "stream_id": "55555555-5555-4555-8555-555555555555",
                  "source": {**STREAM["source"],
                             "endpoint_ref": {**STREAM["source"]["endpoint_ref"], "connection_id": DST}}}
    _write(tmp_path, "pipelines/p/streams/second.json", bad_stream)
    pipeline_doc = json.loads(doc.read_text())
    pipeline_doc["streams"] = [SID, bad_stream["stream_id"]]
    doc.write_text(json.dumps(pipeline_doc))

    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "document" in validators, diag["findings"]  # the orphaned malformed stream
    assert not _BUNDLE_CONNECTION_REF_RULES & set(validators), diag["findings"]  # referential pass skipped
    assert "adapter-crash" not in validators, diag["findings"]  # nothing actually crashed


def test_connector_endpoint_sets_crash_isolated_to_one_connector(tmp_path, monkeypatch):
    # a crash reading one connector's endpoint file must cost only that
    # connector's endpoint set — every OTHER connector's set, and the
    # connector-endpoint-ref checks it feeds, must still be computed
    doc = _build_bundle(tmp_path)
    _add_wise_endpoint(tmp_path, "transfers")
    _write(tmp_path, "connectors/postgresql/definition/endpoints/orders.json", {"endpoint_id": "orders"})

    original = V._read_json

    def boom(path):
        if path.parent.name == "endpoints" and path.parent.parent.parent.name == "postgresql":
            raise TypeError("simulated crash")
        return original(path)

    monkeypatch.setattr(V, "_read_json", boom)
    stream_path = tmp_path / "pipelines/p/streams/orders.json"
    stream = json.loads(stream_path.read_text())
    stream["source"]["endpoint_ref"]["endpoint_id"] = "transferz"  # typo against wise's real "transfers"
    stream_path.write_text(json.dumps(stream))

    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "adapter-crash" in validators, diag["findings"]
    # wise's connector-endpoint-ref check still ran despite postgresql's crash
    warn = [f for f in diag["findings"] if f.get("validator") == "connector-endpoint-ref"]
    assert len(warn) == 1 and "transfers" in warn[0]["message"], diag["findings"]


def test_pipeline_document_error_survives_non_dict_bundle_enrichment(tmp_path):
    # a pipeline document that is not even an object earns its precise
    # contract-model finding at the single-document stage; require_runnable's
    # own field access on that non-dict document must not crash and discard
    # it — isinstance-guarded rather than raising AttributeError, so bundle
    # enrichment runs through cleanly and the published validator gets to add
    # its own precise finding too, instead of both being replaced by one
    # opaque adapter-crash
    doc = _write(tmp_path, "pipelines/p/pipeline.json", [1, 2, 3])
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "contract-model" in validators, diag["findings"]
    assert "adapter-crash" not in validators, diag["findings"]


def test_pipeline_document_error_preserved_when_bundle_enrichment_crashes(tmp_path, monkeypatch):
    # a genuine crash enriching the bundle (not just a non-dict pipeline_doc,
    # which no longer crashes) must not discard the precise contract-model
    # finding the single-document pass already produced
    doc = _write(tmp_path, "pipelines/p/pipeline.json", [1, 2, 3])

    def boom(pipeline_doc, document_path, root):
        raise TypeError("simulated crash")

    monkeypatch.setattr(V, "_assemble_bundle", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    validators = _ids(diag["findings"])
    assert "contract-model" in validators, diag["findings"]
    assert "adapter-crash" in validators, diag["findings"]


def test_crash_finding_handles_broken_exception_str():
    # a third-party backend can raise an exception class whose own __str__
    # itself raises; _crash_finding must never become a second, unguarded
    # crash in main()'s except body
    class Broken(Exception):
        def __str__(self):
            raise RuntimeError("broken __str__")

    finding = V._crash_finding("", Broken())
    assert finding.get("validator") == "adapter-crash"
    assert finding["message"] == "Broken"


def test_main_serializes_within_the_outer_guard(tmp_path, monkeypatch, capsys):
    # a backend finding carrying a JSON-incompatible value (a validator
    # regression) must itself become an adapter-crash result — json.dumps
    # raising after the guard has exited clean would reproduce the empty
    # stdout + traceback failure this fix eliminates
    p = _write(tmp_path, "connection.json", CONN_PG)

    def bad_diagnostics_for(entity, document_path, bundle_root=None):
        return {"passed": False, "findings": [{"validator": "contract-model", "severity": "error",
                                                "path": "", "message": object()}]}

    monkeypatch.setattr(V, "diagnostics_for", bad_diagnostics_for)
    rc = V.main(["--entity", "connection", "--document", str(p)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert any(f.get("validator") == "adapter-crash" for f in out["findings"]), out["findings"]


# ---------------------------------------------------------------------------
# The entity vocabulary, as the agent that drives the CLI states it
# ---------------------------------------------------------------------------

VALIDATOR_AGENT = ROOT / "agents" / "pipeline-schema-validator.md"

# The `entity` input bullet, located by the backticked field name that opens it
# and closed by the next top-level bullet. Lexical throughout: the anchor is an
# identifier `validate.py` owns (it is the CLI flag), and the verdict below is
# handed to `V.PIPELINE_ENTITIES` — no sentence is read.
_ENTITY_BULLET = re.compile(r"^- `entity`.*?(?=^- |\Z)", re.M | re.S)
_TICKED = re.compile(r"`([a-z-]+)`")


def test_validator_agent_states_the_adapter_entity_vocabulary():
    """The agent's `entity` input is the CLI's `--entity` choices, verbatim.

    The agent prose is the only place a user's agent learns which entities
    exist; a member missing from it is a document nobody can ask to have
    validated, and an invented one is a run that dies at argparse. Neither
    shows up in any other gate, because the contract is unchanged either way.

    The expected set is `{entity}` — the field's own name, which is what
    locates the bullet — plus whatever `PIPELINE_ENTITIES` currently holds, so
    adding an entity to the adapter fails here until the agent learns it.

    One site this cannot reach: the same file's frontmatter `description`, which
    paraphrases the vocabulary in running English ("database-endpoint",
    "connection-scoped type-map") because that string is what routes work to
    this agent, not something an agent reads members off. Grading a paraphrase
    means deciding what a sentence of running English asserts, and that is a
    guard reading a sentence — banned by `.claude/rules/guards.md`. So it is
    carried by the failure hint below and by a reader, not by an assertion.
    """
    bullet = _ENTITY_BULLET.search(VALIDATOR_AGENT.read_text())
    assert bullet, (
        f"{VALIDATOR_AGENT.name}: no '- `entity`' input bullet — the file was "
        "restructured, and this guard is now reading nothing")
    assert set(_TICKED.findall(bullet.group(0))) == {"entity", *V.PIPELINE_ENTITIES}, (
        f"{VALIDATOR_AGENT.name}'s `entity` input no longer names exactly "
        f"validate.py's PIPELINE_ENTITIES ({', '.join(V.PIPELINE_ENTITIES)}). "
        "Update the bullet, then update the two prose sites this guard does not "
        "read: this same file's frontmatter `description`, which names the "
        "entities in English so the orchestrator routes to it, and the entity "
        "names in skills/pipeline-builder/SKILL.md (phase 5's type-map writes, "
        "and Edit mode's referenced-closure step)."
    )


def test_pipeline_entities_are_a_document_artifact_kind_subset():
    """Pins `PIPELINE_ENTITIES` — hardcoded in validate.py rather than imported,
    so `--entity`'s `choices=` survives a fresh end-user environment where the
    self-install bootstrap has not yet run — to the contract's own restricted
    vocabulary, so the two cannot drift apart member by member."""
    from analitiq.contracts.shared.rule_record import DOCUMENT_ARTIFACT_KINDS

    assert set(V.PIPELINE_ENTITIES) <= set(DOCUMENT_ARTIFACT_KINDS)
    # connector / api-endpoint are the connector-builder plugin's own document
    # kinds; this adapter's entity vocabulary is everything else that denotes
    # one concrete document.
    assert set(V.PIPELINE_ENTITIES) == set(DOCUMENT_ARTIFACT_KINDS) - {"connector", "api-endpoint"}


def test_cli_main_type_map_entities(tmp_path, capsys):
    # the agents drive the CLI: only this pins that PIPELINE_ENTITIES exposes
    # the entity and that the invocation names no direction, the document's own
    # declaration deciding
    path = _write(tmp_path, "type-map-write.json", _tm(TYPE_MAP_WRITE, "write"))
    rc = V.main(["--entity", "type-map", "--document", str(path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["passed"], out


def test_bundle_grades_a_symlinked_endpoint_by_its_authored_name(tmp_path):
    # RULE-PKG-031 is about where the engine will look for the file, which is
    # the name the connection directory carries — not the name of whatever the
    # entry points at. Resolving the whole path hands the gate the target's
    # basename, and a misnamed endpoint reads clean.
    doc = _build_bundle(tmp_path)
    ep_dir = tmp_path / "connections/postgresql/definition/endpoints"
    ep_path = ep_dir / f"{EID}.json"
    store = tmp_path / "shared"
    store.mkdir()
    target = store / f"{EID}.json"
    target.write_text(ep_path.read_text())
    ep_path.unlink()
    (ep_dir / "wrong-name.json").symlink_to(target)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"], diag["findings"]
    assert any(f.get("rule") == "RULE-PKG-031" for f in diag["findings"]), diag["findings"]


@pytest.mark.parametrize("member, site", [
    ("connections/postgresql/connection.json", "connections/postgresql/connection.json"),
    ("pipelines/p/streams/orders.json", "streams/orders.json"),
])
def test_bundle_grades_every_member_as_the_document_it_is(tmp_path, member, site):
    # The referential checks read a member's refs, never its shape, so a
    # bundled connection or stream would be graded by nothing on the route
    # that assembles it while the same file validated alone was rejected. The
    # finding is re-rooted at the file: a pointer into a document names nothing
    # in a bundle holding several.
    doc = _build_bundle(tmp_path)
    path = tmp_path / member
    body = json.loads(path.read_text())
    body["not_a_declared_field"] = "x"
    path.write_text(json.dumps(body))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"], diag["findings"]
    assert any(f.get("severity") == "error"
               and f.get("path") == f"{site}/not_a_declared_field"
               for f in diag["findings"]), diag["findings"]


@pytest.mark.parametrize("link", ["endpoints", "definition"])
def test_bundle_grades_an_endpoint_under_a_symlinked_directory(tmp_path, link):
    # Following the link takes the file out of the layout RULE-PKG-031
    # recognises, and the gate then reports nothing at all — a misnamed
    # endpoint reads clean. Fails open, where the symlinked-file case at least
    # failed under the wrong name.
    doc = _build_bundle(tmp_path)
    definition = tmp_path / "connections/postgresql/definition"
    moved = tmp_path / f"elsewhere-{link}"
    (definition / link if link == "endpoints" else definition).rename(moved)
    if link == "endpoints":
        (definition / "endpoints").symlink_to(moved, target_is_directory=True)
        ep_dir = definition / "endpoints"
    else:
        definition.parent.joinpath("definition").symlink_to(moved, target_is_directory=True)
        ep_dir = definition / "endpoints"
    (ep_dir / f"{EID}.json").rename(ep_dir / "wrong-name.json")
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"], diag["findings"]
    assert any(f.get("rule") == "RULE-PKG-031" for f in diag["findings"]), diag["findings"]


@pytest.mark.parametrize("member", ["connection", "stream"])
def test_a_crash_grading_a_member_does_not_cost_it_its_place_in_the_bundle(
        tmp_path, monkeypatch, member):
    # `_model_findings` catches only ValidationError. Anything else escaping it
    # must cost its own findings and nothing more: a member excluded from the
    # bundle marks assembly incomplete, and the whole cross-document referential
    # pass is then skipped — so a crash grading one document's shape would
    # silently stop grading every reference in the bundle.
    doc = _build_bundle(tmp_path)
    original = V._model_findings

    def boom(entity, body):
        if entity == member:
            raise RecursionError("too deep")
        return original(entity, body)

    monkeypatch.setattr(V, "_model_findings", boom)
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert any(f.get("validator") == "adapter-crash" for f in diag["findings"]), \
        diag["findings"]
    assert not any("referential integrity was not evaluated" in f.get("message", "")
                   for f in diag["findings"]), diag["findings"]


def test_bundle_grades_a_connection_scoped_endpoint_document(tmp_path):
    # An endpoint in the bundle is graded by every rule its own document
    # settles, the contract model included — not by the filename gate alone.
    # A file the single-document route rejects cannot pass here.
    doc = _build_bundle(tmp_path)
    ep_path = tmp_path / f"connections/postgresql/definition/endpoints/{EID}.json"
    ep = json.loads(ep_path.read_text())
    ep["not_a_declared_field"] = "x"
    ep_path.write_text(json.dumps(ep))
    diag = V.diagnostics_for("pipeline", doc, bundle_root=tmp_path)
    assert not diag["passed"], diag["findings"]
    site = f"connections/postgresql/definition/endpoints/{EID}.json"
    assert any(f.get("severity") == "error"
               and f.get("path") == f"{site}/not_a_declared_field"
               for f in diag["findings"]), diag["findings"]


# ---------------------------------------------------------------------------
# What the scripts borrow from the pinned validator


def _validator_imports_in_scripts() -> set[tuple[str, str]]:
    """(module, name) for every `from analitiq.validator[...] import name` the
    plugin's scripts execute — read from their syntax so a new borrow is graded
    without anyone remembering to list it here."""
    found = set()
    for script in sorted((ROOT / "scripts").glob("*.py")):
        for node in ast.walk(ast.parse(script.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("analitiq.validator"):
                found.update((node.module, a.name) for a in node.names)
    return found


def test_scripts_borrow_published_names_from_the_published_surface():
    # A name the scripts import from the package root is a dependency on
    # `analitiq-validator`'s API, and `__all__` is where that package says which
    # names it owes. One missing from it is a name nothing promised to keep,
    # importable today because it happens to be bound — so the scripts would
    # break at every end user's runtime on a release that tidied it away.
    # Underscore names are the deliberate exception: they are borrowed as private
    # API, so no `__all__` can promise them, and what stands in for that promise is
    # `test_scripts_borrow_private_names_that_still_exist` below, grading them
    # against the source this suite runs on.
    import analitiq.validator as pkg
    root = {name for module, name in _validator_imports_in_scripts()
            if module == "analitiq.validator"}
    assert root, "no root-level validator imports found — the scan stopped measuring"
    public = {n for n in root if not n.startswith("_")}
    assert public <= set(pkg.__all__), sorted(public - set(pkg.__all__))
    for name in root - public:
        assert hasattr(pkg, name), name


def test_scripts_borrow_private_names_that_still_exist():
    # The submodule borrows are private by construction, so `__all__` cannot
    # grade them; what makes them safe is that this suite runs against the source
    # the pin tracks, so a rename fails here before it reaches a user.
    import importlib
    for module, name in sorted(_validator_imports_in_scripts()):
        assert hasattr(importlib.import_module(module), name), f"{module}.{name}"


