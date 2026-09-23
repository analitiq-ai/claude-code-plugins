"""Where each package of a workspace sits."""
from __future__ import annotations

import re
from typing import Any

from pydantic import ConfigDict, RootModel

from analitiq.contracts.connection_package import ConnectionPackage
from analitiq.contracts.connector_package import ConnectorPackage
from analitiq.contracts.pipeline_package import PipelinePackage
from analitiq.contracts.shared.common import PATH_SEGMENT, DocumentPackage, ParseOnly, document_locations

#: Each published package schema's name, and the model it renders from.
PACKAGE_MODELS: dict[str, type[DocumentPackage]] = {
    "connector-package": ConnectorPackage,
    "connection-package": ConnectionPackage,
    "pipeline-package": PipelinePackage,
}

# Paths are read from the workspace root.
_DOCUMENT_LOCATIONS = {
    r"^pipelines/manifest\.json$": "pipeline-manifest",
}
_PACKAGE_DIRECTORIES = {
    rf"^pipelines/{PATH_SEGMENT}/$": "pipeline-package",
    rf"^connections/{PATH_SEGMENT}/$": "connection-package",
    rf"^connectors/{PATH_SEGMENT}/$": "connector-package",
}


class Workspace(ParseOnly, RootModel[dict[str, Any]]):
    """The packages of a workspace and the documents it holds directly, keyed by path from the workspace root. A key ending in `/` is a package's own directory, and its value is that package's documents keyed by path from that directory. Any other key is a document the workspace holds directly. Other files a workspace carries are not described here."""

    model_config = ConfigDict(json_schema_extra=document_locations(_DOCUMENT_LOCATIONS | _PACKAGE_DIRECTORIES))

    @classmethod
    def package_at(cls, key: str) -> tuple[str, type[DocumentPackage], str] | None:
        """For a key inside a package directory: that directory, the package's model and the key within the package; otherwise `None`."""
        for pattern, resource in _PACKAGE_DIRECTORIES.items():
            directory = re.match(pattern.removesuffix("$"), key)
            if directory:
                return directory.group(), PACKAGE_MODELS[resource], key[directory.end():]
        return None

    @classmethod
    def kind_at(cls, key: str) -> str | None:
        """The resource the document at `key` is written against, or `None` outside every location."""
        for pattern, resource in _DOCUMENT_LOCATIONS.items():
            if re.fullmatch(pattern, key):
                return resource
        located = cls.package_at(key)
        return located[1].kind_at(located[2]) if located else None

    @classmethod
    def secret_at(cls, key: str) -> bool:
        """Whether the document at `key` sits at a secret location of its package."""
        located = cls.package_at(key)
        return located is not None and located[1].secret_at(located[2])

    @classmethod
    def secret_patterns(cls) -> list[str]:
        """Every secret location of every package, as a pattern over keys from the workspace root."""
        return [
            directory.removesuffix("$") + secret.removeprefix("^")
            for directory, resource in _PACKAGE_DIRECTORIES.items()
            for secret in sorted(PACKAGE_MODELS[resource].SECRET_LOCATIONS)
        ]
