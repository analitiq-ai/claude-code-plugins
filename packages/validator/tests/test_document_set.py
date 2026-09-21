"""Fixture corpus for the path-free document-set API (`analitiq.validator
.document_set`).

Two corpora already committed for the path-based routes are reused here
rather than re-authored: `packages/validator/tests/corpus/` (a connector
package) and `tests/pipeline_builder/test_validate.py`'s `_build_bundle`
layout (a pipeline bundle) — both are content this suite already keeps
model-valid, so the document-set versions built from them are testing the
document-set mechanism, not guessing at contract shapes.

A package route is graded by the findings it is expected to produce, written
out here. The connector route additionally compares against the path-based
route over the same files, because one connector document anchors both.

The fixtures below build *parsed* documents, because the connector
equivalence case also writes them to disk and hands them to the path-based
route. A request
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
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

from analitiq.contracts.endpoint_identity import build_database_object, derive_db_endpoint_id
from analitiq.contracts.type_map import TYPE_MAP_SCHEMA_URL
from analitiq.contracts.validation_requests import (
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
)

CORPUS = Path(__file__).resolve().parent / "corpus"

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

#: Keys a connector package carries its documents at, as the published
#: `connector-package` schema locates them.
_CONNECTOR_KEY = "definition/connector.json"
_CONNECTOR_TYPE_MAP = "definition/type-map.json"
_ENDPOINTS = "definition/endpoints"


def _connector_package_documents(*, native="STRING", arrow="Utf8") -> dict:
    """A model-valid, coverage-clean connector package as parsed documents
    keyed by package-relative path: the connector
    (`corpus/valid_connector.json`, kind=api), a sibling read map covering the
    one native/arrow pair below, and one endpoint (`corpus/valid_read.json`)
    declaring it."""
    connector = json.loads((CORPUS / "valid_connector.json").read_text())
    endpoint = json.loads((CORPUS / "valid_read.json").read_text())
    endpoint["operations"]["read"]["response"]["schema"]["items"]["properties"] = {
        "a": {"type": "string", "native_type": native, "arrow_type": arrow},
    }
    return {
        _CONNECTOR_KEY: connector,
        _CONNECTOR_TYPE_MAP: _type_map_doc(
            read=[{"match": "exact", "native_type": native, "arrow_type": arrow}]),
        f"{_ENDPOINTS}/v1__records.json": endpoint,
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
# A connection's directory is named by its `connection_id`.
_WISE_CONNECTION_KEY = f"connections/{_SRC}/connection.json"
_PG_CONNECTION_KEY = f"connections/{_DST}/connection.json"
_PG_ENDPOINT_KEY = f"connections/{_DST}/definition/endpoints/{_EID}.json"
_PG_CONNECTION_TYPE_MAP_KEY = f"connections/{_DST}/definition/type-map.json"
_PG_CREDENTIALS_KEY = f"connections/{_DST}/.secrets/credentials.json"
_WISE_CONNECTOR_KEY = "connectors/wise/definition/connector.json"
_PIPELINE_KEY = "pipelines/p/pipeline.json"
_STREAM_KEY = "pipelines/p/streams/orders.json"
_MANIFEST_KEY = "pipelines/manifest.json"
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
# embedded connectors below are fully model-valid and coverage-clean
# documents, not identity-only stand-ins: a case that expects no findings can
# only say what it means where the subtrees earn none of their own.
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
    pipeline workspace carries regardless of what its embedded
    `connectors/` subtree looks like."""
    return {
        _WISE_CONNECTION_KEY: _CONN_WISE,
        _PG_CONNECTION_KEY: _CONN_PG,
        _PG_ENDPOINT_KEY: _DB_ENDPOINT,
        _STREAM_KEY: _STREAM,
        _PIPELINE_KEY: _PIPELINE,
    }


