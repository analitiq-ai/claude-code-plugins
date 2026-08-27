#!/usr/bin/env python3
"""Executable registry of validator-behavior claims, and the blocks rendered from it.

Plugin prose states what the validator checks and — more dangerously — what it
does *not* check ("a `success_when` typo validates clean", "function names are
never checked", "only the leading scope token is validated"). Each such sentence
is a copy of a contract fact, and before this registry existed nothing pinned
any of them: a contract-models change could falsify prose across several files
while CI stayed green.

This module is the fix, in three parts:

1. **Probes** — tiny documents run through the in-repo validator
   (`analitiq.validator.validate_document`, the same entry the plugins invoke),
   each asserting an outcome: rejected with a message, accepted clean, or
   accepted silent (zero findings). A probe is a *measurement*; if the contract
   moves under it, `verify_probes()` fails and CI goes red. A claim about what
   a plugin's own adapter does — the pipeline plugin chooses `require_runnable`
   from the pipeline's status — is measured through that adapter instead, which
   is the entry its agent runs; the findings list is the same shape either way.
2. **Claims** — the prose sentences themselves, authored ONCE here, each naming
   the probes that prove it. Marked regions in the plugin docs
   (`<!-- BEGIN GENERATED: <block-id> -->` … `END GENERATED`, the same marker
   grammar as `gen_pipeline_docs.py`) are rendered from
   this registry — `claim:<id>` blocks from the `Claim` records, the dense
   clusters (the response-scope table, the validator blind-spot list) by the
   dedicated `render_*` functions whose probe couplings sit beside them — so
   neither can drift from the measurement behind it. One block family stands
   on data instead of probes: the connector release-policy projections (see
   `_release_table`).
3. **Fence bookkeeping** — a `<!-- PROBE: <id>[, <id>…] -->` fence naming an id
   no probe defines fails the build, and a probe no block renders and no fence
   names fails the build. Both directions are decidable: an id either resolves
   in this registry or it does not.

   What is NOT here: a gate deciding whether a SENTENCE asserts validator
   behavior. That was a list of hand-curated English regexes, and the rule
   against it is in `.claude/rules/guards.md`, and the authoring obligation the
   regexes were standing in for is in `.claude/rules/plugin-prose.md`. Pinning a
   claim is still required; noticing that a sentence makes one is a reader's job.

Stated limits (deliberate, in the repo tradition of measured gates):

* A PROBE fence pins the *fact* (the probe) and the *association* (the fence),
  and nothing checks that the sentence beside it describes what the probe
  measures. A probe can keep passing while the prose says something else. A
  generated block cannot drift that way, which is why it is the first rung of
  the validator-claim ladder in `.claude/rules/plugin-prose.md` — prefer one
  whenever a whole section states validator behavior. Most sites are fenced
  rather than generated because generating readable English for one spot in one
  document is more work than pointing at a probe, not because fencing is
  equivalent.
* Probes grade the IN-REPO packages (`packages/*/src`), like every other drift
  gate in this repo — during a release window prose describes the contract
  about to be published, not the previous pin.

Usage::

    python3 scripts/render_validator_claims.py write   # regenerate blocks in place
    python3 scripts/render_validator_claims.py check   # CI: probes + blocks + fences
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

# Same bootstrap as render_rule_reference.py: this repo is the contract's source, so
# put the in-repo trees on the path rather than relying on an installed wheel.
sys.path.insert(0, str(REPO_ROOT / "packages" / "contract-models" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "validator" / "src"))
# `analitiq.contracts.shared.common` reads os.environ["DOMAIN"] at import.
os.environ.setdefault("DOMAIN", "analitiq.ai")

PLUGINS_ROOT = REPO_ROOT / "plugins"
CONTRIBUTING_ROOT = REPO_ROOT / "contributing"
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
#: The two connection documents `STREAM_EXAMPLE` and `PIPELINE_EXAMPLE` already
#: reference by `connection_id`, plus the endpoint document a database
#: destination has to resolve to. Staged together they make an on-disk bundle
#: out of documents `tests/pipeline_builder/test_examples.py` already validates.
CONNECTION_EXAMPLES = PIPELINE_PLUGIN / "skills" / "connection-spec" / "examples"
SOURCE_CONNECTION_EXAMPLE = CONNECTION_EXAMPLES / "none.example.json"
DESTINATION_CONNECTION_EXAMPLE = CONNECTION_EXAMPLES / "db.example.json"
ENDPOINT_EXAMPLE = (
    PIPELINE_PLUGIN / "skills" / "endpoint-spec" / "examples" / "postgres-table.example.json"
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


def _record_node(doc: dict) -> dict:
    """The LIVE record-shape node of the shipped example's read response, for a
    probe to declare a field on.

    Indexed, not resolved. `analitiq.contracts.endpoints.resolve_read_record_schema`
    is the record locator every consumer of the read contract must call — it
    follows `$ref` and composes `allOf`, which this does not — but it
    materializes a copy, and a probe needs the node the document actually
    carries so its mutation reaches the validator. So the index is checked
    against it: re-author the example's record behind `$defs` and this fails
    here rather than stamping a field onto a node nothing grades.
    """
    from analitiq.contracts.endpoints import resolve_read_record_schema

    response = doc["operations"]["read"]["response"]
    indexed_ref = "response.body.data"
    node = response["schema"]["properties"]["data"]["items"]
    resolved = resolve_read_record_schema(response, response["schema"])
    # Both halves, because each misses what the other catches: the ref pins
    # WHERE the record is, and a retargeted ref whose new path happens to carry
    # an identical shape compares equal; the value pins what is AT the indexed
    # path, and a record moved behind a `$ref` leaves the ref untouched.
    if response["records"]["ref"] != indexed_ref or resolved != node:
        # Raised, not asserted: `-O` strips an assert, and a check that
        # disappears under an optimisation flag is not a check. This module
        # already reports its own defects this way.
        raise RuntimeError(
            f"the example endpoint's record shape is no longer the `items` of "
            f"{indexed_ref!r} (its `records.ref` is "
            f"{response['records']['ref']!r}); probes would declare their "
            "fields on a node the read contract does not resolve to. Reach it "
            "the way `resolve_read_record_schema` does, on the live document."
        )
    return node


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


def _p_recorded_sample_contradicting_its_node() -> list[dict]:
    """The sevdesk shape: a field the provider documents as a boolean and
    illustrates with the string "0"."""
    doc = _read_endpoint()
    record = _record_node(doc)
    record["properties"]["small_settlement"] = {
        "type": ["boolean", "null"], "native_type": "boolean",
        "arrow_type": "Boolean", "examples": ["0"],
    }
    return _validate(doc)


def _p_recorded_sample_zone_contradiction() -> list[dict]:
    doc = _read_endpoint()
    record = _record_node(doc)
    record["properties"]["updated_at"] = {
        "type": "string", "native_type": "date-time",
        "arrow_type": "Timestamp(MICROSECOND, UTC)",
        "examples": ["2024-01-02T03:04:05"],
    }
    return _validate(doc)


def _p_recorded_sample_agreeing_with_its_node() -> list[dict]:
    doc = _read_endpoint()
    record = _record_node(doc)
    record["properties"]["updated_at"] = {
        "type": "string", "native_type": "date-time",
        "arrow_type": "Timestamp(MICROSECOND, UTC)",
        "examples": ["2024-01-02T03:04:05Z"],
    }
    return _validate(doc)


def _p_no_recorded_sample_is_no_obligation() -> list[dict]:
    """A field with no sample is graded on nothing — recording one is never
    required."""
    doc = _read_endpoint()
    record = _record_node(doc)
    record["properties"]["updated_at"] = {
        "type": "string", "native_type": "date-time",
        "arrow_type": "Timestamp(MICROSECOND, UTC)",
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

def _p_connector_default_header_scope_checked() -> list[dict]:
    # `transport_defaults` rather than a transport entry: it is the merge layer
    # that reaches every request, and it was the last of the resolved header
    # maps to be covered — so this is the probe that fails first if the
    # coverage claim in the blind-spot block stops being true.
    def mutate(doc: dict) -> dict:
        doc.setdefault("transport_defaults", {}).setdefault("headers", {})["X-Bad"] = {
            "ref": "garbage_nonsense"
        }
        return doc
    return _staged_connector(mutate, API_EXAMPLE)


def _p_connector_literal_field_unchecked() -> list[dict]:
    # The other half of the same sentence: a field no runtime resolves takes a
    # `${...}` without complaint, because scoping it would not make it resolve.
    def mutate(doc: dict) -> dict:
        _first_transport(doc)["rate_limit"] = {
            "max_requests": 10, "time_window_seconds": "${garbage_nonsense}",
        }
        return doc
    return _staged_connector(mutate, API_EXAMPLE)


def _p_connector_ref_tail_unchecked() -> list[dict]:
    # The leading scope is contract-checked; what follows it is not. This is
    # the connector-side twin of `scope-tail-unchecked`, and it is what keeps
    # the phase-resolvability claim honest now that the scope itself is
    # refused — the ref names a real scope and an output nothing produces.
    def mutate(doc: dict) -> dict:
        _first_transport(doc).setdefault("headers", {})["X-Weird"] = {
            "ref": "connection.discovered.nothing_produces_this"
        }
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


def _p_sql_capabilities_shape_checked() -> list[dict]:
    """The half `spec-sql-write-path.md` claims the validator DOES do: the
    `sql_capabilities` value sets are closed, so a bogus `merge_form` errors."""
    def mutate(doc: dict) -> dict:
        doc["sql_capabilities"]["merge_form"] = "not_a_real_form"
        return doc
    return _staged_connector(mutate, DB_EXAMPLE)


def _p_sql_capabilities_pairing_unchecked() -> list[dict]:
    """The half it claims the validator does NOT do. A declared `bulk_load`
    mechanism obliges the package to implement the `bulk_land` hook, but the
    validator only ever sees JSON — `connector.py` is the CDK conformance kit's
    surface — so the declaration↔hook pairing cannot be checked here. This is
    what "validates cleanly and is refused at handshake" means, and it is why
    the sentence has to warn rather than rely on the validator."""
    def mutate(doc: dict) -> dict:
        doc["sql_capabilities"]["bulk_load"] = {"sqlalchemy": "copy_from"}
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


def _staged_api_endpoint(endpoint: dict) -> list[dict]:
    """Validate the example API connector with `endpoint` as its only endpoint.

    Coverage is connector-anchored — `check_coverage` reads the endpoint files
    from disk beside `connector.json` — so a probe about type-map coverage has
    to enter through the connector, never through the endpoint document alone.
    """
    doc = _example_body(API_EXAMPLE)
    with tempfile.TemporaryDirectory() as tmp:
        definition = Path(tmp) / "definition"
        (definition / "endpoints").mkdir(parents=True)
        (definition / "connector.json").write_text(json.dumps(doc))
        shutil.copy(API_EXAMPLE / "type-map-read.json", definition / "type-map-read.json")
        (definition / "endpoints" / "v1__items.json").write_text(json.dumps(endpoint))
        return _validate(doc, doc_path=definition / "connector.json")


def _p_endpoint_pair_unresolved() -> list[dict]:
    endpoint = _read_endpoint()
    record = _record_node(endpoint)
    record["properties"]["mystery"] = {
        "type": "string", "native_type": "MYSTERY_TYPE", "arrow_type": "Utf8",
    }
    return _staged_api_endpoint(endpoint)


def _p_write_input_pair_unresolved() -> list[dict]:
    """The write mode `_endpoint_with_write` already builds, with its one
    input field annotated against a native no read map carries.

    The pair of probes differs only in that annotation, which is the whole of
    what the claim in `endpoint-creator.md` turns on.
    """
    endpoint = _endpoint_with_write()
    node = endpoint["operations"]["write"]["insert"]["input"]["schema"]["properties"]["id"]
    node["native_type"] = "MYSTERY_WRITE_TYPE"
    node["arrow_type"] = "Utf8"
    return _staged_api_endpoint(endpoint)


def _p_write_input_unannotated() -> list[dict]:
    return _staged_api_endpoint(_endpoint_with_write())


def _p_param_key_provider_spelling() -> list[dict]:
    """A path param keyed the way the provider spells it, bound to a
    contract-form placeholder through `from_param`."""
    endpoint = _read_endpoint()
    read = endpoint["operations"]["read"]
    read["request"]["path"] = "/v1/items/{object_id}"
    read["request"]["path_params"] = {"object_id": {"from_param": "objectId"}}
    read["params"]["objectId"] = {
        "in": "path", "type": "string", "required": True,
        "default": {"ref": "connection.selections.object_id"},
    }
    return _staged_api_endpoint(endpoint)


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


def _p_pagination_limit_literal() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["pagination"]["limit"]["default"] = {"literal": 50}
    return _validate(doc)


def _p_pagination_page_step_literal() -> list[dict]:
    doc = _read_endpoint()
    doc["operations"]["read"]["pagination"]["page"]["increment_by"] = {"literal": 1}
    return _validate(doc)


def _offset_paginated_endpoint(increment_by: Any) -> dict:
    """The example endpoint, re-cut onto the offset family.

    The shipped example paginates by page, so the offset slot has to be staged
    to be probed at all — and the prose names it among the slots that refuse a
    literal, so a slot no probe reaches is a claim nobody measures. The `page`
    param is renamed rather than added: pagination may only drive a declared
    param, and an undeclared one would raise an unrelated finding that a probe
    asserting an outcome must not be carrying.
    """
    doc = _read_endpoint()
    read = doc["operations"]["read"]
    read["params"]["offset"] = read["params"].pop("page")
    read["request"]["query"]["offset"] = {"from_param": "offset"}
    del read["request"]["query"]["page"]
    pagination = read["pagination"]
    pagination["type"] = "offset"
    del pagination["page"]
    pagination["offset"] = {
        "param": "offset", "initial": 0, "increment_by": increment_by,
    }
    return doc


def _p_pagination_offset_step_literal() -> list[dict]:
    return _validate(_offset_paginated_endpoint({"literal": 50}))


def _p_pagination_offset_step_bare() -> list[dict]:
    # The same document with the spelling the prose recommends. Without it the
    # rejection above could come from the staging rather than from the literal
    # form, and the probe would keep passing after the exclusion was dropped.
    return _validate(_offset_paginated_endpoint(50))


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


def _p_pipeline_schema_pinned_url() -> list[dict]:
    # The connection/stream/pipeline families declare `$schema` as a const, so
    # the pinned spelling of the SAME family is refused exactly like a foreign
    # host is on an endpoint (`endpoint-schema-host-locked`). `schema-hosts.md`
    # tells authors there is no pinned form to reach for; this is why.
    doc = json.loads(PIPELINE_EXAMPLE.read_text())
    doc["$schema"] = "https://schemas.analitiq.ai/pipeline/1.0.0.json"
    return _validate(doc)


def _p_pipeline_cron_inner_spec() -> list[dict]:
    # RULE-PIPE-009 carries no `validator`: the contract patterns the `cron(…)`
    # wrapper and stops, so a schedule that can never fire validates clean and
    # the pipeline looks merely idle at the scheduler.
    doc = json.loads(PIPELINE_EXAMPLE.read_text())
    doc["schedule"] = {
        "type": "cron",
        "cron_expression": "cron(not a spec any scheduler accepts)",
        "timezone": doc["schedule"]["timezone"],
    }
    return _validate(doc)


def _p_pipeline_copied_default() -> list[dict]:
    # RULE-SHRD-004 carries no `validator` either, and cannot: a copied default
    # is byte-identical to a value the user chose. Read off the model so the
    # probe restates no default of its own.
    from analitiq.contracts.pipelines.config import PipelineInput, Schedule

    doc = json.loads(PIPELINE_EXAMPLE.read_text())
    doc["status"] = PipelineInput.model_fields["status"].default
    doc["schedule"] = {
        **doc["schedule"],
        "type": Schedule.model_fields["type"].default,
        "timezone": Schedule.model_fields["timezone"].default,
    }
    return _validate(doc)


def _staged_pipeline_bundle(
    status: str,
    mutate_stream: Callable[[dict], None] | None = None,
    publish_connector_endpoints: bool = False,
) -> list[dict]:
    """The shipped examples laid out on disk as a bundle, graded at `status`.

    Runnability is the one verdict the pipeline plugin asks for conditionally:
    `scripts/validate.py` passes `require_runnable` off the pipeline's own
    status, so measuring the claim means calling the adapter the agent runs,
    not `validate_document`. Everything but the wiring comes from the bundled
    examples — the pipeline's `streams` list and the destination ref are
    repointed because no example pair ships pre-stitched.

    `mutate_stream` runs after that repointing, so a probe can break exactly one
    cross-document agreement in a bundle that is otherwise the shipped set.

    `publish_connector_endpoints` writes the source connector's endpoint set
    under `connectors/<slug>/definition/endpoints/`, which is the only thing
    that makes the plugin's `connector-endpoint-ref` check verify rather than
    skip: it omits a connector whose endpoints are absent, because an unknown
    set must not read as "no endpoints". The id published is the one the stream
    example's source already names, read back rather than typed, so a probe
    that mutates the ref still has a real endpoint to be measured against.
    """
    pipeline = json.loads(PIPELINE_EXAMPLE.read_text())
    stream = json.loads(STREAM_EXAMPLE.read_text())
    endpoint = json.loads(ENDPOINT_EXAMPLE.read_text())
    source = json.loads(SOURCE_CONNECTION_EXAMPLE.read_text())
    destination = json.loads(DESTINATION_CONNECTION_EXAMPLE.read_text())

    pipeline["status"] = status
    pipeline["streams"] = [stream["stream_id"]]
    destination_ref = stream["destinations"][0]["endpoint_ref"]
    destination_ref["endpoint_id"] = endpoint["endpoint_id"]
    destination_ref["database_object"] = endpoint["database_object"]
    published = stream["source"]["endpoint_ref"]["endpoint_id"]
    if mutate_stream is not None:
        mutate_stream(stream)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pipelines" / "p" / "streams").mkdir(parents=True)
        document = root / "pipelines" / "p" / "pipeline.json"
        document.write_text(json.dumps(pipeline))
        (root / "pipelines" / "p" / "streams" / "s.json").write_text(json.dumps(stream))
        for connection in (source, destination):
            slug = connection["connector_id"]
            (root / "connections" / slug).mkdir(parents=True)
            (root / "connections" / slug / "connection.json").write_text(json.dumps(connection))
            definition = root / "connectors" / slug / "definition"
            definition.mkdir(parents=True)
            (definition / "connector.json").write_text(json.dumps({"connector_id": slug}))
        endpoints = (root / "connections" / destination["connector_id"]
                     / "definition" / "endpoints")
        endpoints.mkdir(parents=True)
        (endpoints / f"{endpoint['endpoint_id']}.json").write_text(json.dumps(endpoint))
        if publish_connector_endpoints:
            published_dir = (root / "connectors" / source["connector_id"]
                             / "definition" / "endpoints")
            published_dir.mkdir(parents=True)
            (published_dir / f"{published}.json").write_text(
                json.dumps({"endpoint_id": published}))
        return _pipeline_adapter().diagnostics_for(
            "pipeline", document, bundle_root=root)["findings"]


def _p_pipeline_draft_runnability() -> list[dict]:
    return _staged_pipeline_bundle("draft")


def _p_pipeline_active_runnability() -> list[dict]:
    return _staged_pipeline_bundle("active")


# --- stream mutations, each breaking one cross-document agreement -----------

def _wrong_connection_role(stream: dict) -> None:
    """Read from the connection the pipeline declares a destination.

    Both connections are real and both are named by the pipeline; only the role
    is swapped, which is the shape neither document can settle alone.
    """
    stream["source"]["endpoint_ref"]["connection_id"] = (
        stream["destinations"][0]["endpoint_ref"]["connection_id"])


def _unbacked_connection_endpoint(stream: dict) -> None:
    """Point the destination at a database object no endpoint document declares."""
    from analitiq.contracts.endpoint_identity import derive_db_endpoint_id

    ref = stream["destinations"][0]["endpoint_ref"]
    obj = {**ref["database_object"], "name": "no_such_table_anywhere"}
    ref["database_object"] = obj
    # Derived, never typed: a handle inconsistent with its object is refused by
    # the contract on the stream document alone, and the probe would then be
    # measuring that rejection instead of the missing endpoint document.
    ref["endpoint_id"] = derive_db_endpoint_id(
        obj.get("catalog"), obj.get("schema"), obj["name"])


def _near_miss_connector_endpoint(stream: dict) -> None:
    """Misspell the connector endpoint the source names, by one character.

    Derived from the id the connector publishes rather than written out, so the
    probe states neither spelling and the closest-match suggestion has
    something to find.
    """
    ref = stream["source"]["endpoint_ref"]
    ref["endpoint_id"] = ref["endpoint_id"][:-1]


def _p_stream_cross_document_unchecked_alone() -> list[dict]:
    """One stream document carrying both defects, graded as a stream document.

    The entity the agent passes for a stream never receives a bundle root, so
    this is the whole of what a stream validation can see.
    """
    stream = json.loads(STREAM_EXAMPLE.read_text())
    _wrong_connection_role(stream)
    _unbacked_connection_endpoint(stream)
    with tempfile.TemporaryDirectory() as tmp:
        document = Path(tmp) / "stream.json"
        document.write_text(json.dumps(stream))
        return _pipeline_adapter().diagnostics_for("stream", document)["findings"]


def _p_stream_connection_role_bundle() -> list[dict]:
    return _staged_pipeline_bundle("draft", mutate_stream=_wrong_connection_role)


def _p_stream_connection_endpoint_bundle() -> list[dict]:
    return _staged_pipeline_bundle("draft", mutate_stream=_unbacked_connection_endpoint)


def _p_connector_endpoint_ref_warned() -> list[dict]:
    return _staged_pipeline_bundle(
        "draft", mutate_stream=_near_miss_connector_endpoint,
        publish_connector_endpoints=True)


def _p_connector_endpoint_ref_skipped() -> list[dict]:
    return _staged_pipeline_bundle("draft", mutate_stream=_near_miss_connector_endpoint)


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
    # recorded wire samples
    Probe("recorded-sample-type-contradiction", "error",
          _p_recorded_sample_contradicting_its_node,
          message_re=r"does not satisfy the declaration it sits on"),
    Probe("recorded-sample-zone-contradiction", "error",
          _p_recorded_sample_zone_contradiction,
          message_re=r"RULE-ENDP-063"),
    Probe("recorded-sample-agreeing", "clean",
          _p_recorded_sample_agreeing_with_its_node),
    Probe("no-recorded-sample-no-obligation", "clean",
          _p_no_recorded_sample_is_no_obligation),
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
    Probe("connector-default-header-scope-checked", "error",
          _p_connector_default_header_scope_checked,
          message_re=r"garbage_nonsense"),
    Probe("connector-literal-field-unchecked", "clean",
          _p_connector_literal_field_unchecked,
          forbid_re=r"garbage_nonsense"),
    Probe("connector-ref-tail-unchecked", "clean", _p_connector_ref_tail_unchecked,
          forbid_re=r"(?i)nothing_produces_this|discovered"),
    # The closed-value-set error names the ALLOWED values, not the field, so the
    # pattern pins the vocabulary rather than the key — which is the half of the
    # sentence that matters ("closed value sets").
    Probe("sql-capabilities-shape-checked", "error", _p_sql_capabilities_shape_checked,
          message_re=r"insert_on_conflict.*insert_on_duplicate_key"),
    Probe("sql-capabilities-pairing-unchecked", "clean", _p_sql_capabilities_pairing_unchecked,
          forbid_re=r"(?i)bulk_l(oad|and)|connector\.py|conformance"),
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
    Probe("write-input-pair-unresolved-through-read-map", "error",
          _p_write_input_pair_unresolved,
          message_re=r"native_type 'MYSTERY_WRITE_TYPE'"),
    # `forbid_re` rather than expect="clean" alone: the sentence claims the
    # unannotated node is resolved against nothing, and a warning-tier report
    # naming the read map would falsify it without producing an error.
    Probe("write-input-unannotated-uncovered", "clean", _p_write_input_unannotated,
          forbid_re=r"type-map-read\.json"),
    # The asymmetry spec-request-binding.md teaches: the placeholder takes the
    # contract's form while the param it binds keeps the provider's spelling.
    # `forbid_re` covers the severity `expect="clean"` does not — a
    # warning-tier check landing on the `params` key would falsify the sentence
    # while leaving the document error-free.
    Probe("param-key-keeps-provider-spelling", "clean", _p_param_key_provider_spelling,
          forbid_re=r"(?i)objectId"),
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
          message_re=r"greater than or equal to 1"),
    Probe("pagination-limit-literal-rejected", "error", _p_pagination_limit_literal,
          message_re=r"(?i)tag 'literal'"),
    Probe("pagination-page-step-literal-rejected", "error", _p_pagination_page_step_literal,
          message_re=r"(?i)tag 'literal'"),
    Probe("pagination-offset-step-literal-rejected", "error", _p_pagination_offset_step_literal,
          message_re=r"(?i)tag 'literal'"),
    Probe("pagination-offset-step-bare-accepted", "clean", _p_pagination_offset_step_bare),
    # connection / pipeline / stream
    Probe("connection-sidecar-name-unconstrained", "clean", _p_connection_sidecar_name,
          forbid_re=r"(?i)sidecar"),
    Probe("pipeline-active-empty-streams-rejected", "error", _p_pipeline_active_empty,
          message_re=r"at least one stream"),
    Probe("pipeline-schema-pinned-url-rejected", "error", _p_pipeline_schema_pinned_url,
          message_re=r"schemas\.analitiq\.ai/pipeline/latest\.json"),
    # The pair: the SAME staged bundle, graded at each status. Without the
    # active half, "runnability is enforced once the pipeline is active" would
    # rest on nothing — a build that stopped asking for runnability entirely
    # keeps the draft probe green.
    Probe("pipeline-draft-runnability-unchecked", "clean", _p_pipeline_draft_runnability,
          forbid_re=r"(?i)runnab"),
    Probe("pipeline-active-runnability-enforced", "error", _p_pipeline_active_runnability,
          message_re=r"at least one runnable stream"),
    # Two rules the registry carries with no `validator`, so the prose warning
    # that a clean run proves nothing about them has something behind it.
    Probe("pipeline-cron-inner-spec-unchecked", "clean", _p_pipeline_cron_inner_spec,
          forbid_re=r"(?i)cron"),
    Probe("pipeline-copied-default-unchecked", "clean", _p_pipeline_copied_default,
          forbid_re=r"(?i)default"),
    # The cross-document trio: one stream document breaking both agreements is
    # graded alone and found clean, then each agreement is graded again inside
    # the assembled bundle. Without the first, "only with --bundle-root" rests
    # on nothing; without the other two, so does "checked" — and because all
    # three drive the same mutations, a mutation that stopped biting fails the
    # bundle halves rather than leaving the clean half passing over an
    # untouched document.
    Probe("stream-cross-document-unchecked-alone", "clean",
          _p_stream_cross_document_unchecked_alone,
          forbid_re=r"connections\.source|bundled endpoint document"),
    Probe("stream-connection-role-bundle-rejected", "error",
          _p_stream_connection_role_bundle,
          message_re=r"must match the pipeline's connections\.source"),
    Probe("stream-connection-endpoint-bundle-rejected", "error",
          _p_stream_connection_endpoint_bundle,
          message_re=r"no matching bundled endpoint document"),
    # The pair behind the endpoint-alignment warning: the same misspelled ref,
    # with and without the connector's endpoints on disk. expect="clean" is
    # what makes it a warning rather than a rejection, and require_re holds the
    # suggestion in existence — an alignment the orchestrator offers a user is
    # only offerable while the finding carries it.
    Probe("connector-endpoint-ref-warned", "clean", _p_connector_endpoint_ref_warned,
          require_re=r"Did you mean"),
    Probe("connector-endpoint-ref-skipped-undownloaded", "clean",
          _p_connector_endpoint_ref_skipped,
          forbid_re=r"(?i)did you mean|published endpoints"),
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
        "> connect. What a ref must name is the scope it starts from; whether\n"
        "> anything ever puts a value at the rest of the path is not knowable\n"
        "> from the document, on either the connector or the endpoint side. Walk\n"
        "> the phases by hand.",
        ("connector-ref-tail-unchecked", "scope-tail-unchecked"),
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
    "path-resolved": ("error", "resolved against `response.schema`, must declare a type (RULE-ENDP-023)"),
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
        "resolved, and must land on an array node, RULE-ENDP-012. Referencing",
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
    "write-request-slot-response-ref", "connector-ref-tail-unchecked",
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
        "  on its leading token only — so a `connection.discovered.*` ref with no",
        "  post-auth output that produces it validates clean, on either document.",
        "- **A connector field nothing resolves is not scope-checked either.** The",
        "  leading-token check covers the fields a runtime actually resolves — the",
        "  transports, the default header map, the auth exchange, the post-auth",
        "  request, the DSN bindings. A `${...}` in a field consumed literally, such",
        "  as a rate-limit window or a SQLAlchemy `options` entry, is refused by",
        "  nobody and substituted by nobody: it reaches the driver as written.",
        "- **TLS `ssl_mode` ↔ `ssl_ca_certificate` consistency is not checked.**",
    ]) + "\n"


_BLIND_SPOT_PROBES: tuple[str, ...] = (
    "connector-function-name-unchecked", "endpoint-function-name-unchecked",
    "read-body-path-typo", "read-metadata-undeclared-key",
    "write-metadata-undeclared-key", "read-records-tail-unchecked",
    "read-headers-tail-unchecked", "write-body-path-typo-unresolved",
    "scope-tail-unchecked", "connector-ref-tail-unchecked", "tls-coherence-unchecked",
    "connector-default-header-scope-checked", "connector-literal-field-unchecked",
)


# Natives chosen to exhibit each thing the normalization does — surrounding
# whitespace, an internal run, and casing — so an author reading the rendered
# pairs can predict how their own native will be spelled at match time. The
# right column is never typed: it is what the contract's own function returns.
_NORMALIZATION_SAMPLES: tuple[str, ...] = (
    " varchar ",
    "CHARACTER  VARYING",
    "numeric(10, 2)",
)


def render_native_normalization() -> str:
    """The read-side normalization, shown as worked pairs off the live function.

    The steps themselves are `analitiq.contracts.type_map.normalize_native_type`
    and describing them in prose is a copy that goes stale silently — an author
    who spells a `regex` literal against the wrong casing writes a rule that can
    never match, and nothing rejects it. Rendering what the function actually
    returns keeps the guidance and drops the copy.
    """
    from analitiq.contracts.type_map import normalize_native_type

    rows = [
        f"| `{sample}` | `{normalize_native_type(sample)}` |"
        for sample in _NORMALIZATION_SAMPLES
    ]
    return "\n".join([
        "| Authored or probed native | Spelled this way at match time |",
        "|---|---|",
        *rows,
    ]) + "\n"


def _render_claim(claim_id: str) -> str:
    return CLAIMS_BY_ID[claim_id].text + "\n"


@functools.lru_cache(maxsize=None)
def _release_table():
    """The connector release-table module, imported by path (cached).

    It owns the bump-policy vocabulary (the release table, the classifier's
    bump table, the `DriftVerdict` envelope) as data. Its renderers register
    here because this script owns every generated block in the connector
    plugin's tree — `render_text` fails loud on an id it does not know, so a
    separate renderer could not coexist. Unlike every other block in this
    registry, these stand on that data, not on probes: the policy is the
    plugin's own, not validator behavior.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_connector_release_table", REPO_ROOT / "scripts" / "connector_release_table.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_DEDICATED_RENDERERS: dict[str, Callable[[], str]] = {
    "scope-guarantees": render_scope_guarantees,
    "validator-blind-spots": render_validator_blind_spots,
    "native-normalization": render_native_normalization,
}
_CLAIM_RENDERERS: dict[str, Callable[[], str]] = {
    f"claim:{c.id}": (lambda cid=c.id: _render_claim(cid)) for c in CLAIMS
}
RENDERERS: dict[str, Callable[[], str]] = {
    **_DEDICATED_RENDERERS, **_CLAIM_RENDERERS, **_release_table().RENDERERS,
}
# A colliding id would let one registry silently shadow another's renderer —
# and worse, `block_probe_ids` would then report the shadowed block probeless.
if len(RENDERERS) != len(_DEDICATED_RENDERERS) + len(_CLAIM_RENDERERS) + len(
        _release_table().RENDERERS):
    raise RuntimeError("block-id collision between renderer registries")


