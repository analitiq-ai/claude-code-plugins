# Driver selection

Every database connector package ships its own driver in
`requirements.txt` and picks its transport in `definition/connector.json`
via `transport_type` (`sqlalchemy` or `adbc`). This is the decision
guide for choosing the driver and bulk-write path when authoring a
connector for a new system.

## Decision order

Apply in order; stop at the first match.

1. **A first-class ADBC driver exists and is in the `AdbcTransport.driver`
   enum** → `transport_type: "adbc"`. The driver hands Arrow buffers
   directly to the system's native bulk protocol; no row-by-row path at
   all.
2. **The server exposes an Arrow Flight SQL endpoint** → ADBC via the
   generic Flight SQL driver. Currently unreachable — see §2.
3. **Neither, but the system has a native bulk-load protocol** →
   SQLAlchemy transport for connect/DDL, with the bulk path **declared**
   in `sql_capabilities.bulk_load` and implemented in the dialect's
   `bulk_land` hook (`spec-sql-write-path.md`).
4. **None of the above** → SQLAlchemy transport, landing via executemany.
   This is the fallback, not the default — pick it last.

Tiers 3 and 4 differ only in the `bulk_load` declaration: every SQL write
rides the same stage-then-merge primitive, and the mechanism chosen here
is how the batch **lands in the stage**. Declaring no mechanism (`{}`) is
tier 4.

## 1. First-class ADBC drivers

`cursor.adbc_ingest(...)` genuinely skips a row-by-row insert path for
exactly these:

| System | Package | Bulk path |
|---|---|---|
| PostgreSQL | `adbc-driver-postgresql` | libpq `COPY BINARY`. Production-ready. |
| Snowflake | `adbc-driver-snowflake` | Native Arrow ingestion via the internal Go-Snowflake driver. |
| BigQuery | `adbc-driver-bigquery` | Storage Write API (Arrow-native). |
| DuckDB *(not in the enum — gap path below)* | shipped with `duckdb` itself | Zero-copy in-process. |
| SQLite *(not in the enum — gap path below)* | `adbc-driver-sqlite` | Production-ready; mainly useful for testing, not volume. |

An ADBC transport declares that landing as
`sql_capabilities.bulk_load: {"adbc": "adbc_ingest"}`. It is the backend's
own native path, so — unlike every other mechanism — it obliges **no**
dialect code, which is what makes this tier cheap
(`spec-sql-write-path.md`).

The schema's `AdbcTransport.driver` enum is the **sole validator** for
ADBC driver values (currently `postgresql`, `snowflake`, `bigquery`).
The engine derives the dbapi module from the `driver` value by the
upstream packaging convention `adbc_driver_{driver}.dbapi` — the
connector's `requirements.txt` must ship the matching
`adbc-driver-{driver}` wheel (plus `adbc-driver-manager`).

If the system's driver is not yet in the enum, that is a **contract gap to
raise, not a freeform workaround** — and it is not a one-line change. Adding a
driver means extending the published enum *and* provisioning the platform-side
support for it, so treat it as coordinated work with the contract and platform
owners rather than something a connector author can unblock alone. Until the
enum entry exists, select the next tier in the decision order.

**Redshift** takes the SQLAlchemy transport with the **sync**
`redshift+redshift_connector` driver — the canonical Redshift path.
`redshift_connector` is a sync DBAPI; the engine runs it on the sync
SQLAlchemy engine automatically (see "Constraints" below), so no ADBC
entry is needed. That dispatch is all the engine contributes: as with
every SQLAlchemy connector, system-specific interpretation — TLS,
upsert SQL — ships in the connector package's own dialect, never the
engine (see `spec-connector-package.md`). DSN template
`redshift+redshift_connector://{username}:{password}@{host}:{port}/{database}`.
The libpq-compatible PostgreSQL ADBC driver (`transport_type: "adbc"`,
driver `postgresql`) also reaches Redshift over the postgres wire, but
wire compatibility does not extend to the driver's option surface
(TLS parameters differ — research the actual driver, per
`spec-tls.md`), and the sync SQLAlchemy path is the canonical one.

## 2. Flight SQL

| Driver | Package | Covers |
|---|---|---|
| Flight SQL generic | `adbc-driver-flightsql` | Any server implementing the Arrow Flight SQL protocol — Dremio, Doris, InfluxDB 3.x, Databricks (in some configs), and a growing set of newer warehouses. |

