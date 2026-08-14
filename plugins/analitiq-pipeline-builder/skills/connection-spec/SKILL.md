---
name: connection-spec
description: Connection authoring vocabulary — the parameters/selections/discovered/secret_refs envelope, storage-driven routing, and the `.secrets/` template workflow. Loaded by connection-creator only. Not invoked directly by users.
user-invocable: false
---

# connection-spec

This skill is loaded by `connection-creator` when authoring a connection
document: one user's configured **instance** of a connector.

## Contents

- Required reading (load on demand)
- What this skill covers
- Registered rules for a connection
- What this skill does NOT cover
- Draft connections, and what a clean validation does not prove
- Output rules

## Required reading (load on demand)

- `spec-envelope.md` — the author-time maps
  (`parameters`/`selections`/`discovered`/`secret_refs`), the storage-driven
  routing rule, and the `env:` secret-pointer + `.secrets/` template workflow.
- The closest `examples/*.example.json` for the connector's `auth.type` (shape
  guidance only; the connector contract's `storage` is authoritative for
  routing, so the example set never needs to grow when a new connector ships).

## What this skill covers

Authoring the connection document: every field the table below declares, and
nothing else. Parts of it are not yours to write — `discovered` is filled by the
service from the connector's post-auth discovery (`RULE-CONN-005`), and a
`selections` value the user has not already chosen is only known after auth.

The authoring jobs that implies: routing each `connection_contract` input /
post-auth output into its map (`RULE-CONN-006`; see `spec-envelope.md`), and
scaffolding `secret_refs` as `env:` pointers with the matching
`.secrets/credentials.json` template.

The artifacts that merely hang off a connection are separate documents:
endpoints, discovery artifacts and connection-scoped type maps are authored by
`private-endpoint-creator` (see `endpoint-spec`, incl. `spec-type-map-gaps.md`),
never here.

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

Every rule binding a connection document is in
`../pipeline-builder/references/rules/connection.md` — **read it before
authoring**, satisfy every row, and cite one by id rather than restating it. A rule
whose statement names the connector's `connection_contract` is judged against
that document, so read the downloaded connector beside the connection while
authoring.


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
— a secret not yet provisioned, a post-auth step not yet run. What must resolve
first is the connector's `connection_contract.required_for_activation`. Author
the honest draft; never invent a placeholder value to force a connection into
looking activatable. Draft-vs-active is service-side state, not something this
document carries.

Provider reachability is out of scope: a connection with zero findings can still
fail at runtime on credentials, network, permissions or a missing resource.
Report it as structurally valid, never as "working" or "tested".

## Output rules

Every authored document must:

1. Declare `$schema` with the connection URL from the table below
   (`RULE-SHRD-003`).
2. Author `connector_id` — the slug of the downloaded connector being
   instantiated (`RULE-CONN-011`). Author `connection_id` from the UUID the
   orchestrator minted, or omit it (the service assigns one on ingest).
3. Route every contract input/output by its `storage` (`RULE-CONN-006`;
   procedure in `spec-envelope.md`). For each key the contract routes to secret
   storage, write an `env:` pointer into `secret_refs` and add the env-var name
   to `.secrets/credentials.json`.
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
