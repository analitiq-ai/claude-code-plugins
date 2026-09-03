# Request binding (`params` ↔ `request`)

How an endpoint's declared `params` reach its `request`. This is the part of
endpoint authoring most likely to fail validation, because the intuitive form —
dropping a per-run `ref` straight into `request.query` — is rejected
(RULE-ENDP-032).

Rule ids below (`RULE-ENDP-*`) are registry ids;
`connector-builder/references/rules/api-endpoint.md` carries each one's statement, the
document it grades and what a violation costs. Cite them rather than
re-deriving them.

## Contents

- The model: declare the input, then bind it
- Binding rules
- What must NOT go directly in a request slot
- What legitimately stays direct
- Params carry the *request-input* type
- The same value in two places is two params
- Writes: `from_input`
- Write path segments: `path_params` + `from_input`

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

- **`path_params` values are bindings, never free expressions** (RULE-ENDP-001).
  On a read, exactly `{"from_param": <name>}`, and the bound param must declare
  `in: "path"`. On a write, `{"from_input": "record.<dotted>"}` is also legal —
  the record itself supplies the segment; see "Write path segments" below.
- <!-- PROBE: param-key-keeps-provider-spelling -->
  **The placeholder name is the document's, not the provider's**
  (RULE-ENDP-060). A param may keep the provider's spelling; the `{name}` in
  `path`, and the `path_params` key binding it, are written in the contract's
  placeholder-name form, and `from_param` is what crosses between them:
  `"path": "/v3/objects/{object_id}"` with
  `"path_params": { "object_id": { "from_param": "objectId" } }`. Pasting a
  provider's `{objectId}` straight into the path is how this is usually failed.
  A path never repeats a placeholder (RULE-ENDP-059) and refuses a `${...}`
  template (RULE-ENDP-061).
- **A binding's location must match the site it appears in** (RULE-ENDP-008);
  the placement vocabulary is printed under RULE-ENDP-050 in
  `connector-builder/references/rules/api-endpoint.md`.
- **RULE-ENDP-009** — a declared-but-unbound param is an error, not dead
  weight: if you don't need it, delete it.
- **Every expression dict declares exactly one primary key** — one of `ref` /
  `template` / `literal` / `function` / `from_param` / `from_input`, alongside
  only `x-*` siblings (RULE-ENDP-022).
- **A GET read operation must not declare a body param** (RULE-ENDP-007) — a
  provider's body-bearing search route is a POST read, so change the method,
  not the param's placement.

## What must NOT go directly in a request slot

- <!-- PROBE: request-slot-direct-runtime-ref -->
  **No direct `stream.*`, `state.*`, or `runtime.*` ref** in `headers`,
  `query`, or `body` (RULE-ENDP-032). These are the per-run values (filters,
  cursors, batch sizing), and routing them through a param is what gives them a
  declared type, requiredness, and operator set. Without that, nothing
  downstream knows whether a stream may filter on the value or what it may
  filter with.

  <!-- PROBE: request-slot-template-smuggle -->
  The check catches `{"ref": …}` specifically; smuggling the same value in as
  `{"template": "${runtime.…}"}` slips past it. Don't — the reason to route
  through a param is the declared contract, not the validator.
- <!-- PROBE: read-leading-scope-typo -->
  **No unscoped ref or `${...}` placeholder** (RULE-ENDP-033) — the resolution
  scopes a leading token may name are in
  `connector-builder/references/value-expressions.md`.

## What legitimately stays direct

Values that are **fixed for the endpoint** need no param, because there is no
input to type or filter on: a fixed header is authored directly
(`"headers": { "Accept": "application/json" }` — every endpoint example under
`examples/*/endpoints/` carries it), and a fixed query value binds a literal
expression (`"query": { "api_version": { "literal": "2024-01" } }`).

Connection-scoped values resolved from the connector's connection contract are
also direct refs — they are not per-run inputs. Which scopes are barred from a
request slot, and which are not, is
`connector-builder/references/value-expressions.md` §Logical scopes
(RULE-ENDP-032).

## Params carry the *request-input* type

`params.<name>.type` is a JSON-style request-input type describing what is sent
**up** — the vocabulary is `RULE-ENDP-050`, which prints it beside the `in`
placement vocabulary in `connector-builder/references/rules/api-endpoint.md`. It is unrelated
to `native_type` / `arrow_type`, which describe what comes **back** in
`response.schema`. A timestamp sent as an ISO string is `type: "string"` even
though the response field it filters is `Timestamp(...)`.

More param rules worth knowing while authoring:

- **Filterability is declared by the read operation's `filters` map, not by the
  param.** A param is just a slot the request binds; what makes a *record field*
  filterable is a key for it in `operations.read.filters`, and what makes an
  operator available on that field is an entry naming where the comparison
  reaches the wire (`RULE-ENDP-065`). See `spec-filters.md`.
- **RULE-ENDP-003** — a container-typed `query` param has several legal
  spellings on the wire (repeated key, delimiter-joined, bracketed) and the
  provider accepts one, so the serialization has to be declared, not guessed.

## The same value in two places is two params

If a provider wants the same value in both a header and a query string, declare
two params with distinct names and bind each at its own site. One param cannot
satisfy two bindings (RULE-ENDP-009 counts bindings per param).

## Writes: `from_input`

`{"from_input": ...}` addresses the record being written. Author it inside
`operations.write.<mode>.request.body`, or as a write `path_params` binding
(below). It is never legal in `headers`, `query`, or anywhere on a read
(RULE-ENDP-034), nor in a param `default` — those sites exist before any record
is in scope.

| Value | Means |
|---|---|
| `record` | the whole destination record |
| `records` | the whole batch array |
| `record.<dotted>` | one field of the record |

- **Batching selects the arity** (RULE-ENDP-017) — the table above gives the
  spelling for each.
- **`records.<dotted>` is not supported** (RULE-ENDP-035).

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
source to fill it (RULE-ENDP-028).

The rules that bite:

- **`record.<dotted>` only** (RULE-ENDP-024). Membership in the mode's
  `input.schema` is checked through `$ref` / `allOf`, so a shared `$defs` shape
  works — but the `$defs` must live inside `input.schema`, since a ref out of it
  dangles and is refused by RULE-ENDP-026.
- **Mutually exclusive with `batching`** (RULE-ENDP-025). An update-by-id
  endpoint is per-record by nature; if the provider also offers a bulk route,
  that is a separate mode.
- **Never wrap the binding in a wire-encoding function** (RULE-ENDP-027).
  Encoding is engine-owned, so wrapping double-encodes (`a b` arrives as
  `a%2520b`) and the provider 404s or matches the wrong resource.
