"""Where a workspace key sits, read off the published location tables.

`analitiq.validator.workspace.locate` answers from `analitiq.contracts.workspace
.PACKAGE_LOCATIONS` and each package's `DOCUMENT_LOCATIONS`, never from a table
of its own, so these cases pin what it reads rather than restating the layout:
every expected `kind` is a resource name one of those tables publishes.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("key, expected", [
    ("pipelines/p/pipeline.json", ("pipelines/p/", "pipeline-package", "pipeline")),
    ("pipelines/p/streams/orders.json", ("pipelines/p/", "pipeline-package", "stream")),
    ("connections/c/connection.json", ("connections/c/", "connection-package", "connection")),
    ("connections/c/definition/type-map.json",
     ("connections/c/", "connection-package", "type-map")),
    ("connections/c/definition/endpoints/e.json",
     ("connections/c/", "connection-package", "database-endpoint")),
    ("connections/c/.secrets/credentials.json",
     ("connections/c/", "connection-package", "credentials")),
    ("connectors/k/definition/connector.json",
     ("connectors/k/", "connector-package", "connector")),
    ("connectors/k/definition/endpoints/e.json",
     ("connectors/k/", "connector-package", "api-endpoint")),
    ("pipelines/manifest.json", ("", None, "pipeline-manifest")),
])
def test_a_located_key_names_its_package_root_package_and_kind(validator, key, expected):
    from analitiq.validator.workspace import Located, locate

    assert locate(key) == Located(*expected)


@pytest.mark.parametrize("key", [
    "README.md",
    "pipelines/p/notes.json",
    "pipelines/p/streams/nested/orders.json",
    "connections/c/definition/endpoints/nested/e.json",
    "connections/connection.json",
    "elsewhere/p/pipeline.json",
])
def test_a_key_no_published_location_matches_is_not_located(validator, key):
    from analitiq.validator.workspace import locate

    assert locate(key) is None


def test_a_key_two_published_locations_match_raises(validator, monkeypatch):
    """Two locations claiming one key is the published contract disagreeing
    with itself, which no author can fix, so it is raised, not reported."""
    from analitiq.validator import workspace

    tables = dict(workspace.PACKAGE_DOCUMENT_LOCATIONS)
    tables["pipeline-package"] = {**tables["pipeline-package"], r"^streams/.+$": "stream-copy"}
    monkeypatch.setattr(workspace, "PACKAGE_DOCUMENT_LOCATIONS", tables)
    with pytest.raises(ValueError, match="more than one published location"):
        workspace.locate("pipelines/p/streams/orders.json")


def test_every_published_package_has_a_document_table(validator):
    """The table naming each package's document locations covers every package
    the workspace locates, so a package the workspace starts locating is read
    rather than silently skipped."""
    from analitiq.contracts.workspace import PACKAGE_LOCATIONS
    from analitiq.validator.workspace import PACKAGE_DOCUMENT_LOCATIONS

    packages = {resource for resource in PACKAGE_LOCATIONS.values()
                if resource.endswith("-package")}
    assert packages, "no package located: nothing was measured"
    assert set(PACKAGE_DOCUMENT_LOCATIONS) == packages
