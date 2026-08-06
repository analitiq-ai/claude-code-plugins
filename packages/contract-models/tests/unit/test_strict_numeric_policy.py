"""The strict-numeric policy: no contract model accepts a number spelling its
own published JSON Schema rejects.

Pydantic's lax mode reads ``true`` as ``1`` and ``"50"`` as ``50``. The rendered
schema says ``type: integer`` and refuses both. Left alone, this package accepts
documents every external consumer of ``schemas.analitiq.ai`` rejects — the
authoring side passes locally and is refused wherever the published schema is
the gate. ``true -> 1`` is the worse spelling: silent, and semantically wrong
rather than merely a formatting difference.

The invariant asserted here is one-directional and deliberately so:

    a document this package ACCEPTS is always one the schema accepts.

The reverse gap stays open — JSON Schema's ``type: integer`` matches a
zero-fraction float, so ``50.0`` passes the schema and fails ``Strict()``. That
is the model being the tighter of the two, which is the safe direction. How far
it reaches is measured here rather than listed, since a list of sites would rot
on the next field; ``test_the_reverse_gap_is_measured_where_it_is_claimed``
reads that measurement and ties it to the hand-written statement of the same
asymmetry in ``test_endpoint_model.py``.

Where the tighter side is NOT safe is a field this package reads off a
producer, because there the refusal falls on a document the producer was
entitled to send. Those fields opt out via ``CoerceInt``, and the last section
of this file asserts their agreement in both directions.

**Why this file enumerates instead of listing.** A hand-written table of
``(model, field)`` pairs would be correct the day it is written and would rot on
the next field. So every check here is derived: the model set comes from
:func:`contract_classes`, the numeric field set comes from each model's OWN
``model_json_schema()``, and a document is synthesised from that schema so a
field with required siblings is reached like any other. Reaching them is the
part that is easy to get wrong — a probe that submits single-field documents
silently skips every field whose model has a required sibling, which is how
``OffsetCursor.increment_by``, ``Schedule.interval_minutes`` and the rest
survived an earlier audit. :func:`test_every_numeric_field_was_reached` fails
rather than skips when a model resists synthesis, so a new model can never
quietly drop out of the sweep.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Annotated, Any, NamedTuple, get_args, get_origin

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from analitiq.contracts.shared.introspect import contract_classes
from analitiq.contracts.shared.types import _narrow_integral_number

# Sampling a string that matches a declared `pattern` means walking the parsed
# regex, and the stdlib exposes no public parser. `sre_parse` is deprecated in
# favour of `re._parser`, which is why the import is tried first; the fallback
# is reached only on the Python 3.10 that `requires-python` still admits, so it
# stays until the support window moves — a decision for a release, not for a
# test-side patch.
try:  # Python >= 3.11 renamed the private regex parser.
    import re._parser as _sre_parse
except ImportError:  # pragma: no cover - Python 3.10 and earlier
    import sre_parse as _sre_parse  # type: ignore[no-redef]  # skipcq: PYL-W0402


def bad_spellings(good: Any) -> dict[str, Any]:
    """The lax-mode spellings of ``good`` the rendered schema refuses.

    Derived from the field's OWN legal value rather than a fixed literal: a
    field whose bound is high (`Engine.memory`, minimum 1024) rejects a stock
    `"50"` on the bound, so a fixed probe value would report no violation on a
    field that is fully lax. `True` is kept as its own spelling because it is
    the silent one — it reads as 1 and nothing about the document says so.
    """
    return {"bool-as-int": True, "string-as-number": str(good)}

# Sites whose model has a required sibling or a conditional rule, so a
# single-field probe never reaches them. Not the field list under test — that is
# derived below — but a floor under the SYNTHESISER: if a change to it stopped
# building documents complete enough to reach these, the sweep would go quiet
# instead of red.
SITES_NEEDING_A_COMPLETE_DOCUMENT = {
    "OffsetCursor.increment_by",
    "Batching.max_records",
    "Column.ordinal_position",
    "Param.minimum",
    "Param.maximum",
    "TransportRateLimit.max_requests",
    "HttpTransport.timeout_seconds",
    "WriteUnit.rows",
    "WriteUnit.bytes",
    "Schedule.interval_minutes",
}

# A floor under the sweep's breadth, not a census of it — the exact count moves
# with the contract and is not a fact worth pinning, but an empty or nearly
# empty sweep passing vacuously is.
MINIMUM_FIELDS_SWEPT = 30

_MAX_DEPTH = 8


class Unsynthesisable(Exception):
    """No value satisfying a schema could be built."""


# ---------------------------------------------------------------------------
# Schema-directed document synthesis
# ---------------------------------------------------------------------------


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    """Follow ``$ref`` chains into the model's own ``$defs``."""
    for _ in range(_MAX_DEPTH):
        if not (isinstance(node, dict) and "$ref" in node):
            return node
        node = defs[node["$ref"].rsplit("/", 1)[-1]]
    raise Unsynthesisable("$ref chain too deep")


def _is_objectish(node: dict[str, Any]) -> bool:
    # Models carrying `json_schema_extra` anyOf/oneOf rules (WriteUnit's
    # at-least-one-bound, ConnectionConditionPredicate's exactly-one-operator)
    # render those rules ALONGSIDE `properties`, with no `type` of their own.
    return node.get("type") == "object" or "properties" in node or "required" in node


