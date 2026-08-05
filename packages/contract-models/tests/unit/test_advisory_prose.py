"""Prose-completeness lint — the advisory census's missing other half (issue #127).

`test_advisory_registry.py` verifies the integrity of rules that EXIST; every
one of its checks starts from a registered rule, so an obligation stated in
prose and never registered is invisible to it (that is how #123 shipped). This
suite closes that hole from the prose side: every field description and model
docstring in ``analitiq.contracts`` that states an obligation — the modal set
from #127 — must resolve to a :class:`ProseObligation` entry binding it to an
``ADV-*`` rule, a structural mechanism, or an explicit waiver.

The lint is bidirectional so the census cannot rot in either direction: a
normative site with no entry fails (the #123 direction), and an entry whose
prose site disappeared or lost its normative language also fails (the stale
direction).
"""
from __future__ import annotations

import importlib
import pkgutil
import sys

from pydantic import BaseModel

import analitiq.contracts
from analitiq.contracts.shared.advisory import all_rules
from analitiq.contracts.shared.advisory_prose import (
    NORMATIVE_PATTERN,
    PROSE_OBLIGATIONS,
)


def _contract_classes() -> list[type[BaseModel]]:
    """Every distinct pydantic model defined under ``analitiq.contracts``.

    Walks the whole namespace package rather than a hand-kept module list, so a
    new contract module is scanned the moment it exists.
    """
    for info in pkgutil.walk_packages(
        analitiq.contracts.__path__, prefix="analitiq.contracts."
    ):
        importlib.import_module(info.name)
    seen: dict[int, type[BaseModel]] = {}
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("analitiq.contracts"):
            continue
        for name in dir(module):
            obj = getattr(module, name, None)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__.startswith("analitiq.contracts")
            ):
                seen[id(obj)] = obj
    return list(seen.values())


def _is_contract_model(obj: object) -> bool:
    return (
        isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj is not BaseModel
        and obj.__module__.startswith("analitiq.contracts")
    )


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


def _modal_words(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in NORMATIVE_PATTERN.finditer(text)})


def _normative_sites() -> dict[tuple[str, str | None], list[str]]:
    """(model, field) -> matched modal words; field=None is the class docstring."""
    sites: dict[tuple[str, str | None], list[str]] = {}
    owners: dict[tuple[str, str | None], type[BaseModel]] = {}
    for cls in _contract_classes():
        doc = cls.__dict__.get("__doc__")
        if doc and NORMATIVE_PATTERN.search(doc):
            _claim(sites, owners, (cls.__name__, None), cls, _modal_words(doc))
        for field_name, info in cls.model_fields.items():
            if not info.description or not NORMATIVE_PATTERN.search(info.description):
                continue
            owner = _field_owner(cls, field_name, info)
            _claim(
                sites,
                owners,
                (owner.__name__, field_name),
                owner,
                _modal_words(info.description),
            )
    return sites


def _claim(sites, owners, key, cls, words) -> None:
    """Register a site, refusing a same-named but distinct class: the census
    binds by class NAME (the advisory registry's own convention), so two
    different normative classes sharing a name would silently share one entry."""
    prior = owners.get(key)
    assert prior is None or prior is cls, (
        f"two distinct classes both named {key[0]!r} carry normative prose "
        f"({prior.__module__} and {cls.__module__}); rename one — the census "
        "binds sites by class name"
    )
    owners[key] = cls
    sites[key] = words


SITES = _normative_sites()
CENSUS = {(o.model, o.field): o for o in PROSE_OBLIGATIONS}


def test_census_has_no_duplicate_sites():
    keys = [(o.model, o.field) for o in PROSE_OBLIGATIONS]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"duplicate census entries: {dupes}"


def test_every_normative_site_is_catalogued():
    """The #123 direction: an obligation stated in prose and bound to nothing.

    Fix by binding the new prose in ``advisory_prose.PROSE_OBLIGATIONS`` — to
    the ``ADV-*`` rule enforcing it, to the structural mechanism carrying it,
    or to a waiver saying why it is not mechanisable. Rewording the prose to
    drop the modal language is the other honest exit.
    """
    missing = {
        k: SITES[k]
        for k in sorted(SITES, key=lambda k: (k[0], k[1] or ""))
        if k not in CENSUS
    }
    lines = [
        f"  {model}.{field or '(docstring)'}: modal words {words}"
        for (model, field), words in missing.items()
    ]
    assert not missing, (
        "normative prose with no census entry — bind each site in "
        "analitiq.contracts.shared.advisory_prose.PROSE_OBLIGATIONS "
        "(sites are keyed to the class that DEFINES the prose, not a subclass):\n"
        + "\n".join(lines)
    )


def test_no_stale_census_entries():
    """The rot direction: an entry whose prose no longer states an obligation.

    Fires when the site's class or field was removed/renamed, when the prose
    was reworded below the modal threshold, or when an entry names an
    inheriting subclass instead of the class that defines the prose. Remove or
    re-key the entry — a census carrying dead entries stops being reviewable.
    """
    stale = sorted(
        (k for k in CENSUS if k not in SITES), key=lambda k: (k[0], k[1] or "")
    )
    lines = [f"  {model}.{field or '(docstring)'}" for model, field in stale]
    assert not stale, (
        "census entries with no matching normative prose site:\n" + "\n".join(lines)
    )


def test_census_rule_ids_resolve():
    known = {r.id for r in all_rules()}
    for entry in PROSE_OBLIGATIONS:
        unknown = sorted(set(entry.rule_ids) - known)
        assert not unknown, (
            f"{entry.site}: rule ids not in the advisory registry: {unknown}"
        )
