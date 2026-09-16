"""Request models for validating a document set: `DocumentSet` and the
connector-package and pipeline-package requests that carry one.

A malformed argument is refused when the request is built, so every case below
is a `ValidationError`, never a finding. The published schemas are graded
against the same keys, so a JSON-Schema-only consumer refuses what the models
refuse.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from analitiq.contracts.validation_requests import (
    MAX_DOCUMENT_TEXT_LENGTH,
    MAX_DOCUMENTS,
    DocumentSet,
    ValidateConnectorPackageRequest,
    ValidatePipelinePackageRequest,
)

REPO_ROOT = Path(__file__).resolve().parents[4]

REQUESTS = {
    "validate-connector-package-request": ValidateConnectorPackageRequest,
    "validate-pipeline-package-request": ValidatePipelinePackageRequest,
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
}


def _published_schema(resource: str) -> dict:
    return json.loads((REPO_ROOT / "schemas" / resource / "latest.json").read_text())


@pytest.fixture(params=sorted(REQUESTS))
def resource(request) -> str:
    return request.param


def test_accepts_relative_posix_keys(resource):
    documents = {
        "connector.json": "{}",
        "endpoints/widgets.json": "{}",
        ".hidden/a.json": "not json at all",
        "..a/b..json": "",
    }
    request = REQUESTS[resource].model_validate({"documents": documents})
    assert request.documents.root == documents


def test_empty_set_is_not_a_request_error(resource):
    # An empty package is a package missing its root document, which the
    # validator reports as a finding like any other missing root document.
    assert REQUESTS[resource].model_validate({"documents": {}}).documents.root == {}


@pytest.mark.parametrize("key", list(MALFORMED_KEYS.values()), ids=list(MALFORMED_KEYS))
def test_rejects_malformed_key(resource, key):
    with pytest.raises(ValidationError):
        REQUESTS[resource].model_validate({"documents": {key: "{}"}})


@pytest.mark.parametrize("key", list(MALFORMED_KEYS.values()), ids=list(MALFORMED_KEYS))
def test_published_schema_rejects_malformed_key(resource, key):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"documents": {key: "{}"}}, _published_schema(resource))


def test_rendered_document_set_refuses_off_grammar_keys():
    # Pydantic renders a patterned key as `patternProperties` alone, which
    # forbids nothing; the published schema must close the map itself.
    schema = DocumentSet.model_json_schema()
    assert schema["additionalProperties"] is False
    (key_pattern,) = schema["patternProperties"]
    assert key_pattern.endswith(r"(?![\s\S])")


def test_published_schema_accepts_what_the_model_accepts(resource):
    jsonschema.validate(
        {"documents": {"connector.json": "{}", "endpoints/w.json": "{}"}},
        _published_schema(resource))


@pytest.mark.parametrize("keys", [
    ("endpoints", "endpoints/widgets.json"),
    ("connectors/wise/definition/connector.json/extra.json",
     "connectors/wise/definition/connector.json"),
], ids=["document-then-child", "child-then-document"])
def test_rejects_key_that_is_a_document_and_a_directory(resource, keys):
    with pytest.raises(ValidationError, match="directory"):
        REQUESTS[resource].model_validate({"documents": {k: "{}" for k in keys}})


def test_shared_name_prefix_is_not_a_directory_conflict(resource):
    documents = {"endpoints": "{}", "endpoints.json": "{}", "endpoints-v2/a.json": "{}"}
    assert REQUESTS[resource].model_validate({"documents": documents}).documents.root == documents


@pytest.mark.parametrize("value", ["1", "{}", "[]", "null", "true"])
def test_rejects_non_string_value(resource, value):
    with pytest.raises(ValidationError):
        REQUESTS[resource].model_validate_json(f'{{"documents": {{"connector.json": {value}}}}}')


def test_rejects_unknown_field(resource):
    with pytest.raises(ValidationError):
        REQUESTS[resource].model_validate({"documents": {}, "entity": "connector"})


def test_rejects_missing_documents(resource):
    with pytest.raises(ValidationError):
        REQUESTS[resource].model_validate({})


def test_document_count_ceiling(resource):
    at_ceiling = {f"endpoints/{i}.json": "{}" for i in range(MAX_DOCUMENTS)}
    REQUESTS[resource].model_validate({"documents": at_ceiling})
    with pytest.raises(ValidationError):
        REQUESTS[resource].model_validate(
            {"documents": at_ceiling | {"connector.json": "{}"}})


def test_document_text_ceiling(resource):
    REQUESTS[resource].model_validate(
        {"documents": {"connector.json": "x" * MAX_DOCUMENT_TEXT_LENGTH}})
    with pytest.raises(ValidationError):
        REQUESTS[resource].model_validate(
            {"documents": {"connector.json": "x" * (MAX_DOCUMENT_TEXT_LENGTH + 1)}})


def test_published_schema_carries_the_ceilings(resource):
    document_set = DocumentSet.model_json_schema()
    assert document_set["maxProperties"] == MAX_DOCUMENTS
    (value_schema,) = document_set["patternProperties"].values()
    assert value_schema["maxLength"] == MAX_DOCUMENT_TEXT_LENGTH
    published = _published_schema(resource)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"documents": {"connector.json": "x" * (MAX_DOCUMENT_TEXT_LENGTH + 1)}}, published)
