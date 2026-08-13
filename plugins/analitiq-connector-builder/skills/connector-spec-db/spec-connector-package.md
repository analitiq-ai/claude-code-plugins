# Connector package files

A database connector is an installable Python package. The engine image
ships NO database drivers; each connector brings its own. The engine
resolves a connector in two steps: `kind` selects the generic fallback
class, `connector_id` selects the connector package's own class via
Python entry points.

An `api` connector ships the definition (`connector.json`,
`type-map-read.json`, `endpoints/`) and a README (`RULE-PKG-025`) — no Python
package files (`RULE-CTOR-043`) and no write map (`RULE-PKG-030`).

## Contents

- Required layout
- `pyproject.toml` (`RULE-PKG-007`)
- `requirements.txt`
- `connector.py`
- `__init__.py` (`RULE-PKG-009`)
- Enforcement

## Required layout

The connector root IS the Python package (`RULE-PKG-002`):

```
{connector_id}/
  definition/
    connector.json                   # connector_id; transports; sql_capabilities
    type-map-read.json               # native → Arrow; see spec-type-maps.md
    type-map-write.json              # Arrow → native; RULE-PKG-030
  __init__.py                        # RULE-PKG-009
  connector.py                       # {Name}Dialect(SqlDialect) + {Name}Connector(GenericSQLConnector)
  requirements.txt                   # THIS connector's driver(s) only
  pyproject.toml                     # see below
  README.md                          # RULE-PKG-025
```

The release directory is named for the `connector_id` its `connector.json`
declares (`RULE-CTOR-042`) — the same slug the engine resolves the connector
class by (`RULE-PKG-007`).

## `pyproject.toml` (`RULE-PKG-007`)

- Dependencies are declared dynamically, so `requirements.txt` stays the
  single source of truth for the driver (`RULE-PKG-006`).
- Entry points registered for read **and** write (`RULE-PKG-008`) — never
  ship a one-directional connector; both groups are in the template below.

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

The driver each declared transport needs, and nothing else
(`RULE-PKG-027`). See `spec-driver-selection.md` for choosing. Comment
non-obvious pins (e.g. `pymysql<1.2`).

## `connector.py`

One dialect class plus one connector class (`RULE-PKG-010`):

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
        ...                          # RULE-PKG-017

    ...                              # + whatever sql_capabilities obliges,
                                     #   per spec-sql-write-path.md


class {Name}Connector(GenericSQLConnector):
    dialect_class = {Name}Dialect    # RULE-PKG-010
