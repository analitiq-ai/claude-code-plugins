"""Rule registry — engine, registry integrity, and shared-fixture gate.

This is the drift-prevention linchpin that replaces the removed CUE layer: every
relational rule ships a corpus of valid/invalid instance fixtures, and this suite
asserts the registry-driven Pydantic enforcement agrees with them. A non-Python
re-implementation reconciles against the same JSON fixtures.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from analitiq.contracts.shared.introspect import closed_members, contract_classes

from analitiq.contracts import connection, connector, endpoints, stream, type_map
from analitiq.contracts.pipelines import config as pipeline_config
from analitiq.contracts.pipelines import data_sync
from analitiq.contracts.shared import common
from analitiq.contracts.shared.rules import all_rules
from analitiq.contracts.shared.rule_record import (
    DESCRIPTIVE_TIER,
    MECHANISMS,
    OWNERS,
    RETIRED_BEFORE_THE_REGISTRY,
    SEVERITIES,
    STRUCTURAL_TIER,
    TIERS,
    RuleRecord,
)


# tests/unit/<this file> -> parents[1] is tests/, which holds the fixtures.
# (In the infra repo this reached up to the repo root and back down through
# contract-models/; here the test already lives inside the package.)
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"

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
#: The rules the shared fixture corpus covers — the ones whose record names the
#: concrete model to validate a fixture against. A non-Python re-implementation
#: reconciles against the same JSON.
FIXTURED_RULES = [r for r in all_rules() if r.fixture_model]


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
    assert len(ids) == len(set(ids)), "duplicate rule ids"


def test_retired_rule_ids_are_not_reissued():
    """Uniqueness alone misses this — a retired id is free by definition."""
    live = {r.id for r in all_rules()}
    reissued = sorted(set(RETIRED_BEFORE_THE_REGISTRY) & live)
    assert not reissued, (
        f"retired rule ids reissued: {reissued}. These appear in archived "
        "findings; give the new rule the next free number instead."
    )


def test_every_target_resolves_to_a_model():
    for rule in all_rules():
        for target in rule.targets:
            assert target in MODEL_INDEX, f"{rule.id}: unknown target class {target!r}"


def _enforcer_name(rule) -> str:
    """The bare member name a rule's `validator` binding ends in."""
    return rule.validator_symbol.split(".")[-1]


def _carries(cls, member: str) -> bool:
    """Whether a model carries the member a binding names.

    Either half counts, because both halves enforce: a model validator is a
    class attribute, and the `Literal`/pattern/bound a structural rule rides on
    is a pydantic field, which is not.
    """
    return hasattr(cls, member) or member in getattr(cls, "model_fields", {})


def test_bound_rules_name_a_real_enforcer():
    """A rule's enforcer must exist on EVERY target — it applies to each.

    `render_rules.py` resolves the `validator` binding, but only against the
    one symbol it names. A rule bound to several targets applies to all of
    them, so a member present on the named class and missing on a sibling is a
    rule that silently does not fire for half its targets.
    """
    for rule in all_rules():
        # A binding naming a bare class says the shape IS the class — a
        # discriminated-union branch, say — so there is no member to find on
        # its siblings. `render_rules.py` resolves that the class exists.
        if not rule.validator_symbol or "." not in rule.validator_symbol:
            continue
        enforcer = _enforcer_name(rule)
        missing = [t for t in rule.targets if not _carries(MODEL_INDEX[t], enforcer)]
        assert not missing, f"{rule.id}: enforcer {enforcer!r} missing on {missing}"


def _declared_validators() -> set[tuple[str, str, str]]:
    """Every validator pydantic runs on a contract model, by (module, class, name).

    Both decorator dicts, because both reject. Only `model_validators` was
    walked once, so `@field_validator` was a third door into the same house —
    validators rejecting documents that no record named, and the sweep
    reporting a clean census over the half it could see. The two are the same
    kind of thing to an author: something says no, and the finding should name
    the rule it broke.

    The module is part of the key because a bare class name does not identify a
    class here: `TemplateExpression` is declared by both the connector and the
    endpoint module, and keyed by name alone the one carried the other's
    record — a validator counted as registered by a rule about a different
    document.
    """
    declared: set[tuple[str, str, str]] = set()
    for cls in contract_classes():
        decorators = cls.__pydantic_decorators__
        for group in (decorators.model_validators, decorators.field_validators):
            for name, dec in group.items():
                declared.add(
                    (
                        dec.func.__module__,
                        dec.func.__qualname__.rsplit(".", 2)[-2],
                        name,
                    )
                )
    return declared


