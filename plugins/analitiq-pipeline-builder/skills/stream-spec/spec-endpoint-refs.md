# `endpoint_ref` shape

The `source` and every `destinations[]` entry carry an `endpoint_ref`. It is a
**discriminated union on `scope`** (`analitiq.contracts.stream.EndpointRef`) —
each scope has its own shape and its own required fields, tabled below.

## Contents

- Prefer the ref discovery handed you
- `scope: "connector"` — public connector endpoint (API)
- `scope: "connection"` — private database endpoint
- `connection_id`
- Uniqueness
- Cross-document consistency
- Connector-side endpoint verification (`connector-endpoint-ref`)

## Prefer the ref discovery handed you

When endpoint discovery (or a downloaded connector's endpoint set, or a
`private-endpoint-creator` result) already produced an `endpoint_ref` object,
submit it **as it stands**. Do not re-derive it, re-case it, drop fields you
judge redundant, or "tidy" the object shape. A rewritten ref is the single most
common way a stream stops resolving against the endpoint it was built for.

## `scope: "connector"` — public connector endpoint (API)

<!-- BEGIN GENERATED: fields-connector-endpoint-ref -->
`analitiq.contracts.stream.ConnectorEndpointRef` — closed (`additionalProperties: false`); required: `connection_id`, `endpoint_id`, `scope`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `connection_id` | **yes** | string | — | `pattern=\S`, `minLength=1` |
| `scope` | **yes** | const 'connector' | — | — |
| `endpoint_id` | **yes** | string | — | `minLength=1` |
<!-- END GENERATED: fields-connector-endpoint-ref -->

Refers to a public endpoint baked into the connector document (typically API
endpoints), pinned by the connection's `connector_version` at runtime.
`endpoint_id` matches a key under the connector's `definition/endpoints/*.json`.

## `scope: "connection"` — private database endpoint

<!-- BEGIN GENERATED: fields-connection-endpoint-ref -->
`analitiq.contracts.stream.ConnectionEndpointRef` — closed (`additionalProperties: false`); required: `connection_id`, `database_object`, `scope`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `connection_id` | **yes** | string | — | `pattern=\S`, `minLength=1` |
| `scope` | **yes** | const 'connection' | — | — |
| `database_object` | **yes** | DatabaseObject | — | — |
| `endpoint_id` | no | string \| null | `None` | — |
<!-- END GENERATED: fields-connection-endpoint-ref -->

Refers to a private, connection-scoped database endpoint produced by
introspection. `database_object` carries the verbatim database-object identity
recorded on the endpoint document (its fields:
`../endpoint-spec/spec-database-object.md`) — author it from the endpoint doc's
`database_object`, i.e. the `build_database_object(...)` output, so the two
always agree.

Omit `endpoint_id` and the contract derives it; supply it and the contract
verifies it against the derivation (`RULE-STRM-003`). Author it whenever the
plugin can compute it (`RULE-STRM-018`).

<!-- BEGIN GENERATED: endpoint-id-derivation -->
A database `endpoint_id` is **derived**, not chosen: it is a deterministic handle over the endpoint's verbatim locator, computed by `analitiq.contracts.endpoint_identity.derive_db_endpoint_id(catalog, schema, name)`.

| `catalog` | `schema` | `name` | derived `endpoint_id` |
|---|---|---|---|
| — | `public` | `orders` | `public__orders__371c8422` |
| `cat` | `Public` | `Orders` | `public__orders__cat__a688ced5` |

Derivation must stay deterministic: a handle that changes for an unchanged resource mints a new endpoint and breaks every stream pinned to the old one. Never hand-write one — call the helper (`scripts/endpoint_id.py` wraps it).
<!-- END GENERATED: endpoint-id-derivation -->

That derived handle is an **Analitiq slug, not a database object name**: never
parse schema, table or catalog identity back out of it (`RULE-DBEP-007`,
`RULE-SHRD-005`) — recognizable-looking segments are an artifact of the
derivation, not an interface. When something needs the database's own identity —
displaying it, comparing it, driving DDL — read `database_object`, which is why
the ref carries it.

A `scope: "connection"` ref is equally valid on a stream's `source` and on a
`destinations[]` entry; nothing about a private endpoint restricts it to
reading.

`scope: "connection"` is valid only for **database** endpoints
(`RULE-STRM-031`); `stream-creator` refuses that combination.

## `connection_id`

Copy the connection reference the parent pipeline declares for that side,
verbatim — `pipeline.connections.source` for the stream source, one of
`pipeline.connections.destinations[]` for each destination (`RULE-STRM-033`).
Never re-case it, strip a suffix from it, or reconstruct it from parts.

## Uniqueness

Destination `endpoint_ref`s must be unique within a single stream
(`RULE-STRM-001`; `spec-destinations.md`).

## Cross-document consistency

<!-- PROBE: stream-cross-document-unchecked-alone, stream-connection-role-bundle-rejected, stream-connection-endpoint-bundle-rejected -->
The connection roles (`RULE-STRM-033`) and connection-scoped endpoint
resolution (`RULE-STRM-034`) are checked only with `--bundle-root`, since
neither can be settled from the stream document alone.

## Connector-side endpoint verification (`connector-endpoint-ref`)

<!-- PROBE: connector-endpoint-ref-warned -->
With `--bundle-root`, the plugin also checks each `scope: "connector"` ref
against the connector's endpoint set on disk (`scripts/validate.py`, check id
`connector-endpoint-ref`). It is a **warning**, carrying a closest-match
alignment suggestion ("Did you mean `transfers`?"): the orchestrator surfaces
it and, on the user's confirmation, aligns the stream's
`endpoint_ref.endpoint_id` to the connector's endpoint name. The plugin never
edits the connector — endpoint refs live only in streams, so alignment is
always a stream edit.

<!-- PROBE: connector-endpoint-ref-skipped-undownloaded -->
A ref whose connector publishes no endpoints on disk is skipped, not warned — no
warning is not proof the endpoint exists.
