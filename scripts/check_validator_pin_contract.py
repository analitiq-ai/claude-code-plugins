"""Guard: the PUBLISHED validator pin must accept the drivers the prose teaches.

The plugin prose and the runtime validator pin release on independent trains
(release-please for the plugins, hand-pushed tags for the packages). Nothing
else mechanically ties them together: every in-repo drift test grades the
in-repo contract SOURCE, while `connector-schema-validator` self-installs the
PUBLISHED `VALIDATOR_PIN` wheel. The failure this permits, and has already
produced: prose instructing authors to write `redshift+redshift_connector`
while the pinned wheel still carried the old async-only pattern that rejects it.

This script closes that gap. It installs the pin into an ISOLATED venv (never
the current environment — an installed wheel is a regular package that shadows
the in-repo namespace source; see root CLAUDE.md "The contract") and validates
the canonical `dialect+driver` values against the wheel's own
`SqlAlchemyTransport`.

Single sources, referenced not copied:
  - the pin:     `VALIDATOR_PIN` in plugins/analitiq-pipeline-builder/scripts/_bootstrap.py
  - the canon:   the "## Driver examples" table in
                 plugins/analitiq-connector-builder/skills/connector-spec-db/spec-dsn-bindings.md
  - shipped:     `[project].version` in packages/validator/pyproject.toml

Strictness — a contradiction is only sometimes a defect. The marketplace's
plugin sources are unpinned relative paths, so installs and updates ship main
HEAD, not release tags: main carrying the contradiction IS user exposure, not
merely a precursor to it.
  - pin == shipped (steady state): FAIL. No release is in flight to excuse it.
  - pin != shipped, ordinary pull_request: WARN, exit 0. The pin is
    deliberately behind while the new version publishes — root CLAUDE.md
    documents this sequencing, and failing here would make every
    contract-widening PR red with no way to unblock itself.
  - VALIDATOR_PIN_GUARD_STRICT=1: FAIL regardless of window. The tests
    workflow sets it on pushes to main (main is what users install — the
    window is then a visible red on main until the pin catch-up lands, by
    design) and on `release-please--*` branches (a Release PR merge stamps a
    plugin version onto the contradiction). Any other non-empty value is a
    GuardError — a typo must not silently downgrade to non-strict.

Exit codes: 0 verdict-ok (or window warning), 1 strict contradiction, 2
GuardError, 3 the pin is not on PyPI yet (expected mid-release — push the tag).
Two questions of the same wheel: whether it ACCEPTS the canonical drivers the
plugin prose teaches, and whether it EXPORTS every name plugin code imports
from `analitiq.validator`. The second is fatal in every window, unlike the
first — a driver the pin rejects is a tightening the next release closes, while
a name it does not export is an ImportError the first time a user runs the
plugin, and no release window makes that tolerable.

EVERY infrastructure failure — unreadable sources, venv/pip
failure, probe crash, unparseable probe output — is a GuardError: a guard
that cannot run must never read as green, and must not be mistaken for a
verdict either. Inside the probe, only pydantic's ValidationError counts as
"rejected"; any other exception crashes the probe and surfaces as exit 2, so
a defective wheel cannot launder itself into a rejection list.

Live-settings caveat (same class root CLAUDE.md notes for the environments):
no branch protection currently *requires* this check, and workflow runs on
bot-opened Release PRs start `action_required` until a human approves them —
enforcement on the Release PR is convention plus this red X, not a merge
block, unless a ruleset requiring the check is added on main.

Wiring is pinned by tests/connector_builder/test_validator_pin_guard.py.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard_lib import (  # noqa: E402
    GuardError,
    read_pin,
    read_pin_version,
    read_strict_env,
    surface_warning,
)

STRICT_ENV = "VALIDATOR_PIN_GUARD_STRICT"
CANON_SOURCE = (
    REPO_ROOT
    / "plugins"
    / "analitiq-connector-builder"
    / "skills"
    / "connector-spec-db"
    / "spec-dsn-bindings.md"
)
SHIPPED_SOURCE = REPO_ROOT / "packages" / "validator" / "pyproject.toml"

# The wheel's generated `analitiq/contracts/__init__.py` force-sets
# os.environ["DOMAIN"] (packages/contract-models/scripts/build.py), so the
# probe needs no environment of its own. Only ValidationError counts as a
# rejection — anything else (a defective wheel, an unbuilt model) must crash
# the probe so it surfaces as a GuardError, never as a verdict.
_PROBE = """\
import json, sys
from pydantic import ValidationError
from analitiq.contracts.connector import SqlAlchemyTransport

