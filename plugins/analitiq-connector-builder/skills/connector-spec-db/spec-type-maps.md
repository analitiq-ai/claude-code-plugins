# Type maps

How to author the standalone `type-map.json` that ships alongside every
connector. A type map connects provider-native type labels and Apache
Arrow canonical types, with a section for each direction the connector's
`kind` calls for (`RULE-PKG-030`):

- **Read map** (the `read` section) — native → Arrow. For databases it
  maps native column types (`BIGINT`, `NUMERIC(10,2)`); for API connectors
  it maps the JSON Schema `format`/`type` strings used as endpoint-field
  natives.
- **Write map** (the `write` section) — Arrow → native. It is the
  connector's declarative DDL vocabulary: every transport (SQLAlchemy
  DDL, ADBC DDL, control-plane create_table) renders column types
  through `dialect.render_column_type`, whose default implementation is
  this map (`RULE-PKG-023`).

Which sections a connector's map carries is decided by its `kind`
(`RULE-PKG-030`): a database connector's carries `read` and `write`, an API
connector's `read` alone.

## Contents

- On-disk location
- File shape
- Uppercase rule (read maps)
- `${name}` substitution in regex rules
- Schemaless / JSON-shaped natives
- Non-obvious natives (derive, don't guess)
- API coverage (read map)
- Database coverage
- Arrow types
- Worked example: Postgres (read)
- Worked example: Postgres (write)
- Out of scope

## On-disk location

The map is a **standalone** sibling of `connector.json`:

```
{connector_id}/definition/type-map.json
```

It validates against `https://schemas.analitiq.ai/type-map/latest.json`. The
`write` section shares the `read` section's rule shape but inverts the direction
(`arrow_type` matches, `native_type` renders). The map is never embedded inside
`connector.json` or any endpoint document, and no other `type-map-*.json` name
is authored beside it (`RULE-PKG-030`).

## File shape

The file is a top-level JSON object: its `$schema` URL and, keyed `read` and
`write`, a non-empty array of rule objects per direction, each authored in
resolution order (`RULE-TMAP-013`). A section the connector has no use for is
left out:

<!-- validate: type-map -->
```json
{
  "$schema": "https://schemas.analitiq.ai/type-map/latest.json",
  "read": [
    { "match": "exact", "native_type": "BOOLEAN", "arrow_type": "Boolean" }
  ],
  "write": [
    { "match": "exact", "arrow_type": "Boolean", "native_type": "BOOLEAN" }
  ]
}
```

Each rule object carries exactly the keys named below
and no others — but which key is the *matcher* and which is *rendered*
depends on the direction:

| Key | Read map (`read`) | Write map (`write`) |
|---|---|---|
| `match` | `"exact"` or `"regex"` — how the matcher is compared. | Same. |
| `native_type` | **Matcher.** Literal label (`exact`) or pattern (`regex`). | **Rendered.** The native DDL emitted for a matching `arrow_type`; may carry `${name}` substitutions on `regex` rules. |
| `arrow_type` | **Rendered.** Literal Arrow type, or (on `regex` rules) a template with `${name}` placeholders. | **Matcher.** Literal Arrow type (`exact`) or pattern over the `arrow_type` string (`regex`). |

Matching is full-string, so leading `^` and trailing `$` are harmless but
redundant — keep them for readability when the pattern would otherwise look
ambiguous.

## Uppercase rule (read maps)

Read-side normalization rewrites a native before it is matched:

<!-- BEGIN GENERATED: native-normalization -->
| Authored or probed native | Spelled this way at match time |
|---|---|
| ` varchar ` | `VARCHAR` |
| `CHARACTER  VARYING` | `CHARACTER VARYING` |
| `numeric(10, 2)` | `NUMERIC(10, 2)` |
<!-- END GENERATED: native-normalization -->

It is applied differently to each rule kind, and that difference is the whole
rule:

- **`exact` rules are normalized symmetrically.** The rule's `native_type` is
  normalized at map-build time and the probed native at lookup, so
  `{"native_type": "varchar"}` and `{"native_type": "CHARACTER  VARYING"}` both match.
  Case and spacing genuinely don't matter here — SQL type names are
  case-insensitive and drivers report inconsistent casing, so matching verbatim
  would be a silent-miss footgun.
- **`regex` rules normalize the probe only** (`RULE-TMAP-014`). The pattern is
  used exactly as authored, deliberately: uppercasing it would corrupt classes
  like `\d` into `\D` — so write `^VARCHAR\(\d+\)$`, never
  `^varchar\(\d+\)$`, which can never match.
- **Named capture group names stay lowercase** (`(?<precision>…)`) — only the
  matched text is uppercased, not the group names.

Uppercase remains the house style for `exact` natives too — it reads
consistently against the regex rules that sit beside them — but it is a
convention there, not a correctness requirement.

<!-- PROBE: write-map-regex-arrow-type-case-unchecked -->
Case matters on the write side (`RULE-TMAP-015`): a lowercase **`regex`**
`arrow_type` is not checked at all — `{"match": "regex", "arrow_type": "^utf8$"}`
validates with zero findings and simply never fires.

## `${name}` substitution in regex rules

A `${name}` on a rule's rendered side names a **named capture group** on its
matcher side (`RULE-TMAP-003` read, `RULE-TMAP-016` write) — but only the read
side is enforced: an unbacked write-side `${name}` surfaces first at DDL
render, so verify that half yourself. `RULE-TMAP-005` (read) and
`RULE-TMAP-009` (write) carry what a matcher must compile under and how a
capture is spelled.

- Read map: placeholders in `arrow_type`, captures in `native_type` —
  `native_type: "^NUMERIC\\((?<precision>[1-9]|[12]\\d|3[0-8]),\\s*(?<scale>\\d|[12]\\d|3[0-8])\\)$"`,
  `arrow_type: "Decimal128(${precision}, ${scale})"`. Each capture is bounded to
  what its parameter position admits — RULE-TMAP-010 refuses the rule otherwise.
- Write map: placeholders in `native_type`, captures in `arrow_type` —
  `arrow_type: "^Decimal(128|256)\\((?<p>\\d+),\\s*(?<s>\\d+)\\)$"`,
  `native_type: "NUMERIC(${p}, ${s})"`.

Placeholders are only legal in **parameter positions** of parameterized
types — `Decimal128(${precision}, ${scale})`, `FixedSizeBinary(${n})` on the
read side (`RULE-TMAP-006`); `NUMERIC(${p}, ${s})`, `VARCHAR(${len})` and
similar on the write side, where the rendered native is DDL and only the
placeholder's well-formedness is held (`RULE-TMAP-009`).

On the **read** side a templated render is only legal on a `regex` rule —
an `exact` rule's `arrow_type` must be a fully-resolved Arrow type (the
type-pattern constraint rejects `${…}` there), and an `exact` `native_type` has
no captures to substitute from.

