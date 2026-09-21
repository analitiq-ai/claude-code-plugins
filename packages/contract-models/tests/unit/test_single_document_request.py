"""The request to validate one document: its file text and the name of the
published document schema it is written against.

A malformed request is refused when it is built, so every rejection below is a
`ValidationError`, and the published schema refuses the same inputs.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from analitiq.contracts.shared.common import DOCUMENT_TEXT_MAX_LENGTH
from analitiq.contracts.validation_requests import (
    DOCUMENT_SCHEMA_NAMES,
    ValidateSingleDocumentRequest,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PUBLISHED = json.loads(
    (REPO_ROOT / "schemas" / "validate-single-document-request" / "latest.json").read_text())


def _json(document, entity) -> str:
    return json.dumps({"document": document, "entity": entity})


def _refused_by_both(raw: str) -> None:
    with pytest.raises(ValidationError):
        ValidateSingleDocumentRequest.model_validate_json(raw)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(json.loads(raw), PUBLISHED)


@pytest.mark.parametrize("entity", DOCUMENT_SCHEMA_NAMES)
def test_accepts_every_document_schema_name(entity):
    raw = _json("{}", entity)
    assert ValidateSingleDocumentRequest.model_validate_json(raw).entity == entity
    jsonschema.validate(json.loads(raw), PUBLISHED)


def test_empty_document_is_not_a_request_error():
    # Empty text is unreadable content, which the validator reports as a finding.
    entity = DOCUMENT_SCHEMA_NAMES[0]
    assert ValidateSingleDocumentRequest.model_validate_json(_json("", entity)).document == ""


def test_rejects_missing_entity():
    _refused_by_both(json.dumps({"document": "{}"}))


def test_rejects_missing_document():
    _refused_by_both(json.dumps({"entity": DOCUMENT_SCHEMA_NAMES[0]}))


# `credentials` is a published schema whose model declares no `$schema`, so it
# names no document.
@pytest.mark.parametrize("entity", ["credentials", "connector-package", "any", ""])
def test_rejects_entity_that_names_no_document_schema(entity):
    _refused_by_both(_json("{}", entity))


@pytest.mark.parametrize("document", [1, {}, [], None, True])
def test_rejects_non_string_document(document):
    _refused_by_both(_json(document, DOCUMENT_SCHEMA_NAMES[0]))


def test_rejects_unknown_field():
    _refused_by_both(json.dumps(
        {"document": "{}", "entity": DOCUMENT_SCHEMA_NAMES[0], "direction": "read"}))


def test_document_text_ceiling():
    entity = DOCUMENT_SCHEMA_NAMES[0]
    at_ceiling = _json("x" * DOCUMENT_TEXT_MAX_LENGTH, entity)
    ValidateSingleDocumentRequest.model_validate_json(at_ceiling)
    jsonschema.validate(json.loads(at_ceiling), PUBLISHED)
    _refused_by_both(_json("x" * (DOCUMENT_TEXT_MAX_LENGTH + 1), entity))
