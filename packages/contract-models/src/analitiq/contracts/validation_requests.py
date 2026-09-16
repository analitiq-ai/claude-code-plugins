"""Requests to validate a set of authored documents supplied as text.

A request carries the documents, not a location to read them from. These
models gate the request's shape only: a request they refuse is malformed,
and a document's content — whether it parses, whether it satisfies its
contract — is not judged here.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, RootModel, StringConstraints, model_validator

from analitiq.contracts.shared.common import ParseOnly, StrictModel, closed_true_end_keys

# Coarse guards against an unbounded request, not a policy on package size:
# each is set far above what a real connector or pipeline package needs, so
# one that exceeds a ceiling is a malformed request rather than a large package.
MAX_DOCUMENTS = 2000
MAX_DOCUMENT_TEXT_LENGTH = 1_048_576

# A segment is any run of non-`/` characters except `.` and `..`. Written
# without lookahead, which pydantic-core's regex engine refuses.
_SEGMENT = r"(?:[^/.][^/]*|\.[^/.][^/]*|\.\.[^/]+)"
DOCUMENT_KEY_PATTERN = rf"^{_SEGMENT}(?:/{_SEGMENT})*$"

DocumentKey = Annotated[str, StringConstraints(pattern=DOCUMENT_KEY_PATTERN)]
DocumentText = Annotated[str, StringConstraints(max_length=MAX_DOCUMENT_TEXT_LENGTH)]

# `RootModel` cannot inherit `StrictModel` — pydantic rejects an `extra`
# setting on a root model — so this mixes in the parse-only policy directly,
# as `CredentialsFile` does.


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
    document may not also be the directory of another key. The text is
    opaque to this model.
    """

    @model_validator(mode="after")
    def _no_document_is_a_directory(self) -> DocumentSet:
        # Every key is a file path in one package, so a key that is also a
        # directory prefix of another describes a tree no filesystem holds.
        keys = self.root.keys()
        for key in sorted(keys):
            parts = key.split("/")
            for depth in range(1, len(parts)):
                directory = "/".join(parts[:depth])
                if directory in keys:
                    raise ValueError(
                        f"key {directory!r} names a document and is also the "
                        f"directory of key {key!r}")
        return self


class ValidateConnectorPackageRequest(StrictModel):
    """A request to validate one connector package, supplied as its documents."""

    documents: DocumentSet


class ValidatePipelinePackageRequest(StrictModel):
    """A request to validate one pipeline package, supplied as its documents."""

    documents: DocumentSet
