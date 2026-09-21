"""Census entries for ``workspace``: the table of where each package of a
workspace sits."""
from __future__ import annotations

from census.obligation import ProseObligation

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="Workspace",
        prose_hash="8a44caeeaeb5",
        structural=(
            "`PACKAGE_LOCATIONS` pins each location to the published schema of "
            "the package or document at it, and `document_locations` publishes "
            "that table and refuses every other key"
        ),
        rule_ids=("RULE-PKG-036",),
        waiver=(
            "that each key is a path from the workspace root and each value the "
            "caller's content, which this model does not judge"
        ),
    ),
)
