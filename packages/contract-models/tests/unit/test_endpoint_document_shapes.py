"""An endpoint document is refused when a field it must declare is missing or
misshapen.

Each case bends one field of an otherwise valid API or database endpoint
document and parses the whole document.
"""
from __future__ import annotations

import pytest

from analitiq.contracts.endpoints import parse_endpoint

from _contract_documents import DATABASE_ENDPOINT, PAGINATED_API_ENDPOINT, bent
from _contract_refusals import refusal_at

READ = ("operations", "read")


def _read(bend):
    return bent(PAGINATED_API_ENDPOINT, lambda d: bend(d["operations"]["read"]))


def _request_headers(headers):
    return _read(lambda read: read["request"].update(headers=headers))


@pytest.mark.parametrize(
    "document",
    [
        PAGINATED_API_ENDPOINT,
        _request_headers({"X-Trace": {"literal": "x"}}),
        DATABASE_ENDPOINT,
    ],
    ids=["paginated-read", "request-header", "database"],
)
def test_a_well_shaped_endpoint_is_accepted(document):
    parse_endpoint(document)


@pytest.mark.parametrize(
    "document, path, error_type",
    [
        (
            _read(lambda read: read["pagination"].update(type="seek")),
            (*READ, "pagination"),
            "union_tag_invalid",
        ),
        (
            _read(lambda read: read["pagination"].pop("stop_when")),
            (*READ, "pagination", "stop_when"),
            "missing",
        ),
        (
            # `$` in a Python regex also matches before a trailing newline, so
            # a name anchored that way would pass this request-splitting shape.
            _request_headers({"X-Trace\n": {"literal": "x"}}),
            (*READ, "request", "headers", "X-Trace\n"),
            "string_pattern_mismatch",
        ),
        (
            bent(DATABASE_ENDPOINT, lambda d: d["database_object"].pop("name")),
            ("database_object", "name"),
            "missing",
        ),
        (
            bent(DATABASE_ENDPOINT, lambda d: d.update(columns=[])),
            ("columns",),
            "too_short",
        ),
    ],
    ids=[
        "unknown-pagination-type", "pagination-without-stop-condition",
        "header-name-ending-in-a-line-break", "database-object-without-name",
        "no-columns",
    ],
)
def test_a_misshapen_endpoint_is_refused(document, path, error_type):
    refusal_at(parse_endpoint, document, path, error_type)
