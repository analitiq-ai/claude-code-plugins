"""Requests to validate authored documents supplied as text.

A request carries the documents, not a location to read them from. These
models gate the request's shape only: a request they refuse is malformed,
and a document's content — whether it parses, whether it satisfies its
contract — is not judged here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel, StringConstraints, model_validator

from analitiq.contracts.connection_package import ConnectionPackage
from analitiq.contracts.connector_package import ConnectorPackage
from analitiq.contracts.pipeline_package import PipelinePackage
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

# A coarse guard against an unbounded request, not a policy on package size:
# set far above what a real connector or pipeline package needs, so exceeding
# it is a request error rather than a large package.
MAX_DOCUMENTS = 2000

DOCUMENT_KEY_PATTERN = rf"^{PATH_SEGMENT}(?:/{PATH_SEGMENT})*$"

DocumentKey = Annotated[
    str, StringConstraints(pattern=DOCUMENT_KEY_PATTERN, max_length=DOCUMENT_KEY_MAX_LENGTH)
]


# `RootModel` cannot inherit `StrictModel` — pydantic rejects an `extra`
# setting on a root model — so `DocumentSet` mixes in the parse-only policy
# directly, as `CredentialsFile` does.
class DocumentSet(
    ParseOnly,
    RootModel[
        Annotated[
            dict[DocumentKey, DocumentText],
            Field(max_length=MAX_DOCUMENTS, json_schema_extra=closed_true_end_keys),
        ]
    ],
):
    """Authored documents keyed by the relative path each occupies in its
    package, each value the document's file text.

    A key is a package-relative POSIX path with exactly one spelling per
    document; the key pattern carries the grammar. A key that names a
    document may not also be an ancestor directory of another key. The text
    is opaque to this model.
    """

    @model_validator(mode="after")
    def _no_document_is_a_directory(self) -> DocumentSet:
        # Every key is a file path in one package, so a key that is also a
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


#: Each published package schema's name, and the model it renders from.
PACKAGE_MODELS: dict[str, type[DocumentPackage]] = {
    "connector-package": ConnectorPackage,
    "connection-package": ConnectionPackage,
    "pipeline-package": PipelinePackage,
}


def _refuse_secret_keys(schema: dict[str, Any]) -> None:
    schema["allOf"] = [
        {"if": {"properties": {"package": {"const": name}}},
         "then": {"properties": {"documents": {"propertyNames": {
             "not": {"anyOf": [{"pattern": true_ended(pattern)} for pattern in sorted(model.SECRET_LOCATIONS)]}}}}}}
        for name, model in PACKAGE_MODELS.items() if model.SECRET_LOCATIONS
    ]


class ValidatePackageRequest(StrictModel):
    """A request to validate one package, supplied as its documents."""

    model_config = ConfigDict(json_schema_extra=_refuse_secret_keys)

    package: Literal[tuple(PACKAGE_MODELS)] = Field(  # type: ignore[valid-type]
        ..., description="Name of the published package schema the documents form.")
    documents: DocumentSet

    @model_validator(mode="after")
    def _no_document_at_a_secret_location(self) -> ValidatePackageRequest:
        package = PACKAGE_MODELS[self.package]
        held = sorted(key for key in self.documents.root if package.secret_at(key))
        if held:
            raise ValueError(f"keys at a secret location of {self.package}: {', '.join(map(repr, held))}")
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
    entity: Literal[DOCUMENT_SCHEMA_NAMES] = Field(  # type: ignore[valid-type]
        ...,
        description="Name of the published schema the document is written against.",
    )
