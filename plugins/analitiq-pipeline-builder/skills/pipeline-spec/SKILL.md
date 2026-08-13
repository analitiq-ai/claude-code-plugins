---
name: pipeline-spec
description: Pipeline authoring vocabulary — connection refs, schedule, engine, runtime, streams, status. Loaded by pipeline-creator only. Not invoked directly by users.
user-invocable: false
---

# pipeline-spec

This skill is loaded by `pipeline-creator` when authoring a pipeline document.

## Contents

- Required reading (load on demand)
- `$schema`
- What this skill covers
- What this skill does NOT cover
- Registered rules for a pipeline
- Output rules

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

Every rule binding a pipeline document is in
`../pipeline-builder/references/rules/pipeline.md` — **read it before
authoring**, satisfy every row, and cite one by id rather than restating it.
<!-- PROBE: pipeline-cron-inner-spec-unchecked -->
A clean validation run is not proof they all hold — some are applied only at
connect or run time.


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
