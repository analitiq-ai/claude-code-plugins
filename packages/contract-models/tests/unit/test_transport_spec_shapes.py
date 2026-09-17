"""A connector's transport blocks are refused when their shape is wrong.

Each case bends one field of an otherwise valid connector document and
validates the whole document, so the refusal is the one a connector author
meets, located at the bent field.
"""
from __future__ import annotations

import pytest

from analitiq.contracts.connector import parse_connector

from _contract_documents import API_CONNECTOR, DATABASE_CONNECTOR, DSN, bent
from _contract_refusals import refusal_at

HOST_REF = {"ref": "connection.parameters.host"}


def _http(**fields):
    return bent(API_CONNECTOR, lambda d: d["transports"]["api"].update(fields))


def _sqlalchemy(**fields):
    return bent(DATABASE_CONNECTOR, lambda d: d["transports"]["database"].update(fields))


def _dsn(**fields):
    return bent(DATABASE_CONNECTOR, lambda d: d["transports"]["database"]["dsn"].update(fields))


def _host_binding(binding):
    return bent(
        DATABASE_CONNECTOR,
        lambda d: d["transports"]["database"]["dsn"]["bindings"].update(host=binding),
    )


def _adbc(**fields):
    return bent(
        DATABASE_CONNECTOR,
        lambda d: d["transports"].update(adbc={"transport_type": "adbc", **fields}),
    )


@pytest.mark.parametrize(
    "document",
    [
        API_CONNECTOR,
        DATABASE_CONNECTOR,
        _http(rate_limit={"max_requests": 10, "time_window_seconds": 60}),
        _http(headers={"Accept": "application/json"}),
        _http(base_url={"literal": "https://api.example.test"}),
        _sqlalchemy(options={"pool_size": 5}),
        _sqlalchemy(tls={"mode": {"literal": "require"}}),
        _adbc(driver="postgresql", dsn=DSN),
        _adbc(driver="postgresql", db_kwargs={"uri": HOST_REF}),
    ],
    ids=[
        "api", "database", "rate-limit", "headers", "base-url-literal",
        "options", "tls", "adbc-dsn", "adbc-db-kwargs",
    ],
)
def test_a_well_shaped_transport_is_accepted(document):
    parse_connector(document)


@pytest.mark.parametrize(
    "document, path, error_type",
    [
        (_http(base_url=""), ("base_url",), "string_too_short"),
        (_http(base_url={"literal": ""}), ("base_url",), "string_too_short"),
        (_http(headers=["Authorization: Bearer x"]), ("headers",), "dict_type"),
        (_http(rate_limit=[10, 60]), ("rate_limit",), "model_type"),
        (_http(rate_limit={"max_requests": 10}), ("rate_limit", "time_window_seconds"), "missing"),
        (_http(rate_limit={"time_window_seconds": 60}), ("rate_limit", "max_requests"), "missing"),
    ],
    ids=[
        "empty-base-url", "empty-base-url-literal", "non-object-headers",
        "non-object-rate-limit", "rate-limit-without-window",
        "rate-limit-without-max-requests",
    ],
)
def test_a_misshapen_http_transport_is_refused(document, path, error_type):
    refusal_at(parse_connector, document, ("transports", "api", *path), error_type)


@pytest.mark.parametrize(
    "document, path, error_type",
    [
        (_sqlalchemy(options="pool_size=5"), ("options",), "dict_type"),
        (_sqlalchemy(options=[]), ("options",), "dict_type"),
        (_sqlalchemy(tls="require"), ("tls",), "model_type"),
        (_dsn(kind="unknown"), ("dsn", "kind"), "literal_error"),
        (_dsn(template=""), ("dsn", "template"), "string_too_short"),
        (
            _host_binding({"value": HOST_REF}),
            ("dsn", "bindings", "host", "encoding"),
            "missing",
        ),
        (
            _host_binding({"value": HOST_REF, "encoding": "rot13"}),
            ("dsn", "bindings", "host", "encoding"),
            "literal_error",
        ),
    ],
    ids=[
        "string-options", "array-options", "string-tls", "unknown-dsn-kind",
        "empty-dsn-template", "binding-without-encoding", "binding-unknown-encoding",
    ],
)
def test_a_misshapen_sqlalchemy_transport_is_refused(document, path, error_type):
    refusal_at(parse_connector, document, ("transports", "database", *path), error_type)


@pytest.mark.parametrize(
    "document, path, error_type",
    [
        (_adbc(driver="postgresql", dsn="postgresql://host/db"), ("dsn",), "model_type"),
        (_adbc(driver="postgresql", db_kwargs=["not", "a", "mapping"]), ("db_kwargs",), "dict_type"),
        (_adbc(db_kwargs={"uri": HOST_REF}), ("driver",), "missing"),
    ],
    ids=["string-dsn", "array-db-kwargs", "no-driver"],
)
def test_a_misshapen_adbc_transport_is_refused(document, path, error_type):
    refusal_at(parse_connector, document, ("transports", "adbc", *path), error_type)
