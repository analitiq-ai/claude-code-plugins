# Type-map resolution at runtime

Where type maps live at the two scopes, and how the engine resolves a column
type through them. A connector's map is laid out by `RULE-PKG-030`; the
resolution sections read the engine as it resolves directions — referenced from
`connector-spec-db/spec-type-maps.md` and `spec-resource-discovery.md`, which
own the *authoring* of connector-scoped maps. Connection-scoped maps are
authored by the sibling `analitiq-pipeline-builder` plugin at discovery time,
never by this plugin.

## The two scopes

| Scope | File | Authored by |
|---|---|---|
| Connector | `{connector_id}/definition/type-map.json` | this plugin (`spec-type-maps.md`) |
| Connection | `connections/<connection-slug>/definition/type-map.json` | `analitiq-pipeline-builder`, for natives the connector map doesn't cover on that deployment |

Both scopes share one document shape and one published schema
(`https://schemas.analitiq.ai/type-map/latest.json`): a `read` rule list and a
`write` rule list, each present only where the map has rules for it.

## Resolution order

Where a stream reads or writes through a connection that ships its own map,
the engine composes the connection map
as **primary** over the connector map as **fallback** — the two rule lists are
concatenated, connection rules first, into one first-match-wins list
(`RULE-TMAP-013`). This holds in **each direction**: native → Arrow (read) and
Arrow → native DDL (write). With no connection map present, the connector map
resolves alone.

A probe neither map matches is a **hard error** at runtime (an unmapped-type
failure that stops the stream). Deliberate: no map at either scope declares a
wildcard fallback (`RULE-TMAP-011`), so a coverage gap stays visible instead of
silently corrupting types — the fix is a rule in the right map, never a
catch-all.

Because composition is first-match over the concatenation, a connection rule
for a native/canonical the connector already covers **overrides** the
connector's rendering for every stream on that connection — enforced for the
write direction (`RULE-TMAP-018`); the read-direction mirror (`RULE-TMAP-024`)
is not yet enforced.

## When each direction is consulted

- **Read maps matter at discovery time.** Discovery renders each discovered
  native through the read map to produce the `arrow_type` frozen into the
  endpoint document. At stream run time the frozen `arrow_type` is used
  directly — the read map is not consulted again.
- **The write map is consulted on every run.** Stream configuration renders
  every destination column's frozen `arrow_type` → native DDL through the
  write map each time, so a write-side gap fails a destination stream even
  when its table already exists (`RULE-TMAP-017`).
- **Dialect overrides bypass the write maps.** Where a connector's dialect
  overrides `render_column_type` for a canonical family (see
  `spec-type-maps.md` §Database coverage), no **write** rule — connector or
  connection — is consulted for that family. Read-side rules are unaffected:
  `render_column_type` exists only on the Arrow → native DDL path.

## File-presence semantics

- **Absent file or absent section** — contributes no rules for that direction
  at that scope (a connection without a map leaves the connector map to
  resolve alone).
- **An empty rule list** — never ship one at either scope; the contract
  requires at least one rule in a present section, so an empty one is a
  rejected document rather than the fallthrough an absent section gives you.
