# The SQL write path

Every SQL write — every write mode, on either
transport — is **stage-then-merge**: the engine creates a stage relation,
lands the batch into it, applies one mode statement against the target,
and drops the stage. The engine owns the plan and picks the mode
statement; the connector supplies the **dialect-specific SQL text** and
**declares what its system can do**. A connector never branches on the
write mode.

That splits a write-capable database connector's obligations in two, and
they are checked **against each other**:

| Where | What |
|---|---|
| `definition/connector.json` | the `sql_capabilities` block — declared facts |
| `connector.py`'s dialect | the statement renderers those facts oblige |

The engine **refuses, it does not guess**: it reads the declaration
instead of probing the live database, and refuses at handshake time when
a needed fact was not declared. The CDK conformance kit checks the pair
both ways — a declaration with no hook behind it, and a hook nothing
routes to, are both violations. Author them together, never one alone.

## `sql_capabilities` — the declaration

Top-level in `connector.json`, beside `transports`. The contract makes it
**optional**; the engine makes it **functionally mandatory for any
write-capable connector** — omit it and every write mode is refused at
handshake. A database connector is write-capable by definition (read and
write are both first-class), so declare it.

A declared block is **complete**: all five shape facts are required.
A partial declaration is a config error, not a request for defaults.

| Fact | Values | How to choose |
|---|---|---|
| `catalog` | `none` / `read` / `full` | The system's multi-database reality. `none` — no catalog concept, or catalogs exist but a connection cannot address across them (Postgres, MySQL). `read` — the catalog is addressable but the engine may not create it. `full` — the engine may create and drop catalogs. Must agree with the system's real object hierarchy (`spec-resource-discovery.md`): a three-level system (catalog → schema → table) cannot claim `none`. |
| `session_targeting` | `per_statement` / `session_default` | How the write target is selected. `per_statement` — every statement is fully qualified; the portable choice, correct wherever statements can name `schema.table`. `session_default` — the target is established once as session state (`search_path`, `USE`) and inherited; declare it only when the system genuinely requires it. |
| `merge_form` | `merge` / `insert_on_conflict` / `insert_on_duplicate_key` / `none` | The upsert grammar the system actually supports: SQL-standard `MERGE`; Postgres-style `INSERT … ON CONFLICT`; MySQL-style `INSERT … ON DUPLICATE KEY`; or `none` when the system has no native upsert. Anything other than `none` obliges `merge_statement_sql`. |
| `bulk_load` | per-transport object | Which bulk mechanism each SQL transport family lands with — see below. Required as an object; `{}` is a complete, valid declaration meaning "no bulk mechanism anywhere, land via executemany". |
| `stage` | object | The staging-relation facts — see below. |
| `limits` | object, optional | Declared driver caps: `max_bind_params` (e.g. 2100 on SQL Server), `max_identifier_len` **in bytes** (e.g. 63 on Postgres, NAMEDATALEN − 1 — not characters, which differ on any multibyte identifier). Additive — absence means "no declared cap", never a refusal. Declare a cap the driver genuinely has; omit otherwise. |

### `bulk_load` — per transport, not per connector

A bulk mechanism is a fact about a **transport**, not the connector:
`copy_from` needs the driver's wire connection, `adbc_ingest` an ADBC
cursor. So `bulk_load` maps each SQL transport family it applies to its
mechanism.

| Family | Mechanisms |
|---|---|
| `sqlalchemy` | `copy_from` / `load_data_local_infile` / `load_job` |
| `adbc` | `adbc_ingest` / `copy_from` / `load_data_local_infile` / `load_job` |

- **Absence of a key is the only "none"** — that family lands via
  executemany. Never write `"sqlalchemy": null`; omit the key.
- `copy_from`, `load_data_local_infile`, `load_job` are
  **dialect-implemented** — declaring one on either family obliges the
  `bulk_land` hook.
- `adbc_ingest` is the ADBC backend's **own** native landing, valid only
  under `adbc`, and involves no dialect code — declaring it obliges
  nothing. This is what makes the ADBC tier of the driver-selection
  decision order cheap (`spec-driver-selection.md`).
- A declared family must be a transport the connector actually ships.

### `stage`

| Field | Values | How to choose |
|---|---|---|
| `scope` | `temp` / `real` | `temp` — a session/transaction-scoped temporary table. `real` — an ordinary table the engine creates and drops around the write, for systems with no usable temp relation. The engine passes this through to `stage_table_sql`'s `temp` argument. |
| `schema` | `target` / `dedicated` | `target` co-locates the stage in the target's own schema. `dedicated` places it in a separate schema, and then `dedicated_schema` names it — required exactly when `schema` is `dedicated`, and omitted (or explicitly `null`) otherwise. |
| `transactional_ddl` | `true` / `false` | Whether the system runs `CREATE`/`DROP` **inside** the write transaction. `false` for engines that auto-commit DDL (MySQL), which forces the engine onto a per-step staging strategy. A guess here is a correctness bug, not a tuning knob — take it from the system's documented DDL behaviour. |

