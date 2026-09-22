"""Where each authored document of a pipeline package sits."""
from __future__ import annotations

from pydantic import ConfigDict

from analitiq.contracts.shared.common import DocumentPackage, package_locations


class PipelinePackage(DocumentPackage):
    """The authored documents of a pipeline package that are written against a published schema, keyed by path from the pipeline's own directory. Other files a package carries are not described here."""

    model_config = ConfigDict(json_schema_extra=package_locations)

    ROOT = "pipeline.json"
    ROOT_KIND = "pipeline"
    MEMBER_LOCATIONS = {
        r"^streams/[^/]+\.json$": "stream",
    }
