"""What a connector may name as an HTTP header, and where a body's media
type is declared.

The rules over one list. RULE-HTTP-002 refuses `Content-Length`, whose value
is the size of a body nobody has built yet. RULE-HTTP-003 refuses
`Content-Type`, which has a typed field on the request that carries the body.
Each reads `DeclaredHeaderNames.declared_header_names`, so the coverage claim
worth pinning is not any single block but that **every** block naming a header
inherits them — a property of the check rather than of a list somebody extends
when a route is noticed.

The `content_type` field is pinned from both ends here too: it exists on the
request models that declare a body, and the branch that declares none refuses
it structurally.
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from analitiq.contracts.connector import (
    AuthOperationTemplate,
    HttpTransport,
    PostAuthOperationRequest,
    TransportDefaults,
)
from analitiq.contracts.endpoints import (
    GetReadRequest,
    Idempotency,
    PostReadRequest,
    WriteRequest,
)
from analitiq.contracts.shared.introspect import contract_classes
from analitiq.contracts.shared.rules import DeclaredHeaderNames

#: A minimal instance of each block that names headers through a `headers`
#: map, as (label, model, kwargs-without-headers).
HEADER_MAP_BLOCKS = [
    ("HttpTransport", HttpTransport, {"transport_type": "http"}),
    ("TransportDefaults", TransportDefaults, {}),
    ("AuthOperationTemplate", AuthOperationTemplate, {"path": "/t"}),
    ("PostAuthOperationRequest", PostAuthOperationRequest, {"path": "/t"}),
    ("GetReadRequest", GetReadRequest, {"method": "GET", "path": "/v1/x"}),
    ("PostReadRequest", PostReadRequest, {"method": "POST", "path": "/v1/x"}),
    ("WriteRequest", WriteRequest, {"method": "POST", "path": "/v1/x"}),
]

REFUSED = [
    ("RULE-HTTP-002", "Content-Length", "content-length"),
    ("RULE-HTTP-003", "Content-Type", "content-type"),
]


def _spellings(canonical: str, lowercased: str) -> tuple[str, ...]:
    """Every way of writing one header name that reaches these rules.

    Case only. A padded name never gets this far — `HeaderName` refuses it as
    a name at all, which is a stronger answer than refusing it as this
    particular header, and `test_a_name_that_is_not_a_header_name_is_refused`
    is where that is pinned.
    """
    return (canonical, lowercased, canonical.upper())


@pytest.mark.parametrize("label, model, kwargs", HEADER_MAP_BLOCKS)
@pytest.mark.parametrize("rule_id, canonical, lowercased", REFUSED)
def test_every_header_map_refuses_the_name(
    label, model, kwargs, rule_id, canonical, lowercased
):
    for spelling in _spellings(canonical, lowercased):
        with pytest.raises(ValidationError) as exc:
            model(**kwargs, headers={spelling: "x"})
        assert rule_id in str(exc.value), f"{label}: {spelling} not refused by {rule_id}"


@pytest.mark.parametrize("label, model, kwargs", HEADER_MAP_BLOCKS)
def test_an_ordinary_header_is_untouched(label, model, kwargs):
    # `Accept` is the contrast the rules are drawn against: it states what the
    # client will take back, not how one body is encoded.
    block = model(**kwargs, headers={"Accept": "application/json"})
    assert block.headers == {"Accept": "application/json"}


@pytest.mark.parametrize("rule_id, canonical, _lowercased", REFUSED)
def test_the_finding_names_the_header_the_author_wrote(rule_id, canonical, _lowercased):
    with pytest.raises(ValidationError) as exc:
        HttpTransport(
            transport_type="http",
            headers={"Accept": "application/json", canonical: "x"},
        )
    assert f"headers.{canonical}" in str(exc.value)


@pytest.mark.parametrize("rule_id, canonical, lowercased", REFUSED)
def test_an_idempotency_key_is_a_header_name_too(rule_id, canonical, lowercased):
    # The same matrix the header maps take: this name reaches the wire the
    # same way, so a spelling that slips here slips everywhere.
    for spelling in _spellings(canonical, lowercased):
        with pytest.raises(ValidationError) as exc:
            Idempotency.model_validate({"in": "header", "name": spelling})
        assert rule_id in str(exc.value)
        assert "idempotency.name" in str(exc.value)


@pytest.mark.parametrize("name", ["", " ", " Idempotency-Key", "Idempotency-Key "])
@pytest.mark.parametrize("location", ["header", "body"])
def test_an_idempotency_name_carrying_edge_space_is_refused(name, location):
    # Held to less than a header name's token shape, because `in: body` makes
    # it a JSON field name and those admit more. What both share is that the
    # space around a name is not part of it, so a name carrying one names
    # nothing the provider will match.
    with pytest.raises(ValidationError):
        Idempotency.model_validate({"in": location, "name": name})


@pytest.mark.parametrize(
    "name", ["Idempotency-Key", "idempotency_key", "X-Request-Id", "a b"]
)
def test_an_idempotency_name_without_edge_space_parses(name):
    # `a b` is here deliberately: a body field name may hold what a header
    # name may not, which is why this field is not `HeaderName`.
    assert Idempotency.model_validate({"in": "body", "name": name}).name == name


@pytest.mark.parametrize("rule_id, canonical, _lowercased", REFUSED)
def test_a_body_field_of_that_name_is_not_a_header(rule_id, canonical, _lowercased):
    # `in: body` puts the key in the payload, where these names mean nothing
    # and the rules have nothing to say.
    block = Idempotency.model_validate({"in": "body", "name": canonical})
    assert block.name == canonical


def test_declaring_and_removing_one_header_is_refused_however_it_is_cased():
    # RULE-HTTP-001 reads a name the same way the refusal rules do — one
    # normaliser, so a spelling refused at one site cannot pass at the next.
    with pytest.raises(ValidationError) as exc:
        HttpTransport(
            transport_type="http", headers={"Accept": "a"}, headers_remove=["accept"]
        )
    assert "RULE-HTTP-001" in str(exc.value)


#: Names no provider will ever see, whatever the map holding them says. Padded
#: ones are here rather than in the rule tests above because being a header
#: name at all is the earlier question: the map is keyed on `HeaderName`, so
#: these never reach a rule.
NOT_HEADER_NAMES = ["", " ", "  ", "\t", " Accept", "Accept ", "a b", "a:b", "a\nb"]


@pytest.mark.parametrize("name", NOT_HEADER_NAMES)
@pytest.mark.parametrize("label, model, kwargs", HEADER_MAP_BLOCKS)
def test_a_name_that_is_not_a_header_name_is_refused(name, label, model, kwargs):
    # RFC 9110 makes a field name exactly a token. Anything else is a name the
    # provider never receives — and, since the space around a name is not part
    # of it, a padded one is a second spelling of a header the map may already
    # carry, which would put that header on the wire twice.
    with pytest.raises(ValidationError):
        model(**kwargs, headers={name: "x"})


@pytest.mark.parametrize("name", NOT_HEADER_NAMES)
def test_a_removal_naming_no_header_name_is_refused(name):
    with pytest.raises(ValidationError):
        HttpTransport(transport_type="http", headers_remove=[name])


@pytest.mark.parametrize("name", ["Accept", "X-Api-Key", "content-type-ish"])
def test_an_ordinary_header_name_still_parses(name):
    assert HttpTransport(
        transport_type="http", headers={name: "x"}
    ).headers == {name: "x"}


def test_every_block_naming_headers_carries_the_checks():
    """The coverage claim, as a property of the contract rather than a list.

    A model that grows a `headers` map and does not inherit the mixin is a
    route to the wire that neither rule grades — which is exactly how the
    refusal came to exist at some call sites and not others. Membership is
    read off the live tree, so a new block fails here rather than shipping
    ungraded.
    """
    declaring = [cls for cls in contract_classes() if "headers" in cls.model_fields]
    # A floor, because an extractor that matches nothing reports green in the
    # same voice as one that found every block correct. The blocks this file
    # exercises by hand are the smallest set the live tree can honestly hold;
    # below it, the walk or the field name has moved and the check has stopped
    # measuring rather than found nothing wrong.
    assert len(declaring) >= len(HEADER_MAP_BLOCKS), (
        f"only {sorted(cls.__name__ for cls in declaring)} declare `headers`, "
        f"fewer than the blocks parametrized here — `contract_classes()` or the "
        "field name has moved, and this check is measuring nothing."
    )
    missing = sorted(
        cls.__name__ for cls in declaring if not issubclass(cls, DeclaredHeaderNames)
    )
    assert not missing, (
        f"models declaring `headers` without DeclaredHeaderNames: {missing}. "
        "Mix it in, or the block can name a header no rule grades."
    )


@pytest.mark.parametrize(
    "model, kwargs",
    [
        (PostReadRequest, {"method": "POST", "path": "/v1/x"}),
        (WriteRequest, {"method": "POST", "path": "/v1/x"}),
        (AuthOperationTemplate, {"path": "/t"}),
        (PostAuthOperationRequest, {"path": "/t"}),
    ],
)
def test_the_media_type_is_declarable_where_a_body_is(model, kwargs):
    block = model(**kwargs, content_type="application/x-www-form-urlencoded")
    assert block.content_type == "application/x-www-form-urlencoded"


def test_a_bodiless_read_declares_no_media_type():
    with pytest.raises(ValidationError, match="content_type"):
        GetReadRequest(method="GET", path="/v1/x", content_type="application/json")


@pytest.mark.parametrize(
    "media_type",
    [
        "application/json",
        # A vendor media type is a provider fact, so the contract names no
        # vocabulary — an enum here would need editing per provider.
        "application/vnd.api+json",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "text/csv; charset=utf-8",
        "text/csv;charset=utf-8",
        # Case is not normalised away: a provider that documents its media
        # type in capitals is quoted as it documents it.
        "APPLICATION/JSON",
        # The whole RFC 9110 token set, so a narrowing of the character class
        # fails here rather than at a provider that documents an unusual one.
        "application/x-my_thing.v2+json",
        "x-t!#$%&'*+^_`|~-oken/x-t!#$%&'*+^_`|~-oken",
    ],
)
def test_any_media_type_is_declarable(media_type):
    assert WriteRequest(
        method="POST", path="/v1/x", content_type=media_type
    ).content_type == media_type


@pytest.mark.parametrize(
    "label, value",
    [
        # The empty string is the sharp one: a second spelling of absent, which
        # a resolver reading `content_type or <default>` silently turns into
        # the default the author was overriding.
        ("empty", ""),
        ("whitespace", "   "),
        ("prose", "not a media type"),
        ("the header line rather than its value", "Content-Type: application/json"),
        ("type with no subtype", "application"),
        ("subtype with no type", "/json"),
        ("a third part", "application/json/extra"),
        # A published pattern is read by JSON-Schema consumers too, so the
        # injection shapes are pinned here rather than trusted to the engine.
        ("a header injected after a newline", "application/json\nX-Evil: 1"),
        ("the same with CRLF", "application/json\r\nX-Evil: 1"),
        ("a trailing newline", "application/json\n"),
    ],
)
def test_a_value_that_is_not_a_media_type_is_refused(label, value):
    with pytest.raises(ValidationError, match="content_type"):
        WriteRequest(method="POST", path="/v1/x", content_type=value)


# tests/unit/<this file> -> parents[4] is the repo root.
LATEST_CONNECTOR_SCHEMA = (
    Path(__file__).resolve().parents[4] / "schemas" / "connector" / "latest.json"
)

_CONNECTOR_WITHOUT_TRANSPORTS = {
    "$schema": "https://schemas.analitiq.ai/connector/latest.json",
    "connector_id": "acme",
    "kind": "api",
    "display_name": "Acme",
    "description": (
        "An API connector, minimal but complete, used to grade the published "
        "schema's own view of what a header may be named."
    ),
    "version": "1.0.0",
    "default_transport": "api",
    "auth": {"type": "api_key"},
    "connection_contract": {"inputs": {}},
}


def _connector_declaring(header_name: str) -> dict:
    return dict(_CONNECTOR_WITHOUT_TRANSPORTS, transports={"api": {
        "transport_type": "http",
        "base_url": "https://api.example.test",
        "headers": {header_name: "x"},
    }})


class TestThePublishedSchemaAgreesAboutHeaderNames:
    """A consumer holding only `latest.json` refuses what the models refuse.

    Worth its own test because the natural spelling does NOT give this.
    Pydantic renders a constrained dict key as `patternProperties`, which says
    what a matching key holds and forbids nothing — so the models would reject
    a name and the published document would accept it, and nothing else here
    reads the rendered file closely enough to notice. The `propertyNames`
    fragment beside the field is what closes that, and this is what says so.

    Judged on whether the whole document validates. A bad header name surfaces
    as the connector-kind union failing to match its `http` branch, not as an
    error whose path mentions headers, so asserting on the path reports a pass
    for every input.

    This reads the COMMITTED document, so it grades what a consumer fetches
    and is blind to a model that has drifted from it — dropping the fragment
    leaves this green. `render_schemas.py check` re-renders and compares,
    which is the gate that sees that half.
    """

    @staticmethod
    def _validator() -> Draft202012Validator:
        return Draft202012Validator(json.loads(LATEST_CONNECTOR_SCHEMA.read_text()))

    @pytest.mark.parametrize("name", ["Accept", "X-Api-Key", "Content-Length-ish"])
    def test_a_header_name_validates(self, name):
        assert not list(self._validator().iter_errors(_connector_declaring(name)))

    @pytest.mark.parametrize("name", NOT_HEADER_NAMES)
    def test_a_name_that_is_not_a_header_name_does_not(self, name):
        assert list(self._validator().iter_errors(_connector_declaring(name)))
