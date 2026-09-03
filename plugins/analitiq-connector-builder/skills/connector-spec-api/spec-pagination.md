# Pagination

Authoring patterns for `operations.read.pagination` in API endpoints.

The exact shape — property names, required keys, and the `stop_when`
predicate grammar — is owned by the published api-endpoint contract, not
by this page. Each strategy is a discriminated branch on `type`; the
sections below take one branch each.

## Contents

- Pagination is wired in three places
- `limit`: `max` is the provider's cap, `default` is ours
- `stop_when` is a predicate, not a keyword
- Pagination is not sync scoping
- `offset`
- `page`
- `cursor`
- `link`
- `keyset`
- Pick the right one

## Pagination is wired in three places

This is the part authors get wrong. A pagination block **does not create a
request binding on its own**. Every param it names must be:

1. **declared** in `params`, with
2. **`controlled_by: "pagination"`** on that param, and
3. **bound** into the request with `{"from_param": …}`.

Miss any one and validation fails (RULE-ENDP-009, RULE-ENDP-010). No entry in
`operations.read.filters` may land on a `controlled_by` param: pagination sets
it on every request, so a stream may not also filter through it
(RULE-ENDP-002).

See `examples/api-key/endpoints/v1__items.json` for the full three-place
wiring — every endpoint example under `examples/*/endpoints/` declares
`type: "page"` pagination with its `controlled_by` params declared and bound
this way.

## `limit`: `max` is the provider's cap, `default` is ours

- **`max`** is the largest page the provider permits. Read it from their docs;
  never guess it upward.
- **`default`** is the page size actually requested. Prefer
  `{"ref": "runtime.batch_size"}` (`connector-builder/references/value-expressions.md`
  §Logical scopes) over hardcoding a number, which overrides the operator's
  configured batch size. Hardcode only when the provider's usable page size is
  genuinely fixed, and then write it as a **bare positive integer**
  (`"default": 50`) —
  <!-- PROBE: pagination-limit-bare-zero-rejected -->
  that spelling is bounded by the contract, so a zero or negative size is
  rejected at authoring time rather than at the first request.

## `stop_when` is a predicate, not a keyword

`stop_when` is the condition that ends the page loop. It is **not** a string
like `"page_empty"`; it is a predicate object from the contract's predicate
grammar. Predicate keys (closed set): `eq`,
`neq`, `lt`, `lte`, `gt`, `gte`, `exists`, `missing`, `empty`, `not_empty`,
and the combinators `and`, `or`, `not` — exactly one key per predicate
object. The same grammar backs a write mode's `success_when`. Each leaf
wraps a single key over a value expression:

- `{ "empty": <expr> }` — stop when the expression resolves to nothing
  (an empty record array → no more pages).
- `{ "missing": <expr> }` — stop when the field is absent (no next
  cursor / no next link in the response).

> The predicate **wrappers** are contract-checked, and so is every
> `response.body` path an operand resolves (RULE-ENDP-023). What an operand
> *means* — that this `ref` is the page's record array, that one the next-page
> token — is yours: match it to the actual response shape of the endpoint
> you're authoring.

## Pagination is not sync scoping

Pagination walks *one* result set. It is not the mechanism for incremental
windows (that's `replication`) or for tenant/account scoping (that's an
ordinary param or a connection value). If you find yourself encoding a date
range or an account id in a pagination block, it belongs elsewhere.

## `offset`

Fixed-size pages addressed by an integer offset. Match `offset.increment_by` to
what the provider's `offset` counts (RULE-ENDP-058):

- **records returned** → `{ "ref": "response.record_count" }`
- **the requested window** → the page size actually sent:
  `{ "ref": "runtime.batch_size" }` when no smaller `limit.max` clamps it;
  with a cap, the clamped size as a bare integer — a raw batch size would
  overshoot and skip rows
- a bare positive integer is a fixed step (`1` for page-index-style
  offsets)

<!-- validate: api-endpoint#/operations/read/pagination -->
```json
{
  "type": "offset",
  "offset": { "param": "offset", "initial": 0, "increment_by": { "ref": "response.record_count" } },
  "limit": { "param": "limit", "default": { "ref": "runtime.batch_size" }, "max": 100 },
  "stop_when": { "empty": { "ref": "response.body.data" } }
}
```

## `page`

Pages addressed by a page number. Omit `page.increment_by` unless the provider
advances page numbers by more than one per request; `initial` is usually 1, but
some providers are 0-based — check.

## `cursor`

Server returns an opaque token in each response; the next request passes it
back. The cursor param is declared, marked and bound like every other paging
param (see "Pagination is wired in three places" above), and needs no
`default` — there is no token before the first response.

<!-- validate: api-endpoint#/operations/read/pagination -->
```json
{
  "type": "cursor",
  "cursor": { "param": "starting_after", "next_cursor": { "ref": "response.body.next_cursor" } },
  "limit": { "param": "limit", "default": { "ref": "runtime.batch_size" }, "max": 100 },
  "stop_when": { "missing": { "ref": "response.body.next_cursor" } }
}
```

## `link`

The next-page URL comes from the response. `link.next_url` resolves to that URL
and **replaces the entire request URL**, so it must resolve to a bare,
**absolute** URL — a relative one cannot be followed. Only the first request is
built from `path` + params.

`limit` binds only into that first request, and is wired like every other
strategy (see "Pagination is wired in three places" above). Providers usually
echo the page size back in each next link.

Prefer a body field that already holds the bare URL:

<!-- validate: api-endpoint#/operations/read/pagination -->
```json
{
  "type": "link",
  "link": { "next_url": { "ref": "response.body.links.next" } },
  "limit": { "param": "per_page", "default": { "ref": "runtime.batch_size" }, "max": 100 },
  "stop_when": { "missing": { "ref": "response.body.links.next" } }
}
```

A raw `Link:` header is **not** directly usable — its value is
`<https://…>; rel="next"`, angle brackets and rel-parameters included, not a
bare URL. <!-- PROBE: read-headers-tail-unchecked -->
Nothing validates this, so pointing `next_url` at
`response.headers.link` produces a request to a malformed URL at runtime. When
the provider only offers the header, confirm the response exposes a parsed form
before choosing `link`.

## `keyset`

Advance from the last record's ordering key (e.g. `since_id`). `keyset` names
the request `param` and the `order_by_field` — the dotted record path the
runtime reads the next key from. Requires the response records to be ordered by
that field.

**`order_by_field` is not a `cursor_field`.** It is pagination's ordering key
within one result set; replication's `cursor_field` is the incremental
watermark across runs. They are often the same field and still mean different
things — declaring one does not imply the other.

Omit `initial` entirely for the first page — never write `null`
(RULE-ENDP-044).

<!-- validate: api-endpoint#/operations/read/pagination -->
```json
{
  "type": "keyset",
  "keyset": { "param": "since_id", "order_by_field": "id" },
  "limit": { "param": "limit", "default": { "ref": "runtime.batch_size" }, "max": 100 },
  "stop_when": { "empty": { "ref": "response.body.data" } }
}
```

## Pick the right one

- Offset/page work for older REST APIs where total count or deterministic
  ordering is fine.
- Cursor and link are preferred when available — they're robust to
  insertions during a long sync.
- Keyset is the right choice when the server returns ordered records and
  exposes a stable ordering key.
- Some providers offer multiple pagination modes; use the one with the
  most stable semantics (cursor or link beat offset).
