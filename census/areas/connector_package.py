"""Census entries for ``connector_package``: the table of where a connector
package's authored documents sit."""
from __future__ import annotations

from census.obligation import ProseObligation

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="ConnectorPackage",
        prose_hash="3f8cbd28cd13",
        structural=(
            "the published schema's `patternProperties` pin each of the three "
            "locations to the published schema its document is written against"
        ),
        waiver=(
            "that each key is a path from the connector's own directory and each "
            "value that document is the caller's content, which this model does "
            "not judge"
        ),
    ),
)
