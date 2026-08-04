#!/usr/bin/env python3
"""Executable registry of validator-behavior claims, and the prose gate over them.

Plugin prose states what the validator checks and — more dangerously — what it
does *not* check ("a `success_when` typo validates clean", "function names are
never checked", "only the leading scope token is validated"). Each such sentence
is a copy of a contract fact, and until issue #133 nothing pinned any of them:
a contract-models change could falsify prose across several files while CI
stayed green.

This module is the fix, in three parts:

1. **Probes** — tiny documents run through the in-repo validator
   (`analitiq.validator.validate_document`, the same entry the plugins invoke),
   each asserting an outcome: rejected with a message, accepted clean, or
   accepted silent (zero findings). A probe is a *measurement*; if the contract
   moves under it, `verify_probes()` fails and CI goes red.
2. **Claims** — the prose sentences themselves, authored ONCE here, each naming
   the probes that prove it. Marked regions in the plugin docs
   (`<!-- BEGIN GENERATED: <block-id> -->` … `END GENERATED`, the same marker
   grammar as the pipeline plugin's `gen_contract_docs.py`) are rendered from
   this registry — `claim:<id>` blocks from the `Claim` records, the dense
   clusters (the response-scope table, the validator blind-spot list) by the
   dedicated `render_*` functions whose probe couplings sit beside them — so
   neither can drift from the measurement behind it.
3. **The scan** — a trigger-phrase gate over every markdown file in BOTH
   plugins. A sentence that asserts validator behavior (matches
   `CLAIM_TRIGGERS`) must be pinned: inside a generated region, within reach
   of a `<!-- PROBE: <id>[, <id>…] -->` fence placed above it or of an
   `ADV-*` citation beside it (see `FENCE_REACH` / `ADV_REACH`), or
   explicitly waived in `WAIVERS` with a reason. An unpinned, unwaived claim
   fails the build — that is what covers the class rather than the instances.

Stated limits (deliberate, in the repo tradition of measured gates):

* The scan is lexical. A validator claim phrased without any trigger phrase is
  not caught; the trigger list errs toward the negative/limit claims that
  actually rotted (issue #133's table) plus the positive-limit phrasings that
  burned us ("accepts nothing else", "only the leading token").
* A PROBE fence pins the *fact* (the probe) and the *association* (the fence);
  the wording of a fenced sentence is still hand-maintained. The dense clusters
  are generated for exactly that reason — prefer a generated block when a whole
  section states validator behavior.
* Text inside markdown code fences is treated as a pinned span (validator
  output shown as an example is not a claim), so a claim written inside a
  ``` block escapes the scan.
* Probes grade the IN-REPO packages (`packages/*/src`), like every other drift
  gate in this repo — during a release window prose describes the contract
  about to be published, not the previous pin.

Usage::

    python3 scripts/render_validator_claims.py write   # regenerate blocks in place
    python3 scripts/render_validator_claims.py check   # CI: probes + blocks + scan
"""

from __future__ import annotations

import difflib
import functools
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent

# Same bootstrap as render_advisory.py: this repo is the contract's source, so
# put the in-repo trees on the path rather than relying on an installed wheel.
sys.path.insert(0, str(REPO_ROOT / "packages" / "contract-models" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "validator" / "src"))
# `analitiq.contracts.shared.common` reads os.environ["DOMAIN"] at import.
os.environ.setdefault("DOMAIN", "analitiq.ai")

PLUGINS_ROOT = REPO_ROOT / "plugins"
CONNECTOR_PLUGIN = PLUGINS_ROOT / "analitiq-connector-builder"
API_EXAMPLE = CONNECTOR_PLUGIN / "skills" / "connector-spec-api" / "examples" / "api-key"
DB_EXAMPLE = CONNECTOR_PLUGIN / "skills" / "connector-spec-db" / "examples" / "postgresql"
PIPELINE_PLUGIN = PLUGINS_ROOT / "analitiq-pipeline-builder"
PIPELINE_EXAMPLE = (
    PIPELINE_PLUGIN / "skills" / "pipeline-spec" / "examples" / "manual-api-to-db.example.json"
)
STREAM_EXAMPLE = (
    PIPELINE_PLUGIN / "skills" / "stream-spec" / "examples" / "api-full-refresh-insert.example.json"
)


# ---------------------------------------------------------------------------
# Probe harness
# ---------------------------------------------------------------------------

#: expect="clean"  -> no error-severity finding (warnings tolerated)
#: expect="error"  -> at least one error finding matching `message_re`
#: expect="silent" -> zero findings of any severity
@dataclass(frozen=True)
class Probe:
    id: str
    expect: str
    build: Callable[[], list[dict]]
    message_re: str = ""
    #: No finding of any severity may match this. Two uses: pinning a *gap* in
    #: a warning's coverage (families the write-coverage sample never probes),
    #: and hardening a "nothing checks X" claim beyond expect="clean" — a
    #: warning-tier check for X would falsify the sentence while leaving the
    #: document error-free.
    forbid_re: str = ""
    #: At least one finding of any severity must match this — for claims that
    #: describe a warning's behavior, so the probe fails if the warning itself
    #: disappears rather than merely lacking the forbidden families.
    require_re: str = ""


def _validate(doc: Any, doc_path: Path | None = None, schema_url: str | None = None) -> list[dict]:
    from analitiq.validator import validate_document

    return validate_document(doc, doc_path=doc_path, schema_url=schema_url)


def _read_endpoint() -> dict:
    return json.loads((API_EXAMPLE / "endpoints" / "v1__items.json").read_text())


def _endpoint_with_stop_when(ref: str) -> dict:
    doc = _read_endpoint()
    doc["operations"]["read"]["pagination"]["stop_when"] = {"empty": {"ref": ref}}
    return doc


def _endpoint_with_write(response: dict | None = None, request_extra: dict | None = None,
                         mode: str = "insert", batching: dict | None = None) -> dict:
    doc = _read_endpoint()
    request = {
        "method": "POST",
        "path": "/v1/items",
        "headers": {"Content-Type": "application/json"},
        "body": {"from_input": "records" if batching else "record"},
    }
    if request_extra:
        request.update(request_extra)
    block: dict = {
        "request": request,
        "params": {},
        "input": {"schema": {"type": "object", "properties": {"id": {"type": "string"}}}},
    }
    if response is not None:
        block["response"] = response
    if batching is not None:
        block["batching"] = batching
    doc["operations"]["write"] = {mode: block}
    return doc


def _example_body(example_dir: Path) -> dict:
    """The example connector document in `example_dir`, loud when absent."""
    body = next(example_dir.glob("*.example.json"), None)
    if body is None:
        raise FileNotFoundError(
            f"no *.example.json under {example_dir} — the example tree the "
            "probes stage moved; repoint the *_EXAMPLE constants")
    return json.loads(body.read_text())


