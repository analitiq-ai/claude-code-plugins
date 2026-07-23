"""Pin the wiring of scripts/check_validator_pin_contract.py.

The guard script runs OUTSIDE pytest (a CI job installing the published
VALIDATOR_PIN into an isolated venv — see its module docstring), so nothing in
the ordinary suite would notice its regex extraction rotting against the files
it reads. These tests exercise exactly that wiring against the working tree —
no venv, no network, no wheel install — plus the one semantic invariant the
guard's design rests on: the canon it extracts from prose must be accepted by
the IN-REPO contract, so a guard failure can only ever mean the PUBLISHED
wheel lags the prose, never that the prose itself is wrong.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_validator_pin_contract.py"


@pytest.fixture(scope="module")
def guard():
    spec = spec_from_file_location("check_validator_pin_contract", _SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reads_the_pin_from_its_single_source(guard):
    # `_analitiq.py` owns the pin; the guard references it by regex. If that
    # file is refactored so the regex misses, this fails here instead of the
    # guard erroring in CI.
    pin = guard.read_pin()
    assert pin.startswith("analitiq-validator==")
    assert guard.read_pin_version() == pin.split("==", 1)[1] != ""


def test_reads_the_shipped_version(guard):
    from _pins import PINNED_VERSION

    # Same value `_pins` tracks (packages/contract-models/pyproject.toml moves
    # in lockstep with the validator's — test_contract_models_pin.py enforces
    # that), read from the validator side by the guard's own regex.
    assert guard.read_shipped_version() == PINNED_VERSION


def test_extracts_the_canonical_drivers_from_prose(guard):
    drivers = guard.read_canonical_drivers()
    # The extraction hard-fails on an empty result by design; also require the
    # driver this guard exists for (the sync canonical path, issue #71) so a
    # prose restructure cannot silently drop it from coverage.
    assert "redshift+redshift_connector" in drivers


def test_canon_is_accepted_by_the_in_repo_contract(guard):
    # The guard's verdict semantics assume prose canon ⊆ contract: then a
    # rejection can only mean the published wheel is behind. Prove the
    # assumption against the in-repo source the rest of the suite grades.
    from analitiq.contracts.connector import SqlAlchemyTransport

    for value in guard.read_canonical_drivers():
        SqlAlchemyTransport.model_validate(
            {"transport_type": "sqlalchemy", "driver": value}
        )
