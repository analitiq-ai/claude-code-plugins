"""The validator and contract-models ship as one release unit.

`analitiq-validator` renders from the same authored model layer as
`analitiq-contract-models` and validates through it, so a version skew between
the two is a real defect. The two are kept in sync on BOTH fronts:
  - the validator's own `version` equals contract-models' `version`, and
  - the validator pins contract-models with an EXACT `==` at that same version.
This test is the CI equality gate — it fails the build if either diverges.

"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PYPROJECT = REPO_ROOT / "validator" / "pyproject.toml"
CONTRACT_MODELS_PYPROJECT = REPO_ROOT / "contract-models" / "pyproject.toml"


def _project(pyproject: Path) -> dict:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]


def _project_version(pyproject: Path) -> str:
    version = _project(pyproject).get("version")
    assert isinstance(version, str), f"no [project].version found in {pyproject}"
    return version


def _validator_contract_models_specifier() -> str:
    """The specifier the validator pins contract-models with.

    Read out of `[project].dependencies`, so the backticked mention in the
    package description is not a place this can find one.
    """
    matches = [
        dep[len("analitiq-contract-models"):].strip()
        for dep in _project(VALIDATOR_PYPROJECT).get("dependencies", [])
        if dep.replace(" ", "").startswith("analitiq-contract-models")
    ]
    assert len(matches) == 1, (
        f"expected exactly one analitiq-contract-models dependency, got {matches!r}"
    )
    return matches[0]


def test_validator_pins_contract_models_exactly():
    spec = _validator_contract_models_specifier()
    m = re.fullmatch(r"==(?P<v>[^\s,;]+)", spec)
    assert m, (
        "validator must pin analitiq-contract-models with an exact '==' so the "
        f"two packages ship in sync; got specifier {spec!r}"
    )
    assert m.group("v") == _project_version(CONTRACT_MODELS_PYPROJECT), (
        f"validator pins analitiq-contract-models=={m.group('v')} but "
        f"contract-models/pyproject.toml is {_project_version(CONTRACT_MODELS_PYPROJECT)!r} — "
        "bump both together so the pin stays in sync."
    )


def test_validator_version_matches_contract_models_version():
    validator_v = _project_version(VALIDATOR_PYPROJECT)
    contract_models_v = _project_version(CONTRACT_MODELS_PYPROJECT)
    assert validator_v == contract_models_v, (
        f"analitiq-validator is {validator_v!r} but analitiq-contract-models is "
        f"{contract_models_v!r} — the two ship as one release unit and their own "
        "versions must stay equal; bump both together."
    )
