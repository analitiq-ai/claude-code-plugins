"""Fixture corpus for the path-free document-set API (`analitiq.validator
.document_set`). A case exercising an entry point that raises
`NotImplementedError` is `xfail(strict=True)`; every other case carries no
marker. An implementation turns such a case from `xfail` to passing by
replacing the stub body it exercises and removing that case's marker;
`strict=True` means a case that starts passing while its marker is still on it
fails the suite, so a marker can never survive its own fix by accident.

Two corpora already committed for the path-based routes are reused here
rather than re-authored: `packages/validator/tests/corpus/` (a connector
package) and `tests/pipeline_builder/test_validate.py`'s `_build_bundle`
layout (a pipeline bundle) — both are content this suite already keeps
model-valid, so the document-set versions built from them are testing the
document-set mechanism, not guessing at contract shapes.

The fixtures below build *parsed* documents, because the equivalence cases
also write them to disk and hand them to the path-based route. A request
carries file text, so `_package_request` / `_document_request` serialize at the
call. Nothing here covers a malformed argument — a bad key, a value that is
not text, a key that is also a directory, an `entity` outside the vocabulary.
Those are refused by the request models at construction and belong to the
contract package's own model tests; a case asserting one of them produces a
*finding* would contradict the gate. `bytes` and `bytearray` holding UTF-8 pass
that gate — pydantic decodes them in lax mode, byte-order mark included — and
pinning that coercion belongs to the model tests too.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL
from analitiq.contracts.validation_requests import (
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
)

CORPUS = Path(__file__).resolve().parent / "corpus"

_PLUGIN_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "plugins" / "analitiq-pipeline-builder" / "scripts"
)
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))


def _xfail(fn_name: str):
    return pytest.mark.xfail(
        strict=True, raises=NotImplementedError,
        reason=f"{fn_name} raises NotImplementedError (analitiq.validator.document_set)")


def _package_request(documents: dict) -> ValidatePackageRequest:
    """A package request over `documents`, each serialized to the file text a
    request actually carries."""
    return ValidatePackageRequest(
        documents={key: json.dumps(doc) for key, doc in documents.items()})


def _expected_envelope(validator, findings: list) -> dict:
    """The envelope a route must answer with for `findings`, reduced the one
    way `finding_costs_a_pass` defines rather than by a second predicate that
    can drift from it."""
    return {"passed": not any(validator.finding_costs_a_pass(f) for f in findings),
            "findings": findings}


def _document_request(document, entity: str) -> ValidateSingleDocumentRequest:
    """A single-document request over one parsed document and the published
    schema name its sender declares it is written against."""
    return ValidateSingleDocumentRequest(document=json.dumps(document), entity=entity)


# ---------------------------------------------------------------------------
# Finding — drift guard against the keys `analitiq.validator.finding` actually
# produces (no marker: a type-contract fact). `finding()`'s own
# docstring is the source: `rule` only when given, `severity` only for a
# `fail` kind, everything else unconditional.
# ---------------------------------------------------------------------------

def test_finding_matches_the_keys_finding_builder_produces(validator):
    from typing import get_args, get_type_hints

    from analitiq.validator.document_set import Finding

    from analitiq.validator._core import _KINDS

    # Every call `finding()` admits — each kind, with no rule and with rules of
    # each severity a `fail` finding can report — rather than sampled calls: a
    # key set only on a branch no sample visits would be invisible to both
    # assertions below.
    produced = [validator.finding(rule=rule, message_id="m", kind=kind, path="p", message="msg")
                for kind in _KINDS for rule in (None, "RULE-PKG-030", "RULE-CTOR-043")]
    possible_keys = set().union(*(set(f) for f in produced))
    always_present = set.intersection(*(set(f) for f in produced))
    with_rule_and_severity = validator.finding(
        rule="RULE-PKG-030", message_id="m", kind="fail", path="p", message="msg")
    hints = get_type_hints(Finding)
    # Set equality with no carve-out: every key this TypedDict declares is one
    # `finding()` can produce, and every key `finding()` can produce is
    # declared. A key added to either side alone fails here.
    assert set(hints) == possible_keys
    # Required vs. optional tracks what those calls agreed on: a key every one
    # of them carries is one `finding()` always sets; a key only some carry
    # (`rule`, `severity`) is conditional.
    assert Finding.__required_keys__ == always_present
    assert Finding.__optional_keys__ == possible_keys - always_present

    # `kind`: pinned directly against `finding()`'s own vocabulary constant,
    # not just against the members this test happens to try — a member
    # landing in one and not the other fails here rather than staying invisible.
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
# The entry-point signatures — the surface this module exists to declare (not
# marker: a type-contract fact). The request models are imported
# under `TYPE_CHECKING` and no type checker runs over this repo, so without
# this case a misspelled model name, a deferred import of a module that does
# not exist, or an annotation naming the other request model reaches a release
# unnoticed — as does a parameter reappearing beside `request`.
# ---------------------------------------------------------------------------

def test_entry_points_are_annotated_with_their_request_models(validator):
    import ast
    import importlib
    import inspect
    from typing import get_type_hints

    document_set = validator.document_set
    # The namespace is built by executing the module's OWN deferred imports,
    # never by importing the request models here: a namespace this test chose
    # would resolve the annotation strings whatever that block says, leaving a
    # wrong module path or a deleted import green.
    source = Path(document_set.__file__).read_text(encoding="utf-8")
    deferred = [statement
                for node in ast.parse(source).body
                if isinstance(node, ast.If)
                and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                for statement in node.body if isinstance(statement, ast.ImportFrom)]
    assert deferred, "no `if TYPE_CHECKING:` import resolves these annotations"
    namespace = {}
    for statement in deferred:
        module = importlib.import_module(statement.module)
        for alias in statement.names:
            namespace[alias.asname or alias.name] = getattr(module, alias.name)

    for entry_point, request_model in (
        (document_set.validate_single_document, ValidateSingleDocumentRequest),
        (document_set.validate_connector_package, ValidatePackageRequest),
        (document_set.validate_pipeline_package, ValidatePackageRequest),
    ):
        hints = get_type_hints(entry_point, localns=namespace)
        assert hints["request"] is request_model, entry_point.__name__
        assert hints["return"] is document_set.ValidationEnvelope, entry_point.__name__
        # The kind is carried by the function, so nothing may select between
        # kinds from the argument list: no `schema_url`, `direction`, `probes`,
        # `entity` or package-kind parameter beside `request`.
        assert tuple(inspect.signature(entry_point).parameters) == ("request",), entry_point.__name__


# ---------------------------------------------------------------------------
# Fixtures: a connector package and a pipeline package, each built from
# content this suite already keeps model-valid. Values are parsed documents;
# `_package_request` serializes them into the file text a request carries.
# ---------------------------------------------------------------------------

def _connector_package_documents(*, native="STRING", arrow="Utf8") -> dict:
    """A model-valid, coverage-clean connector package as parsed documents
    keyed by package-relative path:
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
        "type-map.json": _type_map_doc(
            read=[{"match": "exact", "native_type": native, "arrow_type": arrow}]),
        "endpoints/v1__records.json": endpoint,
    }


