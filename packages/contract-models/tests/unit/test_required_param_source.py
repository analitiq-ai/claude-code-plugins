"""RULE-ENDP-066 — a required param the document can never fill.

The record carries the obligation and the argument;
`_validate_required_params_have_a_source` carries the reasoning about where it
runs and what it does not prove. What is here is the behaviour: which documents
the refusal takes, which it leaves alone, and which of the neighbouring checks
speaks first when more than one has something to say.

RULE-ENDP-067 names what the flag commits the run to. Nothing applies it and
nothing here tests it, which is why it is a separate record.
"""
import pytest
from pydantic import ValidationError

from analitiq.contracts.endpoints import ApiEndpointDoc, Param
from analitiq.contracts.shared.rules import all_rules


API_SCHEMA_URL = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"

# `violation` prefixes the record's statement, which for this rule already
# names every source a read has. Asserting on the whole message would pass on
# the statement alone, so the detail is split off and pinned on its own.
_STATEMENT = " ".join(
    next(r for r in all_rules() if r.id == "RULE-ENDP-066").statement.split()
)


def _read_payload(params, *, query=None, path="/v1/records", path_params=None):
    """A minimal read binding each declared param into the request."""
    request = {"method": "GET", "path": path}
    request["query"] = query if query is not None else {
        name: {"from_param": name} for name in params
    }
    if path_params is not None:
        request["path_params"] = path_params
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": "records",
        "operations": {
            "read": {
                "params": params,
                "request": request,
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


def _one_read_param(param):
    return _read_payload({"since": param})


def _write_payload(param, *, slot="query"):
    """A minimal insert binding one param into `slot`."""
    request = {
        "method": "POST",
        "path": "/v1/records",
        "body": {"name": {"from_input": "record.name"}},
    }
    if slot == "query":
        request["query"] = {"since": {"from_param": "since"}}
    elif slot == "body":
        request["body"]["since"] = {"from_param": "since"}
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": "records",
        "operations": {
            "write": {
                "insert": {
                    "params": {"since": param},
                    "request": request,
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


def _message(payload):
    with pytest.raises(ValidationError) as exc:
        ApiEndpointDoc.model_validate(payload)
    return str(exc.value)


def _detail(payload):
    """The finding's own words, with the quoted rule statement stripped."""
    message = _message(payload)
    _, sep, detail = message.partition(f"[RULE-ENDP-066] {_STATEMENT} (")
    assert sep, message
    return detail


# --- The refusal ------------------------------------------------------------


def test_a_required_read_param_with_no_source_is_refused():
    assert "params['since']" in _detail(_one_read_param(
        {"in": "query", "type": "string", "required": True}
    ))


def test_the_detail_names_every_way_out_a_read_has():
    detail = _detail(_one_read_param({"in": "query", "type": "string", "required": True}))
    for way_out in ("`default`", "`operators`", "pagination or replication", "not required"):
        assert way_out in detail


def test_a_required_path_param_with_no_source_is_refused():
    """The shape the rule was written for: a segment the document fills from
    nothing. A location-blind loop is what makes this the same case as a query
    param, and narrowing it to the query walk would drop it."""
    detail = _detail(_read_payload(
        {"account_id": {"in": "path", "type": "string", "required": True}},
        path="/v1/accounts/{account_id}/invoices",
        query={},
        path_params={"account_id": {"from_param": "account_id"}},
    ))
    assert "params['account_id']" in detail


def test_every_offending_param_is_named_in_one_finding():
    """An authoring agent fixes what the finding names, so naming one of two
    costs a whole validate-fix round for the other."""
    detail = _detail(_read_payload({
        "since": {"in": "query", "type": "string", "required": True},
        "until": {"in": "query", "type": "string", "required": True},
        "page": {"in": "query", "type": "integer", "required": True, "default": 1},
    }))
    assert "params['since']" in detail
    assert "params['until']" in detail
    assert "params['page']" not in detail


def test_an_authored_null_default_is_not_a_source():
    """`"default": null` is an expression that resolves to nothing every run.

    An author who writes it has declared the key, not a value, so the param is
    as empty as one carrying no `default` — and the refusal has to read the two
    the same or the rule is walked around by typing four characters.
    """
    assert "params['since']" in _detail(_one_read_param(
        {"in": "query", "type": "string", "required": True, "default": None}
    ))


def test_a_controlled_by_marker_no_block_backs_is_not_a_source():
    """`controlled_by` is self-declared, and only the block→param direction is
    checked anywhere. A param carrying a marker no block names — a typo, or a
    param copied off a paginated endpoint — is handed a value by nothing."""
    for owner in ("pagination", "replication"):
        detail = _detail(_one_read_param({
            "in": "query", "type": "string", "required": True, "controlled_by": owner,
        }))
        assert "params['since']" in detail


# --- The ways out -----------------------------------------------------------


def test_a_ref_default_is_a_source():
    ApiEndpointDoc.model_validate(_one_read_param({
        "in": "query", "type": "string", "required": True,
        "default": {"ref": "connection.parameters.since"},
    }))


def test_a_literal_default_is_a_source():
    ApiEndpointDoc.model_validate(_one_read_param({
        "in": "query", "type": "string", "required": True, "default": "1970-01-01",
    }))


@pytest.mark.parametrize("falsy", [False, 0, "", [], {}])
def test_a_falsy_default_is_a_source(falsy):
    """A declared `false`, `0` or empty string is a value the author chose.

    The check tests `default is None`, not truthiness — swapping the two keeps
    every other case here green while newly refusing ordinary documents.
    """
    ApiEndpointDoc.model_validate(_one_read_param({
        "in": "query", "type": "boolean", "required": True, "default": falsy,
    }))


def test_operators_are_a_source_a_stream_filter_can_fill():
    ApiEndpointDoc.model_validate(_one_read_param({
        "in": "query", "type": "string", "required": True, "operators": ["gte"],
    }))


def test_an_empty_operator_list_opens_nothing():
    """Absence means the param is not stream-filterable, and an empty list says
    the same thing in more characters."""
    assert "params['since']" in _detail(_one_read_param({
        "in": "query", "type": "string", "required": True, "operators": [],
    }))


def test_a_replication_block_naming_the_param_is_a_source():
    payload = _one_read_param({
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


def test_a_pagination_block_naming_the_param_is_a_source():
    payload = _one_read_param({
        "in": "query", "type": "integer", "required": True,
        "controlled_by": "pagination",
    })
    payload["operations"]["read"]["pagination"] = {
        "type": "offset",
        "offset": {
            "param": "since",
            "initial": 0,
            "increment_by": {"ref": "response.record_count"},
        },
        "stop_when": {"empty": {"ref": "response.body"}},
    }
    ApiEndpointDoc.model_validate(payload)


# --- The latitude an optional param keeps -----------------------------------


def test_an_optional_param_with_no_source_is_left_alone():
    """The rule grades a contradiction, not a dead param.

    An optional param nothing fills sends nothing, which is what the document
    said it wanted. Only `required` turns that into the document disagreeing
    with itself, so only `required` is refused.
    """
    ApiEndpointDoc.model_validate(_one_read_param(
        {"in": "query", "type": "string", "required": False}
    ))


# --- On a write the `default` is the whole set ------------------------------


def test_a_required_write_param_needs_a_default():
    assert "params['since']" in _detail(_write_payload(
        {"in": "query", "type": "string", "required": True}
    ))


def test_the_write_detail_offers_no_read_side_way_out():
    """A write has no stream filter and no pagination or replication block, so
    naming either would send the author to fix the document in a way that
    cannot work."""
    detail = _detail(_write_payload({"in": "query", "type": "string", "required": True}))
    assert "`default`" in detail
    assert "`operators`" not in detail
    assert "pagination or replication" not in detail


def test_the_write_detail_names_the_record_for_a_body_param():
    """The value a write puts in the body comes from the record, and the fix is
    to delete the param rather than to invent a `default` for it."""
    detail = _detail(_write_payload(
        {"in": "body", "type": "string", "required": True}, slot="body",
    ))
    assert "from_input" in detail


def test_operators_do_not_fill_a_write_param():
    """A write has no stream filter, so `operators` on a write param opens
    nothing. The contract accepts the declaration; it is not a source."""
    assert "params['since']" in _detail(_write_payload({
        "in": "query", "type": "string", "required": True, "operators": ["gte"],
    }))


def test_a_write_default_is_a_source():
    ApiEndpointDoc.model_validate(_write_payload({
        "in": "query", "type": "string", "required": True,
        "default": {"ref": "connection.parameters.since"},
    }))


# --- Which check speaks first ----------------------------------------------


def test_an_unbound_param_is_reported_as_unbound():
    """A param no binding names has no slot, so a finding about the slot being
    empty names the wrong defect. The binding checks run first and say so."""
    message = _message(_read_payload(
        {"since": {"in": "query", "type": "string", "required": True}}, query={},
    ))
    assert "not referenced by any request binding" in message
    assert "RULE-ENDP-066" not in message


def test_a_write_path_param_is_reported_by_the_rule_that_names_the_record():
    """RULE-ENDP-028 grades a write path_param whether or not it is required,
    and its diagnostic names the `from_input` binding. It runs inside the
    path_params walk, so it reaches the author first."""
    payload = _write_payload({"in": "path", "type": "string", "required": True})
    insert = payload["operations"]["write"]["insert"]
    insert["request"]["path"] = "/v1/records/{since}"
    insert["request"]["path_params"] = {"since": {"from_param": "since"}}
    insert["request"].pop("query")
    message = _message(payload)
    assert "declares no `default`" in message
    assert "RULE-ENDP-066" not in message


# --- The set the rule closes ------------------------------------------------


def test_a_field_added_to_param_puts_this_rule_in_front_of_a_reader():
    """The check, the record, the rendered reference and this module all say
    which fields a param's value can arrive through. A field landing on `Param`
    is either a fourth source — in which case the refusal starts rejecting
    documents that now work, and every one of those sentences is false with no
    word changed — or it is not, and nothing else here can tell the two apart.
    Pinning the members is what makes the addition ask.
    """
    assert set(Param.model_fields) == {
        "location", "type", "required", "description", "default", "enum",
        "format", "pattern", "minimum", "maximum", "min_length", "max_length",
        "min_items", "max_items", "operators", "controlled_by", "style",
        "explode",
    }
