# Type maps

How to author the standalone type-map files that ship alongside every
connector. Type maps connect provider-native type labels and Apache
Arrow canonical types, in two directions:

- **Read map** (`type-map-read.json`) — native → Arrow. Required for
  every connector (API and DB). For databases it maps native column
  types (`BIGINT`, `NUMERIC(10,2)`); for API connectors it maps the
  JSON Schema `format`/`type` strings used as endpoint-field natives.
- **Write map** (`type-map-write.json`) — Arrow → native. **Required
  for `kind: database`, forbidden for `kind: api`.** It is the
  connector's declarative DDL vocabulary: every transport (SQLAlchemy
  DDL, ADBC DDL, control-plane create_table) renders column types
  through `dialect.render_column_type`, whose default implementation is
  this map. Connectors must NOT ship Python type-rendering tables.

## On-disk location

Both files are **standalone** siblings of `connector.json`:

```
{connector_id}/definition/type-map-read.json
{connector_id}/definition/type-map-write.json   # database only
```

The read map validates against
`https://schemas.analitiq.ai/type-map-read/latest.json`. The write map
shares the same rule shape but inverts the direction
(`canonical` matches, `native` renders) and validates against its own
published schema, `https://schemas.analitiq.ai/type-map-write/latest.json`;
the validator derives the direction from the filename and runs the full
contract-model + semantic pass on each. Neither map is ever embedded inside
`connector.json` or any endpoint document. Each present file must be
**non-empty** — an empty array is rejected.

The pre-split filename `type-map.json` is dead: the engine never reads
it and the validator rejects it with a migration finding.

## File shape

Each file is a top-level JSON array of rule objects. Order is
significant: **first match wins** during resolution. Each rule object
carries exactly the keys named below and no others — but which key is
the *matcher* and which is *rendered* depends on the direction:

| Key | Read map (`type-map-read.json`) | Write map (`type-map-write.json`) |
|---|---|---|
| `match` | `"exact"` or `"regex"` — how the matcher is compared. | Same. |
| `native` | **Matcher.** Literal label (`exact`) or ECMA-262 regex (`regex`). | **Rendered.** The native DDL emitted for a matching canonical; may carry `${name}` substitutions on `regex` rules. |
| `canonical` | **Rendered.** Literal Arrow type, or (on `regex` rules) a template with `${name}` placeholders. | **Matcher.** Literal Arrow type (`exact`) or ECMA-262 regex over the canonical string (`regex`). |

Matching uses full-string semantics (Python `re.fullmatch`), so leading
`^` and trailing `$` are harmless but redundant — keep them for
readability when the pattern would otherwise look ambiguous.

## Uppercase rule (read maps)

Read-side normalization — trim, collapse internal whitespace runs, uppercase —
is applied differently to the two rule kinds, and that difference is the whole
rule:

- **`exact` rules are normalized symmetrically.** The rule's `native` is
  normalized at map-build time and the probed native at lookup, so
  `{"native": "varchar"}` and `{"native": "CHARACTER  VARYING"}` both match.
  Case and spacing genuinely don't matter here — SQL type names are
  case-insensitive and drivers report inconsistent casing, so matching verbatim
  would be a silent-miss footgun.
- **`regex` rules normalize the probe only.** The pattern is used exactly as
  authored, deliberately: uppercasing it would corrupt classes like `\d` into
  `\D`. So **literals inside a pattern must be uppercase** —
  `^VARCHAR\(\d+\)$`, never `^varchar\(\d+\)$`, which can never match. The
  validator warns on this one.
- **Named capture group names stay lowercase** (`(?<precision>…)`) — only the
  matched text is uppercased, not the group names.

Uppercase remains the house style for `exact` natives too — it reads
consistently against the regex rules that sit beside them — but it is a
convention there, not a correctness requirement.

<!-- PROBE: write-map-regex-canonical-case-unchecked -->
Write-map matchers run against PascalCase canonical strings **case-preserving**
(the Arrow vocabulary is mixed-case), so case *is* significant there. How
loudly a mistake fails depends on the rule kind: a lowercase **`exact`**
canonical is rejected outright (it fails the Arrow type pattern), while a
lowercase **`regex`** canonical is not checked at all —
`{"match": "regex", "canonical": "^utf8$"}` validates with zero findings and
simply never fires.

## `${name}` substitution in regex rules