#: Validator methods that are deliberately NOT registry rules. Keyed by
#: (defining module, class, method name); the value says why. Anything else
#: that raises on invalid input is a relational rule and belongs in the
#: registry. The module is part of the key for the same reason it is part of
#: the census key: a bare class name is not unique across contract modules, so
#: exempting one would exempt its namesake in the other document too.
EXEMPT_MODEL_VALIDATORS = {
    ("analitiq.contracts.connector", "ConnectorBase", "_inherit_transport_type"): (
        "normalizer: stamps the transport_type discriminator onto transports "
        "entries before union dispatch; its raises only re-shape "
        "container-type errors the structural tier reports anyway"
    ),
    ("analitiq.contracts.shared.common", "RetryErrorHandlingBase", "_default_retry_delay"): (
        "pure normalizer: injects the effective retry delay, never rejects"
    ),
    ("analitiq.contracts.endpoints", "_EndpointModel", "_reject_reserved_fields"): (
        "message-quality duplicate: the closed model (extra='forbid') already "
        "rejects the reserved keys; this validator only names the spec section"
    ),
    ("analitiq.contracts.pipelines.data_sync", "PipelineRunStatusData", "_iso8601"): (
        "pure normalizer: swaps a stored timestamp's space separator for `T` "
        "and passes a non-string straight through, so the declarative "
        "ISO_TS_PATTERN sees the normalized value and reports every rejection"
    ),
}


def test_every_model_validator_is_registered_or_exempt():
    """The enforcer→registry direction of the census.

    The registry's other tests all start from a registered rule, so a bespoke
    validator nobody registered was invisible — an enforced rule the census
    claimed not to exist. Every validator method on a contract model must be
    some rule's enforcer or carry an explicit exemption above. A rule accounts
    for one either way:

    * its `validator` names the defining module and class outright
      (`module::Owner.method`), which is the exact statement and needs no
      lookup; or
    * the enforcer name matches and the defining class sits in the MRO of a
      target declared in that same module, which is what lets one record cover
      a shared enforcer each document redeclares for itself, and what stops a
      same-named validator on an unrelated class from satisfying it.

    Neither half matches on a bare class name. `targets` binds by bare class
    name and two contract modules each declare a `TemplateExpression`, so
    neither is nameable there — and keyed that way, the connector's validator
    was accounted for by the endpoint's record: the failure this census exists
    to prevent, wearing the shape of a pass. The binding is a dotted path and
    says which module; a target resolves to a class that knows its own.

    The module bounds the second half rather than gating it, because a shared
    enforcer is deliberately cross-module — the record binds one module's copy
    and the identical method in four other documents is the same rule.

    The universe is the walked namespace package, not this module's hand-kept
    list, so a new contract module — and every same-named class, not just the
    last one a name-keyed index retains — is swept the moment it exists.
    """
    walked = contract_classes()
    by_name: dict[str, list[type[BaseModel]]] = {}
    for cls in walked:
        by_name.setdefault(cls.__name__, []).append(cls)
    unaccounted = []
    for module, owner, name in sorted(_declared_validators()):
        if (module, owner, name) in EXEMPT_MODEL_VALIDATORS:
            continue
        registered = any(
            (
                rule.validator_module == module
                and rule.validator_symbol == f"{owner}.{name}"
            )
            or (
                rule.validator_symbol
                and _enforcer_name(rule) == name
                and any(
                    cls.__module__ == module
                    and owner in (a.__name__ for a in cls.__mro__)
                    for t in rule.targets
                    for cls in by_name.get(t, ())
                )
            )
            for rule in all_rules()
        )
        if not registered:
            unaccounted.append(f"{module}::{owner}.{name}")
    assert not unaccounted, (
        "validators that are no rule's enforcer and not exempt — give each a "
        "record in rules/records/ naming it as the `validator` (then run "
        "`python3 scripts/render_rules.py write`), or add an explicit "
        f"exemption with its reason: {unaccounted}"
    )