def block_probe_ids(block_id: str) -> set[str]:
    """The probe ids one block stands on."""
    if block_id in _release_table().RENDERERS:
        return set()  # data-backed, not probe-backed — see `_release_table`
    if block_id == "native-normalization":
        # Contract-backed: the rows are what `normalize_native_type` returns,
        # so the function is the pin. No validator behavior is asserted.
        return set()
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

    # A repo-root generator now, like this one: it puts its own directory on
    # the path at import, so nothing here has to arrange for its imports.
    spec = importlib.util.spec_from_file_location(
        "_pipeline_gen_docs", REPO_ROOT / "scripts" / "gen_pipeline_docs.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=None)
def _pipeline_adapter():
    """The pipeline plugin's validator adapter, imported by path (cached).

    One claim in that plugin's prose is about the adapter rather than about the
    published validator: `require_runnable` is chosen from the pipeline's own
    status in `scripts/validate.py`, so a probe that called
    `validate_pipeline_bundle` directly would measure the wrong side of the
    sentence. Import is side-effect-free — `_bootstrap`'s venv build and
    re-exec only fire from the adapter's `main()`.
    """
    import importlib.util

    scripts_dir = str(PIPELINE_PLUGIN / "scripts")
    spec = importlib.util.spec_from_file_location(
        "_pipeline_validate_adapter", PIPELINE_PLUGIN / "scripts" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, scripts_dir)  # validate.py imports _bootstrap
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts_dir)
    return module


