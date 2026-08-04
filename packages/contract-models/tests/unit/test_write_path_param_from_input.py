"""A write `path_params` binding may read the record being written (issue #125).

`PUT /Contact/{id}` — where `{id}` is the id of the record being written — was
contract-valid and unimplementable: `path_params` accepted only `{from_param}`
expressions, write params resolve through the connection/secrets/runtime scopes,
and *no scope reaches the record*. The shipped `sevdesk` connector declares this
shape in 21 of its 22 write endpoints; each one bound `{id}` to a param with no
`default`, so the placeholder could never be substituted. Once the engine began
honouring `path_params` (analitiq-engine#451) those 21 endpoints went from
silently wrong to loudly blocked, with no fix available inside the engine or the
connector.

The contract now permits `{"from_input": "record.<dotted>"}` in
`request.path_params`, **on write operations only**, binding directly with no
declared param. This file is the proof that the sevdesk shape validates, plus
the fence around it:

  * ADV-ENDP-024 — a write `path_param` may bind `record.<field>`, which must be
    declared in `input.schema`; a read `path_param` may not bind `from_input` at
    all (a read has no record).
  * ADV-ENDP-025 — `from_input` in `path_params` is mutually exclusive with
    `batching`: a path segment takes one record's value, and a multi-record
    request has no single record to take it from.

The must-not-regress half matters as much as the new capability: `path_params`
still accepts `{from_param}` (mixable with `{from_input}` in one path), param
binding uniqueness is untouched, and the batching *arity* rules stay a statement
about `request.body` alone.
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from analitiq.contracts.endpoints import (
    _REQUEST_EXPRESSION_SLOTS,
    ApiEndpointDoc,
    parse_endpoint,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
LATEST_API_ENDPOINT_SCHEMA_PATH = REPO_ROOT / "schemas" / "api-endpoint" / "latest.json"

API_SCHEMA_URL = "https://schemas.analitiq.ai/api-endpoint/latest.json"
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


# ---------------------------------------------------------------------------
# Payload factories
# ---------------------------------------------------------------------------


def _api_payload(write, endpoint_id="contact"):
    return {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": endpoint_id,
        "operations": {"write": write},
    }


def _record_schema(properties=None):
    return {
        "$schema": JSON_SCHEMA,
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}}
        if properties is None
        else properties,
    }


def _write_op(
    *,
    path="/Contact/{id}",
    path_params=None,
    method="PUT",
    body=None,
    params=None,
    properties=None,
    batching=None,
    conflict_keys=None,
    request_extras=None,
):
    """A per-record write mode, defaulting to the sevdesk `PUT /Contact/{id}` shape."""
    request = {
        "method": method,
        "path": path,
        "headers": {"Content-Type": "application/json"},
        "body": {"from_input": "record"} if body is None else body,
    }
    if path_params is not None:
        request["path_params"] = path_params
    elif "{" in path:
        request["path_params"] = {"id": {"from_input": "record.id"}}
    if request_extras:
        request.update(request_extras)

    op = {
        "request": request,
        "params": {} if params is None else params,
        "input": {"schema": _record_schema(properties)},
    }
    if batching is not None:
        op["batching"] = batching
    if conflict_keys is not None:
        op["conflict_keys"] = conflict_keys
    return op


# ---------------------------------------------------------------------------
# The headline case: sevdesk's `PUT /Contact/{id}` (issue #125)
# ---------------------------------------------------------------------------


class TestSevdeskPutContactById:
    """The exact document issue #125 says is unimplementable. It now validates."""

    # Verbatim from the issue's "Ask", fleshed out only with the surrounding
    # fields any write mode requires (a body that references the record, and the
    # `input.schema` that declares the record's fields).
    SEVDESK_UPDATE_CONTACT = {
        "$schema": API_SCHEMA_URL,
        "endpoint_id": "contact",
        "operations": {
            "write": {
                "upsert": {
                    "request": {
                        "method": "PUT",
                        "path": "/Contact/{id}",
                        "path_params": {"id": {"from_input": "record.id"}},
                        "headers": {"Content-Type": "application/json"},
                        "body": {"from_input": "record"},
                    },
                    "params": {},
                    "input": {
                        "schema": {
                            "$schema": JSON_SCHEMA,
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                            },
                        },
                    },
                    "conflict_keys": ["id"],
                },
            },
        },
    }

    def test_put_contact_by_id_validates(self):
        doc = parse_endpoint(self.SEVDESK_UPDATE_CONTACT)
        assert isinstance(doc, ApiEndpointDoc)
        upsert = doc.operations.write["upsert"]
        # The placeholder is bound straight to the record field — this is the
        # binding that had no expressible form before #125.
        assert upsert.request.path_params == {"id": {"from_input": "record.id"}}

    def test_the_binding_needs_no_declared_param(self):
        # Point 3 of the issue: `from_input` binds directly, so `in: path` is not
        # a required intermediary. `params` is empty and the document stands.
        doc = parse_endpoint(self.SEVDESK_UPDATE_CONTACT)
        assert doc.operations.write["upsert"].params == {}

    def test_published_json_schema_also_admits_it(self):
        # The models are one half of the contract; the rendered JSON Schema is
        # the half every non-Python consumer reads. Both must accept the shape.
        schema = json.loads(LATEST_API_ENDPOINT_SCHEMA_PATH.read_text())
        errors = list(Draft202012Validator(schema).iter_errors(self.SEVDESK_UPDATE_CONTACT))
        assert errors == [], [e.message for e in errors]

    @pytest.mark.parametrize("mode", ["insert", "upsert", "truncate_insert"])
    def test_accepted_on_every_write_mode(self, mode):
        # Nothing about the record scope is mode-specific; only `conflict_keys`
        # is, and that rule is unchanged.
        parse_endpoint(_api_payload({mode: _write_op(
            conflict_keys=["id"] if mode == "upsert" else None,
        )}))

    def test_nested_record_field_accepted(self):
        parse_endpoint(_api_payload({"insert": _write_op(
            path_params={"id": {"from_input": "record.external.id"}},
            properties={
                "external": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                },
            },
        )}))

    def test_two_placeholders_both_read_the_record(self):
        parse_endpoint(_api_payload({"insert": _write_op(
            path="/Contact/{id}/Address/{address_id}",
            path_params={
                "id": {"from_input": "record.id"},
                "address_id": {"from_input": "record.address_id"},
            },
            properties={
                "id": {"type": "string"},
                "address_id": {"type": "string"},
            },
        )}))


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


