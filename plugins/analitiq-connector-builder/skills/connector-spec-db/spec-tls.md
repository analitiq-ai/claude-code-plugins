# TLS declarations

How `sqlalchemy` database transports declare TLS intent without
embedding driver-specific objects. The generic `tls` block is
**SQLAlchemy-only**; for `adbc` transports, TLS lives inside
`db_kwargs` (e.g. `adbc.postgresql.sslmode`, `adbc.postgresql.sslrootcert`)
— see `spec-dsn-bindings.md`.

<!-- BEGIN GENERATED: claim:tls-coherence-unchecked -->
> **Nothing validates TLS coherence.** The contract's TLS block is
> deliberately vocabulary-agnostic — it enforces no mode set and does not
> check that a verification mode has a CA certificate to verify against.
> Every rule below is author-side discipline; a connector that declares
> `verify-full` with no `ssl_ca_certificate` input validates clean and
> fails at connect. Apply the checklist by hand, for both SQLAlchemy and
> ADBC shapes (they resolve through the same `connection_contract.inputs`).
<!-- END GENERATED: claim:tls-coherence-unchecked -->

## Shape

See the `transports.database.tls` block in
`examples/postgresql/postgresql.example.json` — the only reference example
carrying a `tls` block (`mode` + `ca_certificate` as `ref` expressions).

## Rules

- `tls.mode` is a value expression drawn from the connector's own
  ssl-mode vocabulary (`RULE-CTOR-047`; see below — the vocabulary is
  connector-defined). In practice it should `ref` the canonical input
  `connection.parameters.ssl_mode`.
- `tls.ca_certificate` is a value expression that resolves to a
  PEM-encoded CA bundle. It should `ref` the canonical secret
  `secrets.ssl_ca_certificate`.
- <!-- PROBE: tls-coherence-unchecked -->
  `RULE-CTOR-029` binds whenever the `ssl_mode` enum admits a mode that
  verifies the server certificate against a CA, whatever the driver names
  it. Nothing checks it — verify it yourself.
- Keep driver-specific TLS material out of connector JSON
  (`RULE-CTOR-063`). The runtime resolves the generic declaration and hands
  it to the connector package's dialect, which converts it into
  driver-specific connect arguments.
- A `tls` block obligates the connector package's dialect
  (`RULE-PKG-021`; see `spec-connector-package.md` §Dialect hooks). The
  engine has no built-in TLS interpretation for any driver, so a connector
  that declares `tls` without a dialect TLS hook fails loudly at connect.

## SSL mode vocabulary is connector-defined — researched, never copied

The `ssl_mode` vocabulary belongs to the connector: declare the
system's native mode names in the `connection_contract.inputs.ssl_mode`
enum, and interpret them in the connector package's dialect via the
TLS hook (see `spec-connector-package.md`). The declared set and the set
the dialect interprets must be the same (`RULE-PKG-029`). Users see the
vocabulary their database's own docs use; no translation table ships
anywhere.

The vocabulary is established at author time from the researcher's
grounded facts (`ProviderFacts.tls.supported_modes` — the mode values
the driver's official docs name, verbatim; `RULE-CTOR-026`). Even
wire-compatible systems ship drivers with different TLS surfaces: one
driver may take a many-mode libpq-style string, another only a boolean
toggle plus a narrow set of certificate-verification modes, spread
across several connect parameters. Declare exactly what the driver
documents — no more, no fewer. If the researcher reported `tls` as
null (driver docs ambiguous), that is a gap to surface to the user,
not a license to borrow a vocabulary.

The dialect maps each declared mode to whatever the driver's connect
API takes — a pass-through mode string, a boolean toggle, or an
`SSLContext` built with `cdk.transport_factory.ca_ssl_context` when a
CA bundle is supplied. Which hook carries it — a single connect argument
or the full connect-args mapping — is `spec-connector-package.md`
§Dialect hooks; whichever it is must satisfy `RULE-PKG-022`.

## Authoring checklist

<!-- PROBE: tls-coherence-unchecked -->
Re-verify every rule on this page before returning — no validator checks
any of them.
