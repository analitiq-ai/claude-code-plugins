"""Census entries for ``pipeline_package``: the table of where a pipeline
package's authored documents sit."""
from __future__ import annotations

from census.obligation import ProseObligation

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="PipelinePackage",
        prose_hash="ea65d1d40f3b",
        structural=(
            "`LOCATIONS` pins each location to the published schema its document "
            "is written against; the `DocumentPackage` base publishes that table "
            "into the schema and refuses every other key and a package without "
            "its `ROOT` document"
        ),
        waiver=(
            "that each key is a path from the pipeline's own directory and each "
            "value the document at that path is the caller's content, which this "
            "model does not judge"
        ),
    ),
)
