"""RULE-ENDP-066 — a required param must have something that can fill it.

`required` says the operation is wrong without the value. The document is what
has to be able to supply it, and there is a closed set of ways it can: the
param's own `default`, a stream filter — which declaring `operators` is what
opens — or pagination/replication, which `controlled_by` hands it to. The last
two are read-side, so a write param has only its `default`, which is the same
argument RULE-ENDP-028 already makes one slot narrower.

A required param declaring none of them is empty on every request the operation
sends, while the document says in the same breath that the operation is wrong
that way. These tests pin the refusal, the ways out, and the latitude an
*optional* param keeps — a param nothing can fill is legal when the document
never claimed the operation needed it.

RULE-ENDP-067 names what the flag commits the run to; nothing applies it, and
nothing here tests it, which is the point of it being a separate record.
"""
import pytest
from pydantic import ValidationError

from analitiq.contracts.endpoints import ApiEndpointDoc


API_SCHEMA_URL = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def _read_payload(param, *, endpoint_id="records"):
    """A minimal read binding one query param named `since`."""
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": endpoint_id,
        "operations": {
            "read": {
                "params": {"since": param},
                "request": {
                    "method": "GET",
                    "path": "/v1/records",
                    "query": {"since": {"from_param": "since"}},
                },
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


def _write_payload(param):
    """A minimal create binding one query param named `since`."""
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": "records",
        "operations": {
            "write": {
                "insert": {
                    "params": {"since": param},
                    "request": {
                        "method": "POST",
                        "path": "/v1/records",
                        "query": {"since": {"from_param": "since"}},
                        "body": {"name": {"from_input": "record.name"}},
                    },
                    "input": {
                        "schema": {
                            "$schema": JSON_SCHEMA,
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                        },
                    },
                },
            },
        },
    }


def _refusal(payload):
    with pytest.raises(ValidationError) as exc:
        ApiEndpointDoc.model_validate(payload)
    return str(exc.value)


# --- The refusal ------------------------------------------------------------


def test_required_read_param_with_no_source_is_refused():
    message = _refusal(
        _read_payload({"in": "query", "type": "string", "required": True})
    )
    assert "RULE-ENDP-066" in message
    assert "'since'" in message


def test_the_refusal_names_every_way_out_a_read_has():
    message = _refusal(
        _read_payload({"in": "query", "type": "string", "required": True})
    )
    for way_out in ("`default`", "`operators`", "`controlled_by`", "not required"):
        assert way_out in message


def test_an_authored_null_default_is_not_a_source():
    """`"default": null` is an expression that resolves to nothing every run.

    An author who writes it has declared the key, not a value, so the param is
    as empty as one carrying no `default` at all — and the refusal has to read
    the two the same or the rule is walked around by typing four characters.
    """
    message = _refusal(
        _read_payload({"in": "query", "type": "string", "required": True, "default": None})
    )
    assert "RULE-ENDP-066" in message


# --- The ways out -----------------------------------------------------------


def test_a_default_is_a_source():
    ApiEndpointDoc.model_validate(
        _read_payload({
            "in": "query", "type": "string", "required": True,
            "default": {"ref": "connection.parameters.since"},
        })
    )


def test_a_literal_default_is_a_source():
    ApiEndpointDoc.model_validate(
        _read_payload({
            "in": "query", "type": "string", "required": True, "default": "1970-01-01",
        })
    )


def test_operators_are_a_source_a_stream_filter_fills():
    ApiEndpointDoc.model_validate(
        _read_payload({
            "in": "query", "type": "string", "required": True, "operators": ["gte"],
        })
    )


def test_controlled_by_is_a_source_replication_fills():
    payload = _read_payload({
        "in": "query", "type": "string", "required": True,
        "controlled_by": "replication",
    })
    read = payload["operations"]["read"]
    read["replication"] = {
        "supported_methods": ["incremental"],
        "cursor_mappings": [
            {"cursor_field": "updated_at", "param": "since", "operator": "gte"},
        ],
    }
    read["response"]["schema"]["items"]["properties"] = {
        "updated_at": {"type": "string"},
    }
    ApiEndpointDoc.model_validate(payload)


# --- The latitude an optional param keeps -----------------------------------


def test_an_optional_param_with_no_source_is_left_alone():
    """The rule grades a contradiction, not a dead param.

    An optional param nothing fills sends nothing, which is what the document
    said it wanted. Only `required` turns that into the document disagreeing
    with itself, so only `required` is refused.
    """
    ApiEndpointDoc.model_validate(
        _read_payload({"in": "query", "type": "string", "required": False})
    )


# --- Writes have one source ------------------------------------------------


def test_a_required_write_param_needs_a_default():
    message = _refusal(
        _write_payload({"in": "query", "type": "string", "required": True})
    )
    assert "RULE-ENDP-066" in message


def test_the_write_refusal_offers_only_the_default():
    """`operators` and `controlled_by` are read-side, so naming them here would
    send a write author to fix the document in a way that cannot work."""
    message = _refusal(
        _write_payload({"in": "query", "type": "string", "required": True})
    )
    # The statement the finding quotes names every source a read has, so the
    # assertion reads the detail's own wording, which is what differs.
    assert "give it a `default`, or declare it not required" in message
    assert "declare the `operators` a stream may filter it with" not in message


def test_operators_do_not_fill_a_write_param():
    """A write has no stream filter, so `operators` on a write param opens
    nothing. The contract accepts the declaration; it is not a source, and a
    required write param carrying one and no `default` is still empty."""
    message = _refusal(
        _write_payload({
            "in": "query", "type": "string", "required": True, "operators": ["gte"],
        })
    )
    assert "RULE-ENDP-066" in message


def test_a_write_default_is_a_source():
    ApiEndpointDoc.model_validate(
        _write_payload({
            "in": "query", "type": "string", "required": True,
            "default": {"ref": "connection.parameters.since"},
        })
    )
