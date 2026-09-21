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
from pathlib import Path, PurePosixPath

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


ROOTS = {
    "connector-package": "definition/connector.json",
    "connection-package": "connection.json",
    "pipeline-package": "pipeline.json",
}


def _model(resource: str):
    from analitiq.contracts.validation_requests import PACKAGE_MODELS
    return PACKAGE_MODELS[resource]


def test_the_package_models_are_the_registered_package_schemas():
    from analitiq.contracts.validation_requests import PACKAGE_MODELS
    assert set(PACKAGE_MODELS) == set(PACKAGES)
    for name, model in PACKAGE_MODELS.items():
        assert render_schemas.get_resource(name).adapter._type is model  # skipcq: PYL-W0212


@package
def test_the_root_document_is_required(resource):
    assert _rendered(resource)["required"] == [ROOTS[resource]]


@package
def test_the_model_names_the_kind_at_each_location_and_none_outside(resource):
    model = _model(resource)
    for key, kind in PACKAGES[resource]["located"].items():
        assert model.kind_at(key) == kind, key
    for key in PACKAGES[resource]["outside"]:
        assert model.kind_at(key) is None, key


@package
def test_the_model_admits_a_package_holding_its_root(resource):
    model = _model(resource)
    model.model_validate(dict.fromkeys(PACKAGES[resource]["located"], {}))


@package
def test_the_model_refuses_a_package_without_its_root(resource):
    from pydantic import ValidationError
    located = dict.fromkeys(PACKAGES[resource]["located"], {})
    del located[ROOTS[resource]]
    with pytest.raises(ValidationError, match=re.escape(repr(ROOTS[resource]))):
        _model(resource).model_validate(located)


@package
def test_the_model_refuses_a_key_outside_the_table(resource):
    from pydantic import ValidationError
    key = PACKAGES[resource]["outside"][0]
    with pytest.raises(ValidationError, match=re.escape(repr(key))):
        _model(resource).model_validate({ROOTS[resource]: {}, key: {}})


@package
def test_the_location_directories_are_the_ancestors_of_the_located_keys(resource):
    ancestors = {parent.as_posix() for key in PACKAGES[resource]["located"]
                 for parent in PurePosixPath(key).parents if parent != PurePosixPath(".")}
    assert _model(resource).location_directories() == ancestors

