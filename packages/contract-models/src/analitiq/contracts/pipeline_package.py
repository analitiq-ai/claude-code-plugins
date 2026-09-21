"""Where each authored document of a pipeline package sits."""
from __future__ import annotations

from pydantic import ConfigDict

from analitiq.contracts.shared.common import DocumentPackage, document_locations


class PipelinePackage(DocumentPackage):
    """The authored documents of a pipeline package that are written against a published schema, keyed by path from the pipeline's own directory. Other files a package carries are not described here."""

    model_config = ConfigDict(json_schema_extra=document_locations)

    LOCATIONS = {
        ("", r"pipeline\.json"): "pipeline",
        ("streams", r"[^/]+\.json"): "stream",
    }
    ROOT = "pipeline.json"
