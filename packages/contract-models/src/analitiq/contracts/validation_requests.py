"""Requests to validate authored documents supplied as text.

A request carries the documents, not a location to read them from. These
models gate the request's shape only: a request they refuse is malformed,
and a document's content — whether it parses, whether it satisfies its
contract — is not judged here.
"""
from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable, Iterable
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel, StringConstraints, model_validator

from analitiq.contracts.shared.common import (
    DOCUMENT_KEY_MAX_LENGTH,
    DocumentPackage,
    PATH_SEGMENT,
    DocumentText,
    ParseOnly,
    StrictModel,
    closed_true_end_keys,
    true_ended,
)
from analitiq.contracts.workspace import PACKAGE_MODELS, Workspace

# Coarse guards against an unbounded request, not policies on size: each is set
# far above what a real instance of its unit needs, so exceeding it is a request
# error rather than a large package or workspace. A workspace carries every
# package it references at once, so its guard is its own.
MAX_PACKAGE_DOCUMENTS = 2000
MAX_WORKSPACE_DOCUMENTS = 10_000

DOCUMENT_KEY_PATTERN = rf"^{PATH_SEGMENT}(?:/{PATH_SEGMENT})*$"

DocumentKey = Annotated[
    str, StringConstraints(pattern=DOCUMENT_KEY_PATTERN, max_length=DOCUMENT_KEY_MAX_LENGTH)
]


# `RootModel` cannot inherit `StrictModel` — pydantic rejects an `extra`
# setting on a root model — so the tree mixes in the parse-only policy
# directly, as `CredentialsFile` does. It carries no ceiling and no request
# field is typed as it: each request's set is a sibling subclass declaring its
# own, because pydantic takes a subtype instance without revalidating it, so a
# set that subclassed another would carry its ceiling into the other's request.
class _DocumentTree(ParseOnly, RootModel[dict[DocumentKey, DocumentText]]):

    @model_validator(mode="after")
    def _no_document_is_a_directory(self) -> _DocumentTree:
        # Every key is a file path in one tree, so a key that is also a
        # directory of another describes a tree no filesystem holds. Sorted as
        # segment tuples, a path's descendants follow it directly — nothing
        # else sorts between a tuple and its extensions — so comparing
        # neighbours finds every conflict. Sorting the strings would not:
        # `-` and `.` sort before `/`.
        paths = sorted(tuple(key.split("/")) for key in self.root)
        for path, following in zip(paths, paths[1:]):
            if following[:len(path)] == path:
                raise ValueError(
                    f"key {'/'.join(path)!r} names a document and is also a "
                    f"directory of key {'/'.join(following)!r}")
        return self


class DocumentSet(_DocumentTree):
    """Authored documents keyed by relative path, each value the document's
    file text. The request carrying the set names what a path is relative to.

    A key is a relative POSIX path with exactly one spelling per
    document; the key pattern carries the grammar. A key that names a
    document may not also be an ancestor directory of another key. The text
    is opaque to this model.
    """

    root: Annotated[
        dict[DocumentKey, DocumentText],
        Field(max_length=MAX_PACKAGE_DOCUMENTS, json_schema_extra=closed_true_end_keys),
    ]


class WorkspaceDocumentSet(_DocumentTree):
    """Authored documents spanning every package a workspace carries, keyed
    by relative path from the workspace root, each value the document's file
    text.

    A key is a relative POSIX path with exactly one spelling per
    document; the key pattern carries the grammar. A key that names a
    document may not also be an ancestor directory of another key. The text
    is opaque to this model.
    """

    root: Annotated[
        dict[DocumentKey, DocumentText],
        Field(max_length=MAX_WORKSPACE_DOCUMENTS, json_schema_extra=closed_true_end_keys),
    ]


#: Each package request's kind: the kind of the package's root document.
PACKAGE_KINDS: dict[str, type[DocumentPackage]] = {
    model.ROOT_KIND: model for model in PACKAGE_MODELS.values()}


def _refuses(patterns: list[str]) -> dict[str, Any]:
    return {"not": {"anyOf": [{"pattern": true_ended(pattern)} for pattern in patterns]}}


def _refuse_secret_keys(schema: dict[str, Any]) -> None:
    schema["allOf"] = [
        {"if": {"properties": {"package_kind": {"const": kind}}},
         "then": {"properties": {"documents": {"propertyNames": _refuses(sorted(model.SECRET_LOCATIONS))}}}}
        for kind, model in PACKAGE_KINDS.items() if model.SECRET_LOCATIONS
    ]


def _publish_workspace_request(schema: dict[str, Any]) -> None:
    properties = schema["properties"]
    properties["documents"]["propertyNames"] = _refuses(Workspace.secret_patterns())


def _no_secret_key(keys: Iterable[str], secret_at: Callable[[str], bool], where: str) -> None:
    held = sorted(key for key in keys if secret_at(key))
    if held:
        raise ValueError(f"keys at a secret location of {where}: {', '.join(map(repr, held))}")


class ValidatePackageRequest(StrictModel):
    """A request to validate one package, supplied as its documents keyed by path from the package's own directory."""

    model_config = ConfigDict(json_schema_extra=_refuse_secret_keys)

    package_kind: Literal[tuple(PACKAGE_KINDS)] = Field(  # type: ignore[valid-type]
        ..., description="The kind of the package the documents form, which is the kind of its root document.")
    documents: DocumentSet

    @model_validator(mode="after")
    def _no_document_at_a_secret_location(self) -> ValidatePackageRequest:
        _no_secret_key(self.documents.root, PACKAGE_KINDS[self.package_kind].secret_at, f"a {self.package_kind} package")
        return self


class ValidateWorkspaceRequest(StrictModel):
    """A request to validate a workspace, supplied as its documents keyed by path from the workspace root."""

    model_config = ConfigDict(json_schema_extra=_publish_workspace_request)

    documents: WorkspaceDocumentSet

    @model_validator(mode="after")
    def _no_document_at_a_secret_location(self) -> ValidateWorkspaceRequest:
        _no_secret_key(self.documents.root, Workspace.secret_at, "its package")
        return self


#: Written by `scripts/render_schemas.py document-schemas`: the names of the
#: published schemas whose root model declares `$schema`, each describing one
#: kind of authored document.
DOCUMENT_SCHEMAS_PATH = Path(__file__).with_name("document_schemas.json")
DOCUMENT_SCHEMAS_KEY = "document_schemas"
DOCUMENT_SCHEMA_NAMES: tuple[str, ...] = tuple(
    json.loads(DOCUMENT_SCHEMAS_PATH.read_text())[DOCUMENT_SCHEMAS_KEY])


class ValidateSingleDocumentRequest(StrictModel):
    """A request to validate one document, supplied as its file text."""

    document: DocumentText = Field(
        ..., description="The document's file text, unparsed.")
    document_kind: Literal[DOCUMENT_SCHEMA_NAMES] = Field(  # type: ignore[valid-type]
        ...,
        description="Name of the published schema the document is written against.",
    )
