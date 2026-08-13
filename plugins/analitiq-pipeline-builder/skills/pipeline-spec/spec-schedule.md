# `schedule` block

Omit `schedule`, and any field within it, wherever the user named no value
(`RULE-PIPE-006`, `RULE-SHRD-004`).

<!-- BEGIN GENERATED: fields-schedule -->
`analitiq.contracts.pipelines.config.Schedule` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `type` | no | 'manual' \| 'interval' \| 'cron' | `'manual'` | — |
| `timezone` | no | string | `'UTC'` | — |
| `interval_minutes` | no | integer \| null | `None` | `min=1` |
| `cron_expression` | no | string \| null | `None` | `pattern=^cron\(.+\)$` |

Carries 3 declarative cross-field `if`/`then` rule(s) — see the registered rules for their prose.
<!-- END GENERATED: fields-schedule -->

Author only the fields the chosen type calls for (`RULE-PIPE-002`); leave every
field belonging to a type you did not choose out entirely rather than setting it
to `null`.

## `type: manual`

<!-- validate: pipeline#/schedule -->
```jsonc
{"type": "manual"}
```

Runs only on an explicit user trigger.

## `type: interval`

<!-- validate: pipeline#/schedule -->
```jsonc
{"type": "interval", "interval_minutes": 60}
```

Runs on a fixed cadence of `interval_minutes`. That cadence is
**timezone-invariant**: a fixed period is unaffected by the zone the pipeline
names or by a DST transition inside it. The shortest interval a source can
actually sustain is engine- and provider-dependent, so pick one the source can
serve, not the smallest the contract accepts.

## `type: cron`

<!-- validate: pipeline#/schedule -->
```jsonc
{"type": "cron", "timezone": "Europe/Berlin", "cron_expression": "cron(0 2 * * ? *)"}
```

Fires on an AWS EventBridge cron expression, interpreted in `timezone`
(`RULE-PIPE-009`).

## `timezone`

`RULE-PIPE-015`. Examples: `UTC`, `Europe/Berlin`, `America/New_York`.

Only `type: cron` interprets it; on the other types it is accepted and stored as
metadata, so never author a non-UTC value there expecting it to shift when a run
happens.

## Status interaction

`schedule` is **declarative**; the pipeline's `status` is what decides whether
the scheduler picks it up — see `spec-streams-and-status.md`.
