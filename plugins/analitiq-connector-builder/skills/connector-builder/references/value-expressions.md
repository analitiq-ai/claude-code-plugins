# Value expressions

Shared invariant both creator agents must follow. The scopes below are owned by
`analitiq.contracts.value_expression`, the expression shapes by
`analitiq.contracts.endpoints` and the function forms by
`analitiq.contracts.connector`; this page is the authoring guide to them.

## Expression kinds

A value expression is one of:

| Kind | Shape | Use |
|---|---|---|
| `ref` | `{"ref": "<dotted.path>"}` | Resolve a single value from a runtime scope. |
| `template` | `{"template": "literal-with-${scope.path}-interpolation"}` | Build a string by interpolating one or more refs into a literal. |
| `literal` | `{"literal": <any>}` | A constant value (string, number, boolean, object, array). |
| `function` | `{"function": "<name>", "input": {...}}` | Call a registered function with named inputs. |

Anywhere the schema accepts a value expression, exactly one of the shapes
above is allowed. (Endpoint request slots additionally admit the binding
forms `from_param` / `from_input`; the exactly-one-key rule there is
RULE-ENDP-022 — see `connector-spec-api/spec-request-binding.md`.)

<!-- PROBE: pagination-limit-literal-rejected, pagination-offset-step-literal-rejected, pagination-offset-step-bare-accepted, pagination-page-step-literal-rejected -->
A slot may admit a narrower set. A numeric pagination slot (`limit.default`,
`offset.increment_by`, `page.increment_by`) takes a bare positive integer or a
shape the engine resolves per request — `literal` is refused there, because a
statically-known page size or step IS the bare integer and only that spelling
carries the bound. See `connector-spec-api/spec-pagination.md`.

## Logical scopes

Every `ref` and every `${...}` interpolation inside a `template` must lead with
one of the contract's resolution scopes — on a connector `RULE-CTOR-057`, on an
endpoint `RULE-ENDP-049` for template placeholders and `RULE-ENDP-033` for
request-slot refs. The table below is the authoring guide to the ones a
connector or endpoint actually writes.

