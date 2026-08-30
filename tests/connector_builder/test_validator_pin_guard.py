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
import venv
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


@pytest.mark.parametrize("clause, expected", [
    ("main", ["main"]),
    ("a, b", ["a", "b"]),
    ("main as m", ["main"]),
    # No comment cases here — the scanner strips them before this sees a
    # clause, and handing one in grades a seam that does not exist.
    ("(validate_document, check_coverage)", ["validate_document", "check_coverage"]),
    ("(\n    first,\n    second,\n)", ["first", "second"]),
])
def test_the_import_reader_reads_the_forms_an_author_writes(guard, clause, expected):
    """The name list alone, already comment-stripped by the scanner that
    produces it. Comments are the SCANNER's contract — a case handing them to
    this function grades a seam that does not exist, which is how a locator
    truncating the clause went unnoticed while these passed. The file-level
    cases below cross that seam."""
    assert guard._import_names(clause) == expected


@pytest.mark.parametrize("prose, expected", [
    # The seam the accept-path cases above do not cross. Each of these is a
    # FILE, read end to end, so a locator that truncates the clause before the
    # name parser sees it is caught — which the parser's own cases cannot do,
    # since they are handed complete clauses by hand.
    ("from analitiq.validator import main\n", ["main"]),
    ("from analitiq.validator import (\n    main,\n    check_coverage,\n)\n",
     ["check_coverage", "main"]),
    # A `)` inside a comment is not the one that closes the list.
    ("from analitiq.validator import (\n"
     "    main,   # returns (findings, code)\n"
     "    check_coverage,\n)\n", ["check_coverage", "main"]),
    # A heredoc body inside a fenced shell block — the form the connector
    # agent actually writes, where the import starts its own line and the
    # shell quoting is on other lines entirely.
    ('python3 - <<\'PY\'\nfrom analitiq.validator import main\nPY\n', ["main"]),
    # A statement ends at the `;` that separates it from the next one; without
    # that, `main()` joined the name list and the clause read as unparseable.
    ("from analitiq.validator import main; main()\n", ["main"]),
    # ...but a `;` cannot appear inside a name list, so one there would be part
    # of a clause this must refuse whole rather than a terminator.
    ("from analitiq.validator import (\n    main,\n)  ; main()\n", ["main"]),
    # Quoted or listed by markdown, and both markers of a fenced diff: locating
    # only the removal locates the import being REPLACED and skips the one that
    # ships.
    ("> from analitiq.validator import main\n", ["main"]),
    ("- from analitiq.validator import main\n", ["main"]),
    ("+ from analitiq.validator import main\n", ["main"]),
])
def test_the_import_reader_reads_a_whole_file(
    guard, monkeypatch, tmp_path, prose, expected,
):
    """End to end, from file text to names — the seam where a truncating
    locator hides. Grading the parser alone passed a clause the locator could
    never have produced, so a regex that stopped at the first `)` shipped
    green over names it never asked the wheel about."""
    agents = tmp_path / "plugins" / "p" / "agents"
    agents.mkdir(parents=True)
    (agents / "a.md").write_text(prose, encoding="utf-8")
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)

    found = guard.read_plugin_validator_imports()
    assert sorted(n for _m, n in found["plugins/p/agents/a.md"]) == expected


def test_the_import_reader_ignores_an_inline_mention(guard, monkeypatch, tmp_path):
    """Agent prose is where a sentence MENTIONS an import inside backticks.
    Matching one makes the guard refuse a symbol nobody asked to have checked,
    and accuse the author of dropping coverage."""
    agents = tmp_path / "plugins" / "p" / "agents"
    agents.mkdir(parents=True)
    (agents / "a.md").write_text(
        "The script does `from analitiq.validator import validate_document` "
        "before it runs.\n", encoding="utf-8")
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)

    assert guard.read_plugin_validator_imports() == {}


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


@pytest.fixture(scope="module")
def probe_interpreter(tmp_path_factory):
    """A real interpreter with a site-packages this file controls.

    The export probe's whole subject is what an interpreter does when a module
    or a name is not there, so it is graded by running it — the production
    command, on modules built to fail the way a defective wheel fails. A venv
    is how the probed names get somewhere the probe's isolated interpreter
    will look; `-I` ignores `PYTHONPATH` by design.
    """
    root = tmp_path_factory.mktemp("probe-venv")
    venv.create(root, with_pip=False)
    py = root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python3")
    if not py.exists():  # pragma: no cover - platform layout
        pytest.skip(f"no interpreter at {py}")
    site = next(root.glob("lib/python*/site-packages"), None) or next(
        root.glob("Lib/site-packages"))
    return py, site


@pytest.mark.parametrize("wanted, expected", [
    # A name the module does not export.
    ({("json", "no_such_name_here")}, ["json.no_such_name_here"]),
    # A module the wheel does not carry at all. It reaches the plugin's author
    # as the same ImportError an absent name does, so it is the same verdict —
    # reported against the file that imports it, not raised as "the runner is
    # broken", which loses both the file and the remedy.
    ({("no_such_package_here", "thing")}, ["no_such_package_here.thing"]),
])
def test_the_export_probe_calls_an_absence_a_verdict(
    guard, probe_interpreter, wanted, expected,
):
    py, _site = probe_interpreter
    assert guard.probe_pinned_exports(py, {"plugins/p/scripts/x.py": wanted}) == {
        "plugins/p/scripts/x.py": expected}


@pytest.mark.parametrize("wanted", [
    {("json", "dumps")},          # an attribute
    {("email", "mime")},          # a submodule `import email` does not bind
])
def test_the_export_probe_accepts_what_the_import_would_bind(
    guard, probe_interpreter, wanted,
):
    """`from X import Y` binds a submodule as readily as an attribute, so a
    probe asking only `hasattr` would report every submodule import as a name
    to publish."""
    py, _site = probe_interpreter
    assert guard.probe_pinned_exports(py, {"plugins/p/scripts/x.py": wanted}) == {}


@pytest.mark.parametrize("layout, wanted", [
    # The module itself will not import.
    ({"broken_top/__init__.py": "import absent_dependency_xyz\n"},
     {("broken_top", "thing")}),
    # The module imports, and the submodule the name would bind will not.
    ({"broken_sub/__init__.py": "",
      "broken_sub/leaf.py": "import absent_dependency_xyz\n"},
     {("broken_sub", "leaf")}),
])
def test_the_export_probe_refuses_to_call_a_defective_wheel_a_missing_name(
    guard, probe_interpreter, layout, wanted,
):
    """A module that exists and will not import is a broken install, and
    `ModuleNotFoundError` alone does not separate it from an absence — the
    module it names is a dependency, not the one asked for. Reported as a
    missing name, the remedy printed is "publish a release carrying them",
    which fixes nothing and hides the real failure."""
    py, site = probe_interpreter
    for rel, text in layout.items():
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    with pytest.raises(guard.GuardError, match="export probe crashed"):
        guard.probe_pinned_exports(py, {"plugins/p/scripts/x.py": wanted})


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
