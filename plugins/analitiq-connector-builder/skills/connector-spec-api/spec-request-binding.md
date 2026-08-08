# Request binding (`params` ↔ `request`)

How an endpoint's declared `params` reach its `request`. This is the part of
endpoint authoring most likely to fail validation, because the intuitive form —
dropping a `ref` straight into `request.query` — is rejected for anything
dynamic.

Rule ids below (`ADV-ENDP-*`) are the contract's cross-field rules; the full
list is `connector-builder/references/advisory-rules.md`. The validator enforces
them, so cite them rather than re-deriving them.

## The model: declare the input, then bind it

A dynamic request value is declared **once** as a param — the typed contract for
that input — and **referenced** from the request slot with a binding expression:

<!-- validate: api-endpoint#/operations/read -->
```json
{
  "params": {
    "account_id": { "in": "path", "type": "string", "required": true },
    "updated_since": { "in": "query", "type": "string", "format": "date-time",
                       "required": false, "controlled_by": "replication" }
  },
  "request": {
    "method": "GET",
    "path": "/v1/accounts/{account_id}/invoices",
    "path_params": { "account_id": { "from_param": "account_id" } },
    "query": { "updated_since": { "from_param": "updated_since" } }
  },
  "response": {
    "records": { "ref": "response.body.data" },
    "schema": { "type": "object", "properties": { "data": { "type": "array",
      "items": { "type": "object", "properties": { "id": { "type": "string" } } } } } }
  }
}
```

(The `response` block is the minimal completion a read operation requires —
its rules live in `endpoint-creator.md`; this page is about the binding.)

`{"from_param": "<name>"}` is the only way to route a declared param into a
request — a bare `{"ref": "..."}` is not an alternative spelling (see the
prohibitions below).

## Binding rules

- **`path_params` values are bindings, never free expressions**, and the block
  is present exactly when `request.path` declares placeholders (ADV-ENDP-001).
  On a read, exactly `{"from_param": <name>}`, and the bound param must declare
  `in: "path"`. On a write, `{"from_input": "record.<dotted>"}` is also legal —
  the record itself supplies the segment; see "Write path segments" below.
- **A binding's location must match the site it appears in** (ADV-ENDP-008):
  `request.headers` binds only `in: "header"` params, `request.query` only
  `in: "query"`, `request.body` only `in: "body"`.
- **Every declared param must be referenced by exactly one request binding**
  (ADV-ENDP-009). A declared-but-unbound param is an error, not dead weight —
  if you don't need it, delete it.
- **Every expression dict declares exactly one primary key** — one of `ref` /
  `template` / `literal` / `function` / `from_param` / `from_input`, alongside
  only `x-*` siblings (ADV-ENDP-022).
- **A GET read operation must not declare a body param** (ADV-ENDP-007).

## What must NOT go directly in a request slot

- <!-- PROBE: request-slot-direct-runtime-ref -->
  **No direct `stream.*`, `state.*`, or `runtime.*` ref** in `headers`,
  `query`, or `body` (ADV-ENDP-032). These are the per-run values (filters,
  cursors, batch sizing), and routing them through a param is what gives them a
  declared type, requiredness, and operator set. Without that, nothing
  downstream knows whether a stream may filter on the value or what it may
  filter with.

  <!-- PROBE: request-slot-template-smuggle -->
  The check catches `{"ref": …}` specifically; smuggling the same value in as
  `{"template": "${runtime.…}"}` slips past it. Don't — the reason to route
  through a param is the declared contract, not the validator.
- <!-- PROBE: read-leading-scope-typo -->
  **No unscoped ref or `${...}` placeholder** (ADV-ENDP-033) — the resolution
  scopes a leading token may name are in
  `connector-builder/references/value-expressions.md`.

## What legitimately stays direct

Values that are **fixed for the endpoint** need no param, because there is no
input to type or filter on: a fixed header is authored directly
(`"headers": { "Accept": "application/json" }` — every endpoint example under
`examples/*/endpoints/` carries it), and a fixed query value binds a literal
expression (`"query": { "api_version": { "literal": "2024-01" } }`).

