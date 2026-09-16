#!/usr/bin/env python3
"""Resolve native_type / arrow_type probes through type-map rule files.

This is the gap-detection half of connection-scoped type-map authoring
(`endpoint-spec/spec-type-map-gaps.md`). It holds no matching logic of its
own — resolution dispatches to the pinned `analitiq-validator`'s resolution
internals (the same first-match-wins, `${name}`-substituting,
read-side-normalizing semantics the engine and the validator use), so a probe
resolves here exactly as it will at runtime. Those helpers are private API,
not a published surface — this repo's tests exercise them against the in-repo
source, which moves in lockstep with the pin, so a pin bump that renames them
fails here first.

Maps are passed in precedence order (connection-scoped first, connector
second) and concatenated into one rule list — mirroring the engine's
`TypeMapper.compose`, where the connection map is primary and the connector
map is the fallback.

Usage::

    printf '%s' '["citext", "vector(3)"]' | python3 type_map_gaps.py \
        --map connections/pg/definition/type-map-read.json \
        --map connectors/postgresql/definition/type-map-read.json

Each map's direction is located from its filename, so the names say which
direction is being probed and no flag has to repeat it; maps whose filenames
name different directions are a usage error, as is a filename naming neither
direction. Probes
are a JSON array of strings on stdin (or --probes-file): provider `native_type`
labels reading, `arrow_type` strings writing. Output on stdout::

    {"direction": "read",
     "resolved": {"citext": null, "vector(3)": null},
     "gaps": ["citext", "vector(3)"]}

``resolved`` maps each probe (verbatim) to its rendered value — the Arrow
Arrow type (read) or the native DDL (write) — or ``null`` when no rule in any
map matches; ``gaps`` lists the null probes. Exit status is ``0`` on a clean
run regardless of gaps (a gap is a result, not an error), ``2`` on a CLI /
input error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bootstrap import ensure_deps_or_reexec
from validate import _DIRECTION_BY_FILENAME


def _fail(message: str) -> "int":
    print(f"type_map_gaps: {message}", file=sys.stderr)
    return 2


def _load_rules(path: Path, direction: str) -> list:
    """Read one {$schema, direction, rules} type-map document, grade it as the
    direction its slot names, and return its `rules` array. Grading here is
    load-bearing, not a courtesy: the resolver mirrors runtime semantics, which
    *skip* a malformed rule — so a broken rule would surface as a false "gap",
    indistinguishable from a genuinely uncovered probe, and a false gap makes
    the authoring agent shadow the very rule the map intended. Failing loud
    keeps a reported gap unambiguous.

    Only a finding that costs a pass is read here, and the vocabulary-coverage
    check is advisory, so which vocabulary a map is held to cannot reach this
    verdict."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    from analitiq.validator import finding_costs_a_pass, type_map_findings
    fatal = [f for f in type_map_findings(doc, direction)
             if finding_costs_a_pass(f)]
    if fatal:
        detail = "; ".join(f"{f.get('path') or '/'}: {f['message']}" for f in fatal)
        raise ValueError(
            f"{path} is not a valid {direction} type map — fix it (or, for a "
            f"connector map, raise the defect upstream) before probing: {detail}")
    return doc["rules"]


def resolve(direction: str, probes: list[str], rule_files: list[Path]) -> dict:
    """Resolve every probe through the concatenated rule lists, primary first."""
    # The pinned validator's internal resolution helpers (private API — see the
    # module docstring). `_render_arrow_type` bundles the read-side native_type
    # normalization; write matchers compare the arrow_type as authored
    # (case-preserving).
    from analitiq.validator import _render_arrow_type
    from analitiq.validator.connectors import _first_match_render

    rules: list = []
    for path in rule_files:
        rules.extend(_load_rules(path, direction))

    probes = list(dict.fromkeys(probes))  # dedupe, order-preserving — one verdict per probe
    if direction == "read":
        resolved = {p: _render_arrow_type(p, rules) for p in probes}
    else:
        resolved = {p: _first_match_render(p, rules, "arrow_type", "native_type") for p in probes}
    return {
        "direction": direction,
        "resolved": resolved,
        "gaps": [p for p in probes if resolved[p] is None],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--map", action="append", required=True, dest="maps", metavar="PATH",
                        help="A {$schema, direction, rules} type-map document, named for the "
                             "direction it holds; repeatable, in precedence order "
                             "(connection-scoped map first, connector map after).")
    parser.add_argument("--probes-file", metavar="PATH",
                        help="JSON array of probe strings; defaults to stdin.")
    args = parser.parse_args(argv)

    try:
        ensure_deps_or_reexec(__file__)
    except RuntimeError as exc:
        return _fail(str(exc))

    # The filename is the slot: it says which direction is being probed, so no
    # CLI flag can disagree with it. `_load_rules` then grades each map as that
    # direction, where a map whose envelope declares the other one fails on the
    # `direction` Literal.
    directions = {}
    for m in args.maps:
        implied = _DIRECTION_BY_FILENAME.get(Path(m).name)
        if implied is None:
            names = " or ".join(sorted(_DIRECTION_BY_FILENAME))
            return _fail(f"{m} is named neither {names}, so it names no direction to probe")
        directions.setdefault(implied, m)
    if len(directions) > 1:
        return _fail("every --map must hold the same direction, got "
                     + ", ".join(f"{m} ({d})" for d, m in sorted(directions.items())))
    direction = next(iter(directions))

    try:
        raw = Path(args.probes_file).read_text() if args.probes_file else sys.stdin.read()
        probes = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _fail(f"cannot read probes: {exc}")
    if not isinstance(probes, list) or not all(isinstance(p, str) for p in probes):
        return _fail("probes must be a JSON array of strings")

    try:
        result = resolve(direction, probes, [Path(m) for m in args.maps])
    except (OSError, ValueError) as exc:
        # _load_rules wraps every per-file failure (read, parse, model) into a
        # file-naming ValueError; OSError is the escape hatch for anything else.
        return _fail(str(exc))

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
