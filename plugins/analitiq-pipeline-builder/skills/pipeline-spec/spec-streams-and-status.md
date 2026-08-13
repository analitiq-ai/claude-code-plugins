# `streams` and `status`

Both fields are declared on `PipelineInput` — see the field table in `SKILL.md`
for their types, defaults and constraints.

## `streams`

Each entry is the `stream_id` of a sibling stream document — plugin convention,
see `SKILL.md` § Output rules.

<!-- validate: pipeline#/streams -->
```jsonc
{
  "streams": [
    "22222222-2222-4222-8222-222222222222",
    "23232323-2323-4323-8323-232323232323"
  ]
}
```

Rules:

- Duplicate stream references: `RULE-PIPE-003`.
- A referenced stream must name this pipeline as its parent (`RULE-STRM-032`);
  `--bundle-root` is how the validator sees it.
- Array order is **display-only** (`RULE-PIPE-010`).
- A pipeline that is scheduled must reference a stream (`RULE-PIPE-004`).

## `status`

`status` is the only gate on execution — there is no parallel enabled/disabled
flag. The vocabulary and default are in the `SKILL.md` field table; what each
value means operationally:

| value | semantics |
|---|---|
| `draft` | Editable. Not scheduled. `streams` may be empty. |
| `active` | Scheduled (subject to `schedule.type`), and gated by `RULE-PIPE-004` and `RULE-PIPE-014`. |
| `inactive` | Paused. Not scheduled. `streams` may be empty. |

<!-- PROBE: pipeline-active-empty-streams-rejected, pipeline-draft-runnability-unchecked -->
An `active` pipeline with an empty `streams` list is rejected from the pipeline
document alone; that at least one referenced stream is runnable needs
`--bundle-root`. A **draft** pipeline is legitimately not yet runnable, so
runnability is not checked for a draft (`require_runnable=False`); it is
enforced only once the pipeline is `active`.

## Authoring sequence

The orchestrator authors the pipeline shell with `streams: []` in phase
6, then stitches the stream UUIDs back in phase 8 after the parallel
`stream-creator` dispatch returns each new `stream_id`. Neither the shell nor a
stream authors `status`: both start in the contract's default — the value in
the `SKILL.md` field table — and a default is never copied into a document
(`RULE-SHRD-004`). Promotion to `active` happens later (typically when the user
submits the pipeline to the registry).
