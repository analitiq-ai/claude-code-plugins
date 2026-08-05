"""Advisory rule registry — engine, registry integrity, and shared-fixture gate.

This is the drift-prevention linchpin that replaces the removed CUE layer: every
relational rule ships a corpus of valid/invalid instance fixtures, and this suite
asserts the registry-driven Pydantic enforcement agrees with them. A non-Python
re-implementation reconciles against the same JSON fixtures.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from analitiq.contracts.shared.introspect import contract_classes

from analitiq.contracts import connection, connector, endpoints, stream, type_map
from analitiq.contracts.pipelines import config as pipeline_config
from analitiq.contracts.pipelines import data_sync
from analitiq.contracts.shared import common
from analitiq.contracts.shared.advisory import (
    CUSTOM_KIND,
    GENERIC_KINDS,
    AdvisoryValidated,
    all_rules,
)


# tests/unit/<this file> -> parents[1] is tests/, which holds the fixtures.
# (In the infra repo this reached up to the repo root and back down through
# contract-models/; here the test already lives inside the package.)
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "advisory"

_MODULES = (
    connection, connector, endpoints, stream, type_map,
    pipeline_config, data_sync, common,
)


def _model_index() -> dict[str, type[BaseModel]]:
    """Map every model class name reachable from the contract modules to the class."""
    index: dict[str, type[BaseModel]] = {}
    for module in _MODULES:
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel):
                index[obj.__name__] = obj
    return index


MODEL_INDEX = _model_index()
RULES = {r.id: r for r in all_rules()}
GENERIC_RULES = [r for r in all_rules() if r.kind in GENERIC_KINDS]


def _iter_fixtures():
    for rule_dir in sorted(FIXTURES_DIR.glob("*")):
        if not rule_dir.is_dir():
            continue
        for group in ("valid", "invalid"):
            for path in sorted((rule_dir / group).glob("*.json")):
                yield pytest.param(rule_dir.name, group, path, id=f"{rule_dir.name}/{group}/{path.stem}")


# --- Registry integrity -----------------------------------------------------


def test_rule_ids_are_unique():
    ids = [r.id for r in all_rules()]
    assert len(ids) == len(set(ids)), "duplicate advisory rule ids"


#: Ids that have been retired and must never be reissued. Advisory ids appear in
#: user-facing findings and in archived diagnostics, so reusing one silently
#: re-points every stored occurrence at a different rule. Uniqueness alone does
#: not catch this — a retired id is free by definition.
RETIRED_RULE_IDS = {
    # ADV-STRM-008 ("exactly one of expression or constant"), retired in
    # 1.0.0rc19: `AssignmentValue` became a `kind`-discriminated union, so the
    # union states the rule and no validator enforces it.
    "ADV-STRM-008",
}


def test_retired_rule_ids_are_not_reissued():
    live = {r.id for r in all_rules()}
    reissued = sorted(RETIRED_RULE_IDS & live)
    assert not reissued, (
        f"retired advisory ids reissued: {reissued}. These appear in archived "
        "findings; give the new rule the next free number instead."
    )


def test_every_target_resolves_to_a_model():
    for rule in all_rules():
        for target in rule.targets:
            assert target in MODEL_INDEX, f"{rule.id}: unknown target class {target!r}"


def test_generic_targets_use_the_mixin():
    """A generic rule only fires if its models inherit the advisory mixin."""
    for rule in GENERIC_RULES:
        for target in rule.targets:
            cls = MODEL_INDEX[target]
            assert issubclass(cls, AdvisoryValidated), (
                f"{rule.id}: {target} does not inherit AdvisoryValidated, so the rule never runs"
            )


def test_custom_rules_name_a_real_enforcer():
    """A custom rule's enforcer must exist on EVERY target — it runs on each."""
    for rule in all_rules():
        if rule.kind != CUSTOM_KIND:
            continue
        missing = [t for t in rule.targets if not hasattr(MODEL_INDEX[t], rule.enforcer)]
        assert not missing, (
            f"{rule.id}: enforcer {rule.enforcer!r} missing on {missing}"
        )


#: @model_validator methods that are deliberately NOT registry rules. Keyed by
#: (defining class, method name); the value says why. Anything else that
#: raises on invalid input is a relational rule and belongs in the registry.
EXEMPT_MODEL_VALIDATORS = {
    ("ConnectorBase", "_inherit_transport_type"): (
        "normalizer: stamps the transport_type discriminator onto transports "
        "entries before union dispatch; its raises only re-shape "
        "container-type errors the structural tier reports anyway"
    ),
    ("RetryErrorHandlingBase", "_default_retry_delay"): (
        "pure normalizer: injects the effective retry delay, never rejects"
    ),
    ("AdvisoryValidated", "_run_advisory_rules"): "the registry runner itself",
    ("_EndpointModel", "_reject_reserved_fields"): (
        "message-quality duplicate: the closed model (extra='forbid') already "
        "rejects the reserved keys; this validator only names the spec section"
    ),
}


