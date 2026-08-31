"""The reachability census: which contract fields the engine reads.

The engine publishes which fields its run-time path reads
(``census.consumption.pin`` vendors one pinned version — the pinned artifact
this census rests on, so every verdict below is a comparison against a
published fact, never a reading of the engine). This module walks the live
contract models from the manifest's ``roots`` and classifies every field it
reaches:

- **read** — the manifest claims the field with at least one site.
- **opaque** — the field is unclaimed and belongs to a model the engine
  consumes whole as a JSON grammar. A model may be dumped whole at one site
  and read by attribute at another, so a claim on an opaque model's field
  still counts as a read. The walk records the model and stops: descending
  into it would report every field of an expression tree as unread.
- **unread** — reachable, not opaque, not claimed. The contract declares it
  and the manifest claims no read of it. Each such field needs a
  :class:`~census.consumption.disposition.FieldDisposition`.

Coverage is ``roots`` because reachability through field annotations is the
only route by which the engine can hold a model's instance: a field on a
model no root reaches is not a field the engine could read or ignore, so it
is not covered — unknown, not unread. ``kit_reads`` are the conformance kit
grading a connector, not the engine running one, and ``transport`` sites
re-serialise a document unchanged; neither is a read, so neither is a claim.

:func:`census_report` diffs the classification against the dispositions.
Each consumer — ``tests/census/test_contract_consumption.py`` and
``scripts/render_contract_consumption.py`` — assert on that one report, so
the lint and the tool can never disagree.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
import types
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from analitiq.contracts.shared.common import set_derived_field
from pydantic import BaseModel

from census.consumption.disposition import DispositionKind, FieldDisposition
from census.consumption.pin import CLAIMS_KEY, OPAQUE_KEY, ROOTS_KEY

__all__ = [
    "ConsumptionReport",
    "census_report",
    "classify",
    "qualified_name",
    "reachable_models",
    "resolve_model",
]

FieldRef = tuple[str, str]


def qualified_name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def resolve_model(qualified: str) -> type[BaseModel]:
    """The pydantic model a manifest path names, imported by module path.

    A root the live tree no longer holds is a manifest written against
    another contract version; the census refuses it rather than covering
    less than the engine says it reads.
    """
    module_name, _, class_name = qualified.rpartition(".")
    try:
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
    except (ImportError, AttributeError, ValueError) as exc:
        # ValueError: a dotless or otherwise malformed name, which importlib
        # refuses before it looks anywhere.
        raise LookupError(
            f"manifest names {qualified!r}, which the live contract tree does not hold"
        ) from exc
    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        raise LookupError(f"manifest names {qualified!r}, which is not a pydantic model")
    return cls


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    """Every pydantic model an annotation can hold, unwrapping the containers
    an instance can arrive through: ``Optional`` / ``Union``, ``Annotated``,
    ``list`` / ``set`` / ``tuple`` / ``dict`` and their nesting. A
    ``Literal`` holds values, never models."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    origin = get_origin(annotation)
    if origin is None or origin is Literal:
        return []
    if origin is Annotated:
        return _models_in(get_args(annotation)[0])
    found: list[type[BaseModel]] = []
    for arg in get_args(annotation):
        if arg is Ellipsis:
            continue
        found.extend(_models_in(arg))
    return found


def _is_literal(annotation: Any) -> bool:
    """A field whose value pydantic settles at parse time: a ``Literal``,
    possibly optional or annotated. That is the shape a union discriminator
    or a schema-pinned constant has."""
    origin = get_origin(annotation)
    if origin is Literal:
        return True
    if origin is Annotated:
        return _is_literal(get_args(annotation)[0])
    if origin is Union or origin is types.UnionType:
        members = [a for a in get_args(annotation) if a is not type(None)]
        return bool(members) and all(_is_literal(m) for m in members)
    return False


#: The one sanctioned way a contract model writes a derived value, named
#: through the symbol itself: a rename fails this import rather than leaving
#: the scan below matching nothing and reporting every derivation unwritten.
_DERIVATION_WRITER = set_derived_field.__name__

