#!/usr/bin/env python3
"""Build the arguments of an `analitiq-validator` MCP validation tool from files
on disk.

    validation_request.py document <file> <document_kind>
    validation_request.py package <directory> <package_kind>
    validation_request.py workspace <directory>

Prints `{"tool", "arguments"}`; `arguments` is the tool's input, passed verbatim.

A package or workspace request carries every file under the directory whose
name ends with an extension in `VALIDATOR_ALLOWED_EXTENSIONS`, keyed by its
relative path, and leaves out every file or directory whose name starts with
`.` — that is what keeps `.secrets/` out of every request. The server grades
only the keys its location table matches.

Only real directories are walked; a link is never followed. A file the request
would carry but cannot exits non-zero, naming it, rather than being dropped: a
link, an entry that is not a regular file, an unreadable file, a file that is
not UTF-8. So do an unreadable directory and an argument list no mode takes.

Standard library only: the plugin installs nothing.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

VALIDATOR_ALLOWED_EXTENSIONS = [".json"]


class RequestError(ValueError):
    """Something the request cannot carry."""


def _text(path: Path) -> str:
    try:
        if path.is_symlink():
            raise RequestError(f"{path}: a link, never followed")
        if not path.is_file():
            raise RequestError(f"{path}: not a regular file")
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise RequestError(f"{path}: not UTF-8 text") from None
    except OSError as exc:
        raise RequestError(f"{path}: unreadable: {exc.strerror}") from None


def _documents(directory: Path, prefix: str = "") -> dict[str, str]:
    try:
        with os.scandir(directory) as listing:
            entries = sorted(listing, key=lambda entry: entry.name)
    except OSError as exc:
        raise RequestError(f"{directory}: unreadable: {exc.strerror}") from None
    documents: dict[str, str] = {}
    for entry in entries:
        path, key = Path(entry.path), prefix + entry.name
        if entry.name.startswith("."):
            continue
        if entry.is_dir(follow_symlinks=False):
            documents |= _documents(path, f"{key}/")
        elif entry.name.endswith(tuple(VALIDATOR_ALLOWED_EXTENSIONS)):
            documents[key] = _text(path)
    return documents


def _directory(target: str) -> Path:
    path = Path(target)
    if path.is_symlink() or not path.is_dir():
        raise RequestError(f"{target}: not a directory, or a link")
    return path


_USAGE = ("usage: validation_request.py document <file> <document_kind> | "
          "package <directory> <package_kind> | workspace <directory>")


def build(argv: list[str]) -> dict:
    mode, arguments = (argv[0], argv[1:]) if argv else (None, [])
    if mode == "document" and len(arguments) == 2:
        return {"tool": "validate_single_document",
                "arguments": {"document": _text(Path(arguments[0])), "document_kind": arguments[1]}}
    if mode == "package" and len(arguments) == 2:
        return {"tool": "validate_package",
                "arguments": {"package_kind": arguments[1],
                              "documents": _documents(_directory(arguments[0]))}}
    if mode == "workspace" and len(arguments) == 1:
        return {"tool": "validate_workspace",
                "arguments": {"documents": _documents(_directory(arguments[0]))}}
    raise RequestError(_USAGE)


if __name__ == "__main__":
    try:
        print(json.dumps(build(sys.argv[1:]), ensure_ascii=False))
    except RequestError as exc:
        sys.exit(f"validation_request.py: {exc}")
