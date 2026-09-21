"""Census entries for ``connector_package``: the inventory of where a connector
package's artifacts sit."""
from __future__ import annotations

from census.obligation import ProseObligation

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="ConnectorPackage",
        prose_hash="c54ea4d7b814",
        structural=(
            "`_DOCUMENT_LOCATIONS` pins each location to the published schema its "
            "artifact is written against, `_locate_documents` publishes that "
            "table into the schema, and `closed_true_end_keys` refuses every "
            "other key"
        ),
        waiver=(
            "that each key is a path from the connector's own directory and each "
            "value that artifact is the caller's content, which this model does "
            "not judge"
        ),
    ),
)
