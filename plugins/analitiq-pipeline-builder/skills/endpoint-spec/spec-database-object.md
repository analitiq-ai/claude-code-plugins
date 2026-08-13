# `database_object` block

<!-- BEGIN GENERATED: fields-database-object -->
`analitiq.contracts.endpoints.DatabaseObject` — closed (`additionalProperties: false`); required: `name`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `catalog` | no | string \| null | `None` | `minLength=1` |
| `schema` | no | string \| null | `None` | `minLength=1` |
| `name` | **yes** | string | — | `minLength=1` |
| `object_type` | no | string \| null | `None` | — |
<!-- END GENERATED: fields-database-object -->

Which provider concept lands in `catalog` and `schema` is dialect-specific — see
the tables below.

## Identifier preservation (`RULE-DBEP-009`)

PostgreSQL is case-sensitive when quoted, BigQuery names are case-sensitive in
the catalog API, and MongoDB collection names are case-sensitive throughout.
Whatever the source database reports, that's what goes here.

## Derived `endpoint_id`

`database_object` and the endpoint's `endpoint_id` are two views of one identity.

<!-- BEGIN GENERATED: endpoint-id-derivation -->
A database `endpoint_id` is **derived**, not chosen: it is a deterministic handle over the endpoint's verbatim locator, computed by `analitiq.contracts.endpoint_identity.derive_db_endpoint_id(catalog, schema, name)`.

| `catalog` | `schema` | `name` | derived `endpoint_id` |
|---|---|---|---|
| — | `public` | `orders` | `public__orders__371c8422` |
| `cat` | `Public` | `Orders` | `public__orders__cat__a688ced5` |

Derivation must stay deterministic: a handle that changes for an unchanged resource mints a new endpoint and breaks every stream pinned to the old one. Never hand-write one — call the helper (`scripts/endpoint_id.py` wraps it).
<!-- END GENERATED: endpoint-id-derivation -->

Compute both together with `scripts/endpoint_id.py` (see
`private-endpoint-creator`): the id must equal the handle derived from the
verbatim locator (`RULE-DBEP-011`).

Read database identity from `database_object`, never by splitting `endpoint_id`
(`RULE-DBEP-007`) — slugging folds case and punctuation away, so a consumer that
splits the handle is right for plain lowercase identifiers and wrong for the
first quoted one.

## `name`

The provider-native object identifier, as it appears in the catalog
(`RULE-DBEP-009`).

## `catalog`

The outermost containment level, when the dialect has one. Record every level the
system actually has and invent none it lacks (`RULE-DBEP-005`). A level the
dialect lacks is omitted, never authored as null (`RULE-ENDP-031`):

| Dialect | what goes in `catalog` |
|---|---|
| BigQuery | project ID (`analytics-prod`) |
| Snowflake | database name (`PROD_DB`) |
| SQL Server | database name (`master`) |
| Trino / Presto | catalog name (`hive`) |
| MongoDB | database name (`analytics`) |
| PostgreSQL | database name (often omitted; the connection already identifies it) |

## `schema`

The intermediate namespace:

| Dialect | what goes in `schema` |
|---|---|
| PostgreSQL | schema (`public`) |
| Snowflake / SQL Server | schema (`DBO`) |
| BigQuery | dataset (`warehouse`) |
| MongoDB | (omitted; collections live directly in the database) |

## `object_type`

The label the provider's catalog gives the object, stored verbatim. It is
descriptive only — nothing may branch on it to decide whether the object can be
read or written (`RULE-DBEP-013`). Common values: `table`, `view`,
`materialized_view`, `collection`, `external_table`, `stream`.
