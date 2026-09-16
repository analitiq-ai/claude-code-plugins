"""Census entries for ``validation_requests``: the document set and the
package requests that carry one."""
from __future__ import annotations

from census.obligation import ProseObligation

_CONTENT_IS_THE_VALIDATORS = (
    "whether the documents actually form the named package is their content, "
    "which the validator judges and reports as findings; the request model "
    "gates only the request's shape"
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="DocumentSet",
        prose_hash="49930d4ec90b",
        structural=(
            "every key is typed DocumentKey, whose DOCUMENT_KEY_PATTERN admits "
            "only `/`-separated segments that are non-empty and neither `.` nor "
            "`..`; the `_no_document_is_a_directory` model validator refuses a "
            "key that is also the directory of another; each value is typed "
            "DocumentText, a length-bounded string left unparsed"
        ),
    ),
    ProseObligation(
        model="ValidateConnectorPackageRequest",
        prose_hash="14bf72dead5e",
        structural="`documents` is a required DocumentSet under extra='forbid'",
        waiver=_CONTENT_IS_THE_VALIDATORS,
    ),
    ProseObligation(
        model="ValidatePipelinePackageRequest",
        prose_hash="274fbf44824c",
        structural="`documents` is a required DocumentSet under extra='forbid'",
        waiver=_CONTENT_IS_THE_VALIDATORS,
    ),
)
