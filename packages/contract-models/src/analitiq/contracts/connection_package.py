"""Where each authored document of a connection package sits."""
from __future__ import annotations

from pydantic import ConfigDict

from analitiq.contracts.shared.common import DocumentPackage, package_locations


class ConnectionPackage(DocumentPackage):
    """The authored documents of a connection package that are written against a published schema, keyed by path from the connection's own directory. Other files a package carries are not described here."""

    model_config = ConfigDict(json_schema_extra=package_locations)

    ROOT = "connection.json"
    ROOT_KIND = "connection"
    MEMBER_LOCATIONS = {
        r"^definition/type-map\.json$": "type-map",
        r"^definition/endpoints/[^/]+\.json$": "database-endpoint",
        r"^\.secrets/credentials\.json$": "credentials",
    }
