"""Where each package of a workspace sits."""
from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, RootModel

from analitiq.contracts.shared.common import PATH_SEGMENT, ParseOnly, document_locations

# Paths are read from the workspace root.
PACKAGE_LOCATIONS = {
    r"^pipelines/manifest\.json$": "pipeline-manifest",
    rf"^pipelines/{PATH_SEGMENT}/$": "pipeline-package",
    rf"^connections/{PATH_SEGMENT}/$": "connection-package",
    rf"^connectors/{PATH_SEGMENT}/$": "connector-package",
}


class Workspace(ParseOnly, RootModel[dict[str, Any]]):
    """The packages of a workspace and the documents it holds directly, keyed by path from the workspace root. A key ending in `/` is a package's own directory, and its value is that package's documents keyed by path from that directory. Any other key is a document the workspace holds directly. Other files a workspace carries are not described here."""

    model_config = ConfigDict(json_schema_extra=document_locations(PACKAGE_LOCATIONS))
