# Error classification

`error_map` (`RULE-CTOR-058`) is connector-level and additive: absence means
"no declared mapping," and current engine behavior applies. It has independent
mechanisms — `http` for a status read at the API call site, and
`key_attrs`/`codes` for a native code read off the connector's own caught
exception (database connectors today; the contract types the mechanism
kind-agnostic). Declare only from grounded facts; an ungrounded category is a
config error the moment it turns out wrong, not a harmless guess
(`RULE-CTOR-026`).

## Contents

- Classifying an HTTP status
- Classifying a driver exception (`key_attrs` / `codes`)
- Operational consequence

## Classifying an HTTP status

No status code is classified from general HTTP knowledge, however familiar
its number looks — the same status means different things across providers
(a `409` is a stale read on one API and a rejected duplicate write on
another), and `http` applies at *any* HTTP call site (a read, a discovery
probe, an auth exchange, or a write — `io-contracts.md`'s `ProviderFacts.http`
description), so a status's meaning is never separable from what a specific
provider's docs say it means on the call where it actually occurs. Ground
every entry in `provider_facts.documented_http_errors` — never fabricate one
the docs don't establish, and never carry an entry over from another
connector.

Apply this procedure to what the docs say about each status, not to the
status number itself:

| What the provider's docs establish about this status | Category |
|---|---|
| Credentials or permissions are invalid or insufficient | `auth` |
| The client is being throttled, or has exceeded a rate or quota limit | `rate_limited` |
| The request the connector itself constructed is malformed or misconfigured | `config` |
| A specific write was rejected on its own content (failed validation, conflicts with existing data) — and the docs establish this outcome as write-specific, never shared with a read or discovery call | `write_rejected` |
| A transient, provider-side condition a retry can resolve | `transient` |
| The provider or an upstream dependency is unreachable | `unreachable` |

A status whose documented meaning doesn't clearly fit one row — or that the
docs show occurring across multiple call types with a meaning you cannot
pin to one row — is left unclassified; record the gap in `notes` on the
returned `CreatorOutput` rather than guessing.

`connector-spec-api/examples/api-key/api-key.example.json`'s `error_map.http`
block illustrates the shape for a fictional provider, not a set of universal
meanings. Ground every entry of an actual connector's `error_map.http` in
that connector's own provider's documented behavior (via
`provider_facts.documented_http_errors`), never by copying this or any other
connector's values.

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
   which wins: the entries are tried most-specific first, and the first one
   whose resolved value has a matching `codes` entry wins. An attribute that
   is present but whose value has no `codes` entry falls through to the next
   `key_attrs` entry exactly as if it had been absent, so an attribute ahead
   of the class-name fallback never makes that fallback unreachable — it
   only takes precedence when it actually classifies something. Choose and
   justify the order; there is no separate precedence field.

`codes` keys are compared as literal strings — the contract defines no prefix
or class-level wildcard, so a family of related codes (every value a class of
SQL exception can take, say) is enumerated one key per value, never
represented by a shared prefix. `io-contracts.md`'s `ProviderFacts` fragment
owns the omit-vs-null discipline `error_signals` carries.

`connector-spec-db/examples/postgresql-adbc/postgresql-adbc.example.json`'s
`error_map` block illustrates the shape — real, PostgreSQL-documented
SQLSTATE meanings, mapped to categories directly in this reference rather
than produced by a research pass. It is illustrative, not authoritative:
ground every entry of an actual connector's `error_map` in that connector's
own driver's documented facts (via `provider_facts.error_signals`), never by
copying this or any other connector's values.

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
