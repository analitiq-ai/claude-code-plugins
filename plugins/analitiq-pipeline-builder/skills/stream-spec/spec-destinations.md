# `destinations` block

`stream.destinations[]` is a non-empty array of:

<!-- BEGIN GENERATED: fields-stream-destination -->
`analitiq.contracts.stream.DatabaseStreamDestination` — closed (`additionalProperties: false`); required: `endpoint_ref`, `write`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `execution` | no | Execution \| null | `None` | — |
| `endpoint_ref` | **yes** | ConnectionEndpointRef | — | — |
| `write` | **yes** | DatabaseKeylessWrite \| DatabaseConflictKeyedWrite (by `mode`) | — | — |

`analitiq.contracts.stream.ApiStreamDestination` — closed (`additionalProperties: false`); required: `endpoint_ref`, `write`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `execution` | no | Execution \| null | `None` | — |
| `endpoint_ref` | **yes** | ConnectorEndpointRef | — | — |
| `write` | **yes** | ApiWrite | — | — |
<!-- END GENERATED: fields-stream-destination -->

The sketch below illustrates a filled-in destination.

<!-- validate: stream#/destinations -->
```jsonc
{
  "destinations": [
    {
      "endpoint_ref": { /* see spec-endpoint-refs.md */ },
      "write": {
        "mode": "upsert",
        "conflict_keys": ["id"]
      },
      "execution": {
        "batch_size": 1000
      }
    }
  ]
}
```

## Uniqueness and repeated connections

Destinations must be distinct by their endpoint ref — `RULE-STRM-001` states the
tuple and the contract model enforces it. The emitted JSON Schema carries no
`uniqueItems` keyword for `destinations`, so a schema-only reading looks
permissive; it is not. Duplicates fail validation.

Because uniqueness is over the whole ref and not over `connection_id`, the **same
destination connection may legitimately appear in several destination entries**
as long as the endpoint differs — fanning one stream into two tables of the same
warehouse is a normal shape, not a duplicate.

## `write`

<!-- BEGIN GENERATED: fields-stream-write -->
`analitiq.contracts.stream.DatabaseKeylessWrite` — closed (`additionalProperties: false`); required: `mode`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `mode` | **yes** | 'insert' \| 'truncate_insert' | — | — |

`analitiq.contracts.stream.DatabaseConflictKeyedWrite` — closed (`additionalProperties: false`); required: `conflict_keys`, `mode`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `mode` | **yes** | const 'upsert' | — | — |
| `conflict_keys` | **yes** | array of string | — | `minItems=1`, `item pattern=\S`, `item minLength=1` |

`analitiq.contracts.stream.ApiWrite` — closed (`additionalProperties: false`); required: `mode`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `mode` | **yes** | 'insert' \| 'upsert' \| 'truncate_insert' | — | — |
<!-- END GENERATED: fields-stream-write -->

The destination's `endpoint_ref.scope` picks the whole shape, write block
included — pick the endpoint first, then author the write block its variant
declares. Each variant's `mode` vocabulary is in the tables above; an API
destination's mode is bounded further by `RULE-STRM-024`. The orchestrator's
`WriteModeMapper` (see
`../pipeline-builder/references/enum-mappers.md`) classifies the user's intent
to one of the database modes.

### `write.conflict_keys`

Only the conflict-keyed database variant declares this field — no other write
shape has it to set, and an API upsert's conflict target is endpoint-owned
(`operations.write.upsert.conflict_keys`). It is a **single composite key set**
of destination field names, not a list of alternative key sets:

<!-- validate: stream#/destinations/0/write/conflict_keys -->
```jsonc
["id"]                       // or ["org_id", "external_id"] for a composite key
```

Every key field names a destination-endpoint field (`RULE-STRM-022`); that is
resolved server-side at save time, not by the local validator.

## `execution` (per-destination override)

<!-- BEGIN GENERATED: fields-stream-execution -->
`analitiq.contracts.stream.Execution` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `batch_size` | no | integer \| null | `None` | `min=1`, `max=100000` |
<!-- END GENERATED: fields-stream-execution -->

`execution` is one of the places batching is decided, and each has a different
owner:

| Layer | Field | Owner | Meaning |
|---|---|---|---|
| Pipeline default | `pipeline.runtime.batching` | the pipeline | the baseline for every binding |
| Stream override | destination `execution` | this stream | overrides the default for *this* `(stream, destination)` binding only |
| Provider capacity | destination endpoint `operations.write.batching` | the endpoint | how much the provider will accept in one request |

The layers resolve under `RULE-PIPE-007`. The endpoint's `batching` is not a
default and not an override: it is a ceiling describing the provider.

Use `execution` sparingly — pipeline defaults exist for a reason. Typical use: a
low-throughput destination next to a high-throughput one in the same
`destinations[]`.

`batch_size` is the only override there is. `execution` also carried a
`max_concurrent_batches`, which nothing ever consumed; it was retired from the
contract instead of being left as a knob that read as load-bearing and did
nothing.

What batch size means per destination kind:

- **API destination** — the endpoint's write `batching.max_records` caps how many
  records may ride in one provider request.
- **Database destination** — `execution.batch_size` is the write chunk size once
  defaults have resolved.

If a write operation declares no `batching` at all, the runtime treats it as
single-record writes. That is a real throughput cliff on an API destination:
absent batching does not mean "unbounded", it means one record per request.
