"""Where each authored document of a pipeline package sits."""
from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, RootModel

from analitiq.contracts.shared.common import ParseOnly, document_locations

# Paths are read from the pipeline's own directory, the one holding `pipeline.json`.
_DOCUMENT_LOCATIONS = {
    r"^pipeline\.json$": "pipeline",
    r"^streams/[^/]+\.json$": "stream",
}


class PipelinePackage(ParseOnly, RootModel[dict[str, Any]]):
    """The authored documents of a pipeline package that are written against a published schema, keyed by path from the pipeline's own directory. Other files a package carries are not described here."""

    model_config = ConfigDict(json_schema_extra=document_locations(_DOCUMENT_LOCATIONS))
