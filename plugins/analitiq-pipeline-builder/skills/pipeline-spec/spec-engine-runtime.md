# `engine` and `runtime` blocks

Author `engine` and `runtime` only when the user asked for a value that differs
from the contract's (`RULE-SHRD-004`).

## `engine`

<!-- BEGIN GENERATED: fields-engine -->
`analitiq.contracts.pipelines.config.Engine` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `vcpu` | no | number | `1.0` | `min=0.5` |
| `memory` | no | integer | `8192` | `min=1024` |
<!-- END GENERATED: fields-engine -->

A deployment may impose its own ceilings, and stricter minimums than the
contract's, so a document that validates here can still be refused where it is
submitted. Treat an unusually large request as a question for the user.

`engine` sizes the run as a whole: never author `vcpu` / `memory` as a split
between containers, and never explain them to the user in those terms — how the
runtime divides them is not something this document decides.

## `runtime`

<!-- BEGIN GENERATED: fields-runtime -->
`analitiq.contracts.pipelines.config.Runtime` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `buffer_size` | no | integer | `5000` | `min=100` |
| `logging` | no | Logging | — | — |
| `batching` | no | Batching | — | — |
| `error_handling` | no | ErrorHandling | — | — |
<!-- END GENERATED: fields-runtime -->

### `batching`

<!-- BEGIN GENERATED: fields-batching -->
`analitiq.contracts.pipelines.config.Batching` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `batch_size` | no | integer | `100` | `min=1`, `max=100000` |
<!-- END GENERATED: fields-batching -->

### `logging`

<!-- BEGIN GENERATED: fields-logging -->
`analitiq.contracts.pipelines.config.Logging` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `log_level` | no | 'DEBUG' \| 'INFO' \| 'WARNING' \| 'ERROR' \| 'CRITICAL' | `'INFO'` | — |
| `metrics_enabled` | no | boolean | `True` | — |
<!-- END GENERATED: fields-logging -->

### `error_handling`

<!-- BEGIN GENERATED: fields-error-handling -->
`analitiq.contracts.pipelines.config.ErrorHandling` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `strategy` | no | 'fail' \| 'dlq' \| 'skip' | `'dlq'` | — |
| `max_retries` | no | integer | `3` | `min=0`, `max=5` |
| `retry_delay_seconds` | no | integer \| null | `None` | `min=0` |

Carries 1 declarative cross-field `if`/`then` rule(s) — see the registered rules for their prose.
<!-- END GENERATED: fields-error-handling -->

`max_retries` and `retry_delay_seconds` are coupled — `RULE-RETRY-001`.

What happens to a dead-lettered record — where it is stored, how the run's
outcome is classified — is runtime-owned, so never promise the user a DLQ
location the document cannot express.

## Where batching is decided

`runtime.batching.batch_size` is the chunk a run reads in, and it is one value
for the whole pipeline — every stream reads in that size. So when a user asks
for a specific chunk size, author it here, on the pipeline.

Two other documents also carry a batching value, and neither displaces this one:

- **stream `destinations[].execution`** — accepted by the contract, and not the
  place to author a write size (`RULE-PIPE-007`; see
  `../stream-spec/spec-destinations.md` § `execution`).
- **the destination endpoint's declared capacity** — an API endpoint's write
  `batching`, owned by the connector and never authored here.

Neither is a lever on the size this pipeline declares (`RULE-PIPE-007`), so
never promise a user a throughput change from authoring one.

Batch **concurrency** is not authorable. Do not offer it when a user asks how to
tune throughput — `batch_size` is the lever that exists.
