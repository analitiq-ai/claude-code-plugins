"""Pin the prose that reasons from something the contract does NOT have.

An absence claim is the one shape that never fails on its own. "A database
endpoint carries no `replication` block" is true today, decides which
`replication.method` values this plugin tells an author are selectable, and
would go silently wrong the moment the contract grew the field — every
name-level and enum-level guard would stay green, because nothing they read
changed.

Kept in this suite rather than the connector one: the claim lives in this
plugin's prose, and the root `CLAUDE.md` namespaces `tests/` per plugin, so
someone editing `enum-mappers.md` and running `pytest tests/pipeline_builder`
must see it fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "analitiq.contracts.endpoints",
    reason="analitiq-contract-models not importable; run from a full checkout",
)

from pydantic import TypeAdapter  # noqa: E402
from analitiq.contracts.endpoints import DatabaseEndpointDoc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ENUM_MAPPERS = (
    REPO_ROOT
    / "plugins"
    / "analitiq-pipeline-builder"
    / "skills"
    / "pipeline-builder"
    / "references"
    / "enum-mappers.md"
)

#: Names that would mean a database endpoint had gained a declared support set.
#: `replication` is the block itself; `supported_methods` is what the claim is
#: really about, and the contract could add one without reusing the other name.
FORBIDDEN_ON_DATABASE_ENDPOINTS = ("replication", "supported_methods")


def _property_names(schema: dict) -> set[str]:
    """Every `properties` key anywhere in a JSON Schema.

    Structural, not a substring scan over the serialized document: descriptions
    routinely mention neighbouring concepts ("unlike replication-capable API
    endpoints…"), and a scan would fail on the prose rather than on the shape.
    """
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                found.update(props)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return found


@pytest.mark.parametrize("name", FORBIDDEN_ON_DATABASE_ENDPOINTS)
def test_database_endpoints_declare_no_support_set(name: str) -> None:
    """The absence `enum-mappers.md` reasons from must still hold."""
    properties = _property_names(TypeAdapter(DatabaseEndpointDoc).json_schema())
    assert properties, "no properties found — the document model was restructured"
    assert name not in properties, (
        f"the database-endpoint contract now carries `{name}`, so "
        f"{ENUM_MAPPERS.relative_to(REPO_ROOT)}'s claim that a database "
        "endpoint declares no support set is false. Rewrite that guidance in "
        "this change — the method guidance below it depends on the absence."
    )


def test_the_subject_of_the_claim_is_still_in_the_prose() -> None:
    """A guard for a claim nobody makes is worse than no guard.

    Anchored on the contract-owned TOKEN, not on the sentence containing it.
    Pinning the sentence — "carries no `replication` block" — pins English:
    rewording it more clearly reddens the build on prose that improved, and the
    remedy the failure asks for is to reword it back. It was brittle in a second
    way too, since the phrase wraps a line in the file and only matched at all
    because this test collapses whitespace first.

    The token is what a mechanism can decide. If `enum-mappers.md` stops naming
    `replication` altogether, the guard above is protecting a claim the document
    no longer makes and should go with it. Whether a rewritten sentence still
    *teaches* the absence is a judgment, and it belongs to `plugin-prose.md`.
    """
    assert "`replication`" in ENUM_MAPPERS.read_text(encoding="utf-8"), (
        f"{ENUM_MAPPERS.relative_to(REPO_ROOT)} no longer names `replication`, "
        "so the absence this module guards is not something the document "
        "reasons from any more. Delete both, or re-anchor them on what replaced "
        "the guidance."
    )