Connection-scoped values resolved from the connector's connection contract
(`connection.parameters.*`, `secrets.*`, `auth.*`) are also direct refs — they
are not per-run inputs. Only the `stream`/`state`/`runtime` family is barred.

## Params carry the *request-input* type

`params.<name>.type` is a JSON-style request-input type (`string`, `integer`,
`number`, `boolean`, `array`, `object`) describing what is sent **up**. It is
unrelated to `native_type` / `arrow_type`, which describe what comes **back** in
`response.schema`. A timestamp sent as an ISO string is `type: "string"` even
though the response field it filters is `Timestamp(...)`.

Two more param rules worth knowing while authoring:

- **`operators` is the stream-filterability contract.** Declaring
  `operators: ["gte", "lte"]` is what permits a downstream stream to filter on
  that param, restricted to those operators. Omit it and the param is not
  stream-filterable at all.
- **A `controlled_by` param must not declare `operators`** (ADV-ENDP-002) —
  pagination and replication own those params, so a stream may not also filter
  on them.
- **A `query` param of type `array` or `object` must declare `style` and
  `explode`** (ADV-ENDP-003), because the wire serialization is otherwise
  ambiguous.

## The same value in two places is two params

If a provider wants the same value in both a header and a query string, declare
two params with distinct names and bind each at its own site. One param cannot
satisfy two bindings (ADV-ENDP-009 counts bindings per param).

## Writes: `from_input`

`{"from_input": ...}` addresses the record being written. Author it inside
`operations.write.<mode>.request.body`, or as a write `path_params` binding
(below). It is never legal in `headers`, `query`, or anywhere on a read
(ADV-ENDP-034), nor in a param `default` — those sites exist before any record
is in scope.

| Value | Means |
|---|---|
| `record` | the whole destination record |
| `records` | the whole batch array |
| `record.<dotted>` | one field of the record |

- **A write request body must reference `from_input`.** Batching selects the
  arity: a batched write must use `records`, a non-batched write must use
  `record` / `record.<field>` (ADV-ENDP-017).
- **`records.<dotted>` is not supported** (ADV-ENDP-035).

Provider envelopes are authored literally around the binding; no wrapper key is
special. This example is a **batched** write:

<!-- validate: api-endpoint#/operations/write/insert/request/body -->
```json
"body": { "data": { "from_input": "records" } }
```

## Write path segments: `path_params` + `from_input`

A per-record write whose URL names the record — `PUT /Contact/{id}`,
`DELETE /items/{sku}` — takes the segment from the record itself. Bind the
placeholder with `from_input` and declare **no param at all**:

<!-- validate: api-endpoint#/operations/write/upsert/request -->
```json
"request": {
  "method": "PUT",
  "path": "/Contact/{id}",
  "path_params": { "id": { "from_input": "record.id" } },
  "body": { "from_input": "record" }
}
```

Choosing between the two binding kinds: `from_input` when the segment
identifies the record being written (the update/delete-by-id shape);
`{from_param}` when it comes from configuration (an account id, a workspace
slug) — a write param must then carry a `default`, since a write has no other
source to fill it (ADV-ENDP-028).

The rules that bite:

- **`record.<dotted>` only** (ADV-ENDP-024). A path segment carries exactly one
  value, so the whole `record` and any `records[...]` form are refused, and the
  field it names must exist in the mode's `input.schema` (membership is checked
  through `$ref` / `allOf`, so a shared `$defs` shape works — but the `$defs`
  must live inside `input.schema`, since a ref out of it dangles and is refused
  by ADV-ENDP-026). The binding is write-only: on a read, `path_params` takes
  `{from_param}` and nothing else.
- **Mutually exclusive with `batching`** (ADV-ENDP-025). A multi-record request
  has no single record to take the segment from. An update-by-id endpoint is
  per-record by nature; if the provider also offers a bulk route, that is a
  separate mode.
- **Never wrap the binding in `url_encode` / `base64_encode`** (ADV-ENDP-027).
  Encoding is engine-owned: the engine percent-encodes each substituted value as
  one path segment, so wrapping double-encodes (`a b` arrives as `a%2520b`) and
  the provider 404s or matches the wrong resource.
