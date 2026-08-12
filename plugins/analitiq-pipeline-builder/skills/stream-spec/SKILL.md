---
name: stream-spec
description: Stream authoring vocabulary — endpoint refs, source filters/replication/pagination, destinations write modes, mapping assignments, validation rules. Loaded by stream-creator only. Not invoked directly by users.
disable-model-invocation: true
---

# stream-spec

This skill is loaded by `stream-creator` when authoring a stream document. Copy
the `$schema` value from the row below — never retype it, and never invent a
version-pinned variant:

<!-- BEGIN GENERATED: schema-urls -->
| Entity | Authored file | `$schema` value |
|---|---|---|
| Pipeline | `pipelines/<slug>/pipeline.json` | `https://schemas.analitiq.ai/pipeline/latest.json` |
| Stream | `pipelines/<slug>/streams/<stream-slug>.json` | `https://schemas.analitiq.ai/stream/latest.json` |
| Connection | `connections/<slug>/connection.json` | `https://schemas.analitiq.ai/connection/latest.json` |
| Database endpoint | `connections/<slug>/definition/endpoints/<endpoint_id>.json` | `https://schemas.analitiq.ai/database-endpoint/latest.json` |
<!-- END GENERATED: schema-urls -->

## Required reading (load on demand)

- `spec-endpoint-refs.md` — scope=connector vs scope=connection rules.
- `spec-source.md` — selected_columns, filters, replication, database_pagination, primary_keys.
- `spec-destinations.md` — write modes, conflict_keys, execution overrides.
- `spec-mapping.md` — assignments shape; the constant and expression vocabulary.
- `spec-validation-rules.md` — assignment-level validation.
- `spec-filter-operators.md` — DB vs API operator vocabularies.
- At least one of `examples/*.example.json` for the source/destination kind you're authoring.

## What this skill covers

- The stream's top-level shape (below).
- The mapping expression and constant vocabulary — see `spec-mapping.md`.
- The closed source-filter operator vocabularies per endpoint kind.

## Top-level shape

<!-- BEGIN GENERATED: fields-stream -->
`analitiq.contracts.stream.StreamInput` — closed (`additionalProperties: false`); required: `destinations`, `pipeline_id`, `source`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `$schema` | no | const 'https://schemas.analitiq.ai/stream/latest.json' \| null | `None` | — |
| `display_name` | no | string \| null | `None` | `pattern=^\S(?:[\s\S]*\S)?$`, `minLength=1`, `maxLength=120` |
| `description` | no | string \| null | `None` | `maxLength=2000` |
| `pipeline_id` | **yes** | string | — | `pattern=\S`, `minLength=1` |
| `source` | **yes** | StreamSource | — | — |
| `destinations` | **yes** | array of DatabaseStreamDestination \| ApiStreamDestination | — | `minItems=1` |
| `mapping` | no | StreamMapping \| null | `None` | — |
| `status` | no | 'draft' \| 'active' \| 'inactive' | `'draft'` | — |
| `tags` | no | array of string \| null | `None` | `maxItems=50`, `item pattern=^\S(?:[\s\S]*\S)?$`, `item minLength=1` |
| `stream_id` | no | string \| null | `None` | `pattern=^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` |
<!-- END GENERATED: fields-stream -->

A field the table does not list is rejected, not ignored — including every
server-managed field (see
`../pipeline-builder/references/reserved-fields.md`).

## Closed vocabularies

Each value below is picked from a closed member list; anything outside it is a
validation error, not a pass-through value.