def test_exemptions_name_live_validators():
    """The rot direction of the exemption table: an exemption whose validator
    is gone stays green forever, and silently exempts the next validator to
    reuse the (module, class, method) name."""
    walked = _declared_validators()
    stale = sorted(
        f"{module}::{owner}.{name}"
        for module, owner, name in EXEMPT_MODEL_VALIDATORS
        if (module, owner, name) not in walked
    )
    assert not stale, f"exemptions naming no live validator: {stale}"


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


# --- Tiers ------------------------------------------------------------------


def test_every_tier_is_populated():
    """Non-vacuity: an empty tier makes every guard below pass over nothing.

    The renderers grow a section per tier and the guards that follow all
    iterate one; a tier that fell to zero records would leave a heading with no
    rows and a green suite that checked nothing. If a tier genuinely empties,
    that is a deliberate retirement — delete the tier from the vocabulary, do
    not leave it standing.
    """
    populated = {r.tier for r in all_rules()}
    assert populated == set(TIERS), f"tiers with no rules: {sorted(set(TIERS) - populated)}"


def test_every_severity_is_used():
    """The same, for the cost axis.

    A severity nobody assigns is a distinction the registry claims to draw and
    does not — and `error` swallowing everything is exactly how a severity
    field becomes decoration.
    """
    used = {r.severity for r in all_rules()}
    assert used == set(SEVERITIES), f"severities no rule uses: {sorted(set(SEVERITIES) - used)}"


def test_every_owner_owns_something():
    used = {o for r in all_rules() for o in r.owners}
    assert used == set(OWNERS), f"owners nothing is assigned to: {sorted(set(OWNERS) - used)}"


def test_every_symbol_rule_resolves_to_the_shape_its_mechanism_needs():
    """A `mechanism: pattern` or `mechanism: reserved_names` rule resolves to a
    live constant of the right shape, or renders none.

    The reference prints that constant so an author is shown the form the
    statement points at. A record naming one that moved must fail here rather
    than at the renderer, and a record naming none renders `—` — which is the
    honest answer, not a defect, so this only grades the ones that do. The
    shape check is per mechanism: a `pattern` rule points at a regex string, a
    `reserved_names` rule at a frozenset of forbidden literal strings — mixing
    them up would render silently wrong (a regex printed as a member list, or
    a set's repr printed as if it were a pattern).
    """
    import importlib

    for rule in all_rules():
        if not rule.symbol:
            continue
        assert rule.mechanism in ("pattern", "reserved_names"), (
            f"{rule.id}: symbol on a {rule.mechanism!r} rule"
        )
        module_name, _, name = rule.symbol.partition("::")
        module = importlib.import_module(module_name)
        assert hasattr(module, name), (
            f"{rule.id}: symbol {rule.symbol!r} resolves to no "
            f"name in {module_name}"
        )
        value = getattr(module, name)
        if rule.mechanism == "pattern":
            assert isinstance(value, str), (
                f"{rule.id}: {rule.symbol!r} is not a regex string"
            )
        else:
            assert isinstance(value, frozenset) and value, (
                f"{rule.id}: {rule.symbol!r} is not a non-empty frozenset"
            )


def test_every_mechanism_names_a_rule():
    """The same, for the shape-device axis.

    Only `literal_enum` is read — it is what makes the rendered reference print
    a rule's members off the live model — so the rest earn their place by
    being the answer some author chose instead. A member no record takes is a
    distinction offered and never used, which is the shape of a vocabulary kept
    ready rather than one in service.
    """
    used = {r.mechanism for r in all_rules() if r.mechanism}
    assert used == set(MECHANISMS), (
        f"mechanisms no rule takes: {sorted(set(MECHANISMS) - used)}"
    )


def test_descriptive_prose_cannot_take_a_rule_id():
    """The sixth tier name is there so it can be refused, not stored.

    Prose stating no obligation has nothing to comply with, so an id minted for
    it resolves to advice. `RuleRecord` says no at construction; this pins that
    it still says WHY, because an author who reaches for `descriptive` needs to
    learn the sentence stays prose rather than that the word is misspelled.
    """
    with pytest.raises(ValueError) as exc:
        RuleRecord(
            id="RULE-TEST-001",
            statement="A connector MUST be versioned by git tag.",
            tier=DESCRIPTIVE_TIER,
            severity="info",
            scopes=("connector",),
            rationale="—",
            owners=("connector-plugin",),
        )
    assert "no obligation" in str(exc.value)


