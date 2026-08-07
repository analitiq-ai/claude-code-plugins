# `columns` block

A non-empty array of:

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

Carries 3 declarative cross-field `if`/`then` rule(s) — see the advisory rules for their prose.
<!-- END GENERATED: fields-column -->

## `name`

Verbatim from introspection.

## `native_type`

The provider-native type label, e.g.:

| Dialect | examples |
|---|---|
| PostgreSQL | `uuid`, `text`, `integer`, `numeric(12,2)`, `timestamp with time zone`, `jsonb` |
| MySQL | `BIGINT UNSIGNED`, `VARCHAR(255)`, `DATETIME`, `JSON` |
| Snowflake | `NUMBER(38,0)`, `VARCHAR(16777216)`, `TIMESTAMP_TZ` |
| BigQuery | `STRING`, `INT64`, `STRUCT<…>`, `TIMESTAMP`, `BIGNUMERIC` |
| MongoDB | `BSON.ObjectId`, `BSON.Date`, `BSON.Document` |

Use `"unknown"` as a sentinel when the engine doesn't expose a type.

## `arrow_type`

Fully-qualified Apache Arrow canonical type string. Base names are
PascalCase from `arrow/format/Schema.fbs`.

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

Units are the literal Flatbuffers enum identifiers, uppercase, and each type
admits only the units its alternative above lists — `Time32(MICROSECOND)` and
`Time64(SECOND)` are rejected.

### `Timestamp` timezone

Optional second argument. Valid forms:

- **Omit the slot** — naive timestamp, no implied zone: `Timestamp(MICROSECOND)`.
- **Literal `null`** — explicit naive marker: `Timestamp(MICROSECOND, null)`
  (distinct from omitting; some readers treat it as "zone is unknown
  rather than absent").
- **An actual zone** — IANA name (`UTC`, `Europe/Berlin`), `Etc/GMT±N`,
  or a fixed `±HH:MM` offset: `Timestamp(MICROSECOND, +05:30)`.

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

| Provider native | Typical fully-qualified `arrow_type` |
|---|---|
| `uuid`, `text`, `varchar(n)`, `char(n)` | `Utf8` |
| `smallint` / `integer` / `bigint` | `Int16` / `Int32` / `Int64` |
| `BIGINT UNSIGNED` (MySQL) | `UInt64` |
| `real` / `double precision` | `Float32` / `Float64` |
| `boolean` / `BOOL` | `Boolean` |
| `numeric(p,s)` / `DECIMAL(p,s)` | `Decimal128(p, s)` (use `Decimal256` when `p > 38`; max precision is 76) |
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

There are **no angle-bracket container canonicals** (`Struct<…>`, `List<…>`,
`Map<…>`): the engine does not execute them, and the contract — generated
from the engine's own grammar — rejects them. Declare nested shape with the
bare markers plus sibling keys on the **column itself** (`ADV-ENDP-021`):

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

The parsed default expression if reasonable, else `null`. The runtime treats this
as advisory — actual default behavior is dialect-owned.

## `comment`

Provider-attached comment (PostgreSQL `COMMENT ON COLUMN`, MySQL `COMMENT`,
etc.). Forwarded verbatim. `null` when absent.

## `ordinal_position`

Canonicalizes column order for hashing. Omit for schemaless engines (MongoDB).

## Uniqueness

The registry's database-endpoint rules over this array:

<!-- BEGIN GENERATED: advisory-endpoint -->
| Rule | Constraint |
|---|---|
| `ADV-DBEP-001` | Every column a database endpoint declares MUST carry a name no other column in that document repeats. |
| `ADV-DBEP-002` | Where a database endpoint's columns carry an ordinal position, each column's MUST differ from every other's. |
| `ADV-DBEP-003` | Every name in a database endpoint's `primary_keys` MUST name a column the same document declares. |
| `ADV-ENDP-020` | A column field spec MUST declare the sibling shape key its arrow_type's container marker takes, and MUST declare no shape key at all when its arrow_type is not a container marker. |
| `ADV-ENDP-021` | A database column MUST declare the sibling shape key its arrow_type's container marker takes, and MUST declare no shape key at all when its arrow_type is not a container marker. |

The registry carries these under the same ids, and they are worth citing, but a violation does not come back as a finding — the last column says what does apply it, and `nothing here` means the document validates and fails later.

| Rule | Constraint | Tier | Applied by |
|---|---|---|---|
| `ADV-DBEP-004` | A column's frozen `arrow_type` and `native_type` MUST be the values the applicable type maps render for it; judgment supplies a value only where no map covers the native or the canonical. | referential | nothing here |
| `ADV-DBEP-005` | A discovered object MUST record every namespace level the system it came from actually has, and MUST invent none the system lacks. | judgment | nothing here |
| `ADV-DBEP-006` | A connector release MUST NOT contain a database endpoint document; the connector's resource discovery produces one per connection at connection time. | procedural | nothing here |
| `ADV-DBEP-007` | Database identity MUST be read from an endpoint's `database_object`; the derived `endpoint_id` is an opaque handle and MUST NOT be parsed back into the identifiers it was derived from. | procedural | nothing here |
| `ADV-DBEP-008` | An authored endpoint MUST NOT declare a column the engine synthesises when it creates a table, and MUST drop such a column from a mirrored source's column list. | procedural | nothing here |
| `ADV-DBEP-009` | A database endpoint MUST record every provider identifier exactly as its source reports it, with no case-folding, quoting or other normalisation. | referential | nothing here |
| `ADV-DBEP-010` | A database endpoint for a table that does not exist yet MUST target a namespace discovery returned. | referential | nothing here |
| `ADV-ENDP-043` | A released `endpoint_id` MUST NOT be renamed; a resource whose locator changes ships as a new endpoint document alongside the removal of the old one. | referential | nothing here |
<!-- END GENERATED: advisory-endpoint -->
