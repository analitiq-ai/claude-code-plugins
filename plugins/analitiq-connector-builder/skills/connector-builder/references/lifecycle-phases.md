# Lifecycle phases

Which values exist when, so a transport or operation only references values
that can actually resolve at the point it runs.

<!-- BEGIN GENERATED: claim:phase-resolvability-unchecked -->
> **This is entirely author-side.** No validator checks phase
> resolvability: a transport referencing `connection.discovered.api_domain`
> with no post-auth output producing it validates clean and fails at
> connect. What a ref must name is the scope it starts from; whether
> anything ever puts a value at the rest of the path is not knowable
> from the document, on either the connector or the endpoint side. Walk
> the phases by hand.
<!-- END GENERATED: claim:phase-resolvability-unchecked -->

## Phases

| Phase | Available scopes | Used by |
|---|---|---|
| `pre_auth` | `connection.parameters.*` | Inputs the user submits before auth (host, port, region, tenant slug, …). Transports for pre-auth discovery may run here. |
| `auth` | `pre_auth` scopes + `secrets.*`, `runtime.oauth.*` | Auth operations (`authorize`, `token_exchange`, `refresh`). |
| `post_auth` | `auth` scopes + `auth.*` | Post-auth discovery requests, `options_request`, `discovery_request`. |
| `active` | `post_auth` scopes + `connection.selections.*`, `connection.discovered.*`, `stream.*` | Endpoint operations. |

A later phase may use any earlier phase's scopes.

## Resolvability rule (`RULE-CTOR-050`)

For every transport's references, compute the union of scopes used and
resolve it against the table above. If a transport references
`connection.discovered.api_domain`, it cannot be the `default_transport`
for an operation that runs in `auth` or earlier.

## Example: a value that arrives after auth

A provider issues an access token, then exposes the account's own settings at a
stable endpoint. Reading those settings needs `auth.access_token`, so that
request cannot run in `pre_auth` — it is declared as a post-auth
`discovery_request`, and the value it produces lands at
`connection.discovered.<key>` for later phases to reference.

Declare a separate transport for the discovery request itself, which needs only
`auth` scopes.

A discovered value can be templated straight into the data transport's
`base_url` (see `connector-spec-api/spec-transport.md`), which is the usual
shape for a per-tenant host.

## The failure this prevents

The mirror image of the resolvability rule is declaring an input's `phase` too
late for the transport that needs it (`RULE-CTOR-050`, from the declaration
side: a `base_url` component declared `phase: "auth"` cannot serve a pre-auth
request).

<!-- PROBE: connector-ref-tail-unchecked -->
Validation does not catch it. Before returning a connector, trace each
transport's refs to the declaration that produces them and confirm the
producing phase is no later than the consuming one.

## Runtime OAuth scope

For `auth.type: "oauth2_authorization_code"` only. The `runtime.oauth.*` fields
are engine-supplied and the table below closes the set — a path outside it
validates and resolves to nothing (`RULE-SHRD-008`). Per-operation availability:

| Field(s) | Available in |
|---|---|
| `state`, `redirect_uri` | `auth.authorize` and `auth.token_exchange` |
| `code_challenge`, `code_challenge_method` | `auth.authorize` only |
| `code`, `pkce_verifier` | `auth.token_exchange` only |

Reference each field only where the table places it (`RULE-CTOR-051`) — in
particular the PKCE **verifier, which must never appear in the authorize
request**; only the derived `code_challenge` rides the browser-facing
authorize. `auth.refresh` references none of them: refresh runs after the
authorization-code workflow that produced them has completed.
