# `columns` block

The `columns` array holds:

<!-- BEGIN GENERATED: fields-column -->
`analitiq.contracts.endpoints.Column` — closed (`additionalProperties: false`); required: `arrow_type`, `name`, `native_type`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `name` | **yes** | string | — | `minLength=1` |
| `native_type` | **yes** | string | — | `minLength=1` |
| `arrow_type` | **yes** | string | — | `pattern=(long; see `endpoint-spec/spec-columns.md`)` |
| `nullable` | no | boolean \| null | `None` | — |
| `default` | no | any \| null | `None` | — |
| `comment` | no | string \| null | `None` | — |
| `ordinal_position` | no | integer \| null | `None` | `min=1` |
| `properties` | no | map of ColumnFieldSpec \| null | `None` | — |
| `items` | no | ColumnFieldSpec \| null | `None` | — |

Carries 3 declarative cross-field `if`/`then` rule(s) — see the registered rules for their prose.
<!-- END GENERATED: fields-column -->

## Contents

- `name`
- `native_type`
- `arrow_type`
- `nullable`
- `default`
- `comment`
- `ordinal_position`
- Registered endpoint rules

## `name`

Verbatim from introspection — no case-folding, quoting or other normalisation
(`RULE-DBEP-009`).

## `native_type`

The provider-native type label, e.g.:

| Dialect | examples |
|---|---|
| PostgreSQL | `uuid`, `text`, `integer`, `numeric(12,2)`, `timestamp with time zone`, `jsonb` |
| MySQL | `BIGINT UNSIGNED`, `VARCHAR(255)`, `DATETIME`, `JSON` |
| Snowflake | `NUMBER(38,0)`, `VARCHAR(16777216)`, `TIMESTAMP_TZ` |
| BigQuery | `STRING`, `INT64`, `STRUCT<…>`, `TIMESTAMP`, `BIGNUMERIC` |
| MongoDB | `BSON.ObjectId`, `BSON.Date`, `BSON.Document` |

When introspection cannot report a column's type, author `"unknown"` — the
fallback label the contract's `native_type` field declares — never a guess and
never an invented placeholder (`RULE-DBEP-012`).

## `arrow_type`

Fully-qualified Apache Arrow canonical type string.

<!-- BEGIN GENERATED: arrow-types -->
`arrow_type` is validated by one published regex, `analitiq.contracts.endpoints.ARROW_TYPE_PATTERN` — generated from the engine-published grammar manifest, so it accepts exactly what the engine executes. Its top-level alternatives fall into two families.

**Plain names** — write them exactly as shown:

`Binary`, `Boolean`, `Date32`, `Date64`, `Float16`, `Float32`, `Float64`, `Int16`, `Int32`, `Int64`, `Int8`, `Json`, `LargeBinary`, `LargeUtf8`, `List`, `Null`, `Object`, `UInt16`, `UInt32`, `UInt64`, `UInt8`, `Utf8`

**Parameterized** — the parameter is part of the type and is *not* optional; a bare name here is rejected:

- `Decimal128\((?:[1-9]|1[0-9]|2[0-9]|3[0-8])\s*,\s*(?:[0-9]|1[0-9]|2[0-9]|3[0-8])\)`
- `Decimal256\((?:[1-9]|1[0-9]|2[0-9]|3[0-9]|4[0-9]|5[0-9]|6[0-9]|7[0-6])\s*,\s*(?:[0-9]|1[0-9]|2[0-9]|3[0-9]|4[0-9]|5[0-9]|6[0-9]|7[0-6])\)`
- `Duration\((?:SECOND|MILLISECOND|MICROSECOND|NANOSECOND)\)`
- `FixedSizeBinary\([1-9][0-9]*\)`
- `Time32\((?:SECOND|MILLISECOND)\)`
- `Time64\((?:MICROSECOND|NANOSECOND)\)`
- `Timestamp\((?:SECOND|MILLISECOND|MICROSECOND|NANOSECOND)(?:\s*,\s*(?:null|[A-Za-z_][A-Za-z0-9_\/\-]*|Etc\/GMT[+\-][0-9]{1,2}|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]))?\)`

