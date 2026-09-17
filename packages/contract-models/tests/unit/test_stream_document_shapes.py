"""A stream document is refused when its source, destinations or mapping is
misshapen.

Each case bends one field of an otherwise valid stream document and validates
the whole document.
"""
from __future__ import annotations

import pytest

from analitiq.contracts.stream import StreamInput

from _contract_documents import DATABASE_OBJECT, STREAM, bent
from _contract_refusals import refusal_at

ASSIGNMENT = ("mapping", "assignments", 0)


def _source(**fields):
    return bent(STREAM, lambda d: d["source"].update(fields))


def _endpoint_ref(endpoint_ref):
    return _source(endpoint_ref=endpoint_ref)


def _database_source(**fields):
    return _source(
        endpoint_ref={
            "scope": "connection",
            "connection_id": "source_v1",
            "database_object": DATABASE_OBJECT,
        },
        **fields,
    )


def _assignment(bend):
    return bent(STREAM, lambda d: bend(d["mapping"]["assignments"][0]))


def _error_handling(error_handling):
    return _assignment(
        lambda assignment: assignment.update(
            validate={
                "rules": [{"type": "not_null", "field": ["city"]}],
                "error_handling": error_handling,
            }
        )
    )


@pytest.mark.parametrize(
    "document",
    [
        STREAM,
        _database_source(filters=[{"field": "id", "operator": "eq", "value": 1}]),
        _source(replication={"method": "full_refresh"}),
        _source(replication={"method": "incremental", "cursor_field": "updated_at"}),
        *(_error_handling({"strategy": strategy}) for strategy in ("fail", "dlq", "skip")),
    ],
    ids=[
        "stream", "database-source-filter", "full-refresh", "incremental",
        "strategy-fail", "strategy-dlq", "strategy-skip",
    ],
)
def test_a_well_shaped_stream_is_accepted(document):
    StreamInput.model_validate(document)


@pytest.mark.parametrize(
    "document, path, error_type",
    [
        (_endpoint_ref("connector:source_v1/widgets"), ("source", "endpoint_ref"), "model_attributes_type"),
        (
            _endpoint_ref({"scope": "unknown", "connection_id": "x", "endpoint_id": "y"}),
            ("source", "endpoint_ref"),
            "union_tag_invalid",
        ),
        (
            _endpoint_ref({"scope": "connector", "connection_id": "x", "endpoint_id": "y", "extra": "z"}),
            ("source", "endpoint_ref", "extra"),
            "extra_forbidden",
        ),
        (
            _endpoint_ref({"scope": "connector", "connection_id": "x", "endpoint_id": "y", "x-note": "z"}),
            ("source", "endpoint_ref", "x-note"),
            "extra_forbidden",
        ),
        (
            _endpoint_ref({"scope": "connector", "connection_id": "", "endpoint_id": "y"}),
            ("source", "endpoint_ref", "connection_id"),
            "string_too_short",
        ),
        (
            _endpoint_ref({"scope": "connector", "connection_id": "x", "endpoint_id": ""}),
            ("source", "endpoint_ref", "endpoint_id"),
            "string_too_short",
        ),
        (
            _endpoint_ref({"scope": "connector", "connection_id": "x"}),
            ("source", "endpoint_ref", "endpoint_id"),
            "missing",
        ),
        (
            _endpoint_ref({"scope": "connector"}),
            ("source", "endpoint_ref"),
            "missing",
        ),
        (
            _endpoint_ref({"scope": "connection", "connection_id": "x", "endpoint_id": "public_users"}),
            ("source", "endpoint_ref", "database_object"),
            "missing",
        ),
        (
            _database_source(filters=[{"operator": "eq", "value": 1}]),
            ("source", "filters", 0, "field"),
            "missing",
        ),
        (
            _source(replication={"cursor_field": "updated_at"}),
            ("source", "replication"),
            "union_tag_not_found",
        ),
        (_source(replication={"method": "cdc"}), ("source", "replication"), "union_tag_invalid"),
        (
            _source(replication={"method": "incremental", "cursor_field": ["updated_at"]}),
            ("source", "replication", "cursor_field"),
            "string_type",
        ),
        (bent(STREAM, lambda d: d.update(destinations=[])), ("destinations",), "too_short"),
    ],
    ids=[
        "string-endpoint-ref", "unknown-scope", "unknown-endpoint-ref-key",
        "extension-endpoint-ref-key", "empty-connection-id", "empty-endpoint-id",
        "no-endpoint-id", "scope-alone", "connection-scope-without-database-object",
        "filter-without-field", "replication-without-method",
        "unknown-replication-method", "array-cursor-field", "no-destinations",
    ],
)
def test_a_misshapen_source_or_destination_list_is_refused(document, path, error_type):
    refusal_at(StreamInput.model_validate, document, path, error_type)


@pytest.mark.parametrize(
    "document, path, error_type",
    [
        (
            bent(STREAM, lambda d: d["mapping"].update(defaults={"on_error": "dlq"})),
            ("mapping", "defaults"),
            "extra_forbidden",
        ),
        (
            _assignment(lambda a: a.update(target="city")),
            (*ASSIGNMENT, "target"),
            "model_type",
        ),
        (
            _assignment(lambda a: a["target"].update(generic_type="string")),
            (*ASSIGNMENT, "target", "generic_type"),
            "extra_forbidden",
        ),
        (
            _assignment(lambda a: a["target"].pop("arrow_type")),
            (*ASSIGNMENT, "target", "arrow_type"),
            "missing",
        ),
        (
            _error_handling({"strategy": None}),
            (*ASSIGNMENT, "validate", "error_handling", "strategy"),
            "literal_error",
        ),
        (
            _error_handling({"strategy": "nope"}),
            (*ASSIGNMENT, "validate", "error_handling", "strategy"),
            "literal_error",
        ),
    ],
    ids=[
        "unknown-mapping-key", "scalar-target", "unknown-target-key",
        "target-without-arrow-type", "null-strategy", "unknown-strategy",
    ],
)
def test_a_misshapen_mapping_is_refused(document, path, error_type):
    refusal_at(StreamInput.model_validate, document, path, error_type)