## The dialect renderers

The write-path hooks are plain methods on `cdk.sql.dialects.SqlDialect`,
overridden in the connector package's own dialect subclass. The two
renderers return **statement text** — they execute nothing and never see
records. `bulk_land` is the one exception, and the only hook here that
performs I/O: it receives a live connection and the batch itself, because
a bulk mechanism *is* the act of landing data.

```python
from collections.abc import Sequence

from cdk.sql.dialects import SqlDialect, TableAddress
```

| Hook | Required when | Signature |
|---|---|---|
| `stage_table_sql` | **always**, for every write-capable connector, whatever `merge_form` says | `(self, stage: TableAddress, target: TableAddress, *, temp: bool) -> str` |
| `merge_statement_sql` | `merge_form != "none"` — and **forbidden** when it is `none` | `(self, stage: TableAddress, target: TableAddress, conflict_keys: Sequence[str], columns: Sequence[str]) -> str` |
| `bulk_land` | a `bulk_load` entry names a dialect-implemented mechanism — and **forbidden** otherwise | `(self, conn, stage: TableAddress, batch, *, runtime) -> bool` — a plain `def`, never `async` |

`temp` is keyword-only and derives from `stage.scope`; do not re-derive
it. `stage_table_sql` renders `CREATE [TEMPORARY] TABLE <stage>` shaped
like `<target>` — the column-copy syntax is the vendor's
(Postgres `(LIKE target INCLUDING DEFAULTS)`, MySQL `LIKE target`).

`bulk_land` returns `True` when it landed the batch and `False` to
decline; the engine falls back to executemany on `False` and verifies the
landed row count either way.

### `bulk_land` on an async driver

Every dialect hook is called synchronously — the conformance kit rejects
an `async def` override outright — but an async driver's bulk API returns
a coroutine. The two are bridged, not reconciled:

- `conn` is the **sync-facing** SQLAlchemy `Connection`. On the async
  path the engine obtains it via `AsyncConnection.run_sync(...)`, so the
  hook already runs inside SQLAlchemy's greenlet context.
- Reach the raw driver object through `conn.connection.driver_connection`
  (not `.dbapi_connection`, which is the DBAPI-shaped adapter).
- Drive its coroutine with `sqlalchemy.util.await_only`, which is legal
  precisely *because* of that greenlet context.

```python
from sqlalchemy.util import await_only


class {Name}Dialect(SqlDialect):
    def bulk_land(self, conn, stage, batch, *, runtime) -> bool:
        columns = list(batch.schema.names)
        records = [tuple(row[c] for c in columns) for row in batch.to_pylist()]
        await_only(
            conn.connection.driver_connection.copy_records_to_table(
                stage.table,
                # `stage.schema` is empty only for a temp-scope stage; a
                # real-scope one is qualified and MUST be targeted as such.
                schema_name=stage.schema or None,
                records=records,
                columns=columns,
            )
        )
        return True
```

The CDK exposes **no wrapper of its own** for this, so `sqlalchemy.util`
is the one import outside the CDK-and-your-own-driver rule that a
connector may legitimately reach for — and only here.

**Target the stage's full address, not just its table name.** The engine
leaves a `temp`-scope stage unqualified, but a `real`-scope one carries a
schema — the target's, or the dedicated one. A bulk mechanism that passes
only the bare table name resolves it against whatever the session's
default happens to be, so it works on temp scope and silently lands in
the wrong schema on real scope. Nothing catches this: the engine verifies
the landed **row count**, not where the rows landed.

### The no-op degradation is a hard requirement

`merge_statement_sql` must render a valid statement **when every landed
column is a conflict key** — there is then nothing to update. Render the
form's insert-only degradation (Postgres `ON CONFLICT DO NOTHING`, MySQL's
documented `key = key` self-assignment, a `MERGE` with no `WHEN MATCHED`):
matched rows keep their stored values, and the statement never errors. An
empty `ON DUPLICATE KEY UPDATE` / `DO UPDATE SET` is invalid SQL, and the
conformance kit tests this case explicitly.

### Addressing and quoting

`TableAddress` is a frozen `(table, schema, catalog)` value from
`cdk.sql.dialects`. Render it with the base's own helpers — `quote_ident`
for a bare identifier, `quote_table` for an address — so `quote_char` and
the system-schema rules apply. A catalog without a schema is refused at
construction.