def _staged_connector(mutate: Callable[[dict], dict], example_dir: Path,
                      read_map: list | None = None, write_map: list | None = None) -> list[dict]:
    """Validate a mutated example connector with its siblings staged on disk.

    Staging mirrors `tests/connector_builder/test_examples_validate.py`: the
    cross-file coverage checks walk a `definition/` directory, so the type maps
    (and endpoints, for the API example) must sit beside the document.
    """
    doc = mutate(_example_body(example_dir))
    with tempfile.TemporaryDirectory() as tmp:
        definition = Path(tmp) / "definition"
        definition.mkdir()
        (definition / "connector.json").write_text(json.dumps(doc))
        for name, override in (("type-map-read.json", read_map), ("type-map-write.json", write_map)):
            if override is not None:
                (definition / name).write_text(json.dumps(override))
            elif (example_dir / name).exists():
                shutil.copy(example_dir / name, definition / name)
        if (example_dir / "endpoints").is_dir():
            shutil.copytree(example_dir / "endpoints", definition / "endpoints")
        return _validate(doc, doc_path=definition / "connector.json")


def _staged_type_map(rules: list, filename: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / filename
        path.write_text(json.dumps(rules))
        return _validate(rules, doc_path=path)


def _first_transport(doc: dict) -> dict:
    return doc["transports"][next(iter(doc["transports"]))]


# --- endpoint probes: read-side scope guarantees ---------------------------

def _p_read_body_path_typo() -> list[dict]:
    return _validate(_endpoint_with_stop_when("response.body.nope"))


def _p_read_body_path_untyped() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["response"]["schema"]["properties"]["meta"] = {
        "type": "object", "properties": {"next": {}},
    }
    doc["operations"]["read"]["pagination"]["stop_when"] = {
        "empty": {"ref": "response.body.meta.next"},
    }
    return _validate(doc)


def _p_read_subscope_typo() -> list[dict]:
    return _validate(_endpoint_with_stop_when("response.bodyy.data"))


def _p_read_records_tail() -> list[dict]:
    return _validate(_endpoint_with_stop_when("response.records.next_cursor"))


def _p_read_headers_tail() -> list[dict]:
    return _validate(_endpoint_with_stop_when("response.headers.X-Made-Up"))


def _p_read_status_ref() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["pagination"]["stop_when"] = {
        "eq": [{"ref": "response.status"}, {"literal": 200}],
    }
    return _validate(doc)


def _p_read_record_count() -> list[dict]:
    return _validate(_endpoint_with_stop_when("response.record_count"))


def _p_read_metadata_undeclared() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["response"]["metadata"] = {"total": {"ref": "response.body.data"}}
    doc["operations"]["read"]["pagination"]["stop_when"] = {
        "empty": {"ref": "response.metadata.nope"},
    }
    return _validate(doc)


def _p_read_leading_scope_typo() -> list[dict]:
    return _validate(_endpoint_with_stop_when("conection.parameters.x"))


def _p_scope_tail_unchecked() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["request"]["headers"]["X-T"] = {"ref": "connection.discovered.nope"}
    return _validate(doc)


def _p_auth_state_tail() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["request"]["headers"]["X-T"] = {"ref": "connection.auth_state.token"}
    return _validate(doc)


def _p_runtime_tail() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["pagination"]["limit"]["default"] = {"ref": "runtime.run_id"}
    return _validate(doc)


def _p_request_slot_response_ref() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["request"]["query"]["cursor"] = {"ref": "response.body.data"}
    return _validate(doc)


def _p_request_slot_direct_runtime() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["request"]["query"]["t"] = {"ref": "runtime.batch_size"}
    return _validate(doc)


def _p_request_slot_template_smuggle() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["request"]["query"]["t"] = {"template": "v-${runtime.batch_size}"}
    return _validate(doc)


def _p_read_pathparam_from_input() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["request"]["path"] = "/v1/items/{id}"
    doc["operations"]["read"]["request"]["path_params"] = {"id": {"from_input": "record.id"}}
    return _validate(doc)


def _p_read_pathparam_bare_ref() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["request"]["path"] = "/v1/items/{id}"
    doc["operations"]["read"]["request"]["path_params"] = {"id": {"ref": "connection.parameters.x"}}
    return _validate(doc)


def _p_endpoint_schema_host() -> list[dict]:
    doc = _read_endpoint()
    doc["$schema"] = "https://schemas.analitiq.example/api-endpoint/latest.json"
    return _validate(doc)


def _p_endpoint_function_name() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["params"]["sig"] = {
        "in": "header", "type": "string", "required": False,
        "default": {"function": "jwt_sign", "input": {"key": {"ref": "secrets.k"}}},
    }
    doc["operations"]["read"]["request"]["headers"]["X-Sig"] = {"from_param": "sig"}
    return _validate(doc)


# --- endpoint probes: write-side scope guarantees --------------------------

def _p_write_body_path_typo() -> list[dict]:
    return _validate(_endpoint_with_write(
        response={"success_when": {"eq": [{"ref": "response.body.stauts"}, {"literal": "ok"}]}},
    ))


def _p_write_subscope_typo() -> list[dict]:
    return _validate(_endpoint_with_write(
        response={"success_when": {"empty": {"ref": "response.bodyy.errors"}}},
    ))


def _p_write_record_count_barred() -> list[dict]:
    return _validate(_endpoint_with_write(
        response={"success_when": {"eq": [{"ref": "response.record_count"}, {"literal": 1}]}},
    ))


def _p_write_metadata_undeclared() -> list[dict]:
    return _validate(_endpoint_with_write(
        response={"metadata": {"n": {"ref": "response.body.n"}},
                  "success_when": {"empty": {"ref": "response.metadata.nope"}}},
    ))


def _p_write_request_slot_response_ref() -> list[dict]:
    return _validate(_endpoint_with_write(request_extra={"query": {"c": {"ref": "response.body.id"}}}))


def _p_write_records_tail() -> list[dict]:
    return _validate(_endpoint_with_write(
        response={"success_when": {"empty": {"ref": "response.records.errors"}}},
    ))


def _p_write_headers_tail() -> list[dict]:
    return _validate(_endpoint_with_write(
        response={"success_when": {"eq": [{"ref": "response.headers.X-Made-Up"}, {"literal": "x"}]}},
    ))


def _p_write_status_ref() -> list[dict]:
    return _validate(_endpoint_with_write(
        response={"success_when": {"eq": [{"ref": "response.status"}, {"literal": 200}]}},
    ))


def _p_write_truncate_insert() -> list[dict]:
    return _validate(_endpoint_with_write(mode="truncate_insert", batching={"max_records": 100}))


# --- connector-document probes ---------------------------------------------

def _p_connector_refs_unchecked() -> list[dict]:
    def mutate(doc: dict) -> dict:
        _first_transport(doc).setdefault("headers", {})["X-Weird"] = {"ref": "garbage.nonsense"}
        return doc
    return _staged_connector(mutate, API_EXAMPLE)


def _p_connector_function_name() -> list[dict]:
    def mutate(doc: dict) -> dict:
        _first_transport(doc).setdefault("headers", {})["X-Sig"] = {
            "function": "jwt_sign", "input": {"key": {"literal": "k"}},
        }
        return doc
    return _staged_connector(mutate, API_EXAMPLE)


def _p_connector_lookup_map() -> list[dict]:
    def mutate(doc: dict) -> dict:
        _first_transport(doc).setdefault("headers", {})["X-Region"] = {
            "function": "lookup",
            "input": {"value": {"ref": "connection.parameters.region"},
                      "map": {"literal": {"eu": "eu-1"}}},
        }
        return doc
    return _staged_connector(mutate, API_EXAMPLE)


def _p_connector_schema_optional() -> list[dict]:
    def mutate(doc: dict) -> dict:
        doc.pop("$schema", None)
        return doc
    return _staged_connector(mutate, API_EXAMPLE)


def _p_connector_secret_literal() -> list[dict]:
    def mutate(doc: dict) -> dict:
        _first_transport(doc).setdefault("headers", {})["X-Api-Key"] = {"literal": "hunter2-a-real-secret"}
        return doc
    return _staged_connector(mutate, API_EXAMPLE)


def _p_tls_coherence() -> list[dict]:
    def mutate(doc: dict) -> dict:
        transport = _first_transport(doc)
        transport["tls"] = {"mode": {"literal": "verify-full"}}
        inputs = doc["connection_contract"]["inputs"]
        doc["connection_contract"]["inputs"] = {
            name: spec for name, spec in inputs.items() if "ssl" not in name
        }
        return doc
    return _staged_connector(mutate, DB_EXAMPLE)


def _p_read_map_completeness() -> list[dict]:
    read_map = json.loads((DB_EXAMPLE / "type-map-read.json").read_text())
    return _staged_connector(lambda doc: doc, DB_EXAMPLE, read_map=read_map[:1])


def _p_read_map_native_semantics() -> list[dict]:
    read_map = json.loads((DB_EXAMPLE / "type-map-read.json").read_text())
    read_map = [r for r in read_map if "JSONB" not in json.dumps(r)]
    read_map.append({"match": "exact", "native": "JSONB", "canonical": "Utf8"})
    return _staged_connector(lambda doc: doc, DB_EXAMPLE, read_map=read_map)


def _p_endpoint_pair_unresolved() -> list[dict]:
    endpoint = _read_endpoint()
    record = endpoint["operations"]["read"]["response"]["schema"]["properties"]["data"]["items"]
    record["properties"]["mystery"] = {
        "type": "string", "native_type": "MYSTERY_TYPE", "arrow_type": "Utf8",
    }
    doc = _example_body(API_EXAMPLE)
    with tempfile.TemporaryDirectory() as tmp:
        definition = Path(tmp) / "definition"
        (definition / "endpoints").mkdir(parents=True)
        (definition / "connector.json").write_text(json.dumps(doc))
        shutil.copy(API_EXAMPLE / "type-map-read.json", definition / "type-map-read.json")
        (definition / "endpoints" / "v1__items.json").write_text(json.dumps(endpoint))
        return _validate(doc, doc_path=definition / "connector.json")


# --- type-map probes --------------------------------------------------------

def _p_write_map_regex_case() -> list[dict]:
    rules = json.loads((DB_EXAMPLE / "type-map-write.json").read_text())
    rules.append({"match": "regex", "canonical": "^utf8$", "native": "TEXT"})
    return _staged_type_map(rules, "type-map-write.json")


def _p_write_coverage_sample_gap() -> list[dict]:
    rules = [
        {"match": "exact", "canonical": "Utf8", "native": "TEXT"},
        {"match": "exact", "canonical": "Int64", "native": "BIGINT"},
        {"match": "exact", "canonical": "Boolean", "native": "BOOLEAN"},
    ]
    return _staged_type_map(rules, "type-map-write.json")


def _p_pagination_limit_bare_zero() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["pagination"]["limit"]["default"] = 0
    return _validate(doc)


def _p_pagination_limit_literal_zero() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["pagination"]["limit"]["default"] = {"literal": 0}
    return _validate(doc)


# --- connection / pipeline / stream probes ---------------------------------

def _p_connection_sidecar_name() -> list[dict]:
    doc = json.loads(
        (PIPELINE_PLUGIN / "skills" / "connection-spec" / "examples" / "api-key.example.json")
        .read_text())
    key = next(iter(doc["secret_refs"]))
    doc["secret_refs"][key] = "sidecar:name-that-matches-no-input"
    return _validate(doc)


def _p_stream_selected_columns() -> list[dict]:
    doc = json.loads(
        (PIPELINE_PLUGIN / "skills" / "stream-spec" / "examples"
         / "db-full-refresh-truncate-insert.example.json").read_text())
    doc["source"]["selected_columns"] = ["no_such_column_xyz"]
    return _validate(doc)


def _p_stream_mapping_target() -> list[dict]:
    doc = json.loads(
        (PIPELINE_PLUGIN / "skills" / "stream-spec" / "examples"
         / "db-full-refresh-truncate-insert.example.json").read_text())
    doc["mapping"]["assignments"][0]["target"]["path"] = "no_such_destination_column"
    return _validate(doc)

def _p_pipeline_active_empty() -> list[dict]:
    doc = json.loads(PIPELINE_EXAMPLE.read_text())
    doc["status"] = "active"
    doc["streams"] = []
    return _validate(doc)


def _p_pipeline_draft_runnability() -> list[dict]:
    doc = json.loads(PIPELINE_EXAMPLE.read_text())
    doc["status"] = "draft"
    doc["streams"] = []
    return _validate(doc)


def _p_stream_filter_field_local() -> list[dict]:
    doc = json.loads(STREAM_EXAMPLE.read_text())
    doc["source"].setdefault("filters", []).append(
        {"field": "no_such_field_anywhere", "operator": "eq", "value": "x"},
    )
    return _validate(doc)


PROBES: tuple[Probe, ...] = (
    # read-side scope guarantees
    Probe("read-body-path-typo", "error", _p_read_body_path_typo,
          message_re=r"does not resolve in\s+response\.schema"),
    Probe("read-body-path-untyped", "error", _p_read_body_path_untyped,
          message_re=r"declares no `type`"),
    Probe("read-subscope-typo", "error", _p_read_subscope_typo,
          message_re=r"response sub-scope"),
    Probe("read-records-tail-unchecked", "clean", _p_read_records_tail),
    Probe("read-headers-tail-unchecked", "clean", _p_read_headers_tail),
    Probe("read-status-ref-unchecked", "clean", _p_read_status_ref),
    Probe("read-record-count-unchecked", "clean", _p_read_record_count),
    Probe("read-metadata-undeclared-key", "error", _p_read_metadata_undeclared,
          message_re=r"not a declared `response\.metadata` key"),
    Probe("read-leading-scope-typo", "error", _p_read_leading_scope_typo,
          message_re=r"leading token is not a known resolution scope"),
    Probe("scope-tail-unchecked", "clean", _p_scope_tail_unchecked),
    Probe("auth-state-tail-unchecked", "clean", _p_auth_state_tail),
    Probe("runtime-tail-unchecked", "clean", _p_runtime_tail),
    Probe("request-slot-response-ref", "error", _p_request_slot_response_ref,
          message_re=r"built before the response exists"),
    Probe("request-slot-direct-runtime-ref", "error", _p_request_slot_direct_runtime,
          message_re=r"direct stream/state/runtime ref"),
    Probe("request-slot-template-smuggle", "clean", _p_request_slot_template_smuggle),
    Probe("read-pathparam-from-input-rejected", "error", _p_read_pathparam_from_input,
          message_re=r"from_input is invalid in request\.path_params"),
    Probe("read-pathparam-bare-ref-rejected", "error", _p_read_pathparam_bare_ref,
          message_re=r"must be a `\{from_param"),
    Probe("endpoint-schema-host-locked", "error", _p_endpoint_schema_host,
          message_re=r"schemas\.analitiq\.ai/api-endpoint/latest\.json"),
    Probe("endpoint-function-name-unchecked", "clean", _p_endpoint_function_name,
          forbid_re=r"(?i)function"),
    # write-side scope guarantees
    Probe("write-body-path-typo-unresolved", "clean", _p_write_body_path_typo),
    Probe("write-subscope-typo", "error", _p_write_subscope_typo,
          message_re=r"response sub-scope"),
    Probe("write-record-count-barred", "error", _p_write_record_count_barred,
          message_re=r"read-only"),
    Probe("write-metadata-undeclared-key", "error", _p_write_metadata_undeclared,
          message_re=r"not a declared"),
    Probe("write-request-slot-response-ref", "error", _p_write_request_slot_response_ref,
          message_re=r"built before the response exists"),
    Probe("write-records-tail-unchecked", "clean", _p_write_records_tail),
    Probe("write-headers-tail-unchecked", "clean", _p_write_headers_tail),
    Probe("write-status-ref-unchecked", "clean", _p_write_status_ref),
    Probe("write-truncate-insert-accepted", "clean", _p_write_truncate_insert),
    # connector documents. The forbid_re on the "nothing checks X" probes is
    # what expect="clean" alone cannot give: a warning-tier check for X would
    # falsify the sentence while leaving the document error-free.
    Probe("connector-refs-unchecked", "clean", _p_connector_refs_unchecked),
    Probe("connector-function-name-unchecked", "clean", _p_connector_function_name,
          forbid_re=r"(?i)function"),
    Probe("connector-lookup-map-unvalidated", "clean", _p_connector_lookup_map,
          forbid_re=r"(?i)lookup"),
    Probe("connector-schema-optional", "clean", _p_connector_schema_optional),
    Probe("connector-secret-literal-undetected", "clean", _p_connector_secret_literal,
          forbid_re=r"(?i)secret|literal"),
    Probe("tls-coherence-unchecked", "clean", _p_tls_coherence,
          forbid_re=r"(?i)tls|ssl|certificate"),
    Probe("read-map-completeness-unchecked", "clean", _p_read_map_completeness,
          forbid_re=r"(?i)read.?map|type-map-read|coverage"),
    Probe("read-map-native-semantics-unchecked", "clean", _p_read_map_native_semantics),
    Probe("endpoint-pair-unresolved-through-read-map", "error", _p_endpoint_pair_unresolved,
          message_re=r"native_type 'MYSTERY_TYPE'"),
    # type maps
    Probe("write-map-regex-canonical-case-unchecked", "silent", _p_write_map_regex_case),
    # require_re holds the coverage warning itself in existence: without it,
    # deleting the whole type-map-write-coverage check would leave this probe
    # green while spec-type-maps.md keeps instructing authors to reconcile a
    # warning that no longer fires.
    Probe("write-coverage-sample-gap", "clean", _p_write_coverage_sample_gap,
          forbid_re=r"FixedSizeBinary|Time32|Decimal256",
          require_re=r"no rule rendering"),
    Probe("pagination-limit-bare-zero-rejected", "error", _p_pagination_limit_bare_zero,
          message_re=r"greater than 0"),
    Probe("pagination-limit-literal-zero-accepted", "clean", _p_pagination_limit_literal_zero),
    # connection / pipeline / stream
    Probe("connection-sidecar-name-unconstrained", "clean", _p_connection_sidecar_name,
          forbid_re=r"(?i)sidecar"),
    Probe("pipeline-active-empty-streams-rejected", "error", _p_pipeline_active_empty,
          message_re=r"at least one stream"),
    Probe("pipeline-draft-runnability-unchecked", "clean", _p_pipeline_draft_runnability,
          forbid_re=r"(?i)runnab"),
    Probe("stream-filter-field-unresolved-locally", "clean", _p_stream_filter_field_local,
          forbid_re=r"(?i)filter"),
    Probe("stream-selected-columns-unresolved-locally", "clean", _p_stream_selected_columns,
          forbid_re=r"(?i)column"),
    Probe("stream-mapping-target-unresolved-locally", "clean", _p_stream_mapping_target,
          forbid_re=r"(?i)target"),
)

PROBES_BY_ID: dict[str, Probe] = {p.id: p for p in PROBES}
assert len(PROBES_BY_ID) == len(PROBES), "duplicate probe id"


@dataclass(frozen=True)
class ProbeFailure:
    probe_id: str
    reason: str
    findings: list[dict] = field(default_factory=list)


def _expectation_failure(probe: Probe, findings: list[dict]) -> ProbeFailure | None:
    errors = [f for f in findings if f.get("severity") == "error"]
    if probe.expect == "silent" and findings:
        return ProbeFailure(probe.id, "expected zero findings", findings)
    if probe.expect == "clean" and errors:
        return ProbeFailure(probe.id, "expected no error findings", errors)
    if probe.expect == "error":
        if not errors:
            return ProbeFailure(probe.id, "expected an error finding, got none", findings)
        if not any(re.search(probe.message_re, f.get("message", "")) for f in errors):
            return ProbeFailure(
                probe.id, f"no error message matched {probe.message_re!r}", errors)
    return None


def _pattern_failure(probe: Probe, findings: list[dict]) -> ProbeFailure | None:
    if probe.forbid_re and any(
        re.search(probe.forbid_re, f.get("message", "")) for f in findings
    ):
        return ProbeFailure(probe.id, f"a finding matched forbidden {probe.forbid_re!r}", findings)
    if probe.require_re and not any(
        re.search(probe.require_re, f.get("message", "")) for f in findings
    ):
        return ProbeFailure(
            probe.id, f"no finding matched required {probe.require_re!r}", findings)
    return None


def run_probe(probe: Probe) -> ProbeFailure | None:
    if probe.expect not in ("clean", "error", "silent"):
        raise ValueError(f"probe {probe.id!r}: unknown expectation {probe.expect!r}")
    findings = probe.build()
    # The validator's own last-resort guard converts a crash into an error
    # finding whose message embeds the exception text. That text can contain
    # the same vocabulary as the real rejection message, so a crashed check
    # could otherwise satisfy an expect="error" probe while every user gets
    # "validator bug — please report" instead of the rejection the prose
    # promises. A crash never proves a claim, in either direction.
    crashed = [f for f in findings
               if re.search(r"crashed unexpectedly", f.get("message", ""))]
    if crashed:
        return ProbeFailure(probe.id, "the validator crashed on the probe document", crashed)
    return _expectation_failure(probe, findings) or _pattern_failure(probe, findings)


def verify_probes() -> list[ProbeFailure]:
    return [failure for probe in PROBES if (failure := run_probe(probe)) is not None]


# ---------------------------------------------------------------------------
# Claims: the prose, authored once, each naming the probes that prove it
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    probes: tuple[str, ...]


CLAIMS: tuple[Claim, ...] = (
    Claim(
        "phase-resolvability-unchecked",
        "> **This is entirely author-side.** No validator checks phase\n"
        "> resolvability: a transport referencing `connection.discovered.api_domain`\n"
        "> with no post-auth output producing it validates clean and fails at\n"
        "> connect. On a connector document refs are not checked *at all* — even a\n"
        "> nonsense scope passes — so there is no backstop here whatsoever. Walk\n"
        "> the phases by hand.",
        ("connector-refs-unchecked", "scope-tail-unchecked"),
    ),
    Claim(
        "tls-coherence-unchecked",
        "> **Nothing validates TLS coherence.** The contract's TLS block is\n"
        "> deliberately vocabulary-agnostic — it enforces no mode set and does not\n"
        "> check that a verification mode has a CA certificate to verify against.\n"
        "> Every rule below is author-side discipline; a connector that declares\n"
        "> `verify-full` with no `ssl_ca_certificate` input validates clean and\n"
        "> fails at connect. Apply the checklist by hand, for both SQLAlchemy and\n"
        "> ADBC shapes (they resolve through the same `connection_contract.inputs`).",
        ("tls-coherence-unchecked",),
    ),
    Claim(
        "function-names-unchecked",
        "**Nothing validates the function name.** An unregistered name (including\n"
        "`jwt_sign`) passes every check and fails only when the engine tries to\n"
        "resolve it at connect time — on a connector document and on an endpoint\n"
        "alike. Treat the catalog above as closed and verify by hand; the\n"
        "validator will not catch a typo or a planned-but-unregistered function.\n"
        "To extend the catalog, the engine's function registry must be updated\n"
        "first.",
        ("connector-function-name-unchecked", "endpoint-function-name-unchecked"),
    ),
)

CLAIMS_BY_ID: dict[str, Claim] = {c.id: c for c in CLAIMS}
assert len(CLAIMS_BY_ID) == len(CLAIMS), "duplicate claim id"


# ---------------------------------------------------------------------------
# The response-scope table, rendered from probe expectations
# ---------------------------------------------------------------------------

#: cell kind -> (expectation class every backing probe must land in, cell text).
#: The class is binary: "error", or "clean" (a `silent` probe counts as clean).
_CELL_KINDS: dict[str, tuple[str, str]] = {
    "path-resolved": ("error", "resolved against `response.schema`, must declare a type (ADV-ENDP-023)"),
    "declared-key": ("error", "must name a declared key"),
    "spelling-only": ("clean", "spelling only"),
    "not-resolved": ("clean", "**not resolved** — see below"),
    "barred": ("error", "**barred** — read-only scope"),
}

#: ref label -> (read cell, write cell); each cell = (kind, backing probe ids)
_SCOPE_TABLE_ROWS: tuple[tuple[str, tuple[str, tuple[str, ...]], tuple[str, tuple[str, ...]]], ...] = (
    ("`response.body.<path>`",
     ("path-resolved", ("read-body-path-typo", "read-body-path-untyped")),
     ("not-resolved", ("write-body-path-typo-unresolved",))),
    ("`response.metadata.<key>`",
     ("declared-key", ("read-metadata-undeclared-key",)),
     ("declared-key", ("write-metadata-undeclared-key",))),
    ("`response.records.<path>`",
     ("spelling-only", ("read-records-tail-unchecked",)),
     ("spelling-only", ("write-records-tail-unchecked",))),
    ("`response.headers.<name>`",
     ("spelling-only", ("read-headers-tail-unchecked",)),
     ("spelling-only", ("write-headers-tail-unchecked",))),
    ("`response.status`",
     ("spelling-only", ("read-status-ref-unchecked",)),
     ("spelling-only", ("write-status-ref-unchecked",))),
    ("`response.record_count`",
     ("spelling-only", ("read-record-count-unchecked",)),
     ("barred", ("write-record-count-barred",))),
)


def _cell(kind: str, probe_ids: tuple[str, ...]) -> str:
    if not probe_ids:
        raise RuntimeError(
            f"scope-table cell {kind!r} has no backing probe — an unmeasured "
            "cell is the exact claim class this registry exists to pin"
        )
    expected, text = _CELL_KINDS[kind]
    for probe_id in probe_ids:
        probe = PROBES_BY_ID[probe_id]
        actual = "error" if probe.expect == "error" else "clean"
        if actual != expected:
            raise RuntimeError(
                f"scope-table cell {kind!r} requires probe expectation {expected!r}, "
                f"but {probe_id!r} states {probe.expect!r} — the table no longer "
                "matches the measurement; update the cell kind and the prose"
            )
    return text


def scope_table_probe_ids() -> set[str]:
    ids: set[str] = set()
    for _label, read_cell, write_cell in _SCOPE_TABLE_ROWS:
        ids.update(read_cell[1])
        ids.update(write_cell[1])
    return ids


# ---------------------------------------------------------------------------
# Block renderers
# ---------------------------------------------------------------------------

def render_scope_guarantees() -> str:
    table = ["| Ref | Read op | Write op |", "|---|---|---|"]
    for label, read_cell, write_cell in _SCOPE_TABLE_ROWS:
        table.append(f"| {label} | {_cell(*read_cell)} | {_cell(*write_cell)} |")

    return "\n".join([
        "**Scope checking is two-tier on an endpoint.** Every expression slot on an",
        "operation is swept — `pagination` (every `stop_when` operand included),",
        "`response.metadata`, the `request` `path_params` / `headers` / `query` / `body`",
        "slots, and each `params.<name>.default`. For most scopes only the **leading",
        "token** is checked, so `connection.discovered.nope` passes and resolves empty.",
        "",
        "What is actually proved, by sub-scope. Everything is spelling-checked (a bad",
        "sub-scope like `response.bodyy` is always an error); only the first two rows",
        "are resolved against something declared — `response.body` paths only on a",
        "read, `response.metadata` keys on either operation:",
        "",
        *table,
        "",
        "So `response.body.nope` in a read pagination block is an error rather than a",
        "silent one-page sync — but `response.records.next_cursor` and",
        "`response.headers.X-Made-Up` are not, and cannot be: headers, status and",
        "record counts are runtime values this document declares nothing about, and the",
        "`response.records` **scope** is not the `response.records` **field**. (That",
        "field — the `{ref: response.body.<path>}` selecting the record collection — IS",
        "resolved, and must land on an array node, ADV-ENDP-012. Referencing",
        "`response.records.<something>` from a pagination or metadata expression is a",
        "different thing and is unchecked.)",
        "",
        "**A write mode has no `response.schema`**, so nothing under",
        "`operations.write.<mode>.response` is path-resolved at all: a typo in",
        "`success_when`, `affected_records` or `error.*` validates clean. That is the",
        "worst cell in the table — a `success_when` predicate over a ref that resolves",
        "to nothing holds unconditionally, so every write reports success, including",
        "the ones whose rejected rows the provider listed. Trace write response refs",
        "against the provider's real payload yourself.",
        "",
        "Wherever it appears, a `response.*` ref in a request slot or a param `default`",
        "is refused outright, whatever it names and on either operation: the request is",
        "built before the response exists, so it could only interpolate to nothing.",
        "",
        "In a **connector** document (a transport header, an auth template) there is no",
        "check at all — treat every ref there as unverified and trace it to the",
        "declaration that produces it yourself.",
    ]) + "\n"


#: Probes backing prose inside `scope-guarantees` that is not a table cell.
_SCOPE_GUARANTEES_EXTRA_PROBES: tuple[str, ...] = (
    "read-subscope-typo", "write-subscope-typo", "read-leading-scope-typo",
    "scope-tail-unchecked", "request-slot-response-ref",
    "write-request-slot-response-ref", "connector-refs-unchecked",
)


def render_validator_blind_spots() -> str:
    return "\n".join([
        "Checks the plugin's prose once claimed but the validator does **not** perform —",
        "do not rely on them, and treat these as author-side discipline:",
        "",
        "- **Function names are never checked.** An unregistered or misspelled",
        "  `{\"function\": …}` passes validation and fails at connect time.",
        "- **Ref *resolvability* is checked for exactly two things, one of them",
        "  read-only.** On a READ, a `response.body.<path>` is resolved against",
        "  `response.schema`; on either operation, a `response.metadata.<key>` is",
        "  checked against the declared keys. Those typos are errors. Nothing else is",
        "  proved, and three cases in particular look proved and are not:",
        "  `response.records.<path>` and `response.headers.<name>` are spelling-checked",
        "  only, and a WRITE mode has no `response.schema`, so no write-side",
        "  `response.body` path is resolved — a `success_when` typo validates clean and",
        "  the predicate then holds unconditionally. Every remaining scope is checked",
        "  on its leading token only, and a connector document is not ref-checked at",
        "  all — so a `connection.discovered.*` ref with no post-auth output that",
        "  produces it validates clean, on either document.",
        "- **TLS `ssl_mode` ↔ `ssl_ca_certificate` consistency is not checked.**",
    ]) + "\n"


_BLIND_SPOT_PROBES: tuple[str, ...] = (
    "connector-function-name-unchecked", "endpoint-function-name-unchecked",
    "read-body-path-typo", "read-metadata-undeclared-key",
    "write-metadata-undeclared-key", "read-records-tail-unchecked",
    "read-headers-tail-unchecked", "write-body-path-typo-unresolved",
    "scope-tail-unchecked", "connector-refs-unchecked", "tls-coherence-unchecked",
)


def _render_claim(claim_id: str) -> str:
    return CLAIMS_BY_ID[claim_id].text + "\n"


RENDERERS: dict[str, Callable[[], str]] = {
    "scope-guarantees": render_scope_guarantees,
    "validator-blind-spots": render_validator_blind_spots,
    **{f"claim:{c.id}": (lambda cid=c.id: _render_claim(cid)) for c in CLAIMS},
}


def block_probe_ids(block_id: str) -> set[str]:
    """The probe ids one block stands on."""
    if block_id == "scope-guarantees":
        return scope_table_probe_ids() | set(_SCOPE_GUARANTEES_EXTRA_PROBES)
    if block_id == "validator-blind-spots":
        return set(_BLIND_SPOT_PROBES)
    if block_id.startswith("claim:"):
        return set(CLAIMS_BY_ID[block_id[len("claim:"):]].probes)
    raise UnknownBlock(block_id)


def embedded_block_ids() -> set[str]:
    """Block ids actually embedded (well-formed marker pair) in some doc."""
    ids: set[str] = set()
    for path in generated_docs():
        for match in _BLOCK_RE.finditer(path.read_text(encoding="utf-8")):
            ids.add(match.group("id"))
    return ids


def rendered_block_probe_ids() -> set[str]:
    """Every probe id an *embedded* block stands on.

    Counting a renderer's probes unconditionally would let a deleted block
    keep its probes "referenced" while the prose they pin no longer exists —
    the vacuity `unreferenced probe(s)` exists to prevent.
    """
    ids: set[str] = set()
    for block_id in embedded_block_ids():
        ids |= block_probe_ids(block_id)
    return ids


@functools.lru_cache(maxsize=None)
def _pipeline_gen():
    """The pipeline plugin's generator module, imported by path (cached).

    Its renderer registry decides which pipeline-plugin generated blocks are
    real; restating that set (or its marker grammar) here would be a drift
    surface. Import is side-effect-free — the dependency bootstrap only runs
    when its `main()` calls it.
    """
    import importlib.util

    scripts_dir = str(PIPELINE_PLUGIN / "scripts")
    spec = importlib.util.spec_from_file_location(
        "_pipeline_gen_contract_docs", PIPELINE_PLUGIN / "scripts" / "gen_contract_docs.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, scripts_dir)  # gen_contract_docs imports _bootstrap
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_dir)
    return module


def _known_block_ids_for(path: Path) -> set[str]:
    """The block ids a real renderer stands behind, for this doc's tree.

    Only these pin content during the scan. A marker pair with any other id is
    prose that LOOKS machine-pinned while nothing renders or checks it — the
    exact artifact this gate exists to prevent — so it pins nothing and
    `malformed_marker_docs` reports it.
    """
    if PIPELINE_PLUGIN in path.parents:
        return set(_pipeline_gen().RENDERERS)
    return set(RENDERERS)


def malformed_marker_docs() -> list[str]:
    """Docs whose BEGIN markers don't all resolve to a real, well-formed block.

    Covers two silent degradations: an unpaired or typo'd marker (`render_text`
    leaves the region untouched and the sync test sees no diff), and — in
    either plugin — a marker pair whose id no renderer owns. The pipeline
    generator's grammar rejects e.g. `claim:*` ids without raising, so such a
    pair would otherwise be checked by nobody while exempting nothing-checks-it
    prose from this scan.
    """
    broken: list[str] = []
    for path in sorted(PLUGINS_ROOT.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        begins = text.count("<!-- BEGIN GENERATED:")
        if not begins:
            continue
        known = _known_block_ids_for(path)
        matched = [m.group("id") for m in _BLOCK_RE.finditer(text)]
        if begins != len(matched) or any(block_id not in known for block_id in matched):
            broken.append(path.relative_to(REPO_ROOT).as_posix())
    return broken


# ---------------------------------------------------------------------------
# Marker-block substitution (the pipeline plugin's grammar)
# ---------------------------------------------------------------------------

_BLOCK_RE = re.compile(
    r"(?P<begin><!-- BEGIN GENERATED: (?P<id>[a-z0-9][a-z0-9:-]*) -->\n)"
    r"(?P<body>.*?)"
    r"(?P<end><!-- END GENERATED: (?P=id) -->)",
    re.DOTALL,
)


class UnknownBlock(KeyError):
    """A doc references a block id no renderer produces — fail loud, never skip."""


def render_text(text: str, source: str) -> str:
    def _sub(match: re.Match) -> str:
        block_id = match.group("id")
        try:
            renderer = RENDERERS[block_id]
        except KeyError:
            raise UnknownBlock(
                f"{source}: no renderer for generated block {block_id!r}; "
                f"known blocks: {', '.join(sorted(RENDERERS))}"
            ) from None
        return match.group("begin") + renderer() + match.group("end")

    return _BLOCK_RE.sub(_sub, text)


def generated_docs() -> list[Path]:
    """Connector-plugin docs carrying a generated-block marker.

    Any block id found here must have a renderer in this script — an unknown
    id fails loud (`UnknownBlock`), never skips. The pipeline plugin's
    generated blocks belong to its own `gen_contract_docs.py`; this renderer
    never touches that tree.
    """
    return sorted(
        p for p in CONNECTOR_PLUGIN.rglob("*.md")
        if "<!-- BEGIN GENERATED:" in p.read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# The scan: unpinned validator-behavior claims fail the build
# ---------------------------------------------------------------------------

#: Sentence shapes that assert validator behavior. Lexical by design; the
#: docstring states the coverage limit.
CLAIM_TRIGGERS: tuple[str, ...] = (
    r"validates?\s+(?:clean|cleanly|with\s+zero\s+findings)",
    r"passes?\s+(?:validation|every\s+check)",
    # "still passes." (validation) — but not "still passes the …" (an argument)
    r"still\s+passes\b(?!\s+(?:the|a|an)\b)",
    r"(?:is|are)\s+(?:not|never)\s+(?:checked|validated|resolved|proved|proven|enforced|caught)",
    r"(?:never|nothing)\s+(?:checks?|checked|validates?|rejects?|proves?|enforces?|catch(?:es)?)",
    r"(?:does|do)\s+not\s+(?:check|validate|resolve|read\s+filter)",
    r"\bnot\s+checked\b",
    r"\bno\s+(?:check\b|backstop|validator\s+(?:checks|will))",
    r"\bunchecked\b",
    r"spelling[-\s](?:checked|only)",
    r"\bleading\s+token\b",
    r"read-?only\s+scope",
    r"accepts?\s+nothing\s+else",
    r"slips?\s+past",
)

_TRIGGER_RE = re.compile("|".join(f"(?:{t})" for t in CLAIM_TRIGGERS), re.IGNORECASE)
_FENCE_RE = re.compile(r"<!--\s*PROBE:\s*(?P<ids>[a-z0-9-]+(?:\s*,\s*[a-z0-9-]+)*)\s*-->")
_ADV_RE = re.compile(r"ADV-[A-Z]+-\d+")


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class Waiver:
    """A validator claim declared unpinnable, with the reason on record.

    `contains` is matched against the whole contiguous block a trigger hit
    sits in, and one matching waiver suppresses every hit in that block. A
    waiver that matches no trigger-carrying block is stale and fails the scan,
    so the registry cannot accumulate dead exemptions.
    """

    path: str  # repo-relative
    contains: str  # unique substring of the waived line
    reason: str


WAIVERS: tuple[Waiver, ...] = (
    Waiver(
        "plugins/analitiq-connector-builder/agents/db-connector-creator.md",
        "Nothing validates the match; a",
        "resource_discovery strategy fit is a property of the live database's "
        "object hierarchy at run time; no authored document exists to probe.",
    ),
    Waiver(
        "plugins/analitiq-connector-builder/skills/connector-builder/SKILL.md",
        "are NOT validated here",
        "the validator's input surface is JSON documents; package files never "
        "reach it, so there is no document whose acceptance a probe could pin.",
    ),
    Waiver(
        "plugins/analitiq-connector-builder/skills/connector-builder/SKILL.md",
        "type maps MUST validate clean before any endpoint fan-out",
        "imperative workflow instruction to the orchestrator, not a claim about "
        "what the validator checks.",
    ),
    Waiver(
        "plugins/analitiq-connector-builder/skills/connector-builder/references/pipeline.md",
        "type maps MUST validate clean before the phase-5 endpoint fan-out",
        "imperative workflow instruction to the orchestrator, not a claim about "
        "what the validator checks.",
    ),
    Waiver(
        "plugins/analitiq-connector-builder/skills/connector-spec-db/spec-connector-package.md",
        "BigQuery primary keys\nare NOT ENFORCED",
        "a statement about BigQuery's database semantics, not about this "
        "repo's validator.",
    ),
    Waiver(
        "plugins/analitiq-pipeline-builder/agents/stream-creator.md",
        "do not validate — those are downstream steps",
        "describes the agent's own role boundary, not validator behavior.",
    ),
)


def _code_fence_spans(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line-index spans of ``` / ~~~ code fences.

    Paired line-wise, tolerating indentation (fences inside list items are
    common in both plugins) — a single multiline regex anchored at column 0
    mis-paired an indented opener with a later column-0 closer and silently
    swallowed the prose between two code blocks. An unclosed fence runs to
    EOF, which can only over-exempt the tail of that one file, loudly visible
    in the doc itself.
    """
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    marker = ""
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if open_at is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                open_at, marker = i, stripped[:3]
        elif stripped.startswith(marker):
            spans.append((open_at, i))
            open_at = None
    if open_at is not None:
        spans.append((open_at, len(lines) - 1))
    return spans


def _line_spans(text: str, known_block_ids: set[str]) -> list[tuple[int, int]]:
    """(start, end) line-index spans the scan treats as pinned.

    Two kinds: generated regions whose block id a real renderer owns (an
    unknown id pins nothing — see `_known_block_ids_for`), and code fences
    (example output is not a claim).
    """
    lines = text.splitlines()
    spans = _code_fence_spans(lines)
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    def to_line(pos: int) -> int:
        lo, hi = 0, len(offsets) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if offsets[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    for match in _BLOCK_RE.finditer(text):
        if match.group("id") in known_block_ids:
            spans.append((to_line(match.start()), to_line(match.end())))
    return spans


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Contiguous runs of non-blank lines, as (start, end) inclusive indexes."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(lines) - 1))
    return runs


def _scannable_docs() -> list[Path]:
    docs = []
    for path in sorted(PLUGINS_ROOT.rglob("*.md")):
        if path.name == "CHANGELOG.md" and path.parent.parent == PLUGINS_ROOT:
            continue  # release-please owned; only at a plugin root
        if path.name == "advisory-rules.md":
            # Exempt only while the premise holds — verify, don't trust the
            # filename (the gen_contract_docs NOT_GENERATED pattern).
            first = path.read_text(encoding="utf-8").split("\n", 1)[0]
            if "GENERATED by scripts/render_advisory.py" in first:
                continue
        docs.append(path)
    return docs


def _normalize(text: str) -> str:
    """Strip markdown emphasis so `validates **clean**` still matches a trigger.

    Backticks, asterisks and underscores are removed; newlines are kept so a
    match position still maps back to a source line.
    """
    return re.sub(r"[*_`]", "", text)


#: A fence pins its own line and the lines below it, this far. Directional on
#: purpose: fences are placed ABOVE the sentence they prove, and a downward-only
#: reach stops a fence from retroactively pinning an unrelated claim that
#: happens to sit just above it in the same list.
FENCE_REACH = 10
#: An ADV-* citation pins its own sentence, which may wrap — a couple of lines
#: either side, no more. A citation of rule X two paragraphs up must not exempt
#: a negative claim about Y.
ADV_REACH = 2


def _hit_is_pinned(line_index: int, block: tuple[int, int],
                   pinned_spans: list[tuple[int, int]],
                   fence_lines: list[int], adv_lines: list[int]) -> bool:
    start, end = block
    if any(s <= line_index <= e for s, e in pinned_spans):
        return True
    if any(start <= f <= end and f <= line_index <= f + FENCE_REACH
           for f in fence_lines):
        return True
    return any(start <= a <= end and abs(a - line_index) <= ADV_REACH
               for a in adv_lines)


def _matching_waiver(rel: str, window: str, trigger_text: str) -> Waiver | None:
    """The waiver covering this hit, if any.

    Both conditions are load-bearing: `contains in window` binds the waiver to
    the hit's location, and `trigger in contains` binds it to this specific
    trigger phrase — without the second, a new claim appended beside a waived
    sentence would ride its neighbour's waiver.
    """
    return next(
        (w for w in WAIVERS
         if w.path == rel
         and (w.contains in window or w.contains in _normalize(window))
         and trigger_text in _normalize(w.contains)),
        None)


def _dangling_fence_ids(rel: str, text: str) -> list[str]:
    return [
        f"{rel}: fence names unknown probe {fence_id!r}"
        for fence in _FENCE_RE.finditer(text)
        for fence_id in re.split(r"\s*,\s*", fence.group("ids"))
        if fence_id not in PROBES_BY_ID
    ]


def _scan_doc(path: Path) -> tuple[list[Violation], list[str], set[Waiver]]:
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO_ROOT).as_posix()
    lines = text.splitlines()
    pinned_spans = _line_spans(text, _known_block_ids_for(path))
    fence_lines = [i for i, line in enumerate(lines) if _FENCE_RE.search(line)]
    adv_lines = [i for i, line in enumerate(lines) if _ADV_RE.search(line)]

    violations: list[Violation] = []
    used: set[Waiver] = set()
    for block in _blocks(lines):
        start, end = block
        normalized = _normalize("\n".join(lines[start:end + 1]))
        for match in _TRIGGER_RE.finditer(normalized):
            line_index = start + normalized[:match.start()].count("\n")
            if _hit_is_pinned(line_index, block, pinned_spans, fence_lines, adv_lines):
                continue
            window = "\n".join(
                lines[max(start, line_index - 1):min(end, line_index + 1) + 1])
            waiver = _matching_waiver(rel, window, match.group(0))
            if waiver is not None:
                used.add(waiver)
                continue
            violations.append(Violation(rel, line_index + 1, match.group(0)))
    return violations, _dangling_fence_ids(rel, text), used


def scan() -> tuple[list[Violation], list[str], list[Waiver]]:
    """Returns (violations, dangling fence ids, stale waivers).

    Matching runs over each contiguous block's joined, emphasis-normalized text,
    so a trigger phrase wrapped across lines ("Nothing\\nvalidates …") is still
    caught. Pinning is windowed, not block-wide: a fence covers its own line
    plus `FENCE_REACH` lines below it, an ADV citation `ADV_REACH` lines either
    side, and a waiver's `contains` must match within one line of the hit —
    a 50-line checklist is one contiguous "block", and block-wide pinning let
    one fence exempt every other bullet in it.
    """
    violations: list[Violation] = []
    dangling: list[str] = []
    used_waivers: set[Waiver] = set()
    for path in _scannable_docs():
        doc_violations, doc_dangling, doc_used = _scan_doc(path)
        violations.extend(doc_violations)
        dangling.extend(doc_dangling)
        used_waivers |= doc_used
    stale = [w for w in WAIVERS if w not in used_waivers]
    return violations, dangling, stale


def fence_probe_ids() -> set[str]:
    ids: set[str] = set()
    for path in _scannable_docs():
        for fence in _FENCE_RE.finditer(path.read_text(encoding="utf-8")):
            ids.update(re.split(r"\s*,\s*", fence.group("ids")))
    return ids


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _report_probe_failures(failures: list[ProbeFailure]) -> None:
    for failure in failures:
        print(f"probe {failure.probe_id!r} FAILED: {failure.reason}", file=sys.stderr)
        for finding in failure.findings[:5]:
            print(f"    {finding.get('severity')}: {finding.get('message', '')[:160]}",
                  file=sys.stderr)
    print(
        f"\n{len(failures)} probe(s) no longer match the in-repo contract. Every "
        "probe pins a sentence in plugin prose; update the prose AND the probe "
        "together in scripts/render_validator_claims.py, then run "
        "`python3 scripts/render_validator_claims.py write`.",
        file=sys.stderr,
    )


def _render_docs(docs: list[Path], write: bool) -> list[str]:
    """Render every doc's blocks; return the stale ones (check) or write them."""
    stale_docs: list[str] = []
    for path in docs:
        current = path.read_text(encoding="utf-8")
        rendered = render_text(current, str(path))
        if current == rendered:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if write:
            path.write_text(rendered, encoding="utf-8")
            print(f"regenerated {rel}")
        else:
            stale_docs.append(rel)
            sys.stdout.writelines(difflib.unified_diff(
                current.splitlines(keepends=True), rendered.splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    return stale_docs


def _check_problems(stale_docs: list[str]) -> list[str]:
    """Every gate failure as a printable message; empty means the gate holds."""
    violations, dangling, stale_waivers = scan()
    referenced = rendered_block_probe_ids() | fence_probe_ids()
    unreferenced = sorted(set(PROBES_BY_ID) - referenced)
    unembedded = sorted(set(RENDERERS) - embedded_block_ids())
    broken_markers = malformed_marker_docs()

    problems: list[str] = []
    if unembedded:
        problems.append(
            f"renderer(s) with no embedded block: {', '.join(unembedded)} — "
            "a renderer no document embeds is dead code and its probes pin "
            "nothing; embed the block or delete the renderer.")
    if broken_markers:
        problems.append(
            f"malformed generated-block markers in: {', '.join(broken_markers)} "
            "— a BEGIN marker with no matching END (or an id no renderer owns) "
            "leaves the region looking generated while nothing regenerates or "
            "checks it.")
    if stale_docs:
        problems.append(
            f"{len(stale_docs)} document(s) stale: {', '.join(stale_docs)}\n"
            "Run: python3 scripts/render_validator_claims.py write")
    if violations:
        listing = "\n".join(f"  {v.path}:{v.line}: {v.text[:120]}" for v in violations)
        problems.append(
            f"{len(violations)} unpinned validator-behavior claim(s):\n{listing}\n"
            "Each flagged sentence states what the validator does or does not "
            "check. Pin it: move it into a generated block, add a "
            "`<!-- PROBE: <id> -->` fence backed by a probe in "
            "scripts/render_validator_claims.py, cite the ADV-* rule that enforces "
            "it, or register a Waiver with the reason it cannot be pinned.")
    if dangling:
        problems.append("dangling probe fences:\n  " + "\n  ".join(dangling))
    for waiver in stale_waivers:
        problems.append(
            f"stale waiver (matches nothing): {waiver.path}: {waiver.contains!r}")
    if unreferenced:
        problems.append(
            f"unreferenced probe(s): {', '.join(unreferenced)} — a probe must "
            "back a rendered block or a prose fence; delete it or wire it up.")
    return problems


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "check"
    if mode not in ("write", "check"):
        print(f"usage: {argv[0]} [write|check]", file=sys.stderr)
        return 2

    failures = verify_probes()
    if failures:
        _report_probe_failures(failures)
        return 1

    docs = generated_docs()
    stale_docs = _render_docs(docs, write=(mode == "write"))
    if mode == "write":
        return 0

    problems = _check_problems(stale_docs)
    for problem in problems:
        print(f"\n{problem}", file=sys.stderr)
    if not problems:
        print(f"{len(PROBES)} probes hold; {len(docs)} document(s) in sync; scan clean")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
