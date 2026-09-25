"""The request to validate one document: its file text and the name of the
published document schema it is written against.

A malformed request is refused when it is built, so every rejection below is a
`ValidationError`, and the published schema refuses the same inputs.
"""
from __future__ import annotations

import json

import jsonschema
import pytest
from pydantic import ValidationError

from analitiq.contracts.shared.common import DOCUMENT_TEXT_MAX_LENGTH
from analitiq.contracts.validation_requests import (
    DOCUMENT_SCHEMA_NAMES,
    ValidateSingleDocumentRequest,
)
from render_schemas import rendered_latest

PUBLISHED = rendered_latest("validate-single-document-request")


def _json(document, kind) -> str:
    return json.dumps({"document": document, "document_kind": kind})


def _refused_by_both(raw: str) -> None:
    with pytest.raises(ValidationError):
        ValidateSingleDocumentRequest.model_validate_json(raw)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(json.loads(raw), PUBLISHED)


@pytest.mark.parametrize("kind", DOCUMENT_SCHEMA_NAMES)
def test_accepts_every_document_schema_name(kind):
    raw = _json("{}", kind)
    assert ValidateSingleDocumentRequest.model_validate_json(raw).document_kind == kind
    jsonschema.validate(json.loads(raw), PUBLISHED)


def test_empty_document_is_not_a_request_error():
    # Empty text is unreadable content, which the validator reports as a finding.
    kind = DOCUMENT_SCHEMA_NAMES[0]
    assert ValidateSingleDocumentRequest.model_validate_json(_json("", kind)).document == ""


def test_rejects_missing_document_kind():
    _refused_by_both(json.dumps({"document": "{}"}))


def test_rejects_missing_document():
    _refused_by_both(json.dumps({"document_kind": DOCUMENT_SCHEMA_NAMES[0]}))


# `credentials` is a published schema whose model declares no `$schema`, so it
# names no document.
@pytest.mark.parametrize("kind", ["credentials", "connector-package", "any", ""])
def test_rejects_document_kind_that_names_no_document_schema(kind):
    _refused_by_both(_json("{}", kind))


@pytest.mark.parametrize("document", [1, {}, [], None, True])
def test_rejects_non_string_document(document):
    _refused_by_both(_json(document, DOCUMENT_SCHEMA_NAMES[0]))


def test_rejects_unknown_field():
    _refused_by_both(json.dumps(
        {"document": "{}", "document_kind": DOCUMENT_SCHEMA_NAMES[0], "direction": "read"}))


def test_document_text_ceiling():
    kind = DOCUMENT_SCHEMA_NAMES[0]
    at_ceiling = _json("x" * DOCUMENT_TEXT_MAX_LENGTH, kind)
    ValidateSingleDocumentRequest.model_validate_json(at_ceiling)
    jsonschema.validate(json.loads(at_ceiling), PUBLISHED)
    _refused_by_both(_json("x" * (DOCUMENT_TEXT_MAX_LENGTH + 1), kind))
