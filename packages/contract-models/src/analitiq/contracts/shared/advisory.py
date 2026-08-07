"""The rule registry — one id scheme, one census, three enforceable tiers.

Every obligation this repo states takes exactly one of the four dispositions
:mod:`advisory_prose` names. Three of them are rules and are registered here;
the fourth, ``descriptive``, is the *absence* of a rule and is refused
admission on purpose (see :data:`DESCRIPTIVE_TIER`).

**relational** — the tier this registry started as, and the only one the
validator enforces. Set-equality, disjointness, membership, cross-key
uniqueness between sibling instance values: constraints stock JSON Schema
cannot express. Rather than scatter them across opaque `@model_validator`
bodies (and hand-copy them into a non-Python engine), they are authored ONCE as
structured data in ``advisory_rules.py`` and enforced HERE by one generic
validator. That drives two outputs, in sync by construction:

1. runtime enforcement — the :class:`AdvisoryValidated` mixin runs a model's
   registered rules on every ``model_validate``;
2. a stable ``id`` per rule keys, for the **generic** kinds, a valid/invalid
   instance fixture corpus (``contract-models/tests/fixtures/advisory``) a
   non-Python second system can re-implement the fixed rule *kinds* against and
   reconcile. ``kind="custom"`` rules are enforced only in-process by their
   named validator (they may carry no fixtures), so their logic is not portable
   to a non-Python engine — the registry entry keeps the census complete, not
   re-implementable.

A rule whose logic is irreducibly bespoke (recursive schema walks, hash
equality, path resolution) is still *catalogued* here with ``kind="custom"`` and
an ``enforcer`` naming the method that keeps enforcing it — so the census stays
complete without faking generality.

**structural** — carried by the model's own shape (a ``Literal``, a pattern, a
bound, a required field, a closed object, a discriminated union) and rendered
into the published JSON Schema. Enforced by anyone holding the schema, and
**anonymous**: the rejection message names the field, never a rule. A
structural entry exists so prose can cite an id instead of hand-copying the
shape, and it therefore states the obligation *in kind* and never its values —
``mechanism`` plus ``targets``/``fields`` name where the values live, and the
renderer reads them off the live model.

**waiver** — normative, and nothing in this repo can check it: it governs a
file no contract model sees (a connector's ``connector.py``), the engine's own
runtime conduct, an authoring choice among options that all validate, or a
relation to an artifact outside the document. The entry declares *which* of
those (:data:`WAIVED_SURFACES`) and *why*, so "unenforced" is a reviewable
state rather than an absence nobody can count.

This module imports no contract models: rules bind to their target classes by
*name*, so the data can be imported by tooling without pulling in pydantic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from typing import Any, Callable

from pydantic import model_validator

# --- Tiers ------------------------------------------------------------------

RELATIONAL_TIER = "relational"
STRUCTURAL_TIER = "structural"
WAIVER_TIER = "waiver"

#: The tiers the registry admits. ``relational`` is the default because it is
#: the only tier the validator runs; the other two are declared explicitly, so
#: an entry that is not enforced in-process says so at the entry rather than by
#: the reader inferring it from a missing ``enforcer``.
TIERS = (RELATIONAL_TIER, STRUCTURAL_TIER, WAIVER_TIER)

#: The fourth disposition, named here and refused admission. Prose that states
#: no obligation an instance could violate has no rule to register: minting an
#: id for it would make the registry a second home for explanation, and every
#: reader who resolved the id would find nothing to comply with. Such a
#: sentence stays prose and is dispositioned in the prose census instead.
DESCRIPTIVE_TIER = "descriptive"


# --- Rule kinds -------------------------------------------------------------

#: The fixed, generically-checkable relational vocabulary. Each maps to exactly
#: one checker in ``_CHECKERS``. ``custom`` is the escape hatch: catalogued only,
#: enforced by the method named in ``AdvisoryRule.enforcer``.
GENERIC_KINDS = (
    "disjoint",
    "set_equal",
    "member_of",
    "subset_of",
    "unique_by",
)
CUSTOM_KIND = "custom"

#: The shape devices a ``structural`` entry may name, matching the mechanisms
#: :mod:`advisory_prose` lets a prose site claim. Closed, so "the model carries
#: it" is a specific claim a reviewer can check against the field, not a
#: gesture at pydantic.
STRUCTURAL_MECHANISMS = (
    "literal_enum",
    "pattern",
    "bound",
    "default",
    "required_field",
    "field_type",
    "field_validator",
    "closed_object",
    "discriminated_union",
)

#: Mechanisms that describe the model rather than one of its fields, and so are
#: the only ones that may omit ``fields``.
_MODEL_LEVEL_MECHANISMS = ("closed_object", "discriminated_union")

#: What a waived rule governs — the thing someone would have to read to check
#: it, and therefore the reason this repo cannot. Closed, so the unenforced
#: surface stays countable by category: a value outside this list is a new
#: *class* of thing the plugins state rules about, which is a deliberate
#: addition rather than a free-text field.
WAIVED_SURFACES = (
    # Files in a connector's own Python package — connector.py, pyproject.toml,
    # requirements.txt, the layout. No contract model ever sees them; registry
    # CI and the CDK conformance kit are what enforce them.
    "connector-package",
    # The engine's or CDK's behaviour at configure or run time, not a checkable
    # shape of any document.
    "engine-runtime",
    # Several authorings all validate; which is correct is judgment. No
    # validator can flag a legal-but-wrong choice.
    "authoring-choice",
    # A relation between this document and a file or name outside it.
    "cross-artifact",
)


# --- Rule datum -------------------------------------------------------------


@dataclass(frozen=True)
class AdvisoryRule:
    """One contract rule, authored as data, in one of the registry's tiers.

    Common to every tier: ``id`` is stable and never reused, ``resource`` is the
    document (or artifact) family it constrains, and ``prose`` is the normative
    one-liner — self-contained, and never a copy of a value the model owns.

    ``targets`` are the model class names the rule binds to (matched against the
    whole MRO, so a rule on a base class covers its subclasses).

    The rest is tier-specific, and :meth:`__post_init__` refuses any mixture:

    - ``relational`` — ``kind`` selects a checker in ``_CHECKERS`` (or is
      ``custom``, with ``enforcer`` naming the bespoke method); ``fields`` are
      *field expressions* (see :func:`resolve`) whose meaning is kind-specific
      and documented on each checker; ``options`` carries kind-specific knobs
      (e.g. ``case_insensitive``, ``key``).
    - ``structural`` — ``mechanism`` names the shape device carrying it and
      ``fields`` name the plain model fields it lives on (a field-level
      mechanism requires them; a model-level one does not). No checker runs:
      the published schema is the enforcement.
    - ``waiver`` — ``governs`` names the surface (:data:`WAIVED_SURFACES`) and
      ``waiver`` says why that surface is beyond this repo's reach.
    """

    id: str
    resource: str
    prose: str
    tier: str = RELATIONAL_TIER
    kind: str | None = None
    targets: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    enforcer: str | None = None
    #: Concrete model class the shared fixtures validate against (defaults to the
    #: first target; set explicitly when the first target is an abstract base).
    fixture_model: str | None = None
    #: ``structural`` only: which of :data:`STRUCTURAL_MECHANISMS` carries it.
    mechanism: str | None = None
    #: ``waiver`` only: which of :data:`WAIVED_SURFACES` the rule governs.
    governs: str | None = None
    #: ``waiver`` only: why that surface is beyond this repo's reach.
    waiver: str | None = None

    def __post_init__(self) -> None:
        if self.tier == DESCRIPTIVE_TIER:
            raise ValueError(
                f"{self.id}: '{DESCRIPTIVE_TIER}' is the disposition of prose "
                "that states no obligation an instance could violate, so there "
                "is no rule to register. Leave the sentence where it is and "
                "disposition it in the prose census."
            )
        if self.tier not in TIERS:
            raise ValueError(
                f"{self.id}: unknown tier {self.tier!r}; expected one of {TIERS}"
            )
        if not self.prose.strip():
            raise ValueError(f"{self.id}: a rule with no prose states nothing")
        getattr(self, f"_check_{self.tier}")()

    def _reject(self, **unexpected: Any) -> None:
        """Refuse fields belonging to another tier — the entry is miscategorised."""
        set_here = sorted(name for name, value in unexpected.items() if value)
        if set_here:
            raise ValueError(
                f"{self.id}: {set_here} do not belong to a {self.tier!r} rule — "
                "either the field is wrong or the tier is"
            )

    def _check_relational(self) -> None:
        if not self.kind:
            raise ValueError(
                f"{self.id}: a relational rule must name the kind that checks it"
            )
        if self.kind == CUSTOM_KIND:
            if not self.enforcer:
                raise ValueError(f"{self.id}: custom rule must name an enforcer")
        elif self.kind not in GENERIC_KINDS:
            raise ValueError(f"{self.id}: unknown rule kind {self.kind!r}")
        if not self.targets:
            raise ValueError(f"{self.id}: rule must bind at least one target")
        self._reject(mechanism=self.mechanism, governs=self.governs, waiver=self.waiver)

    def _check_structural(self) -> None:
        if self.mechanism not in STRUCTURAL_MECHANISMS:
            raise ValueError(
                f"{self.id}: structural rule must name one of "
                f"{STRUCTURAL_MECHANISMS}, not {self.mechanism!r}"
            )
        if not self.targets:
            raise ValueError(
                f"{self.id}: structural rule must name the model whose shape "
                "carries it, so a rename of that model fails the build"
            )
        if not self.fields and self.mechanism not in _MODEL_LEVEL_MECHANISMS:
            raise ValueError(
                f"{self.id}: a {self.mechanism!r} rule lives on a field — name "
                f"it, or use one of {_MODEL_LEVEL_MECHANISMS}"
            )
        self._reject(
            kind=self.kind,
            enforcer=self.enforcer,
            options=self.options,
            governs=self.governs,
            waiver=self.waiver,
        )

    def _check_waiver(self) -> None:
        if self.governs not in WAIVED_SURFACES:
            raise ValueError(
                f"{self.id}: a waived rule must name one of {WAIVED_SURFACES}, "
                f"not {self.governs!r}"
            )
        if not (self.waiver or "").strip():
            raise ValueError(
                f"{self.id}: a waiver must say WHY the rule is beyond this "
                "repo's reach — an empty reason declares nothing"
            )
        self._reject(
            kind=self.kind,
            enforcer=self.enforcer,
            fields=self.fields,
            options=self.options,
            mechanism=self.mechanism,
        )

    @property
    def fixture_target(self) -> str:
        """The concrete model name the fixture corpus validates against."""
        return self.fixture_model or self.targets[0]


# --- Field-expression resolver ---------------------------------------------
#
# A deliberately small, closed grammar — enough for every relational rule, and
# no arbitrary code:
#   "enum"                        attribute (scalar or collection)
#   "ui.options"                  dotted attribute chain, None-safe
#   "destinations[]"             a list attribute (the elements)
#   "ui.options[].value"         project ``.value`` over each list element
# Exactly one ``[]`` segment is supported (all rules need at most one level).


def _get_path(obj: Any, path: str) -> Any:
    """Follow a dotted attribute chain, short-circuiting to None on any gap."""
    cur = obj
    for seg in path.split("."):
        if cur is None:
            return None
        cur = getattr(cur, seg, None)
    return cur


def resolve(model: Any, expr: str) -> Any:
    """Resolve a field expression against a model instance.

    Returns the attribute value, or — for a projection (``a[].b``) — a list of
    the projected values. Any missing/None link yields None so relational rules
    can uniformly skip absent operands.
    """
    if "[]" in expr:
        head, _, tail = expr.partition("[]")
        seq = _get_path(model, head.rstrip(".")) if head.strip(".") else model
        if seq is None:
            return None
        tail = tail.lstrip(".")
        return list(seq) if not tail else [_get_path(el, tail) for el in seq]
    return _get_path(model, expr)


def _as_set(value: Any, *, case_insensitive: bool = False) -> set | None:
    """Coerce an operand to a set of comparable members, or None to skip.

    A dict contributes its keys; a list/tuple its items. Empty and None both
    mean "operand absent" — every relational rule below relates two operands
    only when both are actually present, matching the imperative validators.
    """
    if not value:
        return None
    items = list(value.keys()) if isinstance(value, dict) else list(value)
    if case_insensitive:
        items = [m.lower() if isinstance(m, str) else m for m in items]
    return set(items)


# --- Generic checkers -------------------------------------------------------
#
# Each raises ValueError (pydantic wraps it) on violation, or returns quietly.
# The message always leads with the rule id so failures are greppable and the
# fixture corpus can assert on a stable token.


def _msg(rule: AdvisoryRule, detail: str) -> str:
    return f"[{rule.id}] {rule.prose} ({detail})"


def _check_disjoint(rule: AdvisoryRule, model: Any) -> None:
    """``fields = (a, b)``: the members of a and b must not overlap.

    Dict operands contribute keys. ``options.case_insensitive`` casefolds.
    """
    ci = rule.options.get("case_insensitive", False)
    a = _as_set(resolve(model, rule.fields[0]), case_insensitive=ci)
    b = _as_set(resolve(model, rule.fields[1]), case_insensitive=ci)
    if a is None or b is None:
        return
    overlap = sorted(a & b)
    if overlap:
        raise ValueError(_msg(rule, f"overlap={overlap!r}"))


def _check_set_equal(rule: AdvisoryRule, model: Any) -> None:
    """``fields = (a, b)``: a and b must contain exactly the same members.

    Compared element-wise (not via ``set``) so unhashable members — an ``enum``
    of objects/arrays, say — are tolerated exactly as the imperative validators
    were. Absent/empty operands skip; ``a`` is resolved first so ``b`` is never
    coerced when ``a`` is absent.
    """
    a = resolve(model, rule.fields[0])
    if not a:
        return
    b = resolve(model, rule.fields[1])
    if not b:
        return
    extra = [x for x in a if x not in b]
    missing = [x for x in b if x not in a]
    if extra or missing:
        raise ValueError(_msg(rule, f"extra={extra!r}; missing={missing!r}"))


def _check_member_of(rule: AdvisoryRule, model: Any) -> None:
    """``fields = (needle, haystack)``: needle must be a member of haystack.

    Membership is element-wise (``in`` over the collection), tolerating an
    unhashable needle or member as the imperative validator did.
    """
    needle = resolve(model, rule.fields[0])
    haystack = resolve(model, rule.fields[1])
    if needle is None or not haystack:
        return
    if needle not in haystack:
        raise ValueError(_msg(rule, f"value={needle!r} not in {list(haystack)!r}"))


def _check_subset_of(rule: AdvisoryRule, model: Any) -> None:
    """``fields = (sub, sup)``: every member of sub must appear in sup."""
    sub = _as_set(resolve(model, rule.fields[0]))
    sup = _as_set(resolve(model, rule.fields[1]))
    if sub is None or sup is None:
        return
    extra = sorted(sub - sup)
    if extra:
        raise ValueError(_msg(rule, f"not declared: {extra!r}"))


def find_duplicates(seq: Any, key: Callable[[Any], Any] | None = None) -> list:
    """Return the sorted, de-duplicated keys that appear more than once in seq.

    The one uniqueness primitive: the generic ``unique_by`` checker and the thin
    ``_check_unique_destinations`` shim both call it, so the algorithm is defined
    exactly once.
    """
    seen: set = set()
    dups: set = set()
    for el in seq or ():
        k = key(el) if key else el
        if k in seen:
            dups.add(k)
        else:
            seen.add(k)
    return sorted(dups)


def _check_unique_by(rule: AdvisoryRule, model: Any) -> None:
    """``fields = (seq,)``: elements of seq must be unique.

    ``options.key`` (list of dotted subpaths) projects each element to a tuple
    for the comparison; omit it to compare whole elements (scalar lists).
    ``options.skip_null`` drops elements whose key is (or contains) None from the
    comparison — for uniqueness over an optional field, where absent is not a
    collision.
    """
    seq = resolve(model, rule.fields[0])
    if not seq:
        return
    key_paths = rule.options.get("key")
    key_fn = (lambda el: tuple(_get_path(el, kp) for kp in key_paths)) if key_paths else None
    if rule.options.get("skip_null"):
        keys = [key_fn(el) if key_fn else el for el in seq]
        keys = [k for k in keys if k is not None and not (isinstance(k, tuple) and None in k)]
        dups = find_duplicates(keys)
    else:
        dups = find_duplicates(seq, key_fn)
    if dups:
        raise ValueError(_msg(rule, f"duplicates={dups!r}"))


_CHECKERS: dict[str, Callable[[AdvisoryRule, Any], None]] = {
    "disjoint": _check_disjoint,
    "set_equal": _check_set_equal,
    "member_of": _check_member_of,
    "subset_of": _check_subset_of,
    "unique_by": _check_unique_by,
}


def check_rule(rule: AdvisoryRule, model: Any) -> None:
    """Enforce one rule against a model instance.

    A no-op for every tier but ``relational``, and within it for ``custom``
    rules, whose named enforcer runs as its own ``@model_validator``. The
    structural tier is already enforced by the shape the model was built from,
    and the waiver tier is enforced nowhere — which is what the entry declares.
    """
    if rule.tier != RELATIONAL_TIER:
        return
    checker = _CHECKERS.get(rule.kind)
    if checker is not None:
        checker(rule, model)


# --- Registry + mixin -------------------------------------------------------

_RULES_BY_TARGET: dict[str, list[AdvisoryRule]] = {}
_ALL_RULES: list[AdvisoryRule] = []


def register(rules: list[AdvisoryRule]) -> None:
    """Add a batch of rules to the census, indexing the enforceable ones.

    Every rule joins ``_ALL_RULES`` — the census is the whole registry, tiers
    included. Only ``relational`` rules are indexed by target, because that
    index exists to feed the per-validate runner: a structural or waived rule
    in it would be walked on every ``model_validate`` only to be skipped.
    """
    for rule in rules:
        _ALL_RULES.append(rule)
        if rule.tier != RELATIONAL_TIER:
            continue
        for target in rule.targets:
            _RULES_BY_TARGET.setdefault(target, []).append(rule)


@cache
def _ensure_loaded() -> None:
    """Import the rule data on first use (decoupled from import order).

    Both modules reference targets by name only, so importing them never
    constructs a contract model and cannot deadlock model definition.
    ``functools.cache`` makes this a run-once without a module-global flag.
    """
    from . import advisory_rules  # noqa: F401  (import for side-effect: register)
    from . import authoring_rules  # noqa: F401  (same)


def rules_for(cls: type) -> list[AdvisoryRule]:
    """The *enforceable* rules bound to a class or an ancestor, unique by id.

    Relational only — the other tiers are in :func:`all_rules`, which is what
    the renderers and the census read.
    """
    _ensure_loaded()
    out: dict[str, AdvisoryRule] = {}
    for ancestor in cls.__mro__:
        for rule in _RULES_BY_TARGET.get(ancestor.__name__, ()):
            out.setdefault(rule.id, rule)
    return list(out.values())


def all_rules() -> list[AdvisoryRule]:
    """The whole registry (for the JSON export, checklist, and doc generation)."""
    _ensure_loaded()
    return list(_ALL_RULES)


class AdvisoryValidated:
    """Mixin: run this model's registered advisory rules after construction.

    Inheriting classes get relational enforcement for free; a class with no
    registered rules pays only one dict lookup. Bespoke ``@model_validator``s on
    the same class continue to run — they own the ``custom`` rules the registry
    only catalogues.
    """

    @model_validator(mode="after")
    def _run_advisory_rules(self):
        for rule in rules_for(type(self)):
            check_rule(rule, self)
        return self