There are **no angle-bracket container forms**: nested data is declared with the bare authored-shape markers `Object` / `List` (with sibling `properties` / `items` on the owning column or field spec) or opaque `Json`. `Decimal128/256` additionally require scale <= precision — a cross-parameter bound the regex cannot express; the validator enforces it.
<!-- END GENERATED: arrow-types -->

Each type admits only the units its alternative above lists —
`Time32(MICROSECOND)` and `Time64(SECOND)` are rejected.

### `Timestamp` timezone

The zone slot is optional; the `Timestamp` alternative above carries every
accepted spelling. The judgment is which to author: omit the slot for a source
column with no zone; write literal `null` to mark the zone explicitly unknown
rather than absent; write an actual zone for a zoned source, preferring `UTC`
unless the source's own zone is load-bearing.

### Canonical examples

```
Utf8
Int64
Boolean
Date32
Decimal128(38, 9)
Decimal256(76, 0)
Timestamp(MICROSECOND)
Timestamp(MICROSECOND, UTC)
Timestamp(MILLISECOND, +05:30)
Time32(SECOND)
Time64(NANOSECOND)
Duration(MICROSECOND)
FixedSizeBinary(16)
Object
List
Json
```

### Mapping guidance

Judgment supplies a value only where no type map covers the native
(`RULE-DBEP-004`); resolve through the maps first — `spec-type-map-gaps.md`
§Gap detection.

| Provider native | Typical fully-qualified `arrow_type` |
|---|---|
| `uuid`, `text`, `varchar(n)`, `char(n)` | `Utf8` |
| `smallint` / `integer` / `bigint` | `Int16` / `Int32` / `Int64` |
| `BIGINT UNSIGNED` (MySQL) | `UInt64` |
| `real` / `double precision` | `Float32` / `Float64` |
| `boolean` / `BOOL` | `Boolean` |
| `numeric(p,s)` / `DECIMAL(p,s)` | `Decimal128(p, s)` (use `Decimal256` when the precision exceeds what the `Decimal128` alternative above admits) |
| `date` | `Date32` |
| `timestamp` / `DATETIME` (no zone) | `Timestamp(MICROSECOND)` |
| `timestamp with time zone` / `TIMESTAMP_TZ` / BigQuery `TIMESTAMP` | `Timestamp(MICROSECOND, UTC)` |
| BSON `Date` / JavaScript `Date` (ms epoch) | `Timestamp(MILLISECOND, UTC)` |
| `time` | `Time64(MICROSECOND)` |
| `bytea` / `BLOB` | `Binary` |
| arrays | `List` + sibling `items` when the element shape is known, else `Json` |
| record / composite / STRUCT | `Object` + sibling `properties` when introspected, else `Json` |
| JSON / JSONB / VARIANT (not introspected) | `Json` |

### Nested data is authored-shape only

Declare nested shape with the bare markers plus sibling keys on the **column
itself** (`RULE-ENDP-021`):

- `Object` — the sibling `properties` map is recursive: each child is
  `{arrow_type, …}` and may itself be `Object`/`List`.
- `List` — the sibling `items` field spec describes the element.
- `Json` — opaque. Use it when you do not introspect the inner shape.

See `examples/bigquery-struct-table.example.json` for a BigQuery `STRUCT`
column declared as `Object` + `properties`. For schemaless or opaque container
types (e.g. MongoDB `BSON.Document`, PostgreSQL `jsonb` you do not
introspect), use `Json` — never a scalar like `Utf8`, which throws the
structure away.

## `nullable`

`true` when the database reports the column as nullable, else `false`. Omit when
the dialect doesn't expose this (e.g., schemaless engines).

## `default`

The parsed default expression when introspection reports one, else `null` —
never an invented one.

## `comment`

Provider-attached comment (PostgreSQL `COMMENT ON COLUMN`, MySQL `COMMENT`,
etc.). Forwarded verbatim. `null` when absent.

## `ordinal_position`

Declared ordinals canonicalise column order; each must differ from every
other's (`RULE-DBEP-002`). Omit for schemaless engines (MongoDB).

## Registered endpoint rules

Every rule this plugin owns over a database endpoint document is in
`../pipeline-builder/references/rules/database-endpoint.md` — **read it before
authoring** and satisfy every row. Rules graded at the API scope live where
the index in `../pipeline-builder/SKILL.md` § "Registered rules for every
document" places them; this skill authors only database endpoints.
