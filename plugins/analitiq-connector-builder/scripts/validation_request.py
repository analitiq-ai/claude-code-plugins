#!/usr/bin/env python3
"""Build the arguments of an `analitiq-validator` MCP validation tool from files
on disk, selecting a package's files only by its published location table.

    validation_request.py document <path> <document_kind>
    validation_request.py package <directory> <package_kind>
    validation_request.py workspace <directory> [<pipeline-directory-name>]

Prints `{"tool", "arguments", "left_out"}`. `arguments` is the tool's input,
passed verbatim. `left_out` lists what was not submitted and is the
caller's to report: a file at a location that is a link or is not UTF-8 text,
and every linked directory, since documents behind it are never read. A file at
no location, or at a location the table marks `x-secret`, is left out silently:
the first is not a document, the second never leaves the machine.

A workspace request naming a pipeline carries that pipeline's package and every
package outside `pipelines/`, so the pipeline's references resolve against what
the project holds; naming none, it carries every package.

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


def fetch_schema(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def _location_tables(fetch: Fetch) -> Callable[[str], dict[str, dict]]:
    tables: dict[str, dict[str, dict]] = {}

    def table(url: str) -> dict[str, dict]:
        if url not in tables:
            tables[url] = fetch(url)["patternProperties"]
        return tables[url]
    return table


def _entry_at(key: str, locations: dict[str, dict]) -> dict | None:
    for pattern, entry in locations.items():
        if re.match(pattern, key):
            return entry
    return None


def _files(root: Path, left_out: list[dict]):
    """Every file under `root` as (key, path), linked files included. A linked
    directory is never walked into, so it is reported instead."""
    for directory, subdirectories, names in os.walk(root):
        for name in sorted(subdirectories):
            if Path(directory, name).is_symlink():
                left_out.append({"key": Path(directory, name).relative_to(root).as_posix(),
                                 "reason": "a linked directory, never followed"})
        for name in sorted(names):
            path = Path(directory, name)
            yield path.relative_to(root).as_posix(), path


def _read(key: str, path: Path, documents: dict[str, str], left_out: list[dict]) -> None:
    if path.is_symlink():
        left_out.append({"key": key, "reason": "a link, never followed"})
        return
    try:
        documents[key] = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        left_out.append({"key": key, "reason": "not UTF-8 text"})


def package_documents(directory: Path, package_kind: str,
                      fetch: Fetch = fetch_schema) -> tuple[dict[str, str], list[dict]]:
    locations = _location_tables(fetch)(f"{SCHEMA_HOST}/{package_kind}-package/latest.json")
    documents: dict[str, str] = {}
    left_out: list[dict] = []
    for key, path in _files(directory, left_out):
        entry = _entry_at(key, locations)
        if entry is not None and not entry.get("x-secret"):
            _read(key, path, documents, left_out)
    return documents, left_out


def workspace_documents(directory: Path, pipeline: str | None,
                        fetch: Fetch = fetch_schema) -> tuple[dict[str, str], list[dict]]:
    table = _location_tables(fetch)
    workspace = table(f"{SCHEMA_HOST}/workspace/latest.json")
    packages = {pattern.removesuffix(_END): entry for pattern, entry in workspace.items()
                if pattern.endswith(f"/{_END}")}
    documents: dict[str, str] = {}
    left_out: list[dict] = []
    for key, path in _files(directory, left_out):
        if pipeline and key.startswith("pipelines/") and not key.startswith(f"pipelines/{pipeline}/"):
            continue
        for prefix, entry in packages.items():
            held = re.match(prefix, key)
            if held:
                inner = _entry_at(key[held.end():], table(entry["$ref"]))
                break
        else:
            inner = _entry_at(key, workspace)
        if inner is not None and not inner.get("x-secret"):
            _read(key, path, documents, left_out)
    return documents, left_out


def build(argv: list[str], fetch: Fetch = fetch_schema) -> dict:
    mode, target = argv[0], Path(argv[1])
    if mode == "document":
        return {"tool": "validate_single_document",
                "arguments": {"document": target.read_text(encoding="utf-8"), "document_kind": argv[2]},
                "left_out": []}
    if mode == "package":
        documents, left_out = package_documents(target, argv[2], fetch)
        return {"tool": "validate_package",
                "arguments": {"package_kind": argv[2], "documents": documents},
                "left_out": left_out}
    documents, left_out = workspace_documents(target, argv[2] if len(argv) > 2 else None, fetch)
    return {"tool": "validate_workspace", "arguments": {"documents": documents}, "left_out": left_out}


if __name__ == "__main__":
    print(json.dumps(build(sys.argv[1:]), ensure_ascii=False))
