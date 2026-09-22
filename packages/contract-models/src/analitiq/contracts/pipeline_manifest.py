"""The index of the pipelines a workspace holds."""
from __future__ import annotations

import re
from operator import attrgetter

from pydantic import Field, model_validator

from analitiq.contracts.pipeline_package import PipelinePackage
from analitiq.contracts.pipelines.config import PipelineStatus
from analitiq.contracts.shared.common import PATH_SEGMENT, StrictModel, true_ended
from analitiq.contracts.shared.rules import find_duplicates, violation
from analitiq.contracts.shared.types import UUID_PATTERN

# One segment keeps the listed pipeline's directory a `pipelines/<dir>/` the workspace
# schema locates; the segment grammar excludes `.` and `..`, so the path cannot leave
# the manifest's directory.
_LISTED_PIPELINE_PATH = rf"^{PATH_SEGMENT}/{re.escape(PipelinePackage.ROOT)}$"


class PipelineManifestEntry(StrictModel):
    """One pipeline the index lists."""

    pipeline_id: str = Field(pattern=UUID_PATTERN, description="The listed pipeline's identifier.")
    status: PipelineStatus = Field(description="The listed pipeline's lifecycle status.")
    path: str = Field(
        pattern=_LISTED_PIPELINE_PATH,
        json_schema_extra={"pattern": true_ended(_LISTED_PIPELINE_PATH)},
        description="Path of the listed pipeline's `pipeline.json`, from the directory holding this index.",
    )


class PipelineManifest(StrictModel):
    """The index of the pipelines a workspace holds."""

    pipelines: list[PipelineManifestEntry] = Field(description="The pipelines the workspace holds.")

    @model_validator(mode="after")
    def _each_pipeline_listed_once(self) -> "PipelineManifest":
        """RULE-PIPE-020: one entry per pipeline."""
        for field in ("pipeline_id", "path"):
            dups = find_duplicates(self.pipelines, key=attrgetter(field))
            if dups:
                raise violation("RULE-PIPE-020", "pipeline-listed-twice", f"{field}={dups!r}")
        return self
