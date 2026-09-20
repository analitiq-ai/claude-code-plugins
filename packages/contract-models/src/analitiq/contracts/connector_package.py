"""Where each authored document of a connector package sits."""
from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, RootModel

from analitiq.contracts.shared.common import ParseOnly, schema_url_for

# Paths are read from the package root. A pattern ends in `(?![\s\S])` rather
# than `$` for the reason `closed_true_end_keys` in `shared.common` gives: `$`
# lets a Python-`re` schema validator match before a trailing newline.
_DOCUMENT_LOCATIONS = {
    r"^definition/connector\.json(?![\s\S])": "connector",
    r"^definition/type-map\.json(?![\s\S])": "type-map",
    r"^definition/endpoints/[^/]+\.json(?![\s\S])": "api-endpoint",
}


def _locate_documents(schema: dict[str, Any]) -> None:
    schema["patternProperties"] = {
        pattern: {"$ref": schema_url_for(resource)}
        for pattern, resource in _DOCUMENT_LOCATIONS.items()
    }


class ConnectorPackage(ParseOnly, RootModel[dict[str, Any]]):
    """A connector package's authored documents, keyed by path from the package root."""

    model_config = ConfigDict(json_schema_extra=_locate_documents)
