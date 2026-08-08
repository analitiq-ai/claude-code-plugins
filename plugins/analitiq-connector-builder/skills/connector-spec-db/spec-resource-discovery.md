# Resource discovery (databases)

How database connectors declare the discovery strategy that the runtime
uses to enumerate a system's objects.

## The object hierarchy

Systems differ in how many namespace levels sit above a table:

| Shape | Levels | Examples |
|---|---|---|
| Three-level | catalog → schema → table | Snowflake, BigQuery (project → dataset → table), Databricks |
| Two-level | schema → table | PostgreSQL, Redshift, Oracle |
| Schema-less | database → table | MySQL / MariaDB, where the "schema" *is* the database |

This shape is **not** something the connector declares — there is no catalog
trigger to configure, and the discovery contract exposes exactly these
actions: `list_resources` and `describe_resource`. What the shape affects is the
`strategy` you pick and what the generated endpoints carry (`ADV-DBEP-005`).

## Shape

See the `resource_discovery` block in
`examples/postgresql/postgresql.example.json` for the reference shape
(`examples/postgresql-adbc/postgresql-adbc.example.json` carries one too).
Add strategy-specific `options` (e.g.
`"options": { "exclude_schemas": ["information_schema", "pg_catalog"] }`)
per the field notes below.

## Required fields

- `strategy` — registered strategy ID (`ADV-CTOR-036`). Common values:
  - `information_schema` for ANSI-SQL databases that expose
    `information_schema`.
  - `snowflake_account_usage` for Snowflake.
  - Provider-specific IDs as appropriate.

## Optional fields

- `transport_ref` — which transport to use for discovery. Defaults to
  `default_transport`.
- `implementation` — `{ "type": "builtin" }` for engine-shipped
  strategies (the common case), or
  `{ "type": "connector_plugin", "entrypoint": "module.path:ClassName" }`
  to ship strategy code with the connector package (`ADV-CTOR-003`).
- `options` — strategy-specific declarative options (e.g.
  `exclude_schemas`).
- `produces` — the artifact kinds discovery writes (`ADV-CTOR-019`). Most
  database connectors produce endpoints and a type map.
- `triggers` — when each discovery action runs (`ADV-CTOR-020`).

## Rules

- The discovery transport may be the same as the data transport, or a
  separate discovery-only transport with restricted credentials.
- Discovery output is connection-scoped, not connector-scoped.
- The connector-level `type-map-read.json` (see `spec-type-maps.md`)
  provides the seed mapping for native types encountered during
  discovery. Connection-scoped type maps are out of scope for this
  plugin; see `shared/type-maps.md` for runtime resolution rules.

## What discovery must record about each object

The generated endpoints are produced at runtime, not here, but the strategy you
declare determines whether they come out addressable:

- **Every namespace level above the table goes into `catalog` / `schema`,
  verbatim.** Exact case and special characters are preserved — the engine
  dialect-quotes them into the qualified identifier. Never try to recover a
  namespace level from the `endpoint_id`, which is a derived handle (see
  `connector-builder/references/endpoint-identity.md`).
- **A column whose native type cannot be determined is recorded as the literal
  `"unknown"`**, not omitted and not guessed. That surfaces as a visible
  type-map miss rather than a silently mistyped column.
- **`object_type` (table / view / …) is descriptive only.** Do not use it to
  gate readability or writability — capability comes from the connector class's
  protocol conformance, not from a discovered label.

## Common pitfalls

- Don't ship database endpoints in the connector release (`ADV-CTOR-044`,
  `ADV-DBEP-006`).
- Don't embed credentials in `options` (`ADV-CTOR-046`). Auth runs separately.
- Don't author a custom strategy in `implementation` unless one of the
  builtin IDs doesn't fit. Most connectors should use builtin
  strategies.
- Don't pick a strategy that flattens away a level the system actually has
  (`ADV-CTOR-030`) — on a three-level system that hides everything outside the
  default catalog.
