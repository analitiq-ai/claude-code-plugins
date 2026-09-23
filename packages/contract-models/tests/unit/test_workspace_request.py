"""The request to validate a workspace: document texts keyed by path from the
workspace root, and optionally the pipeline to run.

A malformed request is refused when it is built, and the published schema
refuses the same inputs, except where JSON Schema cannot say it (a named
pipeline the request holds no document of).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from analitiq.contracts.validation_requests import ValidateWorkspaceRequest

PUBLISHED = json.loads(
    (Path(__file__).resolve().parents[4] / "schemas" / "validate-workspace-request"
     / "latest.json").read_text())

DOCUMENTS = {
    "pipelines/manifest.json": "{}",
    "pipelines/orders/pipeline.json": "{}",
    "pipelines/orders/streams/a.json": "{}",
    "connections/pg/connection.json": "{}",
    "connectors/postgres/definition/connector.json": "{}",
    "README.md": "not located",
}
SECRET_KEY = "connections/pg/.secrets/credentials.json"


def _accepted_by_both(request: dict) -> ValidateWorkspaceRequest:
    jsonschema.validate(request, PUBLISHED)
    return ValidateWorkspaceRequest.model_validate(request)


def _refused_by_both(request: dict, match: str | None = None) -> None:
    with pytest.raises(ValidationError, match=match):
        ValidateWorkspaceRequest.model_validate(request)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(request, PUBLISHED)


def test_accepts_documents_keyed_from_the_workspace_root():
    assert _accepted_by_both({"documents": DOCUMENTS}).documents.root == DOCUMENTS


def test_the_pipeline_to_run_is_optional():
    assert ValidateWorkspaceRequest.model_validate({"documents": DOCUMENTS}).run_pipeline is None


def test_the_documents_are_a_document_set():
    _refused_by_both({"documents": {"/pipelines/manifest.json": "{}"}})


def test_rejects_unknown_field():
    _refused_by_both({"documents": {}, "package_kind": "pipeline"})


def test_a_key_at_a_secret_location_of_its_package_is_refused():
    _refused_by_both({"documents": DOCUMENTS | {SECRET_KEY: "{}"}}, match=re.escape(repr(SECRET_KEY)))


@pytest.mark.parametrize("key", [
    "connectors/postgres/.secrets/credentials.json",
    ".secrets/credentials.json",
    "connections/pg/x/.secrets/credentials.json",
], ids=["other-package", "workspace-root", "below-the-location"])
def test_a_secret_location_is_secret_only_in_its_own_package(key):
    _accepted_by_both({"documents": DOCUMENTS | {key: "{}"}})


def test_names_a_pipeline_it_holds_to_run():
    request = {"documents": DOCUMENTS, "run_pipeline": "pipelines/orders/"}
    assert _accepted_by_both(request).run_pipeline == "pipelines/orders/"


@pytest.mark.parametrize("run_pipeline", [
    "pipelines/orders",
    "pipelines/orders/pipeline.json",
    "pipelines/manifest.json",
    "connections/pg/",
    "pipelines/a/b/",
    "pipelines/orders/\n",
    "",
])
def test_the_pipeline_to_run_is_a_pipeline_directory(run_pipeline):
    _refused_by_both({"documents": DOCUMENTS, "run_pipeline": run_pipeline})


def test_the_pipeline_to_run_is_one_the_request_holds():
    # JSON Schema cannot relate one field's value to another's keys.
    with pytest.raises(ValidationError, match=re.escape(repr("pipelines/billing/"))):
        ValidateWorkspaceRequest.model_validate({"documents": DOCUMENTS, "run_pipeline": "pipelines/billing/"})
