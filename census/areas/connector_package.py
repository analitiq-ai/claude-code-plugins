"""Census entries for ``connector_package``: the table of where a connector
package's authored documents sit."""
from __future__ import annotations

from census.obligation import ProseObligation

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="ConnectorPackage",
        prose_hash="702528f1d48b",
        structural=(
            "`LOCATIONS` pins each location to the published schema its document "
            "is written against; the `DocumentPackage` base publishes that table "
            "into the schema and refuses every other key and a package without "
            "its `ROOT` document"
        ),
        waiver=(
            "that each key is a path from the connector's own directory and each "
            "value that document is the caller's content, which this model does "
            "not judge"
        ),
    ),
)
