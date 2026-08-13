"""Contract-tree introspection — the one walk of ``analitiq.contracts``.

:func:`contract_classes` imports the whole ``analitiq.contracts`` namespace
package rather than a hand-kept module list, so a new contract module is
scanned the moment it exists, and a subpackage that fails to import fails the
scan instead of being skipped. Every survey of the tree walks through here, so
none can develop a blind spot the others lack: the enforcer census
(``test_rule_registry``), the strict-numeric and immutability policies, the
rendered rule reference, and — from outside the package — this repo's prose
census, which reads these functions to find the sentences it catalogues.

The walk covers ALL pydantic models and ALL ``Enum`` classes defined under
``analitiq.contracts`` — membership by category, mechanical and
judgment-free. For public enums pydantic publishes the class docstring into
the JSON Schema ``description`` exactly like a model docstring; private helper
enums ride along under the same category rather than requiring a per-class
publishability judgment that would rot. Exception classes and other plain
classes publish nothing and are out of scope, as are enum MEMBER docstrings —
pydantic does not publish those, so an obligation belongs in the enum's
CLASS docstring (``.claude/rules/contract-prose.md``).

What this module does NOT hold is the prose census itself — the catalogue of
those sentences and the live-vs-census diff. That is how this repo keeps its
own wording honest rather than anything the contract states, so it lives at
the repo root (``census/``) and does not ship in the wheel. Only the generic
walk is here, because it describes the contract.

This module's top-level imports are stdlib-only; pydantic is imported lazily,
inside the functions that need it.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
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


def contract_vocabularies() -> dict[str, list[str]]:
    """Every closed vocabulary the contract declares, keyed ``Model.field``.

    Derived from the tree, never enumerated: a survey that decides what a
    document may not restate has to see every vocabulary the contract owns, and
    a hand-kept list of them can only ever cover the ones somebody remembered.
    A field the contract closes tomorrow joins this map the moment it exists,
    which is the property a curated list cannot have.

    Single-member vocabularies are included. They are a closed set like any
    other, and whether one carries enough signal to act on is the caller's
    judgment — a survey that dropped them would be making that call for every
    caller at once, invisibly.

    Fields are keyed by the Python attribute, not the wire alias: this answers
    "what does the contract declare", and the alias is a separate question the
    caller resolves off the model when it needs the authored spelling.

    Two fields may legitimately carry the same members — a param's request-input
    type and a connection input's value type are different contracts over one
    member set — so callers that care about the set rather than the field group
    these themselves rather than getting a deduplicated map they cannot undo.
    """
    return {
        f"{cls.__name__}.{name}": members
        for cls in contract_classes()
        for name, info in cls.model_fields.items()
        if (members := closed_members(info.annotation))
    }