#: The writer's parameter naming the field written, and the position it sits
#: at — the index read off the live signature, so a parameter inserted ahead
#: of it cannot leave the scan reading the wrong argument, and the lookup
#: fails this import if the parameter is renamed.
_WRITTEN_FIELD_PARAMETER = "field"
_WRITTEN_FIELD_POSITION = list(
    inspect.signature(set_derived_field).parameters
).index(_WRITTEN_FIELD_PARAMETER)


def _called_name(node: ast.Call) -> str | None:
    """The name a call invokes, whether bare or attribute-qualified.

    A qualified call matches on the attribute alone: what is being called
    ``set_derived_field`` on is not resolvable from the source, and a
    contract model reaching the writer through anything but the shared
    helper is a defect the reader catches, not a second name to grade.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _writes(source: str, name: str) -> bool:
    """Whether ``source`` calls the derivation writer with ``name`` as the
    field written — that argument by the parameter name and position the
    writer itself declares, and as a literal. A call site and a constant:
    located structurally, never read as a sentence."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Source that will not parse establishes nothing, so it contributes
        # no derivation; the report says so in the same words as source that
        # could not be read at all.
        return False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called_name(node) == _DERIVATION_WRITER):
            continue
        written = next(
            (kw.value for kw in node.keywords if kw.arg == _WRITTEN_FIELD_PARAMETER),
            node.args[_WRITTEN_FIELD_POSITION]
            if len(node.args) > _WRITTEN_FIELD_POSITION
            else None,
        )
        if isinstance(written, ast.Constant) and written.value == name:
            return True
    return False


def _writes_derived_field(cls: type[BaseModel], name: str) -> bool:
    """Whether a validator pydantic runs while building ``cls`` writes
    ``name`` through the sanctioned derivation helper.

    The validators come from the model's own decorator registry, which is
    what pydantic will actually run: one declared on a base and inherited
    is there, and one a subclass overrides is there as the override rather
    than beside it. Reading the class body instead would credit a model
    with a call in a method nothing invokes, with a nested class's
    derivation, and with a base's derivation a subclass had replaced.

    Two halves stay the reader's, under
    ``.claude/rules/reachability-dispositions.md``: that the field the entry
    sits on is an INPUT to the derivation rather than some other unread
    field of the same model, and that the call is on a branch the validator
    reaches. A validator that never writes the field is what this decides;
    one that writes it only under a condition no document meets is not.
    """
    for decorator in cls.__pydantic_decorators__.model_validators.values():
        try:
            source = textwrap.dedent(inspect.getsource(decorator.func))
        except (OSError, TypeError):
            # A validator with no readable source establishes no derivation.
            # The report's wording carries that case rather than concluding
            # the contract computes nothing.
            continue
        if _writes(source, name):
            return True
    return False


def reachable_models(manifest: dict[str, Any]) -> dict[str, type[BaseModel]]:
    """Every model a root reaches, keyed by qualified name.

    Opaque models are recorded — they are covered, as a whole — but not
    descended: their fields are consumed as a grammar, not read.
    """
    opaque = set(manifest[OPAQUE_KEY])
    found: dict[str, type[BaseModel]] = {}
    stack = [resolve_model(root) for root in manifest[ROOTS_KEY]]
    while stack:
        cls = stack.pop()
        name = qualified_name(cls)
        if name in found:
            continue
        found[name] = cls
        if name in opaque:
            continue
        for info in cls.model_fields.values():
            stack.extend(_models_in(info.annotation))
    return dict(sorted(found.items()))


def classify(manifest: dict[str, Any]) -> dict[str, frozenset[FieldRef]]:
    """``read`` / ``opaque`` / ``unread`` over every covered field.

    Only ``claims`` makes a field read. ``kit_reads`` and ``transport`` are
    carried by the manifest for the engine's own bookkeeping and never
    consulted here.
    """
    claims = manifest[CLAIMS_KEY]
    opaque_models = set(manifest[OPAQUE_KEY])
    read: set[FieldRef] = set()
    opaque: set[FieldRef] = set()
    unread: set[FieldRef] = set()
    for name, cls in reachable_models(manifest).items():
        claimed = claims.get(name, {})
        for field_name in cls.model_fields:
            ref = (name, field_name)
            # A claim wins: a model consumed whole at one site can still be
            # read by attribute at another, and the artifact lists such a
            # model under both keys. Only an unclaimed field of an opaque
            # model is opaque.
            if claimed.get(field_name):
                read.add(ref)
            elif name in opaque_models:
                opaque.add(ref)
            else:
                unread.add(ref)
    return {
        "read": frozenset(read),
        "opaque": frozenset(opaque),
        "unread": frozenset(unread),
    }


