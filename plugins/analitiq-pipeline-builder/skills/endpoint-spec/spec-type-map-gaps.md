# Connection-scoped type maps (gap authoring)

A connector ships a *documented base vocabulary* in its
`definition/type-map-read.json` / `type-map-write.json`, not every type a live
deployment can surface — extension types (`citext`, `ltree`, `hstore`,
`vector(N)`, PostGIS geometries), custom domains/enums, parameterized variants.
The engine composes a **connection-scoped** map as primary over the connector
map in each direction, and hard-errors on a type neither covers — which is why a
connection rule may only close a gap the connector leaves (`RULE-TMAP-018`). Discovery is when the gap is visible and fixable: this file governs
authoring the connection-scoped maps that close it.

## Contents

- Files
- Gap detection
- Registered rules for a type map
- Authoring rules
- What a clean result does not prove

## Files

| Direction | File | Validates as entity |
|---|---|---|
| native → Arrow | `connections/<connection-slug>/definition/type-map-read.json` | `type_map_read` |
| Arrow → native DDL | `connections/<connection-slug>/definition/type-map-write.json` | `type_map_write` |

The rule shape (exact/regex `match`, matcher vs rendered key per direction,
`${name}` captures) is identical to the connector's own maps — the connector
files you resolve against during gap detection are the live reference for it;
do not restate their vocabulary here.

Filenames are load-bearing: author only the names in the table above. The engine
loads exactly those from the connection's `definition/`, and the pre-split
`type-map.json` is dead.

## Gap detection

Resolution semantics (normalization, first-match-wins, `${name}` substitution)
live in the published packages — never eyeball a regex. Probe with the helper,
maps in precedence order (connection first, when one exists, then connector):

```bash
printf '%s' '["citext", "vector(3)"]' | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/type_map_gaps.py" \
  --direction read \
  --map connections/<slug>/definition/type-map-read.json \
  --map connectors/<connector-slug>/definition/type-map-read.json
```

- **Read probes** — the distinct `native_type` strings introspected across the
  selected tables, before deriving any `arrow_type`.
- **Write probes** — the distinct `arrow_type` strings frozen into the endpoint
  documents, after read-side resolution and judgment are complete.

`resolved` gives the rendered value per covered probe; `gaps` lists the
uncovered ones. Pass only map files that exist.

## Registered rules for a type map

The rules a type-map document is graded by, whichever scope the map is authored
at, are in `../pipeline-builder/references/rules/type-map.md` — **read it
before authoring** and satisfy every row.
<!-- PROBE: write-map-regex-canonical-case-unchecked -->
A clean validation run is not proof they all hold — some are applied only at
connect or run time.


## Authoring rules

- **Gap-only** (`RULE-TMAP-018`). The probes to author for are
  the ones `type_map_gaps.py` reports under `gaps`. A connection rule for
  anything the connector already covers *overrides* the connector for every
  stream on this connection — never shadow. A write-coverage warning is not a
  reason to add one.
- **No gaps → no file.** Never write an empty array; when a direction has no
  gaps, write nothing.
- **Extend, never rewrite** (`RULE-TMAP-012`). Append after the rules a
  connection map already carries — they are prior authored behavior on this
  connection.
- **Read rules.** Choose the canonical for an uncovered native with
  `spec-columns.md` judgment; the rule is the durable record of that judgment
  (`RULE-TMAP-021`). Generalize a parameterized native family with one regex rule
  and `${name}` captures (`vector(3)` observed → match the family, not the
  instance); spell a regex's literals the way the engine normalizes the probe
  (`RULE-TMAP-014`) — probe with the helper rather than eyeballing it.
- **Write rules.** For an uncovered canonical, render the discovered native
  that produced it — the deployment's own spelling is the one type the
  deployment certainly accepts as DDL. When **several distinct** discovered
  natives share one uncovered canonical, do not pick: report the ambiguity
  (see the mode contract in `private-endpoint-creator`) so the orchestrator
  asks the user which native this connection renders.
- **Dialect-override caution.** A canonical family the connector's write map
  leaves unrendered may be one its dialect renders in code (`RULE-TMAP-019`);
  no map rule is consulted for such a family, so a connection rule for it is
  dead weight. If the connector's package files show that override, record the
  gap in `type_maps.notes` instead of authoring a rule.

## What a clean result does not prove

Write coverage is probed against the types *this discovery observed*. A stream
can still hand this destination a canonical no discovered column carried; that
resolves through the connector's write map, and a miss there is a connector
coverage defect to raise upstream, not something to pre-empt with speculative
connection rules.
