---
name: endpoint-spec
description: Database endpoint authoring vocabulary — database_object identity, columns with native_type and Arrow type, primary_keys, connection-scoped type-map gap authoring, and new-table (pending-creation) derivation. Loaded by private-endpoint-creator only. Not invoked directly by users.
user-invocable: false
---

# endpoint-spec

This skill is loaded by `private-endpoint-creator` when authoring a
database endpoint document conforming to the published database-endpoint
contract (`analitiq.contracts.endpoints.DatabaseEndpointDoc`).

## Contents

- Required reading (load on demand)
- Scope
- What this skill covers
- Top-level shape
- What this skill does NOT cover
- Output rules

## Required reading (load on demand)

- `../pipeline-builder/references/rules/database-endpoint.md` — **read before
  authoring**. Every rule binding the document you are about to write, with a
  **Checked** column saying which ones the validator will not catch for you.
- `../pipeline-builder/references/rules/type-map.md` — the same, for the
  type-map gap files this skill authors (`spec-type-map-gaps.md`).

- `spec-database-object.md` — catalog/schema/name/object_type rules; no
  identifier normalization.
- `spec-columns.md` — the column shape, provider `native_type` labels and
  the fully-qualified Apache Arrow `arrow_type` vocabulary.
- `spec-type-map-gaps.md` — connection-scoped type maps for discovered
  natives the connector's base maps don't cover: gap detection in each
  direction, and the authoring rules.
- `spec-new-table.md` — deriving a destination endpoint for a table that
  does not exist yet (no introspection; the engine creates it on first run).
- At least one of `examples/*.example.json` for the database dialect
  you're authoring.

## Scope

API endpoints come from the connector document, not from here. This
skill is **database-only**. API endpoints in stream `endpoint_ref`s use
`scope: connector` and point at the connector's `definition/endpoints/`.
Database endpoints use `scope: connection`; their file location is in the table
at the foot of this file.

## What this skill covers

- The structural identity of a database object: catalog, schema, name,
  object_type.
- The column shape per table/view/collection.
- Primary keys: every declared name must name a column this document declares
  (`RULE-DBEP-003`).
- Connection-scoped type maps
  (`connections/<connection-slug>/definition/type-map-{read,write}.json`),
  authored only when discovery surfaces natives the connector's maps don't
  cover — see `spec-type-map-gaps.md`.
- Endpoints for destination tables that do not exist yet, derived from the
  source endpoint instead of introspection — see `spec-new-table.md`.

## Top-level shape

<!-- BEGIN GENERATED: fields-database-endpoint -->
`analitiq.contracts.endpoints.DatabaseEndpointDoc` — closed (`additionalProperties: false`); required: `$schema`, `columns`, `database_object`, `endpoint_id`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `endpoint_id` | **yes** | string | — | `pattern=^[a-z0-9][a-z0-9_-]*$`, `minLength=1` |
| `display_name` | no | string \| null | `None` | `pattern=^\S(?:[\s\S]*\S)?$`, `minLength=1`, `maxLength=120` |
| `description` | no | string \| null | `None` | `maxLength=2000` |
| `tags` | no | array of string \| null | `None` | `maxItems=50`, `item pattern=^\S(?:[\s\S]*\S)?$`, `item minLength=1` |
| `$schema` | **yes** | const 'https://schemas.analitiq.ai/database-endpoint/latest.json' | — | — |
| `database_object` | **yes** | DatabaseObject | — | — |
| `columns` | **yes** | array of Column | — | `minItems=1` |
| `primary_keys` | no | array of string \| null | `None` | `minItems=1` |
<!-- END GENERATED: fields-database-endpoint -->

Server-managed fields are never authored — the published model is the
**authored** shape, not the persisted one, and it names every field you may
write. `schema_hash` is the usual mistake.

## What this skill does NOT cover

- The connection that owns this endpoint — see `connection-spec`.
- Stream-level concerns (filters, replication, pagination, mapping) —
  those belong to `stream-spec`.
- Discovery mechanics (how to query `information_schema` etc.) — that's
  agent logic, encoded in `private-endpoint-creator`.

## Output rules

Every authored document must:

1. Declare `$schema` with the database-endpoint URL from the table below
   (`RULE-SHRD-003`).
2. Carry every required field from the top-level shape table above.
   `endpoint_id` is the **derived** handle computed by `scripts/endpoint_id.py`,
   never a hand-authored slug — see `spec-database-object.md`
   §Derived `endpoint_id`.
3. Preserve identifier strings verbatim from introspection (`RULE-DBEP-009`).
4. Pass validation (the `pipeline-schema-validator`, entity `database_endpoint`)
   with zero error findings. The `endpoint_id` must equal the handle the
   contract's derivation produces from the verbatim `database_object`
   (`RULE-DBEP-011`).

<!-- BEGIN GENERATED: schema-urls -->
| Entity | Authored file | `$schema` value |
|---|---|---|
| Pipeline | `pipelines/<slug>/pipeline.json` | `https://schemas.analitiq.ai/pipeline/latest.json` |
| Stream | `pipelines/<slug>/streams/<stream-slug>.json` | `https://schemas.analitiq.ai/stream/latest.json` |
| Connection | `connections/<slug>/connection.json` | `https://schemas.analitiq.ai/connection/latest.json` |
| Database endpoint | `connections/<slug>/definition/endpoints/<endpoint_id>.json` | `https://schemas.analitiq.ai/database-endpoint/latest.json` |
<!-- END GENERATED: schema-urls -->
