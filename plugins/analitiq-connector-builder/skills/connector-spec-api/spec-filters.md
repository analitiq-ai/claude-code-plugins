# Filters: which fields a stream may filter on

A read operation's `filters` map answers a question a stream cannot answer for
itself: *this provider can be asked to compare this field this way, and here is
the param that carries the comparison.*

It is keyed by **record field** — the name the stream writes in
`source.filters[].field`, and one the response schema declares. Each field names
the operators it offers, and each operator names the param its comparison is
written to (`RULE-ENDP-065`).

<!-- validate: api-endpoint#/operations/read/filters -->
```jsonc
{
  "amount": {
    "gt": {"from_param": "minAmount"},
    "lt": {"from_param": "maxAmount"},
    "neq": {"from_param": "amount", "template": "<>${stream.filter.value}"}
  }
}
```

## The param is the site

A request binding maps a wire name to one value, so the param is what makes two
comparisons distinguishable. Its own `request` binding already says how it
reaches the wire, which is all most providers need:

| Provider writes | Author as |
|---|---|
| `?minAmount=100&maxAmount=500` | a param per bound, one per operator |
| `?amount__gt=100` | a param bound to the query key `amount__gt` |
| `?created[gte]=…` | a param bound to the query key `created[gte]` |

**No param carries two comparisons**, anywhere in the operation — not two
operators on one field, and not two fields reaching for the same param. Two
would describe one request, and neither document would record which comparison
was meant.

So when a provider's documentation reads as though one key carries several
comparisons, read it again: usually what looked like one key is a family
(`startDate` / `endDate`). Where a key genuinely serves a set and a single value
alike — a comma-joined `?type=INVOICE,CREDIT` — declare the set operator (`in`)
and let a one-element list carry the single case; the param's own serialization
declaration owns how a list is spelled (`RULE-ENDP-003`).

## When the comparison goes inside the value

Some providers write the comparison in the value rather than the key, so the
param alone cannot express it. Add a `template` — the param still names the
slot, the template names the spelling:

| Provider writes | `from_param` | `template` prefixes the value with |
|---|---|---|
| `?amount=<>0` | `amount` | `<>` |
| `?q=created>2020-01-01` | `q` | `created>` |
| `?$filter=amount gt 100` | `$filter` | `amount gt ` |

A template carries the filter's value — the fenced example above shows the
placeholder — and one that never does is refused (`RULE-ENDP-066`), since it
would send the same comparison whatever the stream asked for.

## What not to put here

- **A param the runtime owns.** A param carrying `controlled_by` belongs to
  pagination or replication, and naming it declares two authorities over one
  slot (`RULE-ENDP-067`).
- **A field the records do not carry.** Keys resolve against the response
  schema, so a field the record shape never declares is refused here rather
  than at the first read.
- **A field the provider cannot filter on.** Absence is the correct
  declaration. Whether a stream respects it is settled where the stream and the
  endpoint are resolved together, not by this document.
- **An operator you cannot point at a param.** An operator with no landing site
  is a comparison this contract gives the request no way to carry. Leave it out.

## Reading the provider

The map is only as good as the provider's filtering documentation. Before
authoring a field's entry, find the provider's own statement of what the
parameter does — an inclusive bound authored as exclusive shifts every window by
one boundary value.

Where the documentation is ambiguous about inclusivity, ask rather than guess.
Inclusivity is carried by the operator itself, with no separate flag to correct
it afterwards, so the guess is not recoverable later from the document.
