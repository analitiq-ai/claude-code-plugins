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


def test_probe_expectations_are_well_formed() -> None:
    """`expect="error"` requires a message pattern; others must not carry one."""
    for probe in _REGISTRY.PROBES:
        if probe.expect == "error":
            assert probe.message_re, f"{probe.id}: expect='error' needs message_re"
        else:
            assert probe.expect in ("clean", "silent"), f"{probe.id}: bad expect"
            assert not probe.message_re, f"{probe.id}: message_re without expect='error'"
