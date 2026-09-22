"""Request models for validating a document set: `DocumentSet` and the
package request that carries one.

A malformed argument is refused when the request is built, as a
`ValidationError`. The published schema is graded against the same malformed
keys, so a JSON-Schema-only consumer refuses the key grammar the models refuse;
the document-and-directory conflict is enforced by the model alone, since JSON
Schema cannot express it.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from analitiq.contracts.shared.common import DOCUMENT_KEY_MAX_LENGTH, DOCUMENT_TEXT_MAX_LENGTH
from analitiq.contracts.validation_requests import (
    MAX_DOCUMENTS,
    DocumentSet,
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
)

PUBLISHED_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[4] / "schemas" / "validate-package-request"
     / "latest.json").read_text())

WELL_FORMED_DOCUMENTS = {
    "connector.json": "{}",
    "endpoints/widgets.json": "{}",
    ".hidden/a.json": "not json at all",
    "..a/b..json": "",
    "endpoints/.b": "",
}

MALFORMED_KEYS = {
    "empty": "",
    "leading slash": "/connector.json",
    "trailing slash": "endpoints/",
    "empty segment": "endpoints//widgets.json",
    "dot segment": "./connector.json",
    "inner dot segment": "endpoints/./widgets.json",
    "dot-dot segment": "endpoints/../connector.json",
    "bare dot-dot": "..",
    "nul in segment": "endpoints/a\x00b.json",
    "nul after dot": ".\x00",
    "nul after dot-dot": "..\x00",
}


def _validate(documents: dict) -> ValidatePackageRequest:
    return ValidatePackageRequest.model_validate(
        {"package": "connector-package", "documents": documents})


def _publish_validate(documents: dict) -> None:
    jsonschema.validate({"package": "connector-package", "documents": documents}, PUBLISHED_SCHEMA)


def test_accepts_relative_posix_keys():
    assert _validate(WELL_FORMED_DOCUMENTS).documents.root == WELL_FORMED_DOCUMENTS


def test_published_schema_accepts_what_the_model_accepts():
    _publish_validate(WELL_FORMED_DOCUMENTS)


def test_empty_set_is_not_a_request_error():
    # A package with no root document is a content problem, not a request error.
    assert _validate({}).documents.root == {}


@pytest.mark.parametrize("key", list(MALFORMED_KEYS.values()), ids=list(MALFORMED_KEYS))
def test_rejects_malformed_key(key):
    with pytest.raises(ValidationError):
        _validate({key: "{}"})


@pytest.mark.parametrize("key", list(MALFORMED_KEYS.values()), ids=list(MALFORMED_KEYS))
def test_published_schema_rejects_malformed_key(key):
    with pytest.raises(jsonschema.ValidationError):
        _publish_validate({key: "{}"})


def test_rendered_document_set_refuses_off_grammar_keys():
    # Pydantic renders a patterned key as `patternProperties` alone, which
    # forbids nothing; the published schema must close the map itself.
    schema = DocumentSet.model_json_schema()
    assert schema["additionalProperties"] is False
    (key_pattern,) = schema["patternProperties"]
    assert key_pattern.endswith(r"(?![\s\S])")


@pytest.mark.parametrize("keys", [
    ("endpoints", "endpoints/widgets.json"),
    ("connectors/wise/definition/connector.json/extra.json",
     "connectors/wise/definition/connector.json"),
    ("connectors", "connectors/wise/definition/connector.json"),
    # `-` and `.` sort before `/`, so a sibling sharing the document's name as a
    # prefix lands between the document and its descendant in string order.
    ("endpoints", "endpoints-v2.json", "endpoints.json", "endpoints/widgets.json"),
], ids=["document-then-child", "child-then-document", "ancestor-several-levels-up",
        "sibling-sorts-between"])
def test_rejects_key_that_is_a_document_and_a_directory(keys):
    with pytest.raises(ValidationError, match="directory"):
        _validate({k: "{}" for k in keys})


def test_shared_name_prefix_is_not_a_directory_conflict():
    documents = {"endpoints": "{}", "endpoints.json": "{}", "endpoints-v2/a.json": "{}"}
    assert _validate(documents).documents.root == documents


def test_directory_conflict_check_is_not_quadratic_in_key_depth():
    # Every key at the length ceiling and as deep as that allows, at the count
    # ceiling: a per-depth prefix rebuild takes seconds here.
    width = len(str(MAX_DOCUMENTS))
    depth = (DOCUMENT_KEY_MAX_LENGTH - width) // 2
    documents = {"a/" * depth + f"{i:0{width}}": "" for i in range(MAX_DOCUMENTS)}
    assert {len(key) for key in documents} == {DOCUMENT_KEY_MAX_LENGTH}
    started = time.perf_counter()
    _validate(documents)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("value", ["1", "{}", "[]", "null", "true"])
def test_rejects_non_string_value(value):
    with pytest.raises(ValidationError):
        ValidatePackageRequest.model_validate_json(
            f'{{"package": "connector-package", "documents": {{"connector.json": {value}}}}}')


@pytest.mark.parametrize("cast", [bytes, bytearray])
def test_bytes_like_value_is_coerced_to_text_with_its_byte_order_mark_intact(cast):
    """`bytes` and `bytearray` are what `DocumentText` does not refuse: pydantic
    decodes them in lax mode, byte-order mark and all. Pinned because it is a
    documented property of the request models rather than an accident — a
    consumer that read its files as bytes has its document graded as unreadable
    *content*, not rejected as a malformed argument — and because nothing else
    would notice the coercion tightening under a dependency bump.
    `model_validate_json` cannot reach it: no JSON source produces either.
    """
    text = "\ufeff{}"
    request = ValidatePackageRequest(
        package="connector-package", documents={"connector.json": cast(text.encode())})
    assert request.documents.root["connector.json"] == text

    single = ValidateSingleDocumentRequest(document=cast(b"{}"), entity="connector")
    assert single.document == "{}"


def test_other_buffer_shapes_are_still_refused():
    """The coercion above reaches those two and stops, so the sentences
    describing it name them rather than a bytes-like category: a `memoryview`
    over the same bytes is refused.
    """
    with pytest.raises(ValidationError):
        ValidatePackageRequest(
            package="connector-package", documents={"connector.json": memoryview(b"{}")})


def test_rejects_unknown_field():
    with pytest.raises(ValidationError):
        ValidatePackageRequest.model_validate(
            {"package": "connector-package", "documents": {}, "entity": "connector"})


def test_rejects_missing_documents():
    with pytest.raises(ValidationError):
        ValidatePackageRequest.model_validate({"package": "connector-package"})


def test_rejects_missing_package():
    with pytest.raises(ValidationError):
        ValidatePackageRequest.model_validate({"documents": {}})


def test_package_is_a_published_package_name():
    ValidatePackageRequest.model_validate({"package": "pipeline-package", "documents": {}})
    with pytest.raises(ValidationError):
        ValidatePackageRequest.model_validate({"package": "connector", "documents": {}})


SECRET_KEY = ".secrets/credentials.json"


def test_a_key_at_a_secret_location_of_the_named_package_is_refused():
    request = {"package": "connection-package", "documents": {"connection.json": "{}", SECRET_KEY: "{}"}}
    with pytest.raises(ValidationError, match=re.escape(repr(SECRET_KEY))):
        ValidatePackageRequest.model_validate(request)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(request, PUBLISHED_SCHEMA)


def test_a_location_is_secret_only_in_the_package_that_marks_it():
    request = {"package": "connector-package", "documents": {SECRET_KEY: "{}"}}
    ValidatePackageRequest.model_validate(request)
    jsonschema.validate(request, PUBLISHED_SCHEMA)


def test_document_count_ceiling():
    at_ceiling = {f"endpoints/{i}.json": "{}" for i in range(MAX_DOCUMENTS)}
    _validate(at_ceiling)
    _publish_validate(at_ceiling)
    over = at_ceiling | {"connector.json": "{}"}
    with pytest.raises(ValidationError):
        _validate(over)
    with pytest.raises(jsonschema.ValidationError):
        _publish_validate(over)


def test_document_text_ceiling():
    at_ceiling = {"connector.json": "x" * DOCUMENT_TEXT_MAX_LENGTH}
    _validate(at_ceiling)
    _publish_validate(at_ceiling)
    over = {"connector.json": "x" * (DOCUMENT_TEXT_MAX_LENGTH + 1)}
    with pytest.raises(ValidationError):
        _validate(over)
    with pytest.raises(jsonschema.ValidationError):
        _publish_validate(over)


def test_document_key_ceiling():
    at_ceiling = {"k" * DOCUMENT_KEY_MAX_LENGTH: "{}"}
    _validate(at_ceiling)
    _publish_validate(at_ceiling)
    over = {"k" * (DOCUMENT_KEY_MAX_LENGTH + 1): "{}"}
    with pytest.raises(ValidationError):
        _validate(over)
    with pytest.raises(jsonschema.ValidationError):
        _publish_validate(over)
