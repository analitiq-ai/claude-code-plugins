# Filters: where each operator reaches the wire

A read operation's `filters` map is the endpoint's answer to one question a
stream cannot answer for itself: *this provider can be asked to compare this
field this way, and here is how the comparison is spelled.*

It is keyed by **record field** — the name the stream writes in
`source.filters[].field` — and each field names the operators it offers. Each
operator names its **landing site**: the one place the comparison is expressed.

<!-- validate: api-endpoint#/operations/read/filters -->
```jsonc
{
  "amount": {
    "gt": {"from_param": "minAmount"},
    "lt": {"from_param": "maxAmount"}
  }
}
```

## The two landing sites

A provider spells a comparison in one of two places, and the map has a form for
each. Which one you reach for is a fact about the provider's documentation, never
a preference.

**In a parameter of its own** — `{"from_param": "<name>"}`. The operator routes
the stream's value to that param, and the param's `request` binding already says
how it is spelled. This covers every provider that gives each bound its own key:

| Provider writes | Author as |
|---|---|
| `?minAmount=100&maxAmount=500` | a param per bound, one per operator |
| `?amount__gt=100` | a param bound to the query key `amount__gt` |
| `?created[gte]=…` | a param bound to the query key `created[gte]` |

**In the value** — `{"template": "…"}`. Some providers write the comparison
inside the value, so the rendered string is the whole predicate. Interpolate the
filter's value with `${stream.filter.value}`:

| Provider writes | Author as |
|---|---|
| `?amount=<>0` | `{"template": "<>${stream.filter.value}"}` |
| `?q=created>2020-01-01` | `{"template": "created>${stream.filter.value}"}` |
| `?$filter=amount gt 100` | `{"template": "amount gt ${stream.filter.value}"}` |

A template that never interpolates the value is refused (`RULE-ENDP-066`): it
would send the same comparison whatever the stream asked for.

## Two operators never share a landing site

This is the rule the map exists for (`RULE-ENDP-065`). If `gt` and `lt` both
routed to one param, both would build the identical request — the provider would
apply whichever comparison that param means, and the read would come back with
the wrong rows, green, with nothing raised on either side. A distinct landing
site per operator is what makes the two distinguishable on the wire.

So when a provider's own documentation says one key carries several comparisons,
read it again before authoring: usually it does not, and what looked like one key
is a family (`startDate` / `endDate`). Where a key genuinely serves a set and a
single value alike — a comma-joined `?type=INVOICE,CREDIT` — declare the set
operator (`in`) and let a one-element list carry the single case; `style` and
`explode` on the param own how the list is serialised.

## What not to put here

- **A param the runtime owns.** A param carrying `controlled_by` is set on every
  request by pagination or replication, so routing a filter onto it advertises a
  predicate the runtime overwrites (`RULE-ENDP-002`).
- **A field the provider cannot filter on.** Absence is the correct declaration:
  a field with no key in the map is not filterable, and a stream asking for one
  fails at authoring time rather than reading unfiltered data.
- **An operator you cannot point at a spelling.** There is no client-side
  fallback — an operator with no landing site is a comparison the request cannot
  carry. Leave it out.

## Reading the provider

The map is only as good as the provider's filtering documentation, and that is
the part most often skimmed. Before authoring a field's entry, find the
provider's own statement of what the parameter does — an inclusive bound written
as exclusive is the failure that reads downstream as missing rows on the
boundary, not as a mapping to fix.

Where the documentation is ambiguous about inclusivity, say so to the user and
ask rather than guessing: the operator carries inclusivity in this contract
(`gte` / `lte` inclusive, `gt` / `lt` exclusive), so the guess is not recoverable
later from the document.
