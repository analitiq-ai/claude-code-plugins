"""Where a validated document sits, and what the checks may read beside it.

A cross-file check reads a document's siblings through a `Location` — a key
inside a `Tree` — never through the filesystem directly.
Navigation (`parent`, `name`, joining a name) is lexical arithmetic on the key;
every question a check asks about what is actually there goes to the tree.
"""
from __future__ import annotations

import errno
import os
import posixpath
import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePath
from typing import Iterable, Iterator
from urllib.parse import quote


class Tree(ABC):
    """The reads a cross-file check asks of the tree a document sits in."""

    @abstractmethod
    def is_file(self, key: PurePath) -> bool:
        """Whether a regular file carries `key`. Raises `OSError` where the
        lookup is refused, which says nothing about what is there."""

    @abstractmethod
    def is_dir(self, key: PurePath) -> bool:
        """Whether a directory carries `key`. Raises `OSError` where the
        lookup is refused, which says nothing about what is there."""

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
        directory below it, cannot be listed, or where what an entry below it
        is cannot be looked up."""


class DiskTree(Tree):
    """The filesystem. A key is a `Path`."""

    # Not `Path.is_file`/`Path.is_dir`: which errors they answer False for
    # differs by interpreter, and some answer False for a refused lookup.

    def is_file(self, key: Path) -> bool:
        mode = _mode(key)
        return mode is not None and stat.S_ISREG(mode)

    def is_dir(self, key: Path) -> bool:
        mode = _mode(key)
        return mode is not None and stat.S_ISDIR(mode)

    def read_text(self, key: Path) -> str:
        # Not `Path.read_text`: it decodes with the host's locale encoding and
        # translates newlines, so the parser would see other text than an
        # in-memory caller hands over for the same file.
        return key.read_bytes().decode("utf-8")

    # Not `Path.glob`/`Path.rglob`: they drop a directory the kernel will not
    # list, which reads it as empty. `fnmatch` applies the platform's case
    # rule, as they do.

    def glob(self, key: Path, pattern: str) -> Iterable[Path]:
        if not self.is_dir(key):
            return []
        with os.scandir(key) as entries:
            return [key / entry.name for entry in entries if fnmatch(entry.name, pattern)]

    def rglob(self, key: Path, pattern: str) -> Iterable[Path]:
        # Never descends into a linked directory below `key` (a linked `key` is
        # walked), and must not: through a loop of links, every file below it
        # would be reported again at each turn. Not `os.walk`: an entry it
        # cannot classify it takes for no directory, and skips what is below.
        if not self.is_dir(key):
            return []
        found: list[Path] = []
        pending = [key]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    if fnmatch(entry.name, pattern):
                        found.append(Path(entry.path))
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
        return found


def _mode(key: Path) -> int | None:
    """The mode of what `key` leads to, `None` where it leads to nothing."""
    try:
        return os.stat(key).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None
        raise


DISK = DiskTree()


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

    def read_text(self) -> str:
        return self.tree.read_text(self.key)

    def glob(self, pattern: str) -> Iterator[Location]:
        return (Location(key, self.tree)
                for key in self.tree.glob(self.key, _one_name(pattern)))

    def rglob(self, pattern: str) -> Iterator[Location]:
        return (Location(key, self.tree)
                for key in self.tree.rglob(self.key, _one_name(pattern)))


def reference(target: Location, *, seen_from: Location) -> str:
    """Where `target` sits, read from the directory holding `seen_from`."""
    return relative_reference(
        posixpath.relpath(target.key.as_posix(), seen_from.key.parent.as_posix()))


def relative_reference(path: str) -> str:
    """The POSIX `path` as the relative reference RFC 3986 spells it:
    percent-encoded, so a `#` in a name cannot end the reference early and a
    `:` in its first segment cannot read as a scheme."""
    return quote(path)


def _one_name(pattern: str) -> str:
    """`pattern`, refused unless it matches within one name.

    Both trees match one entry name at a time, so a pattern written to cross
    names would match nothing, or `**` would match as `*`.
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
