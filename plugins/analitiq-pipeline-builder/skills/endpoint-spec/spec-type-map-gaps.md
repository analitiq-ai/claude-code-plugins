# Connection-scoped type maps (gap authoring)

A connector ships a *documented base vocabulary* in its
type map, not every type a live
deployment can surface — extension types (`citext`, `ltree`, `hstore`,
`vector(N)`, PostGIS geometries), custom domains/enums, parameterized variants.
The engine composes a **connection-scoped** map as primary over the connector
map in each direction, and hard-errors on a type neither covers — which is why a
connection rule may only close a gap the connector leaves (`RULE-TMAP-018` for
write, `RULE-TMAP-024` for read). Discovery is when the gap is visible and
fixable: this file governs authoring the connection-scoped map that closes it.

## Contents

- Files
- Gap detection
- Registered rules for a type map
- Authoring rules
- What a clean result does not prove

## Files

The connection's type map is
a whole `{$schema, read, write}` document, not a bare rules array, validated as
document kind `type-map`. The section a rule sits under is its direction — `read`
rules map native → Arrow, `write` rules map Arrow → native DDL — and a map
carries only the sections it has rules for. The `$schema` value is in this
skill's own `SKILL.md` schema-URL table.

The rule shape (exact/regex `match`, matcher vs rendered key per direction,
`${name}` captures) is identical to the connector's own map — the connector's
type map you resolve against during gap detection is the live reference for it;
do not restate their vocabulary here.

## Gap detection

Resolution semantics (normalization, first-match-wins, `${name}` substitution)
live in the contract — never eyeball a regex. Resolve with the `resolve_types`
tool of the `analitiq-validator` MCP server, passing each map's file text in
precedence order (connection first, when one exists, then connector):

<!-- illustrative -->
```jsonc
{"direction": "read", "types": ["citext", "vector(3)"],
 "maps": ["<the connection's type map, as text>",
          "<the connector's type map, as text>"]}
```

- **Read probes** — the distinct `native_type` strings introspected across the
  selected tables, before deriving any `arrow_type`.
- **Write probes** — the distinct `arrow_type` strings frozen into the endpoint
  documents, after read-side resolution and judgment are complete.

`direction` names the probes' vocabulary — `read` for native types, `write`
for `arrow_type` strings — and each map contributes its section for that
direction. A call where no map carries that section is refused.
`resolved` gives the rendered value per covered probe; `gaps` lists the
uncovered ones. Pass only map files that exist.

## Registered rules for a type map

The rules a type-map document is graded by, whichever scope the map is authored
at, are in `../pipeline-builder/references/rules/type-map.md` — **read it
before authoring** and satisfy every row.
<!-- PROBE: write-map-regex-arrow-type-case-unchecked -->
A clean validation run is not proof they all hold — some are applied only at
connect or run time.


## Authoring rules

- **Gap-only** (`RULE-TMAP-018` for write, `RULE-TMAP-024` for read). The
  probes to author for are the ones `resolve_types` reports under `gaps`. A
  connection rule for anything the connector already covers *overrides* the
  connector for every stream on this connection — never shadow. A
  write-coverage warning is not a reason to add one.
- **No gaps → no section.** Never write an empty rule list; a direction with no
  gaps gets no section, and a map with no gaps in any direction is not written.
- **Extend, never rewrite** (`RULE-TMAP-012`). Append after the rules a
  connection map already carries — they are prior authored behavior on this
  connection.
- **Read rules.** Choose the canonical for an uncovered native with
  `spec-columns.md` judgment; the rule is the durable record of that judgment
  (`RULE-TMAP-021`). Generalize a parameterized native family with one regex rule
  and `${name}` captures (`vector(3)` observed → match the family, not the
  instance); spell a regex's literals the way the engine normalizes the probe
  (`RULE-TMAP-014`) — probe with `resolve_types` rather than eyeballing it.
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
  gap in `type_map.notes` instead of authoring a rule.

## What a clean result does not prove

Write coverage is probed against the types *this discovery observed*. A stream
can still hand this destination a canonical no discovered column carried; that
resolves through the connector's write map, and a miss there is a connector
coverage defect to raise upstream, not something to pre-empt with speculative
connection rules.
