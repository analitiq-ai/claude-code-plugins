# Replication (incremental sync)

Authoring `operations.read.replication` for endpoints that support
incremental sync.

The exact shape — property names, required keys, and the closed enums — is
owned by the published api-endpoint contract, not by this page. Author
against it and let the validator check you:

- `#/$defs/Replication` — the block itself: `supported_methods` and
  `cursor_mappings`, each required, and nothing else.
- `#/$defs/SingleCursorMapping` — a cursor filtered by one provider param.
- `#/$defs/WindowCursorMapping` — a cursor filtered by a start/end param pair.

(all in `https://schemas.analitiq.ai/api-endpoint/latest.json`). This page
covers only the authoring decisions the schema can't express: which
mapping variant fits a provider, and when to skip replication entirely.

## Contents

- What a replication block declares
- Single-param cursor (most providers)
- Bounded-window cursor
- Wiring (same three places as pagination)
- More than one cursor mapping
- What the endpoint does and does not own
- Supported methods
- When to omit
- Common pitfalls

## What a replication block declares

A cursor mapping ties a record field to the request params filtered on it:

- a **cursor field** — the top-level record field whose value is the
  per-record watermark (`updated_at`); and
- the **request param(s)** the runtime sets on the next run to fetch only
  records past that watermark, plus the comparison `operator`.

Pick the variant by how the provider's filter works, and author one form or the
other — a mapping mixing their fields is refused (`RULE-ENDP-004`).

## Single-param cursor (most providers)

The provider takes one open-ended "changed since X" filter. Use a
`SingleCursorMapping`:

<!-- validate: api-endpoint#/operations/read/replication -->
```json
{
  "replication": {
    "supported_methods": ["full_refresh", "incremental"],
    "cursor_mappings": [
      {
        "cursor_field": "updated_at",
        "param": "updated_since",
        "operator": "gte",
        "format": "date-time"
      }
    ]
  }
}
```

- `operator` relates the cursor field to the param (`gte` → "at or after the
  stored watermark").
- `format` — how the stored value is rendered *into the param*. Set it only
  when the param expects a specific encoding (e.g. `epoch_seconds`); omit it
  when the field is already in the param's native form. It says nothing about
  the field: an integer field counting epoch ticks declares that in its own
  `format` in `response.schema`, and an integer id takes no mapping `format`
  at all (`RULE-ENDP-078`).

## Bounded-window cursor

The provider won't take an open "since" filter — it requires a closed
window with separate start and end params (e.g. `from`/`to`). Use a
`WindowCursorMapping`:

<!-- validate: api-endpoint#/operations/read/replication -->
```json
{
  "replication": {
    "supported_methods": ["full_refresh", "incremental"],
    "cursor_mappings": [
      {
        "cursor_field": "created",
        "start_param": "created_after",
        "start_operator": "gte",
        "end_param": "created_before",
        "end_operator": "lt",
        "format": "date"
      }
    ]
  }
}
```

Reach for the window variant only when the provider *requires* both
bounds. If an open "since" filter works, the single-param variant is
simpler and spares the runtime from computing an upper bound.

## Wiring (same three places as pagination)

Each param a cursor mapping names is an ordinary declared param: give it
`controlled_by: "replication"` (`RULE-ENDP-011`), bind it with
`{"from_param": …}` (`RULE-ENDP-009`), and never name it as a `filters`
map landing site (`RULE-ENDP-002`) — replication owns its value, so a
filters entry landing there advertises a filter the runtime overwrites and
the run reports success over unfiltered data.
A window mapping wires `start_param` and `end_param` each that way.

A cursor on `updated_at` requires `updated_at` to be a declared field of the
record shape `response.schema` describes (`RULE-ENDP-013`), not merely
something the provider mentions.

That field's own declaration is read as well, because the stored cursor is
read back through it. It must name exactly one JSON type besides `null` in
its own `type`, and that type must be `string` or `integer`
(`RULE-ENDP-074`); a number, boolean or container leaves the next run nothing
it can compare. Write a nullable cursor as `{"type": ["string", "null"]}`:
the declaration is read off that node and no deeper, so a type reached only
through an `anyOf`/`oneOf` branch, a `$ref` or an `allOf` is refused — the
reader that reads a stored cursor back descends none of them, and a document
it cannot read is one that fails as the endpoint is prepared, on every
replication method — not only the incremental one.
Where the field sits is read the same way: it must be a plain key on the
record shape's `properties`, looked up whole. A dotted `cursor_field` names
no key there, so point the cursor at a top-level record field rather than a
nested one. A `$ref` base or an `allOf` branch alongside is fine — the
record shape's own declaration of the field is the one that is read, and
the branch's is not read at all. So a field declared ONLY on that base is
invisible, and a `format` added by a branch to a `type` on the shape is
invisible too — declare the cursor field on the record shape's own
`properties`, with its `type` and any `format` it needs on that node. An
integer says which kind of integer it is in its own
`format` — on that same node, for the same reason: `epoch_seconds` or
`epoch_milliseconds` makes it a moment, a calendar cursor format is refused
outright — a moment an integer cannot spell — and anything else, a provider's
own width token or no format at all, makes it a monotonic id
(`RULE-ENDP-078`). An id has no "now", so it takes neither the window variant
nor a mapping `format`.

## More than one cursor mapping

An endpoint may declare several mappings when the provider exposes more than
one usable watermark (e.g. `updated_at` and `created_at`). Declaring them does
not pick one — it advertises the choices, and the consuming stream selects
which to sync on. List every mapping the provider genuinely supports rather
than pre-choosing on the operator's behalf.

## What the endpoint does and does not own

The endpoint declares how the watermark is sent, never sync policy
(`RULE-ENDP-042`). An author reaches for a fudge factor when the provider has
clock skew or late-arriving rows: that is the operator's sync policy to set per
run, not a shift to bake into the mapping's `operator` or `format`.

## Supported methods

`supported_methods` is the endpoint's capability claim, drawn from the
vocabulary `RULE-ENDP-038` names — which is also why the block carries no
default-method key. Claim incremental sync only where a cursor mapping actually
backs it; an endpoint with no cursorable field omits `replication` entirely
(below).

## When to omit

Omit `replication` entirely when:

- The resource has no cursorable field (no `updated_at`, no monotonic id).
- The endpoint is a small static lookup (countries, currencies).
- The provider doesn't expose a filter param for the cursor field.

## Common pitfalls

- Don't fabricate a cursor field. If `updated_at` is response-side only
  (no filter param), there's no incremental sync to declare.
- `cursor_field` is not a pointer into the page envelope and not a value
  expression.
- Canonical types are resolved through the read map in the standalone
  `type-map.json` shipped alongside the connector, never from anything in `cursor_mappings`.
