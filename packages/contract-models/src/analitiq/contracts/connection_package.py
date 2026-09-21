"""Where each authored document of a connection package sits."""
from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, RootModel

from analitiq.contracts.shared.common import ParseOnly, document_locations

# Paths are read from the connection's own directory, the one holding `connection.json`.
DOCUMENT_LOCATIONS = {
    r"^connection\.json$": "connection",
    r"^definition/type-map\.json$": "type-map",
    r"^definition/endpoints/[^/]+\.json$": "database-endpoint",
    r"^\.secrets/credentials\.json$": "credentials",
}


class ConnectionPackage(ParseOnly, RootModel[dict[str, Any]]):
    """The authored documents of a connection package that are written against a published schema, keyed by path from the connection's own directory. Other files a package carries are not described here."""

    model_config = ConfigDict(json_schema_extra=document_locations(DOCUMENT_LOCATIONS))
