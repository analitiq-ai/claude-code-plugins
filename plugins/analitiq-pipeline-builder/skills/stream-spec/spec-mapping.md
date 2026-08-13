# `mapping` block

Omit `mapping` (or pass `null`) for the default pass-through mapping: every
source field is copied 1:1 to a destination field of the same name, typed by the
registry. Worked example: `examples/default-passthrough-mapping.example.json`.

When you do author it, author `assignments` and nothing else — every other
mapping field is server-computed (see
`../pipeline-builder/references/reserved-fields.md`).

`mapping` is also the *only* stream-owned place where field assignment and type
coercion are declared. If a transformation is expressible here, it belongs here;
if it is not, it belongs to the connector or the destination endpoint, never to a
side channel invented on the stream.

<!-- BEGIN GENERATED: fields-stream-mapping -->
`analitiq.contracts.stream.StreamMapping` — closed (`additionalProperties: false`); required: none

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `assignments` | no | array of Assignment | — | — |
<!-- END GENERATED: fields-stream-mapping -->

Each `assignments[]` entry:

<!-- BEGIN GENERATED: fields-assignment -->
`analitiq.contracts.stream.Assignment` — closed (`additionalProperties: false`); required: `target`, `value`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `target` | **yes** | AssignmentTarget | — | — |
| `value` | **yes** | ExpressionAssignmentValue \| ConstantAssignmentValue (by `kind`) | — | — |
| `validate` | no | Validation \| null | `None` | — |
<!-- END GENERATED: fields-assignment -->

`spec-validation-rules.md` covers the `validate` block.

<!-- validate: stream#/mapping -->
```jsonc
{
  "mapping": {
    "assignments": [
      {
        "target": {
          "path": "id",
          "arrow_type": "Utf8",
          "native_type": "uuid",
          "nullable": false
        },
        "value": {
          "kind": "expression",
          "expression": {"op": "get", "path": ["id"]}
        }
      },
      {
        "target": {"path": "tenant_id", "arrow_type": "Utf8", "nullable": false},
        "value": {
          "kind": "constant",
          "constant": {"arrow_type": "Utf8", "value": "acme-corp"}
        }
      }
    ]
  }
}
```

## `assignments` order is significant (`RULE-STRM-019`)

Preserve the order a caller gave you and never re-sort the array for tidiness —
in edit mode a reordering is a diff the user did not ask for.

## `assignments[].value`

`value` is a **discriminated union**: `kind` names the variant, and the variant
admits exactly one payload key. Always author `kind`; the payload key alone does
not identify the variant.

### `kind: "expression"` — read from the source

<!-- BEGIN GENERATED: fields-assignment-value-expression -->
`analitiq.contracts.stream.ExpressionAssignmentValue` — closed (`additionalProperties: false`); required: `expression`, `kind`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `kind` | **yes** | const 'expression' | — | — |
| `expression` | **yes** | GetExpression \| PipeExpression | — | — |
<!-- END GENERATED: fields-assignment-value-expression -->

`expression` is one of:

- `{"op": "get", "path": ["<segment>", …]}` — read a source field. The default;
  it covers almost every mapping.
- `{"op": "pipe", "args": [{"op": "get", "path": [...]}, {"op": "fn", "name": "<conversion>"}, …]}` —
  a `get` seed passed through one or more `fn` conversion stages
  (`RULE-STRM-005`). An `fn` node is valid **only** inside `pipe.args`, never
  standalone. Author `pipe` only when a conversion is genuinely required;
  otherwise prefer `get`.

#### `get.path` is a token array, never a dotted string

One entry per path segment, outermost first: `["address", "city"]` reads
`city` nested under `address`. A single top-level field is a one-element array,
`["id"]` — not `"id"`.

There is no escaping to learn, because there is nothing to escape: a source
field whose name literally contains a dot is `["a.b"]`, which is a different
path from the nested `["a", "b"]`.

### `kind: "constant"` — assign a typed literal

<!-- BEGIN GENERATED: fields-assignment-value-constant -->
`analitiq.contracts.stream.ConstantAssignmentValue` — closed (`additionalProperties: false`); required: `constant`, `kind`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `kind` | **yes** | const 'constant' | — | — |
| `constant` | **yes** | ConstantValue | — | — |
<!-- END GENERATED: fields-assignment-value-constant -->

The `constant` payload:

