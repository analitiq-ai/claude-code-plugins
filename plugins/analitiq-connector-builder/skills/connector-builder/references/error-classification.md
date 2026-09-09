# Error classification

`error_map` (`RULE-CTOR-058`) is connector-level and additive: absence means
"no declared mapping," and current engine behavior applies. It has independent
mechanisms — `http` for a status read at the API call site, and
`key_attrs`/`codes` for a native code read off the connector's own caught
exception (database connectors today; the contract types the mechanism
kind-agnostic). Declare only from grounded facts; an ungrounded category is a
config error the moment it turns out wrong, not a harmless guess
(`RULE-CTOR-026`).

## Classifying an HTTP status

HTTP status semantics are close to universal, so classify straight from this
table rather than researching each provider from scratch. A provider that
documents different semantics for a status below is the exception, not the
rule — record the deviation in `notes` on the returned `CreatorOutput` rather
than trusting this table over the provider's own docs.

| HTTP status | Category |
|---|---|
| `401` | `auth` |
| `403` | `auth` |
| `429` | `rate_limited` |
| `400` | `config` |
| `409` | `write_rejected` |
| `422` | `write_rejected` |
| `500` | `transient` |
| `502` | `unreachable` |
| `503` | `unreachable` |
| `504` | `unreachable` |

## Classifying a driver exception (`key_attrs` / `codes`)

Ground this half in the driver's own documentation via
`provider_facts.error_signals` — never fabricate a code the docs don't
establish. `key_attrs` and `codes` are declared together or not at all
(`RULE-CTOR-067`).

1. **Does the driver's exception expose its native code as a plain
   attribute** (a SQLSTATE, an `errno`, a vendor code)? Name that attribute in
   `key_attrs`.
2. **Does classification instead depend on the exception's own type**, not a
   value it carries? Use the reserved name `__exception_class__` in
   `key_attrs` — it matches the exception's class name up its MRO instead of
   reading an attribute.
3. **Declaring more than one signal** — an attribute read together with the
   class-name fallback, say — means `key_attrs`'s own declared order decides
   which wins: the contract reads the entries most-specific first, and the
   first one that resolves a value wins. Choose and justify that order; there
   is no separate precedence field.

`codes` keys are compared as literal strings — the contract defines no prefix
or class-level wildcard, so a family of related codes (every value a class of
SQL exception can take, say) is enumerated one key per value, never
represented by a shared prefix. See
`connector-spec-db/examples/postgresql-adbc/postgresql-adbc.example.json`'s
`error_map` block for a grounded worked example, and `io-contracts.md`'s
`ProviderFacts` fragment for the omit-vs-null discipline `error_signals`
carries.

## Operational consequence

No engine cookbook classifies a given native code into a category — that
judgment is left entirely to whoever authors the connector. The verdict below
is a reading of `analitiq-core`'s engine (its capability-declaration module,
`DECLARED_WRITE_VERDICTS` / `DECLARED_READ_DETERMINISTIC`) — neither side pins
this fact to the other, so re-verify against the engine before relying on the
exact wording:

| Verdict | Category |
|---|---|
| Retryable, read and write | `transient` |
| Retryable, read and write | `unreachable` |
| Retryable, read and write | `rate_limited` |
| Fatal, config-defect | `auth` |
| Fatal, config-defect | `config` |
| Fatal, write-rejected | `write_rejected` |