On the **write** side, never author a `${…}` in an `exact` rule's rendered
`native_type`: a placeholder must name a capture its own matcher declares
(`RULE-TMAP-016`), and an `exact` rule has none. Render a concrete native
(`TEXT`, or a fixed `VARCHAR(255)`); use a `regex` rule when the width
genuinely comes from the `arrow_type`.

(Timestamp precision is **not** a `${}` case — Arrow's unit is a
symbolic enum, not a digit; match on the native's digit count and ladder
it to a unit instead. See "Database coverage → Read map".)

## Schemaless / JSON-shaped natives

A schemaless or structured-container native — `JSON`, `JSONB`, `VARIANT`,
`OBJECT`, `ARRAY`, `MAP`, `STRUCT`, a parameterized container like
`array<object>`, or a SQL array suffix like `integer[]` — maps to **`Json`**
(`RULE-TMAP-001`). The `arrow_type` is a *claim about the shape* of the data:
`Utf8` asserts an opaque string and throws the structure away, so it is wrong
for a JSON / array / struct column even when the driver happens to hand the
value over as text on the wire.

`Json` is the only container canonical a read map renders: the shape markers
`Object` / `List` need a sibling `properties` / `items` sub-schema that a
string→string rule cannot carry. Any other container spelling is outside the
published vocabulary and fails validation at author time — check a family's
exact spelling against
[`arrow-types.json`](https://schemas.analitiq.ai/arrow-types.json).

<!-- PROBE: read-map-native-semantics-unchecked -->
> **Only the syntactic half is enforced** (RULE-TMAP-001/002). The contract
> flags a native whose *shape* is visibly a container — angle brackets
> (`array<object>`) or a `[]` suffix (`integer[]`). A bare vendor spelling is
> deliberately not special-cased, so `{"native_type": "JSONB", "arrow_type": "Utf8"}`
> validates **clean**. That is the common case and the one you have to get
> right yourself.

| Native (read) | Canonical |
|---|---|
| `JSON`, `JSONB` (Postgres, MySQL/MariaDB) | `Json` |
| `VARIANT`, `OBJECT`, `ARRAY`, `MAP` (Snowflake) | `Json` |
| `array`, `object` (document stores) | `Json` |
| `integer[]` and other `…[]` array suffixes | `Json` |

`XML` is structured text, not a container, so it maps to `Utf8`.

On the write side the `Json` canonical renders the system's JSON column
type (`Json` → `JSONB` for postgres, `JSON` for MySQL, `VARIANT` for
Snowflake), so the type round-trips.

The shape markers `Object` and `List` split by direction:

- **Read maps never render them** (see above — no rule can carry the
  sibling sub-schema they require). The endpoint walker accepts a field
  typed `Object` or `List` as a valid narrowing of a `Json` read-map
  rule; the validator does not treat that as a mismatch.
- **Write maps must cover them** (`RULE-TMAP-017`). The engine renders every
  destination column's frozen `arrow_type` through the write map **verbatim**
  (`shared/type-maps.md`), and endpoint documents legitimately carry the
  bare markers — an API source's struct field arrives at a database
  destination as the literal canonical `Object`, an array field as
  `List`. A write map without rules for them hard-errors the stream at
  configuration time. Render both exactly like `Json`:

  <!-- validate: type-map -->
  ```json
  {
    "$schema": "https://schemas.analitiq.ai/type-map/latest.json",
    "write": [
      { "match": "exact", "arrow_type": "Object", "native_type": "JSONB" },
      { "match": "exact", "arrow_type": "List",   "native_type": "JSONB" }
    ]
  }
  ```

  Author these as `exact` rules over the bare markers — do **not** widen
  them to regexes over angle-bracket forms (`^(?:Struct<.+>|Object)$`,
  `^(?:Large)?List(?:<.+>)?$`). Those spellings are outside the canonical
  vocabulary entirely — no endpoint or read rule can produce them — so such
  branches are dead pattern surface that misleads the next author.

Map-shaped natives resolve to `Json`, like every other structured container —
never author a `Map<…>` canonical.

## Non-obvious natives (derive, don't guess)

When researching a new system's natives, these are the calls that aren't
mechanical — the same judgment transfers across providers:

- **Semi-structured / container** (`JSON`, `JSONB`, `VARIANT`, `OBJECT`,
  `ARRAY`, `MAP`, `STRUCT`, `…[]`) → `Json`, the only container canonical a
  read map can render — never a scalar (enforced — see "Schemaless /
  JSON-shaped natives").
- **Opaque scalar types with no Arrow equivalent** (`INTERVAL`, `MONEY`,
  network types `INET`/`CIDR`/`MACADDR`, `UUID`, `ENUM(...)`, `XML`) →
  `Utf8`. They are atomic strings on the wire; don't invent a numeric/Decimal
  canonical.
- **Zoned time-of-day** (`TIME WITH TIME ZONE` / `TIMETZ`) →
  `Time32`/`Time64` (unit per the precision ladder; the zone is dropped —
  a bare time-of-day carries no instant). Contrast
  `TIMESTAMP WITH TIME ZONE` → `Timestamp(<unit>, UTC)`.
- **Bare vs zoned timestamp**: choose the tz-aware canonical only when the
  native (or, for APIs, the sample value) actually carries a zone
  (`RULE-SHRD-002`).
- **A boolean spelled as a narrow numeric** — some systems have no boolean
  type and document a width-1 integer as their boolean (MySQL's `TINYINT(1)`).
  Map the documented boolean spelling to `Boolean`, and keep the general
  numeric native mapping to its integer canonical. Follow the provider's
  documentation, not the type name: only map a numeric to `Boolean` where the
  docs say that spelling *is* the boolean.

## API coverage (read map)

<!-- PROBE: endpoint-pair-unresolved-through-read-map -->
For API connectors, every `(native_type, arrow_type)` pair a typed endpoint
field declares must resolve through the read map — the matched rule's
rendered `arrow_type` (with any `${name}` captures substituted) has to equal
the field's frozen `arrow_type` (`RULE-PKG-033`).

`Object` / `List` endpoint markers are accepted narrowings of `Json` —
an endpoint field with `arrow_type: "Object"` paired with a native that
maps to `Json` is **not** a mismatch.

The natives below are lowercase. Rule style follows from the read-map case
rules (see "Uppercase rule (read maps)"): author uppercase `exact` rules,
and where a rule genuinely needs `regex`, spell its literals the way the
probe is spelled (`^STRING$`, never `^string$` — `RULE-TMAP-014`); a
pattern copied from this lowercase vocabulary is the dead-rule shape that
section warns about.

Common API natives:

| Native | Source | Typical canonical |
|---|---|---|
| `uuid` | `{"type":"string", "format":"uuid"}` | `Utf8` |
| `date-time` | `{"type":"string", "format":"date-time"}` | `Timestamp(MICROSECOND)` **or** `Timestamp(MICROSECOND, UTC)` — per the sample value's zone (see "Bare vs zoned timestamp" above) |
| `date` | `{"type":"string", "format":"date"}` | `Date32` |
| `email` / `uri` | `{"type":"string", "format":"…"}` | `Utf8` |
| `string` | `{"type":"string"}` | `Utf8` |
| `integer` | `{"type":"integer"}` | `Int64` |
| `int32` / `int64` | `{"type":"integer", "format":"…"}` | `Int32` / `Int64` |
| `number` | `{"type":"number"}` | `Float64` |
| `boolean` | `{"type":"boolean"}` | `Boolean` |
| `object` (schemaless) | `{"type":"object"}` with no `properties` | `Json` |
| `array` (schemaless) | `{"type":"array"}` with no `items` | `Json` |

## Database coverage

**Read map:** ship the documented provider native vocabulary.

- For OLTP databases (PostgreSQL, MySQL), include the full documented
  native vocabulary.
- For warehouses and document stores (Snowflake, MongoDB), restrict to
  the researched, documented list — provider docs are authoritative.
- Do NOT ship a wildcard fallback rule (`RULE-TMAP-011`) — let an uncovered
  native hard-error at runtime so the gap is visible.
- Use `Utf8` for Arrow's UTF-8 string type, never `String`.
- Capture declared precision on parameterized natives — never collapse it
  to a constant (`RULE-TMAP-004`). The fixed default belongs only on the
  unparameterized fallback rule.
  - **Decimal:** regex `(precision, scale)` into named captures and route
    by Arrow width — precision ≤ 38 → `Decimal128(${precision},
    ${scale})`, 39–76 → `Decimal256(...)`. A precision-only declaration
    (`NUMERIC(p)`, implicit scale 0) needs its own tier rendering
    `Decimal{128,256}(${precision}, 0)`. Precision > 76 exceeds Arrow, so
    leave it uncovered (visible hard-error, `RULE-TMAP-011`); the
    bare/unparameterized native takes the fixed default.
    Bound the **scale** capture to its tier the way the precision capture
    already is (`RULE-TMAP-010`) — that rule judges each capture against its
    own parameter position, so check by hand the precision/scale pairs a tier
    can produce.
  - **Timestamp/time:** the native carries a fractional-second *digit
    count*, but Arrow's unit is a symbolic enum — so ladder the digit
    count to the smallest unit that holds it exactly: `(0)`→`SECOND`,
    `(1–3)`→`MILLISECOND`, `(4–6)`→`MICROSECOND`, `(7–9)`→`NANOSECOND`,
    with the bare form rendering the system's documented default unit.
    A single flat unit truncates any system finer than it — this is a
    per-system ladder, not a constant. Time-of-day picks the Arrow width
    off the same unit: `Time32(SECOND|MILLISECOND)` for coarse,
    `Time64(MICROSECOND|NANOSECOND)` for fine.

**Write map:** cover the full executable canonical vocabulary
(`RULE-TMAP-017`). Angle-bracket spellings are outside that vocabulary and get
no rules (see "Arrow types"); what it holds includes the parameterized
families (Decimal via a regex with `${p}`/`${s}` captures), the bare and
tz-aware `Timestamp` forms, and the bare container markers `Object` / `List`
(see "Schemaless / JSON-shaped natives" — API sources hand them over as
literal canonicals).

Run the validator and reconcile every family its `RULE-TMAP-017` warning
names. A gap is legitimate only where the connector's dialect renders
that family itself (`RULE-TMAP-019`) — BigQuery ships no Decimal rule because
NUMERIC/BIGNUMERIC selection needs precision-range arithmetic rules cannot
express.

<!-- PROBE: write-coverage-sample-gap -->
**A clean warning is not proof of coverage.** The check sends one probe per
canonical family, drawn from the same engine-published grammar the vocabulary
comes from, so a family it skips is a declared exclusion rather than an
oversight. One probe per family is also one parameter *value* per family: a map
missing a skipped family, or matching only the probed spelling of a probed one,
still passes. Verify these by hand:

- `FixedSizeBinary` — `byte_width` is unbounded, so no single probe stands for
  the family
- `Time32` (only `Time64` is probed)
- **tz-aware** `Timestamp` — easy to miss, because each probe omits optional
  parameter positions, so the bare `Timestamp` probe passes without it
- `Decimal256` (only `Decimal128` is probed, so a map whose Decimal rule is
  narrowed to `Decimal128` shows nothing)

Mind precision survival on the write side: MySQL's write map renders
`DATETIME(6)` / `TIME(6)` so microseconds survive the round trip — a
bare `DATETIME` silently truncates.

## Arrow types

Arrow canonical types are fully-qualified PascalCase strings from the shared
Arrow vocabulary: a bare name where the family declares no parameters; where it
does, parens carrying every required parameter position and each optional one
the value actually needs; and the bare authored-shape container markers
`Object` / `List` / `Json`.

Nested data goes through the authored-shape path only — `Object` / `List` with
a sub-schema on the owning document, opaque `Json`; no family spells its
members inside angle brackets.

The full vocabulary is `schemas/arrow-types.json`, published at
[`https://schemas.analitiq.ai/arrow-types.json`](https://schemas.analitiq.ai/arrow-types.json)
— the readable reference when you need a family's exact spelling. Note the flat
path: unlike the connector and endpoint schemas there is no `/latest.json`
variant. Validation never fetches it; the enforced form is `ARROW_TYPE_PATTERN`,
generated in `analitiq.contracts.arrow_grammar` from the vendored engine
grammar and matched offline.

For parameterized canonicals whose database native carries an implicit
default, encode the default explicitly:

- Snowflake `TIMESTAMP_NTZ` → `Timestamp(NANOSECOND)` (precision 9).
- Snowflake `NUMBER` → `Decimal128(38, 0)`.
- MongoDB `date` → `Timestamp(MILLISECOND, UTC)` (ms epoch UTC).
- MongoDB `decimal` → `Decimal128(34, 0)` (IEEE 754 decimal128).

Do NOT emit a bare parameterized name from an `exact` rule
(`{"match": "exact", "native_type": "TIMESTAMP_NTZ", "arrow_type": "Timestamp"}`
is wrong — `Timestamp` requires a unit).

## Worked example: Postgres (read)

See the reference read map, the `read` section of
`examples/postgresql/type-map.json` —
uppercase patterns, the width-tiered `NUMERIC`/`DECIMAL` captures
(`Decimal128` ≤ 38, `Decimal256` above, plus precision-only `(p)`→scale-0
tiers, over a bare fallback), the timestamp precision ladder (digit
count → Arrow unit, there instantiated to Postgres's 0–6 range), and a
`JSONB` column mapped to the `Json` container canonical (not a scalar).

## Worked example: Postgres (write)

See the reference write map, the `write` section of
`examples/postgresql/type-map.json` —
`arrow_type` is the matcher (note the regexes over the `arrow_type` string
with lowercase capture names), and `native_type` is the rendered DDL.

Ordering is what that file demonstrates: the bare `^Timestamp\([A-Z]+\)$`
rule sits before the tz rule yet cannot swallow a two-argument `arrow_type` —
but a genuinely overlapping family rule must be ordered carefully.

## Out of scope

Connection-scoped type maps are out of scope for this plugin; see
`shared/type-maps.md` for runtime resolution rules.
