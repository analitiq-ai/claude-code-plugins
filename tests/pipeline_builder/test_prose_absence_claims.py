"""Pin the prose that reasons from something the contract does NOT have.

An absence claim is the one shape that never fails on its own. "A database
endpoint carries no `replication` block" is true today, decides which
`replication.method` values this plugin tells an author are selectable, and
would go silently wrong the moment the contract grew the field — every
name-level and enum-level guard would stay green, because nothing they read
changed.

Every claim below is graded the same way: the verdict comes from the model's
own property names, and the prose is only ever *located* — by a token the
contract owns, in backticks. Deciding whether the sentence around that token
still teaches the absence is `.claude/rules/plugin-prose.md`'s job, per
`.claude/rules/validator-claims.md`; a phrase match could not tell a sentence
forbidding a shape from one permitting it.

Kept in this suite rather than the connector one: the claims live in this
plugin's prose, and the root `CLAUDE.md` namespaces `tests/` per plugin, so
someone editing one of these documents and running `pytest tests/pipeline_builder`
must see it fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip(
    "analitiq.contracts.endpoints",
    reason="analitiq-contract-models not importable; run from a full checkout",
)

from pydantic import TypeAdapter  # noqa: E402
from analitiq.contracts.endpoints import (  # noqa: E402
    ApiEndpointDoc,
    DatabaseEndpointDoc,
)
from analitiq.contracts.stream import Filter  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN = REPO_ROOT / "plugins" / "analitiq-pipeline-builder"


@dataclass(frozen=True)
class AbsenceClaim:
    """One document reasoning from a name the contract does not declare."""

    #: Path under the plugin root, so a failure names the file to rewrite.
    doc: str
    #: The model whose property names settle the verdict. Chosen as the model
    #: the sentence is about — a wider one would carry the name legitimately
    #: (`StreamSource` declares `filters`; a `Filter` must not).
    model: type
    #: Names whose absence the guidance depends on. More than one where the
    #: contract could grow the capability under a name the prose never used.
    absent: tuple[str, ...]
    #: Contract-owned tokens, in backticks, the document must still carry. A
    #: document that stops naming them no longer makes the claim, and the pin
    #: above is then protecting nobody. Empty where the sentence names no
    #: contract token at all — there the contract half is all a mechanism has.
    anchors: tuple[str, ...]
    #: What the prose does with the absence, for the failure hint.
    teaches: str

    @property
    def path(self) -> Path:
        return PLUGIN / self.doc


CLAIMS: tuple[AbsenceClaim, ...] = (
    AbsenceClaim(
        doc="skills/pipeline-builder/references/enum-mappers.md",
        model=DatabaseEndpointDoc,
        # `replication` is the block itself; `supported_methods` is what the
        # claim is really about, and the contract could add one without
        # reusing the other name.
        absent=("replication", "supported_methods"),
        anchors=("`replication`",),
        teaches="the method guidance below it depends on a database endpoint "
                "declaring no support set",
    ),
    AbsenceClaim(
        doc="skills/stream-spec/spec-source.md",
        model=ApiEndpointDoc,
        absent=("primary_keys",),
        anchors=("`primary_keys`",),
        teaches="§`primary_keys` tells the author it is the ONLY source "
                "identity hint an API source has, because the endpoint "
                "document carries none to inherit",
    ),
    AbsenceClaim(
        doc="skills/stream-spec/spec-filter-operators.md",
        model=Filter,
        # Boolean grouping would arrive as operand keys on a filter, or as a
        # nested filter list; inclusivity as a flag beside the operator.
        absent=("or", "not", "filters", "inclusive"),
        anchors=("`or`", "`not`", "`inclusive`"),
        teaches="§'How filters combine' refuses to encode a disjunction, and "
                "§'Authoring notes' sends a boundary-inclusive range to `gte` "
                "/ `lte` because no flag carries inclusivity",
    ),
    AbsenceClaim(
        doc="skills/endpoint-spec/spec-new-table.md",
        model=DatabaseEndpointDoc,
        # A pending-creation marker would be a boolean on the endpoint
        # document; these are the spellings such a field would plausibly take.
        absent=("create_if_missing", "pending_creation", "if_not_exists", "auto_create"),
        # The sentence ("There is no flag for this") names no contract token,
        # so there is nothing lexical to anchor on — the contract half is the
        # whole mechanism here.
        anchors=(),
        teaches="the file opens by telling the author a new-table endpoint is "
                "an ORDINARY database-endpoint document, with nothing to set",
    ),
)


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


NAME_CASES = [
    pytest.param(claim, name, id=f"{claim.doc}::{claim.model.__name__}.{name}")
    for claim in CLAIMS
    for name in claim.absent
]

ANCHOR_CASES = [
    pytest.param(claim, anchor, id=f"{claim.doc}::{anchor}")
    for claim in CLAIMS
    for anchor in claim.anchors
]


@pytest.mark.parametrize("claim,name", NAME_CASES)
def test_the_contract_still_lacks_the_name(claim: AbsenceClaim, name: str) -> None:
    """The absence each document reasons from must still hold."""
    properties = _property_names(TypeAdapter(claim.model).json_schema())
    assert properties, (
        f"no properties found on {claim.model.__name__} — the model was "
        "restructured and this guard is reading nothing")
    assert name not in properties, (
        f"{claim.model.__name__} now carries `{name}`, so "
        f"{claim.doc} is false: {claim.teaches}. Rewrite that guidance in this "
        "change — the absence is what the advice rests on."
    )


@pytest.mark.parametrize("claim,anchor", ANCHOR_CASES)
def test_the_subject_of_the_claim_is_still_in_the_prose(
    claim: AbsenceClaim, anchor: str
) -> None:
    """A guard for a claim nobody makes is worse than no guard.

    Anchored on the contract-owned TOKEN, not on the sentence containing it.
    Pinning the sentence — "carries no `replication` block" — pins English:
    rewording it more clearly reddens the build on prose that improved, and the
    remedy the failure asks for is to reword it back. It is brittle in a second
    way too, since such a phrase routinely wraps a line in the file.

    The token is what a mechanism can decide. If a document stops naming it
    altogether, the guard above is protecting a claim the document no longer
    makes and should go with it. Whether a rewritten sentence still *teaches*
    the absence is a judgment, and it belongs to
    `.claude/rules/plugin-prose.md`.
    """
    assert claim.path.is_file(), f"{claim.doc} does not exist — repoint the claim"
    assert anchor in claim.path.read_text(encoding="utf-8"), (
        f"{claim.doc} no longer names {anchor}, so the absence this module "
        f"guards is not something the document reasons from any more ({claim.teaches}). "
        "Delete the claim, or re-anchor it on what replaced the guidance."
    )


def test_every_claim_names_a_real_document() -> None:
    """Non-vacuity: a claim pointing at a moved file would grade nothing."""
    missing = [claim.doc for claim in CLAIMS if not claim.path.is_file()]
    assert not missing, f"absence claims naming no such document: {missing}"
