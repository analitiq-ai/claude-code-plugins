"""Where each authored document of a connector package sits."""
from __future__ import annotations

from pydantic import ConfigDict

from analitiq.contracts.shared.common import DocumentPackage, package_locations


class ConnectorPackage(DocumentPackage):
    """The authored documents of a connector package that are written against a published schema, keyed by path from the connector's own directory. Other files a package carries are not described here."""

    model_config = ConfigDict(json_schema_extra=package_locations)

    ROOT = "definition/connector.json"
    ROOT_KIND = "connector"
    MEMBER_LOCATIONS = {
        r"^definition/type-map\.json$": "type-map",
        r"^definition/endpoints/[^/]+\.json$": "api-endpoint",
    }
