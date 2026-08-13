# Resource discovery (databases)

How database connectors declare the discovery strategy that the runtime
uses to enumerate a system's objects.

## The object hierarchy

Systems differ in how many namespace levels sit above a table:

| Shape | Levels |
|---|---|
| Three-level | catalog → schema → table |
| Two-level | schema → table |
| Schema-less | database → table, where the "schema" *is* the database |

Which shape a system has is researched, never assumed from a familiar
neighbour (`RULE-CTOR-026`).

This shape is **not** something the connector declares — there is no catalog
trigger to configure, and the discovery contract exposes exactly these
actions: `list_resources` and `describe_resource`. What the shape affects is the
`strategy` you pick and what the generated endpoints carry (`RULE-DBEP-005`).

## Shape

See the `resource_discovery` block in
`examples/postgresql/postgresql.example.json` for the reference shape
(`examples/postgresql-adbc/postgresql-adbc.example.json` carries one too).
Add strategy-specific `options` (e.g.
`"options": { "exclude_schemas": ["information_schema", "pg_catalog"] }`)
per the field notes below.

## Fields

- `strategy` — registered strategy ID (`RULE-CTOR-036`). Common values:
  - `information_schema` for ANSI-SQL databases that expose
    `information_schema`.
  - `snowflake_account_usage` for Snowflake.
  - Provider-specific IDs as appropriate.
- `transport_ref` — the transport discovery runs on; omit it unless
  discovery uses a transport of its own (see Rules below).
- `implementation` — the strategy's source (`RULE-CTOR-062`; the reference
  row prints the kinds). Engine-shipped is the common case; shipping the
  strategy in the connector package additionally requires an `entrypoint`
  (`module.path:ClassName`) and forbids one otherwise (`RULE-CTOR-003`).
- `options` — strategy-specific declarative options (e.g.
  `exclude_schemas`).
- `produces` — the artifact kinds discovery writes (`RULE-CTOR-019`). Most
  database connectors produce endpoints and a type map.
- `triggers` — when each discovery action runs (`RULE-CTOR-020`).

## Rules

- The discovery transport may be the same as the data transport, or a
  separate discovery-only transport with restricted credentials.
- The connector-level `type-map-read.json` (see `spec-type-maps.md`)
  provides the seed mapping for native types encountered during
  discovery. Connection-scoped type maps are out of scope for this
  plugin; see `shared/type-maps.md` for runtime resolution rules.

## What discovery must record about each object

The generated endpoints are produced at runtime, not here, but the strategy you
declare determines whether they come out addressable:

- **Every namespace level above the table goes into `catalog` / `schema`**
  (`RULE-DBEP-005`), **verbatim** (`RULE-DBEP-009`) — exact case and special
  characters are preserved, and the engine dialect-quotes them into the
  qualified identifier. Never recover a namespace level from the `endpoint_id`
  (`RULE-DBEP-007`), which is a derived handle; see
  `connector-builder/references/endpoint-identity.md`.
- **A column whose native type cannot be determined is recorded as the literal
  `"unknown"`** (`RULE-DBEP-012`), not omitted and not guessed. That surfaces
  as a visible type-map miss rather than a silently mistyped column.
- **`object_type` is descriptive, never a capability signal**
  (`RULE-DBEP-013`) — capability comes from the connector class's protocol
  conformance, not from a discovered label.

## Common pitfalls

- Don't ship database endpoints in the connector release (`RULE-CTOR-044`,
  `RULE-DBEP-006`).
- Don't embed credentials in `options` (`RULE-CTOR-046`). Auth runs separately.
- Don't author a custom strategy in `implementation` unless no builtin
  strategy fits.
- Don't pick a strategy that flattens away a level the system actually has
  (`RULE-CTOR-030`) — on a three-level system that hides everything outside the
  default catalog.
