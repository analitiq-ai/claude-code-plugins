"""Census entries for ``pipeline_manifest``: the index of the pipelines a
workspace holds."""
from __future__ import annotations

from census.obligation import ProseObligation

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    ProseObligation(
        model="PipelineManifest", descriptive=True,
        prose_hash="5b1cab4a8214",
    ),
    ProseObligation(
        model="PipelineManifestEntry", descriptive=True,
        prose_hash="76a1d637d036",
    ),
    ProseObligation(
        model="PipelineManifest", field="pipelines", descriptive=True,
        prose_hash="932fa35d9659",
    ),
    ProseObligation(
        model="PipelineManifestEntry", field="pipeline_id",
        prose_hash="ad80b268efd6",
        structural="Field(pattern=UUID_PATTERN) fixes the identifier grammar",
    ),
    ProseObligation(
        model="PipelineManifestEntry", field="status",
        prose_hash="fdf6a312609e",
        structural="the PipelineStatus Literal is the lifecycle vocabulary",
    ),
    ProseObligation(
        model="PipelineManifestEntry", field="path",
        prose_hash="a45c918b947c",
        structural=(
            "Field(pattern=_LISTED_PIPELINE_PATH) confines the path to the "
            "`pipeline.json` of one pipeline directory beside this index"
        ),
        waiver=(
            "that a `pipeline.json` exists at that path, and that its "
            "`pipeline_id` equals the entry's, needs a second document, which "
            "this model does not have"
        ),
    ),
)
