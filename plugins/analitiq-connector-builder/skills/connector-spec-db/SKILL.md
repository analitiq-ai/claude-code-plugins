---
name: connector-spec-db
description: Database connector authoring vocabulary — driver selection, DSN URL templates with bindings and encoding, TLS declarations, resource discovery, read/write type maps, the SQL write path (sql_capabilities + the dialect renderers), and the Python package files. Loaded by db-connector-creator only. Not invoked directly by users.
disable-model-invocation: true
---

# connector-spec-db

This skill is loaded by `db-connector-creator` when authoring a database
connector. It carries the DB-specific vocabulary and examples needed to
populate `transports`, `auth`, `connection_contract`,
`resource_discovery`, and `sql_capabilities` for `kind: "database"`,
plus the standalone
`type-map-read.json` / `type-map-write.json` shipped alongside the
connector and the package files (`connector.py`, `__init__.py`,
`requirements.txt`, `pyproject.toml`) that make the connector an
installable Python package.

## Required reading (load on demand)

- This skill's `spec-driver-selection.md` — the transport/driver
  decision order (ADBC → Flight SQL → a declared bulk mechanism →
  landing via executemany) and the sync/async dispatch constraints.
- This skill's `spec-dsn-bindings.md` — DSN URL templates and bindings.
- This skill's `spec-tls.md` — TLS declaration mechanics.
- This skill's `spec-resource-discovery.md` — schema/table enumeration at
  connection time.
- This skill's `spec-type-maps.md` — the read map (native → Arrow,
  `type-map-read.json`) and the write map (Arrow → native DDL,
  `type-map-write.json`), incl. the uppercase-pattern rule and the
  direction inversion.
- This skill's `spec-connector-package.md` — package layout,
  `pyproject.toml` + entry points, dialect hooks, CDK import rules.
- This skill's `spec-sql-write-path.md` — the stage-then-merge write
  path: the `sql_capabilities` declaration in `connector.json` and the
  dialect renderers it obliges, which are checked against each other.
- The closest transport archetype under `examples/<name>/`: `postgresql`
  (sqlalchemy + `tls` block, with the full kitchen-sink `type-map-read.json` /
  `type-map-write.json`) or `postgresql-adbc` (adbc + `db_kwargs` TLS, maps
  trimmed to an illustrative stub). The per-provider type map is **derived
  from research** (`spec-type-maps.md`), not copied per provider.

## What this skill covers

- `dsn.kind: "url_template"` shape with `template`, `bindings`, and
  per-binding `encoding` (closed enum — `spec-dsn-bindings.md` §Encoding
  values).
- `tls.mode` and `tls.ca_certificate` declarations and their rules
  (`spec-tls.md`). **SQLAlchemy-only**: ADBC transports express TLS via
  `db_kwargs` entries — they have no `tls` block.
- `resource_discovery` declarations for enumerating schemas / tables /
  columns at connection time.
- Authoring the standalone `type-map-read.json` (native → Arrow) and
  `type-map-write.json` (Arrow → native DDL render rules; full
  canonical-vocabulary coverage) — see `spec-type-maps.md`.
- The connector package files and dialect hooks — see
  `spec-connector-package.md`.
- The `sql_capabilities` write-path declaration and the dialect
  renderers it obliges (`stage_table_sql`, `merge_statement_sql`,
  `bulk_land`) — see `spec-sql-write-path.md`.
- Transport types, chosen per the `spec-driver-selection.md` decision
  order: `adbc` (closed `driver` enum; `dsn` and/or `db_kwargs`,
  ADV-CTOR-004) and `sqlalchemy` (a `dialect+driver`, sync or async;
  generic `tls` block). Both take the same `dsn.kind: "url_template"`
  shape — `spec-dsn-bindings.md`.
- `auth.type: "db"` — credentials live in `connection_contract.inputs`;
  `auth.test` is the connection test operation.

## What this skill does NOT cover

- HTTP transport idioms (that's `connector-spec-api`).
- OAuth flows or other API auth types.
- API endpoint authoring (database connectors do not ship endpoint files).
