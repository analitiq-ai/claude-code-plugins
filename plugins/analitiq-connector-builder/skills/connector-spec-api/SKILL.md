---
name: connector-spec-api
description: API connector authoring vocabulary — auth flows, HTTP transports, pagination, replication, post-auth discovery. Loaded by api-connector-creator only. Not invoked directly by users.
user-invocable: false
---

# connector-spec-api

This skill is loaded by `api-connector-creator` when authoring an API
connector. It carries the API-specific vocabulary and examples needed to
populate `transports`, `auth`, `connection_contract`, and
`resource_discovery` for `kind: "api"`, plus the standalone
`type-map-read.json` shipped alongside the connector. An API connector's
release carries no connector-package Python files (`RULE-CTOR-043`).

## Required reading (load on demand)

Pick what you need for the auth and pagination styles you're authoring:

- This skill's `spec-auth-flows.md` (for the chosen `auth.type`)
- This skill's `spec-transport.md` (for HTTP transport idioms)
- This skill's `spec-request-binding.md` (how `params` reach a `request` —
  read this before authoring any endpoint request)
- This skill's `spec-filters.md` (which fields a stream may filter on, and how
  each operator is spelled on the wire)
- This skill's `spec-pagination.md` (for endpoint pagination)
- This skill's `spec-replication.md` (for incremental sync)
- `connector-spec-db/spec-type-maps.md` for authoring the standalone
  `type-map-read.json` (same rule shape for API and DB; API ships the
  read direction only)
- `connector-builder/references/value-expressions.md` §Function catalog (the
  registered functions, and the ones documented as planned)
- `connector-builder/references/lifecycle-phases.md` (for `post_auth_outputs`
  with `options_request` / `discovery_request`; for `headers_remove` on an
  inheriting transport see `spec-transport.md`)
- The closest auth archetype under `examples/<name>/` (`api-key`,
  `oauth2-authorization-code`, `jwt`) — each a `<name>.example.json` connector
  body with a sibling `type-map-read.json` and `endpoints/`.
  `spec-auth-flows.md` carries a section per API auth type (`db` is
  `connector-spec-db`'s); only the diverse archetypes ship a full example dir

## Endpoint `operations` shape (cross-reference)

Endpoint authoring lives in the `endpoint-creator` agent, and the shape of
`operations` — which keys exist, which are required, which combinations are
legal — is owned by the published api-endpoint contract. It is not restated
here.

What to read instead:

- `spec-request-binding.md` — how `params` reach a `request` (the part most
  likely to fail validation).
- `spec-filters.md` — declaring which record fields a stream may filter on, and
  where each operator lands.
- `spec-pagination.md` / `spec-replication.md` — choosing and wiring those
  blocks.
- `connector-builder/references/rules/connector.md` — every rule binding the
  connector document, citable by id. Satisfy all of them.
- `connector-builder/references/rules/api-endpoint.md` — the same, for each
  endpoint document this skill produces.

## What this skill does NOT cover

- DSN URL templates, bindings, or encoding enums (that's `connector-spec-db`).
- `tls` block (that's `connector-spec-db`).
- Database `resource_discovery` (DB-specific shape).
- Type-map file shape and authoring rules (`connector-spec-db/spec-type-maps.md`).
