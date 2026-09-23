"""Census entries for ``workspace``: the table of where each package of a
workspace sits."""
from __future__ import annotations

from census.obligation import ProseObligation

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="Workspace",
        prose_hash="8a44caeeaeb5",
        structural=(
            "`_DOCUMENT_LOCATIONS` and `_PACKAGE_DIRECTORIES` pin each location to the published schema of "
            "the package or document at it, and `document_locations` publishes "
            "that table and refuses every other key"
        ),
        waiver=(
            "that each key is a path from the workspace root and each value the "
            "caller's content, which this model does not judge; and that a "
            "connection or connector directory's name equals the id its document "
            "declares, which needs that document"
        ),
    ),
)
