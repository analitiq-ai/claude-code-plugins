"""Pin the wiring and verdict semantics of scripts/check_validator_pin_contract.py.

The guard's venv/PyPI probe runs OUTSIDE pytest (a CI job — see its module
docstring), so nothing in the ordinary suite would notice its file-reading
wiring rotting or its verdict logic regressing. These tests cover everything
offline-testable: the readers against the real working tree, the extraction's
all-or-error contract, every `main()` verdict branch with the probe
monkeypatched out (no venv, no network, no wheel install), the CI job's
wiring, and the one semantic invariant the verdicts rest on: the canon
extracted from prose must be accepted by the IN-REPO contract, so a guard
failure can only ever mean the PUBLISHED wheel lags the prose, never that the
prose itself is wrong.
"""
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from _pins import require_contract_models

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_validator_pin_contract.py"
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"


@pytest.fixture(scope="module")
def guard():
    spec = spec_from_file_location("check_validator_pin_contract", _SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the readers, against the real working tree ---------------------------


def test_reads_the_pin_from_its_single_source(guard):
    # `_bootstrap.py` owns the pin; the guard references it by regex. If that
    # file is refactored so the regex misses, this fails here instead of the
    # guard erroring in CI.
    assert guard.read_pin().startswith("analitiq-validator==")
    assert guard.read_pin_version() != ""


def test_reads_the_shipped_version(guard):
    from _pins import PINNED_VERSION

    # Same value `_pins` tracks (packages/contract-models/pyproject.toml moves
    # in lockstep with the validator's — test_contract_models_pin.py enforces
    # that), read from the validator side by the guard's own regex.
    assert guard.read_shipped_version() == PINNED_VERSION


def test_extracts_the_canonical_drivers_from_prose(guard):
    drivers = guard.read_canonical_drivers()
    # Extraction is all-or-error by design; also require the canonical
    # sync-driver path this guard exists for, so a prose restructure cannot
    # silently drop it from coverage.
    assert "redshift+redshift_connector" in drivers


def test_extraction_is_all_or_error(guard, tmp_path, monkeypatch):
    # The docstring promises "a new canonical driver is guarded the moment it
    # is documented" — which is only true if a row the parser cannot read is
    # an ERROR, not a silent drop. An annotated first cell must raise, and a
    # well-formed added row must be picked up.
    table = (
        "## Driver examples\n\n"
        "| Driver | Template |\n|---|---|\n"
        "| `postgresql+asyncpg` | `postgresql+asyncpg://x` |\n"
        "| `databricks+dbsql` | `databricks+dbsql://x` |\n"
    )
    good = tmp_path / "good.md"
    good.write_text(table, encoding="utf-8")
    monkeypatch.setattr(guard, "CANON_SOURCE", good)
    assert guard.read_canonical_drivers() == [
        "postgresql+asyncpg",
        "databricks+dbsql",
    ]

    bad = tmp_path / "bad.md"
    bad.write_text(
        table + "| `mysql+aiomysql` (sync) | `mysql+aiomysql://x` |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "CANON_SOURCE", bad)
    with pytest.raises(guard.GuardError, match="unparseable row"):
        guard.read_canonical_drivers()


# --- main()'s verdict branches, probe monkeypatched out -------------------


@pytest.fixture(autouse=True)
def _isolate_actions_env(monkeypatch):
    # In CI these are always set, and `surface_warning` writes DIRECTLY to
    # the GITHUB_STEP_SUMMARY file — pytest captures stdout, not file writes,
    # so without this a warn-branch test would publish its fabricated
    # rejection into the real job summary.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def test_verdict_accepted_is_ok(guard, monkeypatch):
    monkeypatch.setenv("VALIDATOR_PIN_GUARD_STRICT", "")
    monkeypatch.setattr(guard, "probe_pinned_wheel", lambda pin, drivers, wanted: ([], {}))
    assert guard.main() == 0


def test_verdict_rejection_fails_in_steady_state(guard, monkeypatch):
    # pin == shipped and no env override: a rejection is a live defect.
    monkeypatch.setenv("VALIDATOR_PIN_GUARD_STRICT", "")
    monkeypatch.setattr(guard, "read_shipped_version", guard.read_pin_version)
    monkeypatch.setattr(
        guard, "probe_pinned_wheel", lambda pin, drivers, wanted: (["redshift+redshift_connector"], {})
    )
    assert guard.main() == 1


def test_verdict_rejection_warns_inside_a_release_window(
    guard, monkeypatch, tmp_path
):
    # pin != shipped and the workflow set '' (ordinary PR): warn, exit 0. The
    # '' env case is load-bearing — the workflow's ternary emits '' on
    # non-strict refs, and only the literal '1' may mean strict.
    monkeypatch.setenv("VALIDATOR_PIN_GUARD_STRICT", "")
    monkeypatch.setattr(
        guard, "read_shipped_version", lambda: guard.read_pin_version() + ".post1"
    )
    monkeypatch.setattr(guard, "probe_pinned_wheel", lambda pin, drivers, wanted: (["x+y"], {}))
    # Exercise the Actions surfacing against a redirected summary file, so the
    # warn path's visibility mechanism is covered without touching the real
    # job summary (see _isolate_actions_env).
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert guard.main() == 0
    assert "x+y" in summary.read_text(encoding="utf-8")


def test_verdict_env_override_is_strict_even_in_a_window(guard, monkeypatch):
    monkeypatch.setenv("VALIDATOR_PIN_GUARD_STRICT", "1")
    monkeypatch.setattr(
        guard, "read_shipped_version", lambda: guard.read_pin_version() + ".post1"
    )
    monkeypatch.setattr(guard, "probe_pinned_wheel", lambda pin, drivers, wanted: (["x+y"], {}))
    assert guard.main() == 1


def test_unrecognized_strict_value_is_a_guard_error(guard, monkeypatch):
    # A typo like 'true' must not silently downgrade strict to warn-only.
    monkeypatch.setenv("VALIDATOR_PIN_GUARD_STRICT", "true")
    monkeypatch.setattr(guard, "probe_pinned_wheel", lambda pin, drivers, wanted: ([], {}))
    assert guard.main() == 2


def test_unpublished_pin_exits_3_not_2(guard, monkeypatch):
    # A package-release PR pins the version it is about to publish, so this red
    # is a routine step (root CLAUDE.md "Releases"). It must be distinguishable
    # from "the runner is broken", or people learn to wave exit 2 through.
    monkeypatch.setenv("VALIDATOR_PIN_GUARD_STRICT", "")

    def unpublished(pin, drivers, wanted):
        raise guard.PinNotPublished(f"{pin} is not on PyPI yet")

    monkeypatch.setattr(guard, "probe_pinned_wheel", unpublished)
    assert guard.main() == 3


def test_unpublished_is_told_apart_from_an_unreachable_index(guard):
    # Both cases print "No matching distribution found"; only the version list
    # discriminates. Getting this backwards would either mask a dead index as a
    # routine release step, or make every release PR look like broken CI.
    pin = "analitiq-validator==1.0.0rc20"

    with pytest.raises(guard.PinNotPublished):
        guard._raise_if_unpublished(
            pin,
            "ERROR: Could not find a version that satisfies the requirement "
            f"{pin} (from versions: 1.0.0rc14, 1.0.0rc19)\n"
            f"ERROR: No matching distribution found for {pin}",
        )

    # Index unreachable -> `none`. Must fall through to the GuardError path.
    guard._raise_if_unpublished(
        pin,
        "WARNING: Retrying after connection broken by ProxyError\n"
        "ERROR: Could not find a version that satisfies the requirement "
        f"{pin} (from versions: none)\n"
        f"ERROR: No matching distribution found for {pin}",
    )

    # An unrelated pip failure (e.g. a build error) is not a resolution failure.
    guard._raise_if_unpublished(pin, "ERROR: Failed building wheel for foo")


@pytest.mark.parametrize("shipped", ["same", "ahead"])
def test_an_unexported_name_fails_in_every_window(guard, monkeypatch, shipped):
    """The second question's whole point: a driver the pin rejects is a
    tightening the next release closes, so it warns inside a release window —
    a name the pin does not export is an ImportError the first time a user
    runs the plugin, and no window makes that tolerable."""
    monkeypatch.setenv("VALIDATOR_PIN_GUARD_STRICT", "")
    if shipped == "ahead":
        monkeypatch.setattr(
            guard, "read_shipped_version",
            lambda: guard.read_pin_version() + ".post1")
    monkeypatch.setattr(
        guard, "probe_pinned_wheel",
        lambda pin, drivers, wanted: ([], {"plugins/p/scripts/x.py": ["nope"]}))
    assert guard.main() == 1


def test_the_import_reader_refuses_a_clause_it_cannot_read(guard, monkeypatch, tmp_path):
    """Prose has no AST, so the name list is parsed by hand — and a clause the
    parser cannot read must be an error, not a silent skip under an OK line
    claiming every plugin import was asked of the wheel."""
    agents = tmp_path / "plugins" / "p" / "agents"
    agents.mkdir(parents=True)
    (agents / "a.md").write_text(
        "from analitiq.validator import *\n", encoding="utf-8")
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)

    with pytest.raises(guard.GuardError, match="cannot read the names"):
        guard.read_plugin_validator_imports()


def test_the_import_reader_refuses_a_source_it_cannot_parse(guard, monkeypatch, tmp_path):
    """A plugin source that does not parse must not drop silently out of
    coverage: the OK line claims every plugin import was asked of the wheel,
    and a skipped file makes that false while the guard stays green."""
    plugin = tmp_path / "plugins" / "p" / "scripts"
    plugin.mkdir(parents=True)
    (plugin / "broken.py").write_text("def (:\n", encoding="utf-8")
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)

    with pytest.raises(guard.GuardError, match="does not parse"):
        guard.read_plugin_validator_imports()


