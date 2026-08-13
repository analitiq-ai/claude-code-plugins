---
name: pipeline-spec
description: Pipeline authoring vocabulary — connection refs, schedule, engine, runtime, streams, status. Loaded by pipeline-creator only. Not invoked directly by users.
disable-model-invocation: true
---

# pipeline-spec

This skill is loaded by `pipeline-creator` when authoring a pipeline document.

## Required reading (load on demand)

- `spec-connections.md` — UUID refs for source + destinations.
- `spec-schedule.md` — schedule types and `timezone`.
- `spec-engine-runtime.md` — vcpu/memory floor, batching, logging, error_handling.
- `spec-streams-and-status.md` — stream pinning rules and lifecycle gating.
- At least one of `examples/*.example.json` for the schedule style you're authoring.

## `$schema`

<!-- BEGIN GENERATED: schema-urls -->
| Entity | Authored file | `$schema` value |
|---|---|---|
| Pipeline | `pipelines/<slug>/pipeline.json` | `https://schemas.analitiq.ai/pipeline/latest.json` |
| Stream | `pipelines/<slug>/streams/<stream-slug>.json` | `https://schemas.analitiq.ai/stream/latest.json` |
| Connection | `connections/<slug>/connection.json` | `https://schemas.analitiq.ai/connection/latest.json` |
| Database endpoint | `connections/<slug>/definition/endpoints/<endpoint_id>.json` | `https://schemas.analitiq.ai/database-endpoint/latest.json` |
<!-- END GENERATED: schema-urls -->

## What this skill covers

A pipeline document owns exactly the fields the table below declares and
nothing else. Everything else a pipeline needs is **referenced**, never
inlined.

<!-- BEGIN GENERATED: fields-pipeline -->
`analitiq.contracts.pipelines.config.PipelineInput` — closed (`additionalProperties: false`); required: `connections`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `$schema` | no | const 'https://schemas.analitiq.ai/pipeline/latest.json' \| null | `None` | — |
| `display_name` | no | string \| null | `None` | `pattern=^\S(?:[\s\S]*\S)?$`, `minLength=1`, `maxLength=120` |
| `description` | no | string \| null | `None` | `maxLength=2000` |
| `status` | no | 'draft' \| 'active' \| 'inactive' | `'draft'` | — |
| `tags` | no | array of string \| null | `None` | `maxItems=50`, `item pattern=^\S(?:[\s\S]*\S)?$`, `item minLength=1` |
| `connections` | **yes** | PipelineConnections | — | — |
| `streams` | no | array of string | — | `uniqueItems=True`, `item pattern=\S`, `item minLength=1` |
| `schedule` | no | Schedule | — | — |
| `engine` | no | Engine | — | — |
| `runtime` | no | Runtime | — | — |
| `pipeline_id` | no | string \| null | `None` | `pattern=^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` |

Carries 1 declarative cross-field `if`/`then` rule(s) — see the registered rules for their prose.
<!-- END GENERATED: fields-pipeline -->

Author a value only where the user asked for one: a default the contract already
declares is never copied into the document (`RULE-SHRD-004`). The fields this
plugin authors regardless are in §"Output rules".

## What this skill does NOT cover

- Stream bodies — see `stream-spec`.
- Connection bodies — see `connection-spec`.
- Database endpoint bodies — see `endpoint-spec`.
- Connector bodies — that's the `analitiq-connector-builder` plugin.

A pipeline document is also **not an import bundle**: never nest another
entity's body inside `pipeline.json` to make it self-contained — author each
document as its own file and reference it by id.

## Registered rules for a pipeline

Satisfy every rule below, and cite one by id rather than restating it.
<!-- PROBE: pipeline-cron-inner-spec-unchecked -->
A clean validation run is not proof they all hold — some are applied only at
connect or run time.

<!-- BEGIN GENERATED: rules-pipeline -->
| Rule | Constraint |
|---|---|
| `RULE-PIPE-001` | A pipeline's destination list MUST NOT name the same connection more than once. |
| `RULE-PIPE-002` | A pipeline's schedule MUST author exactly the fields its chosen `type` calls for, and MUST omit the fields belonging to the types it did not choose. |
| `RULE-PIPE-003` | A pipeline MUST NOT list two streams that reduce to the same version-stripped base id. |
| `RULE-PIPE-004` | A pipeline in the status that schedules it MUST reference at least one stream. |
| `RULE-PIPE-005` | A pipeline's `schedule.type` MUST be a member of the vocabulary `Schedule.type` declares; the member chosen then gates which schedule fields are legal (RULE-PIPE-002). |
| `RULE-PIPE-006` | A pipeline MAY omit any schedule field `Schedule` declares a default for, and a document that omits one takes that default. |
| `RULE-PIPE-007` | A stream's per-destination `execution` block MUST NOT be authored as a way to change how much a run writes at a time; the batch size a run uses is the one the pipeline's runtime declares, for every stream. |
| `RULE-PIPE-008` | Every connection a pipeline references MUST belong to the same organization as the pipeline. |
| `RULE-PIPE-009` | A `cron_expression` MUST carry an inner spec the scheduler that runs it accepts; the contract constrains the wrapper alone. |
| `RULE-PIPE-010` | The order of a pipeline's `streams` MUST NOT encode a dependency between streams, and MUST NOT be presented to the user as one. |
| `RULE-PIPE-011` | Every stream a pipeline references MUST resolve to exactly one stream document in the run assembled from it, and the pipeline MUST NOT reference the same stream twice. |
| `RULE-PIPE-012` | Every connection a pipeline references MUST resolve to exactly one connection document in the run assembled from it. |
| `RULE-PIPE-013` | A pipeline MUST NOT reference two connections that reduce to the same version-stripped base id. |
| `RULE-PIPE-014` | A pipeline in the status that schedules it MUST reference at least one stream that is itself in a runnable status. |
| `RULE-PIPE-015` | A schedule's `timezone` MUST name a zone in the IANA database. |
| `RULE-PIPE-016` | A pipeline MUST name its lifecycle state from the vocabulary `PipelineAuthored` declares. |
| `RULE-PIPE-017` | A pipeline's logging block MUST name its level from the vocabulary `Logging` declares. |
<!-- END GENERATED: rules-pipeline -->

## Output rules

Every authored document must:

1. Declare `$schema` with the pipeline URL above (`RULE-SHRD-003`).
2. Include a non-empty `connections` object — see `spec-connections.md`.
   Author `pipeline_id` as a UUID the plugin generates — plugin convention,
   not a contract requirement. The directory name (`pipelines/<slug>/`) stays
   human-readable and is independent of that UUID.
3. Use **connection UUIDs** in `connections.source` and
   `connections.destinations[]`, and **stream UUIDs** in `streams[]` — set to
   the `connection_id` / `stream_id` of the corresponding documents in the
   `$schema` table above. That pairing is plugin convention; `RULE-PIPE-011`
   and `RULE-PIPE-012` are what the wiring must satisfy, and `--bundle-root`
   is how the validator sees it.
4. Pass validation (the `pipeline-schema-validator`, entity `pipeline`) with
   zero error findings.
