"""Every expression dict a connector authors is the documented shape
(RULE-CTOR-065; the endpoint document's counterpart is RULE-ENDP-022).

The records carry the rule and its why. What these tests pin is the
connector-side walk itself: the untyped sites it must reach, both directions
— the documented shapes still author — and the rule's deliberate boundary:
the shape is checked, the function NAME is not (nothing here can read the
engine's function registry — RULE-SHRD-007).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.connector import (
    AdbcTransport,
    AuthOperationTemplate,
    DatabaseTls,
    DsnBinding,
    HttpTransport,
    PostAuthOperationRequest,
    TransportDefaults,
)


def _http(**kwargs):
    return HttpTransport.model_validate({"transport_type": "http", **kwargs})


# --- the gap the rule closes: malformed nodes in untyped expression sites ----


def test_a_transport_header_with_a_stray_sibling_is_refused():
    with pytest.raises(ValidationError) as exc:
        _http(headers={"X-K": {"ref": "secrets.k", "extra": 1}})
    message = str(exc.value)
    assert "headers.X-K" in message and "'ref'" in message and "extra" in message


def test_a_transport_header_with_two_expression_markers_is_refused():
    # Refused as multi-key; the walker's literal-payload comment carries why
    # one marker would otherwise silently win.
    with pytest.raises(ValidationError) as exc:
        _http(headers={"X-K": {"ref": "secrets.k", "literal": "x"}})
    assert "exactly one" in str(exc.value)


def test_a_transport_default_header_is_shape_checked():
    # Merge layer one: this map folds into every transport, so a malformed
    # node here reaches every request the connector makes.
    with pytest.raises(ValidationError):
        TransportDefaults.model_validate(
            {"headers": {"X-K": {"ref": "secrets.k", "extra": 1}}}
        )


def test_an_auth_template_header_is_shape_checked():
    with pytest.raises(ValidationError):
        AuthOperationTemplate.model_validate(
            {"path": "/t", "headers": {"X-K": {"ref": "secrets.k", "extra": 1}}}
        )


def test_an_auth_template_body_is_shape_checked_recursively():
    # `body` is arbitrary JSON with expressions inside; the walk reaches them
    # through the structural object around them.
    with pytest.raises(ValidationError) as exc:
        AuthOperationTemplate.model_validate(
            {"path": "/t", "body": {"secret": {"ref": "secrets.s", "rogue": 1}}}
        )
    assert "body.secret" in str(exc.value)


def test_a_malformed_node_inside_a_list_is_reached():
    # Arrays are ordinary in a body payload, and the walker is shared — this
    # one refusal guards list recursion for the endpoint document too.
    with pytest.raises(ValidationError) as exc:
        AuthOperationTemplate.model_validate(
            {"path": "/t", "body": {"items": [{"ref": "secrets.s", "rogue": 1}]}}
        )
    assert "body.items[0]" in str(exc.value)


def test_a_function_node_with_an_undeclared_sibling_is_refused():
    # The refusal side of the sibling set: a name no function model declares
    # is refused, so deriving the set too wide fails here the way deriving it
    # too narrow fails
    # test_a_function_with_its_documented_argument_fields_is_accepted.
    with pytest.raises(ValidationError) as exc:
        _http(headers={"X-Region": {
            "function": "lookup",
            "input": {"ref": "connection.parameters.region"},
            "map": {"eu": "eu-1"},
            "rogue": 1,
        }})
    assert "unexpected siblings" in str(exc.value) and "rogue" in str(exc.value)


def test_a_database_tls_field_is_shape_checked():
    with pytest.raises(ValidationError) as exc:
        DatabaseTls.model_validate(
            {"mode": {"ref": "connection.parameters.ssl_mode", "extra": 1}}
        )
    assert "[RULE-CTOR-065]" in str(exc.value)


def test_a_post_auth_request_header_is_shape_checked():
    with pytest.raises(ValidationError):
        PostAuthOperationRequest.model_validate(
            {"path": "/m", "headers": {"X-K": {"template": "${auth.t}", "extra": 1}}}
        )


def test_an_adbc_db_kwarg_is_shape_checked():
    with pytest.raises(ValidationError):
        AdbcTransport.model_validate({
            "transport_type": "adbc", "driver": "postgresql",
            "db_kwargs": {"password": {"ref": "secrets.password", "extra": 1}},
        })


def test_a_dsn_binding_value_is_shape_checked():
    with pytest.raises(ValidationError):
        DsnBinding.model_validate(
            {"value": {"ref": "secrets.host", "extra": 1}, "encoding": "host"}
        )


def test_a_function_input_is_shape_checked_through_the_typed_union():
    # `base_url` is graded by the typed expression union, but `url_encode`'s
    # `input` is `Any` inside its model — the walk is what reaches it.
    with pytest.raises(ValidationError):
        _http(base_url={
            "function": "url_encode",
            "input": {"ref": "connection.parameters.host", "extra": 1},
        })


def test_an_extension_key_sibling_is_refused():
    # The authored connector contract is closed — `x-*` smuggling is refused
    # on models, and an untyped header map is not a way around that.
    with pytest.raises(ValidationError):
        _http(headers={"X-K": {"ref": "secrets.k", "x-note": "why"}})


def test_a_non_string_sibling_key_is_still_a_refusal():
    # A body is arbitrary JSON to pydantic, so a walked dict can carry non-str
    # keys beside an expression key; the refusal must be the contract's, not a
    # cross-type sort escaping as a raw TypeError.
    with pytest.raises(ValidationError) as exc:
        AuthOperationTemplate.model_validate(
            {"path": "/t", "body": {"ref": "secrets.k", 1: "x", "z": 2}}
        )
    assert "unexpected siblings" in str(exc.value)


def test_an_endpoint_binding_form_is_structural_here():
    # `from_param` / `from_input` are endpoint request-slot bindings, not
    # connector expression keys: on this document a dict carrying one is
    # structural JSON, walked through rather than graded as an expression.
    # This is the parameterization boundary between the shared walker's
    # callers — widen the connector's key set and this header is refused.
    assert _http(headers={"X-B": {"from_param": "page", "extra": 1}}).headers


# --- the documented shapes still author --------------------------------------


def test_the_documented_forms_still_author():
    transport = _http(headers={
        "Accept": "application/json",
        "Authorization": "Bearer ${secrets.api_key}",
        "X-Ref": {"ref": "connection.discovered.token"},
        "X-Tpl": {"template": "Bearer ${auth.access_token}"},
        "X-Lit": {"literal": "constant"},
        "X-Fn": {"function": "base64_encode", "input": {"ref": "secrets.k"}},
    })
    assert transport.headers


def test_a_function_with_its_documented_argument_fields_is_accepted():
    # `map` and `safe` sit beside `function`, exactly as the function models
    # declare them — which pins the sibling set to those models: derive it
    # from anything narrower and these headers are refused.
    transport = _http(headers={
        "X-Region": {
            "function": "lookup",
            "input": {"ref": "connection.parameters.region"},
            "map": {"eu": "eu-1"},
        },
        "X-Enc": {
            "function": "url_encode",
            "input": {"ref": "connection.parameters.tenant"},
            "safe": "-",
        },
    })
    assert transport.headers


def test_a_lookup_map_value_is_data_not_an_expression():
    # A provider-shaped lookup output is data the walk must not grade;
    # `validate_expression_shapes` draws this boundary and carries why.
    transport = _http(headers={"X-Region": {
        "function": "lookup",
        "input": {"ref": "connection.parameters.region"},
        "map": {"eu": {"ref": "eu-west-1", "region_name": "Europe"}},
    }})
    assert transport.headers


def test_a_node_both_malformed_and_unscoped_is_diagnosed_by_shape():
    # Definition order on the mixin is the only thing putting the shape
    # check before the scope check; this pins it, so a reorder cannot
    # silently flip the diagnosis to the scope rule's.
    with pytest.raises(ValidationError) as exc:
        _http(headers={"X-K": {"ref": "garbage_nonsense", "rogue": 1}})
    message = str(exc.value)
    assert "[RULE-CTOR-065]" in message and "RULE-CTOR-057" not in message


def test_a_literal_payload_stays_opaque():
    # `{literal}` is the opt-out of expression interpretation: a payload that
    # happens to carry expression-shaped keys is data the connector meant
    # verbatim, not a node to grade.
    transport = _http(headers={"X-K": {"literal": {"template": "verbatim", "extra": 1}}})
    assert transport.headers


def test_the_function_name_itself_stays_unchecked():
    # The rule's deliberate boundary: shape is contract-checked, membership in
    # the engine's function registry is not (RULE-SHRD-007) — an unregistered
    # name in a well-formed node still validates.
    transport = _http(headers={"X-Sig": {
        "function": "jwt_sign", "input": {"key": {"literal": "k"}},
    }})
    assert transport.headers


def test_a_structural_object_without_expression_keys_is_not_an_expression():
    template = AuthOperationTemplate.model_validate({
        "path": "/t",
        "body": {"grant_type": "client_credentials",
                 "client_secret": {"ref": "secrets.client_secret"}},
    })
    assert template.body
