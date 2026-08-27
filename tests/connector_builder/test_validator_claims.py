"""Drift guard for validator-behavior claims in plugin prose.

Plugin prose states what the validator checks and does not check. Each such
sentence is a copy of a contract fact; `scripts/render_validator_claims.py`
pins them with executable **probes** (documents run through the in-repo
validator with asserted outcomes) and renders the dense clusters into marked
blocks. This module runs the same predicates under pytest so a contract change
that falsifies prose lands as a red build, not silently-wrong authoring
guidance.

What this module does NOT do is decide which sentences make a claim. That was
a list of hand-curated English regexes over both plugins;
`.claude/rules/guards.md` says why no such list belongs in a test, and
`.claude/rules/plugin-prose.md` owns the authoring obligation the list was
standing in for. What survives is decidable both ways: a fence
naming an id no probe defines fails, and a probe nothing references fails.

Same environment contract as the other drift guards — skipped when the
contract packages fail to import (an incomplete checkout or missing runtime
deps; the packages themselves are the in-repo source, put on `sys.path` by the
root conftest), hard-failed in CI via `DRIFT_REQUIRE_CONTRACT_MODELS=1`.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from _pins import require_contract_models

require_contract_models("analitiq.contracts", "analitiq.validator")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "render_validator_claims.py"


def _load_registry():
    """Import the registry by path — `scripts/` is not an installed package."""
    spec = importlib.util.spec_from_file_location("render_validator_claims", SCRIPT_PATH)
    assert spec and spec.loader, f"cannot load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    # dataclass processing resolves the defining module through sys.modules,
    # so a by-path exec must register itself first or @dataclass crashes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_REGISTRY = _load_registry()


@pytest.mark.parametrize("probe", _REGISTRY.PROBES, ids=lambda p: p.id)
def test_probe_holds(probe) -> None:
    """Every claim's probe must still measure what the prose asserts.

    A failure here means the contract moved under a sentence in plugin prose:
    find the claim the probe backs — grep the plugins for a `PROBE:` fence
    naming the id, or look it up in the script's block→probe couplings
    (`block_probe_ids`) — update the prose AND the probe together in
    `scripts/render_validator_claims.py`, then regenerate.
    """
    failure = _REGISTRY.run_probe(probe)
    assert failure is None, (
        f"probe {probe.id!r}: {failure and failure.reason}; findings: "
        f"{[(f.get('severity'), f.get('message', '')[:140]) for f in (failure.findings if failure else [])]}"
    )


def test_generated_blocks_in_sync() -> None:
    """Every marked block must equal what the registry renders today."""
    docs = _REGISTRY.generated_docs()
    assert docs, (
        "no connector-plugin document carries a generated claim block — the "
        "marker glob no longer finds them, so this gate runs over an empty "
        "scope; the blocks exist, so the scan is broken, not the prose."
    )
    for path in docs:
        current = path.read_text(encoding="utf-8")
        rendered = _REGISTRY.render_text(current, str(path))
        assert current == rendered, (
            f"{path.relative_to(REPO_ROOT)} is stale — run "
            "`python3 scripts/render_validator_claims.py write`"
        )


def test_no_fence_names_a_probe_that_does_not_exist() -> None:
    """A fence is a pointer; a pointer to nothing pins nothing.

    This is what remains of the old claim scan, and it is the half a mechanism
    can decide: an id either resolves in the registry or it does not. Whether a
    SENTENCE asserts validator behaviour was decided by a list of hand-curated
    English regexes; a reader decides it now, and `.claude/rules/guards.md`
    says why no such list belongs in a test.
    """
    dangling = _REGISTRY.dangling_fence_ids()
    assert not dangling, f"fences name unknown probes: {dangling}"


def test_a_fence_naming_no_probe_is_reported(monkeypatch, tmp_path: Path) -> None:
    """The positive control for the assertion above, which cannot supply one.

    On a green tree every fence resolves, so `assert not dangling` passes on an
    empty list and would pass just as well on a function that always returns
    one. `dangling_fence_ids() -> []`, the membership test inverted to
    `if False`, the traversal sliced to one document, `read_text()` stubbed to
    `""` — none of those is visible in a repo where nothing is dangling, and
    dropping the report from `check` mode is invisible for the same reason.

    So the control drives the real function over a document that IS broken, and
    asserts the exact message, because the message is the whole product: an
    author whose build just went red needs the file and the id.
    """
    # TWO documents, with every dangling fence in the second. One would leave a
    # traversal sliced to its first element passing: the glob is sorted, so
    # `agents/` precedes `skills/` and a `[:1]` never reaches the broken file.
    root = tmp_path / "plugins" / "synthetic"
    (root / "agents").mkdir(parents=True)
    (root / "skills").mkdir(parents=True)
    (root / "agents" / "creator.md").write_text(
        "# Clean\n"
        "\n"
        "<!-- PROBE: write-body-path-typo-unresolved -->\n"
        "A fence whose probe is registered.\n",
        encoding="utf-8",
    )
    (root / "skills" / "spec.md").write_text(
        "# Synthetic\n"
        "\n"
        "<!-- PROBE: no-such-probe -->\n"
        "A claim pinned to a probe that was deleted under it.\n"
        "\n"
        "<!-- PROBE: write-body-path-typo-unresolved, also-gone -->\n"
        "A two-id fence where only the second id is dangling.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_REGISTRY, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_REGISTRY, "PLUGINS_ROOT", tmp_path / "plugins")

    rel = "plugins/synthetic/skills/spec.md"
    assert _REGISTRY.dangling_fence_ids() == [
        f"{rel}: fence names unknown probe 'no-such-probe'",
        f"{rel}: fence names unknown probe 'also-gone'",
    ]
    # And `check` mode must carry it out to the author, not merely compute it.
    assert any("dangling probe fences" in problem
               for problem in _REGISTRY._check_problems(stale_docs=[]))


def test_the_fence_traversal_is_not_vacuous() -> None:
    """The bookkeeping must actually see the fences the prose carries.

    Both plugins carry dozens of probe fences; zero found means the fence
    grammar or the doc glob broke, and `dangling_fence_ids` and
    `test_every_probe_is_referenced` would both pass forever over nothing — the
    same vacuous-scope failure the advisory citation gate's found-citations
    assert exists to catch.
    """
    fenced = _REGISTRY.fence_probe_ids()
    assert len(fenced) >= 10, (
        f"only {len(fenced)} probe ids found in prose fences — the traversal "
        "no longer reaches the plugins' fences"
    )


def test_every_probe_is_referenced() -> None:
    """A probe nobody renders or fences pins nothing — wire it up or delete it."""
    referenced = _REGISTRY.rendered_block_probe_ids() | _REGISTRY.fence_probe_ids()
    unreferenced = sorted(set(_REGISTRY.PROBES_BY_ID) - referenced)
    assert not unreferenced, f"unreferenced probes: {unreferenced}"


def test_every_renderer_is_embedded_and_renders() -> None:
    """A renderer no document embeds is dead code whose probes pin nothing.

    This is what actually executes `render_scope_guarantees` and its
    `_CELL_KINDS` consistency check on every run: the sync test only renders
    blocks that exist in docs, so without this manifest a deleted marker pair
    would silence a renderer (and orphan its probes) with everything green.
    Also fails on a BEGIN marker whose pair is malformed — that region would
    look generated while nothing regenerates it.
    """
    embedded = _REGISTRY.embedded_block_ids()
    missing = sorted(set(_REGISTRY.RENDERERS) - embedded)
    assert not missing, f"renderers with no embedded block: {missing}"
    assert not _REGISTRY.malformed_marker_docs()
    for block_id, renderer in _REGISTRY.RENDERERS.items():
        body = renderer()
        assert body.endswith("\n") and body.strip(), f"{block_id}: bad render"


# Where each release-policy projection must live. `unembedded` in the script's
# check only proves a block is embedded SOMEWHERE; each block here has a
# designated reader anchored to this exact path (the bump table is part of the
# classifier's own prompt file, agents load the two references), so a block
# that migrates out of its file starves that reader while every other gate
# stays green.
RELEASE_POLICY_PLACEMENTS = {
    "bump-table":
        "plugins/analitiq-connector-builder/agents/connector-drift-classifier.md",
    "release-table":
        "plugins/analitiq-connector-builder/skills/connector-builder/references/metadata-and-versioning.md",
    "drift-verdict-envelope":
        "plugins/analitiq-connector-builder/skills/connector-builder/references/io-contracts.md",
}


def test_release_policy_blocks_sit_where_their_readers_read() -> None:
    """Each projection of the release table is embedded in its reader's file.

    Also pins the projection set both ways: a renderer added to
    `connector_release_table.py` without a placement decision here fails, and
    a placement whose renderer is gone fails. Presence-only — it does not
    detect the same block embedded in a second file, which would be
    machine-regenerated and in sync rather than a drift surface.
    """
    assert set(RELEASE_POLICY_PLACEMENTS) == set(
        _REGISTRY._release_table().RENDERERS
    ), "release-policy projections changed — decide each one's reader here"
    for block_id, rel_path in RELEASE_POLICY_PLACEMENTS.items():
        text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert f"<!-- BEGIN GENERATED: {block_id} -->" in text, (
            f"{rel_path} no longer embeds generated block {block_id!r} — its "
            "reader now sees a hand copy or nothing"
        )


def test_release_policy_data_guards_fire(monkeypatch) -> None:
    """`_validate`'s divergence guards must actually raise.

    On green data none of these branches executes, and each guards a failure
    that survives the write-and-commit path: the sync test compares the docs
    against a fresh render of the same broken data, so corrupted output
    round-trips green forever. Positive controls: monkeypatch the data into the
    state each branch exists to catch, then assert it raises.
    """
    rt = _REGISTRY._release_table()
    original = rt.CATEGORIES

    # A duplicate slug would list one category under two tiers.
    monkeypatch.setattr(
        rt, "CATEGORIES",
        (*original, rt.Category("input-removed", "patch", "Duplicate")))
    with pytest.raises(ValueError, match="duplicate"):
        rt._validate()

    # A misspelled tier drops the category from both tables but not the enum.
    monkeypatch.setattr(
        rt, "CATEGORIES", (*original, rt.Category("x-changed", "Major", "X")))
    with pytest.raises(ValueError, match="tier vocabulary"):
        rt._validate()

    # A `|` in a meaning splits its markdown-table row into extra columns.
    monkeypatch.setattr(
        rt, "CATEGORIES",
        (*original, rt.Category("pipe-fix", "patch", "Has | a pipe")))
    with pytest.raises(ValueError, match="markdown table"):
        rt._validate()

    # A template whose enum stops varying with the data is the drift class
    # this module exists to kill; the parse-and-compare is its only guard.
    monkeypatch.setattr(rt, "CATEGORIES", original)
    monkeypatch.setattr(
        rt, "_DRIFT_VERDICT_TEMPLATE",
        rt._DRIFT_VERDICT_TEMPLATE.replace("@CATEGORY_ENUM@", '"bogus"'))
    with pytest.raises(ValueError, match="category enum diverged"):
        rt._validate()


def test_bump_table_keeps_every_slug_greppable() -> None:
    """Each category slug survives line wrapping intact, on a single line.

    Pins the no-token-splitting property behaviorally: `textwrap`'s defaults
    would break slugs at their hyphens, and the sync test cannot catch that —
    it compares the docs against a fresh render wrapped the same wrong way.
    """
    rt = _REGISTRY._release_table()
    lines = rt.render_bump_table().splitlines()
    for category in rt.CATEGORIES:
        assert any(category.slug in line for line in lines), (
            f"slug {category.slug!r} no longer appears intact on one line")
    # The rollup line carries hyphenated tokens the slug loop never sees.
    assert not any(line.rstrip().endswith("-") for line in lines), (
        "a token split across lines stops being greppable")


def test_every_fill_in_the_release_module_forbids_token_splitting() -> None:
    """Every `textwrap.fill` call site carries `**_NO_TOKEN_SPLIT`.

    The behavioral asserts above cannot see a site whose current data wraps
    identically with or without the hardening (today's rollup line does), so
    the policy is pinned structurally: a fill added or unhardened goes red
    here even while its rendered output is still innocent.
    """
    import ast

    source = (REPO_ROOT / "scripts" / "connector_release_table.py").read_text(
        encoding="utf-8")
    fills = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "fill"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "textwrap"
    ]
    assert len(fills) >= 3, "the module's fill sites moved — update this guard"
    for call in fills:
        assert any(
            kw.arg is None and isinstance(kw.value, ast.Name)
            and kw.value.id == "_NO_TOKEN_SPLIT"
            for kw in call.keywords
        ), f"textwrap.fill at line {call.lineno} lacks **_NO_TOKEN_SPLIT"


def test_scope_table_refuses_probeless_cells() -> None:
    """`_cell` must refuse an empty probe tuple.

    An unmeasured cell rendered into the scope table is the exact claim class
    this registry exists to pin, and the table shipped with such cells before
    the registry required a probe for each. No live cell is empty anymore, so
    the guard needs a direct negative or its deletion is invisible.
    """
    with pytest.raises(RuntimeError, match="no backing probe"):
        _REGISTRY._cell("spelling-only", ())


def test_run_probe_branches() -> None:
    """Every verdict branch in `run_probe` fires on a synthetic probe.

    The live probes all pass, so on a green tree none of these branches
    executes — mutation testing removed the crash guard and the forbid/require
    checks with everything staying green.
    """
    Probe = _REGISTRY.Probe

    def probe(expect, findings, **kw):
        return Probe("synthetic", expect, lambda: findings, **kw)

    error = {"severity": "error", "message": "the tail does not resolve"}
    warning = {"severity": "warning", "message": "coverage gap in the write map"}
    # Produced by the guard, not written out here: a hand-typed copy of its
    # wording is what let a rename leave the real tripwire matching nothing
    # while this stayed green against its own fabrication.
    from analitiq.validator._core import _run_guarded

    def _boom():
        raise KeyError("x")

    crash = _run_guarded(_boom, vid="contract-model")[0]

    assert _REGISTRY.run_probe(probe("clean", [warning])) is None
    assert _REGISTRY.run_probe(probe("silent", [])) is None
    assert _REGISTRY.run_probe(
        probe("error", [error], message_re="does not resolve")) is None

    assert _REGISTRY.run_probe(probe("clean", [error])).reason.startswith("expected no error")
    assert _REGISTRY.run_probe(probe("silent", [warning])).reason.startswith("expected zero")
    assert _REGISTRY.run_probe(
        probe("error", [warning], message_re="x")).reason.startswith("expected an error")
    assert "no error message matched" in _REGISTRY.run_probe(
        probe("error", [error], message_re="something else")).reason
    # The crash guard beats every expectation — a crash embedding the very
    # vocabulary a message_re looks for must still fail.
    assert "crashed" in _REGISTRY.run_probe(
        probe("error", [crash], message_re=re.escape(crash["message"][:20]))).reason
    assert "crashed" in _REGISTRY.run_probe(probe("clean", [crash])).reason
    assert "forbidden" in _REGISTRY.run_probe(
        probe("clean", [warning], forbid_re="coverage")).reason
    assert "required" in _REGISTRY.run_probe(
        probe("clean", [warning], require_re="no such text")).reason
    with pytest.raises(ValueError, match="unknown expectation"):
        _REGISTRY.run_probe(probe("eror", []))


def test_unknown_block_id_is_reported_as_a_malformed_marker(
    monkeypatch, tmp_path: Path
) -> None:
    """A GENERATED pair whose id no renderer owns must be named, not ignored.

    The pipeline generator's grammar silently ignores ids it cannot parse
    (e.g. `claim:*` — no colon in its id charset), so such a pair is rendered
    by nobody and regenerated by nobody while looking machine-maintained.
    Whether the prose inside it states anything is a reader's question now; that
    the region claims to be generated and is not is decidable, and is what this
    pins.
    """
    _REGISTRY._pipeline_gen()  # warm the cache before the paths are patched
    fake_pipeline = tmp_path / "plugins" / "analitiq-pipeline-builder"
    doc = fake_pipeline / "skills" / "bogus.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "# Bogus\n\n"
        "<!-- BEGIN GENERATED: claim:totally-made-up -->\n"
        "Function names: never checked, honest.\n"
        "<!-- END GENERATED: claim:totally-made-up -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_REGISTRY, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_REGISTRY, "PLUGINS_ROOT", tmp_path / "plugins")
    monkeypatch.setattr(_REGISTRY, "PIPELINE_PLUGIN", fake_pipeline)

    assert _REGISTRY.malformed_marker_docs() == [
        "plugins/analitiq-pipeline-builder/skills/bogus.md"
    ]


def test_probe_expectations_are_well_formed() -> None:
    """`expect="error"` requires a message pattern; others must not carry one."""
    for probe in _REGISTRY.PROBES:
        if probe.expect == "error":
            assert probe.message_re, f"{probe.id}: expect='error' needs message_re"
        else:
            assert probe.expect in ("clean", "silent"), f"{probe.id}: bad expect"
            assert not probe.message_re, f"{probe.id}: message_re without expect='error'"