def _uncovered_endpoint_document(*, endpoint_id="v2__widgets", request_path="/v2/widgets",
                                  native="BOOLEAN", arrow="Boolean") -> dict:
    """A model-valid endpoint declaring a native type `_connector_package_documents`'s
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


def _type_map_doc(**sections) -> dict:
    """A type-map document carrying the given `read` / `write` rule lists."""
    return {"$schema": TYPE_MAP_SCHEMA_URL, **sections}


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
# embedded-connector coverage gap must not make `validate_pipeline_package`
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
_CONNECTOR_WISE_TYPE_MAP = _type_map_doc(
    read=[{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}])
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
_CONNECTOR_PG_TYPE_MAP = _type_map_doc(
    read=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}],
    write=[
        {"match": "exact", "native_type": "bigint", "arrow_type": "Int64"},
        {"match": "regex", "native_type": "TEXT", "arrow_type": ".*"},
    ])
def _pipeline_core_documents() -> dict:
    """The connection, stream, pipeline, and destination-endpoint documents a
    pipeline package carries regardless of what its embedded
    `connectors/` subtree looks like."""
    return {
        "connections/wise/connection.json": _CONN_WISE,
        "connections/postgresql/connection.json": _CONN_PG,
        f"connections/postgresql/definition/endpoints/{_EID}.json": _DB_ENDPOINT,
        "pipelines/p/streams/orders.json": _STREAM,
        "pipelines/p/pipeline.json": _PIPELINE,
    }


def _pipeline_package_documents() -> dict:
    """A model-valid draft pipeline bundle as parsed documents, laid out at the
    same relative paths `_assemble_bundle`
    (`plugins/analitiq-pipeline-builder/scripts/validate.py`) already resolves
    from a filesystem root — so the document-set route and that function's
    on-disk route are given byte-identical content, just supplied two
    different ways. `wise`'s and `postgresql`'s embedded
    `connectors/<slug>/definition/...` subtrees are fully model-valid and
    coverage-clean: this is the fixture the equivalence test uses, so
    validating an embedded subtree as a connector package of its own must
    contribute no findings the path-based route doesn't already produce."""
    return {
        **_pipeline_core_documents(),
        "connectors/wise/definition/connector.json": _CONNECTOR_WISE,
        "connectors/wise/definition/type-map.json": _CONNECTOR_WISE_TYPE_MAP,
        "connectors/wise/definition/endpoints/transfers.json": _WISE_TRANSFERS_ENDPOINT,
        "connectors/postgresql/definition/connector.json": _CONNECTOR_PG,
        "connectors/postgresql/definition/type-map.json": _CONNECTOR_PG_TYPE_MAP,
    }


