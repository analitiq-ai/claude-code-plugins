# Connection-scoped type maps (gap authoring)

A connector ships a *documented base vocabulary* in its
`definition/type-map-read.json` / `type-map-write.json`, not every type a live
deployment can surface — extension types (`citext`, `ltree`, `hstore`,
`vector(N)`, PostGIS geometries), custom domains/enums, parameterized variants.
The engine composes a **connection-scoped** map as primary over the connector
map as fallback, in both directions, and hard-errors on a type neither covers.
Discovery is when the gap is visible and fixable: this file governs authoring
the connection-scoped maps that close it.

## Files

| Direction | File | Validates as entity |
|---|---|---|
| native → Arrow | `connections/<connection-slug>/definition/type-map-read.json` | `type_map_read` |
| Arrow → native DDL | `connections/<connection-slug>/definition/type-map-write.json` | `type_map_write` |

Published schemas: `https://schemas.analitiq.ai/type-map-read/latest.json` /
`.../type-map-write/latest.json`. The rule shape (exact/regex `match`, matcher
vs rendered key per direction, `${name}` captures) is identical to the
connector's own maps — the connector files you resolve against during gap
detection are the live reference for it; do not restate their vocabulary here.

Filenames are load-bearing: the engine loads exactly the names in the table
above from the connection's `definition/`. The pre-split `type-map.json` is dead — the engine
never reads it, and the validators reject it with a migration finding. The
published `type-map-write-coverage` warning does not apply here — it presumes
a connector's full-vocabulary write map, which a gap-only connection map
deliberately is not — so the validator adapter filters it; never "fix" a
coverage warning by adding connection rules.

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

The rules a type-map document is graded by, at either scope. The relational
ones are the connector plugin's business too; the rest are yours, and the
`Rejected by` column says which of them you will not hear about.

