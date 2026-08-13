"""Where the contract's published prose lives, and what each site is bound to.

The census walks the contract tree for every sentence the package publishes —
each pydantic field description, model docstring and ``Enum`` class docstring
under ``analitiq.contracts`` — and diffs that live set against the entries in
:mod:`census`. Both consumers assert on :func:`census_report`, so the lint and
the maintenance script can never disagree about what is missing, stale, or
re-worded.

This module and the entries beside it are maintenance machinery for this repo,
not part of the contract, which is why they sit outside the wheel: a consumer
of `analitiq-contract-models` reads the schemas, never the catalogue of how
their wording is kept honest. The generic tree walk they are built on —
:func:`~analitiq.contracts.shared.introspect.contract_classes` and its
siblings — does ship, because it describes the contract rather than this
repo's process.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from analitiq.contracts.shared.introspect import (
    _is_contract_model,
    contract_classes,
    contract_enums,
)

# Re-exported: `SiteKey` is defined in `census.keys` so `census.obligation` can
# reach it without importing this module, which is what used to close the
# import cycle. Every existing reader imports it from here, and this module is
# still where a site is produced, so the name stays available at both spellings.
from census.keys import SiteKey

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = [
    "CensusReport",
    "HashMismatch",
    "ProseSite",
    "SiteKey",
    "census_report",
    "prose_fingerprint",
    "prose_sites",
]


def prose_fingerprint(text: str) -> str:
    """sha256 of the whitespace-normalized prose, first 12 hex chars.

    Normalization (``' '.join(text.split())``) exists so re-wrapping or
    re-indenting a docstring does not trip the census ratchet; any word
    change does.
    """
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


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
        from census import PROSE_OBLIGATIONS

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
