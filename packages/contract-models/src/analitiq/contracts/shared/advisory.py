"""The relational rule engine — the checks the registry dispatches per instance.

The rules themselves are no longer here. They live in ``rules/adv/*.yaml``, one
record per rule (schema: ``rules/SCHEMA.md``), compiled by
``scripts/render_rules.py`` into the JSON this package ships and loaded by
:mod:`rule_record`. This module is what *runs* the subset of them that a
generic check can express.

Those are the ``advisory``-tier rules whose ``validator`` points back into this
file: set-equality, disjointness, membership, cross-key uniqueness between
sibling instance values — constraints stock JSON Schema cannot state. Authoring
one is a record with ``kind`` in its ``data``; it needs no new imperative code,
which is the whole reason the generic vocabulary is closed and small.

Two things run off that:

1. runtime enforcement — the :class:`AdvisoryValidated` mixin runs a model's
   registered rules on every ``model_validate``;
2. a valid/invalid instance fixture corpus keyed by rule id
   (``contract-models/tests/fixtures/advisory``) that a non-Python second
   system can re-implement the fixed rule *kinds* against and reconcile.

A rule whose logic is irreducibly bespoke (recursive schema walks, hash
equality, path resolution) carries ``kind: custom`` and a ``validator`` naming
the model validator that enforces it. This module never calls those — they run
as their own ``@model_validator`` — but the binding is lint-resolved, so the
registry still knows what enforces what. Every other tier is enforced somewhere
else entirely, or nowhere, and the record says which.
"""
from __future__ import annotations

from functools import cache
from typing import Any, Callable

from pydantic import model_validator

from .rule_record import ADVISORY_TIER, RuleRecord, load_records

# --- Rule kinds -------------------------------------------------------------

#: The fixed, generically-checkable relational vocabulary. Each maps to exactly
#: one checker in ``_CHECKERS``, and a record naming one carries a ``validator``
#: pointing at that function. ``custom`` is the escape hatch: catalogued only,
#: enforced by the model validator its ``validator`` names.
GENERIC_KINDS = (
    "disjoint",
    "set_equal",
    "member_of",
    "subset_of",
    "unique_by",
)
CUSTOM_KIND = "custom"


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


def _msg(rule: RuleRecord, detail: str) -> str:
    return f"[{rule.id}] {rule.statement} ({detail})"


def _check_disjoint(rule: RuleRecord, model: Any) -> None:
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


def _check_set_equal(rule: RuleRecord, model: Any) -> None:
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


def _check_member_of(rule: RuleRecord, model: Any) -> None:
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


def _check_subset_of(rule: RuleRecord, model: Any) -> None:
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


def _check_unique_by(rule: RuleRecord, model: Any) -> None:
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


_CHECKERS: dict[str, Callable[[RuleRecord, Any], None]] = {
    "disjoint": _check_disjoint,
    "set_equal": _check_set_equal,
    "member_of": _check_member_of,
    "subset_of": _check_subset_of,
    "unique_by": _check_unique_by,
}


def check_rule(rule: RuleRecord, model: Any) -> None:
    """Enforce one rule against a model instance.

    A no-op unless the rule's ``validator`` points back into this module — that
    is what "the registry dispatches it" means. A ``custom`` rule's validator
    runs as its own ``@model_validator``; a structural rule is already enforced
    by the shape the model was built from; the rest are enforced elsewhere or
    nowhere, and the record says which.
    """
    if not rule.engine_dispatched:
        return
    checker = _CHECKERS.get(rule.kind)
    if checker is not None:
        checker(rule, model)



# --- Registry + mixin -------------------------------------------------------


@cache
def _load() -> tuple[list[RuleRecord], dict[str, list[RuleRecord]]]:
    """Read the compiled registry once, and index the dispatchable rules.

    Only ``advisory``-tier records are indexed by target, because that index
    feeds the per-validate runner: a structural or referential record in it
    would be walked on every ``model_validate`` only to be skipped.
    ``functools.cache`` makes this run-once without a module-global flag.
    """
    records = load_records()
    by_target: dict[str, list[RuleRecord]] = {}
    for record in records:
        if record.tier != ADVISORY_TIER:
            continue
        for target in record.targets:
            by_target.setdefault(target, []).append(record)
    return records, by_target


def rules_for(cls: type) -> list[RuleRecord]:
    """The *dispatchable* rules bound to a class or an ancestor, unique by id.

    Advisory tier only — every other tier is in :func:`all_rules`, which is what
    the renderers and the census read.
    """
    _, by_target = _load()
    out: dict[str, RuleRecord] = {}
    for ancestor in cls.__mro__:
        for rule in by_target.get(ancestor.__name__, ()):
            out.setdefault(rule.id, rule)
    return list(out.values())


def all_rules() -> list[RuleRecord]:
    """The whole registry — every tier, every scope, ordered by id."""
    records, _ = _load()
    return list(records)


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
