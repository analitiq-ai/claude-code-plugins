# HTTP transport idioms

Authoring patterns for `transports` in API connectors.

## Single-origin

The simplest case: one `base_url`, one transport, one set of common headers.
See `examples/api-key/api-key.example.json` — a single `api` transport with a
literal `base_url`, common headers (including the templated `Authorization`),
`timeout_seconds`, and a `rate_limit`.

## Multi-origin

When a provider exposes auth, discovery, and data on different origins
(e.g. separate `oauth.` / `api.` hosts), define one transport per origin
and factor common headers into `transport_defaults`.

See `examples/oauth2-authorization-code/oauth2-authorization-code.example.json`
— `transport_defaults` carries the shared headers (including the Bearer
`Authorization`), with `auth` / `discovery` / `api` transports, one per origin.
Its `auth` transport overrides the inherited Bearer `Authorization` with
Basic auth.

## Templated `base_url`

A host that varies per connection belongs on the transport itself: `base_url`
accepts a value expression, resolved when the connection is materialized
(`https://schemas.analitiq.ai/connector/latest.json`, `#/$defs/HttpTransport`).

A region or subdomain the user supplies before auth:

<!-- validate: connector#/transports/api/base_url -->
```json
"base_url": { "template": "https://${connection.parameters.region}.example.com" }
```

The matching `region` input must be declared in `connection_contract.inputs`
with `phase: "pre_auth"` (`RULE-CTOR-050`), so the template resolves before auth.

Credentials never go in the URL (`RULE-CTOR-066`), in any form — a literal
`https://user:pass@host` or a template that builds one out of inputs. The
client derives Basic auth from the URL, so such a connector authenticates its
first request and then silently loses the credentials on the first
provider-supplied continuation link, which carries none; and the URL is logged
wherever URLs are. Put them in the transport's `auth` block or its
`Authorization` header.

A per-tenant host discovered *after* auth: see the `api` transport in
`examples/oauth2-authorization-code/oauth2-authorization-code.example.json`,
whose `base_url` templates `${connection.discovered.api_domain}` into the host.

That value comes from a `post_auth_outputs` entry, so the transport is only
usable once post-auth discovery has run — it cannot serve an `auth`-phase
operation (`RULE-CTOR-050`). Declare a separate transport for the discovery
request itself (see `connector-builder/references/lifecycle-phases.md`).

Do not put the host in an operation's `request.path` (`RULE-ENDP-045`) — the
path is resolved against the selected transport's `base_url`, and a host baked
into the path bypasses the transport that owns it.

## Header resolution order

Effective headers per request are built as:

1. Resolved `transport_defaults.headers`.
2. Merge resolved `transports.<ref>.headers`.
3. Remove inherited names listed in operation `headers_remove`.
4. Merge resolved operation `headers`.

Header names match case-insensitively for override and removal.

Removal is governed by `RULE-SHRD-010` and `RULE-HTTP-001`. An endpoint
operation request removes an inherited default the same way a transport does —
that is how one endpoint drops an auth header a public sub-resource rejects.

`Content-Length` and `Content-Type` never enter any of these maps.
`Content-Length` describes a body the engine has not built yet
(`RULE-HTTP-002`), and `Content-Type` describes one request's body, so it
belongs to the operation that carries it — declare it as that request's
`content_type` (`RULE-HTTP-003`). `Accept` is the contrast: it says what the
client will take back, whatever the request carries.
