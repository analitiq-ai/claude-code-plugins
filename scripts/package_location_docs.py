#!/usr/bin/env python3
"""How a reader finds a document's location in a package or workspace schema.

Both plugins place and find documents by reading the published package and
workspace schemas, so both carry the same paragraph on how to read one. It is
rendered here once and registered with each plugin's generator —
`gen_pipeline_docs.py` for the pipeline tree, `render_validator_claims.py` for
the connector tree — so every occurrence, in either plugin's tree, is one block
id rendered by one function.
The secret-location marker is read off the rendered package schemas, which own
it.
"""
from __future__ import annotations


def _secret_marker() -> str:
    from analitiq.contracts.workspace import PACKAGE_MODELS
    from render_schemas import rendered_latest

    markers = {
        key
        for name in PACKAGE_MODELS
        for location in rendered_latest(name)["patternProperties"].values()
        for key in location
        if key != "$ref"
    }
    if len(markers) != 1:
        raise RuntimeError(
            f"expected one secret-location marker across the package schemas, found {sorted(markers)}")
    return markers.pop()


def render_package_location_lookup() -> str:
    return (
        "In a package or workspace schema, each `patternProperties` key is a location: "
        "a pattern matched against a path from that package's own directory, or from "
        "the workspace root. Its `$ref` is the schema of the document or package that "
        "sits there. A package's root document is its schema's `required` entry, and a "
        f"location marked `{_secret_marker()}` holds secret values.\n"
    )


RENDERERS = {"package-location-lookup": render_package_location_lookup}