When a `regex` rule's rendered side carries `${name}` placeholders,
every placeholder must be backed by a matching **named capture group**
in the matcher side. Both directions compile the matcher as ECMA-262,
so write the capture form `(?<name>…)` — Python-only `(?P<name>…)` is
rejected on either side (ADV-TMAP-005 on the read side; model-level on
the write side). The backing-captures half is enforced on the read side
only (ADV-TMAP-003): an unbacked write-side `${name}` surfaces first at
DDL render — verify that half yourself.

- Read map: placeholders in `canonical`, captures in `native` —
  `native: "^NUMERIC\\((?<precision>[0-9]+),\\s*(?<scale>[0-9]+)\\)$"`,
  `canonical: "Decimal128(${precision}, ${scale})"`.
- Write map: placeholders in `native`, captures in `canonical` —
  `canonical: "^Decimal(128|256)\\((?<p>\\d+),\\s*(?<s>\\d+)\\)$"`,
  `native: "NUMERIC(${p}, ${s})"`.

Placeholders are only legal in **parameter positions** of parameterized
types (`Decimal128(${precision}, ${scale})`, `FixedSizeBinary(${n})` on
the read side; `NUMERIC(${p}, ${s})`, `VARCHAR(${len})` and similar on
the write side).

On the **read** side a templated render is only legal on a `regex` rule —
an `exact` rule's `canonical` must be a fully-resolved Arrow type (the
type-pattern constraint rejects `${…}` there), and an `exact` native has
no captures to substitute from.

On the **write** side the contract *accepts* `${…}` in an `exact` rule's
rendered `native` (the placeholder would be filled from a per-column hint
rather than a regex capture) — but **do not author one**. DDL rendering passes
no per-column hints, and an unsubstituted placeholder raises during type
mapping, before any DDL is emitted. Render a concrete native (`TEXT`, or a fixed
`VARCHAR(255)`); use a `regex` rule when the width genuinely comes from the
canonical.

(Timestamp precision is **not** a `${}` case — Arrow's unit is a
symbolic enum, not a digit; match on the native's digit count and ladder
it to a unit instead. See "Database coverage → Read map".)

## Schemaless / JSON-shaped natives

A schemaless or structured-container native — `JSON`, `JSONB`, `VARIANT`,
`OBJECT`, `ARRAY`, `MAP`, `STRUCT`, a parameterized container like
`array<object>`, or a SQL array suffix like `integer[]` — **must** map to
**`Json`**, **never a scalar** like `Utf8`. The canonical is a *claim about
the shape* of the data: `Utf8` asserts an opaque string and throws the
structure away, so it is wrong for a JSON / array / struct column even when
the driver happens to hand the value over as text on the wire.