class TestFromInputInPathParamsRejected:
    """The fence: every shape the record scope must NOT reach."""

    def test_from_input_on_a_read_path_param_rejected(self):
        # Point 4 of the issue: a read has no record, so there is nothing for
        # `from_input` to resolve against. Unchanged ban, unchanged message.
        with pytest.raises(
            ValidationError, match=r"from_input is invalid in request\.path_params"
        ):
            parse_endpoint({
                "$schema": API_SCHEMA_URL,
                "endpoint_id": "contact",
                "operations": {"read": {
                    "request": {
                        "method": "GET",
                        "path": "/Contact/{id}",
                        "path_params": {"id": {"from_input": "record.id"}},
                    },
                    "params": {},
                    "response": {
                        "records": {"ref": "response.body"},
                        "schema": {"type": "array", "items": {"type": "object"}},
                    },
                }},
            })

    def test_from_input_path_param_with_batching_rejected(self):
        # ADV-ENDP-025, point 1 of the issue. Same argument as idempotency ×
        # batching: the value is per-record and one request carries many.
        with pytest.raises(
            ValidationError,
            match=r"from_input in request\.path_params cannot be combined with batching",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                body={"from_input": "records"},
                batching={"max_records": 100},
            )}))

    def test_whole_record_rejected(self):
        # A record is a JSON object; a path segment is one scalar.
        with pytest.raises(
            ValidationError,
            match=r"cannot bind `from_input: 'record'` — a whole record is not a single path segment",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"from_input": "record"}},
            )}))

    def test_records_rejected(self):
        with pytest.raises(
            ValidationError,
            match=r"cannot bind `from_input: 'records'` — a batch has no single value",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"from_input": "records"}},
            )}))

    def test_records_dotted_field_rejected(self):
        # `records.id` is a field of *each* record in a batch — still not one value.
        with pytest.raises(
            ValidationError,
            match=r"cannot bind `from_input: 'records\.id'` — a batch has no single value",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"from_input": "records.id"}},
            )}))

    def test_non_record_scope_rejected(self):
        # The record is the only scope `from_input` names; anything else is a
        # typo or a reach for a scope that belongs in a param.
        with pytest.raises(
            ValidationError,
            match=r"from_input value 'connection\.tenant' must be `record\.<dotted>`",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"from_input": "connection.tenant"}},
            )}))

    def test_field_not_declared_in_input_schema_rejected(self):
        # ADV-ENDP-024's membership half: the addressed field must exist in the
        # record the mode declares, and the error names the path_params site so
        # the author knows which of the two `from_input` surfaces is wrong.
        with pytest.raises(
            ValidationError,
            match=r"request\.path_params from_input 'record\.uuid' references undeclared "
                  r"input\.schema field 'record\.uuid'",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"from_input": "record.uuid"}},
            )}))

    def test_nested_field_not_declared_in_input_schema_rejected(self):
        with pytest.raises(
            ValidationError,
            match=r"request\.path_params from_input 'record\.external\.uuid' references "
                  r"undeclared input\.schema field 'record\.external\.uuid'",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"from_input": "record.external.uuid"}},
                properties={
                    "external": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                },
            )}))

    def test_from_input_still_rejected_in_write_headers(self):
        # `path_params` is the ONE new record-reading site. Headers are built
        # once per request; a record is per-row.
        with pytest.raises(
            ValidationError, match=r"from_input is invalid in request\.headers"
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                request_extras={"headers": {
                    "Content-Type": "application/json",
                    "X-Record-Id": {"from_input": "record.id"},
                }},
            )}))

    def test_from_input_still_rejected_in_write_query(self):
        with pytest.raises(
            ValidationError, match=r"from_input is invalid in request\.query"
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                request_extras={"query": {"id": {"from_input": "record.id"}}},
            )}))


