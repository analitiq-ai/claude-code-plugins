"""The structural diff between two rendered versions of one schema.

The diff is the evidence the bump models classify and the value a bump record
is hashed over, so it is deterministic: keys in sorted order, paths joined with
`/`, values as compact sorted-key JSON and never truncated. A truncated value
hides the deciding evidence (a `required` list at the end of an added
subschema) and the models then answer wrong with high confidence.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

# Keywords whose change is documentation only.
DOC_KEYS = frozenset({"description", "title", "examples", "$comment"})
# Keys stamped per publication; they differ between any two versions.
STAMP_KEYS = frozenset({"$id", "version"})
# Keywords whose children are names chosen by the schema author, never
# keywords: a property named `title` is a property, not documentation.
_NAME_MAPS = frozenset({
    "properties", "patternProperties", "$defs", "dependentSchemas", "dependentRequired",
})
# Keywords holding data rather than schemas: a `description` key inside a
# `default` value is part of the value.
_DATA_KEYWORDS = frozenset({"const", "default", "enum", "examples"})


@dataclass(frozen=True)
class Change:
    """One difference: the rendered `line` and the key `path` it sits at."""

    path: tuple[str, ...]
    line: str


def diff(old: dict, new: dict) -> list[Change]:
    """Every difference between `old` and `new`, stamps excluded."""
    changes: list[Change] = []
    _diff(unstamped(old), unstamped(new), (), _Context.SCHEMA, changes)
    return changes


def diff_lines(old: dict, new: dict) -> list[str]:
    return [change.line for change in diff(old, new)]


def diff_sha256(lines: list[str]) -> str:
    """The digest a bump record pins its diff by."""
    canonical = json.dumps(lines, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def unstamped(schema: dict) -> dict:
    return {k: v for k, v in schema.items() if k not in STAMP_KEYS}


class _Context:
    """What the keys of the object being walked are."""

    SCHEMA = "schema"  # JSON Schema keywords
    NAMES = "names"  # author-chosen names
    DATA = "data"  # instance data


def _child_context(context: str, key: str) -> str:
    if context == _Context.DATA:
        return _Context.DATA
    if context == _Context.NAMES:
        return _Context.SCHEMA
    if key in _NAME_MAPS:
        return _Context.NAMES
    if key in _DATA_KEYWORDS:
        return _Context.DATA
    return _Context.SCHEMA


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _emit(changes: list[Change], path: tuple[str, ...], tag: str, rest: str = "") -> None:
    changes.append(Change(path, f"{tag} {'/'.join(path)}{rest}"))


def _is_doc(context: str, key: str) -> bool:
    return context == _Context.SCHEMA and key in DOC_KEYS


def _diff(old: Any, new: Any, path: tuple[str, ...], context: str, changes: list[Change]) -> None:
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        _diff_dicts(old, new, path, context, changes)
    elif isinstance(old, list) and isinstance(new, list):
        _diff_lists(old, new, path, context, changes)
    else:
        _emit(changes, path, "CHANGED", f": {_json(old)} -> {_json(new)}")


def _diff_dicts(old: dict, new: dict, path: tuple[str, ...], context: str, changes: list[Change]) -> None:
    for key in sorted(old.keys() | new.keys()):
        child = (*path, key)
        doc = "DOC-" if _is_doc(context, key) else ""
        if key not in new:
            _emit(changes, child, f"{doc}REMOVED", f" = {_json(old[key])}")
        elif key not in old:
            _emit(changes, child, f"{doc}ADDED", f" = {_json(new[key])}")
        elif doc:
            if old[key] != new[key]:
                _emit(changes, child, "DOC-CHANGED", f": {_json(old[key])} -> {_json(new[key])}")
        else:
            _diff(old[key], new[key], child, _child_context(context, key), changes)


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list))


def _diff_lists(old: list, new: list, path: tuple[str, ...], context: str, changes: list[Change]) -> None:
    if all(map(_is_scalar, old)) and all(map(_is_scalar, new)):
        _diff_scalar_lists(old, new, path, changes)
    elif len(old) == len(new):
        for index, (o, n) in enumerate(zip(old, new)):
            _diff(o, n, (*path, str(index)), context, changes)
    else:
        old_counts, new_counts = Counter(map(_json, old)), Counter(map(_json, new))
        for item in _in_order(old, old_counts - new_counts):
            _emit(changes, path, "ITEM-REMOVED", f": {item}")
        for item in _in_order(new, new_counts - old_counts):
            _emit(changes, path, "ITEM-ADDED", f": {item}")


def _diff_scalar_lists(old: list, new: list, path: tuple[str, ...], changes: list[Change]) -> None:
    old_counts, new_counts = Counter(map(_json, old)), Counter(map(_json, new))
    added = _in_order(new, new_counts - old_counts)
    removed = _in_order(old, old_counts - new_counts)
    if added:
        _emit(changes, path, "LIST-ADDED", f": [{','.join(added)}]")
    if removed:
        _emit(changes, path, "LIST-REMOVED", f": [{','.join(removed)}]")
    if not added and not removed:
        _emit(changes, path, "LIST-REORDERED")


def _in_order(items: list, surplus: Counter) -> list[str]:
    """The members of `surplus`, in the order `items` holds them."""
    remaining = Counter(surplus)
    picked: list[str] = []
    for item in map(_json, items):
        if remaining[item] > 0:
            remaining[item] -= 1
            picked.append(item)
    return picked
