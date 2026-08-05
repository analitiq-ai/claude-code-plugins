"""Prose-completeness lint — the advisory census's missing other half.

`test_advisory_registry.py` verifies the integrity of rules that EXIST; every
one of its checks starts from a registered rule, so an obligation stated in
prose and never registered is invisible to it — the hole the unenforced
pagination `response.body.*` rule shipped through. This suite closes that hole
from the prose side: every field description and model docstring in
``analitiq.contracts`` that states an obligation (``NORMATIVE_PATTERN``) must
resolve to a :class:`ProseObligation` entry binding it to an ``ADV-*`` rule, a
structural mechanism, or an explicit waiver.

The lint is bidirectional so the census cannot rot in either direction: a
normative site with no entry fails (the unenforced-obligation direction), and
an entry whose prose site disappeared or lost its normative language also
fails (the stale direction).
"""
from __future__ import annotations

import importlib
import pkgutil
import re
import sys
from enum import Enum
from typing import get_args

import pytest
from pydantic import BaseModel

import analitiq.contracts
from analitiq.contracts.shared.advisory import all_rules
from analitiq.contracts.shared.advisory_prose import (
    NORMATIVE_PATTERN,
    PROSE_OBLIGATIONS,
    ProseObligation,
)


def _reraise(name):
    # walk_packages swallows a subpackage that fails to import unless onerror
    # raises — and a silently skipped subtree is a silently skipped census.
    raise ImportError(
        f"contract module {name!r} failed to import during the census scan"
    ) from sys.exc_info()[1]


def _contract_classes() -> list[type[BaseModel]]:
    """Every distinct pydantic model defined under ``analitiq.contracts``.

    Walks the whole namespace package rather than a hand-kept module list, so a
    new contract module is scanned the moment it exists.
    """
    for info in pkgutil.walk_packages(
        analitiq.contracts.__path__, prefix="analitiq.contracts.", onerror=_reraise
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
    """The unenforced-obligation direction: prose stating a rule bound to nothing.

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


# --- The census's own texts must not rot ------------------------------------


#: Identifier-shaped tokens inside structural/waiver texts: helper and
#: validator names, snake_case fields/functions/vocabulary values, ALL_CAPS
#: constants, CamelCase class names.
_IDENTIFIER_TOKENS = re.compile(
    r"\b_[a-z][a-z0-9_]*\b"
    r"|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"
    r"|\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"
    r"|\b(?:[A-Z][a-z0-9]+){2,}\b"
)

#: Identifier-shaped tokens that deliberately name nothing in the contract.
_FOREIGN_TOKENS = {
    # the legacy key the Expression union rejects — its absence is the point
    "response_path",
}


def _literal_strings(annotation) -> set[str]:
    """Every string member of any Literal reachable inside an annotation."""
    out: set[str] = set()
    for arg in get_args(annotation):
        if isinstance(arg, str):
            out.add(arg)
        elif arg is not None:
            out |= _literal_strings(arg)
    return out


def test_census_texts_reference_live_names():
    """Structural/waiver texts name validators, constants and classes; a
    rename must fail here instead of leaving the census pointing at nothing.
    (The texts deliberately never restate constraint VALUES — those live in
    the model, the single source — so names are the only thing that can rot.)
    """
    universe: set[str] = set(_FOREIGN_TOKENS)
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("analitiq.contracts"):
            continue
        universe.update(dir(module))
        for name in dir(module):
            obj = getattr(module, name, None)
            if isinstance(obj, type) and issubclass(obj, Enum):
                universe.update(
                    m.value for m in obj if isinstance(m.value, str)
                )
    for cls in _contract_classes():
        universe.update(dir(cls))
        universe.update(cls.model_fields)
        for info in cls.model_fields.values():
            if info.alias:
                universe.add(info.alias)
            universe |= _literal_strings(info.annotation)
    for entry in PROSE_OBLIGATIONS:
        for text in (entry.structural, entry.waiver):
            if not text:
                continue
            unknown = sorted(
                {t for t in _IDENTIFIER_TOKENS.findall(text) if t not in universe}
            )
            assert not unknown, (
                f"{entry.site}: census text names identifiers that do not "
                f"resolve in analitiq.contracts: {unknown}"
            )


def test_prose_obligation_refuses_unbound_and_blank_dispositions():
    """__post_init__ is what makes 'catalogued' mean 'bound' — the site tests
    check only key membership, so an unbound entry would satisfy them."""
    with pytest.raises(ValueError, match="declares nothing"):
        ProseObligation(model="X", field="y")
    with pytest.raises(ValueError, match="empty structural"):
        ProseObligation(model="X", field="y", structural="   ")
    with pytest.raises(ValueError, match="empty waiver"):
        ProseObligation(model="X", waiver=" ")


def test_normative_pattern_tolerates_wrapped_modal_phrases():
    """Docstrings wrap; a two-word modal split across a newline must still
    match, or the first wrapped obligation silently escapes the census."""
    for phrase in (
        "defaults\n    to",
        "may\n  not",
        "is\n  required\n  to",
        "MUST NOT",
    ):
        assert NORMATIVE_PATTERN.search(f"lead {phrase} trail"), (
            f"modal phrase not detected when wrapped: {phrase!r}"
        )
