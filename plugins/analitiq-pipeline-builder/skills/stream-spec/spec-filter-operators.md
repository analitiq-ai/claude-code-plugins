# Filter operator vocabularies

Filters declared in `source.filters[]` name an operator from a closed
vocabulary (`RULE-STRM-036`), narrowed to the **source scope's** half of it — a
database operator on an API source, or the reverse, is rejected locally
(`RULE-STRM-012`).

<!-- BEGIN GENERATED: filter-operators -->
| Availability | Operators |
|---|---|
| Both scopes | `eq`, `gt`, `gte`, `in`, `lt`, `lte`, `neq`, `not_in` |
| `scope: "connection"` (database) only | `ilike`, `is_not_null`, `is_null`, `like` |
| `scope: "connector"` (API) only | `contains`, `ends_with`, `starts_with` |

`is_null`, `is_not_null` are unary — they must omit `value`; every other operator requires it.
<!-- END GENERATED: filter-operators -->

## How filters combine

Multiple entries in `source.filters[]` combine with an implicit **`AND`** — every
filter must hold for a record to be read. There is no `or`, no `not`, and no
nesting: complex boolean grouping is deliberately out of the contract's scope.
When a user asks for one, do not attempt to encode it (a disjunction is not
expressible as a list of `in` values in general). Say the contract cannot express
it and offer the alternatives that exist: narrow the filter set, filter
downstream, or ask the connector to expose a suitable parameter.

## Authoring notes

- Inclusivity is carried **by the operator**, never by a separate flag: `gte` and
  `lte` are inclusive, `gt` and `lt` are exclusive. There is no `inclusive`
  field, so a range that must include its boundary value has to be authored with
  the inclusive operator.
- `like` / `ilike` accept SQL wildcard syntax in `value` (`%`, `_`). The engine
  routes these to the dialect's pattern operator.
- `value` typing follows the referenced field (`RULE-STRM-027`).
- `field` names a **record field** on either scope (`RULE-STRM-022`): a column of
  the source endpoint's schema on a database source, a field the endpoint's read
  `filters` map offers on an API source. It is never a provider parameter name —
  the endpoint's map is what routes the comparison to a parameter.

## What the local validator still cannot check

<!-- PROBE: stream-filter-field-unresolved-locally -->
Field-existence is **not** resolved locally — the validator does not read filter
fields against endpoint files, so a typo in `field` passes here and fails
server-side at save time. Read the field name back to the user rather than
guessing it.

Membership is not a promise of executability (`RULE-STRM-012`): a dialect may
refuse an operator for a given column or type even though it is in the set
above. Read the operator choice back to the user when the column type is
unusual.

For an API source, the endpoint document narrows the vocabulary further
(`RULE-STRM-026`). Read `operations.read.filters` on the endpoint:

- a key for the field, carrying the operator → filterable with that operator.
- a key for the field, without that operator → the provider offers no way to
  send this comparison on this field. Pick an operator the field does offer, or
  say so; there is no client-side fallback.
- no key for the field → not filterable, whatever parameters the endpoint
  declares.

Each operator the map carries names where it reaches the wire — a parameter of
its own, or a template rendering the comparison into the value. That is the
endpoint author's concern, not the stream's; what matters here is that an
operator with no entry cannot be asked for.
