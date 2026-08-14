---
name: stream-spec
description: Stream authoring vocabulary — endpoint refs, source filters/replication/pagination, destinations write modes, mapping assignments, validation rules. Loaded by stream-creator only. Not invoked directly by users.
user-invocable: false
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

## Contents

- Required reading (load on demand)
- What this skill covers
- Top-level shape
- Closed vocabularies
- What this skill does NOT cover
- Registered rules for a stream
- Output rules

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

Every rule binding a stream document is in
`../pipeline-builder/references/rules/stream.md` — **read it before
authoring**, satisfy every row, and cite one by id rather than restating it.
<!-- PROBE: stream-filter-field-unresolved-locally -->
A clean validation run is not proof they all hold — some are applied only at
connect or run time.


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
