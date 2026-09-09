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
another), and `error_map.http` is read at the HTTP call site, independent of
`key_attrs`/`codes` (the contract's own `ErrorMap.http` description). In
`analitiq-core`'s engine, that call site is every read and write HTTP
request — the same classification path serves both — never a
discovery/health-check probe, which bypasses `error_map` entirely, and never
a distinct auth-exchange call, since credentials ride on the read/write
request itself rather than a separate handshake: an auth failure is
classified only if and when it surfaces as a status on an actual read or
write. This is a reading of the engine as it stands, not a contract
guarantee — re-verify against the engine before relying on the exact scope.
So a status's meaning is never separable from what a specific provider's
docs say it means on the call where it actually occurs. Ground every entry
in `provider_facts.documented_http_errors` — never fabricate one the docs
don't establish, and never carry an entry over from another connector.

Apply this procedure to what the docs say about each status, not to the
status number itself:

| What the provider's docs establish about this status | Category |
|---|---|
| Credentials or permissions are invalid or insufficient | `auth` |
| The client is being throttled, or has exceeded a quota the docs document as resetting or lifting during normal operation | `rate_limited` |
| The request the connector itself constructed is malformed or misconfigured | `config` |
| A specific write was rejected on its own content (failed validation, conflicts with existing data) — and the docs establish this outcome as write-specific, never shared with a read call (a discovery-only conflict doesn't apply — `error_map.http` never fires for a discovery/health-check probe, above) | `write_rejected` |
| A transient, provider-side condition a retry can resolve | `transient` |
| The provider or an upstream dependency is unreachable | `unreachable` |

A status whose documented meaning doesn't clearly fit one row is left
unclassified — this includes a quota the docs describe as terminal within a
run (no documented reset short of a plan change or a billing-cycle
rollover), since `rate_limited` is read by the engine as retryable (below)
and a terminal quota is not. So is a status the docs document *differently*
across the calls `error_map.http` actually classifies — a read and a write
(above); a conflict involving *only* a discovery/health-check probe doesn't
apply, since that call never reaches `error_map`. `error_map.http` carries
no per-endpoint scoping, so a status with conflicting documented meanings
across read and write cannot be classified without silently applying the
wrong one at whichever call site the other meaning belonged to. In every
case, record the gap — or the conflicting meanings — in `notes` on the
returned `CreatorOutput` rather than guessing or picking one arbitrarily.

`connector-spec-api/examples/api-key/api-key.example.json`'s `error_map.http`
block illustrates the shape for a fictional provider, not a set of universal
meanings. Ground every entry of an actual connector's `error_map.http` in
that connector's own provider's documented behavior (via
`provider_facts.documented_http_errors`), never by copying this or any other
connector's values.

## Classifying a driver exception (`key_attrs` / `codes`)

Ground this half in whichever source actually documents each fact —
the driver's own docs for how it exposes a signal, and, for what a
documented code VALUE means, the driver's docs or the database/server's own
published catalog (a SQLSTATE table, a vendor error-code reference) when the
server, not the driver, owns that meaning — via `provider_facts.error_signals`.
Never fabricate a code the docs don't establish. `key_attrs` and `codes` are
declared together or not at all (`RULE-CTOR-067`).

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
4. **Choosing each entry's category** applies the same procedure the HTTP
   table above teaches, against `error_signals.documented_codes`' recorded
   meaning instead of a status — see the table below.

| What the recorded meaning establishes | Category |
|---|---|
| Credentials or permissions are invalid or insufficient | `auth` |
| The client is being throttled, or has exceeded a quota the docs document as resetting or lifting during normal operation | `rate_limited` |
| The statement or connection the connector itself constructed is malformed, or the connector lacks a required setting | `config` |
| The write's own content was rejected (a constraint violation, a failed validation) | `write_rejected` |
| A transient condition a retry can resolve (a serialization failure, a deadlock) | `transient` |
| The server, or a dependency it needs, is unreachable | `unreachable` |

A meaning that doesn't clearly fit one row is left unclassified — this
includes a quota the docs describe as terminal within a run (no documented
reset short of a plan change or a billing-cycle rollover), for the same
reason as the HTTP table above. Omit that entry from `codes` and record the
gap in `notes`, never guess.

A connector may ship more than one driver across its transports
(`connector-spec-db/spec-driver-selection.md`); when two drivers document
*different* meanings for what would resolve to the same `codes` key — most
often a shared exception class name under `__exception_class__`, since
drivers from different vendors can happen to name a class `OperationalError`
— `error_map` carries no per-driver scoping, so installing one entry
silently misclassifies whichever driver's exception the entry wasn't
grounded from. Omit that entry and record the conflicting per-driver
meanings in `notes`, exactly as a per-call HTTP conflict is refused above,
rather than picking one driver's meaning arbitrarily.

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
own driver's and server's documented facts (via `provider_facts.error_signals`),
never by copying this or any other connector's values.

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
