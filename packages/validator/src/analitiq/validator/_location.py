"""Where a validated document sits, and what the checks may read beside it.

A cross-file check reads a document's siblings through a `Location` — a key
inside a `Tree` — never through the filesystem, so one implementation of each
check serves a document found on disk and a package handed over as text.
Navigation (`parent`, `name`, joining a name) is lexical arithmetic on the key;
every question a check asks about what is actually there goes to the tree.
"""
from __future__ import annotations

import io
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from fnmatch import fnmatch, fnmatchcase
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
        particular order; none where `key` is no directory. Raises `OSError`
        where `key` is a directory that cannot be listed."""

    @abstractmethod
    def rglob(self, key: PurePath, pattern: str) -> Iterable[PurePath]:
        """The entries anywhere below `key` whose names match `pattern`, never
        from inside a linked directory below it, in no particular order; none
        where `key` is no directory. Raises `OSError` where `key`, or a
        directory below it, cannot be listed."""


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

    # Not `Path.glob`/`Path.rglob`: they drop a directory the kernel will not
    # list, which reads it as empty. `fnmatch` applies the platform's case
    # rule, as they do.

    def glob(self, key: Path, pattern: str) -> Iterable[Path]:
        if not key.is_dir():
            return []
        with os.scandir(key) as entries:
            return [key / entry.name for entry in entries if fnmatch(entry.name, pattern)]

    def rglob(self, key: Path, pattern: str) -> Iterable[Path]:
        # Never descends into a linked directory below `key` (a linked `key` is
        # walked), and must not: through a loop of links, every file below it
        # would be reported again at each turn.
        if not key.is_dir():
            return []
        return [Path(parent) / name
                for parent, dirs, files in os.walk(key, onerror=_refuse)
                for name in dirs + files if fnmatch(name, pattern)]


def _refuse(error: OSError) -> None:
    raise error


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
    """`where` as a location: a `Path` names a place on disk.

    A link stands where it is, so a linked document's siblings are read in the
    directory holding the link, not where its target lives. Absolute, so that
    `parent` of a relative path names the directory it sits in rather than
    stopping at `.`.

    Raises the kernel's `OSError` where it cannot look up a `Path` up to its
    last `..`, and `ValueError` where that lookup lands elsewhere than the
    names spell: such a path has no location to grade.
    """
    if isinstance(where, Location):
        return where
    return Location(_collapsed(Path(where).absolute()), DISK)


def _collapsed(path: Path) -> Path:
    """`path` with each `..` collapsed against the name written before it.

    POSIX steps up from where a link leads, so a read of `link/..` can open a
    directory other than the one the names spell, and the document would be
    read from one directory while its siblings are read from another. The
    kernel is asked whether the two agree, up to the last `..`, and the path
    is refused where they do not; a lookup it cannot make is refused for its
    own reason.
    """
    if ".." not in path.parts:
        return path
    last = len(path.parts) - path.parts[::-1].index("..")
    prefix = Path(*path.parts[:last])
    read = os.stat(prefix)
    try:
        agree = os.path.samestat(read, os.stat(os.path.normpath(prefix)))
    except OSError:
        agree = False
    if not agree:
        raise ValueError(
            f"{path}: a `..` steps out of a link, so reading this path opens a different "
            f"directory than its names spell; pass the path without the `..`")
    return Path(os.path.normpath(path))
