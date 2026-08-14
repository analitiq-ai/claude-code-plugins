"""Tests for the contract-doc generator (scripts/gen_pipeline_docs.py).

The prose under the plugin root is the only prose Analitiq still keeps, so the facts it
states about the contract are generated from the pinned published package rather
than typed by hand. The load-bearing test here is `test_generated_blocks_in_sync`:
it is the drift gate. If a pin bump changes an enum, a regex, a bound, or an
advisory rule, that test fails until the docs are regenerated.

Like the adapter tests, this module skips cleanly when the published packages are
absent so a bare `pytest` never fails confusingly.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from _rule_files import rule_reference_root

ROOT = Path(__file__).resolve().parents[2] / "plugins" / "analitiq-pipeline-builder"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gen_pipeline_docs as G  # noqa: E402

pytest.importorskip("analitiq.validator",
                    reason="requires: pip install -r requirements-dev.txt")


def test_generated_blocks_in_sync():
    """Every generated block in every doc matches what the pinned package emits.

    This is the drift gate. On failure, run:
        python3 scripts/gen_pipeline_docs.py
    """
    stale = [
        p.relative_to(ROOT).as_posix()
        for p in G.generated_docs()
        if p.read_text() != G.render_text(p.read_text(), str(p))
    ]
    assert not stale, (
        "generated blocks are out of sync with the published contract in: "
        f"{', '.join(stale)}. Run: python3 scripts/gen_pipeline_docs.py"
    )


def _referenced_block_ids():
    return {
        m.group("id")
        for p in G.generated_docs()
        for m in G._BLOCK_RE.finditer(p.read_text())
    }


def test_every_doc_block_has_a_renderer():
    """A block id with no renderer must fail loud, not be silently left alone."""
    referenced = _referenced_block_ids()
    assert referenced, "expected at least one generated block across the docs"
    assert referenced <= set(G.RENDERERS), (
        f"docs reference block ids with no renderer: "
        f"{sorted(referenced - set(G.RENDERERS))}"
    )


def test_every_renderer_is_referenced_by_a_doc():
    """The inverse: a renderer no doc consumes is dead code.

    It also reads as covered, because test_renderer_emits_nonempty_block
    parametrizes over RENDERERS and so reports a passing test for a block that
    reaches no agent. Either wire it into a doc or delete it.
    """
    unreferenced = sorted(set(G.RENDERERS) - _referenced_block_ids())
    assert not unreferenced, f"renderers referenced by no doc: {unreferenced}"


def test_no_malformed_markers():
    """Every BEGIN/END marker must actually parse as a block.

    A typo'd id, a missing newline after the opening marker, or an unclosed pair
    makes the region invisible to the regex — the generator would skip it and the
    in-sync test would pass while the doc silently kept stale hand-typed content.
    """
    # Count on a deliberately loose detector: any HTML comment mentioning
    # GENERATED. Counting the exact `<!-- BEGIN GENERATED` prefix would make the
    # assertion vacuous when BOTH markers are mangled inside the prefix itself
    # (`<!-- BEGIN  GENERATED: x -->`), which is the realistic
    # copy-paste-a-broken-template case: all three counts would be 0 and the doc
    # would keep stale hand-typed content forever.
    # `.*?` may span across an unrelated comment, merging two matches into one.
    # That is count-neutral for this assertion — a comment without GENERATED
    # contributes nothing either way — so the arithmetic still holds; the looseness
    # is what catches a marker mangled inside its own prefix.
    loose = re.compile(r"<!--.*?GENERATED.*?-->", re.IGNORECASE | re.DOTALL)
    # The per-scope rule files are generated whole by render_rule_reference.py
    # — their header comment names that script, not a BEGIN/END pair — and
    # test_rule_reference_sync.py fails on any byte of drift in them, so this
    # marker arithmetic does not apply there.
    rule_files_root = rule_reference_root()
    for path in sorted(G.DOCS_ROOT.rglob("*.md")):
        if path.is_relative_to(rule_files_root):
            continue
        text = path.read_text()
        markers = len(loose.findall(text))
        parsed = len(G._BLOCK_RE.findall(text))
        assert markers == 2 * parsed, (
            f"{path.relative_to(ROOT)}: {markers} GENERATED marker(s) but "
            f"{parsed} parsed block(s) — a marker is malformed and is being skipped"
        )


def test_every_schema_url_in_prose_is_published():
    """No doc may name a schema URL the package does not publish.

    Some `$schema` mentions live in imperative prose ("Declare `$schema`: …")
    where a generated block does not fit. They are still a drift surface, so this
    pins them: every schemas.analitiq.ai URL appearing anywhere under the plugin root must be
    one the pinned package actually emits.
    """
    from analitiq.contracts.shared.common import schema_url_for

    published = {
        schema_url_for(resource)
        for resource in ("pipeline", "stream", "connection", "database-endpoint",
                         "api-endpoint", "connector", "credentials",
                         "type-map-read", "type-map-write")
    }
    url_re = re.compile(r"https://schemas\.analitiq\.ai/[A-Za-z0-9._/-]+")
    offenders = {}
    for path in sorted(G.DOCS_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in {".md", ".json", ".py"}:
            continue
        for url in url_re.findall(path.read_text()):
            if url.rstrip(".,;:)") not in published:
                offenders.setdefault(path.relative_to(ROOT).as_posix(), set()).add(url)
    assert not offenders, f"unpublished schema URLs referenced in prose: {offenders}"


def test_unknown_block_id_raises():
    text = ("<!-- BEGIN GENERATED: no-such-block -->\n"
            "stale\n"
            "<!-- END GENERATED: no-such-block -->")
    with pytest.raises(G.UnknownBlock):
        G.render_text(text, "<test>")


def test_render_is_idempotent():
    """Rendering twice equals rendering once — no block grows or accumulates."""
    for path in G.generated_docs():
        once = G.render_text(path.read_text(), str(path))
        assert G.render_text(once, str(path)) == once, f"{path} render is not idempotent"


def test_content_outside_markers_is_untouched():
    text = ("Hand-written intro.\n\n"
            "<!-- BEGIN GENERATED: validator-ids -->\n"
            "obsolete body\n"
            "<!-- END GENERATED: validator-ids -->\n\n"
            "Hand-written outro.\n")
    rendered = G.render_text(text, "<test>")
    assert rendered.startswith("Hand-written intro.\n\n")
    assert rendered.endswith("\nHand-written outro.\n")
    assert "obsolete body" not in rendered


@pytest.mark.parametrize("block_id", sorted(G.RENDERERS))
def test_renderer_emits_nonempty_block(block_id):
    """Each renderer produces content ending in exactly one newline.

    Guards the failure mode where a renamed package symbol makes a renderer
    silently emit nothing, quietly deleting a rule from the agent's instructions.
    """
    body = G.RENDERERS[block_id]()
    assert body.strip(), f"{block_id} rendered empty"
    assert body.endswith("\n") and not body.endswith("\n\n"), (
        f"{block_id} must end with exactly one newline")


def test_filter_operator_scopes_are_disjoint_and_complete():
    """The empirically probed operator vocabulary matches the published Literal."""
    from typing import get_args

    from analitiq.contracts.stream import FilterOperator

    accepted = G._accepted_operators_by_scope()
    connection, connector = set(accepted["connection"]), set(accepted["connector"])
    assert connection | connector == set(get_args(FilterOperator)), (
        "probe did not reproduce the full published operator vocabulary")
    # Pin the split itself, not merely that one exists. `!=` would still pass if a
    # pin bump swapped the two vocabularies, which would generate a table that
    # actively misleads the agent rather than merely omitting something.
    assert {"like", "ilike", "is_null", "is_not_null"} <= connection - connector, (
        "database-only operators are no longer connection-scope-only")
    assert {"contains", "starts_with", "ends_with"} <= connector - connection, (
        "API-only operators are no longer connector-scope-only")
    assert {"eq", "neq", "in", "not_in"} <= connection & connector, (
        "common operators are no longer accepted in both scopes")
