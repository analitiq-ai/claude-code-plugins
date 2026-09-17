"""Every expression an API endpoint authors carries the payload its own form
models (RULE-ENDP-022; the connector document's counterpart is RULE-CTOR-057,
RULE-CTOR-068 and RULE-CTOR-069).

The record carries the rule and its why. What these tests pin is the gap the
widening closes: `Expression`'s members say a `ref` and a `template` are
strings and a `function` is a name plus the arguments it models, but the
request slots are typed `Any`, so the union never reaches the document as
authored and the resolver was handed payloads no form describes.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.endpoints import parse_endpoint

API_SCHEMA_URL = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def _read_doc(**request_extras):
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": "records",
        "operations": {
            "read": {
                "request": {"method": "GET", "path": "/v1/records", **request_extras},
                "params": {},
                "response": {
                    "records": {"ref": "response.body"},
                    "schema": {
                        "$schema": JSON_SCHEMA,
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
            },
        },
    }


def _write_doc(**request_extras):
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": "records",
        "operations": {
            "write": {
                "insert": {
                    "request": {
                        "method": "POST",
                        "path": "/v1/records",
                        "body": {"from_input": "record"},
                        **request_extras,
                    },
                    "input": {
                        "schema": {
                            "$schema": JSON_SCHEMA,
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                        },
                    },
                },
            },
        },
    }


# --- a ref is a string ------------------------------------------------------


@pytest.mark.parametrize(
    "payload", [5, ["connection.parameters.a"], {"scope": "connection"}, None, True]
)
def test_a_ref_that_is_not_a_string_is_refused(payload):
    with pytest.raises(ValidationError) as exc:
        parse_endpoint(_read_doc(query={"q": {"ref": payload}}))
    message = str(exc.value)
    assert "request.query" in message and "ref" in message


def test_a_ref_that_is_a_string_still_authors():
    parse_endpoint(_read_doc(query={"q": {"ref": "connection.parameters.a"}}))


# --- a template is a string, and closes what it opens -----------------------


@pytest.mark.parametrize("payload", [7, {"a": 1}, ["${connection.parameters.a}"], None])
def test_a_template_that_is_not_a_string_is_refused(payload):
    with pytest.raises(ValidationError) as exc:
        parse_endpoint(_read_doc(headers={"X-K": {"template": payload}}))
    assert "request.headers" in str(exc.value)


def test_a_template_leaving_a_placeholder_open_is_refused():
    with pytest.raises(ValidationError) as exc:
        parse_endpoint(
            _read_doc(headers={"X-K": {"template": "Bearer ${secrets.token"}})
        )
    assert "${" in str(exc.value)


def test_a_bare_string_leaving_a_placeholder_open_is_refused():
    # A bare string IS a template — `resolve_template_deep` interpolates it —
    # so the open `${` is the same defect with no dict around it.
    with pytest.raises(ValidationError) as exc:
        parse_endpoint(_read_doc(headers={"X-K": "Bearer ${secrets.token"}))
    assert "${" in str(exc.value)


def test_a_closed_placeholder_still_authors():
    parse_endpoint(_read_doc(headers={"X-K": "Bearer ${secrets.token}"}))


def test_an_open_placeholder_inside_a_literal_is_left_alone():
    # `literal` is opt-out: the resolver returns the payload verbatim, so the
    # `${` is data and never interpolated.
    parse_endpoint(_read_doc(headers={"X-K": {"literal": "cost ${"}}))


# --- a function names itself, and carries the arguments it models -----------


@pytest.mark.parametrize("name", [5, "", None, ["url_encode"]])
def test_a_function_without_a_usable_name_is_refused(name):
    with pytest.raises(ValidationError) as exc:
        parse_endpoint(_read_doc(query={"q": {"function": name, "input": "x"}}))
    assert "function" in str(exc.value)


def test_a_function_map_that_is_not_a_table_is_refused():
    with pytest.raises(ValidationError) as exc:
        parse_endpoint(
            _read_doc(query={"q": {"function": "lookup", "input": "x", "map": ["a"]}})
        )
    assert "map" in str(exc.value)


def test_a_function_safe_that_is_not_a_string_is_refused():
    with pytest.raises(ValidationError) as exc:
        parse_endpoint(
            _read_doc(query={"q": {"function": "url_encode", "input": "x", "safe": 5}})
        )
    assert "safe" in str(exc.value)


def test_a_documented_function_still_authors():
    parse_endpoint(
        _read_doc(
            query={
                "q": {
                    "function": "url_encode",
                    "input": {"ref": "connection.parameters.a"},
                    "safe": "/",
                }
            }
        )
    )


def test_an_extension_sibling_on_a_function_still_authors():
    parse_endpoint(
        _read_doc(query={"q": {"function": "url_encode", "input": "x", "x-why": "note"}})
    )


# --- the write side is the same document, so it is graded the same ----------


def test_a_write_request_ref_that_is_not_a_string_is_refused():
    with pytest.raises(ValidationError) as exc:
        parse_endpoint(_write_doc(headers={"X-K": {"ref": 5}}))
    assert "operations.write.request.headers" in str(exc.value)


def test_a_write_request_template_leaving_a_placeholder_open_is_refused():
    with pytest.raises(ValidationError) as exc:
        parse_endpoint(_write_doc(headers={"X-K": "Bearer ${secrets.token"}))
    assert "${" in str(exc.value)