<!-- PROBE: runtime-tail-unchecked -->
| Scope | Holds |
|---|---|
| `secrets.*` | User-entered or platform-injected secret values, opaque references. |
| `connection.parameters.*` | Non-secret user/platform values declared in `connection_contract.inputs` with `storage: "connection.parameters"`. |
| `connection.selections.*` | Durable user choices declared as `post_auth_outputs` with `storage: "connection.selections"`. |
| `connection.discovered.*` | Auto-discovered non-secret context (e.g. `api_domain`) declared as `post_auth_outputs` with `storage: "connection.discovered"`. |
| `auth.*` | Auth tokens (access_token, refresh_token, expiry). |
| `runtime.*` | Per-run values (`RULE-SHRD-008`): `runtime.batch_size` (the run's configured page size — use it for a pagination `limit.default`) and the OAuth set in `lifecycle-phases.md`. The scope accepts any path, so an invented one (`runtime.run_id`) validates clean and fails at resolution — don't guess names. |
| `response.*` | The response being processed — `response.body.*`, `response.headers.*`. This is what pagination `stop_when` / `next_cursor` and `response.metadata` refs target. |
| `request.*` | The request being built. |
| `stream.*` | Stream-owned routing, tenant context, stream-specific auth context. |
| `state.*` | Replication watermarks and other carried-over run state. |
| `connector.*` | Connector-level declared values. |

When each connector-lifecycle scope becomes available is `lifecycle-phases.md`
§Phases (`RULE-CTOR-050`). `request.*` and `response.*` exist only while an
endpoint operation builds its request and processes its response; `state.*` and
`connector.*` are available throughout a run.

<!-- BEGIN GENERATED: scope-guarantees -->
**Scope checking is two-tier on an endpoint.** Every expression slot on an
operation is swept — `pagination` (every `stop_when` operand included),
`response.metadata`, the `request` `path_params` / `headers` / `query` / `body`
slots, and each `params.<name>.default`. For most scopes only the **leading
token** is checked, so `connection.discovered.nope` passes and resolves empty.

What is actually proved, by sub-scope. Everything is spelling-checked (a bad
sub-scope like `response.bodyy` is always an error); only the first two rows
are resolved against something declared — `response.body` paths only on a
read, `response.metadata` keys on either operation:

| Ref | Read op | Write op |
|---|---|---|
| `response.body.<path>` | resolved against `response.schema`, must declare a type (RULE-ENDP-023) | **not resolved** — see below |
| `response.metadata.<key>` | must name a declared key | must name a declared key |
| `response.records.<path>` | spelling only | spelling only |
| `response.headers.<name>` | spelling only | spelling only |
| `response.status` | spelling only | spelling only |
| `response.record_count` | spelling only | **barred** — read-only scope |

So `response.body.nope` in a read pagination block is an error rather than a
silent one-page sync — but `response.records.next_cursor` and
`response.headers.X-Made-Up` are not, and cannot be: headers, status and
record counts are runtime values this document declares nothing about, and the
`response.records` **scope** is not the `response.records` **field**. (That
field — the `{ref: response.body.<path>}` selecting the record collection — IS
resolved, and must land on an array node, RULE-ENDP-012. Referencing
`response.records.<something>` from a pagination or metadata expression is a
different thing and is unchecked.)

**A write mode has no `response.schema`**, so nothing under
`operations.write.<mode>.response` is path-resolved at all: a typo in
`success_when`, `affected_records` or `error.*` validates clean. That is the
worst cell in the table — a `success_when` predicate over a ref that resolves
to nothing holds unconditionally, so every write reports success, including
the ones whose rejected rows the provider listed. Trace write response refs
against the provider's real payload yourself.

Wherever it appears, a `response.*` ref in a request slot or a param `default`
is refused outright, whatever it names and on either operation: the request is
built before the response exists, so it could only interpolate to nothing.

In a **connector** document (a transport header, an auth template) there is no
check at all — treat every ref there as unverified and trace it to the
declaration that produces it yourself.
<!-- END GENERATED: scope-guarantees -->

(`request.path_params` takes bindings, not raw refs: `{from_param: …}` on a
read, and on a write also `{from_input: "record.<dotted>"}` — see
`connector-spec-api/spec-request-binding.md`.)

<!-- PROBE: request-slot-direct-runtime-ref -->
> **`stream.*`, `state.*` and `runtime.*` are barred from endpoint request
> slots** (`RULE-ENDP-032`) — see `connector-spec-api/spec-request-binding.md`.

<!-- PROBE: auth-state-tail-unchecked -->
Paths that *look* like scopes but are not, and so fail at runtime after passing
validation (`RULE-SHRD-008`; the leading token `connection` is legal, the rest is
not): `connection.auth_state.*` and `connection.secret_refs.*`.

## Function catalog (`RULE-SHRD-007`)

The registered functions, as `analitiq.contracts.connector.DerivedValue` models
them:

- `basic_auth` — build a Basic credential/header from `username` + `password` (or client-credentials) inputs.
- `base64_encode` — base64-encode a string/bytes value for provider auth formats.
- `lookup` — map an input value through a connector-declared inline `map`, returning the mapped value.
- `url_encode` — percent-encode a scalar for a URL component. Escapes every reserved character by default (`safe: ""`); pass a `safe` field to widen the unescaped set.

<!-- PROBE: connector-lookup-map-unvalidated -->
**`lookup` maps must be total** (`RULE-CTOR-053`). Nothing validates it:
enumerate the mapped input's `enum` and give every member a key before returning
the connector.

**Never apply a wire-encoding function inside a DSN binding** (`RULE-CTOR-054`)
— the double encoding yields a well-formed DSN carrying a credential the server
rejects, which reads as a bad secret rather than a bad binding. `url_encode` is
for URL components you build yourself in a `template`.

**Planned — NOT yet registered; do not reference** (`RULE-SHRD-007`):
`jwt_sign` (sign a JWT from key/algorithm/claims) and `pkce_challenge_s256`
(derive a PKCE S256 challenge from a runtime verifier). This includes the
inline-signing path for `jwt` auth.

<!-- BEGIN GENERATED: claim:function-names-unchecked -->
**Nothing validates the function name.** An unregistered name (including
`jwt_sign`) passes every check and fails only when the engine tries to
resolve it at connect time — on a connector document and on an endpoint
alike. Treat the catalog above as closed and verify by hand; the
validator will not catch a typo or a planned-but-unregistered function.
To extend the catalog, the engine's function registry must be updated
first.
<!-- END GENERATED: claim:function-names-unchecked -->

## DSN placeholders are not value expressions

Inside `dsn.template`, `{placeholder}` markers are NOT `${...}` value
expressions. They resolve through `dsn.bindings`, where each binding
declares a `value` (a value expression) and an `encoding` (closed enum) —
see `connector-spec-db/spec-dsn-bindings.md`.