# ---------------------------------------------------------------------------
# Must-not-regress
# ---------------------------------------------------------------------------


class TestPathParamBindingNotRegressed:
    """Everything `path_params` did before #125 it must still do."""

    def test_from_param_and_from_input_mix_in_one_path(self):
        # A path can need both: one segment from the record, one from a param
        # the connection defaults.
        doc = parse_endpoint(_api_payload({"insert": _write_op(
            path="/Contact/{id}/Note/{kind}",
            path_params={
                "id": {"from_input": "record.id"},
                "kind": {"from_param": "kind"},
            },
            params={"kind": {
                "in": "path", "type": "string", "required": True, "default": "memo",
            }},
        )}))
        request = doc.operations.write["insert"].request
        assert request.path_params["id"] == {"from_input": "record.id"}
        assert request.path_params["kind"] == {"from_param": "kind"}

    def test_declared_but_unreferenced_param_still_rejected(self):
        # Param binding uniqueness is untouched: a `from_input` binding does not
        # count as "referencing" a param, so a declared param still needs its own
        # binding somewhere.
        with pytest.raises(
            ValidationError,
            match=r"declared param 'stale' is not referenced by any request binding",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                params={"stale": {"in": "query", "type": "string", "required": False}},
            )}))

    def test_path_param_bound_to_unknown_param_still_rejected(self):
        with pytest.raises(
            ValidationError, match=r"references unknown param 'nope'"
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"from_param": "nope"}},
            )}))

    def test_path_param_bound_to_non_path_param_still_rejected(self):
        with pytest.raises(
            ValidationError, match=r"binds to param 'id' which has in='query'"
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"from_param": "id"}},
                params={"id": {
                    "in": "query", "type": "string", "required": True, "default": "1",
                }},
            )}))

    def test_path_param_that_is_neither_binding_still_rejected(self):
        # A `${record.id}` template is not a binding expression; the pre-#125
        # "must be a {from_param} expression" message is what an author sees.
        with pytest.raises(
            ValidationError,
            match=r"request\.path_params\['id'\] must be a `\{from_param: <name>\}` expression",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"template": "${record.id}"}},
            )}))

    def test_batching_arity_remains_a_statement_about_the_body(self):
        # A batched write with no path placeholder is unaffected by #125: the
        # body says `records`, and no path segment reads a record.
        parse_endpoint(_api_payload({"insert": _write_op(
            method="POST",
            path="/Contact",
            body={"objects": {"from_input": "records"}},
            batching={"max_records": 50},
        )}))

    def test_non_batched_body_must_still_reference_the_record(self):
        # A `from_input` in `path_params` does NOT satisfy the body's arity rule:
        # the path binding is deliberately kept out of the body's from_input set.
        with pytest.raises(
            ValidationError,
            match=r"non-batched write request body must reference `from_input: 'record'`",
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                body={"static": True},
            )}))

    def test_body_from_input_membership_still_reported_against_the_body(self):
        # Two `from_input` surfaces, two error sites: the body's message must not
        # be re-labelled as a path_params failure.
        with pytest.raises(ValidationError) as exc:
            parse_endpoint(_api_payload({"insert": _write_op(
                body={"email": {"from_input": "record.emial"}},
                properties={"id": {"type": "string"}, "email": {"type": "string"}},
            )}))
        message = str(exc.value)
        assert "from_input 'record.emial' references undeclared input.schema field" in message
        assert "path_params" not in message

    def test_upsert_conflict_keys_unchanged(self):
        # `conflict_keys` still required on upsert, still checked against
        # `input.schema`, still forbidden elsewhere — with a record-reading path.
        parse_endpoint(_api_payload({"upsert": _write_op(conflict_keys=["id"])}))

        with pytest.raises(
            ValidationError, match=r"operations\.write\.upsert\.conflict_keys is required"
        ):
            parse_endpoint(_api_payload({"upsert": _write_op()}))

        with pytest.raises(
            ValidationError, match=r"conflict_keys is not allowed"
        ):
            parse_endpoint(_api_payload({"insert": _write_op(conflict_keys=["id"])}))

        with pytest.raises(
            ValidationError, match=r"unknown input\.schema fields \['nope'\]"
        ):
            parse_endpoint(_api_payload({"upsert": _write_op(conflict_keys=["nope"])}))

    def test_path_params_still_required_to_match_placeholders(self):
        # The presence correlation is upstream of the binding rules and untouched.
        with pytest.raises(
            ValidationError, match=r"request\.path_params keys must equal placeholders in path"
        ):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"contact_id": {"from_input": "record.id"}},
            )}))


