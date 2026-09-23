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

from analitiq.contracts.validation_requests import MAX_WORKSPACE_DOCUMENTS, ValidateWorkspaceRequest

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


def test_document_count_ceiling():
    """A workspace carries every package at once, so it is bounded by a
    ceiling of its own, not a package's."""
    at_ceiling = {f"connectors/c{i}/definition/endpoints/e.json": "{}" for i in range(MAX_WORKSPACE_DOCUMENTS)}
    _accepted_by_both({"documents": at_ceiling})
    _refused_by_both({"documents": at_ceiling | {"README.md": "{}"}})
