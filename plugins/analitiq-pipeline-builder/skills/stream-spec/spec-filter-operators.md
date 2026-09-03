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
- `field` names a record field on both branches — a column on a database
  source, a field the endpoint's read `filters` map keys on for an API source
  (`RULE-STRM-022`). Never a request parameter name: which param an operator
  lands in is the endpoint's business and never leaves it.

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
(`RULE-STRM-026`). Its read operation carries a `filters` map keyed by record
field, and under each field one entry per operator it can actually send:

- the field has an entry, and that entry has this operator → filterable with it.
- the field has an entry, and it does not → the endpoint cannot express this
  comparison. Pick an operator it does bind, or ask for the endpoint to offer
  one; do not substitute a neighbouring operator, which reads different rows.
- the field has no entry → not filterable at all.

An operator absent from the map cannot reach the wire, which is why the map
exists: before it, an endpoint could advertise an operator with nowhere to send
it, and three operators built one identical request.

### Worked: a filter against an API source

Given an endpoint whose read operation declares

<!-- illustrative -->
```json
{
  "filters": {
    "amount": { "gt": { "param": "min_amount" } }
  }
}
```

the stream that narrows on it is

<!-- validate: stream#/source/filters -->
```json
[
  { "field": "amount", "operator": "gt", "value": 100 }
]
```

`field` is `amount` — the record field the endpoint keys its map on, not
`min_amount`. Which param the operator lands in, and which query key that param
reaches, are the endpoint's decisions and never appear in the stream.
