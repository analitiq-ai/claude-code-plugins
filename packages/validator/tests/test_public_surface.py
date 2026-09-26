"""The package root is the validator's declared public surface: `__all__`.

A consumer imports from `analitiq.validator`, so any other name bound there
reads as supported whatever `__all__` says. Internals are reached through the
module that defines them.
"""
import inspect


def test_the_package_root_binds_only_its_declared_surface(validator):
    bound = {name for name, value in vars(validator).items()
             if not (name.startswith("__") or inspect.ismodule(value))}
    assert bound == set(validator.__all__)


def test_the_declared_surface_is_the_request_api_and_its_verdict(validator):
    """The entry points taking the published request models, the verdict and
    finding shapes they answer with, and what builds and judges a finding.
    A per-document check is reached through a request, never called alone."""
    assert set(validator.__all__) == {
        "validate_single_document", "validate_package", "validate_workspace",
        "ValidationEnvelope", "Finding", "finding", "finding_costs_a_pass",
    }
