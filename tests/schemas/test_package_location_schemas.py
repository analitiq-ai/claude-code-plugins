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

# Per package: a sample key at each location and the schema it is written against,
# and keys that sit outside every location. The outside keys are chosen to tell an
# anchored pattern from an unanchored one (a prefixed key, trailing newlines).
PACKAGES = {
    "connector-package": {
        "located": {
            "definition/connector.json": "connector",
            "definition/type-map.json": "type-map",
            "definition/endpoints/customers.json": "api-endpoint",
        },
        "outside": (
            "definition/type-map-read.json",
            "definition/type-map-write.json",
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
        },
        "outside": (
            "definition/connection.json",
            "definition/type-map-read.json",
            "definition/type-map-write.json",
            "definition/endpoints/a/b.json",
            "definition/endpoints/.gitkeep",
            "vendor/connection.json",
            "vendor/definition/type-map.json",
            "connection.json\n",
            "definition/type-map.json\n",
            "definition/endpoints/customers.json\n",
            "definition/connector.json",
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
def test_the_schema_holds_locations_and_references_only(resource):
    rendered = _rendered(resource)
    assert all(set(node) == {"$ref"} for node in rendered["patternProperties"].values())
    assert "$defs" not in rendered
    assert "properties" not in rendered


@package
def test_no_location_beyond_the_table_is_admitted(resource):
    assert _rendered(resource)["additionalProperties"] is False


@package
def test_every_reference_names_a_registered_schema(resource):
    published = {schema_url_for(name) for name in render_schemas.RESOURCES_BY_NAME}
    assert set(_table(resource).values()) <= published