Caveat: this only helps if the target server actually exposes a Flight
SQL endpoint. Ordinary MySQL/Postgres deployments do not.

Today this tier is **unreachable**: `flightsql` is not in the
`AdbcTransport.driver` enum, so selecting it is the same contract-gap
path as §1. Record the server's Flight SQL support in research; do not
author the transport.

## Do not use the JDBC bridge

| Driver | Package | What it does |
|---|---|---|
| JDBC bridge | `adbc-driver-jdbc` | Wraps any JDBC driver — gives an ADBC API surface over Oracle/MSSQL/MariaDB/MySQL/Redshift, but underneath it still binds row-by-row through JDBC. |

A connector that needs one of these systems takes the SQLAlchemy
transport (or the native bulk path below) instead.

## 3. Native bulk-load protocols (no ADBC)

The connect/DDL layer stays on the SQLAlchemy transport; the bulk write
goes through the **declared** mechanism — the connector names it under
`sql_capabilities.bulk_load.sqlalchemy` and implements the dialect's
`bulk_land` hook. Declaring a mechanism without the hook (or the hook
without a declaration) fails the CDK conformance kit; the mechanism
vocabulary is closed, so a protocol it does not name is a contract gap to
raise, not a private override.

Only the first two rows below are tier 3. The rest are systems an author
might *expect* to find here: they reach tier 4, and the row says why —
either because the fast path is the driver's own executemany tuning, or
because the protocol is not in the closed vocabulary.

| System | Driver | Tier | Bulk path |
|---|---|---|---|
| MySQL / MariaDB | aiomysql (SQLAlchemy async) | 3 | `load_data_local_infile` — `LOAD DATA LOCAL INFILE` into the stage table, streaming Arrow → CSV/TSV. Roughly 10x a parameterized INSERT. Needs `local_infile` enabled on **both** server and client (off by default on MySQL 8.0), so a connector that cannot rely on it drops to tier 4 with `bulk_load: {}` — as the shipped `mysql` connector does. |
| PostgreSQL (when not on ADBC) | psycopg / asyncpg | 3 | `copy_from` — `COPY FROM stdin BINARY`. Roughly 10x a parameterized INSERT. |
| Oracle | python-oracledb (SQLAlchemy) | 4 | No declared mechanism. `executemany` with a tuned `arraysize` **is** the standard fast path; SQL*Loader is not practical from Python. |
| MSSQL / SQL Server | pyodbc (SQLAlchemy) | 4 | No declared mechanism. `fast_executemany=True` tunes the driver's own TDS batched parameter stream, so the executemany landing is already the fast path. |
| ClickHouse | clickhouse-connect | 4 | First-class Arrow ingest (`client.insert_arrow`), just not branded ADBC — and **not in the closed mechanism vocabulary**, so it cannot be declared without a contract change. |

BigQuery is not in this table: it is tier 1 (`adbc-driver-bigquery`). Its
`load_job` mechanism exists in the vocabulary for a connector that
reaches BigQuery over a SQLAlchemy transport instead, which is not the
canonical path.

## Constraints from the engine contract

- SQLAlchemy transports accept a **sync or async** DBAPI. The engine
  builds the sync vs async SQLAlchemy engine automatically from the
  dialect's own `is_async` capability — there is no driver allow-list.
  Async drivers (`postgresql+asyncpg`, `mysql+aiomysql`) run on the
  async engine; sync drivers (`redshift+redshift_connector`,
  `postgresql+psycopg2`) run on the sync engine, in a worker thread off
  the event loop. Prefer async where the system has
  a working async driver; use a sync driver when that is the system's
  viable path (Redshift's `redshift_connector` is the canonical sync
  case). The declared `driver` must be a real SQLAlchemy
  `dialect+driver` registration — e.g. `redshift_connector` registers
  under the `redshift` dialect, so `postgresql+redshift_connector` is
  invalid and fails at transport build.
- The driver lives ONLY in the connector's `requirements.txt`. The
  engine image ships no database drivers.
- Known pin: aiomysql's adapter still passes the deprecated positional
  argument to PyMySQL's `Connection.ping()`; pin `pymysql<1.2` until
  aiomysql ships a fix (the reference `mysql`/`mariadb` connectors do
  this).
- A connector may ship more than one driver when it declares (or is
  expected to grow) more than one transport — the reference `postgres`
  connector ships `asyncpg` for the SQLAlchemy transport plus
  `adbc-driver-postgresql`/`adbc-driver-manager` for the ADBC path.
