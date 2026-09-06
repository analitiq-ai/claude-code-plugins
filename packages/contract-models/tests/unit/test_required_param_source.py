"""RULE-ENDP-066 — a required param the document can never fill.

The record carries the obligation and the argument;
`_validate_required_params_have_a_source` carries the reasoning about where it
runs and what it does not prove. What is here is the behaviour: which documents
the refusal takes, which it leaves alone, and which of the neighbouring checks
speaks first when more than one has something to say.

RULE-ENDP-067 names what the flag asks of the author. Nothing applies it and
nothing here tests it, which is why it is a separate record.
"""
import pytest
from pydantic import ValidationError
from typing import get_args

from analitiq.contracts.endpoints import (
    ApiEndpointDoc,
    Cursor,
    CursorMapping,
    Pagination,
    Param,
)
from analitiq.contracts.shared.rules import all_rules


API_SCHEMA_URL = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"

# `violation` prefixes the record's statement, which for this rule already
# names every source a read has. Asserting on the whole message would pass on
# the statement alone, so the detail is split off and pinned on its own.
# Keyed rather than searched: a registry that has lost the record should fail
# as a missing key, not as an exhausted generator.
_RECORDS = {record.id: record for record in all_rules()}
_STATEMENT = " ".join(_RECORDS["RULE-ENDP-066"].statement.split())