# ---------------------------------------------------------------------------
# The two things #125 asked to SETTLE, not just to permit
# ---------------------------------------------------------------------------


class TestPathSegmentEncodingIsEngineOwned:
    """ADV-ENDP-027. #125 point 2 asked for the encoding contract to be stated
    "so an author does not double-encode by reaching for `url_encode` as well".
    Stating it is not enough on its own — the author reaching for it was the
    accepted, unflagged case — so the reach is refused where it would do harm.

    The harm is silent and downstream: a record id containing `/` or a space
    goes on the wire as `a%2520b`, and the provider 404s or matches the wrong
    resource. Nothing after authoring can tell that apart from a value that
    really did contain `%25`.
    """

    def test_url_encode_wrapped_path_param_rejected(self):
        with pytest.raises(ValidationError, match=r"must not apply 'url_encode'"):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {
                    "function": "url_encode", "input": {"from_input": "record.id"},
                }},
            )}))

    def test_base64_encode_wrapped_path_param_rejected(self):
        with pytest.raises(ValidationError, match=r"must not apply 'base64_encode'"):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {
                    "function": "base64_encode", "input": {"from_input": "record.id"},
                }},
            )}))

    def test_the_refusal_says_why_and_names_the_fix(self):
        with pytest.raises(ValidationError) as exc:
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {
                    "function": "url_encode", "input": {"from_param": "id"},
                }},
                params={"id": {
                    "in": "path", "type": "string", "required": True, "default": "1",
                }},
            )}))
        message = str(exc.value)
        assert "the engine percent-encodes each substituted path segment" in message
        assert "Bind the raw value" in message

    def test_a_read_path_param_is_refused_too(self):
        # The encoding contract is about the path, not about writes: a read
        # binding wrapping `url_encode` double-encodes identically.
        payload = {
            "$schema": API_SCHEMA_URL,
            "endpoint_id": "contact",
            "operations": {"read": {
                "request": {
                    "method": "GET", "path": "/Contact/{id}",
                    "path_params": {"id": {
                        "function": "url_encode", "input": {"from_param": "id"},
                    }},
                },
                "params": {"id": {
                    "in": "path", "type": "string", "required": True, "default": "1",
                }},
                "response": {
                    "records": {"ref": "response.body.objects"},
                    "schema": {
                        "$schema": JSON_SCHEMA,
                        "type": "object",
                        "properties": {"objects": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"id": {"type": "string"}}},
                        }},
                    },
                },
            }},
        }
        with pytest.raises(ValidationError, match=r"must not apply 'url_encode'"):
            parse_endpoint(payload)

    def test_a_nested_encoder_is_still_found(self):
        # The walk reaches a function nested as another expression's argument,
        # which is exactly where `_collect_singleton_values` finds the binding
        # that made the document look well-formed in the first place.
        with pytest.raises(ValidationError, match=r"must not apply 'url_encode'"):
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {
                    "function": "lookup",
                    "input": {"function": "url_encode", "input": {"from_input": "record.id"}},
                    "map": {"a": "b"},
                }},
            )}))

    def test_a_non_encoding_function_is_untouched(self):
        # Only functions whose job is to escape a value for the wire are
        # refused. `lookup` maps a value; it does not encode one.
        parse_endpoint(_api_payload({"insert": _write_op(
            path_params={"id": {
                "function": "lookup",
                "input": {"from_input": "record.id"},
                "map": {"a": "b"},
            }},
        )}))

    def test_the_refused_encoders_are_real_catalog_functions(self):
        """The refused set is a judgement about what each function DOES, so it
        is stated by hand. This pins it to the callable catalog: renaming or
        dropping a function there must not leave the guard silently naming
        nothing."""
        from analitiq.contracts.connector import Base64EncodeDerived, UrlEncodeDerived
        from analitiq.contracts.endpoints import _WIRE_ENCODING_FUNCTIONS

        catalog_names = {
            UrlEncodeDerived.model_fields["function"].annotation.__args__[0],
            Base64EncodeDerived.model_fields["function"].annotation.__args__[0],
        }
        assert _WIRE_ENCODING_FUNCTIONS == catalog_names


