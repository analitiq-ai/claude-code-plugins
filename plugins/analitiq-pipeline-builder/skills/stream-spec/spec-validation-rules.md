# Assignment-level validation

Each `mapping.assignments[]` entry may carry an optional `validate` block:

<!-- BEGIN GENERATED: fields-validation -->
`analitiq.contracts.stream.Validation` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `rules` | no | array of ValidationRule | — | — |
| `error_handling` | no | StreamValidationErrorHandling \| null | `None` | — |
<!-- END GENERATED: fields-validation -->

Sections below cover each field. A worked assignment:

<!-- validate: stream#/mapping/assignments/0 -->
```jsonc
{
  "target": {"path": "email", "arrow_type": "Utf8", "nullable": false},
  "value": {"kind": "expression", "expression": {"op": "get", "path": ["email"]}},
  "validate": {
    "rules": [
      {"type": "required", "field": ["email"]},
      {"type": "pattern", "field": ["email"], "value": "^[^@]+@[^@]+$", "message": "Invalid email format."}
    ]
  }
}
```

This is **stream record validation**: it grades records the pipeline moved. Do
not confuse it with connection **input** validation, which lives on the
connector's `connection_contract` and grades configuration a user typed — it is
connection/connector-owned. Never carry a rule from one into the other, and
never expect this block to validate connection inputs.

Validation runs on assignment **output**: the rules see the value the assignment
produced, after any `pipe`/`fn` conversion, and before the destination write. So
a rule addresses the mapped output path, never the source field name it was read
from (`RULE-STRM-015`) — where the two differ, naming the source is the mistake
to watch for.

## `rules[]`

<!-- BEGIN GENERATED: fields-validation-rule -->
`analitiq.contracts.stream.ValidationRule` — closed (`additionalProperties: false`); required: `field`, `type`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `type` | **yes** | 'required' \| 'not_null' \| 'min_length' \| 'max_length' \| 'pattern' \| 'range' \| 'in_list' | — | — |
| `field` | **yes** | array of string | — | `minItems=1`, `item pattern=\S`, `item minLength=1` |
| `value` | no | any | `None` | — |
| `message` | no | string \| null | `None` | — |

Carries 1 declarative cross-field `if`/`then` rule(s) — see the registered rules for their prose.
<!-- END GENERATED: fields-validation-rule -->

### `rules[].type`

`RULE-STRM-009` settles which members take a `value` and which must omit it, and
`RULE-STRM-021` the shape that `value` takes. What neither those nor the table
state is what each member *means*:

- `required` — the field must be present.
- `not_null` — the field must be present and non-null.
- `min_length` / `max_length` — string length bound (integer `value`).
- `pattern` — the value matches the regex in `value`.
- `range` — the numeric value falls inside the `{min, max}` in `value`.
- `in_list` — the value is one of the array in `value`.

### `rules[].field`

`RULE-STRM-015` settles how a `field` resolves: token array, first token a target
declared anywhere in the same mapping, later tokens the nesting under it. Reach
into an `Object` target with a further token (`["address", "city"]`), never a
dotted string — the destination declares nesting with `arrow_type` + `properties`.
One token is one field name, so a `.` inside a token is part of that name, as in
a source `get` path.

## `error_handling`

`StreamValidationErrorHandling` is a mirror of `pipeline.runtime.error_handling`
(`analitiq.contracts.pipelines.config.ErrorHandling`) — the same strategy
vocabulary, the same retry fields, the same gating (`RULE-RETRY-001`).

The contract accepts this block, and it is never the way to change how a
validation failure is answered (`RULE-STRM-040`). So do not author one, and
never offer it as a per-assignment error policy. When a user wants failing
records handled differently — how often one is retried, where one that keeps
failing lands — take them to the pipeline's `runtime.error_handling`
(`../pipeline-spec/spec-engine-runtime.md`) and tell them the policy they choose
governs every assignment the pipeline runs.

## When to use

Use sparingly. Validation rules are useful for **defensive** checks
against malformed source data when the destination is strict (e.g.,
a column has a NOT NULL constraint that the source might violate).
For routine type coercion, rely on the registry's type-map machinery
rather than authoring `validate` blocks.