<!-- BEGIN GENERATED: enum-vocabulary -->
| Field | Members | Published as |
|---|---|---|
| `pipeline.status` / `stream.status` | `draft`, `active`, `inactive` | `analitiq.contracts.pipelines.config.PipelineInput.status` |
| `pipeline.schedule.type` | `manual`, `interval`, `cron` | `analitiq.contracts.pipelines.config.Schedule.type` |
| `pipeline.runtime.logging.log_level` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | `analitiq.contracts.pipelines.config.Logging.log_level` |
| `error_handling.strategy` | `fail`, `dlq`, `skip` | `analitiq.contracts.pipelines.config.ErrorHandling.strategy` |
| `stream…filters[].operator` | `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `is_null`, `is_not_null`, `like`, `ilike`, `contains`, `starts_with`, `ends_with` | `analitiq.contracts.stream.Filter.operator` |
| `stream…validate.rules[].type` | `required`, `not_null`, `min_length`, `max_length`, `pattern`, `range`, `in_list` | `analitiq.contracts.stream.ValidationRule.type` |
| `stream.source.replication.method` | `full_refresh`, `incremental` | discriminated union `analitiq.contracts.stream.Replication` |
| `stream.source.database_pagination.type` | `offset`, `keyset` | discriminated union `analitiq.contracts.stream.DatabasePagination` |
| `…endpoint_ref.scope` | `connector`, `connection` | discriminated union `analitiq.contracts.stream.EndpointRef` |
| `stream.destinations[].write.mode` (database) | `insert`, `truncate_insert`, `upsert` | discriminated union `analitiq.contracts.stream.DatabaseWrite` (an API destination's mode is bounded by the endpoint write-key universe instead) |
<!-- END GENERATED: enum-vocabulary -->

`status` is the only execution gate on a stream; take its value from the
vocabulary table above and from nowhere else (`RULE-STRM-035`).

## What this skill does NOT cover

- The full registry-side type vocabulary expansion — the authored mapping is
  `assignments`-only, and a key no field table names is rejected rather than
  passed through (`RULE-SHRD-014`;
  `../pipeline-builder/references/reserved-fields.md`).
- Endpoint bodies. The stream **references** endpoints by ref; it does
  not embed them.

## Registered rules for a stream

Satisfy every rule below, and cite one by id rather than restating it.
<!-- PROBE: stream-filter-field-unresolved-locally -->
A clean validation run is not proof they all hold — some are applied only at
connect or run time.

<!-- BEGIN GENERATED: rules-stream -->
| Rule | Constraint |
|---|---|
| `RULE-STRM-001` | Each entry in a stream's destinations MUST address an endpoint no other entry addresses. |
| `RULE-STRM-002` | Each assignment in a mapping MUST own a destination path no other assignment in that mapping writes. |
| `RULE-STRM-003` | A connection-scoped endpoint reference that supplies an endpoint_id MUST supply the handle the contract's derivation over its verbatim database object produces. |
| `RULE-STRM-004` | A filter MUST carry a value unless its operator takes no operand, and an operator that takes none MUST NOT carry one. |
| `RULE-STRM-005` | A pipe expression's arguments MUST begin with the source-read node that seeds it and continue only with conversion stages applied left to right. |
| `RULE-STRM-006` | An arrow field spec MUST declare the inner shape its arrow_type calls for and no other: a container marker carries its own inner declaration, and any other type carries none. |
| `RULE-STRM-007` | A constant MUST carry a value of the JSON kind its declared arrow_type admits, and MUST declare the inner shape that type calls for and no other. |
| `RULE-STRM-009` | A validation rule MUST carry a value when its type takes a parameter, and MUST omit one when its type takes none. |
| `RULE-STRM-010` | An assignment target MUST declare the inner shape its arrow_type calls for and no other: a container marker carries its own inner declaration, and any other type carries none. |
| `RULE-STRM-012` | A filter's operator MUST belong to the operator vocabulary of the scope its source endpoint reference declares. |
| `RULE-STRM-014` | A stream source bound to a connector-scoped endpoint MUST NOT declare any read feature the source model reserves for database sources. |
| `RULE-STRM-015` | A validation rule's field MUST resolve within its own mapping: the first token naming an assignment target the mapping declares, and each later token a field declared beneath the one before it. |
| `RULE-STRM-016` | A stream destination's write block MUST take the shape its mode selects and carry only the fields that shape declares. |
| `RULE-STRM-017` | A stream's replication block MUST take the shape its method selects and carry only the fields that shape declares. |
| `RULE-STRM-018` | A connection-scoped endpoint reference SHOULD carry the derived endpoint_id whenever the author can compute it, so the cross-document bundle check can resolve the reference. |
| `RULE-STRM-019` | A mapping's assignments MUST be kept in the order they were authored and MUST NOT be re-sorted, because the engine applies them in that order. |
| `RULE-STRM-020` | An assignment whose source and target types form a conversion the engine classifies as explicit MUST name that conversion function in a pipe stage rather than reading the field bare. |
| `RULE-STRM-021` | A validation rule's value MUST carry the payload shape its type requires. |
| `RULE-STRM-022` | Every field name a stream references MUST resolve to a field the endpoint document on that side of the transfer declares. |
| `RULE-STRM-023` | A stream MUST reproduce every source-endpoint field name exactly as the endpoint document records it, with no case-folding, trimming, quoting or other normalization. |
| `RULE-STRM-024` | An API destination's write mode MUST be one the referenced api-endpoint document declares a write operation for. |
| `RULE-STRM-025` | An API source's replication method MUST be one the referenced endpoint declares in its supported set. |
| `RULE-STRM-026` | A filter on an API source MUST name a read parameter the referenced endpoint declares filterable — one that publishes its own operator set and is not reserved to the runtime. |
| `RULE-STRM-027` | A filter's value MUST carry the type the referenced field declares, and a membership operator MUST carry an array of such values. |
| `RULE-STRM-028` | An assignment target's arrow_type MUST reproduce the destination column's declared type exactly, its parameters included. |
| `RULE-STRM-029` | A stream source MUST declare a replication policy unless the referenced source endpoint supports full refresh. |
| `RULE-STRM-030` | A stream MUST declare source primary keys when the transfer needs record identity and the source endpoint carries no primary-key metadata of its own, and MUST NOT declare keys that contradict the endpoint's. |
| `RULE-STRM-031` | A stream's reference to an API endpoint MUST use connector scope; connection scope refers to a database endpoint only. |
| `RULE-STRM-032` | A stream MUST name as its parent the pipeline that references it. |
| `RULE-STRM-033` | A stream's source connection MUST be the one its pipeline declares as the source, and each of its destination connections MUST be one the pipeline declares as a destination. |
| `RULE-STRM-034` | A connection-scoped endpoint reference MUST resolve to an endpoint document belonging to the connection it names. |
| `RULE-STRM-035` | A stream MUST name its lifecycle state from the vocabulary `StreamAuthored` declares. |
| `RULE-STRM-036` | A stream filter MUST name its operator from the vocabulary `Filter` declares. |
| `RULE-STRM-037` | A validation rule MUST name its kind from the vocabulary `ValidationRule` declares. |
| `RULE-STRM-038` | A stream destination's write block MUST name its mode from the write-mode vocabulary the block it selects declares. |
| `RULE-STRM-039` | An incremental stream's late-arrival safety window MUST be sized from how late the source's own records arrive, and MUST NOT be authored as a rewind the stream can count on: whether a window shifts a read at all is decided by the source, not by the stream document. |
| `RULE-STRM-040` | A mapping assignment's validation `error_handling` MUST NOT be authored as the policy for the failures its own rules raise; how a run answers a validation failure is what the pipeline's runtime error handling declares, for every assignment. |
<!-- END GENERATED: rules-stream -->

## Output rules

Every authored document must:

1. Declare `$schema` with the stream URL from the table at the top of this file
   (`RULE-SHRD-003`).
2. Carry every required field from the top-level shape table. Author `stream_id`
   even though the table marks it optional — see
   `../pipeline-builder/references/identity-and-versioning.md`. `pipeline_id`
   carries the parent pipeline's UUID.
3. Fill every `endpoint_ref.connection_id` from the pipeline's own connection
   references (`RULE-STRM-033`); `spec-endpoint-refs.md` §`connection_id` says
   which side takes which.
4. Shape each `endpoint_ref` by its `scope` — see `spec-endpoint-refs.md` for
   the per-scope field tables and `RULE-STRM-018` for the derived
   `endpoint_id`.
5. Pass validation (the `pipeline-schema-validator`, entity `stream`) with zero
   error findings.
