"""Census entries for ``validation_requests``: the document set, the package
and workspace requests that carry one, and the single-document request."""
from __future__ import annotations

from census.obligation import ProseObligation

_CONTENT_IS_NOT_JUDGED_HERE = (
    "which package the documents form, and whether they form one, is their content, "
    "which this model does not judge; it gates only the request's shape"
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="DocumentSet",
        prose_hash="f9a0d01efb9f",
        structural=(
            "every key is typed DocumentKey, whose DOCUMENT_KEY_PATTERN admits "
            "only `/`-separated segments that are non-empty, hold no NUL, and are "
            "neither `.` nor `..`; the `_no_document_is_a_directory` model validator refuses a "
            "key that is also a directory of another; each value is typed "
            "DocumentText, a length-bounded string left unparsed"
        ),
        waiver=(
            "that each key is the path its document occupies and each value that "
            "document's text is the caller's content, which this model does not judge"
        ),
    ),
    ProseObligation(
        model="ValidatePackageRequest",
        prose_hash="00710fba528a",
        structural=(
            "`package_kind` and `documents` are required under extra='forbid'; "
            "`documents` is a DocumentSet"
        ),
        waiver=(
            _CONTENT_IS_NOT_JUDGED_HERE + "; nor is it judged that each key is a "
            "path from the package's own directory"
        ),
    ),
    ProseObligation(
        model="ValidatePackageRequest", field="package_kind",
        prose_hash="b9c193dd6e1f",
        structural=(
            "a Literal over PACKAGE_KINDS, built from the ROOT_KIND of each model "
            "in PACKAGE_MODELS, each model held to the registered package "
            "resource rendered from it"
        ),
        waiver=(
            "whether the documents form the named package is their content, which "
            "this model does not judge"
        ),
    ),
    ProseObligation(
        model="ValidateSingleDocumentRequest",
        prose_hash="a02ec8e5a989",
        structural="`document` and `document_kind` are required under extra='forbid'",
        waiver=(
            "whether the text is the document `document_kind` names is its content, which "
            "this model does not judge; it gates only the request's shape"
        ),
    ),
    ProseObligation(
        model="ValidateSingleDocumentRequest", field="document",
        prose_hash="76e04fa58fd3",
        structural="typed DocumentText, a length-bounded string no validator parses",
    ),
    ProseObligation(
        model="ValidateSingleDocumentRequest", field="document_kind",
        prose_hash="93f351e3bea3",
        structural=(
            "a Literal over DOCUMENT_SCHEMA_NAMES, generated from the registered "
            "resources whose root model declares `$schema`"
        ),
        waiver=(
            "whether the document is written against the named schema is its "
            "content, which this model does not judge"
        ),
    ),
    ProseObligation(
        model="ValidateWorkspaceRequest",
        prose_hash="71068bb20329",
        structural="`documents` is required under extra='forbid' and is a DocumentSet",
        waiver=(
            "that each key is a path from the workspace root is the caller's "
            "content, which this model does not judge"
        ),
    ),
    ProseObligation(
        model="ValidateWorkspaceRequest", field="run_pipeline",
        prose_hash="2d452fe45ad6",
        structural=(
            "optional; its pattern is the workspace's pipeline-package directory "
            "patterns, and the `_the_pipeline_to_run_is_held` model validator "
            "refuses a directory no document key sits under"
        ),
        waiver=(
            "that the named pipeline is the one to be run is the caller's intent, "
            "and what validating as authored content means is the validator's "
            "grading; neither is judged by this model"
        ),
    ),
)