The address **factory** `table_address()` and the bound `capabilities`
attribute are framework-owned: use them, never override them.

## What the connector must not do

- **No stage naming.** Stage names are engine-generated and deterministic,
  so a retry of the same batch reuses the name and self-heals leftovers.
  A connector that invents a stage name breaks idempotency.
- **No side ledger.** Idempotency is content-derived (the contract primary
  key, or the engine's synthetic record-hash identity) plus the
  deterministic stage name. There is no batch-commit table and no key-type
  hook for one.
- **No private overrides.** Overriding a `_`-prefixed CDK member is a
  conformance violation; so is adding a public attribute that is not a
  `SqlDialect` hook. A helper of your own belongs under a leading
  underscore, with a name the base does not use.
- **Nothing on the connector class but `dialect_class`.** All
  system-specific behaviour lives in the dialect, reached through the
  sanctioned hooks. The connector class carries `dialect_class` and
  nothing else — not even an `__init__`.
- **No changed signatures.** An override must accept every call shape the
  base admits: same parameter names, same keyword-only markers, same
  async-ness. The conformance kit compares shapes, not just names.

## Worked example — MySQL

MySQL: no catalog, fully-qualified statements, `ON DUPLICATE KEY UPDATE`,
no wired bulk path (`LOAD DATA LOCAL INFILE` needs `local_infile` enabled
on **both** server and client, off by default on MySQL 8.0), temp staging
in the target schema, and DDL that auto-commits.

```json
"sql_capabilities": {
  "catalog": "none",
  "session_targeting": "per_statement",
  "merge_form": "insert_on_duplicate_key",
  "bulk_load": {},
  "stage": {
    "scope": "temp",
    "schema": "target",
    "transactional_ddl": false
  }
}
```

```python
class MySQLDialect(SqlDialect):
    name = "mysql"
    quote_char = "`"

    def stage_table_sql(
        self, stage: TableAddress, target: TableAddress, *, temp: bool
    ) -> str:
        keyword = "CREATE TEMPORARY TABLE" if temp else "CREATE TABLE"
        return f"{keyword} {self.quote_table(stage)} LIKE {self.quote_table(target)}"

    def merge_statement_sql(
        self,
        stage: TableAddress,
        target: TableAddress,
        conflict_keys: Sequence[str],
        columns: Sequence[str],
    ) -> str:
        column_list = ", ".join(self.quote_ident(c) for c in columns)
        statement = (
            f"INSERT INTO {self.quote_table(target)} ({column_list}) "
            f"SELECT {column_list} FROM {self.quote_table(stage)} "
        )
        keys = set(conflict_keys)
        update_columns = [c for c in columns if c not in keys]
        if not update_columns:
            # Every landed column is a conflict key: MySQL rejects an empty
            # ON DUPLICATE KEY UPDATE, so render its documented
            # self-assignment no-op on one key column. Indexing is safe
            # here — this hook is only reached for a declared merge_form,
            # i.e. an upsert, which is keyed on at least one column.
            key = self.quote_ident(conflict_keys[0])
            return statement + f"ON DUPLICATE KEY UPDATE {key} = {key}"
        assignments = ", ".join(
            f"{self.quote_ident(c)} = VALUES({self.quote_ident(c)})"
            for c in update_columns
        )
        return statement + f"ON DUPLICATE KEY UPDATE {assignments}"
```

The `insert_on_duplicate_key` form names no match keys in the statement —
MySQL reads them from whichever unique index the conflict lands on — so
`conflict_keys` only selects which landed columns are updated.

`VALUES(col)` is the portable way to reference the incoming row here:
MySQL 8.0.19+ offers a row-alias form instead, but MariaDB — which ships
its own copy of this dialect — does not support it. `VALUES()` is
deprecated on MySQL 8.0.20+ (a warning, not a removal). This is the kind
of per-system judgement the spec cannot make for you: research the
versions the connector targets.

## Enforcement

The plugin's schema validator checks the `sql_capabilities` **shape**
(required facts, closed value sets, the `dedicated_schema` rule). It never
sees `connector.py`, so the declaration ↔ hook pairing is the CDK
conformance kit's job, run by registry CI. Getting the pairing wrong ships
a connector that validates cleanly and is refused at handshake — which is
why the two are authored together here.

**The CDK is the authority on the hook surface; this document restates
it.** The `sql_capabilities` vocabularies above are pinned to the contract
models by `tests/connector_builder/test_schema_drift.py`, but the hook
names and signatures are engine-owned and nothing here can pin them —
they describe the engine's **SQL write path v2** surface (its ADR,
`docs/sql-write-path-v2.md`, is the source of record). Where the
conformance kit and this document disagree, the kit wins, and the
disagreement is a bug to report against this spec.
