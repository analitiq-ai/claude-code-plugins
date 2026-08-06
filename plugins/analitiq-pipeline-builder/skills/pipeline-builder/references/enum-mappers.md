# Closed-enum mappers

The orchestrator runs these mappers inline at phase 3. Every mapper is
**fail-closed**: if the user input doesn't match an entry, halt and ask.

The target vocabularies are contract-owned. The mapping from a user's phrasing
onto them is not, and is what this file adds.

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

## ScheduleTypeMapper

| User input contains | → | `schedule.type` |
|---|---|---|
| "manual", "on-demand", "trigger only", "run when I say" | → | `manual` |
| "every N minutes", "every hour", "every day", "interval" | → | `interval` |
| "cron expression", "at 02:00 UTC", "weekdays at 9am" (anything that needs a cron spec) | → | `cron` |

After selecting `type`, the contract gates which sibling fields may appear
(`ADV-PIPE-002`); `pipeline-spec/spec-schedule.md` carries the generated shape.
The scheduler validates a cron expression's inner spec at runtime — the contract
check is deliberately coarse.

## ReplicationMethodMapper

| User input contains | → | `replication.method` |
|---|---|---|
| "full refresh", "full reload", "reload everything", "snapshot" | → | `full_refresh` |
| "incremental", "delta", "changes since", "watermark" | → | `incremental` |

For `incremental`, the user must name the `cursor_field`. If they
can't, halt and ask for the column/parameter name they want to use as
the watermark.

For an API endpoint, `replication.method` must be in the source endpoint's
declared support set — the connector document lists
`operations.read.replication.supported_methods`. A database endpoint carries
no `replication` block, so no endpoint-declared support set constrains it;
the stream-side rule above still does, and `incremental` still requires a
`cursor_field`.

## WriteModeMapper

| Destination kind | User input contains | → | `write.mode` |
|---|---|---|---|
| api | one of the endpoint's `operations.write` keys | → | that key (verbatim) |
| database | "insert", "append", "load" | → | `insert` |
| database | "upsert", "merge", "on-conflict update" | → | `upsert` (requires `conflict_keys`) |
| database | "overwrite", "replace", "full refresh", "truncate and load", "re-sync" | → | `truncate_insert` (forbids `conflict_keys`) |

For database `upsert`, ask the user (or infer from the destination
endpoint's `primary_keys`) which fields form the conflict resolution
key set. `conflict_keys` is a flat, non-empty list of field names
(`[<field>, …]`).

`truncate_insert` **empties the destination** and reloads it — the full-refresh
mode every comparable tool carries (Airbyte `overwrite`, Singer `FULL_TABLE`,
Fivetran re-sync). Route to it only when the user describes *replacing* the
destination's contents rather than adding to them.

It is destructive, so never infer it from the source's shape alone. A source
with no cursor and no stable key is a signal, not a verdict: an append-only
event or log stream fits that description exactly, and `insert` is correct there
— `truncate_insert` would wipe the accumulated history on every run. Decide by
what the destination is *for*: a mirror of current state (route here) or an
accumulating record (do not). When the user's intent is not explicit, **ask
before routing to a mode that empties a table.**

Delivery is at-least-once by design; say so if the user is weighing modes.

## AuthTypeMapper (informational)

The orchestrator does **not** author the connector's `auth` block — that's the
connector-builder plugin's job. `connection-creator` routes each connection value
into `parameters` / `secret_refs` / `selections` by the connector contract's
`storage` field, so it needs **no** per-auth-type template. The connector's
`auth.type` is only a hint for which `examples/*.example.json` is the closest
shape illustration (`api_key`, `basic_auth`, `oauth2_authorization_code`,
`oauth2_client_credentials`, `jwt`, `db`, `credentials`, `aws_iam` each map to the
same-named example; `none` → `examples/none.example.json`, parameters only).

`connection-creator` loads `connection-spec` and routes by `storage` regardless
of auth type, so a new auth type needs no plugin change.
