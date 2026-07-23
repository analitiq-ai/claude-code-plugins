"""Guard: the PUBLISHED validator pin must accept the drivers the prose teaches.

The plugin prose and the runtime validator pin release on independent trains
(release-please for the plugins, hand-pushed tags for the packages). Nothing
else mechanically ties them together: every in-repo drift test grades the
in-repo contract SOURCE, while `connector-schema-validator` self-installs the
PUBLISHED `VALIDATOR_PIN` wheel. Issue #71 is the failure this permits — prose
instructing authors to write `redshift+redshift_connector` while the pinned
wheel still carried the old async-only pattern that rejects it.

This script closes that gap. It installs the pin into an ISOLATED venv (never
the current environment — an installed wheel is a regular package that shadows
the in-repo namespace source; see root CLAUDE.md "The contract") and validates
the canonical `dialect+driver` values against the wheel's own
`SqlAlchemyTransport`.

Single sources, referenced not copied:
  - the pin:     `VALIDATOR_PIN` in plugins/analitiq-pipeline-builder/scripts/_analitiq.py
  - the canon:   the "## Driver examples" table in
                 plugins/analitiq-connector-builder/skills/connector-spec-db/spec-dsn-bindings.md
  - shipped:     `[project].version` in packages/validator/pyproject.toml

Strictness — a contradiction is only sometimes a defect:
  - pin == shipped (steady state): FAIL. The published contract the plugin
    installs at runtime rejects what its own prose teaches, and no release is
    in flight to fix it.
  - pin != shipped (a package release window is in progress): WARN, exit 0.
    The pin is deliberately behind while the new version publishes — root
    CLAUDE.md documents this sequencing, and failing here would make every
    contract-widening PR red with no way to unblock itself.
  - VALIDATOR_PIN_GUARD_STRICT=1: FAIL regardless of window. The tests
    workflow sets it on `release-please--*` branches, because merging a
    Release PR is exactly the act that ships the contradiction to users —
    the window excuse does not apply there.

Infrastructure errors (venv build, pip install, probe crash) exit 2 in every
mode: a guard that cannot run must never read as green.

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

PIN_SOURCE = (
    REPO_ROOT / "plugins" / "analitiq-pipeline-builder" / "scripts" / "_analitiq.py"
)
CANON_SOURCE = (
    REPO_ROOT
    / "plugins"
    / "analitiq-connector-builder"
    / "skills"
    / "connector-spec-db"
    / "spec-dsn-bindings.md"
)
SHIPPED_SOURCE = REPO_ROOT / "packages" / "validator" / "pyproject.toml"

# `analitiq.contracts.shared.common` reads os.environ["DOMAIN"] at import; the
# probe below imports the contract models inside the venv, so it needs the same
# value the root conftest sets for pytest.
PROBE_ENV_DOMAIN = "analitiq.ai"

_PROBE = """\
import json, sys
from analitiq.contracts.connector import SqlAlchemyTransport

rejected = []
for value in sys.argv[1:]:
    try:
        SqlAlchemyTransport.model_validate(
            {"transport_type": "sqlalchemy", "driver": value}
        )
    except Exception:
        rejected.append(value)
print(json.dumps(rejected))
"""


class GuardError(RuntimeError):
    """The guard could not run — infrastructure, not a verdict."""


def read_pin() -> str:
    """The full `analitiq-validator==X` requirement from `_analitiq.py`."""
    match = re.search(
        r'^VALIDATOR_PIN = "(analitiq-validator==[^"]+)"$',
        PIN_SOURCE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise GuardError(f"VALIDATOR_PIN not found in {PIN_SOURCE}")
    return match.group(1)


def read_pin_version() -> str:
    return read_pin().split("==", 1)[1]


def read_shipped_version() -> str:
    """What this repo ships: `[project].version` in the validator pyproject."""
    match = re.search(
        r'^version = "([^"]+)"$',
        SHIPPED_SOURCE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise GuardError(f"[project].version not found in {SHIPPED_SOURCE}")
    return match.group(1)


def read_canonical_drivers() -> list[str]:
    """First-column driver values of the prose's "## Driver examples" table.

    The prose OWNS the canon; extracting it here (instead of copying it) means
    a new canonical driver is guarded the moment it is documented. Extraction
    failure raises — an empty canon must never read as "nothing to check".
    """
    text = CANON_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"^## Driver examples$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        raise GuardError(f'no "## Driver examples" section in {CANON_SOURCE}')
    drivers = re.findall(
        r"^\|\s*`([a-z][a-z0-9_]*\+[a-z][a-z0-9_]*)`\s*\|", match.group(1), re.MULTILINE
    )
    if not drivers:
        raise GuardError(
            f'no `dialect+driver` rows extracted from "## Driver examples" in {CANON_SOURCE} '
            "— if the table was restructured, update read_canonical_drivers() with it"
        )
    return drivers


def probe_pinned_wheel(pin: str, drivers: list[str]) -> list[str]:
    """Install `pin` into a throwaway venv; return the drivers its models reject."""
    with tempfile.TemporaryDirectory(prefix="validator-pin-guard-") as tmp:
        venv_dir = Path(tmp) / "venv"
        py = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
        steps = [
            [sys.executable, "-m", "venv", str(venv_dir)],
            # An exact `==<rc>` pin resolves pre-releases without --pre (PEP 440).
            [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", pin],
        ]
        for cmd in steps:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise GuardError(f"{' '.join(cmd)} failed:\n{result.stderr}")
        result = subprocess.run(
            [str(py), "-c", _PROBE, *drivers],
            capture_output=True,
            text=True,
            env={**os.environ, "DOMAIN": PROBE_ENV_DOMAIN},
        )
        if result.returncode != 0:
            raise GuardError(f"probe crashed inside the venv:\n{result.stderr}")
        return json.loads(result.stdout)


def main() -> int:
    pin = read_pin()
    pin_version = read_pin_version()
    shipped = read_shipped_version()
    drivers = read_canonical_drivers()
    strict = os.environ.get("VALIDATOR_PIN_GUARD_STRICT") == "1" or pin_version == shipped

    print(f"pin: {pin}  shipped: {shipped}  strict: {strict}")
    print(f"canonical drivers ({CANON_SOURCE.name}): {', '.join(drivers)}")

    try:
        rejected = probe_pinned_wheel(pin, drivers)
    except GuardError as exc:
        print(f"GUARD ERROR (not a verdict): {exc}", file=sys.stderr)
        return 2

    if not rejected:
        print("OK: the pinned release accepts every canonical driver.")
        return 0

    verdict = (
        f"the pinned {pin} REJECTS canonical driver(s) the plugin prose "
        f"teaches: {', '.join(rejected)}"
    )
    if strict:
        print(f"FAIL: {verdict}", file=sys.stderr)
        print(
            "In steady state (or on a Release PR) this ships a plugin whose own "
            "pinned validator contradicts its prose — finish the package release "
            "and bump VALIDATOR_PIN first (see root CLAUDE.md, issue #71).",
            file=sys.stderr,
        )
        return 1
    print(
        f"WARNING (release window, pin {pin_version} != shipped {shipped}): {verdict}\n"
        "Allowed while the new package version publishes; the pin bump follow-up "
        "must land before any plugin Release PR merges."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
