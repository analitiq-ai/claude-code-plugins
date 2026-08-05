"""Drift guard for validator-behavior claims in plugin prose.

Plugin prose states what the validator checks and does not check. Each such
sentence is a copy of a contract fact; `scripts/render_validator_claims.py`
pins them with executable **probes** (documents run through the in-repo
validator with asserted outcomes), renders the dense clusters into marked
blocks, and scans BOTH plugins for unpinned claim sentences. This module runs
the same predicates under pytest so a contract change that falsifies prose
lands as a red build, not silently-wrong authoring guidance.

The scan spans every plugin (like `test_advisory_sync.py`'s citation gate):
the validator the claims describe is one shared artifact, so one scanner pins
every claim site rather than a per-plugin copy that would itself drift.

Same environment contract as the other drift guards — skipped when the
contract packages fail to import (an incomplete checkout or missing runtime
deps; the packages themselves are the in-repo source, put on `sys.path` by the
root conftest), hard-failed in CI via `DRIFT_REQUIRE_CONTRACT_MODELS=1`.
"""

from __future__ import annotations

import importlib.util
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


def test_no_unpinned_claims_and_no_stale_waivers() -> None:
    """The class gate: an unpinned validator-behavior claim fails the build.

    Also fails on a fence naming a probe that does not exist, and on a waiver
    matching nothing (a dead exemption someone could hide behind later).
    """
    violations, dangling, stale_waivers = _REGISTRY.scan()
    assert not dangling, f"fences name unknown probes: {dangling}"
    assert not stale_waivers, (
        "stale waivers (match no claim): "
        f"{[(w.path, w.contains) for w in stale_waivers]}"
    )
    assert not violations, (
        "unpinned validator-behavior claims:\n"
        + "\n".join(f"  {v.path}:{v.line}: {v.text}" for v in violations)
        + "\nPin each: generated block, `<!-- PROBE: <id> -->` fence, ADV-* "
        "citation, or a registered Waiver in scripts/render_validator_claims.py."
    )