<!-- BEGIN GENERATED: fields-constant-value -->
`analitiq.contracts.stream.ConstantValue` — closed (`additionalProperties: false`); required: `arrow_type`, `value`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `arrow_type` | **yes** | string | — | `pattern=(long; see `endpoint-spec/spec-columns.md`)` |
| `value` | **yes** | any | — | — |
| `properties` | no | map of ArrowFieldSpec \| null | `None` | — |
| `items` | no | ArrowFieldSpec \| null | `None` | — |

Carries 7 declarative cross-field `if`/`then` rule(s) — see the registered rules for their prose.
<!-- END GENERATED: fields-constant-value -->

`properties` and `items` read as optional in that table, but `RULE-STRM-007`
ties both to `arrow_type`: a constant whose declared type calls for an inner
shape must declare it, and one whose type does not must omit it. Do not assume
a type plus a payload is always the whole constant.

### When a bare `get` is not enough (`RULE-STRM-020`)

The engine's conversion matrix classifies each `(source type, target type)` pair
as implicit or explicit — `Int64 → Utf8` is an explicit one. So when source and
target Arrow types differ, check the pair before reaching for `get`:
if it is an explicit conversion, the assignment must be
`{"op": "pipe", "args": [{"op": "get", "path": [...]}, {"op": "fn", "name": …}]}`. The
conversion function names are closed (`analitiq.contracts.stream.FnExpression`).
An `fn` node names the conversion and nothing more: never carry over the
engine's `version` or `args` node fields, which this contract does not publish.

## `assignments[].target`

<!-- BEGIN GENERATED: fields-assignment-target -->
`analitiq.contracts.stream.AssignmentTarget` — closed (`additionalProperties: false`); required: `arrow_type`, `path`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `path` | **yes** | string | — | `pattern=^[^.]*[^.\s][^.]*$`, `minLength=1` |
| `arrow_type` | **yes** | string | — | `pattern=(long; see `endpoint-spec/spec-columns.md`)` |
| `native_type` | no | string \| null | `None` | — |
| `nullable` | no | boolean | `True` | — |
| `properties` | no | map of ArrowFieldSpec \| null | `None` | — |
| `items` | no | ArrowFieldSpec \| null | `None` | — |

Carries 3 declarative cross-field `if`/`then` rule(s) — see the registered rules for their prose.
<!-- END GENERATED: fields-assignment-target -->

### `target.path`

Must be unique within `assignments` (`RULE-STRM-002`).

`target.path` addresses the **assignment root only** — the destination field this
assignment writes — so it is a single segment; the pattern in the table above is
what enforces it. Inner structure is declared recursively by `properties`
(for an `Object` target) and `items` (for a `List` target), governed by
`RULE-STRM-010`. Child field specs are **not** separately addressable from
`assignments`: you cannot write a second assignment at `address.city` to reach
inside an `address` Object target. One assignment owns one root and declares
everything beneath it.

Note the asymmetry with the source side, and that it is deliberate: `get.path`
is a token array because a source read may descend into a nested record;
`target.path` is one segment because nesting on the destination is already
expressed by `arrow_type` + `properties`/`items`.

<!-- PROBE: stream-mapping-target-unresolved-locally -->
Cross-document (`RULE-STRM-022`): endpoint resolution is server-side at save
time; the local validator does **not** check this.

## `arrow_type` vocabulary

Every `arrow_type` — target or constant — must be **fully-qualified**. The
vocabulary is owned by `analitiq.contracts.endpoints.ARROW_TYPE_PATTERN` — the
same pattern the endpoint columns use, generated from the engine-published
grammar manifest — so a bare parameterized name is rejected. See
[`endpoint-spec/spec-columns.md`](../endpoint-spec/spec-columns.md) for the
canonical walkthrough: the shapes (bare / `( )`), the authored-shape
container markers, unit identifiers and timezone forms apply identically here.

Container shape is not free-form either: `RULE-STRM-006`, `RULE-STRM-007` and
`RULE-STRM-010` tie `arrow_type` to whether the field declares `properties`,
`items`, or neither, and tie a constant's JSON kind to its declared type.

The destination endpoint's `columns[]` decides `target.arrow_type`
(`RULE-STRM-028`) — a `Decimal128(12, 2)` column gives a `Decimal128(12, 2)`
target, precision and scale included.
