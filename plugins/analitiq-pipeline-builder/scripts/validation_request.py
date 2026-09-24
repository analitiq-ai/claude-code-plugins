#!/usr/bin/env python3
"""Build the arguments of an `analitiq-validator` MCP validation tool from files
on disk, selecting a package's files only by its published location table.

    validation_request.py document <file> <document_kind>
    validation_request.py package <directory> <package_kind>
    validation_request.py workspace <directory> [<pipeline-directory-name>]

Prints `{"tool", "arguments", "left_out"}`. `arguments` is the tool's input,
passed verbatim; `left_out` is the caller's to report.

An argument that names nothing exits non-zero rather than narrowing the
request: an unknown mode or arity, a target that is missing, linked or not
UTF-8 text, or a pipeline the workspace table does not locate, has no
unlinked directory, or whose directory holds no document its package requires.

Under a package or workspace directory, an entry is in scope when a location
the table does not mark `x-secret` could hold it — for a directory, some key
beneath it; naming a pipeline puts every other pipeline's directory out of
scope. Out of scope is skipped silently and never walked into. In scope:

| entry | outcome |
|---|---|
| located regular file, readable UTF-8 text | submitted |
| located entry that is a link, not a regular file (a directory included), unreadable, or not UTF-8 | `left_out`, never walked into |
| unlocated directory that is a link (never followed) or unreadable | `left_out` |

A workspace request carries every in-scope package, so a named pipeline's
references resolve against what the project holds.

Standard library only: the plugin installs nothing.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

SCHEMA_HOST = "https://schemas.analitiq.ai"
# The end anchor every published location pattern carries. A workspace pattern
# whose key ends in `/` names a package's own directory, so without the anchor
# it matches that directory as a prefix of the keys inside it.
_END = r"(?![\s\S])"

Fetch = Callable[[str], dict]


class RequestError(ValueError):
    """An argument that names nothing a request could carry."""


def fetch_schema(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def _segments(pattern: str) -> list[str]:
    """A location pattern split at each `/` outside a group or class, so a
    directory can be tested one path segment at a time."""
    body = pattern.removeprefix("^").removesuffix(_END)
    segments, current, depth, in_class, escaped = [], "", 0, False, False
    for char in body:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char in "()":
            depth += 1 if char == "(" else -1
        elif char == "/" and depth == 0:
            segments.append(current)
            current = ""
            continue
        current += char
    return [*segments, current]


class _Table:
    """One package's or the workspace's non-secret locations."""

    def __init__(self, schema: dict):
        self.locations = [pattern for pattern, entry in schema["patternProperties"].items()
                          if not entry.get("x-secret")]
        self.refs = {pattern: entry.get("$ref") for pattern, entry in schema["patternProperties"].items()}
        self.required = schema.get("required", [])

    def locates(self, key: str) -> bool:
        return any(re.match(pattern, key) for pattern in self.locations)

    def reaches(self, directory: str) -> bool:
        parts = directory.split("/")
        for pattern in self.locations:
            segments = _segments(pattern)
            if len(segments) > len(parts) and all(
                    re.fullmatch(segment, part) for segment, part in zip(segments, parts)):
                return True
        return False


class _Scope:
    """Whether a key under the root is in scope: `file` for a document key,
    `directory` for one to walk into."""

    def __init__(self, table: _Table, fetch: Fetch, pipeline: str | None = None):
        self.table = table
        self.pipeline = pipeline
        # A workspace location ending in `/` is a package's own directory; the
        # keys inside it are located by that package's table.
        self.packages = {pattern.removesuffix(_END): _Table(fetch(table.refs[pattern]))
                         for pattern in table.locations if pattern.endswith(f"/{_END}")}

    def _other_pipeline(self, parts: list[str], is_directory: bool) -> bool:
        inside = len(parts) >= (2 if is_directory else 3)
        return (self.pipeline is not None and parts[0] == "pipelines" and inside
                and parts[1] != self.pipeline)

    def file(self, key: str) -> bool:
        if self._other_pipeline(key.split("/"), False):
            return False
        for prefix, package in self.packages.items():
            held = re.match(prefix, key)
            if held:
                return package.locates(key[held.end():])
        return self.table.locates(key)

    def package_at(self, directory: str) -> _Table:
        """The table of the package whose own directory is `directory`/."""
        return next(package for prefix, package in self.packages.items()
                    if re.match(f"{prefix}{_END}", f"{directory}/"))

    def directory(self, key: str) -> bool:
        if self._other_pipeline(key.split("/"), True):
            return False
        for prefix, package in self.packages.items():
            held = re.match(prefix, f"{key}/")
            if held:
                inner = key[held.end():]
                return not inner or package.reaches(inner)
        return self.table.reaches(key)


class _LeftOut(Exception):
    """Why a file in scope is not submitted."""


def _read(path: Path) -> str:
    if path.is_symlink():
        raise _LeftOut("a link, never followed")
    if not path.is_file():
        raise _LeftOut("not a regular file")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise _LeftOut("not UTF-8 text") from None
    except OSError as exc:
        raise _LeftOut(f"unreadable: {exc.strerror}") from None


def _collect(root: Path, scope: _Scope) -> tuple[dict[str, str], list[dict]]:
    documents: dict[str, str] = {}
    left_out: list[dict] = []

    def key_of(path: str) -> str:
        return Path(path).relative_to(root).as_posix()

    def unreadable(exc: OSError) -> None:
        key = key_of(exc.filename)
        if key == ".":
            raise RequestError(f"{root}: unreadable: {exc.strerror}")
        left_out.append({"key": f"{key}/", "reason": f"unreadable: {exc.strerror}"})

    for directory, subdirectories, names in os.walk(root, onerror=unreadable):
        walked = []
        for name in sorted([*subdirectories, *names]):
            path = Path(directory, name)
            key = key_of(path)
            # A document location outranks directory reach: whatever sits
            # there is the document, never a directory to walk into.
            if scope.file(key):
                try:
                    documents[key] = _read(path)
                except _LeftOut as exc:
                    left_out.append({"key": key, "reason": str(exc)})
            elif name in subdirectories and scope.directory(key):
                if path.is_symlink():
                    left_out.append({"key": f"{key}/", "reason": "a link, never followed"})
                else:
                    walked.append(name)
        subdirectories[:] = walked
    return documents, left_out


def _accounted_for(key: str, documents: dict[str, str], left_out: list[dict]) -> bool:
    """Whether `key` is carried or reported; a left-out key ending in `/`
    reports every key beneath it."""
    return key in documents or any(
        entry["key"] == key or (entry["key"].endswith("/") and key.startswith(entry["key"]))
        for entry in left_out)


def _directory(target: str) -> Path:
    path = Path(target)
    if path.is_symlink() or not path.is_dir():
        raise RequestError(f"{target}: not a directory, or a link")
    return path


def package_documents(directory: str, package_kind: str,
                      fetch: Fetch = fetch_schema) -> tuple[dict[str, str], list[dict]]:
    table = _Table(fetch(f"{SCHEMA_HOST}/{package_kind}-package/latest.json"))
    return _collect(_directory(directory), _Scope(table, fetch))


def workspace_documents(directory: str, pipeline: str | None,
                        fetch: Fetch = fetch_schema) -> tuple[dict[str, str], list[dict]]:
    root = _directory(directory)
    table = _Table(fetch(f"{SCHEMA_HOST}/workspace/latest.json"))
    scope = _Scope(table, fetch, pipeline)
    if pipeline is None:
        return _collect(root, scope)
    package = f"pipelines/{pipeline}"
    held = root / package
    if not table.locates(f"{package}/") or held.is_symlink() or not held.is_dir():
        raise RequestError(f"{pipeline}: no pipeline directory under {root / 'pipelines'}")
    documents, left_out = _collect(root, scope)
    missing = [key for key in scope.package_at(package).required
               if not _accounted_for(f"{package}/{key}", documents, left_out)]
    if missing:
        raise RequestError(f"{held}: holds no {', '.join(missing)}")
    return documents, left_out


def _document_text(target: str) -> str:
    try:
        return _read(Path(target))
    except _LeftOut as exc:
        raise RequestError(f"{target}: {exc}") from None


_USAGE = ("usage: validation_request.py document <file> <document_kind> | "
          "package <directory> <package_kind> | workspace <directory> [<pipeline>]")


def build(argv: list[str], fetch: Fetch = fetch_schema) -> dict:
    mode, arguments = (argv[0], argv[1:]) if argv else (None, [])
    if mode == "document" and len(arguments) == 2:
        return {"tool": "validate_single_document",
                "arguments": {"document": _document_text(arguments[0]), "document_kind": arguments[1]},
                "left_out": []}
    if mode == "package" and len(arguments) == 2:
        documents, left_out = package_documents(arguments[0], arguments[1], fetch)
        return {"tool": "validate_package",
                "arguments": {"package_kind": arguments[1], "documents": documents},
                "left_out": left_out}
    if mode == "workspace" and len(arguments) in (1, 2):
        documents, left_out = workspace_documents(arguments[0], arguments[1] if len(arguments) == 2 else None, fetch)
        return {"tool": "validate_workspace", "arguments": {"documents": documents}, "left_out": left_out}
    raise RequestError(_USAGE)


if __name__ == "__main__":
    try:
        print(json.dumps(build(sys.argv[1:]), ensure_ascii=False))
    except (RequestError, OSError) as exc:
        # OSError covers a location table the schema host would not serve.
        sys.exit(f"validation_request.py: {exc}")
