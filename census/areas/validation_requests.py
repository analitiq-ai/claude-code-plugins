"""Census entries for ``validation_requests``: the document set and the
package request that carries one."""
from __future__ import annotations

from census.obligation import ProseObligation

_CONTENT_IS_NOT_JUDGED_HERE = (
    "which package the documents form, and whether they form one, is their content, "
    "which this model does not judge; it gates only the request's shape"
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="DocumentSet",
        prose_hash="bca16856d263",
        structural=(
            "every key is typed DocumentKey, whose DOCUMENT_KEY_PATTERN admits "
            "only `/`-separated segments that are non-empty and neither `.` nor "
            "`..`; the `_no_document_is_a_directory` model validator refuses a "
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
        prose_hash="68a6572fde0c",
        structural="`documents` is a required DocumentSet under extra='forbid'",
        waiver=_CONTENT_IS_NOT_JUDGED_HERE,
    ),
)
