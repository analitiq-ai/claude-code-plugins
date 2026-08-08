"""Contract-tree introspection for the census suites — one scan, one diff.

:func:`contract_classes` imports the whole ``analitiq.contracts`` namespace
package rather than a hand-kept module list, so a new contract module is
scanned the moment it exists, and a subpackage that fails to import fails the
census instead of being skipped. Both census directions — prose→registry
(``test_prose_census``) and enforcer→registry (``test_rule_registry``) —
walk the tree through it, so neither can develop a blind spot the other lacks.
The prose scan covers ALL pydantic models and ALL ``Enum`` classes defined
under ``analitiq.contracts`` — membership by category, mechanical and
judgment-free. For public enums pydantic publishes the class docstring into
the JSON Schema ``description`` exactly like a model docstring; private helper
enums ride along under the same category rather than requiring a per-class
publishability judgment that would rot. Exception classes and other plain
classes publish nothing and are out of scope, as are enum MEMBER docstrings —
pydantic does not publish those, so an obligation belongs in the enum's
CLASS docstring (``.claude/rules/contract-prose.md``).

:func:`census_report` computes the full live-prose vs census diff in ONE
place, consumed by both ``tests/unit/test_prose_census.py`` and
``scripts/render_prose_census.py`` — the lint and the maintenance tool can
never disagree about what is missing, stale, or re-worded.

This module's top-level imports are stdlib-only (pydantic is imported lazily,
inside the functions that need it): ``prose_obligation`` imports
:class:`SiteKey` from here at module import time, and the census must stay
readable without pulling in pydantic.
"""
from __future__ import annotations

import hashlib
import importlib
import pkgutil
import sys
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


def _reraise(name):
    # walk_packages swallows a subpackage that fails to import unless onerror
    # raises — and a silently skipped subtree is a silently skipped census.
    raise ImportError(
        f"contract module {name!r} failed to import during the census scan"
    ) from sys.exc_info()[1]


def _import_contract_tree() -> None:
    import analitiq.contracts

    for info in pkgutil.walk_packages(
        analitiq.contracts.__path__, prefix="analitiq.contracts.", onerror=_reraise
    ):
        importlib.import_module(info.name)


def _namespace_types(predicate) -> list[type]:
    seen: dict[int, type] = {}
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("analitiq.contracts"):
            continue
        for name in dir(module):
            obj = getattr(module, name, None)
            if predicate(obj):
                seen[id(obj)] = obj
    return list(seen.values())


def contract_classes() -> list[type[BaseModel]]:
    """Every distinct pydantic model defined under ``analitiq.contracts``."""
    _import_contract_tree()
    return _namespace_types(_is_contract_model)


def contract_enums() -> list[type[Enum]]:
    """Every distinct ``Enum`` subclass defined under ``analitiq.contracts``."""
    _import_contract_tree()
    return _namespace_types(_is_contract_enum)


def _is_contract_model(obj: object) -> bool:
    from pydantic import BaseModel

    return (
        isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj is not BaseModel
        and obj.__module__.startswith("analitiq.contracts")
    )


def _is_contract_enum(obj: object) -> bool:
    return (
        isinstance(obj, type)
        and issubclass(obj, Enum)
        and obj is not Enum
        and obj.__module__.startswith("analitiq.contracts")
    )


def closed_members(annotation) -> list[str]:
    """Every member of a closed vocabulary reachable in a field annotation.

    Both spellings count. A closed set is a ``Literal`` in most of the contract
    and an ``Enum`` where its members carry their own docstrings, and which one
    a field uses is a decision about that field, not about the rules over it.
    Reading only ``Literal`` yields nothing for an ``Enum``-typed field — no
    members and no error, so the field's vocabulary is simply absent from
    whatever the caller renders or checks.

    Here rather than beside either caller because both the rendered reference
    and the no-restatement census read the same vocabularies, and a second copy
    of this walk is a second set of fields one of them silently cannot see.

    Declaration order, no repeats: a set would reorder the rendered reference
    on every run and make its drift check flap.
    """
    import typing

    found: list[str] = []
    stack = [annotation]
    while stack:
        current = stack.pop()
        if isinstance(current, type) and issubclass(current, Enum):
            found += [m.value for m in current if isinstance(m.value, str)]
        elif typing.get_origin(current) is typing.Literal:
            found += [a for a in typing.get_args(current) if isinstance(a, str)]
        else:
            stack += list(typing.get_args(current))
    return list(dict.fromkeys(found))


def prose_fingerprint(text: str) -> str:
    """sha256 of the whitespace-normalized prose, first 12 hex chars.

    Normalization (``' '.join(text.split())``) exists so re-wrapping or
    re-indenting a docstring does not trip the census ratchet; any word
    change does.
    """
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class SiteKey:
    """Identity of one prose site — the ONE definition of the site label.

    ``field`` is ``None`` for a class docstring. Every surface that names a
    site (:class:`ProseSite`, ``ProseObligation``, the lint's failure lines,
    the maintenance script's output) formats through :attr:`label`, so the
    rendering can never fork between them.
    """

    model: str
    field: str | None = None

    @property
    def label(self) -> str:
        return f"{self.model}.{self.field}" if self.field else f"{self.model} (docstring)"


@dataclass(frozen=True)
class ProseSite:
    """One live prose site: a described field, or a class's OWN docstring."""

    key: SiteKey
    module: str
    text: str

    @property
    def model(self) -> str:
        return self.key.model

    @property
    def field(self) -> str | None:
        return self.key.field

    @property
    def fingerprint(self) -> str:
        return prose_fingerprint(self.text)

    @property
    def label(self) -> str:
        return self.key.label


