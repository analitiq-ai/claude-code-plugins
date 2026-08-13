# API auth flows

Per-auth-type authoring craft. `auth` is a discriminated union on `type`
(`RULE-CTOR-024`), so take a branch's field set from
`https://schemas.analitiq.ai/connector/latest.json`, not from this page. What
each section below carries is the craft that shape cannot express, and a
pointer to a worked example under `examples/` where one ships.

## `api_key`

The API key value itself lives in `connection_contract.inputs`
(`RULE-CTOR-052`) with `secret: true`. Auth header construction happens in the
transport's `headers` block, e.g.
`"Authorization": { "template": "Bearer ${secrets.api_key}" }`.

Example: `examples/api-key/api-key.example.json` (with sibling
`examples/api-key/type-map-read.json`). For a templated / post-auth-discovered
host, see the multi-origin transport pattern in `spec-transport.md`.

## `basic_auth`

Declare `username` and `password` in `connection_contract.inputs`
(`RULE-CTOR-052`), the password with `secret: true`. The `Authorization` header
in the transport should use the `basic_auth` function expression — never
pre-compute base64.

<!-- validate: connector#/transports/api/headers/Authorization -->
```json
"Authorization": {
  "function": "basic_auth",
  "input": {
    "username": { "ref": "connection.parameters.username" },
    "password": { "ref": "secrets.password" }
  }
}
```

## `oauth2_authorization_code`

`authorize` describes the URL that will be opened in the user's browser
(method usually `GET`); `token_exchange` describes the back-channel
request that swaps the auth code for tokens. Both are
`AuthOperationTemplate` objects (`RULE-CTOR-025`).

`client_id` typically lives in `connection.parameters` with
`source: "platform"`; `client_secret` lives in `secrets` with
`source: "platform"` and `secret: true`.

**Don't invent *user-facing* form inputs for an OAuth connector.** The redirect
flow collects the user's authorization through the browser, so declare a
`source: "user"` input only when the provider genuinely needs a value *before*
the authorize URL can be built (a region, or a tenant slug in the authorize
host). Asking the user for anything the redirect already yields is noise.

This does not mean an empty `inputs` map: every ref an auth template resolves
must be declared there (`RULE-CTOR-052`), and the app's own `client_id` /
`client_secret` still are, as `source: "platform"` (above).
<!-- PROBE: connector-ref-tail-unchecked -->
Nothing validates that a ref resolves, so dropping them leaves a connector that
passes validation and fails at connect with no credentials.

**Platform-owned vs user-owned OAuth apps differ only in `source`.** Whether
your platform registers one app for everyone (`source: "platform"`) or each
user brings their own (`source: "user"`), the storage paths and every auth
template stay identical — flip `source` and change nothing else.

**Refresh timing is not yours to declare.** Author the `refresh` template and
stop: when to refresh, how expiry is tracked, and retry behaviour are engine
concerns. There is no place to encode a TTL or a refresh policy.

Example: `examples/oauth2-authorization-code/oauth2-authorization-code.example.json`
(multi-origin provider with post-auth discovery; sibling
`examples/oauth2-authorization-code/type-map-read.json`).

## `oauth2_client_credentials`

Used for machine-to-machine auth. The `token_exchange` request POSTs
client credentials and gets an access token (no browser redirect):

<!-- validate: connector#/auth -->
```json
"auth": {
  "type": "oauth2_client_credentials",
  "token_exchange": {
    "transport_ref": "auth",
    "method": "POST",
    "path": "/oauth/token",
    "headers": { "Content-Type": "application/x-www-form-urlencoded" },
    "body": {
      "template": "grant_type=client_credentials&client_id=${connection.parameters.client_id}&client_secret=${secrets.client_secret}"
    }
  }
}
```

## `jwt`

Declare the signing key, algorithm, and claim inputs in
`connection_contract.inputs`, and set the transport's `Authorization` header
from the minted token with the `Bearer ${auth.access_token}` template —
`examples/jwt/jwt.example.json` shows both.

> **A `jwt` connector may only call a signing function the catalog registers**
> (`connector-builder/references/value-expressions.md` §Function catalog,
> `RULE-SHRD-007`). Where it registers none, declare the inputs, author no
> signing call, and flag the capability gap before shipping a `jwt` connector
> that depends on local signing.

## `credentials`

Declare the credential bundle in `connection_contract.inputs`
(`RULE-CTOR-052`), flagging each secret member `secret: true`.

## `aws_iam`

Declare the user-supplied values in `connection_contract.inputs`
(`RULE-CTOR-052`). There is no signing block to author: the connector document
declares intent only.

## `none`

For public APIs that require no authentication. Rare.
