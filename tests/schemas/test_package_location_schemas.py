"""A package schema states where each authored document of a package sits and which
published schema it is written against — and nothing else.

It is a table of locations. A document's shape is the published schema its
location points at, so a package schema holds no shape of its own. Every package
schema is graded here against the same properties, so a new package is one entry
in `PACKAGES`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts", "render_schemas")

import render_schemas  # noqa: E402
from analitiq.contracts.shared.common import schema_url_for  # noqa: E402
from analitiq.contracts.workspace import PACKAGE_MODELS, Workspace  # noqa: E402

# Per package: a sample key at each location and the schema it is written against,
# the sample keys at a location that holds secret values, and keys that sit outside
# every location. The outside keys are chosen to tell an
# anchored pattern from an unanchored one (a prefixed key, trailing newlines).
PACKAGES = {
    "connector-package": {
        "located": {
            "definition/connector.json": "connector",
            "definition/type-map.json": "type-map",
            "definition/endpoints/customers.json": "api-endpoint",
        },
        "outside": (
            "definition/endpoints/a/b.json",
            "definition/endpoints/.gitkeep",
            "vendor/definition/connector.json",
            "definition/connector.json\n",
            "definition/type-map.json\n",
            "definition/endpoints/customers.json\n",
            "connector.json",
            "README.md",
        ),
    },
    "connection-package": {
        "located": {
            "connection.json": "connection",
            "definition/type-map.json": "type-map",
            "definition/endpoints/customers.json": "database-endpoint",
            ".secrets/credentials.json": "credentials",
        },
        "secret": {".secrets/credentials.json"},
        "outside": (
            "definition/connection.json",
            "definition/endpoints/a/b.json",
            "definition/endpoints/.gitkeep",
            "vendor/connection.json",
            "vendor/.secrets/credentials.json",
            ".secrets/credentials.json\n",
            ".secrets/other.json",
            "vendor/definition/type-map.json",
            "connection.json\n",
            "definition/type-map.json\n",
            "definition/endpoints/customers.json\n",
            "definition/connector.json",
            "README.md",
        ),
    },
    "pipeline-package": {
        "located": {
            "pipeline.json": "pipeline",
            "streams/orders.json": "stream",
        },
        "outside": (
            "streams/a/b.json",
            "streams/.gitkeep",
            "vendor/pipeline.json",
            "vendor/streams/orders.json",
            "pipeline.json\n",
            "streams/orders.json\n",
            "pipelines/x/pipeline.json",
            "connection.json",
            "README.md",
        ),
    },
    "workspace": {
        "located": {
            "pipelines/manifest.json": "pipeline-manifest",
            "pipelines/orders/": "pipeline-package",
            "connections/0b1f0c9e-2c7a-4d1e-9b6a-3f5e8d2c1a47/": "connection-package",
            "connectors/postgres/": "connector-package",
        },
        "outside": (
            "pipelines/manifest.json\n",
            "pipelines/orders/\n",
            "connectors/postgres/\n",
            "pipelines/orders/pipeline.json",
            "pipelines/orders",
            "pipelines/",
            "pipelines/../",
            "pipelines/./",
            "connections/a/b/",
            "connectors/",
            "vendor/connectors/postgres/",
            "vendor/pipelines/manifest.json",
            "manifest.json",
            "README.md",
        ),
    },
}

package = pytest.mark.parametrize("resource", sorted(PACKAGES))


def _rendered(resource: str) -> dict:
    return render_schemas.render_latest(render_schemas.get_resource(resource), "1.0.0")


def _table(resource: str) -> dict[str, str]:
    """Location pattern -> the published schema URL a document there is written against."""
    return {pattern: node["$ref"]
            for pattern, node in _rendered(resource)["patternProperties"].items()}


def _written_against(resource: str, key: str) -> set[str]:
    return {ref for pattern, ref in _table(resource).items() if re.search(pattern, key)}


@package
def test_each_authored_document_is_located_and_pointed_at_its_published_schema(resource):
    for key, kind in PACKAGES[resource]["located"].items():
        assert _written_against(resource, key) == {schema_url_for(kind)}, key


@package
def test_a_location_outside_the_table_is_written_against_nothing(resource):
    for key in PACKAGES[resource]["outside"]:
        assert _written_against(resource, key) == set(), key


@package
def test_the_schema_holds_locations_references_and_secret_marks_only(resource):
    rendered = _rendered(resource)
    assert all(set(node) - {"x-secret"} == {"$ref"} for node in rendered["patternProperties"].values())
    assert "$defs" not in rendered
    assert "properties" not in rendered


@package
def test_a_location_is_marked_secret_exactly_when_it_holds_secret_values(resource):
    nodes = _rendered(resource)["patternProperties"]
    for key in PACKAGES[resource]["located"]:
        marks = {nodes[pattern].get("x-secret") for pattern in nodes if re.search(pattern, key)}
        expected = True if key in PACKAGES[resource].get("secret", ()) else None
        assert marks == {expected}, key


@package
def test_no_location_beyond_the_table_is_admitted(resource):
    assert _rendered(resource)["additionalProperties"] is False


@package
def test_every_reference_names_a_registered_schema(resource):
    published = {schema_url_for(name) for name in render_schemas.RESOURCES_BY_NAME}
    assert set(_table(resource).values()) <= published


# The workspace is a table of packages, not a package of documents: it has no root.
document_package = pytest.mark.parametrize("resource", sorted(PACKAGE_MODELS))


def _root(resource: str) -> str:
    return PACKAGE_MODELS[resource].ROOT


def test_the_package_models_are_the_registered_package_schemas():
    assert set(PACKAGE_MODELS) == set(PACKAGES) - {"workspace"}
    assert set(PACKAGE_MODELS) == set(PACKAGES["workspace"]["located"].values()) - {"pipeline-manifest"}
    for name, model in PACKAGE_MODELS.items():
        assert render_schemas.get_resource(name).adapter._type is model  # skipcq: PYL-W0212


@document_package
def test_the_root_document_is_required(resource):
    assert _rendered(resource)["required"] == [_root(resource)]


@document_package
def test_the_model_names_the_kind_at_each_location_and_none_outside(resource):
    model = PACKAGE_MODELS[resource]
    for key, kind in PACKAGES[resource]["located"].items():
        assert model.kind_at(key) == kind, key
    for key in PACKAGES[resource]["outside"]:
        assert model.kind_at(key) is None, key


@document_package
def test_the_model_names_each_secret_location_and_nothing_else(resource):
    model = PACKAGE_MODELS[resource]
    for key in PACKAGES[resource]["located"]:
        assert model.secret_at(key) is (key in PACKAGES[resource].get("secret", ())), key
    for key in PACKAGES[resource]["outside"]:
        assert model.secret_at(key) is False, key


@document_package
def test_the_model_admits_a_package_holding_its_root(resource):
    model = PACKAGE_MODELS[resource]
    model.model_validate(dict.fromkeys(PACKAGES[resource]["located"], {}))


@document_package
def test_the_model_refuses_a_package_without_its_root(resource):
    from pydantic import ValidationError
    located = dict.fromkeys(PACKAGES[resource]["located"], {})
    del located[_root(resource)]
    with pytest.raises(ValidationError, match=re.escape(repr(_root(resource)))):
        PACKAGE_MODELS[resource].model_validate(located)


@document_package
def test_the_model_refuses_a_key_outside_the_table(resource):
    from pydantic import ValidationError
    key = PACKAGES[resource]["outside"][0]
    with pytest.raises(ValidationError, match=re.escape(repr(key))):
        PACKAGE_MODELS[resource].model_validate({_root(resource): {}, key: {}})


def test_a_secret_location_must_be_one_of_the_package_member_locations():
    from analitiq.contracts.shared.common import DocumentPackage
    with pytest.raises(TypeError, match="secret locations outside"):
        class _Package(DocumentPackage):  # noqa: F841  # skipcq: PTC-W0065
            ROOT = "root.json"
            ROOT_KIND = "connection"
            MEMBER_LOCATIONS = {r"^a\.json$": "credentials"}
            SECRET_LOCATIONS = frozenset({r"^b\.json$"})


# A workspace key is a package directory followed by a key of that package, or
# a document the workspace holds directly.
_WORKSPACE_DIRECTORIES = {
    directory: resource for directory, resource in PACKAGES["workspace"]["located"].items()
    if directory.endswith("/")}


@pytest.mark.parametrize("directory", sorted(_WORKSPACE_DIRECTORIES))
def test_the_workspace_names_the_kind_at_each_package_location_and_none_outside(directory):
    table = PACKAGES[_WORKSPACE_DIRECTORIES[directory]]
    for key, kind in table["located"].items():
        assert Workspace.kind_at(directory + key) == kind, key
        assert Workspace.secret_at(directory + key) is (key in table.get("secret", ())), key
    for key in table["outside"]:
        assert Workspace.kind_at(directory + key) is None, key
        assert Workspace.secret_at(directory + key) is False, key


@pytest.mark.parametrize("directory", sorted(_WORKSPACE_DIRECTORIES))
def test_the_workspace_splits_a_key_into_its_package_and_the_key_within_it(directory):
    model = PACKAGE_MODELS[_WORKSPACE_DIRECTORIES[directory]]
    assert Workspace.package_at(directory + model.ROOT) == (directory, model, model.ROOT)


def test_the_workspace_names_the_documents_it_holds_directly_and_nothing_outside():
    for key, kind in PACKAGES["workspace"]["located"].items():
        if not key.endswith("/"):
            assert Workspace.kind_at(key) == kind, key
            assert Workspace.package_at(key) is None, key
    for key in ("pipelines/manifest.json\n", "pipelines/orders", "connectors/postgres",
                "vendor/pipelines/manifest.json", "vendor/connections/pg/connection.json",
                "manifest.json", "README.md"):
        assert Workspace.kind_at(key) is None, key
        assert Workspace.package_at(key) is None, key