def _pipeline_package_documents_with_two_findings() -> dict:
    """`_pipeline_package_documents` with one unresolvable connection id on the
    pipeline's own `connections.destinations`, and `wise`'s connection document
    naming a connector no bundled subtree defines — a `connection-ref-unresolved`
    finding against the pipeline document and a `connector-ref-unresolved`
    finding against a connection document.

    The two findings come from two *different* documents deliberately. Findings
    originating in one document are emitted in that document's own internal
    order whatever order the caller built its mapping in, so a route that
    emitted findings in caller-mapping order would still compare equal — the
    order and equivalence comparisons below can only detect that with content
    whose order the mapping genuinely decides."""
    documents = _pipeline_package_documents()
    pipeline = {**documents["pipelines/p/pipeline.json"]}
    pipeline["connections"] = {
        **pipeline["connections"],
        "destinations": [*pipeline["connections"]["destinations"], "missing-connection-a"],
    }
    connection = {**documents["connections/wise/connection.json"],
                  "connector_id": "not-a-bundled-connector"}
    return {**documents,
            "pipelines/p/pipeline.json": pipeline,
            "connections/wise/connection.json": connection}


def _pipeline_package_documents_with_embedded_connectors() -> dict:
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
    RULE-PKG-030 finding proves each embedded subtree is reported on its
    own rather than the walk stopping at the first one."""
    return {
        **_pipeline_core_documents(),
        "connectors/wise/definition/connector.json": _CONNECTOR_WISE,
        "connectors/postgresql/definition/connector.json": _CONNECTOR_PG,
    }


def _write_package(root: Path, documents: dict) -> None:
    for rel, doc in documents.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))


# ---------------------------------------------------------------------------
# validate_single_document — one document, the caller's declared schema name
# checked against detection rather than trusted.
# ---------------------------------------------------------------------------

def test_single_document_wraps_the_path_based_route(validator):
    """A document consistent with its declared `entity` reports exactly what
    `validate_document` reports for it with no path, wrapped in one envelope.
    With no path there are no siblings to read, so a connector's coverage check
    is skipped, and the skip costs the pass."""
    document = json.loads((CORPUS / "valid_connector.json").read_text())
    expected_findings = validator.validate_document(document)
    result = validator.validate_single_document(_document_request(document, "connector"))
    assert json.dumps(result) == json.dumps(_expected_envelope(validator, expected_findings))
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["coverage-check-skipped-no-path"]


def test_declared_entity_that_disagrees_with_the_document_is_a_finding(validator):
    """The caller's declaration is checked, not trusted: a stream document sent
    as a connector is reported, not silently validated as whatever it looks
    like. The same document sent under its real schema name is not."""
    mismatched = validator.validate_single_document(_document_request(_STREAM, "connector"))
    assert mismatched["passed"] is False
    # Exactly one: an implementation that trusted the declaration and validated
    # the stream against the connector model would report a pile of model
    # errors and never the mismatch, which passes an `any(...)` check.
    assert [f["kind"] for f in mismatched["findings"]].count("fail") == 1, mismatched

    matched = validator.validate_single_document(_document_request(_STREAM, "stream"))
    assert not any(f["kind"] == "fail" for f in matched["findings"]), matched


# Each of these rules is valid in one direction only — a regex `arrow_type` a
# read rule may not render, an exact `native_type` placeholder a write rule may
# not leave unclosed — so each section's model reports a rule the other never
# does, and which one fired shows the section a rule was graded as.
_RULES_VALID_IN_ONE_DIRECTION = [
    {"match": "regex", "native_type": "TEXT", "arrow_type": ".*"},
    {"match": "exact", "native_type": "VARCHAR(${", "arrow_type": "Utf8"},
]
_GRADED_ONLY_AS = {"read": "read-regex-arrow-type-invalid", "write": "write-exact-malformed-placeholder"}


@pytest.mark.parametrize("section", ["read", "write"])
def test_type_map_is_graded_as_validate_document_grades_it(validator, section):
    """A type map sent as `type-map` is graded exactly as `validate_document`
    grades it: each rule as the section it sits under."""
    document = _type_map_doc(**{section: _RULES_VALID_IN_ONE_DIRECTION})
    result = validator.validate_single_document(_document_request(document, "type-map"))
    assert result == _expected_envelope(validator, validator.validate_document(document))
    ids = [f["message_id"] for f in result["findings"]]
    for direction, only_that_section_reports in _GRADED_ONLY_AS.items():
        assert (only_that_section_reports in ids) == (direction == section), result


@pytest.mark.parametrize("document,sent_as", [
    (_CONNECTOR_PG_TYPE_MAP, "connector"),
    ({"$schema": f"{_H}/type-map-read/latest.json", "direction": "read",
      "rules": [{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}]}, "type-map"),
], ids=["a map sent as a connector", "a split-shape map"])
def test_a_type_map_entity_mismatch_is_reported(validator, document, sent_as):
    """A map sent under another kind's name is reported, and so is a
    split-shape document: the `$schema` it names is no document schema the
    contract registers, so nothing grades it as a map."""
    result = validator.validate_single_document(_document_request(document, sent_as))
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["entity-mismatch"], result


def test_unparseable_document_text_is_a_finding_not_a_raise(validator):
    """Document *content* is what this API judges, so text the JSON parser
    cannot read comes back as a finding under `unreadable-document`, the
    message id `analitiq.validator._core`'s CLI already mints when it cannot
    read a document off disk — never as a raised error, which is reserved for a
    defect in this package."""
    request = ValidateSingleDocumentRequest(document="{not json", entity="connector")
    result = validator.validate_single_document(request)
    assert result["passed"] is False
    assert any(f["message_id"] == "unreadable-document" for f in result["findings"]), result


def test_text_refused_outside_jsondecodeerror_is_a_finding_not_a_raise(
        validator, text_refused_outside_jsondecodeerror):
    """The parser refuses some text with an exception that is not a
    `JSONDecodeError`. That text is still content a caller sent, so it is
    reported like any other unreadable document rather than raised as though it
    were a defect in this package."""
    result = validator.validate_single_document(ValidateSingleDocumentRequest(
        document=text_refused_outside_jsondecodeerror, entity="connector"))
    assert [f["message_id"] for f in result["findings"]] == ["unreadable-document"], result


# ---------------------------------------------------------------------------
# `_consistent_entities`'s tables restate a vocabulary the contract package
# generates, and nothing else in the validator package reads that owner. Per
# `.claude/rules/no-drift-surfaces.md` a copy is pinned by a test that reads the
# owner, or it is a defect — so the pin below reads `DOCUMENT_SCHEMA_NAMES`
# itself. A kind registering with no name there cannot be declared as an
# `entity` at all, so the vocabulary is what the tables have to track.
# ---------------------------------------------------------------------------

#: A document for every registration a published document-schema name resolves
#: from, built from the fixtures above — a connector missing its `kind` is
#: claimed by a registration of its own. The names are asserted against
#: `DOCUMENT_SCHEMA_NAMES` itself, so a name added or renamed there fails here
#: rather than silently going undetected.
_DOCUMENT_FOR_ENTITY = (
    ("connector", _CONNECTOR_WISE),
    ("connector", {k: v for k, v in _CONNECTOR_WISE.items() if k != "kind"}),
    ("connection", _CONN_WISE),
    ("pipeline", _PIPELINE),
    ("stream", _STREAM),
    ("api-endpoint", _WISE_TRANSFERS_ENDPOINT),
    ("database-endpoint", _DB_ENDPOINT),
    ("type-map", _CONNECTOR_PG_TYPE_MAP),
)


def test_every_published_document_schema_name_is_detected(validator):
    """Each name the contract publishes resolves from a document of that kind,
    and only that name does. A name the contract adds or renames lands here as a
    missing entry, rather than as every document of that kind drawing a spurious
    `entity-mismatch`."""
    from analitiq.contracts.validation_requests import DOCUMENT_SCHEMA_NAMES
    from analitiq.validator.document_set import _consistent_entities

    assert {entity for entity, _ in _DOCUMENT_FOR_ENTITY} == set(DOCUMENT_SCHEMA_NAMES)
    for entity, document in _DOCUMENT_FOR_ENTITY:
        assert _consistent_entities(document) == {entity}, (entity, document)


def test_an_assembled_bundle_resolves_to_no_published_name(validator):
    """A bundle is not a single document and no published schema names one, so
    it is deliberately absent from `_consistent_entities`'s tables. Sent to this
    entry point it is reported as matching no published schema — never
    validated as the `pipeline` its core carries."""
    bundle = {"pipeline": _PIPELINE, "streams": [_STREAM], "connections": {_SRC: _CONN_WISE}}
    result = validator.validate_single_document(_document_request(bundle, "pipeline"))
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"] if f["kind"] == "fail"] == ["entity-mismatch"]


# ---------------------------------------------------------------------------
# The package entry points — each called directly for the kind it names.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_package")
def test_validate_connector_package_validates_its_own_root_shape(validator):
    result = validator.validate_connector_package(
        _package_request(_connector_package_documents()))
    # Findings empty, not merely `passed` — a route emitting a `notApplicable`
    # finding and still reporting a pass would satisfy the weaker assertion.
    assert result == _expected_envelope(validator, []), result


@_xfail("validate_connector_package")
def test_unparseable_document_in_a_package_is_a_finding_not_a_raise(validator):
    """One document's text being unreadable is package *content*, not a
    malformed argument — the request model accepts any text — so the package
    comes back as a failing envelope rather than a raised error."""
    documents = {key: json.dumps(doc)
                 for key, doc in _connector_package_documents().items()}
    documents["endpoints/v2__widgets.json"] = "{not json"
    result = validator.validate_connector_package(
        ValidatePackageRequest(documents=documents))
    assert result["passed"] is False
    # Naming the document, not merely failing: the rest of this package is
    # clean, so a bare `any(kind == "fail")` cannot tell "reported as
    # unreadable" from "reported as something else entirely". The message id is
    # deliberately not pinned — this route may mint its own.
    unreadable = [f for f in result["findings"]
                  if f["kind"] == "fail" and "v2__widgets" in (f["message"] + f["path"])]
    assert unreadable, result


@_xfail("validate_pipeline_package")
def test_validate_pipeline_package_validates_its_own_root_shape(validator):
    result = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents()))
    assert result == _expected_envelope(validator, []), result


@_xfail("validate_pipeline_package")
def test_embedded_connector_subtree_gets_its_own_coverage_findings(validator):
    """`connectors/wise/definition/connector.json` (kind=api) ships no sibling
    type-map or `endpoints/` directory — today's plugin never notices:
    `_assemble_bundle` reads such a subtree's `connector.json` only for its
    `connector_id`, and `_connector_endpoint_sets` reads its endpoint ids only
    for stream-ref resolution. `validate_pipeline_package` calls
    `validate_connector_package`
    on the subtree and reports its OWN coverage findings (RULE-PKG-030 missing
    read map, RULE-PKG-035 missing endpoints/), scoped under the subtree's key
    prefix."""
    result = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents_with_embedded_connectors()))
    # Containment, not a prefix spelling: what is fixed is that the finding is
    # scoped to its subtree, and `rule` is optional on a `Finding`, so neither
    # a leading slash nor a ruleless finding turns this into a failure about
    # something other than what it names.
    scoped = [f for f in result["findings"] if "connectors/wise/" in f["path"]]
    assert any(f.get("rule") == "RULE-PKG-030" for f in scoped), result["findings"]
    assert any(f.get("rule") == "RULE-PKG-035" for f in scoped), result["findings"]
    # Both subtrees: a walk that stopped at whichever it reached first satisfies
    # the assertions for that one alone.
    pg_scoped = [f for f in result["findings"] if "connectors/postgresql/" in f["path"]]
    assert any(f.get("rule") == "RULE-PKG-030" for f in pg_scoped), result["findings"]


# ---------------------------------------------------------------------------
# Deterministic output: findings do not depend on the order the caller happened
# to build its mapping in.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_package")
def test_connector_package_finding_order_is_independent_of_input_order(validator):
    # Two distinct uncovered endpoints, not the clean package: comparing two
    # empty findings lists cannot detect order-sensitivity at all.
    documents = {
        **_connector_package_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        "endpoints/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    forward = validator.validate_connector_package(_package_request(documents))
    assert len(forward["findings"]) >= 2, forward  # non-vacuous: order genuinely matters below
    reversed_documents = dict(reversed(list(documents.items())))
    backward = validator.validate_connector_package(_package_request(reversed_documents))
    assert forward == backward


@_xfail("validate_pipeline_package")
def test_pipeline_package_finding_order_is_independent_of_input_order(validator):
    # The same determinism rule on the other package entry point, over a
    # fixture that reports real findings down both orders.
    documents = _pipeline_package_documents_with_two_findings()
    forward = validator.validate_pipeline_package(_package_request(documents))
    assert len(forward["findings"]) >= 2, forward  # non-vacuous: order genuinely matters below
    reversed_documents = dict(reversed(list(documents.items())))
    backward = validator.validate_pipeline_package(_package_request(reversed_documents))
    assert forward == backward


# ---------------------------------------------------------------------------
# Acceptance — equivalence: the path-based route and the document-set route
# produce byte-identical results for the same content, per package kind.
# ---------------------------------------------------------------------------

@_xfail("validate_connector_package")
def test_connector_package_equivalence_with_the_path_based_route(validator, tmp_path):
    # Two distinct uncovered endpoints, not just a clean package: a route
    # producing zero findings would make "findings order included" vacuous.
    documents = {
        **_connector_package_documents(),
        "endpoints/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        "endpoints/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    _write_package(tmp_path, documents)
    path_based = validator.validate_document(documents["connector.json"], doc_path=tmp_path / "connector.json")
    assert len(path_based) >= 2, path_based  # non-vacuous: order genuinely matters below
    package_based = validator.validate_connector_package(_package_request(documents))
    assert json.dumps(package_based) == json.dumps(_expected_envelope(validator, path_based))


@_xfail("validate_pipeline_package")
def test_pipeline_package_equivalence_with_the_path_based_route(validator, tmp_path):
    import validate as pipeline_adapter  # plugins/analitiq-pipeline-builder/scripts/validate.py

    documents = _pipeline_package_documents_with_two_findings()
    _write_package(tmp_path, documents)
    path_based = pipeline_adapter.diagnostics_for(
        "pipeline", tmp_path / "pipelines" / "p" / "pipeline.json", bundle_root=tmp_path)
    assert len(path_based["findings"]) >= 2, path_based  # non-vacuous: order genuinely matters below
    package_based = validator.validate_pipeline_package(_package_request(documents))
    assert json.dumps(package_based) == json.dumps(path_based)
