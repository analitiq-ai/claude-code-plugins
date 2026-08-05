# Connector package files

A database connector is an installable Python package. The engine image
ships NO database drivers; each connector brings its own. The engine
resolves a connector in two steps: `kind` selects the generic fallback
class, `connector_id` selects the connector package's own class via
Python entry points.

API connectors carry **only** the definition (`connector.json`,
`type-map-read.json`, `endpoints/`) — no package files, no write map.

## Required layout

The connector root IS the Python package:

```
{connector_id}/
  definition/
    connector.json                   # connector_id; transports; sql_capabilities
    type-map-read.json               # native → Arrow; regex patterns UPPERCASE
    type-map-write.json              # Arrow → native; REQUIRED for kind: database
  __init__.py                        # re-exports the connector class
  connector.py                       # {Name}Dialect(SqlDialect) + {Name}Connector(GenericSQLConnector)
  requirements.txt                   # THIS connector's driver(s) only
  pyproject.toml                     # analitiq-connector-{connector_id}; see below
```

`connector_id` in `connector.json` must equal the repo/directory name —
it is the entry-point name the engine resolves.

## `pyproject.toml`

- `name = "analitiq-connector-{connector_id}"`.
- `dynamic = ["dependencies"]` +
  `[tool.setuptools.dynamic] dependencies = { file = ["requirements.txt"] }`
  — `requirements.txt` is the single source of truth for the driver.
- Package mapping (the repo root is the package):
  `packages = ["analitiq_connector_{connector_id}"]`,
  `package-dir = { "analitiq_connector_{connector_id}" = "." }`.
- Entry points — name = `connector_id`, **both roles** (read and write
  are both first-class; never ship a one-directional connector):

  ```toml
  [project.entry-points."analitiq.source_connectors"]
  {connector_id} = "analitiq_connector_{connector_id}.connector:{Name}Connector"

  [project.entry-points."analitiq.destination_connectors"]
  {connector_id} = "analitiq_connector_{connector_id}.connector:{Name}Connector"
  ```

- The CDK is provided by the engine environment — never list it as a
  dependency.

Template (postgres reference):

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "analitiq-connector-{connector_id}"
version = "0.1.0"
description = "Analitiq connector for {DisplayName}: dialect, driver, definition."
requires-python = ">=3.11"
dynamic = ["dependencies"]

[tool.setuptools.dynamic]
dependencies = { file = ["requirements.txt"] }

[tool.setuptools]
packages = ["analitiq_connector_{connector_id}"]
package-dir = { "analitiq_connector_{connector_id}" = "." }

[project.entry-points."analitiq.source_connectors"]
{connector_id} = "analitiq_connector_{connector_id}.connector:{Name}Connector"

[project.entry-points."analitiq.destination_connectors"]
{connector_id} = "analitiq_connector_{connector_id}.connector:{Name}Connector"
```

## `requirements.txt`

THIS connector's driver(s) only — the SQLAlchemy DBAPI (sync or async)
for SQLAlchemy transports and/or the `adbc-driver-{driver}` wheel (+
`adbc-driver-manager`) for ADBC transports. See
`spec-driver-selection.md` for choosing. Comment non-obvious pins (e.g.
`pymysql<1.2`).

## `connector.py`

One dialect class plus one connector class:

```python
from cdk.sql.dialects import SqlDialect, TableAddress
from cdk.transport_factory import ca_ssl_context
from cdk.sql.generic import GenericSQLConnector


class {Name}Dialect(SqlDialect):
    name = "{dialect_name}"
    system_schemas = (...)           # catalog schemas to exclude from discovery

    def stage_table_sql(
        self, stage: TableAddress, target: TableAddress, *, temp: bool
    ) -> str:
        ...                          # REQUIRED of every write-capable connector

    ...                              # + whatever sql_capabilities obliges,
                                     #   per spec-sql-write-path.md


class {Name}Connector(GenericSQLConnector):
    dialect_class = {Name}Dialect    # and nothing else on this class