```

### Import rules (`RULE-PKG-011`)

**The CDK is the authority on every symbol and hook this page names; this
document restates them** (`RULE-PKG-003`). Where the CDK and this page
disagree, the CDK wins, and the disagreement is a bug to report against this
spec.

The CDK surface a connector reaches for: `cdk.sql.dialects.SqlDialect`
and `cdk.sql.dialects.TableAddress`,
`cdk.sql.generic.GenericSQLConnector`, `cdk.sql.exceptions`,
`cdk.transport_factory.ca_ssl_context`, `cdk.type_map` — plus the
connector's own driver. MariaDB is the worked case: it ships its own
copy of the mysql-shaped dialect rather than importing the mysql
connector.

The write-path **renderers** return statement text, so they need no
SQLAlchemy construct helpers — `sqlalchemy.dialects.*.insert` and friends
are outside the import surface `RULE-PKG-011` admits. `bulk_land` is the
exception: it performs the landing itself, so it reaches for the driver's
own bulk API — and, on an **async** driver, for `sqlalchemy.util.await_only`
to drive that coroutine from a hook the CDK calls synchronously
(`spec-sql-write-path.md`; the CDK provides no wrapper for it).

The standard library is unrestricted — `__future__`, `collections.abc`
for the `Sequence[str]` annotations the renderers take, `typing`, and
`ssl` for building a TLS context.

### Dialect hooks

Missing hooks fail loudly with `UnsupportedDialectOperationError`
(`RULE-PKG-003`):

| Transport feature | Required hook(s) |
|---|---|
| SQLAlchemy + TLS (`RULE-PKG-021`) | `build_tls_connect_arg(mode, ca_pem)` — interprets the connector's declared `ssl_mode` vocabulary into the driver's single TLS connect argument (mode string, `False`, or an `SSLContext` built via `ca_ssl_context`); the CDK currently lands it under `connect_args["ssl"]`. When the driver takes TLS through **several** connect parameters instead, override `build_tls_connect_args(mode, ca_pem)` (plural) and return the full connect-args mapping. |
| TLS downgrade check (`RULE-PKG-021`) | `verify_tls_state(dbapi_connection, mode)` — the post-connect probe that refuses a TLS-promising mode which landed an unencrypted session. Its mode vocabulary is the one `spec-tls.md` teaches you to research. |
| Writing | `stage_table_sql`, and — paired with what `sql_capabilities` declares — `merge_statement_sql` / `bulk_land`. The write path has its own spec: **`spec-sql-write-path.md`**. |
| Discovery | `schemas_query(catalog="")` and the `system_schemas` exclusion list. |
| Pre-DDL | `sqlalchemy_pre_ddl(schema_name)` when schemas must exist before `create_all` (postgres `CREATE SCHEMA IF NOT EXISTS`). |
| Session setup | `session_init_sql()` for per-connection statements (MySQL's `SET time_zone`). |

### Structural overrides — only where the portable form is invalid (`RULE-PKG-001`)

- `current_timestamp_default()` — where the DEFAULT expression must
  carry precision (MySQL/MariaDB: `CURRENT_TIMESTAMP(6)`; the bare form
  is error 1067 against a `DATETIME(6)` column).
- `empty_table_sql(target)` — where the base's ANSI `DELETE FROM` is not
  accepted as written (BigQuery requires a `WHERE` clause). Never
  `TRUNCATE` (`RULE-PKG-015`).

### Type vocabulary is declarative-only (`RULE-PKG-023`)

Every transport (SQLAlchemy DDL, ADBC DDL, control-plane create_table)
renders column types through `dialect.render_column_type`, whose
default is the write map. The logic a rule cannot express is the only
thing that earns an override (BigQuery's NUMERIC/BIGNUMERIC
precision-range arithmetic) — and even then it delegates everything else
back to the map.

### Thick-path overrides go in the dialect, on sanctioned hooks

When the system needs behavior the generic base cannot express, override
just the quirky hook (the thin → thick gradient) — **on the dialect**
(`RULE-PKG-001`). The sanctioned surface is the public hooks `SqlDialect`
itself declares (`RULE-PKG-012`); anything outside it fails the CDK
conformance kit's surface check.

Systems whose native bulk-load path rides the SQLAlchemy transport
(`spec-driver-selection.md`, the decision order) reach it the same way:
declare the mechanism in `sql_capabilities.bulk_load` and implement
`bulk_land` (`RULE-PKG-016`), never a private override against the raw
cursor (`spec-sql-write-path.md`).

## `__init__.py` (`RULE-PKG-009`)

```python
"""analitiq-connector-{connector_id}: {DisplayName} connector package for Analitiq."""

from .connector import {Name}Connector, {Name}Dialect

__all__ = ["{Name}Connector", "{Name}Dialect"]
```

## Enforcement

<!-- PROBE: sql-capabilities-pairing-unchecked -->
The plugin's schema validator checks JSON documents only. Package files
are enforced by registry CI: `pip wheel --no-deps .` must build, the
wheel must contain `analitiq_connector_{id}/connector.py` plus every
entry point, and the CDK **conformance kit** must pass — it audits the
dialect's override surface and checks the `sql_capabilities` declaration
against the hooks the package actually implements
(`spec-sql-write-path.md`).
