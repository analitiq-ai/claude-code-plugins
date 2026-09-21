"""The index of the pipelines a workspace holds."""
from __future__ import annotations

from pydantic import Field

from analitiq.contracts.pipeline_package import PIPELINE_DOCUMENT_PATH
from analitiq.contracts.pipelines.config import PipelineStatus
from analitiq.contracts.shared.common import PATH_SEGMENT, StrictModel
from analitiq.contracts.shared.types import UUID_PATTERN

# One segment keeps the listed pipeline's directory a `pipelines/<dir>/` the workspace
# schema locates; the segment grammar excludes `.` and `..`, so the path cannot leave
# the manifest's directory.
_LISTED_PIPELINE_PATH = rf"^{PATH_SEGMENT}/{PIPELINE_DOCUMENT_PATH}$"


class PipelineManifestEntry(StrictModel):
    """One pipeline the index lists."""

    pipeline_id: str = Field(pattern=UUID_PATTERN, description="The listed pipeline's identifier.")
    status: PipelineStatus = Field(description="The listed pipeline's lifecycle status.")
    path: str = Field(
        pattern=_LISTED_PIPELINE_PATH,
        description="Path of the listed pipeline's `pipeline.json`, from the directory holding this index.",
    )


class PipelineManifest(StrictModel):
    """The index of the pipelines a workspace holds."""

    pipelines: list[PipelineManifestEntry] = Field(description="The pipelines the workspace holds.")