def test_scan_is_not_vacuous() -> None:
    """The gate must actually see the fences the prose carries.

    Both plugins carry dozens of probe fences; zero found means the fence
    grammar or the doc glob broke, and the scan would pass forever over
    nothing — the same vacuous-scope failure the advisory citation gate's
    found-citations assert exists to catch.
    """
    fenced = _REGISTRY.fence_probe_ids()
    assert len(fenced) >= 10, (
        f"only {len(fenced)} probe ids found in prose fences — the scan no "
        "longer sees the plugins' fences"
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
    round-trips green forever. Positive controls, same idiom as
    `test_scanner_positive_control`.
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


def test_release_policy_blocks_carry_no_validator_claims() -> None:
    """A release-policy block's render must never state validator behavior.

    Generated regions are pinned spans for the scan — an exemption earned by
    the probes behind them, which these blocks don't have. A validator claim
    written into a `Category.note` would render into the classifier's prompt
    looking machine-pinned while backed by nothing. The CLI check enforces
    the same rule; this is its pytest twin.
    """
    rt = _REGISTRY._release_table()
    for block_id, renderer in rt.RENDERERS.items():
        rendered = _REGISTRY._normalize(renderer())
        assert not _REGISTRY._TRIGGER_RE.search(rendered), (
            f"{block_id}: rendered text states validator behavior — carry it "
            "as a Claim with a probe instead, or reword it")


# Keyed by the VERBATIM pattern string in CLAIM_TRIGGERS — a reworded alternate
# raises KeyError here, forcing this table to move with the trigger list. Each
# specimen must be matched by its own alternate and by NO other, so a broken
# alternate cannot hide behind an overlapping neighbour.
_SPECIMENS = {
    r"validates?\s+(?:clean|cleanly|with\s+zero\s+findings)": "it validates **clean** today",
    r"passes?\s+(?:validation|every\s+check)": "the typo passes validation",
    "still\\s+passes\\b(?!\\s+(?:the|a|an)\\b)": "a broken map still passes.",
    r"(?:is|are)\s+(?:not|never)\s+(?:checked|validated|resolved|proved|proven|enforced|caught)":
        "the field is not validated",
    r"(?:never|nothing)\s+(?:checks?|checked|validates?|rejects?|proves?|enforces?|catch(?:es)?)":
        "Nothing\nrejects it at authoring time",
    r"(?:does|do)\s+not\s+(?:check|validate|resolve|read\s+filter)":
        "the local validator does **not** resolve column names",
    r"\bnot\s+checked\b": "TLS coherence: not checked here.",
    r"\bno\s+(?:check\b|backstop|validator\s+(?:checks|will))": "there is no backstop here",
    r"\bunchecked\b": "that path is unchecked",
    r"spelling[-\s](?:checked|only)": "headers are spelling-checked",
    r"\bleading\s+token\b": "only the leading token counts",
    r"read-?only\s+scope": "barred as a read-only scope",
    r"accepts?\s+nothing\s+else": "path_params accepts nothing else",
    r"slips?\s+past": "a template slips past it",
}


def test_every_trigger_alternate_matches_a_specimen() -> None:
    """Each trigger stays individually alive — and provably so.

    Live prose exercises almost none of them (pinned claims are skipped before
    matching), so a broken alternate would silently stop covering that
    phrasing class for all future prose. Iterating `CLAIM_TRIGGERS` itself
    (not the table) and requiring each specimen to match ONLY its own
    alternate is what makes this structural: a mutation to one alternate
    cannot be absorbed by an overlapping neighbour, and a reworded alternate
    fails the lookup. Plus the negative that guards the `still passes`
    lookahead's intent.
    """
    import re

    triggers = list(_REGISTRY.CLAIM_TRIGGERS)
    assert len(triggers) == len(set(triggers)) == len(_SPECIMENS)
    for i, pattern in enumerate(triggers):
        assert pattern in _SPECIMENS, (
            f"no specimen for trigger {pattern!r} — CLAIM_TRIGGERS changed; "
            "move the specimen table with it"
        )
        specimen = _REGISTRY._normalize(_SPECIMENS[pattern])
        assert re.search(pattern, specimen, re.IGNORECASE), (
            f"specimen {specimen!r} does not match its alternate {pattern!r}"
        )
        others = re.compile(
            "|".join(f"(?:{t})" for j, t in enumerate(triggers) if j != i),
            re.IGNORECASE,
        )
        assert not others.search(specimen), (
            f"specimen {specimen!r} is matched by another alternate too — it "
            f"cannot prove {pattern!r} is alive; pick a phrasing unique to it"
        )
    assert not _REGISTRY._TRIGGER_RE.search(
        _REGISTRY._normalize("aiomysql still passes the deprecated argument")
    ), "'still passes the …' must stay excluded (an argument, not validation)"


def test_scanner_positive_control(monkeypatch, tmp_path: Path) -> None:
    """The scan's violation path must actually fire — on the right line.

    On a green tree every claim is pinned, so without this control a
    regression in the trigger regex, block splitting, span mapping, or
    normalization would turn the gate vacuously green. The line-number assert
    is what catches a `_normalize` change that drops newlines (violations
    would then map to the wrong line and pinned-span checks misclassify).
    """
    doc = tmp_path / "plugins" / "synthetic.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "# Synthetic\n"
        "\n"
        "An innocent paragraph.\n"
        "\n"
        "A bold claim: response typos are **not checked** on reads.\n"
        "\n"
        "<!-- PROBE: read-body-path-typo -->\n"
        "A fenced claim: this one is not checked either.\n"
        "\n"
        "<!-- BEGIN GENERATED: claim:tls-coherence-unchecked -->\n"
        "inside a block: validates clean.\n"
        "<!-- END GENERATED: claim:tls-coherence-unchecked -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_REGISTRY, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_REGISTRY, "_scannable_docs", lambda: [doc])
    monkeypatch.setattr(
        _REGISTRY, "WAIVERS",
        (_REGISTRY.Waiver("plugins/synthetic.md", "no such sentence", "stale on purpose"),),
    )

    violations, dangling, stale = _REGISTRY.scan()
    assert [(v.line, v.text) for v in violations] == [(5, "are not checked")], (
        f"expected exactly the unpinned claim on line 5, got "
        f"{[(v.line, v.text) for v in violations]}"
    )
    assert not dangling
    assert [w.contains for w in stale] == ["no such sentence"]

    with pytest.raises(_REGISTRY.UnknownBlock):
        _REGISTRY.render_text(
            "<!-- BEGIN GENERATED: no-such-block -->\nx\n"
            "<!-- END GENERATED: no-such-block -->",
            "synthetic",
        )


def test_scan_exemption_boundaries(monkeypatch, tmp_path: Path) -> None:
    """Every exemption path in `scan()` holds at its exact boundary.

    Round-2 mutation testing showed the gate's aggregate verdict on a green
    tree exercises none of its branches: `ADV_REACH = 1000`, a deleted
    dangling-fence report, or column-anchored code fences all survived CI.
    One synthetic doc per rule, each asserting both sides of the boundary, so
    a reach constant or an exemption arm cannot drift silently.
    """
    # Pin the constants themselves: this fixture derives its geometry from
    # them, so without these asserts a widened reach would widen the fixture
    # with it and the boundary test would prove nothing (mutation-verified).
    assert _REGISTRY.FENCE_REACH == 10, (
        "FENCE_REACH changed — re-measure the live fence distances (max was 8) "
        "and rework this fixture's geometry deliberately"
    )
    reach = _REGISTRY.FENCE_REACH
    filler = [f"filler line {i} with no trigger." for i in range(reach)]
    doc = tmp_path / "plugins" / "boundaries.md"
    doc.parent.mkdir(parents=True)
    lines = [
        "# Boundaries",                                        # 1
        "",
        "enforced by ADV-ENDP-023, and the tail is not checked here.",  # 3: ADV same line -> pinned
        "one line from the citation, still pinned: not checked.",       # 4: distance 1
        "two lines from the citation, still pinned: not checked.",      # 5: distance 2 == ADV_REACH
        "",
        "<!-- PROBE: read-body-path-typo -->",                 # 7: fence
        *filler,                                               # 8 .. 7+reach: within reach
        "past the fence's reach, this one is not checked.",    # 8+reach: distance reach+1 -> flagged
        "",
        "  ```",                                               # indented code fence
        "  a fenced example: validates clean.",                # pinned as code
        "  ```",
        "",
        "<!-- PROBE: no-such-probe -->",                       # dangling fence id
    ]
    assert _REGISTRY.ADV_REACH == 2, (
        "ADV_REACH changed — rework this fixture's line geometry with it"
    )
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _REGISTRY._pipeline_gen()
    monkeypatch.setattr(_REGISTRY, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_REGISTRY, "PLUGINS_ROOT", tmp_path / "plugins")
    monkeypatch.setattr(_REGISTRY, "WAIVERS", ())

    violations, dangling, _stale = _REGISTRY.scan()
    flagged_lines = sorted(v.line for v in violations)
    # Line 3 (ADV same line) and lines 4-5 (within ADV_REACH=2 of line 3's
    # citation) are pinned; the one past the fence's reach is flagged.
    assert flagged_lines == [8 + reach], (
        f"expected only the past-reach line {8 + reach}, got "
        f"{[(v.line, v.text) for v in violations]}"
    )
    assert dangling == ["plugins/boundaries.md: fence names unknown probe 'no-such-probe'"]

    # The ADV boundary from the other side: the same claim 3 lines below the
    # citation (ADV_REACH + 1) must be flagged.
    far = tmp_path / "plugins" / "adv-far.md"
    far.write_text(
        "enforced by ADV-ENDP-023.\n"
        "filler with no trigger.\n"
        "filler with no trigger.\n"
        "three lines from the citation: not checked.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_REGISTRY, "_scannable_docs", lambda: [far])
    violations, _, _ = _REGISTRY.scan()
    assert [(v.line, v.text) for v in violations] == [(4, "not checked")]

    # Waiver-at-the-hit, the phrase condition alone: a waiver ON the hit line
    # whose `contains` lacks the trigger phrase must not apply, and comes back
    # stale, loudly.
    monkeypatch.setattr(
        _REGISTRY, "WAIVERS",
        (_REGISTRY.Waiver("plugins/adv-far.md", "three lines from the citation", "wrong phrase"),),
    )
    violations, _, stale = _REGISTRY.scan()
    assert [(v.line, v.text) for v in violations] == [(4, "not checked")]
    assert [w.reason for w in stale] == ["wrong phrase"]

    # The location condition ALONE: a waiver whose `contains` DOES carry the
    # hit's trigger phrase, but sits in another paragraph. Without
    # `contains in window` this waiver would swallow the unrelated claim too —
    # and four of the live waivers carry a trigger phrase, so each would
    # silently widen from one sentence to every same-trigger claim in its file.
    two = tmp_path / "plugins" / "two.md"
    two.write_text(
        "The waived sentence: refs are not checked here.\n"
        "\n"
        "A totally different paragraph.\n"
        "An unrelated new claim: names are not checked.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_REGISTRY, "_scannable_docs", lambda: [two])
    monkeypatch.setattr(
        _REGISTRY, "WAIVERS",
        (_REGISTRY.Waiver("plugins/two.md", "refs are not checked here", "legit"),),
    )
    violations, _, stale = _REGISTRY.scan()
    assert [(v.line, v.text) for v in violations] == [(4, "are not checked")]
    assert not stale


def test_scope_table_refuses_probeless_cells() -> None:
    """`_cell` must refuse an empty probe tuple.

    An unmeasured cell rendered into the scope table is the exact claim class
    this registry exists to pin — round 2 found three live ones. No live cell
    is empty anymore, so the guard needs a direct negative or its deletion is
    invisible.
    """
    with pytest.raises(RuntimeError, match="no backing probe"):
        _REGISTRY._cell("spelling-only", ())


def test_run_probe_branches() -> None:
    """Every verdict branch in `run_probe` fires on a synthetic probe.

    The live probes all pass, so on a green tree none of these branches
    executes — round-2 mutation testing removed the crash guard and the
    forbid/require checks with everything staying green.
    """
    Probe = _REGISTRY.Probe

    def probe(expect, findings, **kw):
        return Probe("synthetic", expect, lambda: findings, **kw)

    error = {"severity": "error", "message": "the tail does not resolve"}
    warning = {"severity": "warning", "message": "coverage gap in the write map"}
    crash = {"severity": "error",
             "message": "check 'contract-model' crashed unexpectedly (KeyError: 'x')"}

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
        probe("error", [crash], message_re="crashed unexpectedly")).reason
    assert "crashed" in _REGISTRY.run_probe(probe("clean", [crash])).reason
    assert "forbidden" in _REGISTRY.run_probe(
        probe("clean", [warning], forbid_re="coverage")).reason
    assert "required" in _REGISTRY.run_probe(
        probe("clean", [warning], require_re="no such text")).reason
    with pytest.raises(ValueError, match="unknown expectation"):
        _REGISTRY.run_probe(probe("eror", []))


def test_unknown_block_id_pins_nothing_and_is_reported(monkeypatch, tmp_path: Path) -> None:
    """A GENERATED pair whose id no renderer owns must not exempt its contents.

    The pipeline generator's grammar silently ignores ids it cannot parse
    (e.g. `claim:*` — no colon in its id charset), so such a pair used to be
    rendered by nobody, checked by nobody, and still exempt everything inside
    it from the scan — prose that LOOKS machine-pinned. Now the claim inside
    is flagged and `malformed_marker_docs` names the file.
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
    monkeypatch.setattr(_REGISTRY, "WAIVERS", ())

    violations, _dangling, _stale = _REGISTRY.scan()
    # `checks?` carries no trailing \b, so the match inside "never checked"
    # stops at "never check" — the detection is what matters, not the tail.
    assert [(v.line, v.text) for v in violations] == [(4, "never check")]
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
