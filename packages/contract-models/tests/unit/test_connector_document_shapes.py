"""A connector document is refused when a block outside its transports is
misshapen, or when its transports block cannot select a transport.

Each case bends one field of an otherwise valid connector document and
validates the whole document through the `kind`-discriminated union.
"""
from __future__ import annotations

import pytest

from analitiq.contracts.connector import parse_connector

from _contract_documents import API_CONNECTOR, DATABASE_CONNECTOR, SQL_CAPABILITIES, bent
from _contract_refusals import refusal_at


def _set(document, **fields):
    return bent(document, lambda d: d.update(fields))


def _without(document, field):
    return bent(document, lambda d: d.pop(field))


def _input_storage(storage, **extra):
    return _set(
        API_CONNECTOR,
        connection_contract={
            "inputs": {
                "token": {
                    "source": "user",
                    "phase": "pre_auth",
                    "storage": storage,
                    "type": "string",
                    "required": True,
                    **extra,
                }
            }
        },
    )


@pytest.mark.parametrize(
    "document",
    [
        _set(API_CONNECTOR, error_map={"http": {"429": "rate_limited"}}),
        _set(DATABASE_CONNECTOR, sql_capabilities=SQL_CAPABILITIES),
        _set(DATABASE_CONNECTOR, sql_capabilities={**SQL_CAPABILITIES, "limits": {}}),
        _input_storage("connection.parameters"),
        _input_storage("secrets", secret=True),
    ],
    ids=["error-map", "sql-capabilities", "sql-limits", "input-in-parameters", "input-in-secrets"],
)
def test_a_well_shaped_block_is_accepted(document):
    parse_connector(document)


@pytest.mark.parametrize(
    "document, error_type",
    [
        (_set(API_CONNECTOR, kind=""), "union_tag_invalid"),
        (_set(API_CONNECTOR, kind="graphql"), "union_tag_invalid"),
        (_without(API_CONNECTOR, "kind"), "union_tag_not_found"),
    ],
    ids=["empty-kind", "kind-outside-the-union", "no-kind"],
)
def test_a_kind_the_union_does_not_name_is_refused(document, error_type):
    refusal_at(parse_connector, document, (), error_type)


@pytest.mark.parametrize(
    "document, path, error_type",
    [
        (_set(API_CONNECTOR, transports={}), ("transports",), "too_short"),
        (
            _set(API_CONNECTOR, transports={"api": {"base_url": "https://api.example.test"}}),
            ("transports", "api"),
            "union_tag_not_found",
        ),
        (_set(API_CONNECTOR, error_map="auth"), ("error_map",), "model_type"),
        (_set(API_CONNECTOR, error_map={"http": ["429"]}), ("error_map", "http"), "dict_type"),
        (_set(DATABASE_CONNECTOR, sql_capabilities="full"), ("sql_capabilities",), "model_type"),
        (
            _set(DATABASE_CONNECTOR, sql_capabilities={**SQL_CAPABILITIES, "limits": 2100}),
            ("sql_capabilities", "limits"),
            "model_type",
        ),
        (
            _input_storage("connection.discovered"),
            ("connection_contract", "inputs", "token", "storage"),
            "literal_error",
        ),
    ],
    ids=[
        "no-transports", "transport-without-type", "string-error-map",
        "array-error-map-http", "string-sql-capabilities", "integer-sql-limits",
        "input-stored-outside-the-connection-scopes",
    ],
)
def test_a_misshapen_block_is_refused(document, path, error_type):
    refusal_at(parse_connector, document, path, error_type)