def _read_payload(params, *, query=None, path="/v1/records", path_params=None):
    """A minimal read binding each declared param into the request."""
    request = {
        "method": "GET",
        "path": path,
        "query": query if query is not None else {
            name: {"from_param": name} for name in params
        },
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
    """The finding's own words, with the quoted rule statement stripped.

    Stops at the finding's closing paren: pydantic's `input_value=` footer
    echoes a truncated repr of the document, so a `in detail` assertion run
    over the whole message could be satisfied by the payload rather than by
    the diagnostic.
    """
    message = _message(payload)
    _, sep, detail = message.partition(f"[RULE-ENDP-066] {_STATEMENT} (")
    assert sep, message
    finding, footer, _ = detail.partition(") [type=")
    assert footer, message
    return finding


# --- The refusal ------------------------------------------------------------


def test_a_required_read_param_with_no_source_is_refused():
    assert "params['since']" in _detail(_one_read_param(
        {"in": "query", "type": "string", "required": True}
    ))


def test_the_detail_names_every_way_out_a_read_has():
    detail = _detail(_one_read_param({"in": "query", "type": "string", "required": True}))
    for way_out in ("`default`", "`operators`", "pagination block",
                    "`incremental`", "not required"):
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


@pytest.mark.parametrize("falsy", [False, 0, "", []])
def test_a_falsy_default_is_a_source(falsy):
    """A declared `false`, `0`, empty string or empty list is a value the
    author chose. The check does not test truthiness — doing so would keep
    every other case here green while newly refusing ordinary documents.
    """
    ApiEndpointDoc.model_validate(_one_read_param({
        "in": "query", "type": "boolean", "required": True, "default": falsy,
    }))


@pytest.mark.parametrize("default", [
    {"literal": None}, {}, {"from_param": "other"},
], ids=["literal-null", "empty-object", "binding-form"])
def test_a_default_the_resolver_cannot_turn_into_a_value_is_not_a_source(default):
    """Three shapes are non-null and still arrive as nothing: the resolver
    unwraps `{"literal": null}` back to null, and it implements neither the
    empty object nor a binding form, so both return nothing. Reading them as
    present is the same walkaround an authored `null` would be, spelled longer.
    """
    assert "params['since']" in _detail(_one_read_param({
        "in": "query", "type": "string", "required": True, "default": default,
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


def _replicated(params, cursor_mappings, methods=("incremental",)):
    payload = _read_payload(params)
    read = payload["operations"]["read"]
    read["replication"] = {
        "supported_methods": list(methods),
        "cursor_mappings": cursor_mappings,
    }
    read["response"]["schema"]["items"]["properties"] = {
        "updated_at": {"type": "string"},
    }
    return payload


_CURSOR_PARAM = {"in": "query", "type": "string", "required": True,
                 "controlled_by": "replication"}


def test_a_window_cursor_mapping_fills_both_ends():
    ApiEndpointDoc.model_validate(_replicated(
        {"since": dict(_CURSOR_PARAM), "until": dict(_CURSOR_PARAM)},
        [{"cursor_field": "updated_at", "start_param": "since",
          "start_operator": "gte", "end_param": "until", "end_operator": "lt"}],
    ))


def test_a_block_that_supports_only_full_refresh_fills_nothing():
    """A cursor value comes from the state a previous incremental run left, so
    a block that never runs incrementally names its params and fills none."""
    detail = _detail(_replicated(
        {"since": dict(_CURSOR_PARAM)},
        [{"cursor_field": "updated_at", "param": "since", "operator": "gte"}],
        methods=("full_refresh",),
    ))
    assert "params['since']" in detail


def test_a_block_supporting_both_methods_still_fills_its_cursor():
    """`full_refresh` beside `incremental` is the ordinary shape the spec
    teaches; the gate asks whether any run leaves a cursor, not whether every
    one does."""
    ApiEndpointDoc.model_validate(_replicated(
        {"since": dict(_CURSOR_PARAM)},
        [{"cursor_field": "updated_at", "param": "since", "operator": "gte"}],
        methods=("full_refresh", "incremental"),
    ))


def test_pagination_and_replication_fill_their_own_params_in_one_document():
    """A paginated incremental read is as ordinary as this contract gets, and
    each param is sourced by a different block — so the two sets have to be
    unioned, not replaced."""
    payload = _replicated(
        {"since": dict(_CURSOR_PARAM),
         "per_page": {"in": "query", "type": "integer", "required": True,
                      "controlled_by": "pagination"}},
        [{"cursor_field": "updated_at", "param": "since", "operator": "gte"}],
    )
    read = payload["operations"]["read"]
    read["pagination"] = {
        "type": "link",
        "link": {"next_url": {"ref": "response.body.next"}},
        "limit": {"param": "per_page", "default": 50},
        "stop_when": {"empty": {"ref": "response.body.rows"}},
    }
    read["response"]["records"] = {"ref": "response.body.rows"}
    read["response"]["schema"] = {
        "$schema": JSON_SCHEMA,
        "type": "object",
        "properties": {
            "next": {"type": "string"},
            "rows": {"type": "array", "items": {
                "type": "object",
                "properties": {"updated_at": {"type": "string"}}}},
        },
    }
    ApiEndpointDoc.model_validate(payload)


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


def _paginated(param, block):
    payload = _one_read_param(param)
    block.setdefault("stop_when", {"empty": {"ref": "response.body.rows"}})
    read = payload["operations"]["read"]
    read["pagination"] = block
    read["response"]["records"] = {"ref": "response.body.rows"}
    read["response"]["schema"] = {
        "$schema": JSON_SCHEMA,
        "type": "object",
        "properties": {
            "next": {"type": "string"},
            "rows": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"id": {"type": "string"}}},
            },
        },
    }
    return payload


_CONTROLLED = {"in": "query", "type": "integer", "required": True,
               "controlled_by": "pagination"}
_INCREMENT = {"ref": "response.record_count"}


@pytest.mark.parametrize("block", [
    {"type": "offset",
     "offset": {"param": "since", "initial": 0, "increment_by": _INCREMENT}},
    {"type": "page",
     "page": {"param": "since", "initial": 1, "increment_by": 1}},
    {"type": "keyset",
     "keyset": {"param": "since", "order_by_field": "id", "initial": 0}},
    {"type": "link", "link": {"next_url": {"ref": "response.body.next"}},
     "limit": {"param": "since", "default": 50}},
    {"type": "link", "link": {"next_url": {"ref": "response.body.next"}},
     "limit": {"param": "since", "default": {"ref": "runtime.batch_size"}}},
    {"type": "cursor", "cursor": {"param": "page_token",
                                  "next_cursor": {"ref": "response.body.next"}},
     "limit": {"param": "since", "default": 50}},
], ids=["offset", "page", "keyset-with-initial", "link-limit",
        "link-limit-expression", "cursor-limit"])
def test_a_block_that_declares_a_starting_value_is_a_source(block):
    """Each strategy reaches the returned set by its own branch, and the branch
    is now what decides acceptance rather than only triggering a check — so a
    branch left out refuses a working document instead of missing one."""
    payload = _paginated(dict(_CONTROLLED), block)
    if block["type"] == "cursor":
        payload["operations"]["read"]["params"]["page_token"] = {
            "in": "query", "type": "string", "required": False,
            "controlled_by": "pagination",
        }
        payload["operations"]["read"]["request"]["query"]["page_token"] = {
            "from_param": "page_token"}
    ApiEndpointDoc.model_validate(payload)


@pytest.mark.parametrize("block", [
    {"type": "offset",
     "offset": {"param": "page", "initial": 0, "increment_by": _INCREMENT},
     "limit": {"param": "since"}},
    {"type": "keyset", "keyset": {"param": "since", "order_by_field": "id"}},
    {"type": "cursor",
     "cursor": {"param": "since", "next_cursor": {"ref": "response.body.next"}}},
    {"type": "offset",
     "offset": {"param": "since", "initial": None, "increment_by": _INCREMENT}},
    {"type": "page",
     "page": {"param": "since", "initial": None, "increment_by": 1}},
    {"type": "offset",
     "offset": {"param": "since", "initial": {"literal": None},
                "increment_by": _INCREMENT}},
    {"type": "keyset",
     "keyset": {"param": "since", "order_by_field": "id",
                "initial": {"literal": None}}},
], ids=["limit-without-default", "keyset-without-initial", "opaque-cursor",
        "offset-initial-null", "page-initial-null",
        "offset-initial-literal-null", "keyset-initial-literal-null"])
def test_a_block_that_names_the_param_and_gives_it_nothing_is_not_a_source(block):
    """Naming a param says which slot the strategy drives, not that the
    document put a value there. Counting a mention would be worse than not
    checking at all: the finding names the blocks as a way out, so an author
    would add `limit: {"param": <name>}`, go green, and change nothing.
    """
    payload = _paginated(dict(_CONTROLLED), block)
    if block["type"] == "offset":
        payload["operations"]["read"]["params"]["page"] = {
            "in": "query", "type": "integer", "required": False,
            "controlled_by": "pagination",
        }
        payload["operations"]["read"]["request"]["query"]["page"] = {
            "from_param": "page"}
    assert "params['since']" in _detail(payload)


def test_the_detail_says_the_pagination_block_gives_the_param_nothing():
    """An author who reached for a block and got no value is not helped by
    being told to reach for one."""
    detail = _detail(_paginated(dict(_CONTROLLED), {
        "type": "keyset", "keyset": {"param": "since", "order_by_field": "id"},
    }))
    assert "controlled_by: pagination" in detail
    assert "gives it no starting value" in detail


def test_the_detail_says_a_marker_no_block_names_is_backed_by_nothing():
    detail = _detail(_one_read_param({
        "in": "query", "type": "string", "required": True,
        "controlled_by": "pagination",
    }))
    assert "no block names it" in detail


def test_the_detail_sends_a_replication_author_to_supported_methods():
    """`Replication` declares no starting value anywhere, so telling this
    author to declare one names a key the contract does not have."""
    detail = _detail(_replicated(
        {"since": dict(_CURSOR_PARAM)},
        [{"cursor_field": "updated_at", "param": "since", "operator": "gte"}],
        methods=("full_refresh",),
    ))
    assert "`incremental`" in detail
    assert "starting value" not in detail


def test_a_marker_carrying_param_is_not_told_to_declare_operators():
    """`Param` forbids `operators` beside `controlled_by`, so offering it here
    would cost the author a round trip to a different refusal."""
    detail = _detail(_one_read_param({
        "in": "query", "type": "string", "required": True,
        "controlled_by": "replication",
    }))
    assert "`operators`" not in detail


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


def test_a_write_marker_does_not_divert_the_author_to_the_block():
    """A marker on a write param is backed by nothing and can never be — a
    write declares no block. But the fix RULE-ENDP-066 wants is still the
    `default`, so the write branch answers first and does not send the author
    off to a block that cannot exist. That the marker is illegal there at all
    is a different obligation than this one.
    """
    detail = _detail(_write_payload({
        "in": "query", "type": "string", "required": True,
        "controlled_by": "pagination",
    }))
    assert "give it a `default`" in detail
    assert "block" not in detail


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


def test_a_param_bound_twice_is_reported_as_bound_twice():
    """The other half of the ordering the check moved for: a param with two
    slots has a binding defect, and its own diagnostic names it."""
    payload = _read_payload(
        {"since": {"in": "query", "type": "string", "required": True}},
        query={"a": {"from_param": "since"}, "b": {"from_param": "since"}},
    )
    message = _message(payload)
    assert "referenced by 2 request bindings" in message
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
    assert "RULE-ENDP-028" in message
    assert "RULE-ENDP-066" not in message


# --- The sets the rule closes ----------------------------------------------


def test_a_field_added_to_param_puts_this_rule_in_front_of_a_reader():
    """The check, the record and the rendered reference each say which fields a
    param's value can arrive through. A field landing on `Param` either makes
    something else a source — in which case the refusal starts rejecting
    documents that now work, and those sentences are false with no word changed
    — or it does not, and nothing else here can tell the two apart. Pinning the
    members is what makes the addition ask.
    """
    assert set(Param.model_fields) == {
        "location", "type", "required", "description", "default", "enum",
        "format", "pattern", "minimum", "maximum", "min_length", "max_length",
        "min_items", "max_items", "operators", "controlled_by", "style",
        "explode",
    }


def test_an_opaque_cursor_still_has_no_field_for_a_starting_value():
    """`CursorPagination` is the one strategy that fills nothing, and the
    reason is that `Cursor` has no field to declare a first-page value in. A
    field added there makes it a source, and the branch that skips it becomes a
    refusal of a working document with nothing red.
    """
    assert set(Cursor.model_fields) == {"param", "next_cursor"}


@pytest.mark.parametrize("union, members", [
    (Pagination, {"OffsetPagination", "PagePagination", "CursorPagination",
                  "LinkPagination", "KeysetPagination"}),
    (CursorMapping, {"SingleCursorMapping", "WindowCursorMapping"}),
], ids=["pagination", "cursor-mapping"])
def test_a_new_strategy_puts_the_controlled_set_in_front_of_a_reader(union, members):
    """The wiring helpers walk these unions with an `isinstance` chain and no
    `else`, and what they return now decides whether a param has a source. A
    member added without a branch is not a missed check — it is a working
    document refused, with no branch left to fail. Only reading the new member
    can say whether it declares a value a param starts from.
    """
    branches = get_args(get_args(union)[0])
    assert {get_args(b)[0].__name__ for b in branches} == members
