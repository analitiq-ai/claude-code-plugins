"""Where each authored document of a connection package sits."""
from __future__ import annotations

from pydantic import ConfigDict

from analitiq.contracts.shared.common import DocumentPackage, document_locations


class ConnectionPackage(DocumentPackage):
    """The authored documents of a connection package that are written against a published schema, keyed by path from the connection's own directory. Other files a package carries are not described here."""

    model_config = ConfigDict(json_schema_extra=document_locations)

    LOCATIONS = {
        ("", r"connection\.json"): "connection",
        ("definition", r"type-map\.json"): "type-map",
        ("definition/endpoints", r"[^/]+\.json"): "database-endpoint",
        (".secrets", r"credentials\.json"): "credentials",
    }
    ROOT = "connection.json"
