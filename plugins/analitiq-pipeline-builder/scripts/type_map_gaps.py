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

Each map declares the direction being probed; its filename says nothing about
it here, as it says nothing anywhere else a map is consumed. Maps declaring
different directions are a usage error, as is one declaring neither. Probes are
a JSON array of strings on stdin (or --probes-file): provider `native_type`
labels when reading, `arrow_type` strings when writing. Output on stdout::

    {"direction": "read",
     "resolved": {"citext": null, "vector(3)": null},
     "gaps": ["citext", "vector(3)"]}

``resolved`` maps each probe (verbatim) to its rendered value — the Arrow type
(read) or the native DDL (write) — or ``null`` when no rule in any map matches;
``gaps`` lists the null probes. Exit status is ``0`` on a clean run regardless
of gaps (a gap is a result, not an error), ``2`` on a CLI / input error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _bootstrap import ensure_deps_or_reexec


def _fail(message: str) -> "int":
    print(f"type_map_gaps: {message}", file=sys.stderr)
    return 2


def _declared_direction(path: Path) -> str:
    """The direction a type-map document declares. A name says nothing about a
    map's direction anywhere a map is consumed, so it says nothing here either:
    probing the direction a filename suggested would report gaps in a vocabulary
    the document never claimed to hold."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    declared = doc.get("direction") if isinstance(doc, dict) else None
    if declared not in ("read", "write"):
        raise ValueError(
            f"{path} declares direction {declared!r}; a type-map document declares "
            "'read' or 'write', and nothing else says which vocabulary to probe")
    return declared


def _load_rules(path: Path, direction: str) -> list:
    """Read one {$schema, direction, rules} type-map document, grade it as the
    direction named, and return its `rules` array. Grading here is
    load-bearing, not a courtesy. Read of the engine as it stands: a malformed
    rule is *skipped* at resolution rather than failing the run, and the resolver
    mirrors that — so a broken rule would surface here as a false "gap",
    indistinguishable from a genuinely uncovered probe, and a false gap makes the
    authoring agent shadow the very rule the map intended.

    So every finding reaches the operator, and the severity decides what happens
    after: a pass-costing one also stops the probe, because a gap reported over a
    map that did not grade clean means nothing. Everything else is a map that
    resolves while something about it is still wrong — an advisory that is most
    often the explanation for a gap reported below it, and dropping it leaves the
    gap looking uncaused."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    from analitiq.validator import finding_costs_a_pass, type_map_findings
    # The connection scope is the one either kind of map can meet: a connection
    # map covers only the gaps it fills, and a connector map rendering the whole
    # vocabulary clears the weaker bar too.
    findings = type_map_findings(doc, direction, scope="connection")
    fatal, advisory = [], []
    for f in findings:
        (fatal if finding_costs_a_pass(f) else advisory).append(f)
    # Reported before anything raises: a fatal finding elsewhere in the same
    # document is no reason to make the author fix that first and rediscover this
    # one on the next run. The fatal ones travel in the raise below instead, which
    # names the file they were found in.
    for f in advisory:
        print(f"type_map_gaps: {path}: {f.get('path') or '/'}: {f['message']}",
              file=sys.stderr)
    if fatal:
        detail = "; ".join(f"{f.get('path') or '/'}: {f['message']}" for f in fatal)
        # A check that crashed stops the probe for the same reason a defect does
        # — nothing graded the map — but it is not something the author wrote, so
        # telling them to fix it would send them after a defect the map does not
        # have.
        if any(f.get("message_id") == "check-crashed" for f in fatal):
            raise ValueError(f"{path} could not be graded: {detail}")
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
                        help="A {$schema, direction, rules} type-map document, which declares "
                             "the direction it holds; repeatable, in precedence order "
                             "(connection-scoped map first, connector map after).")
    parser.add_argument("--probes-file", metavar="PATH",
                        help="JSON array of probe strings; defaults to stdin.")
    args = parser.parse_args(argv)

    try:
        ensure_deps_or_reexec(__file__)
    except RuntimeError as exc:
        return _fail(str(exc))

    # Each document declares the direction being probed, and they must agree:
    # the resolver concatenates their rule arrays into one precedence order, so
    # a pair holding opposite directions has no single vocabulary to probe.
    directions: dict[str, str] = {}
    for m in args.maps:
        try:
            directions.setdefault(_declared_direction(Path(m)), m)
        except ValueError as exc:
            return _fail(str(exc))
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
        # _load_rules names the file in a ValueError for every way one can fail
        # — unreadable, unparseable, or graded fatal — which is the only path
        # that opens a file here; OSError guards an open that fails outside it.
        return _fail(str(exc))

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
