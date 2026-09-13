"""An in-memory stand-in for the slice of `pathlib.Path` the path-anchored
per-kind checks actually call, so `analitiq.validator.document_set` can hand
them a `DocumentSet` instead of a real filesystem without changing any of
those checks.

`connectors.check_coverage`, `connectors.endpoint_filename_findings`,
`connectors._load_json_sibling` and the sibling-`connector.json` lookup inside
`connectors._validate_api_endpoint` all read a document's siblings through
`doc_path`: `.parent`, `__truediv__`, `.is_file()`, `.is_dir()`, `.read_text()`,
`.rglob("*.json")`, `.relative_to()`, `.name`, `.as_posix()`, `.resolve()`, and
ordering (`sorted(...)`). `VirtualPath` implements exactly that subset over
`_VirtualFS`, so none of those checks needs to change to run over a
`DocumentSet`.

`_VirtualFS` normalizes a `DocumentSet` once into layers, kept apart because
they answer different questions:

- `known_keys` — every key whose own key syntax and value TYPE passed
  validation, kept even when turning its value into text later crashed. A
  document that exists but fails to parse still "exists" on a real
  filesystem for a directory listing, a duplicate-id check, or a
  `.is_dir()` test on some OTHER key that happens to be its path prefix —
  `is_file`/`is_dir`/`glob_json` all read this layer, never `texts`, so a
  crashed sibling does not silently vanish from identity/discovery the way
  it would if discovery read post-crash content instead.
- `texts` — the materialized string content of every key whose value could
  actually be turned into text (str as-is, bytes decoded, dict/list
  `json.dumps`-ed). `read_text` reads this layer, and raises the same way
  `Path.read_text()` raises on a directory or a genuinely unreadable file
  when a key is a directory (per `known_keys`) or was never materialized.
- `objects` — the original `dict`/`list` value for a key already given to
  this module as one, read back by `parsed()` instead of re-parsing its own
  `json.dumps` output, so a caller gets the exact value it was handed.
"""
from __future__ import annotations

import json
from typing import Any, Iterator


class _VirtualFS:
    def __init__(self, texts: dict[str, str], objects: dict[str, Any],
                 known_keys: set[str]) -> None:
        self.texts = texts
        self.objects = objects
        self.known_keys = known_keys

    def is_dir(self, key: str) -> bool:
        if key == "":
            return bool(self.known_keys)
        prefix = f"{key}/"
        return any(k.startswith(prefix) for k in self.known_keys)

    def is_file(self, key: str) -> bool:
        return key in self.known_keys and not self.is_dir(key)

    def read_text(self, key: str) -> str:
        if self.is_dir(key):
            raise IsADirectoryError(f"{key!r} is a directory in this document set")
        if key not in self.texts:
            raise OSError(f"{key!r} could not be read as text")
        return self.texts[key]

    def parsed(self, key: str) -> Any:
        if key in self.objects:
            return self.objects[key]
        return json.loads(self.texts[key])

    def materialized(self, key: str) -> bool:
        """Whether `key` has content a document can actually be read from —
        `texts` and `objects` are populated together for every key
        `_normalize_documents` accepts, so a key present in `known_keys` but
        absent from `texts` is exactly one whose materialization itself
        crashed."""
        return key in self.texts

    def glob_json(self, dir_key: str) -> list[str]:
        """Every known key under `dir_key` ending in `.json`, at any depth —
        matching `Path.rglob("*.json")`, the only pattern any caller uses.
        Sorted by path-part tuple, matching `pathlib.Path`'s ordering (which
        compares parts, not the raw string) rather than raw-string order — the
        two disagree whenever a `-` (0x2D) and a `/` (0x2F) compete at the same
        position, e.g. `"a-b.json"` sorts before `"a/z.json"` as a path but
        after it as a string."""
        prefix = f"{dir_key}/" if dir_key else ""
        return sorted(
            (k for k in self.known_keys
             if k != dir_key and k.startswith(prefix) and k.endswith(".json")),
            key=lambda k: k.split("/"),
        )

    def direct_json_children(self, prefix: str) -> list[str]:
        """Every materialized key directly under `prefix` (exactly one further
        path segment) ending in `.json` — the flat, one-level `*.json` listing
        `_connector_endpoint_sets` and `validate_pipeline_tree`'s stream/
        endpoint scans each need, factored once so the filter isn't
        hand-written at every call site."""
        return [
            key for key in sorted(self.known_keys)
            if key.startswith(prefix)
            and "/" not in key[len(prefix):]
            and key.endswith(".json")
            and self.materialized(key)
        ]


class VirtualPath:
    """Duck-types the `pathlib.Path` operations `connectors.py` performs on a
    `doc_path`, backed by a `_VirtualFS` instead of the real filesystem."""

    __slots__ = ("_fs", "_key")

    def __init__(self, fs: _VirtualFS, key: str) -> None:
        self._fs = fs
        self._key = key

    @property
    def name(self) -> str:
        return self._key.rsplit("/", 1)[-1]

    @property
    def parent(self) -> "VirtualPath":
        if "/" not in self._key:
            return VirtualPath(self._fs, "")
        return VirtualPath(self._fs, self._key.rsplit("/", 1)[0])

    def __truediv__(self, other: str) -> "VirtualPath":
        return VirtualPath(self._fs, f"{self._key}/{other}" if self._key else other)

    def is_file(self) -> bool:
        return self._fs.is_file(self._key)

    def is_dir(self) -> bool:
        return self._fs.is_dir(self._key)

    def read_text(self) -> str:
        return self._fs.read_text(self._key)

    def rglob(self, pattern: str) -> Iterator["VirtualPath"]:
        if pattern != "*.json":
            raise NotImplementedError(
                f"VirtualPath.rglob supports only '*.json' (the only pattern any "
                f"caller uses), got {pattern!r}")
        for key in self._fs.glob_json(self._key):
            yield VirtualPath(self._fs, key)

    def relative_to(self, other: "VirtualPath") -> "VirtualPath":
        if self._key == other._key:
            # Matches `pathlib.Path`: `Path('a/b').relative_to('a/b') ==
            # Path('.')`, and `""` is this type's own empty/`.`-equivalent key
            # (see `is_dir`'s and `parent`'s root case).
            return VirtualPath(self._fs, "")
        prefix = f"{other._key}/" if other._key else ""
        if not self._key.startswith(prefix):
            raise ValueError(f"{self._key!r} is not relative to {other._key!r}")
        return VirtualPath(self._fs, self._key[len(prefix):])

    def as_posix(self) -> str:
        return self._key

    def resolve(self) -> "VirtualPath":
        return self

    def __eq__(self, other: object) -> bool:
        return isinstance(other, VirtualPath) and self._fs is other._fs and self._key == other._key

    def __lt__(self, other: "VirtualPath") -> bool:
        # Path-part comparison, matching `pathlib.Path`'s ordering — see
        # `_VirtualFS.glob_json`'s docstring for why raw-string order diverges.
        return self._key.split("/") < other._key.split("/")

    def __hash__(self) -> int:
        return hash((id(self._fs), self._key))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"VirtualPath({self._key!r})"
