# `source` block

<!-- BEGIN GENERATED: fields-stream-source -->
`analitiq.contracts.stream.StreamSource` — closed (`additionalProperties: false`); required: `endpoint_ref`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `endpoint_ref` | **yes** | ConnectorEndpointRef \| ConnectionEndpointRef (by `scope`) | — | — |
| `selected_columns` | no | array of string \| null | `None` | — |
| `filters` | no | array of Filter \| null | `None` | — |
| `replication` | no | FullRefreshReplication \| IncrementalReplication (by `method`) \| null | `None` | — |
| `database_pagination` | no | OffsetDatabasePagination \| KeysetDatabasePagination (by `type`) \| null | `None` | — |
| `primary_keys` | no | array of string \| null | `None` | — |
<!-- END GENERATED: fields-stream-source -->

The sketch below illustrates a filled-in source; it is not a statement of what
the contract requires.

<!-- validate: stream#/source -->
```jsonc
{
  "source": {
    "endpoint_ref": { /* see spec-endpoint-refs.md */ },
    "selected_columns": ["id", "amount", "updated_at"],
    "filters": [
      {"field": "status", "operator": "eq", "value": "paid"}
    ],
    "replication": {
      "method": "incremental",
      "cursor_field": "updated_at",
      "safety_window_seconds": 300,
      "tie_breaker_fields": ["id"]
    },
    "database_pagination": {
      "type": "offset",
      "order_by_field": "updated_at"
    },
    "primary_keys": ["id"]
  }
}
```

## Contents

- Field references are verbatim
- `selected_columns` (database only, `RULE-STRM-014`)
- `filters`
- `replication`
- `database_pagination` (database only)
- `primary_keys`

## Field references are verbatim

Copy each source-endpoint field name byte-for-byte from the endpoint document
(`RULE-STRM-023`) — `Order_ID` and `order_id` are different fields.

## `selected_columns` (database only, `RULE-STRM-014`)

A field projection over source-endpoint field names (`RULE-STRM-022`). Omit it
unless the user asked for a subset.
<!-- PROBE: stream-selected-columns-unresolved-locally -->
The local validator does **not** resolve column names
against endpoint files — this check happens server-side at save time;
typos surface as a registry rejection rather than a local error.

## `filters`

Stream-owned read predicates; the operator vocabulary is closed and depends on
the source's endpoint scope — see `spec-filter-operators.md`.

A filter may reference a database column that is **not** in
`selected_columns`: the projection controls what is carried to the destination,
the filter controls which rows are read. Filtering on `updated_at` while
projecting only `id` and `amount` is legitimate and common.

## `replication`

`method` selects the variant, and the variant decides the rest of the block
(`RULE-STRM-017`):

<!-- BEGIN GENERATED: fields-stream-replication-full-refresh -->
`analitiq.contracts.stream.FullRefreshReplication` — closed (`additionalProperties: false`); required: `method`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `safety_window_seconds` | no | integer \| null | `None` | `min=0` |
| `tie_breaker_fields` | no | array of string \| null | `None` | — |
| `method` | **yes** | const 'full_refresh' | — | — |
<!-- END GENERATED: fields-stream-replication-full-refresh -->

<!-- BEGIN GENERATED: fields-stream-replication-incremental -->
`analitiq.contracts.stream.IncrementalReplication` — closed (`additionalProperties: false`); required: `cursor_field`, `method`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `safety_window_seconds` | no | integer \| null | `None` | `min=0` |
| `tie_breaker_fields` | no | array of string \| null | `None` | — |
| `method` | **yes** | const 'incremental' | — | — |
| `cursor_field` | **yes** | string | — | `minLength=1` |
<!-- END GENERATED: fields-stream-replication-incremental -->

`replication` is the stream's **policy** declaration, and that is all it is.
Ownership across the system:

| Concern | Owner |
|---|---|
| Replication policy for this stream | the stream (`source.replication`) |
| Which methods a source actually supports | the source endpoint / runtime (`RULE-STRM-025`) |
| How a cursor maps onto a provider request | the API endpoint's `operations.read.replication.cursor_mappings` |
| The current cursor **value** | runtime state — never the stream document |
| Late-arrival safety window | the stream (`source.replication.safety_window_seconds`) |
| Tie-breaking when cursor values collide | contract-specific (`tie_breaker_fields`, database sources only) |

Omitting `replication` is bounded by `RULE-STRM-029`: when the source's
full-refresh support is not established, author an explicit `replication`
policy rather than relying on the omission default.

`cursor_field` is the **source record field** used as the watermark. It is not a
provider request parameter and not a page-ordering key. `RULE-STRM-022`,
applied to `cursor_field`:

- For a database source, `cursor_field` must name a column that exists in the
  source endpoint's schema.
- For an API source, it must match an
  `operations.read.replication.cursor_mappings[].cursor_field` on the endpoint
  exactly — the mapping is what turns the watermark into a request.

Read the field name back to the user rather than guessing it.

`safety_window_seconds` is a late-arrival overlap the author declares — size it
from how late the provider's records arrive, not from a rewind you assume
happens.

## `database_pagination` (database only)

`type` selects the variant:

<!-- BEGIN GENERATED: fields-stream-pagination-offset -->
`analitiq.contracts.stream.OffsetDatabasePagination` — closed (`additionalProperties: false`); required: `type`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `page_size` | no | integer \| null | `None` | `min=1` |
| `type` | **yes** | const 'offset' | — | — |
| `order_by_field` | no | string \| null | `None` | `minLength=1` |
<!-- END GENERATED: fields-stream-pagination-offset -->

<!-- BEGIN GENERATED: fields-stream-pagination-keyset -->
`analitiq.contracts.stream.KeysetDatabasePagination` — closed (`additionalProperties: false`); required: `order_by_field`, `type`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `page_size` | no | integer \| null | `None` | `min=1` |
| `type` | **yes** | const 'keyset' | — | — |
| `order_by_field` | **yes** | string | — | `minLength=1` |
<!-- END GENERATED: fields-stream-pagination-keyset -->

Of this block, only `order_by_field` reaches the runtime: it sets the ORDER BY
the read pages by. On an incremental stream it must name the same field as
`cursor_field` — a mismatch raises before a single record is extracted, so
declare it there only to restate the cursor, never to order by something else.

`type` and `page_size` are declared and validated by the contract but consumed
by nothing: every database source read pages with OFFSET/LIMIT whichever `type`
says, and the LIMIT is `pipeline.runtime.batching.batch_size`. Neither is a
lever — never offer a paging strategy or a page size as a way to tune a read,
and never author `page_size` expecting it to bound one.

`order_by_field` names a source-endpoint field (`RULE-STRM-022`). Author
`database_pagination` only when the read needs a declared page order.

## `primary_keys`

A **fallback** identity hint (`RULE-STRM-030`).

For API endpoints this is the only source identity hint there is: an API endpoint
document has no primary-key metadata to inherit, so record identity for an upsert
destination has no other source.
