# The connection envelope

A connection document carries its values in the maps the field table in
`SKILL.md` declares, each keyed by a connection-contract input or
post-auth-output name. There is no single `values` object — the plugin routes
each key into the right map itself, driven entirely by the connector's contract.
Omit any map that would be empty (`RULE-SHRD-004`).

## Contents

- Routing rule (the whole thing)
- Type fidelity
- TLS verification needs its CA material (`RULE-CONN-008`)
- Secrets — reference, never embed (`RULE-CONN-009`)

## Routing rule (the whole thing)

Read the downloaded connector's `connection_contract` and route each
`inputs.<key>` and `post_auth_outputs.<key>` by its `storage` (`RULE-CONN-006`).
One rule covers every connector and auth type — there are no auth-type branches,
and nothing here changes when a new connector ships:

- `connection.parameters` → `parameters.<key>` = the user's value.
- `secrets` → `secret_refs.<key>` = a secret **pointer** (see below); the value
  goes in `.secrets/`, never the document.
- `connection.selections` → `selections.<key>` **only if** the user supplies it
  up front; usually omit (a post-auth selection isn't known at authoring time).
- `connection.discovered` → **never author** (`RULE-CONN-005`); the connections
  API rejects a client-supplied value.

### `selections` vs. `discovered`

The two are easy to conflate and the distinction is *who chose the value*, not
when it arrived. Use `selections` **only** for a durable user choice — the user
was shown options after auth and picked one (a warehouse, a workspace, an
account, a sync target), and that choice must survive re-auth. Use `discovered`
for provider-returned context that is durable and connection-specific but that
the user never entered or picked (a tenant id the provider assigned, an
account's region). A value the user typed at authoring time is a `parameter`,
not a selection.

## Type fidelity

Each contract input declares a JSON `type` (`RULE-CONN-007`) — `port: 5432`
(integer), not `"5432"`. The plugin does not coerce; read the type from the
connector or ask the user. Optional inputs (`required: false`) may be omitted
(`RULE-SHRD-004`).

## TLS verification needs its CA material (`RULE-CONN-008`)

A certificate-verification mode is one from the connector's declared `ssl_mode`
enum that verifies the server certificate against a CA, whatever the connector's
vocabulary names it (`verify-ca`/`verify-full` are the libpq-shaped example);
the CA material is the contract's own input for it (`ssl_ca_certificate` in the
connectors that declare one). The mode vocabulary is connector-defined: judge
each enum value by what it does, not by these example spellings, and when a
mode's meaning is not evident from the connector's contract, ask the user rather
than guessing whether it verifies. A driver may silently fall back to the host's
trust store, and verifying against whatever CAs happen to be installed is not
the mode the user asked for — that fallback makes a misconfigured connection
look healthy. If the user selects a verifying mode without supplying the
certificate, ask for it rather than authoring the mode alone. The CA input
routes like any other input — by its declared `storage`, which is normally
`secrets`, so it becomes a pointer in `secret_refs`.

## Secrets — reference, never embed (`RULE-CONN-009`)

The real value goes in a gitignored `.secrets/credentials.json` the user
provisions; the plugin never holds or logs one.

Author an **`env:` pointer** by default — portable and resolved from the runtime
environment. `examples/db.example.json` is the worked document: one pointer per
secret input, beside the `parameters` the same contract routes.

Env-var name: `ANALITIQ_<connection-slug>_<key>`, upper-cased, every
non-alphanumeric replaced with `_`. That composition is a **plugin convention**,
not a contract fact — the contract constrains only the `env:` grammar in the
scheme list below, and any name matching it is legal. Keep the convention unless
the user names their own variable. Emit the sibling template the user fills in:

<!-- illustrative -->
```jsonc
// .secrets/credentials.json
{
  "ANALITIQ_POSTGRESQL_PASSWORD": "<paste-password-here>",
  "ANALITIQ_POSTGRESQL_SSL_CA_CERTIFICATE": "<paste-ca-pem-here>"
}
```

The user (or CI) exports these into the environment where the pipeline runs (or
loads them into their secret store) before submission; nothing on the
connection-authoring path reads `.secrets/` — only `private-endpoint-creator`
does, as the fallback when an `env:` variable is unset.

The file's shape is the published credentials-sidecar contract
(`https://schemas.analitiq.ai/credentials/latest.json`): a flat map that
constrains no key, which is why the env-var-keyed template above conforms to it.
Write string values: the engine string-coerces on read, so JSON-encode a
structured credential into a string rather than authoring a nested object.

### `.secrets/credentials.json` is not a `sidecar:` file

The two look alike and resolve completely differently. Do not conflate them:

- The plugin's template is keyed by **env-var name** and paired with `env:`
  pointers. The user resolves it by exporting those variables; no `secret_refs`
  scheme resolves against this file, and the plugin never writes over one the
  user has already filled in. This pairing is a plugin convention.
- <!-- PROBE: connection-sidecar-name-unconstrained -->
  The `sidecar:<name>` scheme names an entry in a credentials file the engine
  reads directly, keyed by the **connection-contract input name** — the same
  `<name>` that keys `secret_refs`, not an env-var name. The `<name>` part
  constrains nothing after the prefix, so a wrong name validates cleanly and
  fails only at resolution time.

So a `sidecar:` pointer against the env-keyed template is never right
(`RULE-CONN-010`): it would look up `password` in a file whose only key is
`ANALITIQ_…_PASSWORD`. Emit `sidecar:` pointers only when the user asks for that
store, and then key the file by the contract input names.

Substitute the user's pointer verbatim if they ask for a specific store. Which
resolver runs for a given scheme is **engine-owned** — the contract declares the
scheme and nothing more. Author the pointer; never author, infer, or promise
resolution behavior (lookup order, caching, rotation, failure mode) on the
strength of the scheme name.

<!-- BEGIN GENERATED: secret-ref-grammar -->
Every `secret_refs` value must carry an explicit scheme — a bare token (a pasted raw secret) is rejected by the contract.

Accepted schemes (`analitiq.contracts.connection.SECRET_REF_VALUE_PATTERN`):

- `env:[A-Za-z_][A-Za-z0-9_]*`
- `file:[A-Za-z0-9_.][A-Za-z0-9_./\-]*`
- `s3://[A-Za-z0-9._\-]+/[A-Za-z0-9_./\-]+`
- `arn:aws:secretsmanager:[A-Za-z0-9\-]+:\d+:secret:[A-Za-z0-9/_\-+=.@]+`
- `arn:aws:ssm:[A-Za-z0-9\-]+:\d+:parameter/[A-Za-z0-9_./\-]+`
- `ssm:/[A-Za-z0-9_./\-]+`
- `sidecar:.+`
<!-- END GENERATED: secret-ref-grammar -->