rejected = []
for value in sys.argv[1:]:
    try:
        SqlAlchemyTransport.model_validate(
            {"transport_type": "sqlalchemy", "driver": value}
        )
    except ValidationError:
        rejected.append(value)
print(json.dumps(rejected))
"""


#: Which of the names plugin code imports the pinned wheel does not export.
#:
#: The question is exactly the one `from <module> import <name>` asks, so the
#: probe asks it the same way: an attribute on the imported module, or failing
#: that a submodule of it — `from X import Y` binds either.
#:
#: An ABSENCE is a verdict (the pin does not carry the name; exit 1, attributed
#: to the file that imports it). Anything else is a defective wheel and raises
#: out of the probe as a GuardError (exit 2): laundering a module that will not
#: import into the unexported list would make "publish a release carrying them"
#: the remedy for a broken install. `ModuleNotFoundError` alone does not
#: separate the two — a module that exists and imports a dependency the wheel
#: forgot raises it as well — so the probe reads `.name`, which is the module
#: that was not found, and treats it as an absence only when it is the module
#: asked for or one of its parents. That is the same test CPython's own
#: `from … import …` applies before deciding a name is missing rather than
#: broken.
_EXPORT_PROBE = """\
import importlib, json, sys

def absent(exc, dotted):
    parts = dotted.split('.')
    return exc.name in {'.'.join(parts[:i]) for i in range(1, len(parts) + 1)}

missing = []
for dotted in json.loads(sys.argv[1]):
    module, _, name = dotted.rpartition('::')
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if not absent(exc, module):
            raise
        missing.append(dotted)
        continue
    if hasattr(mod, name):
        continue
    try:
        importlib.import_module(f'{module}.{name}')
    except ModuleNotFoundError as exc:
        if exc.name != f'{module}.{name}':
            raise
        missing.append(dotted)
