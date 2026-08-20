"""RULE-CTOR-066 — a `base_url` the author wrote carries no credentials.

The boundary is what makes this rule safe to state: the check reads the text
an author wrote, never a value that arrives at resolution. A bare string and a
`{literal}` are that text whole; a `{template}`'s authority is that text
wherever no placeholder stands in it, which is why a credential-bearing
template is caught here and a host arriving through a placeholder is not. The
forms that carry no text at all read back as nothing to check, and the engine
refuses those at connect with the resolved URL in hand.

Pinned as accept/reject through the model rather than through the published
schema: the shape is a value expression union, so nothing in `latest.json`
says anything about the authority inside it.
"""
import pytest
from pydantic import ValidationError

from analitiq.contracts.connector import HttpTransport

WITH_CREDENTIALS = [
    ("bare string", "https://user:pass@api.example.test/v1"),
    ("username only", "https://user@api.example.test/v1"),
    ("literal form", {"literal": "https://user:pass@api.example.test"}),
    (
        "template authority",
        {"template": "https://${secrets.user}:${secrets.password}@api.example.test"},
    ),
    (
        "template username only",
        {"template": "https://${secrets.token}@api.example.test"},
    ),
]

WITHOUT = [
    ("bare string", "https://api.example.test/v1"),
    ("port, no userinfo", "https://api.example.test:8443/v1"),
    ("literal form", {"literal": "https://api.example.test"}),
    ("host from a placeholder", {"template": "https://${connection.parameters.host}/v1"}),
    ("an @ in the path", "https://api.example.test/mail/a@b"),
    ("ref form", {"ref": "connection.discovered.api_url"}),
    ("omitted", None),
]


@pytest.mark.parametrize("label, base_url", WITH_CREDENTIALS)
def test_authored_userinfo_is_refused(label, base_url):
    with pytest.raises(ValidationError) as exc:
        HttpTransport(transport_type="http", base_url=base_url)
    assert "RULE-CTOR-066" in str(exc.value), label


@pytest.mark.parametrize("label, base_url", WITHOUT)
def test_a_base_url_without_credentials_parses(label, base_url):
    transport = HttpTransport(transport_type="http", base_url=base_url)
    assert transport.transport_type == "http"


def test_the_finding_points_at_the_authority():
    # The path is never the problem, so the detail the rule adds is the part
    # of the URL the author has to change.
    with pytest.raises(ValidationError) as exc:
        HttpTransport(
            transport_type="http",
            base_url="https://user:pass@api.example.test/very/long/prefix/v1",
        )
    assert "base_url authority 'user:pass@api.example.test'" in str(exc.value)


def test_a_url_the_parser_cannot_read_is_left_to_the_engine():
    # Whether a base URL is well-formed at all is refused at connect, where
    # the resolved value is in hand. Reporting THIS rule for that defect would
    # send the author looking for credentials that are not there.
    transport = HttpTransport(transport_type="http", base_url="https://[not-an-ip/v1")
    assert transport.base_url == "https://[not-an-ip/v1"
