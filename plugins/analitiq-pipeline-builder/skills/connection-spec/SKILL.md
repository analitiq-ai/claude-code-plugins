---
name: connection-spec
description: Connection authoring vocabulary — the parameters/selections/discovered/secret_refs envelope, storage-driven routing, and the `.secrets/` template workflow. Loaded by connection-creator only. Not invoked directly by users.
disable-model-invocation: true
---

# connection-spec

This skill is loaded by `connection-creator` when authoring a connection
document: one user's configured **instance** of a connector.

## Required reading (load on demand)

- `spec-envelope.md` — the author-time maps
  (`parameters`/`selections`/`discovered`/`secret_refs`), the storage-driven
  routing rule, and the `env:` secret-pointer + `.secrets/` template workflow.
- The closest `examples/*.example.json` for the connector's `auth.type` (shape
  guidance only; the connector contract's `storage` is authoritative for
  routing, so the example set never needs to grow when a new connector ships).

## What this skill covers

A connection owns exactly this much, and nothing else:

- user-entered non-secret values;
- user-selected post-auth values that must persist;
- provider-discovered post-auth values (server-managed);
- secret references;
- auth lifecycle metadata (server-managed);
- connection-level private endpoints, discovery artifacts, and
  connection-scoped type maps.

Plus the authoring jobs those imply: routing each `connection_contract` input /
post-auth output into its map (`RULE-CONN-006`; see `spec-envelope.md`), and
scaffolding `secret_refs` as `env:` pointers with the matching
`.secrets/credentials.json` template.

Only what the field table below declares is authorable here. The server-managed
parts of that list — `discovered` (`RULE-CONN-005`) and auth lifecycle metadata —
belong to the service, and the endpoint / discovery / type-map artifacts are
separate documents that merely hang off this connection — endpoints and
connection-scoped type maps are authored by `private-endpoint-creator`
(see `endpoint-spec`, incl. `spec-type-map-gaps.md`), never here.

<!-- BEGIN GENERATED: fields-connection -->
`analitiq.contracts.connection.ConnectionInput` — closed (`additionalProperties: false`); required: `connector_id`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `parameters` | no | object | — | — |
| `selections` | no | object | — | — |
| `discovered` | no | object | — | — |
| `secret_refs` | no | map of string | — | — |
| `$schema` | no | const 'https://schemas.analitiq.ai/connection/latest.json' \| null | `None` | — |
| `display_name` | no | string \| null | `None` | `pattern=^\S(?:[\s\S]*\S)?$`, `minLength=1`, `maxLength=120` |
| `description` | no | string \| null | `None` | `maxLength=2000` |
| `connector_id` | **yes** | string | — | `minLength=1` |
| `tags` | no | array of string \| null | `None` | `maxItems=50`, `item pattern=^\S(?:[\s\S]*\S)?$`, `item minLength=1` |
| `connection_id` | no | string \| null | `None` | `pattern=^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` |
<!-- END GENERATED: fields-connection -->

## Registered rules for a connection

Satisfy every rule below, and cite one by id rather than restating it. A clean
validation run is especially weak proof here: a connection is judged against the
*connector's* `connection_contract`, which a single-document run never opens, so
most of these surface at connect rather than at authoring time.

<!-- BEGIN GENERATED: rules-connection -->
| Rule | Constraint |
|---|---|
| `RULE-CONN-004` | A connection's non-secret maps MUST NOT carry a key that names credential material; such a value lives in secret storage and is reached through `secret_refs`. |
| `RULE-CONN-005` | A connection MUST NOT author a value into its `discovered` map, which the connections service owns and fills from the connector's post-auth discovery. |
| `RULE-CONN-006` | Every connection-contract input and post-auth output MUST be authored into the connection map its own declaration's `storage` names. |
| `RULE-CONN-007` | A connection MUST author each value in the JSON type its connection-contract input declares, uncoerced, and within that input's declared allowed-value list where it declares one. |
| `RULE-CONN-008` | A connection selecting a TLS mode that verifies the server certificate MUST also supply the CA material the connector declares an input for. |
| `RULE-CONN-009` | A connection MUST NOT carry a secret value; an input the connector routes to secret storage is authored as a pointer in `secret_refs`. |
| `RULE-CONN-010` | A `sidecar:` pointer MUST name an entry in a credentials file keyed by connection-contract input name, never a key of the env-var-keyed template the plugin emits. |
| `RULE-CONN-011` | A connection's `connector_id` MUST resolve to a connector present in the run the connection is assembled into. |
<!-- END GENERATED: rules-connection -->

## What this skill does NOT cover

- The connector's `connection_contract` itself — that lives in the connector
  document, authored by the `analitiq-connector-builder` plugin. A connection is
  an *instance* of a connector, never a second copy of it: never restate a
  connector-owned request template, transport rule, auth flow, pagination rule
  or provider quirk inside a connection. If a value is the same for every user
  of the connector, it belongs to the connector.
- Endpoint discovery — see `endpoint-spec`.

## Draft connections, and what a clean validation does not prove

A connection may be saved as a **draft** with its activation requirements unmet
— a secret not yet provisioned, a post-auth step not yet run. It becomes active
only once everything the contract marks `required_for_activation` resolves.
Author the honest draft; never invent a placeholder value to force a connection
into looking activatable. Draft-vs-active is service-side state, not something
this document carries — the field table declares no connection `status`, so
there is nothing to author either way.

Provider reachability is out of scope for connection validation: the validator
never contacts the provider. A connection with zero findings can still fail at
runtime on credentials, network, permissions or a missing resource. Report it as
structurally valid, never as "working" or "tested".

## Output rules

Every authored document must:

1. Declare `$schema` with the connection URL from the table below
   (`RULE-SHRD-003`).
2. Author `connector_id` (the connector slug being instantiated; required).
   `connection_id` is an RFC-4122 UUID — author one the plugin generates, or
   omit it and let the service assign one on ingest.
3. Route every contract input/output into `parameters` / `selections` /
   `secret_refs` by its `storage` (never author `discovered` —
   `RULE-CONN-005`). For each `storage: "secrets"` key, write an `env:` pointer
   into `secret_refs` and add the env-var name to `.secrets/credentials.json`.
4. Pass the validator (`pipeline-schema-validator`, entity `connection`) with
   zero error findings.

<!-- BEGIN GENERATED: schema-urls -->
| Entity | Authored file | `$schema` value |
|---|---|---|
| Pipeline | `pipelines/<slug>/pipeline.json` | `https://schemas.analitiq.ai/pipeline/latest.json` |
| Stream | `pipelines/<slug>/streams/<stream-slug>.json` | `https://schemas.analitiq.ai/stream/latest.json` |
| Connection | `connections/<slug>/connection.json` | `https://schemas.analitiq.ai/connection/latest.json` |
| Database endpoint | `connections/<slug>/definition/endpoints/<endpoint_id>.json` | `https://schemas.analitiq.ai/database-endpoint/latest.json` |
<!-- END GENERATED: schema-urls -->
