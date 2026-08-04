"""Drift guard for validator-behavior claims in plugin prose (issue #133).

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

Same environment contract as the other drift guards: skipped when the pinned
packages are absent (offline dev), hard-failed in CI via
`DRIFT_REQUIRE_CONTRACT_MODELS=1`.
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
    find the claim the probe backs (its id appears in a `PROBE:` fence or a
    rendered block), update the prose AND the probe together in
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
        "marker glob no longer finds them (the issue #65 vacuous-scope failure "
        "mode); the blocks exist, so the scan is broken, not the prose."
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
    nothing — the same failure mode issue #65 documented for the advisory
    citation gate.
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


_SPECIMENS = {
    r"validates?\s+(?:clean|cleanly|with\s+zero\s+findings)": "it validates **clean** today",
    r"passes?\s+(?:validation|every\s+check)": "the typo passes validation",
    "still_passes": "a map missing all of these still passes.",
    r"(?:is|are)\s+(?:not|never)\s+(?:checked|validated|resolved|proved|proven|enforced|caught)":
        "runnability is not checked for a draft",
    r"(?:never|nothing)\s+(?:checks?|checked|validates?|rejects?|proves?|enforces?|catch(?:es)?)":
        "Nothing\nvalidates the function name",
    r"(?:does|do)\s+not\s+(?:check|validate|resolve|read\s+filter)":
        "the local validator does **not** resolve column names",
    r"\bnot\s+checked\b": "consistency is not checked.",
    r"\bno\s+(?:check\b|backstop|validator\s+(?:checks|will))": "there is no check at all",
    r"\bunchecked\b": "that path is unchecked",
    r"spelling[-\s](?:checked|only)": "headers are spelling-checked only",
    r"\bleading\s+token\b": "checked on its leading token",
    r"read-?only\s+scope": "barred — read-only scope",
    r"accepts?\s+nothing\s+else": "path_params accepts nothing else",
    r"slips?\s+past": "a template slips past it",
}


def test_every_trigger_alternate_matches_a_specimen() -> None:
    """Each trigger stays individually alive.

    Live prose exercises almost none of them (pinned blocks are skipped before
    matching), so a broken alternate would silently stop covering that
    phrasing class for all future prose. One specimen per alternate, matched
    through `_normalize` exactly as the scanner matches; plus the negative
    that guards the `still passes` lookahead's intent.
    """
    triggers = list(_REGISTRY.CLAIM_TRIGGERS)
    assert len(triggers) == len(_SPECIMENS), (
        "CLAIM_TRIGGERS and the specimen table are out of step — add a "
        "specimen for the new/changed trigger"
    )
    for pattern, specimen in _SPECIMENS.items():
        import re

        compiled = _REGISTRY._TRIGGER_RE
        normalized = _REGISTRY._normalize(specimen)
        assert compiled.search(normalized), f"no trigger matches specimen {specimen!r}"
        if pattern != "still_passes":
            assert re.search(pattern, normalized, re.IGNORECASE), (
                f"specimen {specimen!r} no longer matches its own alternate "
                f"{pattern!r} — realign the table"
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


def test_probe_expectations_are_well_formed() -> None:
    """`expect="error"` requires a message pattern; others must not carry one."""
    for probe in _REGISTRY.PROBES:
        if probe.expect == "error":
            assert probe.message_re, f"{probe.id}: expect='error' needs message_re"
        else:
            assert probe.expect in ("clean", "silent"), f"{probe.id}: bad expect"
            assert not probe.message_re, f"{probe.id}: message_re without expect='error'"