`Json` is the **only** container canonical a read map can render. The shape
markers `Object` / `List` need a sibling `properties` / `items` sub-schema
that a string→string rule cannot carry, and the typed angle-bracket forms
(`List<…>`, `Struct<…>`, `Map<…>`) are not contract grammar at all — the
vocabulary is trimmed to the executable set (issue #81), so a rule that
renders one now fails validation at author time instead of freezing a
canonical that would die at schema construction.

<!-- PROBE: read-map-native-semantics-unchecked -->
> **Only the syntactic half is enforced** (ADV-TMAP-001/002). The contract
> flags a native whose *shape* is visibly a container — angle brackets
> (`array<object>`) or a `[]` suffix (`integer[]`). A bare vendor spelling is
> deliberately not special-cased, so `{"native": "JSONB", "canonical": "Utf8"}`
> validates **clean**. That is the common case and the one you have to get
> right yourself.

| Native (read) | Canonical |
|---|---|
| `JSON`, `JSONB` (Postgres, MySQL/MariaDB) | `Json` |
| `VARIANT`, `OBJECT`, `ARRAY`, `MAP` (Snowflake) | `Json` |
| `array`, `object` (document stores) | `Json` |
| `integer[]` and other `…[]` array suffixes | `Json` |

`XML` is structured text, not a JSON/array/struct container, so it maps to
`Utf8` — the rule covers JSON / array / struct / map containers only.

On the write side the `Json` canonical renders the system's JSON column
type (`Json` → `JSONB` for postgres, `JSON` for MySQL, `VARIANT` for
Snowflake), so the type round-trips.

The shape markers `Object` and `List` split by direction:

- **Read maps never render them** (see above — no rule can carry the
  sibling sub-schema they require). The endpoint walker accepts a field
  typed `Object` or `List` as a valid narrowing of a `Json` read-map
  rule; the validator does not treat that as a mismatch.
- **Write maps must cover them.** The engine renders every destination
  column's frozen `arrow_type` through the write map **verbatim**
  (`shared/type-maps.md`), and endpoint documents legitimately carry the
  bare markers — an API source's struct field arrives at a database
  destination as the literal canonical `Object`, an array field as
  `List`. A write map without rules for them hard-errors the stream at
  configuration time. Render both exactly like `Json`:

  <!-- validate: type-map-write -->
  ```json
  [
    { "match": "exact", "canonical": "Object", "native": "JSONB" },
    { "match": "exact", "canonical": "List",   "native": "JSONB" }
  ]
  ```

  Author these as `exact` rules over the bare markers — do **not** widen
  them to regexes over angle-bracket forms (`^(?:Struct<.+>|Object)$`,
  `^(?:Large)?List(?:<.+>)?$`). Those spellings are outside the canonical
  vocabulary entirely (issue #81) — no endpoint or read rule can produce
  them — so such branches are dead pattern surface that misleads the
  next author.

**No `Map` canonical exists.** The vocabulary has no `Map` family
(issue #81 trimmed it): a `Map<…>` spelling fails validation as a read
render or an exact write canonical, and as a write regex matcher it can
never match a canonical that syncs. Never author `Map` rules; map-shaped
natives resolve to `Json` like every other structured container.

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
  native (or, for APIs, the sample value) actually carries a zone.
- **A boolean spelled as a narrow numeric** — some systems have no boolean
  type and document a width-1 integer as their boolean (MySQL's `TINYINT(1)`).
  Map the documented boolean spelling to `Boolean`, and keep the general
  numeric native mapping to its integer canonical. Follow the provider's
  documentation, not the type name: only map a numeric to `Boolean` where the
  docs say that spelling *is* the boolean.

## API coverage (read map)

<!-- PROBE: endpoint-pair-unresolved-through-read-map -->
For API connectors, the validator walks every endpoint file under
`{connector_id}/definition/endpoints/`, collects every `(native_type,
arrow_type)` pair from typed fields, and asserts each one resolves
through `type-map-read.json` (after normalizing the native). Resolution
renders the matched rule's `canonical` (substituting any `${name}`
captures from the regex match) and compares the result to the endpoint
field's `arrow_type`. A pair that does not resolve is a validation
error.

`Object` / `List` endpoint markers are accepted narrowings of `Json` —
an endpoint field with `arrow_type: "Object"` paired with a native that
maps to `Json` is **not** a mismatch.

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

API connectors ship **no write map** — the write direction is a
database-package concept (DDL rendering).

## Database coverage

**Read map:** ship the documented provider native vocabulary.

- For OLTP databases (PostgreSQL, MySQL), include the full documented
  native vocabulary.
- For warehouses and document stores (Snowflake, MongoDB), restrict to
  the researched, documented list — provider docs are authoritative.
- Do NOT ship a wildcard fallback rule. If a native type isn't covered,
  let the runtime hard-error so the gap is visible.
- Use `Utf8` (not `String`) for Arrow's UTF-8 string type — `String` is
  not a member of the published Arrow vocabulary.
- Capture declared precision on parameterized natives — never collapse it
  to a constant. The fixed default belongs only on the unparameterized
  fallback rule.
  - **Decimal:** regex `(precision, scale)` into named captures and route
    by Arrow width — precision ≤ 38 → `Decimal128(${precision},
    ${scale})`, 39–76 → `Decimal256(...)`. A precision-only declaration
    (`NUMERIC(p)`, implicit scale 0) needs its own tier rendering
    `Decimal{128,256}(${precision}, 0)`. Precision > 76 exceeds Arrow, so
    leave it uncovered (visible hard-error, per the no-wildcard rule
    above); the bare/unparameterized native takes the fixed default.
  - **Timestamp/time:** the native carries a fractional-second *digit
    count*, but Arrow's unit is a symbolic enum — so ladder the digit
    count to the smallest unit that holds it exactly: `(0)`→`SECOND`,
    `(1–3)`→`MILLISECOND`, `(4–6)`→`MICROSECOND`, `(7–9)`→`NANOSECOND`,
    with the bare form rendering the system's documented default unit.
    A single flat unit truncates any system finer than it — this is a
    per-system ladder, not a constant. Time-of-day picks the Arrow width
    off the same unit: `Time32(SECOND|MILLISECOND)` for coarse,
    `Time64(MICROSECOND|NANOSECOND)` for fine.

**Write map:** cover the **full executable canonical vocabulary** — every
Arrow type a source can actually hand this system needs a rendering (the
angle-bracket nested families are dead grammar and get no rules — see
"Canonical types"), including the parameterized families (Decimal via a
regex with `${p}`/`${s}` captures), both the bare and tz-aware `Timestamp`
forms, and the bare container markers `Object` / `List` (see "Schemaless /
JSON-shaped natives" — API sources hand them over as literal canonicals).

Run the validator and reconcile every family its `type-map-write-coverage`
warning names. A gap is legitimate **only** when the connector's dialect takes
over that family's rendering via a `render_column_type` override (BigQuery
ships no Decimal rule because NUMERIC/BIGNUMERIC selection needs
precision-range arithmetic rules cannot express), never as a way to cut scope.

<!-- PROBE: write-coverage-sample-gap -->
**A clean warning is not proof of coverage.** The check probes a representative
sample, so whole families go unexercised — a map missing all of these still
passes. Verify by hand at least:

- `FixedSizeBinary`
- `Time32` (only `Time64` is probed)
- **tz-aware** `Timestamp` — easy to miss, because the bare `Timestamp` probe
  passes without it
- `Decimal256` (only `Decimal128` is probed, so a map whose Decimal rule is
  narrowed to `Decimal128` shows nothing)

Treat that as the floor rather than the whole set: rarer scalars are
unprobed too.

Mind precision survival on the write side: MySQL's write map renders
`DATETIME(6)` / `TIME(6)` so microseconds survive the round trip — a
bare `DATETIME` silently truncates.

## Canonical types

Arrow canonical types are fully-qualified PascalCase strings from the
shared Arrow vocabulary — bare names where the type has no parameters
(`Int32`, `Int64`, `Float64`, `Utf8`, `Boolean`, `Binary`, `Date32`),
parens for parameterized scalars (`Decimal128(p, s)`,
`Decimal256(p, s)`, `Timestamp(MICROSECOND, UTC)`, `Time64(MICROSECOND)`,
`FixedSizeBinary(16)`), and the bare authored-shape container markers
(`Object`, `List`, `Json`).

There are no angle-bracket nested forms: the vocabulary is generated from
the engine-published grammar manifest and carries exactly the families the
engine executes end-to-end (issue #81). Nested data goes through the
authored-shape path only — `Object` / `List` with a sub-schema on the
owning document, opaque `Json`.

The full vocabulary is `schemas/canonical-types.json`, published at
[`https://schemas.analitiq.ai/canonical-types.json`](https://schemas.analitiq.ai/canonical-types.json)
— the readable reference when you need a family's exact spelling. Note the flat
path: unlike the connector and endpoint schemas there is no `/latest.json`
variant. Validation never fetches it; the enforced form is `ARROW_TYPE_PATTERN`
in `analitiq.contracts.endpoints`, matched offline.

For parameterized canonicals whose database native carries an implicit
default, encode the default explicitly:

- Snowflake `TIMESTAMP_NTZ` → `Timestamp(NANOSECOND)` (precision 9).
- Snowflake `NUMBER` → `Decimal128(38, 0)`.
- MongoDB `date` → `Timestamp(MILLISECOND, UTC)` (ms epoch UTC).
- MongoDB `decimal` → `Decimal128(34, 0)` (IEEE 754 decimal128).

Do NOT emit a bare parameterized name from an `exact` rule
(`{"match": "exact", "native": "TIMESTAMP_NTZ", "canonical": "Timestamp"}`
is wrong — `Timestamp` requires a unit).

## Worked example: Postgres (read)

See the reference read map, `examples/postgresql/type-map-read.json` —
uppercase patterns, the width-tiered `NUMERIC`/`DECIMAL` captures
(`Decimal128` ≤ 38, `Decimal256` above, plus precision-only `(p)`→scale-0
tiers, over a bare fallback), the timestamp precision ladder (digit
count → Arrow unit, there instantiated to Postgres's 0–6 range), and a
`JSONB` column mapped to the `Json` container canonical (not a scalar).

## Worked example: Postgres (write)

See the reference write map, `examples/postgresql/type-map-write.json` —
`canonical` is the matcher (note the regexes over the canonical string
with lowercase capture names), and `native` is the rendered DDL.

First-match-wins applies per file: more specific rules come **before**
broader fallbacks. In that file the bare `^Timestamp\([A-Z]+\)$` rule
sits before the tz rule yet cannot swallow a two-argument canonical —
but a genuinely overlapping family rule must be ordered carefully.

## Out of scope

Connection-scoped type maps are out of scope for this plugin; see
`shared/type-maps.md` for runtime resolution rules.
