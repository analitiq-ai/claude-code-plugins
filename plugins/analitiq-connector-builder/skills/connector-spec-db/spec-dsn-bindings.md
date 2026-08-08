# DSN URL templates + bindings

The full authoring contract for `transports.<name>.dsn` when
`dsn.kind == "url_template"`. Applies identically to `sqlalchemy` and
`adbc` transport types — the DSN shape is shared. The transport-specific
fields are:

| `transport_type` | Identity field | Extras |
|---|---|---|
| `sqlalchemy` | `driver` — a `dialect+driver`, sync or async (e.g. `"postgresql+asyncpg"`, `"mysql+aiomysql"`, `"redshift+redshift_connector"`; dispatch is engine-side — see `spec-driver-selection.md` §Constraints). Optional in the contract, since SQLAlchemy can derive it from the DSN's scheme — but **declare it anyway**: it is the one place a reader can see the sync/async choice was deliberate. | optional `tls` block (`ssl_mode` + `ssl_ca_certificate` refs; mode vocabulary is connector-defined) |
| `adbc` | `driver` — a closed enum owned by the contract's `AdbcTransport` (see `spec-driver-selection.md` §1) | `db_kwargs` (object; values may be value expressions). **AdbcTransport requires at least one of `dsn` / `db_kwargs`** (ADV-CTOR-004). TLS lives inside `db_kwargs` (e.g. `adbc.postgresql.sslmode`); no `tls` block. |

Transport choice follows the decision order in `spec-driver-selection.md`
(`ADV-CTOR-027`). The chosen driver ships ONLY in the connector's
`requirements.txt` (the engine pins no database drivers). ADBC drivers that accept all connection
state via `db_kwargs` (e.g. Snowflake) may omit `dsn` entirely.

## Shape

See the `transports.database.dsn` block in
`examples/postgresql/postgresql.example.json` — the reference `url_template` +
`bindings` shape, with the canonical per-field encodings.

## Rules

- `template` is a connector-authored string with `{placeholder}` markers.
  A `${...}` context reference never appears in the template (`ADV-SHRD-006`) —
  those go inside binding `value` expressions.
- Every placeholder in the template must have a matching binding key, and
  every binding key must be referenced by the template (ADV-CTOR-011).
- Each binding declares:
  - `value` — a value expression the runtime resolves at connection time
    (`ADV-CTOR-035`); the grammar is in
    `connector-builder/references/value-expressions.md`.
  - `encoding` — see "Choosing an encoding" below.

## Choosing an encoding (`ADV-CTOR-018`)

The vocabulary is closed and the registry prints its members; what it cannot
tell you is which member a binding takes. That is decided by the URL position
the value is substituted into:

| Where the value lands in the URL | Encoding |
|---|---|
| Numeric or already-safe values (port, integers) — no encoding | `raw` |
| Hostname — IPv6 brackets, IDN punycode | `host` |
| Userinfo: passwords, usernames (RFC 3986) | `url_userinfo` |
| Path segment: database names that may contain special chars | `url_path_segment` |
| Query key | `url_query_key` |
| Query value: warehouse, schema, other query parameters | `url_query_value` |

## Authoring checklist

1. Pick the canonical DSN form for the driver (look at SQLAlchemy /
   driver documentation).
2. Write the template with one `{placeholder}` per logical field.
3. For each placeholder, declare the binding's `value` and `encoding`.
4. Resolve a credential from the `secrets` scope (`secrets.password`), never
   from `connection.parameters` (`ADV-CTOR-046`).
5. Never pre-encode any value (`ADV-CTOR-034`) — a pre-encoded password renders
   a DSN that looks correct and carries the wrong credential.

## Driver examples

| Driver | Template |
|---|---|
| `postgresql+asyncpg` | `postgresql+asyncpg://{username}:{password}@{host}:{port}/{database}` |
| `mysql+aiomysql` | `mysql+aiomysql://{username}:{password}@{host}:{port}/{database}` |
| `redshift+redshift_connector` | `redshift+redshift_connector://{username}:{password}@{host}:{port}/{database}` |

These are SQLAlchemy transports (DSN `url_template`) — the first two
async, the third a sync driver (Redshift). ADBC drivers
differ by driver: a driver may carry all connection state in `db_kwargs`
and omit the DSN entirely (Snowflake authenticates this way), while
`postgresql` keeps core coordinates in a `dsn` `url_template` and
reserves `db_kwargs` for driver-namespaced extras like TLS — see the
`postgresql-adbc` reference example for the DSN-plus-`db_kwargs` shape.
