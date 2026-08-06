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
is the model being the tighter of the two, which is the safe direction; it is
pinned by ``test_float_spelled_integer_is_a_one_directional_gap`` in
``test_endpoint_model.py``.

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
from collections.abc import Callable
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from analitiq.contracts.shared.introspect import contract_classes

try:  # Python >= 3.11 renamed the private regex parser.
    import re._parser as _sre_parse
except ImportError:  # pragma: no cover - Python 3.10 and earlier
    import sre_parse as _sre_parse  # type: ignore[no-redef]


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


#: Keywords under which a schema declares the shape of a COLLECTION's members,
#: paired with how to build a one-member collection of that shape. A number
#: reached through one of these is not the property itself, so the bad spelling
#: goes into a member and the member goes into the container.
_COLLECTION_KEYWORDS: dict[str, Callable[[Any], Any]] = {
    "items": lambda member: [member],
    "prefixItems": lambda member: [member],
    "additionalProperties": lambda member: {"a": member},
    "patternProperties": lambda member: {"a": member},
}


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


def _numeric_fields(
    node: Any, defs: dict[str, Any], depth: int = 0
) -> tuple[str, Callable[[Any], Any] | None] | None:
    """How a property's schema admits ``integer``/``number``, if it does.

    Returns ``("scalar", None)`` when the property IS the number, so a bad
    spelling goes straight into it; ``("nested", wrap)`` when the number lives
    inside a collection the property declares (``list[int]``,
    ``dict[str, int]``), where ``wrap`` builds a one-member collection around a
    member value; ``None`` when there is no number here.

    Both are swept. A collection member is reached by probing the member and
    wrapping it — a guard that quietly ignored a shape it could not substitute
    into would be the exact failure this file exists to prevent.

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
        return ("scalar", None)
    if _is_objectish(node) and node.get("properties"):
        return None
    nested: tuple[str, Callable[[Any], Any] | None] | None = None
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in node.get(keyword, []):
            found = _numeric_fields(branch, defs, depth + 1)
            if found is None:
                continue
            if found[0] == "scalar":
                return found
            nested = nested or found
    for keyword, wrap in _COLLECTION_KEYWORDS.items():
        for member in _member_schemas(node, keyword):
            if _numeric_fields(member, defs, depth + 1) is not None:
                return ("nested", wrap)
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
        self.fields = list(reach)
        # How to put a probe value into each field: a scalar goes in directly,
        # a collection member is wrapped first. Both are swept; nothing is
        # dropped for being a shape the substitution has to think about.
        self.wrap: dict[str, Callable[[Any], Any]] = {
            name: (wrap or (lambda member: member)) for name, (_how, wrap) in reach.items()
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


def _probes() -> list[Probe]:
    return [
        probe
        for probe in (
            Probe(model)
            for model in sorted(
                contract_classes(), key=lambda c: (c.__module__, c.__name__)
            )
        )
        if probe.fields
    ]


def _sweep() -> tuple[list[str], list[str], set[str], list[str]]:
    """``(violations, unreached, reached_labels, float_gap)`` over the tree."""
    violations: list[str] = []
    unreached: list[str] = []
    reached: set[str] = set()
    float_gap: list[str] = []
    for probe in _probes():
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
        "checked them. Teach `synthesise`/`enum_choices` to build a document "
        "that reaches them:\n  " + "\n  ".join(sorted(unreached))
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
