"""Where each authored document of a connector package sits."""
from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, RootModel

from analitiq.contracts.shared.common import ParseOnly, closed_true_end_keys, schema_url_for

# Paths are read from the connector's own directory, the one holding `definition/`.
_DOCUMENT_LOCATIONS = {
    r"^definition/connector\.json$": "connector",
    r"^definition/type-map\.json$": "type-map",
    r"^definition/endpoints/[^/]+\.json$": "api-endpoint",
}


def _locate_documents(schema: dict[str, Any]) -> None:
    # A reference names the kind's `latest.json`, the URL each document declares
    # as its own `$schema`: a location keeps its kind across that schema's versions.
    schema["patternProperties"] = {
        pattern: {"$ref": schema_url_for(resource)}
        for pattern, resource in _DOCUMENT_LOCATIONS.items()
    }
    closed_true_end_keys(schema)


class ConnectorPackage(ParseOnly, RootModel[dict[str, Any]]):
    """The inventory of a connector package's artifacts, keyed by path from the connector's own directory."""

    model_config = ConfigDict(json_schema_extra=_locate_documents)