class TestAWritePathParamMustBeAbleToResolve:
    """ADV-ENDP-028. #125 opens with a document it calls "contract-valid and
    unimplementable": a write binding `{id}` to an `in: path` param that carries
    no `default`. On a write a param has exactly ONE source — its own `default`.
    `operators` makes a param stream-filterable and `controlled_by` hands it to
    pagination or replication; both are read-side and neither is reachable from
    a write. So the placeholder provably can never be substituted, and the
    endpoint is dead at the engine handshake while validating green.

    That is the sevdesk shape. It is refused here, naming the binding that
    replaces it, so the 21 blocked endpoints get told what to do instead.
    """

    def test_sourceless_path_param_on_a_write_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            parse_endpoint(_api_payload({"insert": _write_op(
                path_params={"id": {"from_param": "id"}},
                params={"id": {"in": "path", "type": "string", "required": True}},
            )}))
        message = str(exc.value)
        assert "declares no `default`" in message
        assert "record.<field>" in message

    def test_a_default_makes_it_resolvable_and_accepted(self):
        parse_endpoint(_api_payload({"insert": _write_op(
            path_params={"id": {"from_param": "id"}},
            params={"id": {
                "in": "path", "type": "string", "required": True, "default": "42",
            }},
        )}))

    def test_a_value_expression_default_counts_as_a_source(self):
        parse_endpoint(_api_payload({"insert": _write_op(
            path_params={"id": {"from_param": "id"}},
            params={"id": {
                "in": "path", "type": "string", "required": True,
                "default": {"ref": "connection.parameters.tenant"},
            }},
        )}))

    def test_a_read_path_param_needs_no_default(self):
        # Reads keep the old latitude: a read path param can be supplied by a
        # stream filter, so a missing `default` is not proof it cannot resolve.
        payload = {
            "$schema": API_SCHEMA_URL,
            "endpoint_id": "contact",
            "operations": {"read": {
                "request": {
                    "method": "GET", "path": "/Contact/{id}",
                    "path_params": {"id": {"from_param": "id"}},
                },
                "params": {"id": {
                    "in": "path", "type": "string", "required": True,
                    "operators": ["eq"],
                }},
                "response": {
                    "records": {"ref": "response.body.objects"},
                    "schema": {
                        "$schema": JSON_SCHEMA,
                        "type": "object",
                        "properties": {"objects": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"id": {"type": "string"}}},
                        }},
                    },
                },
            }},
        }
        parse_endpoint(payload)


