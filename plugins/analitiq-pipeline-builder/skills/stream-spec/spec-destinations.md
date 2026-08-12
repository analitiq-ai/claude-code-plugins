# `destinations` block

`stream.destinations[]` entries take one of these shapes:

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
      }
    }
  ]
}
```

## Uniqueness and repeated connections

Destinations must be distinct by the endpoint they address (`RULE-STRM-001`).

Fanning one stream into two tables of the same warehouse is a normal shape: the
**same destination connection may appear in several entries** as long as the
endpoint differs.

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
included — pick the endpoint first, then author the write block its `mode`
selects, carrying only the fields that shape declares (`RULE-STRM-016`). Each
variant's `mode` vocabulary is in the tables above; an API destination's mode is
bounded further by `RULE-STRM-024`. The
orchestrator's `WriteModeMapper` (see
`../pipeline-builder/references/enum-mappers.md`) classifies the user's intent
to one of the database modes.

### `write.conflict_keys`

An API upsert's conflict target is endpoint-owned
(`operations.write.upsert.conflict_keys`), not stream-owned. This field is a
**single composite key set** of destination field names, not a list of
alternative key sets:

<!-- validate: stream#/destinations/0/write/conflict_keys -->
```jsonc
["id"]                       // or ["org_id", "external_id"] for a composite key
```

Every key field names a destination-endpoint field (`RULE-STRM-022`).

## `execution`

<!-- BEGIN GENERATED: fields-stream-execution -->
`analitiq.contracts.stream.Execution` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `batch_size` | no | integer \| null | `None` | `min=1`, `max=100000` |
<!-- END GENERATED: fields-stream-execution -->

The contract accepts this block, and it is never the way to change how much a
destination writes at a time (`RULE-PIPE-007`). So never offer it as a
per-destination tuning knob: when a user asks for a different write size, take
them to the pipeline's `runtime.batching`
(`../pipeline-spec/spec-engine-runtime.md` § "Where batching is decided") and
tell them the size they choose applies to the whole pipeline.

An API destination whose write operation declares no `batching` is a throughput
cliff: absent batching means one request per record, not an unbounded one.
Raise it with the user before authoring against that endpoint.