@dataclass(frozen=True)
class ConsumptionReport:
    """The classification diffed against the dispositions.

    Every finding is a set comparison or an annotation shape check. Whether
    a reason is the right one is the reader's —
    ``.claude/rules/reachability-dispositions.md`` — and a finding here is
    how that reader is summoned.
    """

    #: Reachable, unclaimed, and no entry says what consumes it.
    unread_without_disposition: tuple[FieldRef, ...]
    #: An entry for a field the manifest now claims — the engine reads it, so
    #: the disposition is stale.
    disposition_now_claimed: tuple[FieldDisposition, ...]
    #: An entry naming a field outside coverage: a model no root reaches, a
    #: model the engine consumes opaquely, or a field the model no longer
    #: declares.
    disposition_of_unknown_field: tuple[FieldDisposition, ...]
    #: More than one entry for one field.
    duplicate_dispositions: tuple[FieldRef, ...]
    #: ``structural`` claims pydantic settles the value at parse time, and
    #: the field's annotation is not the ``Literal`` shape that would.
    structural_not_literal: tuple[FieldDisposition, ...]
    #: ``derivation_input`` claims the field reaches the run under the name
    #: it derives, and the manifest claims no read of that name on this
    #: model — either it is the wrong name, or nothing reads the product.
    derivation_product_unread: tuple[FieldDisposition, ...]
    #: ``derivation_input`` claims the contract computes ``derives`` at
    #: parse time, and no validator the model runs writes it — the entry
    #: names a derivation the contract does not perform.
    derivation_not_written: tuple[FieldDisposition, ...]
    #: A claim naming a field the live model does not declare — the manifest
    #: was generated against another contract version.
    claim_of_unknown_field: tuple[FieldRef, ...]
    #: A ``claims`` or ``opaque`` key naming a model no root reaches, or one
    #: the live tree does not hold at all.
    manifest_names_unknown_model: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (
            self.unread_without_disposition
            or self.disposition_now_claimed
            or self.disposition_of_unknown_field
            or self.duplicate_dispositions
            or self.structural_not_literal
            or self.derivation_product_unread
            or self.derivation_not_written
            or self.claim_of_unknown_field
            or self.manifest_names_unknown_model
        )

    def render(self) -> str:
        lines: list[str] = []

        def group(title: str, items, line_of) -> None:
            if not items:
                return
            lines.append(f"{title} ({len(items)}):")
            lines.extend(f"    {line_of(item)}" for item in items)
            lines.append("")

        group(
            "unread fields with no disposition — the pinned manifest claims no "
            "read of these; add a FieldDisposition naming what consumes each, "
            "or declare the gap",
            self.unread_without_disposition,
            lambda ref: f"{ref[0]}.{ref[1]}",
        )
        group(
            "dispositions of fields the manifest now claims — the engine "
            "reads them; remove the entry",
            self.disposition_now_claimed,
            lambda d: f"{d.qualified_model}.{d.field} ({d.kind})",
        )
        group(
            "dispositions of fields outside coverage — model unreachable from "
            "any root, opaque, or field no longer declared; remove or re-key",
            self.disposition_of_unknown_field,
            lambda d: f"{d.qualified_model}.{d.field} ({d.kind})",
        )
        group(
            "duplicate dispositions",
            self.duplicate_dispositions,
            lambda ref: f"{ref[0]}.{ref[1]}",
        )
        group(
            "structural dispositions on fields that are not Literal-typed — "
            "the annotation is not the shape pydantic settles at parse time; "
            "use another kind: "
            f"{', '.join(_NON_STRUCTURAL_KINDS)}",
            self.structural_not_literal,
            lambda d: f"{d.qualified_model}.{d.field}",
        )
        group(
            "derivation_input dispositions whose derived field the model does "
            "not declare or the manifest does not claim — either derives names "
            "the wrong field, or the product reaches no run-time read either "
            "and this is a gap, not a derivation",
            self.derivation_product_unread,
            lambda d: f"{d.qualified_model}.{d.field} -> {d.derives}",
        )
        group(
            "derivation_input dispositions whose derived field no validator "
            f"writes — no {_DERIVATION_WRITER} call naming it in any "
            "validator the model runs, so either the contract does not "
            "compute what the entry says it computes, or it computes it "
            "somewhere the census cannot read",
            self.derivation_not_written,
            lambda d: f"{d.qualified_model}.{d.field} -> {d.derives}",
        )
        group(
            "claims of fields the live model does not declare — the manifest "
            "was generated against another contract version; re-vendor or "
            "hold the pin",
            self.claim_of_unknown_field,
            lambda ref: f"{ref[0]}.{ref[1]}",
        )
        group(
            "manifest keys naming models outside coverage — no root reaches "
            "the model, or the live tree does not hold it",
            self.manifest_names_unknown_model,
            lambda name: name,
        )
        if not lines:
            return "reachability census is complete and current"
        return "\n".join(lines).rstrip()


