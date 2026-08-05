#!/usr/bin/env python3
"""The connector release table — single structured source of the bump policy.

The connector plugin's release policy surfaces in three places, each read by a
different consumer: the release table (`metadata-and-versioning.md` §Release
version, read by authoring skills), the drift classifier's bump table
(`agents/connector-drift-classifier.md`, the mapping the classifier applies at
run time), and the `DriftVerdict` envelope's `category` enum
(`references/io-contracts.md`, the verdict's output vocabulary). All three are
projections of one vocabulary — which change categories exist, which semver
tier each triggers, and what each means — and hand-maintaining three copies is
the drift surface `.claude/rules/no-drift-surfaces.md` forbids.

This module is the one owner. The policy is the plugin's own — no contract
model or engine artifact defines it (`metadata-and-versioning.md` says so) —
which is why the source is data here rather than a pinned package import.
`scripts/render_validator_claims.py` registers the renderers below and writes
each projection into its `BEGIN GENERATED` block: that script owns every
generated block in the connector plugin's tree, and a marker id it does not
know fails loud rather than rendering nothing.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Callable

#: Rollup precedence, highest first: any major-tier change makes the bump
#: `major`, else any minor-tier makes it `minor`, else patch. The classifier's
#: process prose applies this order; a verdict with no change at all is `none`.
TIERS: tuple[str, ...] = ("major", "minor", "patch")

#: What each tier means for a saved connection — the release table's
#: Meaning column.
TIER_MEANINGS: dict[str, str] = {
    "major": "Possible connection drift.",
    "minor": "Additive, non-drifting.",
    "patch": "No connection drift.",
}

#: The `DriftVerdict.bump` vocabulary: a tier, or `none` when nothing changed.
BUMP_VALUES: tuple[str, ...] = (*reversed(TIERS), "none")


@dataclass(frozen=True)
class Category:
    """One change category: a `DriftVerdict.category` value and its tier."""

    slug: str  # the enum value, and the bump table's identifier
    tier: str  # which semver tier this category triggers
    meaning: str  # the release table's human phrase for the change
    note: str = ""  # classifier craft: how to judge the borderline cases


CATEGORIES: tuple[Category, ...] = (
    Category("input-removed", "major", "Input removed"),
    Category("input-renamed", "major", "Input renamed"),
    Category("input-type-changed", "major", "Input type changed"),
    Category("input-enum-narrowed", "major", "Input enum narrowed"),
    Category("storage-changed", "major", "Storage moved"),
    Category("non-optional-input-added", "major", "Non-optional input added"),
    Category("auth-shape-changed", "major", "Auth-shape change"),
    Category("discovery-shape-changed", "major", "Discovery-shape change"),
    Category(
        "sql-capabilities-changed",
        "major",
        "`sql_capabilities` shape fact narrowed, removed, or replaced with "
        "one an existing connection may not satisfy (any `stage.scope` or "
        "`stage.schema` change)",
        note=(
            "a declared `sql_capabilities` shape fact **narrowed, removed, or "
            "replaced with one an existing connection may not satisfy** — the "
            "engine reads these at handshake. Narrowing: `merge_form` → "
            "`none`, `catalog` → `none`, or dropping the block. Replacement: "
            "any change to `stage.scope` or `stage.schema`, which is neither "
            "a narrowing nor a widening but can break every saved connection "
            "whose credentials cannot create a persistent stage table, or "
            "lack rights on a newly-named `dedicated_schema`. Widening alone "
            "is not drift: adding a `bulk_load` mechanism or gaining a "
            "`merge_form` is strictly enabling and classifies as `tuning`"
        ),
    ),
    Category("type-map-rule-removed", "major", "Type-map rule removed"),
    Category(
        "type-map-canonical-changed",
        "major",
        "Render side changed for an existing matcher (read map: `canonical` "
        "changed for an existing `native`; write map: `native` changed for "
        "an existing `canonical`)",
        note=(
            "an existing matcher now resolves to a different render — read "
            "map: an existing `native` resolves to a different canonical; "
            "write map: an existing `canonical` renders a different native "
            "DDL — either invalidates downstream consumers"
        ),
    ),
    Category("optional-input-added", "minor", "Optional input added"),
    Category("optional-output-added", "minor", "Optional discovery output added"),
    Category("optional-endpoint-added", "minor", "Optional endpoint added"),
    Category("type-map-rule-added", "minor", "Type-map rule added"),
    Category("bug-fix", "patch", "Bug fixes"),
    Category("doc-fix", "patch", "Doc fixes"),
    Category("tuning", "patch", "Transport implementation tuning"),
    Category(
        "type-map-rule-reordered",
        "patch",
        "Type-map rule reordered (when the reorder does not change "
        "first-match resolution for any existing input)",
        note=(
            "when the reorder doesn't change first-match resolution for any "
            "existing input in that map's direction"
        ),
    ),
)


def _tier_categories(tier: str) -> tuple[Category, ...]:
    return tuple(c for c in CATEGORIES if c.tier == tier)


def _decapitalize(phrase: str) -> str:
    """Lower a leading ASCII capital so joined phrases read as one sentence."""
    if phrase[:1].isascii() and phrase[:1].isupper():
        return phrase[0].lower() + phrase[1:]
    return phrase


def render_release_table() -> str:
    """The release table: one row per tier, examples joined from the categories."""
    rows = ["| Bump | Meaning | Examples |", "|---|---|---|"]
    for tier in reversed(TIERS):  # the table reads patch → major
        phrases = [c.meaning for c in _tier_categories(tier)]
        examples = ", ".join(
            [phrases[0], *(_decapitalize(p) for p in phrases[1:])]
        )
        rows.append(f"| {tier.capitalize()} | {TIER_MEANINGS[tier]} | {examples}. |")
    return "\n".join(rows) + "\n"


def render_bump_table() -> str:
    """The classifier's category → tier mapping, notes attached in place."""
    bullets = []
    for tier in TIERS:
        items = [
            c.slug + (f" ({c.note})" if c.note else "")
            for c in _tier_categories(tier)
        ]
        bullets.append(
            textwrap.fill(
                f"- **{tier}**: " + ", ".join(items) + ".",
                width=76,
                subsequent_indent="  ",
                break_on_hyphens=False,  # a slug split across lines stops being greppable
            )
        )
    return "\n".join(bullets) + "\n"


