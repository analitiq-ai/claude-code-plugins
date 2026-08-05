# Driver selection

Every database connector package ships its own driver in
`requirements.txt` and picks its transport in `definition/connector.json`
via `transport_type` (`sqlalchemy` or `adbc`). This is the decision
guide for choosing the driver and bulk-write path when authoring a
connector for a new system.

**It is a decision procedure, not a lookup table.** What a given system
supports is a researched fact — `provider_facts.adbc_driver_package`,
`.flight_sql_endpoint`, `.bulk_load_protocol`, `.sqlalchemy_driver`,
grounded from the vendor's own documentation at author time. This file
supplies the *order* and the *closed vocabularies* the contract owns; it
deliberately does not carry a per-system capability table, because a
frozen copy of researched facts rots silently and biases authoring
toward whichever systems happen to be listed. Never infer a system's
capability from a similar one, however wire-compatible
(`spec-tls.md` states the same rule for TLS vocabularies).

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

`cursor.adbc_ingest(...)` hands Arrow buffers to the system's own bulk
protocol with no row-by-row path. This tier is reachable only for the
values the contract's `AdbcTransport.driver` enum admits — currently
`postgresql`, `snowflake`, `bigquery` — so the enum, not a curated list
of systems, is what decides eligibility. Confirm the system has a
production ADBC driver via `provider_facts.adbc_driver_package`; an
upstream driver that exists but is absent from the enum does not qualify
(see below).

An ADBC transport declares that landing as
`sql_capabilities.bulk_load: {"adbc": "adbc_ingest"}`. It is the backend's
own native path, so — unlike every other mechanism — it obliges **no**
dialect code, which is what makes this tier cheap
(`spec-sql-write-path.md`).

That enum is the **sole validator** for ADBC driver values.
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

**Wire compatibility is not driver compatibility.** A system reachable
over another system's wire protocol may still take an entirely different
option surface — TLS parameters especially — so an ADBC driver that
*connects* is not evidence that it is the right transport. Research the
actual driver the connector will ship (`spec-tls.md`), and prefer the
system's own canonical path even when a compatible driver exists.

## 2. Flight SQL

`adbc-driver-flightsql` reaches any server implementing the Arrow Flight
SQL protocol, which is what makes it a tier of its own rather than a
per-system entry. Two caveats: the target must genuinely expose a Flight
SQL endpoint — established from the vendor's docs into
`provider_facts.flight_sql_endpoint`, never assumed from a system being
"modern" — and `flightsql` is not currently in the `AdbcTransport.driver`
enum, so this tier needs the same contract change as any other missing
driver before it can be declared.

Today this tier is **unreachable**: `flightsql` is not in the
`AdbcTransport.driver` enum, so selecting it is the same contract-gap
path as §1. Record the server's Flight SQL support in research; do not
author the transport.

## Do not use the JDBC bridge

`adbc-driver-jdbc` wraps any JDBC driver and presents an ADBC API over
it, but underneath it still binds row-by-row through JDBC. It buys the
interface, not the performance, and it is never the right answer here —
a system whose only ADBC-shaped option is the JDBC bridge takes the
SQLAlchemy transport instead, at tier 3 or 4.

## 3. Native bulk-load protocols (no ADBC)

The connect/DDL layer stays on the SQLAlchemy transport; the bulk write
goes through the **declared** mechanism — the connector names it under
`sql_capabilities.bulk_load.sqlalchemy` and implements the dialect's
`bulk_land` hook. Declaring a mechanism without the hook (or the hook
without a declaration) fails the CDK conformance kit.

The mechanism vocabulary is **closed and contract-owned**, so this tier
is reachable only when the system's documented protocol maps onto one of:

| Mechanism | The protocol it names |
|---|---|
| `copy_from` | A server-side copy of a bulk stream (`COPY FROM stdin`) |
| `load_data_local_infile` | A server-side read of a client-supplied delimited file |
| `load_job` | A batch-load API job submitted out of band |

Three consequences, and they are where authors go wrong:

- **A tuning knob is not a protocol.** Where the driver's fast path *is*
  `executemany` with the right settings — a raised array size, a batched
  parameter stream — there is no mechanism to declare. That is tier 4
  with `bulk_load: {}`, and it is the correct answer, not a shortfall.
- **A real bulk protocol outside the vocabulary is a contract gap**, to
  raise with the contract owners rather than route through a private
  override. Until it is added, take the next tier.
- **A protocol the deployment cannot rely on is tier 4.** Some require
  server- *and* client-side opt-in that is off by default, so a connector
  that cannot guarantee both declares `bulk_load: {}` instead of shipping
  a path that fails at runtime. `provider_facts.bulk_load_protocol` names
  the protocol; whether the deployment can actually use it is an
  authoring judgement.

Which mechanism (if any) a given system supports comes from research, not
from this file — see the note at the top.

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