def _field_head(expr: str) -> str:
    """The plain model attribute a field expression starts from."""
    return expr.split("[]")[0].split(".")[0]


def test_structural_fields_resolve_on_their_model():
    """A structural entry's claim is checkable, or it is a comment.

    The entry says "this model's shape carries the rule"; the only thing that
    makes that a claim rather than a gesture is that the named field still
    exists. Renaming the field then fails here instead of leaving prose citing
    an id whose mechanism moved.
    """
    unresolved = []
    for rule in all_rules():
        if rule.tier != STRUCTURAL_TIER:
            continue
        for target in rule.targets:
            model = MODEL_INDEX[target]
            for expr in rule.fields:
                head = _field_head(expr)
                if head not in model.model_fields:
                    unresolved.append(f"{rule.id}: {target} has no field {head!r}")
    assert not unresolved, unresolved


def test_every_bound_vocabulary_field_yields_its_members():
    """A field a `literal_enum` rule binds must read back a non-empty set.

    Both consumers of `closed_members` fail *quietly* on a field it cannot
    read: the rendered reference prints no members for it, and the
    no-restatement check below compares a statement against an empty set and
    passes. Reading only `Literal` did exactly that to every `Enum`-typed
    field, so a rule could name four vocabularies and publish two.

    An empty read is therefore a defect whichever way it happens — a new way of
    spelling a closed set, or a rule pointing at a field that carries none.
    """
    silent = []
    for rule in all_rules():
        if rule.mechanism != "literal_enum":
            continue
        for target in rule.targets:
            model = MODEL_INDEX[target]
            for expr in rule.fields:
                info = model.model_fields.get(_field_head(expr))
                if info is None:
                    continue  # test_structural_fields_resolve_on_their_model owns this
                if not closed_members(info.annotation):
                    silent.append(f"{rule.id}: {target}.{_field_head(expr)}")
    assert not silent, (
        f"bound as a closed vocabulary but reading back empty: {silent}. Either "
        "the field does not carry one, or `closed_members` cannot see how it is "
        "spelled — both publish a rule with a vocabulary missing."
    )


def _vocabulary_bindings() -> set[tuple[str, str]]:
    """Every ``(target, field)`` some `literal_enum` rule binds."""
    return {
        (target, _field_head(expr))
        for rule in all_rules()
        if rule.mechanism == "literal_enum"
        for target in rule.targets
        for expr in rule.fields
    }


def test_every_multi_member_vocabulary_is_bound_by_a_rule():
    """The registry's admission test, in the direction that was missing.

    Two ways the contract carries an obligation, and only one of them was
    obliged to register. A rule applied by imperative code is caught by
    `test_every_model_validator_is_registered_or_exempt` above and by the
    validator package's check census: write a `@model_validator`, register it or
    exempt it. A rule applied by an *annotation* — a closed vocabulary on a
    field — was under no such obligation, so the registry held every
    code-enforced rule plus whichever annotation-carried ones somebody had
    wanted to cite. Two records read identically while differing in whether
    their absence would have failed anything.

    That asymmetry is what let a vocabulary reach plugin prose as a hand-typed
    list. Prose may cite a rule id and let `render_rule_reference.py` print the
    members off the live model; where no id existed there was nothing to cite,
    and the members were retyped.

    So every closed set an author picks a value from carries an id. The rule
    then owns the vocabulary in one place, and the prose that teaches it cites
    that id instead of copying the members
    (`.claude/rules/no-drift-surfaces.md`).

    **Multi-member only, and that threshold is the rule's substance.** A
    single-member `Literal` — `AdbcTransport.transport_type` is `adbc`,
    `ApiKeyAuth.type` is `api_key` — is a discriminator tag or a constant stamp.
    There is no set to pick from and nothing prose can copy wrong: naming the
    value IS naming the branch, and the obligation that governs it is the
    discriminated union the tag selects, which registers on its own terms.
    Demanding an id per tag would mint a record whose statement could only
    restate the annotation. `contract_vocabularies` deliberately leaves this
    call to its callers; this is the caller making it.

    Bindings match through the MRO, the way `targets` is defined to: a record
    naming `StreamAuthored.status` covers `StreamInput`, which inherits the
    field unchanged. A subclass that *redeclares* the field with a different set
    is a different vocabulary and needs its own binding — which it gets, because
    the members are read off the subclass.
    """
    bound = _vocabulary_bindings()
    unbound = []
    for cls in contract_classes():
        for name, info in cls.model_fields.items():
            if len(closed_members(info.annotation)) < 2:
                continue
            if any((base.__name__, name) in bound for base in cls.__mro__):
                continue
            unbound.append(f"{cls.__name__}.{name}")
    assert not unbound, (
        "closed vocabularies no rule binds: "
        f"{sorted(set(unbound))}. Each is a member list prose can only restate "
        "by hand. Add a record with `mechanism: literal_enum` naming the model "
        "in `targets` and the field in `fields` (rules/SCHEMA.md), so the "
        "rendered reference prints the members off the live model and prose "
        "cites the id."
    )