```

### Import rules

A connector depends only on the CDK: `cdk.sql.dialects.SqlDialect` and
`cdk.sql.dialects.TableAddress`, `cdk.sql.generic.GenericSQLConnector`,
`cdk.sql.exceptions`, `cdk.transport_factory.ca_ssl_context`,
`cdk.type_map` — plus the connector's own driver. It never imports
another connector and never imports an engine/runtime. MariaDB ships its
own copy of the mysql-shaped dialect rather than importing the mysql
connector.

The write-path **renderers** return statement text, so they need no
SQLAlchemy construct helpers (`sqlalchemy.dialects.*.insert` and friends
belonged to the removed record-executor surface). `bulk_land` is the
exception: it performs the landing itself, so it reaches for the driver's
own bulk API — and, on an **async** driver, for `sqlalchemy.util.await_only`
to drive that coroutine from a hook the CDK calls synchronously
(`spec-sql-write-path.md`; the CDK provides no wrapper for it).

The standard library is unrestricted — `__future__`, `collections.abc`
for the `Sequence[str]` annotations the renderers take, `typing`, and
`ssl` for building a TLS context. Beyond that and the connector's own
driver, exactly **one** third-party helper is sanctioned, and only where
the hook needing it exists: `sqlalchemy.util.await_only`, for an async
`bulk_land`. Any other third-party import is out of bounds.

### Dialect hooks

The dialect must implement every hook its transports require — missing
hooks fail loudly with `UnsupportedDialectOperationError`:

| Transport feature | Required hook(s) |
|---|---|
| SQLAlchemy + TLS | `build_tls_connect_arg(mode, ca_pem)` — interprets the connector's declared `ssl_mode` vocabulary into the driver's single TLS connect argument (mode string, `False`, or an `SSLContext` built via `ca_ssl_context`); the CDK currently lands it under `connect_args["ssl"]`. When the driver takes TLS through **several** connect parameters instead, override `build_tls_connect_args(mode, ca_pem)` (plural) and return the full connect-args mapping. |
| TLS downgrade check | `verify_tls_state(dbapi_connection, mode)` — the post-connect probe that refuses a TLS-promising mode which landed an unencrypted session. Its mode vocabulary is the one `spec-tls.md` teaches you to research. |
| Writing | `stage_table_sql`, and — paired with what `sql_capabilities` declares — `merge_statement_sql` / `bulk_land`. The write path has its own spec: **`spec-sql-write-path.md`**. |
| Discovery | `schemas_query(catalog="")` and the `system_schemas` exclusion list. |
| Pre-DDL | `sqlalchemy_pre_ddl(schema_name)` when schemas must exist before `create_all` (postgres `CREATE SCHEMA IF NOT EXISTS`). |
| Session setup | `session_init_sql()` for per-connection statements (MySQL's `SET time_zone`). |

### Structural overrides — only where the portable form is invalid

- `current_timestamp_default()` — where the DEFAULT expression must
  carry precision (MySQL/MariaDB: `CURRENT_TIMESTAMP(6)`; the bare form
  is error 1067 against a `DATETIME(6)` column).
- `empty_table_sql(target)` — where the base's ANSI `DELETE FROM` is not
  accepted as written (BigQuery requires a `WHERE` clause). Never
  `TRUNCATE`: its implicit commit breaks the staged write cycle.

### Type vocabulary is declarative-only

The write direction lives in `type-map-write.json` and nowhere else:
every transport (SQLAlchemy DDL, ADBC DDL, control-plane create_table)
renders column types through `dialect.render_column_type`, whose
default is the write map. A dialect overrides it ONLY for logic rules
cannot express (BigQuery's NUMERIC/BIGNUMERIC precision-range
arithmetic) — and even then delegates everything else back to the map.
**Connectors must NOT ship Python type-rendering tables.**

### Thick-path overrides go in the dialect, on sanctioned hooks

When the system needs behavior the generic base cannot express, override
just the quirky hook (the thin → thick gradient) — **on the dialect**.
The connector class carries `dialect_class` and nothing else; the
sanctioned override surface is the public hooks `SqlDialect` itself
declares, minus the framework-owned `capabilities` and `table_address`.
Anything outside that — a private CDK internal, an invented public
attribute, an extra member on the connector class — fails the CDK
conformance kit's surface check. A helper of your own belongs under a
leading underscore, with a name the base does not use.

Systems on decision-order step 3 reach their native bulk-load path the
same way: declare the mechanism in `sql_capabilities.bulk_load` and
implement `bulk_land`, never a private override against the raw cursor
(`spec-sql-write-path.md`).

## `__init__.py`

```python
"""analitiq-connector-{connector_id}: {DisplayName} connector package for Analitiq."""

from .connector import {Name}Connector, {Name}Dialect

__all__ = ["{Name}Connector", "{Name}Dialect"]
```

## Enforcement

The plugin's schema validator checks JSON documents only. Package files
are enforced by registry CI: `pip wheel --no-deps .` must build, the
wheel must contain `analitiq_connector_{id}/connector.py` plus every
entry point, and the CDK **conformance kit** must pass — it audits the
dialect's override surface and checks the `sql_capabilities` declaration
against the hooks the package actually implements
(`spec-sql-write-path.md`).