def synthesise(node: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    """A JSON value satisfying ``node``, or raise :class:`Unsynthesisable`."""
    if depth > _MAX_DEPTH:
        raise Unsynthesisable("nesting too deep")
    node = _resolve(node, defs)
    if not isinstance(node, dict):
        raise Unsynthesisable(f"not a schema object: {node!r}")
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return node["enum"][0]
    # A null default is the "field omitted" shape, not a usable value.
    if node.get("default") is not None:
        return node["default"]

    if _is_objectish(node):
        properties = node.get("properties") or {}
        required = list(node.get("required", []))
        for keyword in ("anyOf", "oneOf"):
            for branch in node.get(keyword, []):
                branch = _resolve(branch, defs)
                required += [r for r in branch.get("required", []) if r not in required]
                break  # one branch is enough to satisfy an anyOf/oneOf
        document = {}
        for name in required:
            if name not in properties:
                raise Unsynthesisable(f"required {name!r} has no property schema")
            document[name] = synthesise(properties[name], defs, depth + 1)
        return document

    for keyword in ("anyOf", "oneOf"):
        if keyword in node:
            reasons = []
            for branch in node[keyword]:
                if _resolve(branch, defs).get("type") == "null":
                    continue
                try:
                    return synthesise(branch, defs, depth + 1)
                except Unsynthesisable as exc:
                    reasons.append(str(exc))
            raise Unsynthesisable(f"no {keyword} branch: {reasons}")
    if "allOf" in node:
        return synthesise(node["allOf"][0], defs, depth + 1)

    kind = node.get("type")
    if kind is None:
        # `Any` — the schema constrains nothing, so anything satisfies it.
        return 1
    if kind == "null":
        return None
    if kind == "boolean":
        return True
    if kind in ("integer", "number"):
        value: float = 1
        if node.get("minimum") is not None:
            value = max(value, node["minimum"])
        if node.get("exclusiveMinimum") is not None:
            value = max(value, node["exclusiveMinimum"] + 1)
        if node.get("maximum") is not None:
            value = min(value, node["maximum"])
        return int(value) if kind == "integer" else float(value)
    if kind == "string":
        pattern = node.get("pattern")
        text = regex_sample(pattern) if pattern else "a"
        minimum = node.get("minLength") or 0
        if not pattern and len(text) < minimum:
            text = "a" * minimum
        return text
    if kind == "array":
        count = node.get("minItems") or 0
        items = node.get("items")
        if count and items is None:
            raise Unsynthesisable("minItems without an item schema")
        return [synthesise(items, defs, depth + 1) for _ in range(count)]
    raise Unsynthesisable(f"unhandled type {kind!r}")


def enum_choices(node: Any, defs: dict[str, Any], depth: int = 0) -> list[Any]:
    """Every closed value a schema admits (``const`` / ``enum``, through unions).

    Used to vary a discriminator so a conditionally-forbidden field becomes
    legal: ``Schedule.interval_minutes`` is rejected under the default
    ``type: "manual"`` and reachable only under ``type: "interval"``.
    """
    if depth > _MAX_DEPTH:
        return []
    node = _resolve(node, defs)
    if not isinstance(node, dict):
        return []
    if "const" in node:
        return [node["const"]]
    if "enum" in node:
        return [choice for choice in node["enum"] if choice is not None]
    found: list[Any] = []
    for keyword in ("anyOf", "oneOf"):
        for branch in node.get(keyword, []):
            found += enum_choices(branch, defs, depth + 1)
    return found


# ---------------------------------------------------------------------------
# Pattern sampling — a string matching a declared `pattern`
# ---------------------------------------------------------------------------


def regex_sample(pattern: str) -> str:
    """A shortest-ish string matching ``pattern``.

    The contract's patterns are anchored alternations, character classes and
    counted repeats (arrow type names, ISO timestamps, ``\\S``); this walks the
    parsed form rather than guessing per pattern, so a new patterned field needs
    no entry anywhere.
    """
    sample = _emit(_sre_parse.parse(pattern))
    if not re.match(pattern, sample):
        raise Unsynthesisable(f"sampled {sample!r} does not match {pattern!r}")
    return sample


_CATEGORY_SAMPLE = {
    "CATEGORY_DIGIT": "1",
    "CATEGORY_WORD": "a",
    "CATEGORY_SPACE": " ",
    "CATEGORY_NOT_DIGIT": "a",
    "CATEGORY_NOT_WORD": "-",
    "CATEGORY_NOT_SPACE": "a",
}
_CLASS_CANDIDATES = "abz019-_./:+"


def _emit(sequence: Any) -> str:
    out: list[str] = []
    for op, arg in sequence:
        name = str(op)
        if name == "LITERAL":
            out.append(chr(arg))
        elif name == "NOT_LITERAL":
            out.append("a" if chr(arg) != "a" else "b")
        elif name == "ANY":
            out.append("a")
        elif name in ("AT", "ASSERT", "ASSERT_NOT"):
            continue  # anchors and look-arounds contribute no characters
        elif name == "IN":
            out.append(_emit_class(arg))
        elif name in ("MAX_REPEAT", "MIN_REPEAT"):
            low, _high, sub = arg
            out.append(_emit(sub) * max(low, 0))
        elif name == "SUBPATTERN":
            out.append(_emit(arg[3]))
        elif name == "ATOMIC_GROUP":
            out.append(_emit(arg))
        elif name == "BRANCH":
            _, branches = arg
            errors: list[str] = []
            for branch in branches:
                try:
                    out.append(_emit(branch))
                    break
                except Unsynthesisable as exc:
                    errors.append(str(exc))
            else:
                raise Unsynthesisable(f"no usable branch: {errors}")
        else:
            raise Unsynthesisable(f"unhandled regex op {name}")
    return "".join(out)


def _class_matches(items: Any, char: str) -> bool:
    for op, arg in items:
        name = str(op)
        if name == "LITERAL" and chr(arg) == char:
            return True
        if name == "RANGE" and arg[0] <= ord(char) <= arg[1]:
            return True
        if name == "CATEGORY":
            category = str(arg)
            if category == "CATEGORY_DIGIT" and char.isdigit():
                return True
            if category == "CATEGORY_WORD" and (char.isalnum() or char == "_"):
                return True
            if category == "CATEGORY_SPACE" and char.isspace():
                return True
    return False


def _emit_class(items: Any) -> str:
    if any(str(op) == "NEGATE" for op, _ in items):
        rest = [item for item in items if str(item[0]) != "NEGATE"]
        for candidate in _CLASS_CANDIDATES:
            if not _class_matches(rest, candidate):
                return candidate
        raise Unsynthesisable("negated character class excludes every candidate")
    for op, arg in items:
        name = str(op)
        if name == "LITERAL":
            return chr(arg)
        if name == "RANGE":
            return chr(arg[0])
        if name == "CATEGORY":
            return _CATEGORY_SAMPLE[str(arg)]
    raise Unsynthesisable("empty character class")


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


#: Every keyword under which a schema declares the shape of a COLLECTION's
#: members. A number reached through one of these is not the property itself,
#: so a bad spelling has to go into a member and the member into the container.
#: Detection covers all of them, so a number under any collection is FOUND.
_COLLECTION_KEYWORDS = ("items", "prefixItems", "additionalProperties", "patternProperties")

#: How to build a one-member collection — for the keywords whose member the
#: sweep can actually substitute into.
#:
#: Only `items` is here, and the omissions are the point rather than an
#: oversight. A correct wrap for the others cannot be written without reading
#: the rest of the schema: a `patternProperties` key has to MATCH the declared
#: pattern, `additionalProperties` may be closed off by a sibling
#: `properties`/`propertyNames`, and a `prefixItems` tuple has a declared arity
#: and position. A plausible-looking wrap — a hardcoded `{"a": member}`, a
#: one-element list for a two-element tuple — would build a document the schema
#: rejects for a reason that has nothing to do with the number in it, and the
#: sweep would report the field unreachable while pointing at the synthesiser.
#:
#: So a number under a keyword absent from this map is detected and reported
#: unswept, naming THIS map. Add the entry when a field needs it, written
#: against that field's actual shape.
_MEMBER_WRAPS: dict[str, Callable[[Any], Any]] = {
    "items": lambda member: [member],
}


def _identity(member: Any) -> Any:
    """Wrap for a property that IS the number — the value goes in as-is."""
    return member


class Reach(NamedTuple):
    """How a property admits a number.

    ``wrap`` builds the value to substitute into the property; ``None`` means
    the number was found but the sweep has no correct way to reach it, which is
    reported rather than dropped. ``keyword`` is the collection keyword the
    number sits under, or ``None`` when the property is the number itself.
    """

    wrap: Callable[[Any], Any] | None
    keyword: str | None


_SCALAR = Reach(wrap=_identity, keyword=None)


def _prefer(current: Reach | None, found: Reach) -> Reach:
    """Keep the more useful of two reaches — a sweepable shape beats an unsweepable one."""
    if current is None or (current.wrap is None and found.wrap is not None):
        return found
    return current


def _member_schemas(node: dict[str, Any], keyword: str) -> list[Any]:
    """The member schemas ``node`` declares under ``keyword``.

    `patternProperties` maps regexes to schemas and `prefixItems` is a list;
    the rest carry a single schema. A `True`/`False` member schema constrains
    nothing and carries no number, so it is dropped.
    """
    declared = node.get(keyword)
    if isinstance(declared, dict) and keyword == "patternProperties":
        candidates = list(declared.values())
    elif isinstance(declared, list):
        candidates = declared
    else:
        candidates = [declared]
    return [c for c in candidates if isinstance(c, dict)]


def _numeric_fields(node: Any, defs: dict[str, Any], depth: int = 0) -> Reach | None:
    """How a property's schema admits ``integer``/``number``, if it does.

    Returns :data:`_SCALAR` when the property IS the number, a nested
    :class:`Reach` when the number lives inside a collection the property
    declares (``list[int]``, ``dict[str, int]``), or ``None`` when there is no
    number here.

    Detection spans every keyword in :data:`_COLLECTION_KEYWORDS`, while only
    those in :data:`_MEMBER_WRAPS` can be substituted into. That split is
    deliberate: a number the sweep cannot reach is found and reported anyway,
    because a guard that quietly ignored a shape it did not want to think about
    is the exact failure this file exists to prevent. Where a property admits
    the number through more than one shape, the sweepable one wins.

    The walk stops at an object with ``properties``: that is another model's
    shape, and models are swept in their own right from :func:`contract_classes`,
    so descending would report every nested model's numbers a second time.
    """
    if depth > _MAX_DEPTH:
        return None
    node = _resolve(node, defs)
    if not isinstance(node, dict):
        return None
    if node.get("type") in ("integer", "number"):
        return _SCALAR
    if _is_objectish(node) and node.get("properties"):
        return None
    nested: Reach | None = None
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in node.get(keyword, []):
            found = _numeric_fields(branch, defs, depth + 1)
            if found is None:
                continue
            if found.keyword is None:  # the branch itself is the number
                return found
            nested = _prefer(nested, found)
    for keyword in _COLLECTION_KEYWORDS:
        outer = _MEMBER_WRAPS.get(keyword)
        for member in _member_schemas(node, keyword):
            child = _numeric_fields(member, defs, depth + 1)
            if child is None:
                continue
            if child.wrap is None:
                # The number sits under a collection DEEPER IN that has no
                # wrap (`list[dict[str, int]]`). The outer collection being
                # substitutable is irrelevant — the value still cannot be
                # built — so carry the child's reach up. Reporting the outer
                # keyword instead would name a shape that is not the blocker.
                nested = _prefer(nested, child)
            elif outer is None:
                nested = _prefer(nested, Reach(None, keyword))
            else:
                # Wraps compose: the member is built by the child's wrap, then
                # placed in this collection. `list[list[int]]` becomes
                # `[[value]]`, not `[value]`.
                nested = _prefer(
                    nested,
                    Reach(lambda v, o=outer, i=child.wrap: o(i(v)), keyword),
                )
    return nested


def _numeric_leaf(node: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    """The sub-schema that IS the number, so a numeric value can be synthesised.

    :func:`synthesise` takes the first satisfiable branch of a union, which for
    a permissive member type (``str | int | float | bool``) is the string — the
    one spelling that would make the probe vacuous. This picks the numeric
    branch instead, so the bad spellings are derived from a number.
    """
    if depth > _MAX_DEPTH:
        raise Unsynthesisable("nesting too deep")
    node = _resolve(node, defs)
    if not isinstance(node, dict):
        raise Unsynthesisable(f"not a schema object: {node!r}")
    if node.get("type") in ("integer", "number"):
        return node
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in node.get(keyword, []):
            try:
                return _numeric_leaf(branch, defs, depth + 1)
            except Unsynthesisable:
                continue
    for keyword in _COLLECTION_KEYWORDS:
        for member in _member_schemas(node, keyword):
            try:
                return _numeric_leaf(member, defs, depth + 1)
            except Unsynthesisable:
                continue
    raise Unsynthesisable("no numeric branch")


class Probe:
    """One model's numeric fields, each with a document that reaches it."""

    def __init__(self, model: type[BaseModel]):
        self.model = model
        self.schema = model.model_json_schema(ref_template="#/$defs/{model}")
        self.defs = self.schema.get("$defs", {})
        self.properties = self.schema.get("properties") or {}
        self.validator = Draft202012Validator({**self.schema, "$defs": self.defs})
        reach = {
            name: found
            for name, node in self.properties.items()
            if (found := _numeric_fields(node, self.defs)) is not None
        }
        # How to put a probe value into each field: a scalar goes in directly,
        # a collection member is wrapped first.
        self.wrap: dict[str, Callable[[Any], Any]] = {
            name: r.wrap for name, r in reach.items() if r.wrap is not None
        }
        self.fields = list(self.wrap)
        # Numbers found under a collection `_MEMBER_WRAPS` has no wrap for,
        # against the keyword carrying them. Not swept — and so reported by
        # `test_every_numeric_field_was_reached` rather than dropped, which is
        # the whole reason detection is wider than substitution.
        self.unsweepable: dict[str, str | None] = {
            name: r.keyword for name, r in reach.items() if r.wrap is None
        }
        self._candidates: list[dict[str, Any]] | None = None

    @property
    def label(self) -> str:
        return self.model.__name__

    def documents(self) -> list[dict[str, Any]]:
        """Candidate base documents: the minimal one, plus one per discriminator
        value, so a conditionally-forbidden numeric field is still reachable."""
        if self._candidates is None:
            base = synthesise(self.schema, self.defs)
            candidates = [base]
            for name, node in self.properties.items():
                if name in self.fields:
                    continue
                for choice in enum_choices(node, self.defs):
                    if base.get(name) != choice:
                        candidates.append({**base, name: choice})
            self._candidates = candidates
        return self._candidates

    def accepts(self, document: dict[str, Any]) -> bool:
        try:
            self.model.model_validate(document)
        except Exception:
            return False
        return True

    def good(self, field: str) -> Any:
        """A legal NUMERIC value for ``field``, at the depth the number lives."""
        return synthesise(_numeric_leaf(self.properties[field], self.defs), self.defs)

    def reach(self, field: str) -> dict[str, Any] | None:
        """A document both halves accept, carrying a legal value in ``field``.

        A candidate is also tried stripped to its required properties. Some
        models declare their operators as mutually exclusive alternatives
        (`ConnectionConditionPredicate` permits exactly one of `eq`/`in`/…), so
        the synthesised document already spends the single slot on a sibling
        and adding the field under test makes the document illegal for a reason
        that has nothing to do with the number in it.
        """
        good = self.wrap[field](self.good(field))
        required = set(self.schema.get("required", ()))
        for candidate in self.documents():
            minimal = {k: v for k, v in candidate.items() if k in required}
            for base in (candidate, minimal):
                document = {**base, field: good}
                if self.accepts(document) and self.validator.is_valid(document):
                    return base
        return None


def _probes(models: Iterable[type[BaseModel]] | None = None) -> list[Probe]:
    """Probes for every model carrying a number, reachable or not.

    ``models`` defaults to the whole contract tree; a caller passes its own so
    the filter and the reporting below can be exercised on a shape the contract
    does not currently declare.
    """
    return [
        probe
        for probe in (
            Probe(model)
            for model in sorted(
                contract_classes() if models is None else models,
                key=lambda c: (c.__module__, c.__name__),
            )
        )
        if probe.fields or probe.unsweepable
    ]


def _sweep(probes: list[Probe] | None = None) -> tuple[list[str], list[str], set[str], list[str]]:
    """``(violations, unreached, reached_labels, float_gap)`` over the tree.

    ``probes`` defaults to the whole contract tree; a caller passes its own to
    assert what this reports for a shape the contract does not yet declare.
    """
    violations: list[str] = []
    unreached: list[str] = []
    reached: set[str] = set()
    float_gap: list[str] = []
    for probe in (_probes() if probes is None else probes):
        unreached += [
            f"{probe.label}.{name}: a number under {keyword!r}, which "
            f"`_MEMBER_WRAPS` carries no wrap for. Add one written against "
            f"that field's shape — a {keyword!r} member has constraints a "
            f"generic wrap cannot honour."
            for name, keyword in probe.unsweepable.items()
        ]
        try:
            probe.documents()
        except Unsynthesisable as exc:
            unreached += [f"{probe.label}.{f}: no document ({exc})" for f in probe.fields]
            continue
        for field in probe.fields:
            site = f"{probe.label}.{field}"
            try:
                base = probe.reach(field)
            except Unsynthesisable as exc:
                unreached.append(f"{site}: no legal value ({exc})")
                continue
            if base is None:
                unreached.append(f"{site}: no document both halves accept")
                continue
            reached.add(site)
            good = probe.good(field)
            wrap = probe.wrap[field]
            for spelling, bad in bad_spellings(good).items():
                document = {**base, field: wrap(bad)}
                if probe.accepts(document) and not probe.validator.is_valid(document):
                    violations.append(
                        f"{site}: model accepts {spelling} {bad!r}, schema rejects it "
                        f"(document {json.dumps(document)})"
                    )
            # The reverse direction, measured rather than listed: an integer
            # field whose schema accepts the float spelling of its own legal
            # value while `Strict()` refuses it.
            if isinstance(good, int) and not isinstance(good, bool):
                document = {**base, field: wrap(float(good))}
                model_ok = probe.accepts(document)
                schema_ok = probe.validator.is_valid(document)
                if schema_ok and not model_ok:
                    float_gap.append(site)
                elif model_ok and not schema_ok:
                    violations.append(
                        f"{site}: model accepts float-spelled {float(good)!r}, "
                        f"schema rejects it (document {json.dumps(document)})"
                    )
    return violations, unreached, reached, float_gap


SWEEP = _sweep()


def test_no_model_accepts_a_number_spelling_its_own_schema_rejects():
    violations, _unreached, _reached, _gap = SWEEP
    assert not violations, (
        "the model is looser than its published schema at "
        f"{len(violations)} site(s). Annotate the field with a strict alias from "
        "`analitiq.contracts.shared.types` (StrictPositiveInt / "
        "StrictNonNegativeInt / StrictInt / StrictFloat) rather than bare "
        "`int`/`float`:\n  " + "\n  ".join(sorted(violations))
    )


def test_every_numeric_field_was_reached():
    # The sweep's own coverage. A field the synthesiser cannot build a document
    # for is reported here rather than silently dropped — an unreached field is
    # an unchecked field, and the whole point of deriving the field set is that
    # it cannot go quiet.
    _violations, unreached, _reached, _gap = SWEEP
    assert not unreached, (
        "the strict-numeric sweep could not reach these fields, so nothing "
        "checked them. Each entry names what blocked it:\n  "
        + "\n  ".join(sorted(unreached))
    )


def test_the_sweep_reaches_fields_whose_model_has_required_siblings():
    _violations, _unreached, reached, _gap = SWEEP
    missing = SITES_NEEDING_A_COMPLETE_DOCUMENT - reached
    assert not missing, (
        "the synthesiser stopped building documents complete enough to reach "
        f"{sorted(missing)}. A single-field probe misses exactly these, which is "
        "how they survived an earlier audit."
    )


def test_the_sweep_finds_a_real_field_set():
    # A sweep over an empty field set passes vacuously. Pin that it is walking a
    # substantial part of the tree, so an import or introspection change that
    # empties it fails instead of going green.
    _violations, _unreached, reached, _gap = SWEEP
    assert len(reached) >= MINIMUM_FIELDS_SWEPT, sorted(reached)


def test_the_reverse_gap_is_measured_where_it_is_claimed():
    # The reverse direction is recorded by being COUNTED, not by being listed —
    # a list of sites would rot on the next field. Counting only works if
    # something reads the count: the gap arm shares its branch with the
    # inversion check, so an edit that stopped either from finding anything
    # would leave every assertion above green while the safe direction went
    # unwatched.
    #
    # The membership assertion is what ties the two halves of the record
    # together: `test_float_spelled_integer_is_a_one_directional_gap` in
    # `test_endpoint_model.py` states the asymmetry by hand on `PageSize`, and
    # a hand-written statement about a site the sweep no longer finds in the
    # gap is a claim nobody is checking.
    _violations, _unreached, _reached, gap = SWEEP
    assert gap, (
        "the sweep found the reverse direction nowhere. Either every integer "
        "field became lax — in which case the violations above should have "
        "fired — or the gap arm stopped running and the inversion check went "
        "with it."
    )
    hand_pinned = {"PageSize.default", "PageSize.max"}
    assert hand_pinned <= set(gap), (
        f"{sorted(hand_pinned - set(gap))} is pinned by hand as a float-gap "
        "site in `test_endpoint_model.py`, and the sweep no longer agrees. "
        "Whichever moved, the two records of the same asymmetry now disagree."
    )


def test_the_sweep_detects_a_lax_field():
    # The detector proven able to go red. A diff that broke `Probe.accepts` or
    # the schema comparison would otherwise make every assertion above pass by
    # finding nothing.
    class Lax(BaseModel):
        model_config = ConfigDict(extra="forbid")

        sibling: str = Field(...)
        count: int = Field(..., ge=1)

    probe = Probe(Lax)
    assert probe.fields == ["count"]
    base = probe.reach("count")
    assert base is not None
    spellings = bad_spellings(synthesise(probe.properties["count"], probe.defs))
    caught = [
        spelling
        for spelling, bad in spellings.items()
        if probe.accepts({**base, "count": bad})
        and not probe.validator.is_valid({**base, "count": bad})
    ]
    assert sorted(caught) == sorted(spellings)


def test_detection_spans_every_collection_keyword():
    # Detection must never be narrower than substitution. If it were, a number
    # under a collection with no wrap would not be found at all, and the field
    # would drop out of the sweep silently instead of being reported — the
    # failure mode this whole file is built to make impossible.
    assert set(_MEMBER_WRAPS) <= set(_COLLECTION_KEYWORDS)

    members = {"type": "integer", "minimum": 1}
    per_keyword = {
        "items": {"type": "array", "items": members},
        "prefixItems": {"type": "array", "prefixItems": [members]},
        "additionalProperties": {"type": "object", "additionalProperties": members},
        "patternProperties": {"type": "object", "patternProperties": {"^x_": members}},
    }
    assert set(per_keyword) == set(_COLLECTION_KEYWORDS), (
        "a collection keyword was added to `_COLLECTION_KEYWORDS` without a case "
        "here, so nothing checks that a number under it is detected."
    )
    for keyword, schema in per_keyword.items():
        found = _numeric_fields(schema, {})
        assert found is not None, f"a number under {keyword!r} went undetected"
        assert found.keyword == keyword
    # Which of them are substitutable is NOT asserted here: `_numeric_fields`
    # reads the wrap straight out of `_MEMBER_WRAPS`, so comparing the two
    # would be true by construction whatever that map contained. The
    # substitution half is proven behaviourally below instead.


#: For each collection keyword, a schema declaring a number under it in the most
#: constrained form the contract could plausibly render — a closed object, a
#: key pattern, a fixed-arity tuple. A wrap that ignores those constraints
#: builds a document this schema rejects, which is what makes the test below
#: able to fail.
_WRAP_CASES: dict[str, dict[str, Any]] = {
    "items": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
    "prefixItems": {
        "type": "array",
        "prefixItems": [{"type": "integer"}, {"type": "integer"}],
        "minItems": 2,
    },
    "additionalProperties": {
        "type": "object",
        "propertyNames": {"pattern": "^x_"},
        "additionalProperties": {"type": "integer"},
    },
    "patternProperties": {
        "type": "object",
        "patternProperties": {"^x_": {"type": "integer"}},
        "additionalProperties": False,
    },
}


def test_every_declared_wrap_builds_a_document_its_schema_accepts():
    # The substitution half, proven rather than named. A wrap that ignores the
    # shape it claims to handle — a key that must match a pattern, a tuple with
    # a declared arity — builds a document the schema rejects for a reason that
    # has nothing to do with the number in it. The field is then reported
    # unreachable, and the reader is sent to the synthesiser rather than here.
    #
    # This is the test that decides what may live in `_MEMBER_WRAPS`: the three
    # generic wraps that once sat beside `items` all fail it.
    assert set(_WRAP_CASES) == set(_COLLECTION_KEYWORDS), (
        "every collection keyword needs a case here, so a wrap added later is "
        "proven against the shape it claims to handle rather than assumed."
    )
    for keyword, wrap in _MEMBER_WRAPS.items():
        document = wrap(1)
        assert Draft202012Validator(_WRAP_CASES[keyword]).is_valid(document), (
            f"the {keyword!r} wrap built {document!r}, which its own schema "
            f"rejects. A wrap has to honour the shape it claims to handle, or "
            f"the probe reports the field unreachable for a reason unrelated "
            f"to the number in it."
        )


def test_a_number_the_sweep_cannot_substitute_into_is_reported_not_dropped():
    # `_MEMBER_WRAPS` deliberately covers only `items`, because a correct wrap
    # for the others has to honour constraints a generic one cannot see (a
    # `patternProperties` key must match the pattern; a `prefixItems` tuple has
    # an arity). The cost of that restraint is paid here: such a field must
    # still be FOUND and reported, so it fails the coverage arm loudly rather
    # than vanishing from the field set.
    class DictOfCounts(BaseModel):
        model_config = ConfigDict(extra="forbid")

        name: str = Field(...)
        counts: dict[str, int] = Field(default_factory=dict)

    probe = Probe(DictOfCounts)
    assert probe.fields == []
    assert probe.unsweepable == {"counts": "additionalProperties"}

    # Detection alone is not the guarantee — the field has to survive the
    # filter in `_probes` and the reporting in `_sweep` to actually reach the
    # coverage arm. Asserting only `Probe.unsweepable` would keep passing if
    # either of those dropped it, which is the exact silence this guards.
    assert [p.label for p in _probes([DictOfCounts])] == ["DictOfCounts"], (
        "a probe carrying only unsweepable numbers was filtered out, so its "
        "field reaches no assertion at all"
    )
    _violations, unreached, reached, _gap = _sweep(_probes([DictOfCounts]))
    assert not reached
    assert len(unreached) == 1, unreached
    assert unreached[0].startswith("DictOfCounts.counts:")
    # The message has to name the real cause. Sending the reader to the
    # synthesiser is what made this worth fixing in the first place.
    assert "additionalProperties" in unreached[0]
    assert "_MEMBER_WRAPS" in unreached[0]


def test_an_unsweepable_collection_nested_under_a_sweepable_one_names_the_blocker():
    # `list[dict[str, int]]`: the OUTER collection is substitutable, the inner
    # one is not. Reporting the outer keyword — or worse, marking the field
    # sweepable and substituting `[1]` for a list of objects — names a shape
    # that is not the blocker and sends the reader to the wrong place. The
    # child's reach has to win.
    class ListOfDicts(BaseModel):
        model_config = ConfigDict(extra="forbid")

        rows: list[dict[str, int]] = Field(default_factory=list)

    probe = Probe(ListOfDicts)
    assert probe.fields == []
    assert probe.unsweepable == {"rows": "additionalProperties"}
    _violations, unreached, _reached, _gap = _sweep(_probes([ListOfDicts]))
    assert len(unreached) == 1, unreached
    assert "additionalProperties" in unreached[0], unreached[0]
    assert "'items'" not in unreached[0], (
        f"named the outer collection, which is not what blocked it: {unreached[0]}"
    )


def test_nested_sweepable_collections_compose_their_wraps():
    # `list[list[int]]` needs `[[value]]`. A wrap that stopped at the outer
    # collection would substitute `[value]` — a document the schema rejects for
    # a reason unrelated to the number — and the field would be reported
    # unreachable despite every shape on the path being substitutable.
    class ListOfLists(BaseModel):
        model_config = ConfigDict(extra="forbid")

        grid: list[list[int]] = Field(default_factory=list)

    probe = Probe(ListOfLists)
    assert probe.fields == ["grid"]
    assert probe.wrap["grid"](1) == [[1]]
    _violations, unreached, reached, _gap = _sweep(_probes([ListOfLists]))
    assert not unreached, unreached
    assert reached == {"ListOfLists.grid"}


@pytest.mark.parametrize(
    "pattern,expected_match",
    [
        (r"^\d{4}-\d{2}-\d{2}$", True),
        (r"\S", True),
        (r"^(?:foo|bar)-[0-9]+$", True),
        (r"^[a-z0-9][a-z0-9_-]*$", True),
    ],
)
def test_regex_sample_produces_a_matching_string(pattern, expected_match):
    # `regex_sample` feeds required sibling fields; a wrong sample would make a
    # model unreachable and show up as a coverage failure rather than a silent
    # skip, but pin it directly so the failure names the real cause.
    assert bool(re.match(pattern, regex_sample(pattern))) is expected_match


# ---------------------------------------------------------------------------
# The read path: where the policy is required to hold in BOTH directions
# ---------------------------------------------------------------------------
#
# `CoerceInt` is the opt-out from the asymmetry the sweep measures above. On an
# authoring gate the model may be the tighter side: the author picks the
# spelling, so refusing `1500.0` costs a rewrite. On a field this package READS
# off a producer, the same refusal rejects a response the published schema calls
# valid and the producer was entitled to send. So on those fields agreement is
# required in both directions, and the alias buys it with a before-validator.
#
# That before-validator is load-bearing rather than incidental: `CoerceInt` is
# built on `StrictInt`, so it is the only path a `Decimal` or a `1500.0` has.


def _carries_coerce_int(annotation: Any) -> bool:
    """Whether ``annotation`` reaches a ``CoerceInt`` anywhere inside it.

    Keyed off the marker the alias is BUILT from — the identity of its
    before-validator function — rather than off a name or a field list. A site
    spelled `CoerceInt | None`, or wrapped in a collection, is found without
    the discovery having to know which spellings exist.
    """
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        if any(
            isinstance(marker, BeforeValidator)
            and marker.func is _narrow_integral_number
            for marker in args[1:]
        ):
            return True
        return _carries_coerce_int(args[0])
    return any(_carries_coerce_int(arg) for arg in get_args(annotation))


COERCE_INT_SITES = [
    (model, name)
    for model in sorted(contract_classes(), key=lambda c: (c.__module__, c.__name__))
    for name, field in model.model_fields.items()
    if _carries_coerce_int(field.annotation)
]
COERCE_INT_IDS = [f"{model.__name__}.{name}" for model, name in COERCE_INT_SITES]


def test_the_read_path_alias_is_in_use():
    # Every assertion below is parametrized over the discovery, so an empty
    # discovery turns them all into vacuous passes — including the only
    # coverage the before-validator has. A contract that genuinely stopped
    # reading producer-written integers would delete the alias too, and this
    # failure is the prompt to delete these tests with it.
    assert COERCE_INT_SITES, (
        "no contract field carries `CoerceInt`. Either the read path lost its "
        "alias or `_carries_coerce_int` stopped recognising it."
    )


@pytest.mark.parametrize(("model", "field"), COERCE_INT_SITES, ids=COERCE_INT_IDS)
def test_the_read_path_agrees_with_its_schema_in_both_directions(model, field):
    # The sweep above asserts one direction everywhere; here the reverse one is
    # asserted too, which is the whole difference between an authoring field and
    # a field a producer writes. `probe.accepts` and `probe.validator` are the
    # same pair the sweep uses, so this cannot drift from it.
    probe = Probe(model)
    base = probe.reach(field)
    assert base is not None, f"no document reaches {model.__name__}.{field}"
    good = probe.good(field)
    spellings = {
        # A JSON producer serialising a computed count emits the zero-fraction
        # float, which `type: integer` matches. This is the spelling the
        # before-validator exists for.
        "integral-float": float(good),
        # Not a count. Converting it would invent one, so it stays refused —
        # and the schema refuses it too, which is what makes that safe.
        "fractional-float": float(good) + 0.5,
        **bad_spellings(good),
    }
    for spelling, value in spellings.items():
        document = {**base, field: probe.wrap[field](value)}
        assert probe.accepts(document) == probe.validator.is_valid(document), (
            f"{model.__name__}.{field} and its published schema disagree on the "
            f"{spelling} spelling {value!r} (document {json.dumps(document)}). "
            "On a field a producer writes, either direction is a defect: "
            "looser than the schema accepts documents no external consumer "
            "will, tighter refuses ones the producer was entitled to send."
        )


@pytest.mark.parametrize(("model", "field"), COERCE_INT_SITES, ids=COERCE_INT_IDS)
def test_the_read_path_takes_the_decimal_a_driver_returns(model, field):
    # A driver hands a numeric column back as `Decimal`. It never crosses JSON,
    # so no schema spelling covers it and the agreement test above cannot see
    # it — yet it is the spelling that reaches these fields when the row is read
    # straight out of the database rather than off the wire.
    probe = Probe(model)
    base = probe.reach(field)
    assert base is not None
    good = probe.good(field)
    document = {**base, field: probe.wrap[field](Decimal(good))}
    assert probe.accepts(document), (
        f"{model.__name__}.{field} refuses the `Decimal` a driver returns "
        f"(document field {Decimal(good)!r})"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Narrowed: the spellings a producer writes for a whole number.
        (Decimal("1500"), 1500),
        (Decimal("1500.000"), 1500),
        (Decimal("-2"), -2),
        (Decimal("0"), 0),
        (1500.0, 1500),
        (-2.0, -2),
        (0.0, 0),
        # Passed through, so `StrictInt` decides. Each of these would become a
        # wrong answer if the narrowing were widened to whatever `int()`
        # swallows: a truncated count, or the coercion the strict aliases exist
        # to close.
        (Decimal("1.5"), Decimal("1.5")),
        (Decimal("NaN"), Decimal("NaN")),
        (Decimal("Infinity"), Decimal("Infinity")),
        (1.5, 1.5),
        (float("inf"), float("inf")),
        ("50", "50"),
        (True, True),
        (7, 7),
        (None, None),
    ],
    ids=repr,
)
def test_the_read_path_narrows_only_an_integral_number(value, expected):
    # The truth table of the before-validator itself. The wiring tests above
    # prove it is reached; this proves what it does when it is, including the
    # cases that have no JSON spelling and so cannot be asserted through a
    # schema.
    result = _narrow_integral_number(value)
    if isinstance(expected, Decimal) and expected.is_nan():
        assert isinstance(result, Decimal) and result.is_nan()
        return
    assert result == expected
    # Equality is blind here — `Decimal("1500") == 1500 == 1500.0` — so the
    # narrowing is only observable as a type change.
    assert type(result) is type(expected), (
        f"{value!r} came back as {type(result).__name__}, expected "
        f"{type(expected).__name__}"
    )