def _pipeline_package_documents() -> dict:
    """A model-valid draft pipeline workspace as parsed documents keyed by
    workspace path. `wise`'s and `postgresql`'s embedded
    `connectors/<connector_id>/definition/...` subtrees are fully model-valid
    and coverage-clean, so a case built on this fixture reads any finding it
    gets as coming from the pipeline it is about rather than from a subtree
    that was never clean to begin with."""
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
    order comparison below can only detect that with content whose order the
    mapping genuinely decides."""
    documents = _pipeline_package_documents()
    pipeline = {**documents[_PIPELINE_KEY]}
    pipeline["connections"] = {
        **pipeline["connections"],
        "destinations": [*pipeline["connections"]["destinations"], "missing-connection-a"],
    }
    connection = {**documents[_WISE_CONNECTION_KEY], "connector_id": "not-a-bundled-connector"}
    return {**documents, _PIPELINE_KEY: pipeline, _WISE_CONNECTION_KEY: connection}


def _pipeline_package_documents_with_embedded_connectors() -> dict:
    """`_pipeline_core_documents` plus `wise`'s and `postgresql`'s own
    model-valid connector documents (the same documents the bundle fixture
    ships fully covered) shipped alone — no sibling type-map or `endpoints/`
    directory — so each embedded subtree has coverage findings of its own to
    report: `wise`'s RULE-PKG-030/035 and `postgresql`'s RULE-PKG-030."""
    return {
        **_pipeline_core_documents(),
        "connectors/wise/definition/connector.json": _CONNECTOR_WISE,
        "connectors/postgresql/definition/connector.json": _CONNECTOR_PG,
    }


def _pipeline_package_documents_with_a_connection_type_map() -> dict:
    """`_pipeline_package_documents` with a type map beside the `postgresql`
    connection.

    Its `read` section repeats one rule, which RULE-TMAP-022 reports, so the
    map is provably graded rather than merely collected. Its `write` section
    renders part of the Arrow vocabulary, which a *connector*'s map earns
    RULE-TMAP-017 for and a *connection*'s does not (RULE-TMAP-018) — the pair
    is what tells "graded at connection scope" apart from "graded at all".
    """
    rule = {"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}
    return {
        **_pipeline_package_documents(),
        _PG_CONNECTION_TYPE_MAP_KEY: _type_map_doc(read=[rule, dict(rule)], write=[rule]),
    }


def _pipeline_package_documents_with_a_stream_key_holding_a_connection() -> dict:
    """`_pipeline_package_documents` with a connection document filed under a
    stream's key. The key is what declares the kind on this route, so this is
    an `entity-mismatch`, not a stream graded as whatever its content
    resembles."""
    return {**_pipeline_package_documents(), _STREAM_KEY: _CONN_WISE}


#: A connection id no fixture document declares.
_OTHER_ID = "55555555-5555-4555-8555-555555555555"

#: A model-valid document filed where a published location reaches it, under
#: an owner the package does not carry. Each is clean in isolation, so the only
#: thing that can report it is the route noticing it belongs to nothing — a
#: fixture with its own defect could not tell the two apart.
_UNOWNED_MEMBERS = {
    "endpoint under a connection the package does not carry": (
        f"connections/{_OTHER_ID}/definition/endpoints/{_EID}.json", _DB_ENDPOINT),
    "type map under a connection the package does not carry": (
        f"connections/{_OTHER_ID}/definition/type-map.json",
        _type_map_doc(read=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}])),
    "credentials under a connection the package does not carry": (
        f"connections/{_OTHER_ID}/.secrets/credentials.json", {"password": "x"}),
    "stream under a pipeline directory the package does not carry": (
        "pipelines/q/streams/orders.json", _STREAM),
}


def _active_pipeline_package_documents() -> dict:
    """`_pipeline_package_documents` with the pipeline authored `active` while
    its one stream stays a draft — the smallest package the runnability gate
    answers True for, and so the only kind that grades the derivation."""
    documents = _pipeline_package_documents()
    return {**documents, _PIPELINE_KEY: {**documents[_PIPELINE_KEY], "status": "active"}}


def _texts(documents: dict) -> dict[str, str]:
    """`documents` as the file text a request carries, leaving alone a value
    that is already text — a document whose whole defect is that it does not
    parse."""
    return {key: doc if isinstance(doc, str) else json.dumps(doc)
            for key, doc in documents.items()}


def _withholding_reasons() -> dict[str, tuple[dict, list[tuple[str, str]], list[str]]]:
    """One package per reason `validate_pipeline_package` keeps a member out of
    the bundle, each mapped to the exact findings the route must then produce —
    `(message_id, path)`, in the order it returns them — and to the keys its
    skipped referential pass must name.

    Every row mutates `_pipeline_package_documents`, which reports nothing at
    all, so a row's expectation is the whole of what its one defect produces. A
    reason that stops reporting its `fail`, stops withholding, starts
    withholding something else, or runs the referential pass anyway reddens
    here.
    """
    orphan_endpoint_key = f"connections/{_OTHER_ID}/definition/endpoints/{_EID}.json"
    foreign_stream_key = "pipelines/q/streams/orders.json"
    skipped = ("referential-check-skipped", f"{quote(_PIPELINE_KEY)}#")
    documents = _pipeline_package_documents
    return {
        "the member's own text did not parse": (
            {**documents(), _STREAM_KEY: "{not json"},
            [skipped, ("unreadable-document", f"{quote(_STREAM_KEY)}#")],
            [_STREAM_KEY]),
        "the member holds a kind its key does not declare": (
            _pipeline_package_documents_with_a_stream_key_holding_a_connection(),
            [skipped, ("entity-mismatch", f"{quote(_STREAM_KEY)}#")],
            [_STREAM_KEY]),
        "the package carries no owning connection": (
            {**documents(), orphan_endpoint_key: _DB_ENDPOINT},
            [("owning-document-missing", f"{quote(orphan_endpoint_key)}#"), skipped],
            [orphan_endpoint_key]),
        "the package carries no owning pipeline": (
            {**documents(), foreign_stream_key: _STREAM},
            [skipped, ("owning-document-missing", f"{quote(foreign_stream_key)}#")],
            [foreign_stream_key]),
        "the owning connection did not grade": (
            {**documents(), _PG_CONNECTION_KEY: "{not json"},
            [("unreadable-document", f"{quote(_PG_CONNECTION_KEY)}#"), skipped],
            [_PG_CONNECTION_KEY, _PG_ENDPOINT_KEY]),
        "the owning connection declares no identity": (
            {**documents(),
             _PG_CONNECTION_KEY: {k: v for k, v in _CONN_PG.items() if k != "connection_id"}},
            [("owning-document-unidentified", f"{quote(_PG_CONNECTION_KEY)}#"), skipped],
            [_PG_CONNECTION_KEY, _PG_ENDPOINT_KEY]),
        "the owning connection names another directory": (
            {**documents(), _PG_CONNECTION_KEY: {**_CONN_PG, "connection_id": _OTHER_ID}},
            [("package-directory-mismatch", f"{quote(_PG_CONNECTION_KEY)}#/connection_id"),
             skipped],
            [_PG_CONNECTION_KEY, _PG_ENDPOINT_KEY]),
        "the embedded connector holds content no detector claims": (
            {**documents(), _WISE_CONNECTOR_KEY: [1, 2]},
            [("unrecognized-document", f"{quote(_WISE_CONNECTOR_KEY)}#"), skipped],
            [_WISE_CONNECTOR_KEY]),
        "the embedded connector declares no identity": (
            {**documents(),
             _WISE_CONNECTOR_KEY: {k: v for k, v in _CONNECTOR_WISE.items() if k != "connector_id"}},
            [("missing", f"{quote(_WISE_CONNECTOR_KEY)}#/connector_id"), skipped],
            [_WISE_CONNECTOR_KEY]),
        "the embedded connector names another directory": (
            {**documents(), _WISE_CONNECTOR_KEY: {**_CONNECTOR_WISE, "connector_id": "wise-two"}},
            [("package-directory-mismatch", f"{quote(_WISE_CONNECTOR_KEY)}#/connector_id"),
             skipped],
            [_WISE_CONNECTOR_KEY]),
        "the pipeline document itself did not grade": (
            {**documents(), _PIPELINE_KEY: "{not json"},
            [("unreadable-document", f"{quote(_PIPELINE_KEY)}#"), skipped],
            [_PIPELINE_KEY, _STREAM_KEY]),
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


def test_a_type_map_entity_mismatch_is_reported(validator):
    """A map sent under another kind's name is reported — its `$schema` names
    the type map, not the kind the caller declared."""
    result = validator.validate_single_document(
        _document_request(_CONNECTOR_PG_TYPE_MAP, "connector"))
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["entity-mismatch"], result


_SPLIT_SHAPE_MAP = {"$schema": f"{_H}/type-map-read/latest.json", "direction": "read",
                    "rules": [{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}]}


def test_a_document_no_detector_claims_is_graded_not_refused(validator):
    """A split-shape document's `$schema` is no document schema the contract
    registers, so no detector claims it. The caller's declaration does not
    refuse that — grading answers with every published kind's discriminating
    field, which a refusal naming only the declared entity does not."""
    result = validator.validate_single_document(
        _document_request(_SPLIT_SHAPE_MAP, "type-map"))
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"]] == ["unrecognized-document"], result


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
# `_claimed_entities`'s tables restate a vocabulary the contract package
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
    from analitiq.validator.document_set import _claimed_entities

    assert {entity for entity, _ in _DOCUMENT_FOR_ENTITY} == set(DOCUMENT_SCHEMA_NAMES)
    for entity, document in _DOCUMENT_FOR_ENTITY:
        assert _claimed_entities(document) == {entity}, (entity, document)


def test_an_assembled_bundle_resolves_to_no_published_name(validator):
    """A bundle is not a single document and no published schema names one, so
    it is deliberately absent from `_claimed_entities`'s tables. A detector
    still claims it, so sent to this entry point it is refused — never
    validated as the `pipeline` its core carries."""
    bundle = {"pipeline": _PIPELINE, "streams": [_STREAM], "connections": {_SRC: _CONN_WISE}}
    result = validator.validate_single_document(_document_request(bundle, "pipeline"))
    assert result["passed"] is False
    assert [f["message_id"] for f in result["findings"] if f["kind"] == "fail"] == ["entity-mismatch"]




# ---------------------------------------------------------------------------
# validate_connector_package — the connector document where the published
# `connector-package` schema locates it.
# ---------------------------------------------------------------------------

def test_validate_connector_package_validates_its_own_root_shape(validator):
    result = validator.validate_connector_package(
        _package_request(_connector_package_documents()))
    # Findings empty, not merely `passed` — a route emitting a `notApplicable`
    # finding and still reporting a pass would satisfy the weaker assertion.
    assert result == _expected_envelope(validator, []), result


def test_unparseable_document_in_a_package_is_a_finding_not_a_raise(validator):
    """One document's text being unreadable is package *content*, not a
    malformed argument — the request model accepts any text — so the package
    comes back as a failing envelope rather than a raised error."""
    documents = {key: json.dumps(doc)
                 for key, doc in _connector_package_documents().items()}
    documents[f"{_ENDPOINTS}/v2__widgets.json"] = "{not json"
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


def test_a_connector_package_finding_names_the_document_it_is_about(validator):
    documents = {**_connector_package_documents(),
                 f"{_ENDPOINTS}/v2__widgets.json": _uncovered_endpoint_document()}
    documents[_CONNECTOR_KEY]["display_name"] = 7
    result = validator.validate_connector_package(_package_request(documents))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("string_type", "definition/connector.json#/display_name"),
        ("native-type-unresolved",
         "definition/endpoints/v2__widgets.json#/operations/read/response/schema/items/properties/b"),
    ], result


def test_a_connector_package_obligation_is_about_the_whole_connector(validator):
    documents = _connector_package_documents()
    del documents[_CONNECTOR_TYPE_MAP]
    result = validator.validate_connector_package(_package_request(documents))
    [found] = [f for f in result["findings"] if f["message_id"] == "read-map-missing"]
    assert found["path"] == "definition/connector.json#", found


def test_a_connector_package_key_is_percent_encoded_in_a_finding(validator):
    documents = {**_connector_package_documents(),
                 f"{_ENDPOINTS}/v2 widgets.json": _uncovered_endpoint_document()}
    result = validator.validate_connector_package(_package_request(documents))
    [found] = [f for f in result["findings"] if f["message_id"] == "native-type-unresolved"]
    assert found["path"] == (
        "definition/endpoints/v2%20widgets.json#/operations/read/response/schema/items/properties/b"), found


def test_a_connector_package_without_a_connector_document_fails(validator):
    documents = _connector_package_documents()
    del documents[_CONNECTOR_KEY]
    result = validator.validate_connector_package(_package_request(documents))
    assert result["passed"] is False
    [found] = result["findings"]
    # Keyless: the package sent no connector document, so there is none for
    # the finding to be about.
    assert (found["message_id"], found["kind"], found["path"]) == (
        "connector-document-missing", "fail", ""), found
    assert "rule" not in found, found


def test_a_connector_document_at_the_package_root_is_not_the_connector(validator):
    """The published `connector-package` schema locates the connector under
    `definition/`, so one at the package root is no connector document."""
    documents = _connector_package_documents()
    documents["connector.json"] = documents.pop(_CONNECTOR_KEY)
    result = validator.validate_connector_package(_package_request(documents))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("connector-document-missing", "")], result


def test_a_connector_document_that_does_not_parse_is_a_finding(validator):
    documents = {key: json.dumps(doc) for key, doc in _connector_package_documents().items()}
    documents[_CONNECTOR_KEY] = "{not json"
    result = validator.validate_connector_package(ValidatePackageRequest(documents=documents))
    assert result["passed"] is False
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("unreadable-document", "definition/connector.json#")], result


def test_a_connector_document_holding_another_entity_is_a_mismatch(validator):
    documents = _connector_package_documents()
    documents[_CONNECTOR_KEY] = documents[_CONNECTOR_TYPE_MAP]
    result = validator.validate_connector_package(_package_request(documents))
    assert result["passed"] is False
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("entity-mismatch", "definition/connector.json#")], result


def _package_texts(documents: dict, **texts: str) -> dict:
    """`documents` serialized to request text, with `texts` (keyed by a
    package key spelt with `__` for `/`) sent verbatim instead."""
    return {**{key: json.dumps(doc) for key, doc in documents.items()},
            **{key.replace("__", "/"): text for key, text in texts.items()}}


def _without(documents: dict, *keys: str) -> dict:
    return {key: doc for key, doc in documents.items() if key not in keys}


def _mistyped_endpoint() -> dict:
    endpoint = _uncovered_endpoint_document()
    endpoint["endpoint_id"] = 7
    return endpoint


# Each package reports at least one finding, between them about the connector
# itself, a sibling map, an endpoint, a key that needs encoding, and a document
# that never parsed.
_PACKAGES_WITH_FINDINGS = {
    "mistyped connector, uncovered endpoint": {
        **_connector_package_documents(),
        _CONNECTOR_KEY: {**_connector_package_documents()[_CONNECTOR_KEY], "display_name": 7},
        f"{_ENDPOINTS}/v2__widgets.json": _uncovered_endpoint_document()},
    "key needing encoding": {**_connector_package_documents(),
                             f"{_ENDPOINTS}/v2 widgets.json": _uncovered_endpoint_document()},
    "mistyped endpoint": {**_connector_package_documents(),
                          f"{_ENDPOINTS}/v2__widgets.json": _mistyped_endpoint()},
    "read map missing": _without(_connector_package_documents(), _CONNECTOR_TYPE_MAP),
    "endpoints missing": _without(_connector_package_documents(), f"{_ENDPOINTS}/v1__records.json"),
    "endpoint nested": {**_without(_connector_package_documents(), f"{_ENDPOINTS}/v1__records.json"),
                        f"{_ENDPOINTS}/sub/v1__records.json":
                            _connector_package_documents()[f"{_ENDPOINTS}/v1__records.json"]},
    "database write section missing": {
        _CONNECTOR_KEY: _CONNECTOR_PG,
        _CONNECTOR_TYPE_MAP: _type_map_doc(
            read=[{"match": "exact", "native_type": "bigint", "arrow_type": "Int64"}])},
    "connector holding another entity": {**_connector_package_documents(),
                                         _CONNECTOR_KEY: _CONNECTOR_PG_TYPE_MAP},
}


def _assert_every_path_locates_its_finding(
        findings: list, keys: set, package_wide: frozenset = frozenset()) -> None:
    """Every finding is located where `document_set.ValidationEnvelope` says a
    package's finding is: a key the caller sent, or a directory one of those
    keys implies.

    `package_wide` names the message ids this call expects to arrive carrying
    the empty path, so the check runs both ways: an id named here that arrives
    carrying a reference names an entry the package does not carry, and any
    other id arriving with the empty path has lost the entry it was about.
    """
    assert findings, "no findings: nothing was measured"
    entries = set(keys)
    for key in keys:
        segments = key.split("/")
        entries.update("/".join(segments[:depth]) for depth in range(1, len(segments)))
    for f in findings:
        if f["message_id"] in package_wide:
            assert f["path"] == "", f
            continue
        reference, sep, pointer = f["path"].partition("#")
        assert sep and unquote(reference) in entries, f
        # No document here carries a member keyed "", so a pointer of `/` can
        # only be the whole document misspelt.
        assert pointer == "" or (pointer.startswith("/") and pointer != "/"), f


#: Each case is `(route, request texts, message ids expected keyless)`.
_PACKAGE_CASES_WITH_FINDINGS = {
    **{f"connector: {name}": ("validate_connector_package", _package_texts(documents), frozenset())
       for name, documents in _PACKAGES_WITH_FINDINGS.items()},
    "connector: map unparseable": (
        "validate_connector_package",
        _package_texts(_connector_package_documents(), **{"definition__type-map.json": "{not json"}),
        frozenset()),
    "connector: connector unparseable": (
        "validate_connector_package",
        _package_texts(_connector_package_documents(), **{"definition__connector.json": "{not json"}),
        frozenset()),
    "connector: no connector document": (
        "validate_connector_package",
        _package_texts(_without(_connector_package_documents(), _CONNECTOR_KEY)),
        frozenset({"connector-document-missing"})),
    "pipeline: unresolved refs": (
        "validate_pipeline_package",
        _package_texts(_pipeline_package_documents_with_two_findings()), frozenset()),
    "pipeline: embedded connector subtrees": (
        "validate_pipeline_package",
        _package_texts(_pipeline_package_documents_with_embedded_connectors()), frozenset()),
    "pipeline: embedded connector with no connector document": (
        "validate_pipeline_package",
        _package_texts(_without(_pipeline_package_documents(), _WISE_CONNECTOR_KEY)),
        frozenset()),
    "pipeline: connection type map": (
        "validate_pipeline_package",
        _package_texts(_pipeline_package_documents_with_a_connection_type_map()), frozenset()),
    "pipeline: stream key holding a connection": (
        "validate_pipeline_package",
        _package_texts(_pipeline_package_documents_with_a_stream_key_holding_a_connection()),
        frozenset()),
    "pipeline: no pipeline document": (
        "validate_pipeline_package",
        _package_texts(_without(_pipeline_package_documents(), _PIPELINE_KEY)),
        frozenset({"pipeline-document-missing"})),
    "pipeline: two pipeline documents": (
        "validate_pipeline_package",
        _package_texts({**_pipeline_package_documents(),
                        "pipelines/q/pipeline.json": _PIPELINE}),
        frozenset({"pipeline-document-ambiguous"})),
    **{f"pipeline: {name}": ("validate_pipeline_package", _texts(documents), frozenset())
       for name, (documents, _, _) in _withholding_reasons().items()},
}


@pytest.mark.parametrize("route, texts, package_wide", _PACKAGE_CASES_WITH_FINDINGS.values(),
                         ids=_PACKAGE_CASES_WITH_FINDINGS.keys())
def test_every_package_finding_names_a_submitted_document(validator, route, texts, package_wide):
    result = getattr(validator, route)(ValidatePackageRequest(documents=texts))
    _assert_every_path_locates_its_finding(result["findings"], set(texts), package_wide)


def test_a_finding_about_the_package_directory_names_the_connector(validator, tmp_path, refuse):
    # A package in memory can always be listed, so only a directory on disk
    # reaches the finding that is about the directory rather than a document
    # in it. Both routes read that finding from the package root.
    from analitiq.validator.document_set import _from_package_root
    documents = _connector_package_documents()
    _write_package(tmp_path, documents)
    refuse(tmp_path / "definition", 0o300)
    findings = validator.validate_document(documents[_CONNECTOR_KEY],
                                           doc_path=tmp_path / _CONNECTOR_KEY)
    assert "type-map-dir-unlisted" in [f["message_id"] for f in findings], findings
    _assert_every_path_locates_its_finding(
        _from_package_root(findings, _CONNECTOR_KEY), set(documents))


# ---------------------------------------------------------------------------
# Deterministic output: findings do not depend on the order the caller happened
# to build its mapping in.
# ---------------------------------------------------------------------------

def test_connector_package_finding_order_is_independent_of_input_order(validator):
    # Two distinct uncovered endpoints, not the clean package: comparing two
    # empty findings lists cannot detect order-sensitivity at all.
    documents = {
        **_connector_package_documents(),
        f"{_ENDPOINTS}/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        f"{_ENDPOINTS}/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    forward = validator.validate_connector_package(_package_request(documents))
    assert len(forward["findings"]) >= 2, forward  # non-vacuous: order genuinely matters below
    reversed_documents = dict(reversed(list(documents.items())))
    backward = validator.validate_connector_package(_package_request(reversed_documents))
    assert forward == backward


def test_pipeline_package_finding_order_is_independent_of_input_order(validator):
    # The same determinism rule on the other package entry point, over a
    # fixture that reports real findings down both orders.
    documents = _pipeline_package_documents_with_two_findings()
    forward = validator.validate_pipeline_package(_package_request(documents))
    assert len(forward["findings"]) >= 2, forward  # non-vacuous: order genuinely matters below
    reversed_documents = dict(reversed(list(documents.items())))
    backward = validator.validate_pipeline_package(_package_request(reversed_documents))
    assert forward == backward


def test_connector_package_equivalence_with_the_path_based_route(validator, tmp_path):
    # Two distinct uncovered endpoints, not just a clean package: a route
    # producing zero findings would make "findings order included" vacuous.
    documents = {
        **_connector_package_documents(),
        f"{_ENDPOINTS}/v2__widgets.json": _uncovered_endpoint_document(
            endpoint_id="v2__widgets", request_path="/v2/widgets", native="BOOLEAN", arrow="Boolean"),
        f"{_ENDPOINTS}/v3__gadgets.json": _uncovered_endpoint_document(
            endpoint_id="v3__gadgets", request_path="/v3/gadgets", native="INTEGER", arrow="Int64"),
    }
    from analitiq.validator.document_set import _from_package_root
    _write_package(tmp_path, documents)
    path_based = validator.validate_document(documents[_CONNECTOR_KEY],
                                             doc_path=tmp_path / _CONNECTOR_KEY)
    assert len(path_based) >= 2, path_based  # non-vacuous: order genuinely matters below
    package_based = validator.validate_connector_package(_package_request(documents))
    # The disk route reads a finding about the connector itself as a bare
    # pointer, and one about a sibling from the connector's own directory. A
    # package has no validated document, so the package route reads both from
    # the package root, and does nothing else.
    expected = _from_package_root(path_based, _CONNECTOR_KEY)
    assert json.dumps(package_based) == json.dumps(_expected_envelope(validator, expected))


# ---------------------------------------------------------------------------
# validate_pipeline_package — a workspace holding one pipeline package, graded
# by the findings it must produce. A bundle-level finding points into the
# assembled bundle (`/connections/1/connector_id`); what a caller was handed is
# a key, so each one is reported against the document it came from.
# ---------------------------------------------------------------------------

def test_validate_pipeline_package_validates_its_own_root_shape(validator):
    result = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents()))
    assert result == _expected_envelope(validator, []), result


def test_a_pipeline_package_reports_each_bundle_finding_against_its_own_document(validator):
    result = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents_with_two_findings()))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("connector-ref-unresolved", f"connections/{_SRC}/connection.json#/connector_id"),
        ("connection-ref-unresolved", "pipelines/p/pipeline.json#/connections"),
    ], result


def test_embedded_connector_subtree_gets_its_own_coverage_findings(validator):
    """`connectors/wise/definition/connector.json` (kind=api) ships no sibling
    type map or `endpoints/` directory. `validate_pipeline_package` grades the
    subtree as a connector package and reports its own coverage findings
    (RULE-PKG-030 missing read map, RULE-PKG-035 missing endpoints/), read from
    the workspace root."""
    result = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents_with_embedded_connectors()))
    scoped = [f for f in result["findings"] if f["path"].startswith("connectors/wise/")]
    assert any(f.get("rule") == "RULE-PKG-030" for f in scoped), result["findings"]
    assert any(f.get("rule") == "RULE-PKG-035" for f in scoped), result["findings"]
    # Both subtrees: a walk that stopped at whichever it reached first satisfies
    # the assertions for that one alone.
    pg_scoped = [f for f in result["findings"] if f["path"].startswith("connectors/postgresql/")]
    assert any(f.get("rule") == "RULE-PKG-030" for f in pg_scoped), result["findings"]


def test_an_embedded_connector_without_its_connector_document_is_reported_at_its_directory(
        validator):
    """The connector route reports a package carrying no connector document
    with the empty path; embedded, that package is a directory the workspace
    carries, and the finding names it."""
    result = validator.validate_pipeline_package(
        _package_request(_without(_pipeline_package_documents(), _WISE_CONNECTOR_KEY)))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("connector-document-missing", "connectors/wise#"),
        ("referential-check-skipped", f"{quote(_PIPELINE_KEY)}#"),
    ], result


def test_a_connection_type_map_is_graded_at_connection_scope(validator):
    result = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents_with_a_connection_type_map()))
    reported = [(f["message_id"], f["path"]) for f in result["findings"]]
    assert reported == [
        ("duplicate-type-map-rule", f"{_PG_CONNECTION_TYPE_MAP_KEY}#/read/1")], reported


def test_a_package_with_no_pipeline_document_fails(validator):
    """A pipeline document's key is wherever `pipelines/<dir>/` puts it, so
    there is no key to name and the finding is about the package, with the
    empty path."""
    documents = _without(_pipeline_package_documents(), _PIPELINE_KEY)
    result = validator.validate_pipeline_package(_package_request(documents))
    assert result["passed"] is False
    [found] = result["findings"]
    assert (found["message_id"], found["kind"], found["path"]) == (
        "pipeline-document-missing", "fail", ""), found
    assert "rule" not in found, found


def test_a_package_with_two_pipeline_documents_fails(validator):
    documents = {**_pipeline_package_documents(), "pipelines/q/pipeline.json": _PIPELINE}
    result = validator.validate_pipeline_package(_package_request(documents))
    assert result["passed"] is False
    [found] = result["findings"]
    assert (found["message_id"], found["kind"], found["path"]) == (
        "pipeline-document-ambiguous", "fail", ""), found
    # Both keys, so the author is told which two to reconcile rather than that
    # "a" duplicate exists.
    assert _PIPELINE_KEY in found["message"], found
    assert "pipelines/q/pipeline.json" in found["message"], found


@pytest.mark.parametrize("key,document", _UNOWNED_MEMBERS.values(), ids=_UNOWNED_MEMBERS.keys())
def test_a_member_the_package_carries_no_owner_for_is_reported(validator, key, document):
    """Where a document sits is what says which pipeline or connection it
    belongs to, so one filed under an owner the package does not carry belongs
    to nothing. Passing it silently is the failure this pins: the document was
    submitted, a published location reaches it, and nothing said a word about
    it."""
    documents = {**_pipeline_package_documents(), key: document}
    result = validator.validate_pipeline_package(_package_request(documents))
    assert result["passed"] is False, result
    about = [(f["message_id"], f["path"]) for f in result["findings"]
             if unquote(f["path"].partition("#")[0]) == key]
    assert about == [("owning-document-missing", f"{quote(key)}#")], result


@pytest.mark.parametrize("documents,expected,withheld", _withholding_reasons().values(),
                         ids=_withholding_reasons().keys())
def test_each_reason_a_member_is_kept_out_of_the_bundle_reports_it(
        validator, documents, expected, withheld):
    """A member missing from the bundle costs every referential verdict about
    it, so the caller is owed two things for each: a `fail` at the document
    responsible, and the skip naming what could not be placed. Asserting the
    whole finding set is what grades the pair — a reason that withholds while
    reporting nothing produces a strictly smaller set, and passes any
    assertion written as a membership check."""
    result = validator.validate_pipeline_package(
        ValidatePackageRequest(documents=_texts(documents)))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == expected, result
    [skip] = [f for f in result["findings"] if f["message_id"] == "referential-check-skipped"]
    assert [key for key in sorted(documents) if repr(key) in skip["message"]] == \
        sorted(withheld), skip


def test_a_member_claimed_for_another_kind_is_refused(validator):
    """Decision branch one: a detector claims the stream key's document as a
    connection, which is evidence it is something else, so the key's declared
    kind refuses it rather than grading it as whatever it resembles."""
    result = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents_with_a_stream_key_holding_a_connection()))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("referential-check-skipped", f"{quote(_PIPELINE_KEY)}#"),
        ("entity-mismatch", f"{quote(_STREAM_KEY)}#"),
    ], result


def test_a_member_no_detector_claims_is_graded(validator):
    """Decision branch two: a stream missing `destinations` is claimed by no
    detector, so it is graded rather than refused — the grade answers with
    every published kind's discriminating field — and, identifying no kind, it
    is still kept out of the bundle."""
    documents = _pipeline_package_documents()
    stream = {k: v for k, v in documents[_STREAM_KEY].items() if k != "destinations"}
    result = validator.validate_pipeline_package(
        _package_request({**documents, _STREAM_KEY: stream}))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("referential-check-skipped", f"{quote(_PIPELINE_KEY)}#"),
        ("unrecognized-document", f"{quote(_STREAM_KEY)}#"),
    ], result


def test_an_endpoint_key_holding_another_kind_is_a_mismatch(validator):
    """A connection's `endpoints/` directory holds database endpoints, so an
    api endpoint filed there is refused rather than graded as what it
    resembles."""
    result = validator.validate_pipeline_package(
        _package_request({**_pipeline_package_documents(), _PG_ENDPOINT_KEY: _WISE_TRANSFERS_ENDPOINT}))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("entity-mismatch", f"{quote(_PG_ENDPOINT_KEY)}#"),
        ("referential-check-skipped", f"{quote(_PIPELINE_KEY)}#"),
    ], result


_CONNECTOR_TYPE_MAP_KEY = "connectors/postgresql/definition/type-map.json"
_MAP_WITHOUT_ITS_SCHEMA = {k: v for k, v in _CONNECTOR_PG_TYPE_MAP.items() if k != "$schema"}


@pytest.mark.parametrize("key,document,expected", [
    (_PG_CONNECTION_TYPE_MAP_KEY, _STREAM,
     [("entity-mismatch", f"{quote(_PG_CONNECTION_TYPE_MAP_KEY)}#")]),
    (_PG_CONNECTION_TYPE_MAP_KEY, _MAP_WITHOUT_ITS_SCHEMA,
     [("missing", f"{quote(_PG_CONNECTION_TYPE_MAP_KEY)}#/$schema")]),
    (_CONNECTOR_TYPE_MAP_KEY, _MAP_WITHOUT_ITS_SCHEMA,
     [("missing", f"{quote(_CONNECTOR_TYPE_MAP_KEY)}#/$schema")]),
], ids=["another kind at the type-map key", "a connection map missing its `$schema`",
        "the same map inside a connector subtree"])
def test_a_defective_type_map_is_graded_as_the_map_its_key_declares(
        validator, key, document, expected):
    """A map whose `$schema` is missing — the field its detector reads, so
    nothing claims it — is graded as a map rather than refused, and answers
    with the field itself. A document some detector claims for another kind is
    refused."""
    result = validator.validate_pipeline_package(
        _package_request({**_pipeline_package_documents(), key: document}))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == expected, result


def test_a_connections_unreadable_type_map_is_reported_through_the_published_loader(validator):
    documents = _package_texts(_pipeline_package_documents_with_a_connection_type_map())
    documents[_PG_CONNECTION_TYPE_MAP_KEY] = "{not json"
    result = validator.validate_pipeline_package(ValidatePackageRequest(documents=documents))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("type-map-unparseable", f"{_PG_CONNECTION_TYPE_MAP_KEY}#")], result


def test_a_connector_reference_resolves_only_off_a_graded_connector(validator):
    """The identity a connection's `connector_id` resolves against is read off
    what the connector route graded, never off a parse of the same text made
    here. A document that route refused would otherwise donate its
    `connector_id` and resolve a reference the refusal should have left
    standing."""
    documents = _pipeline_package_documents()
    ghost_key = "connectors/ghost/definition/connector.json"
    result = validator.validate_pipeline_package(_package_request({
        **documents,
        _WISE_CONNECTION_KEY: {**documents[_WISE_CONNECTION_KEY], "connector_id": "ghost"},
        ghost_key: {**_CONN_WISE, "connector_id": "ghost", "connection_id": _DST},
    }))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == [
        ("entity-mismatch", f"{quote(ghost_key)}#"),
        ("referential-check-skipped", f"{quote(_PIPELINE_KEY)}#"),
    ], result


def test_runnability_is_gated_by_the_pipeline_documents_own_status(validator):
    """The package reads `require_runnable` off the pipeline document rather
    than taking it as an argument. A draft package is held to referential
    integrity alone; the same package authored `active` is held to runnability
    too, and its one draft stream is what it then lacks."""
    draft = validator.validate_pipeline_package(
        _package_request(_pipeline_package_documents()))
    assert draft["findings"] == [], draft
    active = validator.validate_pipeline_package(
        _package_request(_active_pipeline_package_documents()))
    assert [(f["message_id"], f["path"]) for f in active["findings"]] == [
        ("active-pipeline-no-runnable-stream", "pipelines/p/pipeline.json#/streams")], active


@pytest.mark.parametrize("field, value, expected", [
    ("connection_id", "somebody-else",
     [("value_error", f"{_PG_ENDPOINT_KEY}#")]),
    ("scope", "connector",
     [("extra_forbidden", f"{_PG_ENDPOINT_KEY}#/scope")]),
])
def test_a_bundle_stamp_the_model_refused_does_not_place_the_endpoint(
        validator, field, value, expected):
    """The connection an endpoint sits under is what says which connection it
    belongs to. An endpoint carrying that answer itself has just been refused
    for carrying it; were the value left to stand, the bundle would answer
    with the stream's `endpoint_ref` failing to resolve — blaming a document
    whose reference is correct."""
    documents = _pipeline_package_documents()
    documents[_PG_ENDPOINT_KEY] = {**documents[_PG_ENDPOINT_KEY], field: value}
    result = validator.validate_pipeline_package(_package_request(documents))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == expected, result


def test_a_connection_directory_not_named_by_its_connection_id_is_a_finding(validator):
    """A connection's directory is its identity: the document's own
    `connection_id` must equal the directory name."""
    documents = _pipeline_package_documents()
    documents["connections/postgresql/connection.json"] = documents.pop(_PG_CONNECTION_KEY)
    documents[f"connections/postgresql/definition/endpoints/{_EID}.json"] = \
        documents.pop(_PG_ENDPOINT_KEY)
    result = validator.validate_pipeline_package(_package_request(documents))
    mismatch = [f for f in result["findings"] if f["message_id"] == "package-directory-mismatch"]
    assert [(f["path"], f["rule"]) for f in mismatch] == [
        ("connections/postgresql/connection.json#/connection_id", "RULE-PKG-036")], result
    assert result["passed"] is False


#: A value only the credentials document carries, so a finding quoting it can
#: only have read it from there.
_PLANTED_SECRET = "planted-secret-7f3a9c"


@pytest.mark.parametrize("text, expected", [
    pytest.param(json.dumps({"password": _PLANTED_SECRET}), [], id="valid"),
    pytest.param(json.dumps([_PLANTED_SECRET]),
                 [("dict_type", f"{_PG_CREDENTIALS_KEY}#")], id="not an object"),
    pytest.param(f'{{"password": "{_PLANTED_SECRET}"',
                 [("unreadable-document", f"{_PG_CREDENTIALS_KEY}#")], id="unparseable"),
])
def test_connection_credentials_are_graded_by_shape_and_never_echoed(validator, text, expected):
    documents = {**_package_texts(_pipeline_package_documents()), _PG_CREDENTIALS_KEY: text}
    result = validator.validate_pipeline_package(ValidatePackageRequest(documents=documents))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == expected, result
    assert not any(_PLANTED_SECRET in json.dumps(f) for f in result["findings"]), result


def test_a_key_no_published_location_matches_is_not_graded(validator):
    """Only what a published location reaches is graded, so text that would
    fail any grade reports nothing where no location reaches it."""
    documents = {**_package_texts(_pipeline_package_documents()),
                 "README.md": "{not json",
                 "pipelines/p/notes.json": "{not json",
                 f"connections/{_DST}/definition/endpoints/nested/extra.json": "{not json"}
    result = validator.validate_pipeline_package(ValidatePackageRequest(documents=documents))
    assert result == _expected_envelope(validator, []), result


_MANIFEST = {"pipelines": [{"pipeline_id": _PID, "status": "draft", "path": "p/pipeline.json"}]}


@pytest.mark.parametrize("manifest, expected", [
    pytest.param(_MANIFEST, [], id="valid"),
    pytest.param({"pipelines": [{**_MANIFEST["pipelines"][0], "pipeline_id": "not-a-uuid"}]},
                 [("string_pattern_mismatch", f"{_MANIFEST_KEY}#/pipelines/0/pipeline_id")],
                 id="invalid"),
    pytest.param(_CONN_WISE, [("entity-mismatch", f"{_MANIFEST_KEY}#")],
                 id="another kind at the manifest key"),
])
def test_a_carried_manifest_is_graded_as_a_pipeline_manifest(validator, manifest, expected):
    result = validator.validate_pipeline_package(
        _package_request({**_pipeline_package_documents(), _MANIFEST_KEY: manifest}))
    assert [(f["message_id"], f["path"]) for f in result["findings"]] == expected, result
