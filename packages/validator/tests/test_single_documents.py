"""Single-document validation for the connection / stream / pipeline authored
kinds — the CLI now validates every authored kind, not just the connector family.

The caller names the kind it is submitting; nothing here infers it from the
document's shape. Each kind is validated wholly against its contract model, so
these tests assert its model verdict is surfaced — a valid document passes and
an invalid one fails.
"""
import json
from pathlib import Path

from analitiq.contracts.connection import CONNECTION_SCHEMA_URL
from analitiq.contracts.pipelines.config import PIPELINE_SCHEMA_URL
from analitiq.contracts.stream import STREAM_SCHEMA_URL

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_SRC_ROOT = _REPO_ROOT / "contract-models" / "src"
SRC_ROOT = _REPO_ROOT / "validator" / "src"

SOURCE_CONN = "11111111-1111-4111-8111-111111111111"
DEST_CONN = "22222222-2222-4222-8222-222222222222"
PIPELINE = "b4904c77-0a4a-4a8d-a768-4a8b5f2f2414"


def _errors(findings):
    return [f for f in findings if f["severity"] == "error"]


# --- valid document fixtures (minimal, model-valid) -------------------------

def _valid_connection() -> dict:
    return {"$schema": CONNECTION_SCHEMA_URL, "connector_id": "stripe"}


def _valid_stream() -> dict:
    return {
        "$schema": STREAM_SCHEMA_URL,
        "pipeline_id": PIPELINE,
        "source": {
            "endpoint_ref": {
                "scope": "connector",
                "connection_id": f"{SOURCE_CONN}_v1",
                "endpoint_id": "transfers",
            }
        },
        "destinations": [
            {
                "endpoint_ref": {
                    "scope": "connector",
                    "connection_id": f"{DEST_CONN}_v1",
                    "endpoint_id": "orders",
                },
                "write": {"mode": "insert"},
            }
        ],
    }


def _valid_pipeline() -> dict:
    return {
        "$schema": PIPELINE_SCHEMA_URL,
        "connections": {
            "source": f"{SOURCE_CONN}_v1",
            "destinations": [f"{DEST_CONN}_v1"],
        }
    }


# --- connection --------------------------------------------------------------

def test_valid_connection_passes(validator):
    assert validator.validate_document(_valid_connection(), "connection") == []


def test_invalid_connection_is_flagged(validator):
    # A secret-shaped key in `parameters` is a model rule violation (RULE-CONN-004).
    doc = {"connector_id": "stripe", "parameters": {"password": "hunter2"}}
    findings = validator.validate_document(doc, "connection")
    errors = _errors(findings)
    assert errors and all(e.get("rule") == "RULE-CONN-004" for e in errors)


def test_connection_missing_connector_id_is_flagged_as_missing_field(validator):
    # `connector_id` is a required field on the connection model; submitted as
    # "connection", a document without it fails on that field, not on kind.
    findings = validator.validate_document({"display_name": "x"}, "connection")
    errors = _errors(findings)
    assert any(
        e.get("message_id") == "missing" and e["path"] == "/connector_id"
        for e in errors
    )


# --- stream ------------------------------------------------------------------

def test_valid_stream_passes(validator):
    assert validator.validate_document(_valid_stream(), "stream") == []


def test_invalid_stream_is_flagged(validator):
    doc = _valid_stream()
    doc["status"] = "bogus"  # not a valid lifecycle status
    errors = _errors(validator.validate_document(doc, "stream"))
    # A closed-`Literal` mismatch is pydantic's own rejection, with no
    # `rules.violation` behind it — the field constraint carries no rule id.
    assert errors and all(e.get("rule") is None for e in errors)


def test_stream_extra_top_level_field_rejected(validator):
    # The authored-top-level guard (extra='forbid') is a model rule; proving it
    # fires confirms the real StreamInput model runs, not a lax stand-in.
    doc = _valid_stream()
    doc["org_id"] = "server-managed"
    assert _errors(validator.validate_document(doc, "stream"))


# --- pipeline ----------------------------------------------------------------

def test_valid_pipeline_passes(validator):
    assert validator.validate_document(_valid_pipeline(), "pipeline") == []


def test_invalid_pipeline_is_flagged(validator):
    doc = _valid_pipeline()
    doc["status"] = "bogus"
    errors = _errors(validator.validate_document(doc, "pipeline"))
    # A closed-`Literal` mismatch is pydantic's own rejection, with no
    # `rules.violation` behind it — the field constraint carries no rule id.
    assert errors and all(e.get("rule") is None for e in errors)


def test_single_pipeline_document_passes_its_model(validator):
    """A single pipeline document (no nested `pipeline`) submitted as "pipeline"
    routes to and passes the pipeline model."""
    doc = _valid_pipeline()
    assert validator.validate_document(doc, "pipeline") == []


def test_active_pipeline_without_streams_flagged(validator):
    """An `active` pipeline with no stream references violates the
    single-document half of the activation gate — a contract-model error, not a
    passing document. (The API previously rejected such a document that this
    single-doc path accepted.)"""
    doc = {**_valid_pipeline(), "status": "active"}
    errors = _errors(validator.validate_document(doc, "pipeline"))
    assert errors and all(e.get("rule") == "RULE-PIPE-004" for e in errors)
    assert any("at least one stream reference" in e["message"] for e in errors)


def test_active_pipeline_with_stream_passes(validator):
    """The active-status gate is not blanket rejection: an `active` pipeline that
    references a stream still passes the single-document model."""
    doc = {**_valid_pipeline(), "status": "active", "streams": [f"{PIPELINE}_v1"]}
    assert validator.validate_document(doc, "pipeline") == []


# --- CLI end to end (the one-stop-validator ask) -----------------------------

def test_cli_validates_connection_exit0(validator_cli):
    r = validator_cli.on_document(_valid_connection(), "connection")
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["passed"] is True


def test_cli_invalid_stream_exit1(validator_cli):
    bad = _valid_stream()
    bad["status"] = "bogus"
    r = validator_cli.on_document(bad, "stream")
    assert r.returncode == 1
    assert json.loads(r.stdout)["passed"] is False
