"""Where each authored document of a connector package sits."""
from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, RootModel

from analitiq.contracts.shared.common import ParseOnly, document_locations

# Paths are read from the connector's own directory, the one holding `definition/`.
DOCUMENT_LOCATIONS = {
    r"^definition/connector\.json$": "connector",
    r"^definition/type-map\.json$": "type-map",
    r"^definition/endpoints/[^/]+\.json$": "api-endpoint",
}


class ConnectorPackage(ParseOnly, RootModel[dict[str, Any]]):
    """The authored documents of a connector package that are written against a published schema, keyed by path from the connector's own directory. Other files a package carries are not described here."""

    model_config = ConfigDict(json_schema_extra=document_locations(DOCUMENT_LOCATIONS))
