#!/usr/bin/env python3
"""Resolve native_type / arrow_type probes through type-map rule files.

This is the gap-detection half of connection-scoped type-map authoring
(`endpoint-spec/spec-type-map-gaps.md`). It holds no matching logic of its
own — it reads the map files and hands them to the pinned `analitiq-validator`'s
`resolve_type_map_gaps`, so a probe resolves here exactly as the validator
resolves it.

Maps are passed in precedence order (connection-scoped first, connector
second); the package concatenates them into one first-match rule list — the
composition `RULE-TMAP-018` is written against — after model-validating each,
so a broken rule is refused rather than read as a gap.

Usage::

    printf '%s' '["citext", "vector(3)"]' | python3 type_map_gaps.py \
        --direction read \
        --map connections/pg/definition/type-map-read.json \
        --map connectors/postgresql/definition/type-map-read.json

Probes are a JSON array of strings on stdin (or --probes-file): provider
`native_type` labels for ``--direction read``, `arrow_type` strings for
``--direction write``. Output on stdout::

    {"direction": "read",
     "resolved": {"citext": null, "vector(3)": null},
     "gaps": ["citext", "vector(3)"]}

``resolved`` maps each probe (verbatim) to its rendered value — the Arrow
type (read) or the native DDL (write) — or ``null`` when no rule in any map
matches; ``gaps`` lists the null probes. Exit status is ``0`` on a clean run
regardless of gaps (a gap is a result, not an error), ``2`` on a CLI / input
error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _bootstrap import ensure_deps_or_reexec


def _fail(message: str) -> "int":
    print(f"type_map_gaps: {message}", file=sys.stderr)
    return 2


def _load_map(path: Path) -> Any:
    """Read one rule-list file, naming it on a read or parse failure."""
    from analitiq.validator import read_document

    doc, problem = read_document(path)
    if problem is not None:
        raise ValueError(f"{path}: {problem}")
    return doc


def resolve(direction: str, probes: list[str], rule_files: list[Path]) -> dict:
    """Resolve every probe through the map files, primary first.

    The package names a refused map by its position; the file list is appended
    in the same order so the message resolves to a path for the reader."""
    from analitiq.validator import resolve_type_map_gaps

    maps = [_load_map(path) for path in rule_files]
    try:
        return resolve_type_map_gaps(direction, probes, maps)
    except ValueError as exc:
        ordered = ", ".join(f"maps[{i}] = {path}" for i, path in enumerate(rule_files))
        raise ValueError(f"{exc} ({ordered})") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--direction", required=True, choices=("read", "write"),
                        help="read: probes are native types, maps are type-map-read files; "
                             "write: probes are Arrow types, maps are type-map-write files.")
    parser.add_argument("--map", action="append", required=True, dest="maps", metavar="PATH",
                        help="Rule-list file; repeatable, in precedence order "
                             "(connection-scoped map first, connector map after).")
    parser.add_argument("--probes-file", metavar="PATH",
                        help="JSON array of probe strings; defaults to stdin.")
    args = parser.parse_args(argv)

    try:
        ensure_deps_or_reexec(__file__)
    except RuntimeError as exc:
        return _fail(str(exc))

    # Read and write rules share the {match, native_type, arrow_type} key set, so a
    # wrong-direction map would not error — it would resolve, plausibly and
    # wrongly. The two load-bearing filenames declare their direction; hold a
    # map named either of them to it.
    load_bearing = {"type-map-read.json": "read", "type-map-write.json": "write"}
    for m in args.maps:
        implied = load_bearing.get(Path(m).name)
        if implied is not None and implied != args.direction:
            return _fail(f"{m} is a {implied}-direction map (by filename) but "
                         f"--direction is {args.direction}")

    from analitiq.validator import parse_document, read_document

    if args.probes_file:
        probes, problem = read_document(Path(args.probes_file))
    else:
        probes, problem = parse_document(sys.stdin.read())
    if problem is not None:
        return _fail(f"cannot read probes: {problem}")
    if not isinstance(probes, list) or not all(isinstance(p, str) for p in probes):
        return _fail("probes must be a JSON array of strings")

    try:
        result = resolve(args.direction, probes, [Path(m) for m in args.maps])
    except ValueError as exc:
        # Every per-file failure — read, parse, model — is named for its file
        # and raised as a ValueError by `resolve`, so there is one failure
        # shape to report and anything else is a defect that should surface.
        return _fail(str(exc))

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
