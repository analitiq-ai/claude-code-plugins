"""Census entries for ``connector_package``: the table of where a connector
package's authored documents sit."""
from __future__ import annotations

from census.obligation import ProseObligation

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="ConnectorPackage",
        prose_hash="c4bb4f943706",
        descriptive=True,
    ),
)
