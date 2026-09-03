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

- a **cursor field** — the dotted record path whose value is the
  per-record watermark (`updated_at`, `meta.changed`); and
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
- `format` — set it only when the param expects a specific
  encoding of the value (e.g. `epoch_seconds`); omit it when the field is
  already in the param's native form.

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
`{"from_param": …}` (`RULE-ENDP-009`), and never name it as a filter operator's
landing site (`RULE-ENDP-002`) — replication owns its value, so a filter routed
onto it is one the runtime overwrites, and the run reports success over
unfiltered data.
A window mapping wires `start_param` and `end_param` each that way.

A cursor on `updated_at` requires `updated_at` to be a declared field of the
record shape `response.schema` describes (`RULE-ENDP-013`), not merely
something the provider mentions.

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
- Canonical types are resolved through the standalone `type-map-read.json`
  shipped alongside the connector, never from anything in `cursor_mappings`.
