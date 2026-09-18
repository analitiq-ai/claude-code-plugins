"""Where a validated document sits, and what the checks may read beside it.

A cross-file check reads a document's siblings through a `Location` — a key
inside a `Tree` — never through the filesystem, so one implementation of each
check serves a document found on disk and a package handed over as text.
Navigation (`parent`, `name`, joining a name) is lexical arithmetic on the key;
every question about what is actually there goes to the tree.
"""
from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePath, PurePosixPath
from typing import Iterable, Iterator, Mapping


class Tree(ABC):
    """The reads a cross-file check asks of the tree a document sits in."""

    @abstractmethod
    def is_file(self, key: PurePath) -> bool:
        """Whether a regular file carries `key`."""

    @abstractmethod
    def is_dir(self, key: PurePath) -> bool:
        """Whether a directory carries `key`."""

    @abstractmethod
    def occupied(self, key: PurePath) -> bool:
        """Whether any entry at all carries `key`, including one that cannot be
        followed to anything."""

    @abstractmethod
    def read_text(self, key: PurePath) -> str:
        """The text of the regular file at `key`."""

    @abstractmethod
    def glob(self, key: PurePath, pattern: str) -> Iterable[PurePath]:
        """The entries directly inside `key` whose names match `pattern`, in no
        particular order."""

    @abstractmethod
    def rglob(self, key: PurePath, pattern: str) -> Iterable[PurePath]:
        """The entries anywhere below `key` whose names match `pattern`, in no
        particular order."""

    @abstractmethod
    def ancestor(self, key: PurePath, levels: int) -> PurePath | None:
        """The directory `levels` above `key`, or None when that is outside the
        tree."""


class DiskTree(Tree):
    """The filesystem. A key is a `Path`."""

    def is_file(self, key: Path) -> bool:
        return key.is_file()

    def is_dir(self, key: Path) -> bool:
        return key.is_dir()

    def occupied(self, key: Path) -> bool:
        # `exists()` follows a link, so a dangling one answers False on its own.
        return key.exists() or key.is_symlink()

    def read_text(self, key: Path) -> str:
        return key.read_text()

    def glob(self, key: Path, pattern: str) -> Iterable[Path]:
        return key.glob(pattern)

    def rglob(self, key: Path, pattern: str) -> Iterable[Path]:
        return key.rglob(pattern)

    def ancestor(self, key: Path, levels: int) -> Path:
        # Resolved first: `Path("thing.json").parent.parent` is `.`, so a
        # relative key would stop short of the directory it names. Resolving
        # also follows links, so a linked document is anchored where its
        # target lives, not where it was authored.
        anchor = key.resolve()
        for _ in range(levels):
            anchor = anchor.parent
        return anchor


DISK = DiskTree()


class MemoryTree(Tree):
    """A package handed over as text keyed by package-relative POSIX path.

    Every key is a regular file, and a directory is every path some key sits
    under — the package root included. The keys are those a `DocumentSet`
    (`analitiq.contracts.validation_requests`) admits; this tree re-checks none
    of them.
    """

    def __init__(self, texts: Mapping[str, str]) -> None:
        # Read as a text-mode file is: `\r\n` and a lone `\r` become `\n`.
        # A parse error quotes offsets into what it was handed, so a document
        # read any other way is reported differently from the same file on disk.
        self._texts = {PurePosixPath(key): io.StringIO(text, newline=None).read()
                       for key, text in texts.items()}
        self._dirs = {parent for key in self._texts for parent in key.parents}

    def _entries(self) -> Iterator[PurePosixPath]:
        yield from self._texts
        yield from self._dirs

    def is_file(self, key: PurePath) -> bool:
        return key in self._texts

    def is_dir(self, key: PurePath) -> bool:
        return key in self._dirs

    def occupied(self, key: PurePath) -> bool:
        return self.is_file(key) or self.is_dir(key)

    def read_text(self, key: PurePath) -> str:
        return self._texts[key]

    def glob(self, key: PurePath, pattern: str) -> list[PurePosixPath]:
        return [entry for entry in self._entries()
                if entry != key and entry.parent == key and fnmatchcase(entry.name, pattern)]

    def rglob(self, key: PurePath, pattern: str) -> list[PurePosixPath]:
        return [entry for entry in self._entries()
                if key in entry.parents and fnmatchcase(entry.name, pattern)]

    def ancestor(self, key: PurePath, levels: int) -> PurePath | None:
        parents = key.parents
        return parents[levels - 1] if levels <= len(parents) else None


@dataclass(frozen=True)
class Location:
    """A key inside a tree: where one document sits, and the handle a check
    reads its siblings through."""

    key: PurePath
    tree: Tree

    @property
    def name(self) -> str:
        return self.key.name

    @property
    def parent(self) -> Location:
        return Location(self.key.parent, self.tree)

    def __truediv__(self, name: str) -> Location:
        return Location(self.key / name, self.tree)

    def __lt__(self, other: Location) -> bool:
        return self.key < other.key

    def __str__(self) -> str:
        return str(self.key)

    def is_file(self) -> bool:
        return self.tree.is_file(self.key)

    def is_dir(self) -> bool:
        return self.tree.is_dir(self.key)

    def occupied(self) -> bool:
        return self.tree.occupied(self.key)

    def read_text(self) -> str:
        return self.tree.read_text(self.key)

    def glob(self, pattern: str) -> Iterator[Location]:
        return (Location(key, self.tree)
                for key in self.tree.glob(self.key, _one_name(pattern)))

    def rglob(self, pattern: str) -> Iterator[Location]:
        return (Location(key, self.tree)
                for key in self.tree.rglob(self.key, _one_name(pattern)))

    def ancestor(self, levels: int) -> Location | None:
        if levels < 1:
            raise ValueError(f"levels must be at least 1, got {levels}")
        key = self.tree.ancestor(self.key, levels)
        return None if key is None else Location(key, self.tree)


def _one_name(pattern: str) -> str:
    """`pattern`, refused unless it matches within one name.

    pathlib reads `/` and `**` as crossing directories and the memory tree
    matches names alone, so such a pattern would be answered differently by
    the two trees.
    """
    if "/" in pattern or "**" in pattern:
        raise ValueError(f"pattern must match a single name, got {pattern!r}")
    return pattern


def located(where: Path | Location) -> Location:
    """`where` as a location: a `Path` names a place on disk."""
    return where if isinstance(where, Location) else Location(where, DISK)