def _field_owner(cls: type[BaseModel], field_name: str, info) -> type[BaseModel]:
    """The most-basal ancestor declaring this field with the same description.

    Inherited fields would otherwise surface one site per subclass (seven
    connector kinds × four fields); sites are keyed to the class that owns the
    prose, and a census entry naming a subclass fails as an unknown site.
    """
    owner = cls
    for base in cls.__mro__[1:]:
        if not _is_contract_model(base):
            continue
        base_info = base.model_fields.get(field_name)
        if base_info is not None and base_info.description == info.description:
            owner = base
    return owner


def _scan() -> dict[SiteKey, ProseSite]:
    """All live prose sites, keyed by :class:`SiteKey`.

    Covers every contract model (docstring + described fields) and every
    contract enum's own docstring — membership by category, whether or not a
    private helper's docstring reaches a published schema. Enums contribute
    class-docstring sites only: they declare no fields, and member docstrings
    are not published by pydantic, so an obligation stated on a member is
    censused by nothing and belongs in the class docstring instead.

    The census binds sites by class name (the rule registry's own
    convention), so two distinct prose-carrying classes sharing a bare name
    would silently share one entry. Such a name is therefore module-qualified
    for EVERY class carrying it (``connector.RefExpression`` /
    ``endpoints.RefExpression``) — deterministic, and loud: introducing a
    second class under an already-catalogued name re-keys the first class's
    sites, so its entries go stale and must be re-keyed to the qualified name.
    """
    claims: dict[tuple[int, str | None], tuple[type, str | None, str]] = {}
    for cls in contract_classes():
        # __dict__ (not attribute access) so an inherited docstring is the
        # basal class's site, never re-surfaced per subclass.
        doc = cls.__dict__.get("__doc__")
        if doc:
            claims[(id(cls), None)] = (cls, None, doc)
        for field_name, info in cls.model_fields.items():
            if not info.description:
                continue
            owner = _field_owner(cls, field_name, info)
            claims[(id(owner), field_name)] = (owner, field_name, info.description)
    for cls in contract_enums():
        doc = cls.__dict__.get("__doc__")
        if doc:
            claims[(id(cls), None)] = (cls, None, doc)

    carriers: dict[str, dict[int, type]] = {}
    for cls, _field, _text in claims.values():
        carriers.setdefault(cls.__name__, {})[id(cls)] = cls

    def _label_for(cls: type) -> str:
        if len(carriers[cls.__name__]) == 1:
            return cls.__name__
        tail = cls.__module__.removeprefix("analitiq.contracts.")
        return f"{tail}.{cls.__name__}"

    sites: dict[SiteKey, ProseSite] = {}
    for cls, field, text in claims.values():
        key = SiteKey(model=_label_for(cls), field=field)
        sites[key] = ProseSite(key=key, module=cls.__module__, text=text)
    return sites


def prose_sites() -> dict[SiteKey, str]:
    """ALL live prose sites: ``SiteKey -> prose text``.

    Every model field with a non-empty description, keyed to the class that
    DEFINES the prose, plus a field-less key for every model's and every
    enum's own docstring.
    """
    return {key: site.text for key, site in _scan().items()}


@dataclass(frozen=True)
class HashMismatch:
    """A census entry whose live prose no longer matches its ``prose_hash``."""

    site: ProseSite
    recorded: str


@dataclass(frozen=True)
class CensusReport:
    """The full live-prose vs census diff, in site order.

    ``missing`` — live sites with no census entry; ``stale`` — entry keys
    with no live site; ``hash_mismatches`` — entries whose prose was re-worded
    since their ``prose_hash`` was stamped.

    Every field is a comparison of the census against the live tree, decided
    by set membership or a hash. Whether a site's declared disposition is the
    RIGHT one is not here and cannot be: it needs someone to read the sentence
    (``.claude/rules/contract-prose.md``). A hash mismatch is how that reader
    is summoned.
    """

    missing: tuple[ProseSite, ...]
    stale: tuple[SiteKey, ...]
    hash_mismatches: tuple[HashMismatch, ...]

    @property
    def clean(self) -> bool:
        return not (self.missing or self.stale or self.hash_mismatches)


def _site_order(key: SiteKey) -> tuple[str, str]:
    return (key.model, key.field or "")


def census_report(live=None, census=None) -> CensusReport:
    """The live-prose vs census diff.

    ``live`` (``SiteKey -> ProseSite``) and ``census`` (an iterable of
    ``ProseObligation``) default to the real scan and the real census; the
    parameters exist so the detectors themselves are testable with synthetic
    inputs — a diff only ever asserted empty is a diff nobody has proven can
    go non-empty.
    """
    if live is None:
        live = _scan()
    if census is None:
        from analitiq.contracts.shared.prose_census import PROSE_OBLIGATIONS

        census = PROSE_OBLIGATIONS
    entries = {entry.key: entry for entry in census}

    missing = tuple(
        live[key] for key in sorted(live, key=_site_order) if key not in entries
    )
    stale = tuple(sorted((key for key in entries if key not in live), key=_site_order))
    mismatches: list[HashMismatch] = []
    for key in sorted(entries, key=_site_order):
        site = live.get(key)
        if site is None:
            continue
        entry = entries[key]
        if entry.prose_hash != site.fingerprint:
            mismatches.append(HashMismatch(site=site, recorded=entry.prose_hash))
    return CensusReport(
        missing=missing, stale=stale, hash_mismatches=tuple(mismatches)
    )