#: The kinds a ``structural`` finding points the author at: every kind but
#: ``structural`` itself.
_NON_STRUCTURAL_KINDS = tuple(
    kind for kind in get_args(DispositionKind) if kind != "structural"
)


def census_report(
    manifest: dict[str, Any], dispositions: tuple[FieldDisposition, ...]
) -> ConsumptionReport:
    models = reachable_models(manifest)
    opaque_models = set(manifest[OPAQUE_KEY])
    classes = classify(manifest)

    unknown_models = sorted(
        name
        for name in {*manifest[CLAIMS_KEY], *opaque_models}
        if name not in models
    )
    unknown_claims = sorted(
        (name, field_name)
        for name, fields in manifest[CLAIMS_KEY].items()
        if name in models
        for field_name in fields
        if field_name not in models[name].model_fields
    )

    seen: dict[FieldRef, int] = {}
    for entry in dispositions:
        ref = (entry.qualified_model, entry.field)
        seen[ref] = seen.get(ref, 0) + 1
    duplicates = tuple(sorted(ref for ref, n in seen.items() if n > 1))

    now_claimed: list[FieldDisposition] = []
    unknown: list[FieldDisposition] = []
    not_literal: list[FieldDisposition] = []
    product_unread: list[FieldDisposition] = []
    not_written: list[FieldDisposition] = []
    for entry in dispositions:
        ref = (entry.qualified_model, entry.field)
        cls = models.get(entry.qualified_model)
        if (
            cls is None
            or entry.qualified_model in opaque_models
            or entry.field not in cls.model_fields
        ):
            unknown.append(entry)
            continue
        if ref in classes["read"]:
            now_claimed.append(entry)
        if entry.kind == "structural" and not _is_literal(
            cls.model_fields[entry.field].annotation
        ):
            not_literal.append(entry)
        # One membership test covers both defects the finding names: a claim
        # is only ever recorded against a field its model declares, so a
        # `derives` the model does not declare cannot be in `read` either.
        # The ref is model-qualified — the product is a field of THIS model,
        # not a name some other model happens to have read.
        if (
            entry.kind == "derivation_input"
            and (entry.qualified_model, entry.derives) not in classes["read"]
        ):
            product_unread.append(entry)
        # Only where the model declares the product: a `derives` naming no
        # field is already the finding above, and "nothing derives a field
        # that does not exist" would print a second line about one defect.
        if (
            entry.kind == "derivation_input"
            and entry.derives in cls.model_fields
            and not _writes_derived_field(cls, entry.derives)
        ):
            not_written.append(entry)

    return ConsumptionReport(
        unread_without_disposition=tuple(sorted(classes["unread"] - set(seen))),
        disposition_now_claimed=tuple(now_claimed),
        disposition_of_unknown_field=tuple(unknown),
        duplicate_dispositions=duplicates,
        structural_not_literal=tuple(not_literal),
        derivation_product_unread=tuple(product_unread),
        derivation_not_written=tuple(not_written),
        claim_of_unknown_field=tuple(unknown_claims),
        manifest_names_unknown_model=tuple(unknown_models),
    )