def test_the_import_reader_refuses_an_empty_read(guard, monkeypatch, tmp_path):
    """Zero imports found is a walk that has stopped measuring, not a plugin
    tree that reaches into nothing — the same floor every other extractor in
    this file carries."""
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)

    with pytest.raises(guard.GuardError, match="reading nothing"):
        guard.probe_pinned_exports(Path(sys.executable), {})


def test_the_import_reader_sees_submodules_and_agent_prose(guard):
    """Three channels, all of which shipped uncovered once: the package, a
    submodule (where the private names live), and an agent's prose — the
    connector plugin ships no Python at all, so prose is the only surface it
    has."""
    by_file = guard.read_plugin_validator_imports()
    modules = {module for names in by_file.values() for module, _ in names}
    assert "analitiq.validator" in modules, modules
    assert any(m.startswith("analitiq.validator.") for m in modules), modules
    assert any(path.endswith(".md") for path in by_file), sorted(by_file)


def test_guard_error_exits_2_never_a_verdict(guard, monkeypatch):
    # "A guard that cannot run must never read as green" — nor as a
    # contradiction. Both an infrastructure failure in the probe and an
    # unreadable source must exit 2.
    monkeypatch.setenv("VALIDATOR_PIN_GUARD_STRICT", "")

    def boom(pin, drivers, wanted):
        raise guard.GuardError("venv exploded")

    monkeypatch.setattr(guard, "probe_pinned_wheel", boom)
    assert guard.main() == 2

    monkeypatch.setattr(guard, "CANON_SOURCE", Path("/nonexistent/canon.md"))
    assert guard.main() == 2


# --- the CI wiring and the semantic invariant -----------------------------


def test_ci_job_runs_the_guard_with_the_strictness_key(guard):
    # The gap this guard exists to close — a pinned release whose published
    # wheel rejects the canonical drivers the prose teaches — stays open unless
    # CI actually runs it with the documented strictness policy. Presence-first,
    # same convention as the CLAUDE.md pin assertions in
    # test_contract_enforcement.py.
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/check_validator_pin_contract.py" in workflow
    assert "VALIDATOR_PIN_GUARD_STRICT" in workflow
    assert "startsWith(github.head_ref, 'release-please--')" in workflow
    assert "github.event_name == 'push'" in workflow


def test_canon_is_accepted_by_the_in_repo_contract(guard):
    # The guard's verdict semantics assume prose canon ⊆ contract: then a
    # rejection can only mean the published wheel is behind. Prove the
    # assumption against the in-repo source the rest of the suite grades.
    require_contract_models("analitiq.contracts.connector")
    from analitiq.contracts.connector import SqlAlchemyTransport

    for value in guard.read_canonical_drivers():
        SqlAlchemyTransport.model_validate(
            {"transport_type": "sqlalchemy", "driver": value}
        )
