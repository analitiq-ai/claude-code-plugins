"""Where each authored document of a connector package sits."""
from __future__ import annotations

from pydantic import ConfigDict

from analitiq.contracts.shared.common import DocumentPackage, document_locations


class ConnectorPackage(DocumentPackage):
    """The authored documents of a connector package that are written against a published schema, keyed by path from the connector's own directory. Other files a package carries are not described here."""

    model_config = ConfigDict(json_schema_extra=document_locations)

    LOCATIONS = {
        ("definition", r"connector\.json"): "connector",
        ("definition", r"type-map\.json"): "type-map",
        ("definition/endpoints", r"[^/]+\.json"): "api-endpoint",
    }
    ROOT = "definition/connector.json"
