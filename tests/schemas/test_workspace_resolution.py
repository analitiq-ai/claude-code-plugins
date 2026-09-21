"""A workspace key resolves to its package and to the published schema of the
document at it, read from the committed workspace and package schemas alone.

The resolver holds no path of its own: every location it knows comes from a
committed table, so a key the tables cannot place resolves to nothing.
"""
from __future__ import annotations

import json
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
from analitiq.contracts.pipeline_manifest import PipelineManifestEntry  # noqa: E402
from analitiq.contracts.shared.common import schema_url_for  # noqa: E402

CONNECTION_ID = "0b1f0c9e-2c7a-4d1e-9b6a-3f5e8d2c1a47"
ENTRY_PATH = "orders/pipeline.json"

_RESOURCE_OF = {schema_url_for(name): name for name in render_schemas.RESOURCES_BY_NAME}


def _rendered(resource: str) -> dict:
    return json.loads((REPO_ROOT / "schemas" / resource / "latest.json").read_text())


def _locate(resource: str, key: str) -> list[tuple[str, str]]:
    table = {p: node["$ref"] for p, node in _rendered(resource)["patternProperties"].items()}
    return [(key, _RESOURCE_OF[ref]) for p, ref in table.items() if re.search(p, key)]


def resolve(key: str) -> tuple[str, str | None, str | None] | None:
    """(package root, package schema, kind of the document at `key`), or None."""
    candidates = [key[: i + 1] for i, c in enumerate(key) if c == "/"] + [key]
    hits = [(c, r) for c in candidates for (_, r) in _locate("workspace", c)]
    if not hits:
        return None
    assert len(hits) == 1, hits  # two roots for one key is a schema defect
    root, resource = hits[0]
    if root == key:  # a document the workspace holds directly
        return (root, None, resource)
    inner = _locate(resource, key[len(root):])
    assert len(inner) <= 1, inner
    return (root, resource, inner[0][1] if inner else None)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("pipelines/manifest.json",
         ("pipelines/manifest.json", None, "pipeline-manifest")),
        ("pipelines/orders/streams/a.json",
         ("pipelines/orders/", "pipeline-package", "stream")),
        (f"connections/{CONNECTION_ID}/definition/endpoints/x.json",
         (f"connections/{CONNECTION_ID}/", "connection-package", "database-endpoint")),
        ("connectors/postgres/definition/connector.json",
         ("connectors/postgres/", "connector-package", "connector")),
        ("connectors/postgres/README.md",
         ("connectors/postgres/", "connector-package", None)),
        ("pipelines/manifest.json/pipeline.json",
         ("pipelines/manifest.json/", "pipeline-package", "pipeline")),
        ("vendor/x.json", None),
    ],
)
def test_a_workspace_key_resolves_to_its_package_and_kind(key, expected):
    assert resolve(key) == expected


def test_a_manifest_path_resolves_to_a_pipeline_document():
    manifest_key = "pipelines/manifest.json"
    assert resolve(manifest_key) == (manifest_key, None, "pipeline-manifest")
    manifest_dir = manifest_key[: manifest_key.rindex("/") + 1]
    assert resolve(manifest_dir + ENTRY_PATH)[2] == "pipeline"

    pattern = PipelineManifestEntry.model_json_schema()["properties"]["path"]["pattern"]
    assert re.fullmatch(pattern, ENTRY_PATH)