json.dump(missing, sys.stdout)
"""


class PinNotPublished(RuntimeError):
    """The pinned version is not on PyPI yet. Exits 3.

    Deliberately NOT a GuardError. A package release bumps the pin and publishes
    from the PR head before merging (root CLAUDE.md "Releases"), so this state is
    a routine step of every release — red, but expected, and cleared by pushing
    the tag rather than by fixing anything. Collapsing it into exit 2 would train
    people to wave through the code that also means "the runner is broken".
    """


def read_shipped_version() -> str:
    """What this repo ships: `[project].version` in the validator pyproject.

    Anchored to the `[project]` table — a bare `^version =` would take
    whichever table came first if one were ever added above it (same
    precaution as tests/pipeline_builder/test_contract_enforcement.py).
    """
    text = SHIPPED_SOURCE.read_text(encoding="utf-8")
    project = text.split("[project]", 1)[-1].split("\n[", 1)[0]
    match = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
    if not match:
        raise GuardError(f"no [project] version found in {SHIPPED_SOURCE}")
    return match.group(1)


def read_canonical_drivers() -> list[str]:
    """Every driver in the prose's "## Driver examples" table, first column.

    The prose OWNS the canon; extracting it here (instead of copying it) means
    a new canonical driver is guarded the moment it is documented. To keep
    that promise, extraction is all-or-error: EVERY body row's first cell must
    be one backticked `dialect+driver` token, and a row this parser cannot
    read raises instead of silently dropping out of coverage. The token
    charset restates the contract's driver pattern — pinned together with the
    prose sites by tests/connector_builder/test_schema_drift.py
    (test_sqlalchemy_driver_pattern_matches_schema names this file).
    """
    text = CANON_SOURCE.read_text(encoding="utf-8")
    match = re.search(
        r"^## Driver examples$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    if not match:
        raise GuardError(f'no "## Driver examples" section in {CANON_SOURCE}')
    rows = [
        line for line in match.group(1).splitlines() if line.lstrip().startswith("|")
    ]
    if len(rows) < 3:  # GFM: header, separator, then at least one body row
        raise GuardError(
            f'the "## Driver examples" table in {CANON_SOURCE} has no body rows'
        )
    drivers = []
    for row in rows[2:]:
        first_cell = row.split("|")[1].strip()
        cell_match = re.fullmatch(r"`([a-z][a-z0-9_]*\+[a-z][a-z0-9_]*)`", first_cell)
        if not cell_match:
            raise GuardError(
                f"unparseable row in the Driver examples table of {CANON_SOURCE}: "
                f"{row!r} — every body row's first cell must be a single "
                "backticked dialect+driver token; if the driver charset widened, "
                "update read_canonical_drivers() with it (see "
                "test_sqlalchemy_driver_pattern_matches_schema)"
            )
        drivers.append(cell_match.group(1))
    return drivers


def _raise_if_unpublished(pin: str, output: str) -> None:
    """Re-raise a pip resolution failure as `PinNotPublished` — but only when the
    index was reachable and simply has no such version.

    pip reports "index is unreachable" and "version does not exist" with the same
    `No matching distribution found`. The discriminator is the version list it
    offers: a real list proves it read the index and the pin is genuinely absent
    (the expected state of an open package-release PR, whose pin names the
    version that PR is about to publish). `none` means it never saw the index —
    a network or index failure, which stays a GuardError.
    """
    if "No matching distribution found" not in output:
        return
    match = re.search(r"\(from versions: ([^)]*)\)", output)
    if not match or match.group(1).strip() == "none":
        return
    raise PinNotPublished(
        f"{pin} is not on PyPI yet — the index offers: {match.group(1).strip()}"
    )


def probe_pinned_wheel(
    pin: str, drivers: list[str], wanted: dict[str, set[tuple[str, str]]],
) -> tuple[list[str], dict[str, list[str]]]:
    """Install `pin` into a throwaway venv and ask it both questions.

    One venv: the pin is installed once, and the drivers it rejects and the
    names it does not export are read off the same wheel rather than two.
    """
    with tempfile.TemporaryDirectory(prefix="validator-pin-guard-") as tmp:
        venv_dir = Path(tmp) / "venv"
        py = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
        steps = [
            [sys.executable, "-m", "venv", str(venv_dir)],
            # -I (isolated): ambient PYTHONPATH / user site-packages must not
            # front-run the venv. An exact `==<rc>` pin resolves pre-releases
            # without --pre (PEP 440).
            [str(py), "-I", "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", pin],
        ]
        for cmd in steps:
            # check=False: the returncode is inspected by hand so the failure
            # can be wrapped as a GuardError (exit 2, never a verdict).
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                _raise_if_unpublished(pin, f"{result.stdout}\n{result.stderr}")
                raise GuardError(
                    f"{' '.join(cmd)} failed:\n"
                    f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                )
        result = subprocess.run(
            [str(py), "-I", "-c", _PROBE, *drivers],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise GuardError(f"probe crashed inside the venv:\n{result.stderr}")
        try:
            rejected = json.loads(result.stdout)
        except ValueError as exc:
            raise GuardError(
                f"probe output is not JSON ({exc}) — raw stdout:\n{result.stdout}"
            ) from exc
        # The same wheel, asked the second question before the venv goes.
        return rejected, probe_pinned_exports(py, wanted)


#: Where a plugin reaches into the validator at run time. These run from the
#: user's plugin cache against the wheel `VALIDATOR_PIN` names, so a symbol
#: newer than that pin is an ImportError on every user's machine — and the
#: suite cannot see it, because the suite runs the in-repo source.
#:
#: Agent prose is here for the same reason its Python is: the connector plugin
#: ships no `.py` at all, and root `CLAUDE.md` names
#: `connector-schema-validator.md` as the one unavoidable second copy of the
#: pin. Its import line is an instruction an agent runs.
PLUGIN_SOURCE_GLOBS = ("plugins/*/scripts/*.py", "plugins/*/agents/*.py")
PLUGIN_PROSE_GLOBS = ("plugins/*/agents/*.md",)

#: Where a `from <pinned module> import …` statement STARTS, in prose. Only
#: the opening is matched; the clause is read by scanning forward, because a
#: regex for the parenthesised form stops at the first `)` — and a `)` inside
#: a comment is not the closing one, so it truncated the list silently.
#:
#: Anchored at the start of a line, after any quoting, list or diff marker a
#: markdown file puts there, because agent prose is where a sentence MENTIONS
#: an import inline: matching one of those makes the guard refuse a symbol
#: nobody asked to have checked. Both diff markers, not just `-`: admitting the
#: removal marker alone locates the import a fenced diff is REPLACING and skips
#: the one it replaces it with, which is the half that ships.
_PROSE_IMPORT_START = re.compile(
    r"^[>\-+*\s]*from[^\S\n]+"
    r"((?:analitiq\.validator|analitiq\.contracts)"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*)"
    r"[^\S\n]+import[^\S\n]+(.*)$",
)


def _strip_comment(line: str) -> str:
    """The code half of a line. Quotes are not tracked — a `#` inside a string
    here would be prose about an import, not one."""
    return line.split("#")[0]


def _statement_head(code: str, depth: int) -> tuple[str, int, bool]:
    """The part of `code` still inside the import statement, the paren depth it
    leaves behind, and whether the statement ended on this line.

    A statement ends at the `;` that separates it from the next one, or at end
    of line once every `(` it opened is closed. The `;` counts only outside
    parens — inside a name list it cannot appear, and treating one there as a
    terminator would keep half the list.
    """
    kept: list[str] = []
    for char in code:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == ";" and depth <= 0:
            return "".join(kept), depth, True
        kept.append(char)
    return "".join(kept), depth, depth <= 0


def _iter_prose_imports(text: str):
    """`(module, clause)` for each import statement prose spells out.

    Scans rather than matches, over comment-stripped lines, so a paren or a
    `;` inside a comment cannot end the statement early and a name list
    spanning lines is read whole.
    """
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = _PROSE_IMPORT_START.match(lines[index])
        if not match:
            index += 1
            continue
        module = match.group(1)
        code = _strip_comment(match.group(2))
        clause: list[str] = []
        depth = 0
        while True:
            head, depth, ended = _statement_head(code, depth)
            clause.append(head)
            index += 1
            if ended or index >= len(lines):
                break
            code = _strip_comment(lines[index])
        if not ended:
            raise GuardError(
                f"an import from {module} opens a name list that never closes: "
                f"{' '.join(c.strip() for c in clause)!r}"
            )
        yield module, "\n".join(clause)


#: Both packages the pin installs. `analitiq-validator` depends on
#: `analitiq-contract-models` with an exact `==`, so a plugin importing a
#: contract model at run time is admitted against the version that pin
#: resolves to, exactly as it is for the validator's own names.
_PINNED_ROOTS = ("analitiq.validator", "analitiq.contracts")


def _import_names(clause: str) -> list[str]:
    """The names an import clause binds, or `[]` if it binds none readably.

    The clause arrives comment-stripped from :func:`_iter_prose_imports`, so
    this only has to read the list: `a, b`, the parenthesised form across
    lines, and `a as b` — where the WHEEL is asked for the exported name, so
    the left half is what matters.

    A clause this cannot read is an error at the call site, not a silent skip:
    a name dropped here is a name the guard never asks about, under an OK line
    saying it asked about every one.
    """
    body = clause.replace("(", " ").replace(")", " ")
    names = []
    for part in body.split(","):
        name = part.split(" as ")[0].strip()
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            return []
        names.append(name)
    return names


def _validator_module(name: str | None) -> bool:
    """Whether `name` is one of the pinned packages or a module inside one.

    Submodules count: `from analitiq.validator.pipelines import _base_id` binds
    a private name from the same wheel, and private names are the ones most
    likely to move without notice.
    """
    return bool(name) and any(
        name == root or name.startswith(f"{root}.") for root in _PINNED_ROOTS)


def read_plugin_validator_imports() -> dict[str, set[tuple[str, str]]]:
    """Every name plugin code imports from a pinned package or a submodule.

    Python is read from the AST: the question is which names a module binds,
    which is what an import statement IS, and a regex over the same lines
    would answer for a commented-out one just as readily. Prose is read with a
    regex because there is no AST for a markdown file — it still only LOCATES
    the statement; the pinned wheel decides.

    Extraction is all-or-error for what it FINDS: a source that does not
    parse, and a clause whose names cannot be read, both raise rather than
    dropping out of coverage under an OK line claiming full coverage — the
    shape `.claude/rules/guards.md` calls a silent exemption.

    What it does not reach is the reader's half: an import statement written
    somewhere the locator does not look. It reads Python from the AST, and
    prose from the start of a line after any quoting or list marker; an
    import buried mid-sentence or inside an unusual wrapper is not found, and
    a green run says nothing about one.
    """
    import ast

    found: dict[str, set[str]] = {}
    for glob in PLUGIN_SOURCE_GLOBS:
        for path in sorted(REPO_ROOT.glob(glob)):
            rel = path.relative_to(REPO_ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError) as exc:
                raise GuardError(
                    f"{rel} does not parse ({exc}); it would drop out of "
                    "coverage under an OK line claiming every plugin import "
                    "was checked"
                ) from exc
            names = {
                (node.module, alias.name)
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and _validator_module(node.module)
                for alias in node.names
            }
            if names:
                found[rel] = names

    for glob in PLUGIN_PROSE_GLOBS:
        for path in sorted(REPO_ROOT.glob(glob)):
            rel = path.relative_to(REPO_ROOT).as_posix()
            names = set()
            for module, clause in _iter_prose_imports(
                    path.read_text(encoding="utf-8")):
                if not _validator_module(module):
                    continue
                bound = _import_names(clause)
                if not bound:
                    raise GuardError(
                        f"{rel}: cannot read the names imported from "
                        f"{module} in {clause.strip()!r}; it would drop out "
                        "of coverage under an OK line claiming every plugin "
                        "import was checked"
                    )
                names |= {(module, name) for name in bound}
            if names:
                found[rel] = found.get(rel, set()) | names
    return found


def probe_pinned_exports(
    py: Path, wanted: dict[str, set[tuple[str, str]]],
) -> dict[str, list[str]]:
    """Which of those names the pinned wheel does not export, per file.

    Runs in the venv the driver probe already built, so the pin is installed
    once and both questions are asked of the same wheel.
    """
    qualified = sorted({f"{module}::{name}"
                        for names in wanted.values()
                        for module, name in names})
    if not qualified:
        raise GuardError(
            "no plugin source imports anything from the pinned packages — the "
            f"globs {PLUGIN_SOURCE_GLOBS + PLUGIN_PROSE_GLOBS} match nothing, "
            "so this check is reading nothing and would report agreement"
        )
    result = subprocess.run(
        [str(py), "-I", "-c", _EXPORT_PROBE, json.dumps(qualified)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise GuardError(f"export probe crashed inside the venv:\n{result.stderr}")
    try:
        missing = set(json.loads(result.stdout))
    except ValueError as exc:
        raise GuardError(
            f"export probe output is not JSON ({exc}) — raw stdout:\n"
            f"{result.stdout}"
        ) from exc
    return {
        path: sorted(f"{module}.{name}" for module, name in names
                     if f"{module}::{name}" in missing)
        for path, names in wanted.items()
        if any(f"{module}::{name}" in missing for module, name in names)
    }


def main() -> int:
    try:
        pin = read_pin()
        pin_version = read_pin_version()
        shipped = read_shipped_version()
        drivers = read_canonical_drivers()
        strict = read_strict_env(STRICT_ENV) or pin_version == shipped

        print(f"pin: {pin}  shipped: {shipped}  strict: {strict}")
        print(f"canonical drivers ({CANON_SOURCE.name}): {', '.join(drivers)}")

        wanted = read_plugin_validator_imports()
        print(f"plugin imports from the pinned packages: "
              f"{sum(len(n) for n in wanted.values())} across {len(wanted)} file(s)")

        rejected, unexported = probe_pinned_wheel(pin, drivers, wanted)
    except PinNotPublished as exc:
        print(f"NOT PUBLISHED YET: {exc}", file=sys.stderr)
        print(
            "Expected while a package-release PR is open — the pin names the "
            "version this PR releases, and the publish runs from the PR head "
            "before the merge. Push the release tag at this commit, approve the "
            "publish, then re-run this job. See root CLAUDE.md 'Releases'.",
            file=sys.stderr,
        )
        return 3
    except (GuardError, OSError, UnicodeDecodeError) as exc:
        # OSError covers unreadable source files and unlaunchable
        # subprocesses; UnicodeDecodeError covers a source that is readable
        # and not text — a `ValueError`, so not caught by OSError, and an
        # uncaught one exits 1, which is this guard's VERDICT code.
        print(f"GUARD ERROR (not a verdict): {exc}", file=sys.stderr)
        return 2

    if unexported:
        # Always fatal, in every window. A driver the pin rejects is a
        # tightening the release closes; a name it does not export is an
        # ImportError the moment a user runs the plugin, and no window makes
        # that tolerable.
        print("FAIL: plugin scripts import names the pinned release does not "
              "export — every user would get an ImportError:", file=sys.stderr)
        for path, names in sorted(unexported.items()):
            print(f"  {path}: {', '.join(names)}", file=sys.stderr)
        print(f"\nEither stop using them from plugin code, or publish a "
              f"release carrying them and move VALIDATOR_PIN past {pin}.",
              file=sys.stderr)
        return 1

    if not rejected:
        print("OK: the pinned release accepts every canonical driver, and "
              "the packages it installs export every name the plugins import "
              "from them.")
        return 0

    verdict = (
        f"the pinned {pin} REJECTS canonical driver(s) the plugin prose "
        f"teaches: {', '.join(rejected)}"
    )
    if strict:
        print(f"FAIL: {verdict}", file=sys.stderr)
        print(
            "Users install main HEAD, so this contradiction is (or is about to "
            "be) live inside one plugin flow — finish the package release and "
            "bump VALIDATOR_PIN first (see root CLAUDE.md).",
            file=sys.stderr,
        )
        return 1
    surface_warning(
        f"WARNING (release window, pin {pin_version} != shipped {shipped}): {verdict}\n"
        "Allowed on an ordinary PR while the new package version publishes; the "
        "pin bump follow-up must land before any plugin Release PR merges, and "
        "main stays red (strict on push) until it does.",
        title="validator pin",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