<!-- BEGIN GENERATED: advisory-type-map -->
| Rule | Constraint |
|---|---|
| `ADV-TMAP-001` | A schemaless or structured native type must not resolve to a scalar canonical type. |
| `ADV-TMAP-002` | A schemaless or structured native pattern must not resolve to a scalar canonical type. |
| `ADV-TMAP-003` | Every ${name} in the canonical render must name a capture group in the native pattern. |
| `ADV-TMAP-004` | A native pattern with named captures must not map to a canonical whose parenthesised parameters are all hardcoded, discarding them. The detector keys on the parentheses, so a capture dropped into a canonical carrying none is out of scope. |
| `ADV-TMAP-005` | A regex read rule's native must compile as an ECMA-262 regex; Python-only (?P…) syntax and otherwise-invalid patterns are rejected. |
| `ADV-TMAP-006` | A regex read rule's canonical must be a valid (optionally ${name}-templated) Arrow type matched full-string, so a trailing newline is rejected. |
| `ADV-TMAP-007` | A ${...} placeholder in a canonical render must be well-formed: no empty ${} and no unclosed ${. |
| `ADV-TMAP-008` | A write exact rule's canonical must satisfy the cross-parameter Arrow bounds (Decimal scale <= precision), and its native DDL render's ${...} placeholders must be well-formed. |
| `ADV-TMAP-009` | A write regex rule's canonical must compile as an ECMA-262 regex, and its native DDL render's ${...} placeholders must be well-formed. |
| `ADV-TMAP-010` | A ${name} capture feeding a canonical parameter position must be unable to match a value that position refuses — a byte width of 0, a unit only a sibling family admits — and where a cross-parameter bound applies (Decimal scale <= precision) that bound resolves against the literal sibling present; a literal in such a bounded position must in turn hold against every value the capture bounding it can match. Two things are left undecided: a position whose admissible values the grammar states as an open pattern rather than a member list (a timezone) is not interrogated, and where a bound carries a placeholder on each side every capture is judged against its own position, so the pair reachable from those captures together is not judged at all. |

The registry carries these under the same ids, and they are worth citing, but a violation does not come back as a finding — the last column says what does reject it, and `nothing here` means the document validates and fails later.

| Rule | Constraint | Rejected by |
|---|---|---|
| `ADV-TMAP-011` | A type map declares no wildcard or catch-all fallback rule, leaving an uncovered native or canonical to hard-error at runtime so the coverage gap stays visible. | nothing here (authoring-choice) |
| `ADV-TMAP-012` | New rules on a connection-scoped type map are appended after the rules already present, and an existing rule is never removed, reordered, or edited. | nothing here (authoring-choice) |
| `ADV-TMAP-013` | A type map's rules are authored in the order they must resolve — the reader stops at the first rule whose matcher hits — so a narrow rule is placed ahead of any broader rule that would also match its input. | nothing here (engine-runtime) |
| `ADV-TMAP-014` | A read map's `regex` rule is matched against the probe after the engine's native-type normalization while the pattern itself is used exactly as authored, so a literal written in any other form yields a rule that validates and never fires. | nothing here (engine-runtime) |
| `ADV-TMAP-015` | A write map's `canonical` matcher is authored in the exact casing the canonical Arrow vocabulary uses, because write-side matching preserves case where read-side matching does not. | nothing here (engine-runtime) |
| `ADV-TMAP-016` | Every `${name}` in a write rule's rendered native names a capture group declared by that same rule's `canonical` matcher. | nothing here (engine-runtime) |
| `ADV-TMAP-017` | A connector's write map declares a rendering for every canonical type a source can hand its system, including the container markers an API source emits as literal canonicals, and a family is left uncovered only where the connector's dialect renders it in code. | nothing here (engine-runtime) |
| `ADV-TMAP-018` | A connection-scoped type map declares a rule only for a native or canonical its connector's map leaves unresolved; a rule repeating one the connector already covers replaces the connector's rendering for every stream on that connection. | nothing here (engine-runtime) |
| `ADV-TMAP-019` | A canonical family a connector's `type-map-write.json` leaves unrendered is one the connector's dialect renders itself through a `render_column_type` override, never one left out to cut scope. | nothing here (cross-artifact) |
| `ADV-TMAP-021` | A connection-scoped read rule renders the same canonical type the endpoint document froze for the native it matches. | nothing here (cross-artifact) |
<!-- END GENERATED: advisory-type-map -->

## Authoring rules

- **Gap-only** (`ADV-TMAP-018`). The probes to author for are
  the ones `type_map_gaps.py` reports under `gaps`. A connection rule for
  anything the connector already covers *overrides* the connector for every
  stream on this connection — never shadow.
- **No gaps → no file.** A present-but-empty `[]` is an engine load-time error
  (worse than absent) and the contract rejects it. Write nothing.
- **Extend, never rewrite** (`ADV-TMAP-012`). Append after the rules a
  connection map already carries — they are prior authored behavior on this
  connection.
- **Read rules.** Choose the canonical for an uncovered native with
  `spec-columns.md` judgment; the rule is the durable record of that judgment
  (`ADV-TMAP-021`). Generalize a parameterized native family with one regex rule
  and `${name}` captures (`vector(3)` observed → match the family, not the
  instance); literals inside a regex must be uppercase (`ADV-TMAP-014`).
- **Write rules.** For an uncovered canonical, render the discovered native
  that produced it — the deployment's own spelling is the one type the
  deployment certainly accepts as DDL. When **several distinct** discovered
  natives share one uncovered canonical, do not pick: report the ambiguity
  (see the mode contract in `private-endpoint-creator`) so the orchestrator
  asks the user which native this connection renders.
- **Dialect-override caution.** A connector dialect may render a canonical
  family in code (a `render_column_type` override — e.g. precision-range
  arithmetic no rule can express). No map rule, connector or connection, is
  consulted for such a family, so a connection write rule for it is dead
  weight. If the connector's package files show an override covering the gap
  family, record the gap in `type_maps.notes` instead of authoring a rule.

## What a clean result does not prove

Write coverage is probed against the types *this discovery observed*. A stream
can still hand this destination a canonical no discovered column carried; that
resolves through the connector's write map, and a miss there is a connector
coverage defect to raise upstream, not something to pre-empt with speculative
connection rules.