def test_every_model_validator_is_registered_or_exempt():
    """The enforcer→registry direction of the census.

    The registry's other tests all start from a registered rule, so a bespoke
    @model_validator nobody registered was invisible — an enforced rule the
    census claimed not to exist. Every model validator on a contract model must
    be some rule's enforcer (target-aware: the defining class must sit in a
    target's MRO, so a same-named validator on an unrelated class does not
    satisfy it) or carry an explicit exemption above. The universe is the
    walked namespace package, not this module's hand-kept list, so a new
    contract module — and every same-named class, not just the last one a
    name-keyed index retains — is swept the moment it exists.
    """
    walked = contract_classes()
    by_name: dict[str, list[type[BaseModel]]] = {}
    for cls in walked:
        by_name.setdefault(cls.__name__, []).append(cls)
    validators = set()
    for cls in walked:
        for name, dec in cls.__pydantic_decorators__.model_validators.items():
            owner = dec.func.__qualname__.rsplit(".", 2)[-2]
            validators.add((owner, name))
    unaccounted = []
    for owner, name in sorted(validators):
        if (owner, name) in EXEMPT_MODEL_VALIDATORS:
            continue
        registered = any(
            rule.enforcer == name
            and any(
                owner in (a.__name__ for a in cls.__mro__)
                for t in rule.targets
                for cls in by_name.get(t, ())
            )
            for rule in all_rules()
        )
        if not registered:
            unaccounted.append(f"{owner}.{name}")
    assert not unaccounted, (
        "model validators that are no rule's enforcer and not exempt — "
        "register each in advisory_rules.py or add an explicit exemption "
        f"with its reason: {unaccounted}"
    )


def test_exemptions_name_live_validators():
    """The rot direction of the exemption table: an exemption whose validator
    is gone stays green forever, and silently exempts the next validator to
    reuse the (class, method) name."""
    walked = {
        (dec.func.__qualname__.rsplit(".", 2)[-2], name)
        for cls in contract_classes()
        for name, dec in cls.__pydantic_decorators__.model_validators.items()
    }
    stale = sorted(
        f"{owner}.{name}"
        for owner, name in EXEMPT_MODEL_VALIDATORS
        if (owner, name) not in walked
    )
    assert not stale, f"exemptions naming no live model validator: {stale}"


def test_rule_targets_are_unambiguous_class_names():
    """Rules bind targets (and fixture models) by bare class name; two
    contract classes sharing a bound name would make every name-based
    resolution in this suite silently pick one of them. Unbound name
    collisions are tolerated — this pins only the names the registry uses."""
    by_name: dict[str, set[type[BaseModel]]] = {}
    for cls in contract_classes():
        by_name.setdefault(cls.__name__, set()).add(cls)
    bound = {t for r in all_rules() for t in r.targets}
    bound |= {r.fixture_model for r in all_rules() if r.fixture_model}
    ambiguous = {
        name: sorted(c.__module__ for c in by_name[name])
        for name in sorted(bound)
        if len(by_name.get(name, set())) > 1
    }
    assert not ambiguous, (
        f"registry-bound class names resolved by more than one class: {ambiguous}"
    )


# --- Shared fixture corpus --------------------------------------------------


def test_every_generic_rule_has_fixtures():
    """Fail closed: each generic rule carries >=2 valid and >=2 invalid fixtures."""
    for rule in GENERIC_RULES:
        for group, minimum in (("valid", 2), ("invalid", 2)):
            found = list((FIXTURES_DIR / rule.id / group).glob("*.json"))
            assert len(found) >= minimum, (
                f"{rule.id}: {len(found)} {group} fixtures, need >= {minimum}"
            )


def test_no_orphan_fixture_directories():
    for rule_dir in FIXTURES_DIR.glob("*"):
        if rule_dir.is_dir():
            assert rule_dir.name in RULES, f"fixtures for unknown rule {rule_dir.name!r}"


@pytest.mark.parametrize("rule_id, group, path", list(_iter_fixtures()))
def test_fixture_matches_enforcement(rule_id, group, path):
    rule = RULES[rule_id]
    model = MODEL_INDEX[rule.fixture_target]
    payload = json.loads(path.read_text())
    if group == "valid":
        model.model_validate(payload)  # must not raise
    else:
        with pytest.raises(ValidationError) as exc:
            model.model_validate(payload)
        if rule.kind in GENERIC_KINDS:
            assert rule_id in str(exc.value), (
                f"{rule_id} invalid fixture rejected, but not by this rule"
            )