# ---------------------------------------------------------------------------
# Review finding (PR #131): the membership rule no-opped on the very shape
# ADV-ENDP-026's rejection message tells authors to write.
# ---------------------------------------------------------------------------


class TestMembershipHoldsThroughRefsAndAllOf:
    """`_json_schema_top_level_fields` read `properties` raw, so an
    `input.schema` written as `{"$ref": "#/$defs/Rec"}` — exactly what
    ADV-ENDP-026 instructs when it refuses a non-local ref — made
    ADV-ENDP-024's membership check, the body `from_input` check and
    `conflict_keys` all silently pass. A `{id}` placeholder bound to a field
    that does not exist then ships: the wrong-URL failure #125 was filed for.

    The read half of this PR had already been fixed this way; the write half
    had not, and every existing test in this module used an inline schema.
    """

    REF_SCHEMA = {
        "$schema": JSON_SCHEMA,
        "$ref": "#/$defs/Rec",
        "$defs": {
            "Rec": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
            }
        },
    }
    ALLOF_SCHEMA = {
        "$schema": JSON_SCHEMA,
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "allOf": [{"$ref": "#/$defs/Ids"}],
        "$defs": {"Ids": {"type": "object", "properties": {"id": {"type": "string"}}}},
    }

    @pytest.mark.parametrize("schema_name", ["REF_SCHEMA", "ALLOF_SCHEMA"])
    def test_declared_field_through_composition_is_accepted(self, schema_name):
        op = _write_op(path_params={"id": {"from_input": "record.id"}})
        op["input"]["schema"] = getattr(self, schema_name)
        parse_endpoint(_api_payload({"insert": op}))

    @pytest.mark.parametrize("schema_name", ["REF_SCHEMA", "ALLOF_SCHEMA"])
    def test_undeclared_field_through_composition_is_rejected(self, schema_name):
        op = _write_op(path_params={"id": {"from_input": "record.nope"}})
        op["input"]["schema"] = getattr(self, schema_name)
        with pytest.raises(ValidationError, match="record.nope"):
            parse_endpoint(_api_payload({"insert": op}))

    @pytest.mark.parametrize("schema_name", ["REF_SCHEMA", "ALLOF_SCHEMA"])
    def test_conflict_keys_membership_holds_through_composition(self, schema_name):
        op = _write_op(
            path_params={"id": {"from_input": "record.id"}}, conflict_keys=["nope"]
        )
        op["input"]["schema"] = getattr(self, schema_name)
        with pytest.raises(ValidationError, match="conflict_keys"):
            parse_endpoint(_api_payload({"upsert": op}))


class TestLiteralWrappedBindingIsNotABinding:
    """`resolve_value_expression` returns a literal's contents verbatim, so a
    `from_input` inside one is inert data the engine never resolves. Collecting
    it satisfied "this placeholder has a binding", passed the `record.<dotted>`
    shape check and the membership check — and then put the literal dict itself
    on the wire as the path segment."""

    def test_literal_wrapped_from_input_does_not_bind_the_placeholder(self):
        op = _write_op(path_params={"id": {"literal": {"from_input": "record.id"}}})
        with pytest.raises(ValidationError, match="from_param"):
            parse_endpoint(_api_payload({"insert": op}))


