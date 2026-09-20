"""The connector-package schema states where each authored document sits and which
published schema it is written against — and nothing else.

It is a table of locations. A document's shape is the published schema its
location points at, so this schema holds no shape of its own.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts", "render_schemas")

import render_schemas  # noqa: E402
from analitiq.contracts.shared.common import schema_url_for  # noqa: E402

RESOURCE = "connector-package"


def _table() -> dict[str, str]:
    """Location pattern -> the published schema URL a document there is written against."""
    rendered = render_schemas.render_latest(
        render_schemas.get_resource(RESOURCE), "1.0.0")
    return {pattern: node["$ref"]
            for pattern, node in rendered["patternProperties"].items()}


def _written_against(key: str) -> set[str]:
    return {ref for pattern, ref in _table().items() if re.search(pattern, key)}


def test_each_authored_document_is_located_and_pointed_at_its_published_schema():
    assert _written_against("definition/connector.json") == {schema_url_for("connector")}
    assert _written_against("definition/type-map.json") == {schema_url_for("type-map")}
    assert _written_against("definition/endpoints/customers.json") == {
        schema_url_for("api-endpoint")}


def test_a_location_outside_the_table_is_written_against_nothing():
    for key in (
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
    ):
        assert _written_against(key) == set(), key


def test_the_schema_holds_locations_and_references_only():
    rendered = render_schemas.render_latest(
        render_schemas.get_resource(RESOURCE), "1.0.0")
    assert all(set(node) == {"$ref"} for node in rendered["patternProperties"].values())
    assert "$defs" not in rendered
    assert "properties" not in rendered


def test_every_reference_names_a_registered_schema():
    published = {schema_url_for(name) for name in render_schemas.RESOURCES_BY_NAME}
    assert set(_table().values()) <= published