def test_structural_rules_do_not_restate_the_values_they_point_at():
    """The tier exists to STOP the copy, so an entry must not become one.

    A structural entry names where a value list lives; the renderer reads the
    members off the live model. Spelling them into ``prose`` too would recreate
    the drift surface one layer down, where nothing regenerates it.

    Two members is the threshold, not one: a lone member can be ordinary
    English in a sentence about the field (an ``ssl_mode`` rule may say "none"),
    while two whole-word hits in one sentence is an enumeration.
    """
    leaked = []
    for rule in all_rules():
        if rule.tier != STRUCTURAL_TIER or rule.mechanism != "literal_enum":
            continue
        for target in rule.targets:
            model = MODEL_INDEX[target]
            for expr in rule.fields:
                info = model.model_fields.get(_field_head(expr))
                if info is None:
                    continue  # test_structural_fields_resolve_on_their_model owns this
                hits = sorted(
                    m
                    for m in set(closed_members(info.annotation))
                    if re.search(rf"\b{re.escape(m)}\b", rule.statement)
                )
                if len(hits) >= 2:
                    leaked.append(f"{rule.id}: statement enumerates {hits} — say what the "
                                  f"member list is FOR; {target}.{_field_head(expr)} carries it")
    assert not leaked, leaked


# --- Shared fixture corpus --------------------------------------------------


def test_every_fixtured_rule_has_fixtures():
    """Fail closed: a rule naming a fixture_model carries >=2 valid and >=2 invalid."""
    for rule in FIXTURED_RULES:
        for group, minimum in (("valid", 2), ("invalid", 2)):
            found = list((FIXTURES_DIR / rule.id / group).glob("*.json"))
            assert len(found) >= minimum, (
                f"{rule.id}: {len(found)} {group} fixtures, need >= {minimum}"
            )


def test_no_orphan_fixture_directories():
    """The other direction: a corpus directory belongs to a rule that claims it.

    Without this, dropping `fixture_model` from a record would quietly retire
    its fixtures — the rule leaves the corpus and the files stay on disk
    exercising nothing.
    """
    claimed = {r.id for r in FIXTURED_RULES}
    for rule_dir in FIXTURES_DIR.glob("*"):
        if rule_dir.is_dir():
            assert rule_dir.name in claimed, (
                f"fixtures for {rule_dir.name!r}, which names no fixture_model"
            )


@pytest.mark.parametrize("rule_id, group, path", list(_iter_fixtures()))
def test_fixture_matches_enforcement(rule_id, group, path):
    rule = RULES[rule_id]
    model = MODEL_INDEX[rule.fixture_model]
    payload = json.loads(path.read_text())
    if group == "valid":
        model.model_validate(payload)  # must not raise
    else:
        with pytest.raises(ValidationError) as exc:
            model.model_validate(payload)
        # Unconditional: a fixture proves the rule only if this rule is what
        # rejected it. Some other constraint failing first would pass a bare
        # `raises`, and the corpus would certify a rule nothing enforces.
        # `violation()` puts the id in the message, so every enforcer can.
        assert rule_id in str(exc.value), (
            f"{rule_id} invalid fixture rejected, but not by this rule"
        )
