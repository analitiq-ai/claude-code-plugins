"""Requests to validate a set of authored documents supplied as text.

A request carries the documents, not a location to read them from. These
models gate the request's shape only: a request they refuse is malformed,
and a document's content — whether it parses, whether it satisfies its
contract — is not judged here.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, RootModel, StringConstraints, model_validator

from analitiq.contracts.shared.common import (
    DOCUMENT_KEY_MAX_LENGTH,
    DocumentText,
    ParseOnly,
    StrictModel,
    closed_true_end_keys,
)

# A coarse guard against an unbounded request, not a policy on package size:
# set far above what a real connector or pipeline package needs, so exceeding
# it is a request error rather than a large package.
MAX_DOCUMENTS = 2000

# A segment is any run of non-`/` characters except `.` and `..`. Written
# without lookahead, which pydantic-core's regex engine refuses.
_SEGMENT = r"(?:[^/.][^/]*|\.[^/.][^/]*|\.\.[^/]+)"
DOCUMENT_KEY_PATTERN = rf"^{_SEGMENT}(?:/{_SEGMENT})*$"

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


class ValidatePackageRequest(StrictModel):
    """A request to validate one connector or pipeline package, supplied as its
    documents."""

    documents: DocumentSet
