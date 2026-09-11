"""A document tree: where a check reads its cross-file facts from.

Every cross-file check — a connector's sibling type maps and endpoint files,
an endpoint's parent connector, a pipeline's streams, connections and
connectors — asks the same few questions of the files around a document: is
there a file at this key, what does it parse to, which `.json` files sit under
this directory. `Tree` is those questions. `MemoryTree` answers them from a
mapping handed over in one call (`validate_tree`); `DiskTree` from the
filesystem (`validate_document(..., doc_path=...)`, the CLI). A check written
against `Tree` grades a tree received over the wire exactly as it grades a
checkout, because the reader is the only thing that differs.

Keys are POSIX relative paths. A `DiskTree` is rooted at the filesystem anchor,
so a document's absolute path is its key and sibling arithmetic is the same
string arithmetic in both trees. A tree of documents has no empty directories:
a directory exists exactly when some file sits under it.

Reading a key answers `(document, None)` or `(None, why)` — for text that does
not parse, a file the reader could not read, or a key the tree does not hold —
and never raises: what a broken or missing sibling costs depends on which check
needed it, so the check phrases the finding.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


def key_problem(key: Any) -> str | None:
    """Why `key` cannot address a tree entry, or None when it can."""
    if not isinstance(key, str) or not key:
        return "empty key"
    if key.startswith("/"):
        return "absolute path"
    if any(segment in ("", ".", "..") for segment in key.split("/")):
        return "'.', '..' or empty segment"
    return None


def parent_key(key: str) -> str | None:
    """The directory holding `key`: `""` for a top-level key, None above that."""
    if not key:
        return None
    directory, _, _ = key.rpartition("/")
    return directory


def join_key(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _parse(text: str | bytes) -> tuple[Any, str | None]:
    """`json.loads`, with a failure returned as its reason. `RecursionError` is
    the one failure the parser raises that is not a `ValueError` — nesting past
    the interpreter's limit — and a document that deep is unreadable the same
    way malformed text is."""
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, str(exc)
    except RecursionError as exc:
        return None, f"{type(exc).__name__}: {exc}"


@dataclass(frozen=True)
class Unreadable:
    """A tree entry for a file that exists but yields no text — a directory
    under a document's name, an undecodable file, a permission error. A reader
    hands it over instead of dropping the key, so the checks still see the file
    is there (its directory still counts) and report the read failure at its
    key, exactly as they report text that does not parse."""

    reason: str


def _by_component(key: str) -> list[str]:
    """Order keys the way `pathlib` orders paths — segment by segment — so a
    tree lists `endpoints/a/b.json` before `endpoints/a.json` whichever store
    it is read from, and a finding order never depends on the reader."""
    return key.split("/")


class Tree:
    """The questions a check may ask of the files around a document."""

    def is_file(self, key: str) -> bool:
        raise NotImplementedError

    def is_dir(self, key: str) -> bool:
        raise NotImplementedError

    def read(self, key: str) -> tuple[Any, str | None]:
        """`(document, None)` for a key that parses, `(None, why)` for one that
        does not or is not there."""
        raise NotImplementedError

    def files(self, directory: str, *, recursive: bool = False) -> list[str]:
        """The `.json` keys under `directory`, in component order — direct
        children only unless `recursive`."""
        raise NotImplementedError

    def locate(self, key: str) -> str:
        """How a message names this key: the key itself, or where it is on disk."""
        raise NotImplementedError

    def at(self, key: str) -> "Location":
        return Location(self, key)


class MemoryTree(Tree):
    """A tree over a `{key: text | Unreadable | document}` mapping. A `str` or
    `bytes` is the file's text and parses on first read; an `Unreadable` reads
    as its reason; anything else is the document already. Parsing is cached, so
    every check that reads a key reads one object — the same as one file read
    once from disk."""

    def __init__(self, documents: Mapping[str, Any]) -> None:
        self._documents = dict(documents)
        self._parsed: dict[str, tuple[Any, str | None]] = {}

    def is_file(self, key: str) -> bool:
        return key in self._documents

    def is_dir(self, key: str) -> bool:
        prefix = f"{key}/"
        return any(k.startswith(prefix) for k in self._documents)

    def read(self, key: str) -> tuple[Any, str | None]:
        if key not in self._documents:
            return None, "no such key"
        if key not in self._parsed:
            value = self._documents[key]
            if isinstance(value, Unreadable):
                self._parsed[key] = (None, value.reason)
            elif isinstance(value, (str, bytes, bytearray)):
                self._parsed[key] = _parse(value)
            else:
                self._parsed[key] = (value, None)
        return self._parsed[key]

    def files(self, directory: str, *, recursive: bool = False) -> list[str]:
        prefix = f"{directory}/"
        found = [
            k for k in self._documents
            if k.startswith(prefix) and k.endswith(".json")
            and (recursive or "/" not in k[len(prefix):])
        ]
        return sorted(found, key=_by_component)

    def locate(self, key: str) -> str:
        return key


class DiskTree(Tree):
    """A tree over the filesystem below `root`, keys relative to it."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        return self._root / key

    def is_file(self, key: str) -> bool:
        return self._path(key).is_file()

    def is_dir(self, key: str) -> bool:
        return self._path(key).is_dir()

    def read(self, key: str) -> tuple[Any, str | None]:
        try:
            text = self._path(key).read_text()
        except (OSError, UnicodeDecodeError) as exc:
            return None, str(exc)
        return _parse(text)

    def files(self, directory: str, *, recursive: bool = False) -> list[str]:
        base = self._path(directory)
        matches = base.rglob("*.json") if recursive else base.glob("*.json")
        found = [join_key(directory, p.relative_to(base).as_posix()) for p in matches]
        return sorted(found, key=_by_component)

    def locate(self, key: str) -> str:
        return str(self._path(key))


@dataclass(frozen=True)
class Location:
    """One document's place in a tree: what the per-kind validators receive
    instead of a filesystem path."""

    tree: Tree
    key: str

    @property
    def name(self) -> str:
        return self.key.rpartition("/")[2]

    @property
    def directory(self) -> str:
        return self.key.rpartition("/")[0]

    @property
    def path(self) -> PurePosixPath:
        return PurePosixPath(self.key)

    def sibling(self, name: str) -> str:
        return join_key(self.directory, name)


def disk_location(doc_path: Path) -> Location:
    """A document on disk as a location: its directory resolved (so a relative
    path, `..`, or a symlinked directory still reaches the siblings), its own
    name kept as authored (the name is what a type map's direction and an
    endpoint's filename gate read, and a resolved symlink would re-grade the
    file under a name the author never wrote)."""
    doc_path = Path(doc_path)
    directory = doc_path.parent.resolve()
    root = Path(directory.anchor)
    key = (directory / doc_path.name).relative_to(root).as_posix()
    return Location(DiskTree(root), key)