class TestWriteBlocksAreSweptForScopeTypos:
    """A write has no `response.schema`, so declared-path resolution has nothing
    to resolve against — but both SCOPE checks apply, and they are what catches
    this class. `success_when` is the worst cell: it decides whether a write
    SUCCEEDED, and a ref that resolves to nothing makes `empty` hold on every
    response, so every write reports success including the ones whose rejected
    rows the provider listed. Partial data loss, green run."""

    def _op(self, **response):
        op = _write_op(path_params={"id": {"from_input": "record.id"}})
        op["response"] = response
        return _api_payload({"insert": op})

    @pytest.mark.parametrize(
        "ref", ["response.bodyy.errors", "responses.body.errors", "Response.body.ok"]
    )
    def test_success_when_scope_typo_is_rejected(self, ref):
        with pytest.raises(ValidationError):
            parse_endpoint(self._op(success_when={"empty": {"ref": ref}}))

    @pytest.mark.parametrize("slot", ["affected_records", "generated_keys"])
    def test_extraction_slot_scope_typo_is_rejected(self, slot):
        with pytest.raises(ValidationError):
            parse_endpoint(self._op(**{slot: {"ref": "response.bodyy.n"}}))

    def test_write_request_slot_scope_typo_is_rejected(self):
        op = _write_op(
            path_params={"id": {"from_input": "record.id"}},
            request_extras={"query": {"c": {"ref": "response.bodyy.x"}}},
        )
        with pytest.raises(ValidationError):
            parse_endpoint(_api_payload({"insert": op}))

    #: Write slots that accept a free expression. `path_params` is excluded by
    #: CONTRACT — it must be `{from_param}`/`{from_input}`, a strictly stronger
    #: rule, pinned by its own case below.
    FREE_SLOTS = ("headers", "query", "body")

    @pytest.mark.parametrize("slot", FREE_SLOTS)
    @pytest.mark.parametrize(
        "ref",
        [
            # A write has NO `response.schema`, so path-level checking could
            # never reject any of these — every `response.*` ref in a write
            # request slot was accepted unconditionally. The rule is
            # scope-level: the request is built before the response exists.
            "response.body.anything",
            "response.body.errors",
            "response.record_count",
        ],
    )
    def test_a_response_ref_in_any_write_request_slot_is_rejected(self, slot, ref):
        """`body` had no negative case on the write side at all, so dropping it
        from the write site table left the suite green."""
        binding = {"ref": ref}
        extras = {slot: binding} if slot == "body" else {slot: {"c": binding}}
        op = _write_op(request_extras=extras)
        with pytest.raises(ValidationError, match="before the response exists"):
            parse_endpoint(_api_payload({"insert": op}))

    def test_write_path_params_is_guarded_by_the_stronger_binding_rule(self):
        op = _write_op(path_params={"id": {"ref": "response.body.anything"}})
        with pytest.raises(ValidationError, match="must be a `.from_param"):
            parse_endpoint(_api_payload({"insert": op}))

    def test_the_write_slot_tuple_still_covers_every_expression_field(self):
        assert set(_REQUEST_EXPRESSION_SLOTS) == set(self.FREE_SLOTS) | {"path_params"}

    def test_a_response_ref_in_a_write_param_default_is_rejected(self):
        """The write side's `params[<n>].default` had a positive case and no
        negative one — the read side's equivalent is what #123 was filed for."""
        op = _write_op(
            params={"tok": {
                "in": "query",
                "type": "string",
                "required": False,
                "default": {"ref": "response.body.next"},
            }},
            request_extras={"query": {"tok": {"from_param": "tok"}}},
        )
        with pytest.raises(ValidationError, match="before the response exists"):
            parse_endpoint(_api_payload({"insert": op}))

    def _with_metadata(self, success_ref):
        op = _write_op(path_params={"id": {"from_input": "record.id"}})
        op["response"] = {
            "metadata": {"total": {"ref": "response.body.n"}},
            "success_when": {"eq": [{"ref": success_ref}, 1]},
        }
        return _api_payload({"insert": op})

    def test_an_undeclared_write_metadata_key_is_rejected(self):
        """`WriteResponse.metadata` has the same closed, author-declared key set
        as the read side, and the harm here is the worst in the contract: a
        `success_when` over a ref that resolves to nothing holds unconditionally,
        so every write reports success including the ones whose rejected rows the
        provider listed.

        The check was wired to the read sweep only — the write sweep passed no
        `metadata_keys`, so the guard was dead on this side. Same "a site that
        was not on somebody's list" shape, one call site right.
        """
        with pytest.raises(ValidationError, match="not a declared `response.metadata` key"):
            parse_endpoint(self._with_metadata("response.metadata.nope"))

    def test_the_write_harm_named_is_the_success_when_harm(self):
        with pytest.raises(ValidationError, match="every write reports success"):
            parse_endpoint(self._with_metadata("response.metadata.nope"))

    def test_a_declared_write_metadata_key_resolves(self):
        parse_endpoint(self._with_metadata("response.metadata.total"))

    def test_a_good_write_response_still_validates(self):
        parse_endpoint(
            self._op(
                success_when={"empty": {"ref": "response.body.errors"}},
                affected_records={"ref": "response.body.count"},
            )
        )