def _known_block_ids_for(path: Path) -> set[str]:
    """The block ids a real renderer stands behind, for this doc's tree.

    These are the ids `malformed_marker_docs` accepts. A marker pair with any
    other id is prose that LOOKS machine-pinned while nothing renders or checks
    it — the exact artifact this registry exists to prevent — so it is reported
    rather than treated as a block.
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
    pair would otherwise be checked by nobody.
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
    generated blocks belong to `gen_pipeline_docs.py`; this renderer
    never touches that tree.
    """
    return sorted(
        p for p in CONNECTOR_PLUGIN.rglob("*.md")
        if "<!-- BEGIN GENERATED:" in p.read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# Probe fences
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"<!--\s*PROBE:\s*(?P<ids>[a-z0-9-]+(?:\s*,\s*[a-z0-9-]+)*)\s*-->")


def _fence_docs() -> list[Path]:
    """Every markdown document a probe fence could appear in.

    No exemptions. The old prose scan had two — a plugin's release-please
    `CHANGELOG.md`, and `rules.md` while its first line still declared
    itself generated — because both are prose nobody hand-writes claims into.
    Fence bookkeeping asks a different question, and for it those exemptions
    are not a narrowing but a blind spot: a fence naming a deleted probe goes
    unreported in exactly the files nobody rereads.

    `contributing/` is scanned for the same reason. A plugin's contributor
    guide states validator behaviour as readily as the prose that ships, and
    living outside the artifact does not make the claim any less rot-prone.
    """
    return sorted([*PLUGINS_ROOT.rglob("*.md"), *CONTRIBUTING_ROOT.rglob("*.md")])


def dangling_fence_ids() -> list[str]:
    """Probe fences naming an id no probe defines.

    All that survives of the old prose scan, and the only part of it a
    mechanism could ever decide: a fence either names a registered probe or it
    does not. Whether a SENTENCE asserts validator behaviour is a judgment, and
    judgments live in `.claude/rules/guards.md`.
    """
    return [
        f"{path.relative_to(REPO_ROOT).as_posix()}: fence names unknown probe "
        f"{fence_id!r}"
        for path in _fence_docs()
        for fence in _FENCE_RE.finditer(path.read_text(encoding="utf-8"))
        for fence_id in re.split(r"\s*,\s*", fence.group("ids"))
        if fence_id not in PROBES_BY_ID
    ]


def fence_probe_ids() -> set[str]:
    ids: set[str] = set()
    for path in _fence_docs():
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
    dangling = dangling_fence_ids()
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
    if dangling:
        problems.append("dangling probe fences:\n  " + "\n  ".join(dangling))
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
        print(f"{len(PROBES)} probes hold; {len(docs)} document(s) in sync; every fence resolves")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
