"""Shared contract-tree walker for the census suites.

Both census directions — prose→registry (`test_advisory_prose`) and
enforcer→registry (`test_advisory_registry`) — must see EVERY contract model,
or a class outside their view is a silent hole. This walker imports the whole
``analitiq.contracts`` namespace package rather than a hand-kept module list,
so a new contract module is scanned the moment it exists, and a subpackage
that fails to import fails the suite instead of being skipped.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys

from pydantic import BaseModel

import analitiq.contracts


def _reraise(name):
    # walk_packages swallows a subpackage that fails to import unless onerror
    # raises — and a silently skipped subtree is a silently skipped census.
    raise ImportError(
        f"contract module {name!r} failed to import during the census scan"
    ) from sys.exc_info()[1]


def contract_classes() -> list[type[BaseModel]]:
    """Every distinct pydantic model defined under ``analitiq.contracts``."""
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
