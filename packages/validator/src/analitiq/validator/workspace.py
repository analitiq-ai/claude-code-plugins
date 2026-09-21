"""Where each document of a workspace sits, read off the published location
tables: `analitiq.contracts.workspace.PACKAGE_LOCATIONS` places each package
under the workspace root, and each package's `DOCUMENT_LOCATIONS` places its
documents under the package's own directory. Nothing here states a location of
its own, so the validator cannot disagree with the published schemas about
where a document is authored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ._core import contract_model_domain

with contract_model_domain():
    from analitiq.contracts import connection_package, connector_package, pipeline_package
    from analitiq.contracts.workspace import PACKAGE_LOCATIONS

#: Each package resource `PACKAGE_LOCATIONS` names, mapped to its own table.
PACKAGE_DOCUMENT_LOCATIONS: dict[str, dict[str, str]] = {
    "pipeline-package": pipeline_package.DOCUMENT_LOCATIONS,
    "connection-package": connection_package.DOCUMENT_LOCATIONS,
    "connector-package": connector_package.DOCUMENT_LOCATIONS,
}


@dataclass(frozen=True)
class Located:
    """Where a key sits: the directory of the package holding it (`""` for the
    workspace root), that package's resource name (`None` for a document the
    workspace holds directly), and the resource name of the document itself."""

    package_root: str
    package: str | None
    kind: str


def document_kind(package: str, key: str) -> str | None:
    """The resource name `package`'s table gives `key`, read from the
    package's own directory, or `None` where no location matches it."""
    matched = _matches(PACKAGE_DOCUMENT_LOCATIONS[package], key)
    return matched[0] if matched else None


def locate(key: str) -> Located | None:
    """Where the workspace key `key` sits, or `None` where no published
    location reaches it.

    Raises `ValueError` where two published locations match one key: the
    published contract disagreeing with itself, which no author can fix.
    """
    candidates = [key[:end + 1] for end, char in enumerate(key) if char == "/"] + [key]
    found = []
    for root in candidates:
        for resource in _matches(PACKAGE_LOCATIONS, root):
            if resource in PACKAGE_DOCUMENT_LOCATIONS:
                kind = document_kind(resource, key[len(root):])
                if kind is not None:
                    found.append(Located(root, resource, kind))
            elif root == key:
                found.append(Located("", None, resource))
    if len(found) > 1:
        raise ValueError(f"{key!r} matches more than one published location: {found}")
    return found[0] if found else None


def _matches(table: dict[str, str], key: str) -> list[str]:
    """Every resource `table` locates at `key`, raising `ValueError` where there
    is more than one."""
    matched = [resource for pattern, resource in table.items() if re.fullmatch(pattern, key)]
    if len(matched) > 1:
        raise ValueError(f"{key!r} matches more than one published location: {matched}")
    return matched
