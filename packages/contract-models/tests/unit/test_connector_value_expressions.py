"""Every value-expression token a connector authors names a resolution scope.

The API-endpoint document has enforced this since it grew typed expression
models: a `ref` carries a published pattern, and a template's placeholders are
checked against the same vocabulary. The connector document feeds the *same*
grammar from `base_url`, transport headers and DSN bindings, and checked none
of it — so the two halves of one grammar disagreed about what they accepted.

What an unqualified token costs is not a failure to resolve, and it is not one
thing: the resolvers that read this document disagree. One reads the bare name
as a top-level context key and falls back to `secrets`, so `${token}` picks up
whatever is stored under that name; the other raises on a scope it does not
know. On a transport either is worse than on an operation, because `base_url`
and `headers` apply to every request the transport makes.

These pin both directions — the scoped form still authors — because a check
that only ever rejects is one nobody can tell from a broken one.

They do NOT cover every site the rule states. `RULE-CTOR-057` is written over
every expression a connector authors; the models check the transport's two
fields, a DSN binding and a rate-limit window. The auth-exchange templates and
`TransportDefaults.headers` are open, and that gap is tracked as a coverage
defect, not as a decision.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.connector import (
    DsnBinding,
    HttpTransport,
    TransportRateLimit,
)
from analitiq.contracts.value_expression import RESOLUTION_SCOPES


def _http(**kwargs):
    return HttpTransport.model_validate({"transport_type": "http", **kwargs})


def _shows_the_vocabulary(exc: pytest.ExceptionInfo) -> bool:
    """The refusal names every scope the author could have used.

    A `ref` is refused by its published pattern and a template by the
    validator, so the two carry different text. What both owe the author is
    the list of scopes — an author who reaches for a bare name is one who does
    not know the vocabulary, and a refusal that withholds it just says no.
    """
    message = str(exc.value)
    return all(scope in message for scope in RESOLUTION_SCOPES)


UNQUALIFIED_BASE_URLS = [
    pytest.param("https://${host}/v1", id="bare-string-template"),
    pytest.param({"template": "https://${host}/v1"}, id="template-form"),
    pytest.param({"ref": "host"}, id="ref-form"),
    pytest.param(
        {"function": "url_encode", "input": {"ref": "host"}}, id="function-input"
    ),
    pytest.param(
        {"function": "basic_auth", "input": {"username": "${user}", "password": "x"}},
        id="function-nested-input",
    ),
]


@pytest.mark.parametrize("base_url", UNQUALIFIED_BASE_URLS)
def test_an_unqualified_base_url_is_refused(base_url):
    with pytest.raises(ValidationError) as exc:
        _http(base_url=base_url)
    assert _shows_the_vocabulary(exc)


SCOPED_BASE_URLS = [
    pytest.param("https://${connection.parameters.host}/v1", id="bare-string-template"),
    pytest.param(
        {"template": "https://${connection.parameters.host}/v1"}, id="template-form"
    ),
    pytest.param({"ref": "connection.discovered.api_domain"}, id="ref-form"),
    pytest.param(
        {"function": "url_encode", "input": {"ref": "connection.parameters.host"}},
        id="function-input",
    ),
]


@pytest.mark.parametrize("base_url", SCOPED_BASE_URLS)
def test_a_scoped_base_url_is_accepted(base_url):
    assert _http(base_url=base_url).base_url is not None


def test_a_placeholder_free_base_url_is_accepted():
    assert _http(base_url="https://api.example.com/v1").base_url


def test_a_literal_form_is_opaque():
    # `{literal}` is the opt-out of expression interpretation, so a `${...}`
    # inside it is data the connector meant verbatim, not a token to qualify.
    assert _http(base_url={"literal": "https://${not-a-token}/v1"}).base_url


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("Bearer ${token}", id="bare-string-template"),
        pytest.param({"template": "Bearer ${token}"}, id="template-form"),
        pytest.param({"ref": "token"}, id="ref-form"),
        pytest.param(
            {"function": "base64_encode", "input": {"ref": "token"}}, id="function-input"
        ),
    ],
)
def test_an_unqualified_transport_header_is_refused(value):
    with pytest.raises(ValidationError) as exc:
        _http(headers={"Authorization": value})
    assert _shows_the_vocabulary(exc)


def test_a_scoped_transport_header_is_accepted():
    assert _http(headers={"Authorization": "Bearer ${secrets.api_key}"}).headers


def test_a_literal_header_is_accepted():
    # A header with nothing to resolve is the common case and stays untouched.
    assert _http(headers={"Accept": "application/json"}).headers


def test_the_failure_names_the_token_and_the_header():
    with pytest.raises(ValidationError) as exc:
        _http(headers={"X-Tenant": "${tenant}", "Accept": "application/json"})
    message = str(exc.value)
    assert "tenant" in message and "X-Tenant" in message


def test_an_unqualified_dsn_binding_is_refused():
    with pytest.raises(ValidationError) as exc:
        DsnBinding.model_validate({"value": {"ref": "host"}, "encoding": "host"})
    assert _shows_the_vocabulary(exc)


def test_a_scoped_dsn_binding_is_accepted():
    binding = DsnBinding.model_validate(
        {"value": {"ref": "connection.parameters.host"}, "encoding": "host"}
    )
    assert binding.encoding == "host"


def test_an_unqualified_rate_limit_window_is_refused():
    with pytest.raises(ValidationError) as exc:
        TransportRateLimit.model_validate(
            {"max_requests": 10, "time_window_seconds": {"ref": "window"}}
        )
    assert _shows_the_vocabulary(exc)


def test_a_plain_rate_limit_window_is_accepted():
    limit = TransportRateLimit.model_validate(
        {"max_requests": 10, "time_window_seconds": 60}
    )
    assert limit.time_window_seconds == 60


def test_the_two_documents_agree_on_the_vocabulary():
    """The connector and endpoint models refuse the same token.

    They are separate classes in separate modules publishing separate `$defs`,
    which is how they came to disagree. Both now read the vocabulary from the
    resolver, and this is what fails if one of them stops.
    """
    from analitiq.contracts.endpoints import TemplateExpression as EndpointTemplate

    payload = {"template": "https://${host}/v1"}
    with pytest.raises(ValidationError):
        EndpointTemplate.model_validate(payload)
    with pytest.raises(ValidationError):
        _http(base_url=payload)