# The envelope's fixed shape, kept compact by hand because the fence is loaded
# into agent context (a pretty-printed dump doubles its line count). Only the
# two enum slots vary with the data; `_validate` parses the rendered result, so
# a template edit that breaks the JSON — or an enum that stops matching the
# data — fails at import, not in a user's session.
_DRIFT_VERDICT_TEMPLATE = """\
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["bump", "previous_version", "next_version", "rationale"],
  "properties": {
    "bump": { "type": "string", "enum": [@BUMP_ENUM@] },
    "previous_version": { "type": "string" },
    "next_version": { "type": "string" },
    "rationale": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["change_path", "category"],
        "properties": {
          "change_path": { "type": "string" },
          "category": {
            "type": "string",
            "enum": [
@CATEGORY_ENUM@
            ]
          },
          "note": { "type": "string" }
        }
      }
    }
  }
}"""


def render_drift_verdict() -> str:
    """The `DriftVerdict` envelope with both enums drawn from the data above.

    Plugin-internal I/O, outside the published contract's validation surface —
    hence the `illustrative` annotation the fence convention requires.
    """
    category_enum = textwrap.fill(
        ", ".join(json.dumps(c.slug) for c in CATEGORIES),
        width=76,
        initial_indent=" " * 14,
        subsequent_indent=" " * 14,
        break_on_hyphens=False,
    )
    body = _DRIFT_VERDICT_TEMPLATE.replace(
        "@BUMP_ENUM@", ", ".join(json.dumps(v) for v in BUMP_VALUES)
    ).replace("@CATEGORY_ENUM@", category_enum)
    return "<!-- illustrative -->\n```json\n" + body + "\n```\n"


def _rendered_envelope() -> dict:
    fence_body = render_drift_verdict().split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    return json.loads(fence_body)


RENDERERS: dict[str, Callable[[], str]] = {
    "release-table": render_release_table,
    "bump-table": render_bump_table,
    "drift-verdict-envelope": render_drift_verdict,
}


def _validate() -> None:
    slugs = [c.slug for c in CATEGORIES]
    if len(slugs) != len(set(slugs)):
        raise ValueError(f"duplicate category slugs: {sorted(slugs)}")
    bad_tiers = {c.tier for c in CATEGORIES} - set(TIERS)
    if bad_tiers or set(TIER_MEANINGS) != set(TIERS):
        raise ValueError(f"tier vocabulary broken: {bad_tiers or TIER_MEANINGS}")
    for tier in TIERS:
        if not _tier_categories(tier):
            raise ValueError(f"tier {tier!r} has no categories — dead table row")
    envelope = _rendered_envelope()  # raises on malformed JSON in the template
    rationale = envelope["properties"]["rationale"]["items"]["properties"]
    if rationale["category"]["enum"] != slugs:
        raise ValueError("rendered category enum diverged from CATEGORIES")
    if envelope["properties"]["bump"]["enum"] != list(BUMP_VALUES):
        raise ValueError("rendered bump enum diverged from BUMP_VALUES")


_validate()
