#!/usr/bin/env python3
"""The connector release table — single structured source of the bump policy.

The connector plugin's release policy surfaces in three places, each read by a
different consumer: the release table (`metadata-and-versioning.md` §Release
version, read by authoring skills), the drift classifier's bump table
(`agents/connector-drift-classifier.md`, the mapping the classifier applies at
run time), and the `DriftVerdict` envelope (`references/io-contracts.md`),
whose `bump` and `category` enums are the verdict's output vocabulary. All
three are projections of one vocabulary — which change categories exist, which
semver tier each triggers, and what each means — and hand-maintaining three
copies opens three ways for them to disagree, which is the surface
`.claude/rules/no-drift-surfaces.md` exists to keep closed.

This module is the one owner. The policy is the plugin's own — no contract
model or engine artifact defines it, and `metadata-and-versioning.md`'s intro
states it is the plugin's own policy — which is why the source is data here
rather than a pinned package import. `scripts/render_validator_claims.py`
registers the renderers below and writes each projection into its
`BEGIN GENERATED` block; why they live in that script's registry is
documented on its `_release_table` hook.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Callable

#: Rollup precedence, highest first: any major-tier change makes the bump
#: `major`, else any minor-tier makes it `minor`, else patch. The rollup is
#: rendered into the bump-table block by `render_bump_table`; a verdict with
#: no change at all is `none`.
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
    Category(
        "endpoint-removed",
        "major",
        "Endpoint removed",
        note=(
            "an `endpoint_id` the previous release shipped is absent from this "
            "one. Streams pin endpoints by id, so the pin resolves to nothing "
            "and the stream stops reading — which is why a resource whose "
            "locator moves ships as a new document plus this removal rather "
            "than a rename (`RULE-ENDP-043`), and why the removal half is what "
            "sets the bump"
        ),
    ),
    Category(
        "write-mode-removed",
        "major",
        "Write mode removed from a kept endpoint",
        note=(
            "a mode key under `operations.write` the previous release shipped "
            "is absent from an endpoint this one still ships — dropping the "
            "whole `operations.write` block withdraws every mode it declared "
            "at once. A stream's API destination selects the mode by that key, "
            "so the selection resolves to nothing and the destination stops "
            "writing; the endpoint surviving is what separates this from "
            "`endpoint-removed`"
        ),
    ),
    Category(
        "record-field-removed",
        "major",
        "Record field removed",
        note=(
            "a field the previous release declared in a read operation's "
            "record shape — the `items` of the array node `response.records` "
            "resolves to (`RULE-ENDP-012`) — is no longer declared there. A "
            "stream names those fields: its incremental `cursor_field`, and "
            "every mapping assignment that reads one by path"
        ),
    ),
    Category(
        "record-field-type-changed",
        "major",
        "Record field type changed",
        note=(
            "a field both releases declare in the record shape froze a "
            "different `native_type` / `arrow_type` pair. Direction does not "
            "soften it: widening and narrowing alike re-type the column a "
            "destination already created from that `arrow_type`, and a JSON "
            "`type` that held still while the pair moved is the case a shape "
            "diff misses"
        ),
    ),
    Category(
        "filter-operators-narrowed",
        "major",
        "Filter operators narrowed",
        note=(
            "an operator a param offered under `operators` — the "
            "stream-filterability contract (`RULE-ENDP-055`) — is no longer "
            "offered, whether the member left the list, the `operators` key "
            "was dropped, or the param carrying it is gone. A stream filters "
            "on the members the endpoint offered, so its filter stops being "
            "expressible"
        ),
    ),
    Category(
        "conflict-keys-changed",
        "major",
        "Conflict keys changed on a kept write mode",
        note=(
            "the `conflict_keys` an upsert mode both releases ship matches on "
            "are not the same set. The key is endpoint-owned — a stream "
            "declares none — so a change re-keys every existing stream's "
            "upsert silently: rows that matched an existing row now insert, "
            "and rows that did not now overwrite one"
        ),
    ),
    Category(
        "endpoint-capability-narrowed",
        "major",
        "Kept endpoint withdrew something a stream binds",
        note=(
            "an endpoint both releases ship no longer offers something an "
            "existing stream depends on — whether the stream names it or "
            "reads it through the endpoint's own behaviour — and no category "
            "above says which. The "
            "endpoint's interior is wider than the categories that enumerate "
            "it — a read operation dropped from a write-bearing endpoint, a "
            "replication method or a cursor mapping withdrawn, a `pagination` "
            "block removed so a stream silently reads one page, a filterable "
            "param whose request-value contract tightened anywhere — a bound, "
            "a pattern, a length, not only its type — an idempotency block "
            "removed, a write input field removed or retyped, a nested record "
            "field changed under an unchanged parent. Reach for this when the "
            "diff withdraws something and nothing more specific fits, and say "
            "in the `note` what was withdrawn. A release is never patch "
            "because the vocabulary had no word for what it took away"
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
    Category(
        "write-mode-added",
        "minor",
        "Write mode added to a kept endpoint",
        note=(
            "a mode key under `operations.write` that endpoint did not declare "
            "before; a whole new endpoint document is `optional-endpoint-added`"
        ),
    ),
    Category(
        "record-field-added",
        "minor",
        "Record field added",
        note=(
            "a field the record shape did not declare before; the discovery "
            "outputs `optional-output-added` names are a connector-level "
            "block, not this. Minor because nothing an existing stream binds "
            "stops resolving; a stream that maps its source without naming "
            "fields carries the new one too, so name the added fields in the "
            "`note`"
        ),
    ),
    Category(
        "filter-operators-widened",
        "minor",
        "Filter operators widened",
        note=(
            "a param offers an operator it did not offer before, including a "
            "param newly declared with `operators`. These are endpoint params, "
            "not the connection inputs `optional-input-added` names"
        ),
    ),
    Category(
        "endpoint-obligation-added",
        "major",
        "Kept endpoint now demands something of an existing document",
        note=(
            "an addition an existing stream must satisfy rather than one it "
            "may opt into: a read param declared `required` with no default, "
            "so a stream supplying no value for it stops resolving, or a "
            "member added to a write mode's required input, so a stream whose "
            "mapping does not produce it sends a record the provider refuses. "
            "The additive categories are for what a stream MAY now use; an "
            "addition it MUST now satisfy is drift wearing the other sign"
        ),
    ),
    Category(
        "endpoint-capability-added",
        "minor",
        "Kept endpoint offers something more a stream can bind",
        note=(
            "the additive counterpart, and the same fallback: an endpoint both "
            "releases ship now offers something a stream document can name "
            "that no category above covers"
        ),
    ),
    Category("type-map-rule-added", "minor", "Type-map rule added"),
    Category("bug-fix", "patch", "Bug fixes"),
    Category("doc-fix", "patch", "Doc fixes"),
    Category("tuning", "patch", "Transport implementation tuning"),
    Category(
        "capability-block-added",
        "patch",
        "Top-level capability block introduced where the connector carried "
        "none (`sql_capabilities`, `error_map`)",
        note=(
            "a top-level capability block the connector did not carry before "
            "(`sql_capabilities`, `error_map`) appears for the first time — "
            "neither an input, an output nor an endpoint. Introducing one is "
            "strictly enabling, so no saved connection drifts; narrowing or "
            "removing it afterwards is `sql-capabilities-changed`"
        ),
    ),
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


#: A broken token stops being greppable — no fill in this module may split
#: one, neither at a hyphen nor mid-word in an overlong code span.
_NO_TOKEN_SPLIT = {"break_on_hyphens": False, "break_long_words": False}


def _decapitalize(phrase: str) -> str:
    """Lower a leading ASCII capital so joined phrases read as one sentence.

    A phrase opening with an acronym (`API endpoint …`) is left alone.
    """
    if phrase[:1].isascii() and phrase[:1].isupper() and not phrase[1:2].isupper():
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
    """The classifier's category → tier mapping and rollup, notes in place."""
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
                **_NO_TOKEN_SPLIT,
            )
        )
    first, *rest = TIERS
    rollup = "; ".join(
        [f"Rollup: any {first}-tier category → bump = `{first}`"]
        + [f"else any {tier}-tier → `{tier}`" for tier in rest]
        + [f"else → `{BUMP_VALUES[-1]}`"]
    )
    return "\n".join(bullets) + "\n\n" + textwrap.fill(
        rollup + ".", width=76, **_NO_TOKEN_SPLIT) + "\n"


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
        **_NO_TOKEN_SPLIT,
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
    # The markdown projections get no parse-back like the envelope's, so bad
    # cell content must be refused at the data layer or it ships silently.
    for c in CATEGORIES:
        if not c.slug.strip() or not c.meaning.strip():
            raise ValueError(f"category {c.slug!r}: empty slug or meaning")
        if "|" in c.meaning or "\n" in c.meaning:
            raise ValueError(
                f"category {c.slug!r}: meaning would break the markdown table")
    if any("|" in m or "\n" in m for m in TIER_MEANINGS.values()):
        raise ValueError("a tier meaning would break the markdown table")
    envelope = _rendered_envelope()  # raises on malformed JSON in the template
    rationale = envelope["properties"]["rationale"]["items"]["properties"]
    if rationale["category"]["enum"] != slugs:
        raise ValueError("rendered category enum diverged from CATEGORIES")
    if envelope["properties"]["bump"]["enum"] != list(BUMP_VALUES):
        raise ValueError("rendered bump enum diverged from BUMP_VALUES")


_validate()
