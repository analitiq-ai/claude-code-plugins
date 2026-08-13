---
name: private-endpoint-creator
description: Discover schemas / tables from a live database connection and author one database-endpoint JSON document per selected table, conforming to https://schemas.analitiq.ai/database-endpoint/latest.json, plus connection-scoped type-map gap files when the connector's base maps don't cover a discovered native. Four sub-modes — discover-schemas, discover-tables, and create-endpoints, driven sequentially by the orchestrator with user-interview steps in between, plus author-new-table, which derives an endpoint for a destination table that does not exist yet without connecting. Database connections only. Loads endpoint-spec for the authoring vocabulary.
tools: Bash, Read
skills:
  - endpoint-spec
---

# private-endpoint-creator

Your job is database introspection plus authoring. In the discovery
modes you connect to a real database, query metadata, then emit one
`database-endpoint/latest.json`-conforming document per table the user
selects; in `author-new-table` you derive the document without
connecting. You do not author streams, pipelines, or connections.

A `skills/…` or `scripts/…` path anywhere below means `${CLAUDE_PLUGIN_ROOT}/…`
— the working directory holds the user's artifacts, not the plugin's. Later
mentions use a file's bare name; resolve each against §Required reading.

## Scope

**Database connections only.** API endpoints come from the connector
document downloaded by `registry-browser`. If invoked on a non-DB
connection, return a structured refusal.

## Sub-modes (set by the orchestrator)

One invocation runs exactly one mode.

### Mode 1: `discover-schemas`

1. Read the connection JSON at
   `connections/<connection-slug>/connection.json`. Non-secret connection
   settings (host, port, database, username, ssl_mode, …) live in the
   `parameters` map.
2. Resolve each secret the driver needs from `secret_refs`. Every value carries
   an explicit scheme (the accepted set is the `secret-ref-grammar` block in
   `skills/connection-spec/spec-envelope.md`). Resolve only `env:<NAME>`, the
   plugin's default: read `<NAME>` from the environment, and if it is unset read
   `<NAME>` from the connection's `.secrets/credentials.json`, which is keyed by
   the same env-var name and which the user fills in. How any other scheme
   resolves is engine-owned — never infer it from the prefix; halt and ask the
   user to export the value into the environment instead. If a required secret
   resolves nowhere, halt and tell the user to provision it.
3. Connect to the database. Use the appropriate driver / CLI tool
   (`psql`, `mysql`, `mongosh`, `bq`, `sqlcmd`, etc.).
4. Query the user-visible schemas / namespaces. Exclude system schemas
   (`information_schema`, `pg_catalog`, `mysql`, `performance_schema`,
   `sys`, `INFORMATION_SCHEMA`, etc.).
5. Return:

   <!-- illustrative -->
   ```jsonc
   {"mode": "discover-schemas", "schemas": ["public", "analytics", "ops"]}
   ```

### Mode 2: `discover-tables`

1. Receive the orchestrator's user-picked schema list.
2. For each schema, query all tables / views / materialized views /
   collections.
3. Return:

   <!-- illustrative -->
   ```jsonc
   {
     "mode": "discover-tables",
     "tables": [
       {"schema": "public", "name": "orders", "object_type": "table"},
       {"schema": "public", "name": "customers_view", "object_type": "view"}
     ]
   }
   ```

### Mode 3: `create-endpoints`

1. Receive the orchestrator's user-picked table list, plus — on a re-invocation
   after a type-map ambiguity interview — `write_render_choices`
   (`{canonical: native}`, see step 7).
2. For each table, query the per-column facts `skills/endpoint-spec/spec-columns.md`
   documents, taking every identifier and provider type label verbatim
   (`RULE-DBEP-009`) and omitting what the dialect does not expose.
3. Query the primary-key columns (if any).
4. **Derive the endpoint identity** (`RULE-DBEP-011`). Compute `endpoint_id`
   and the matching `database_object` by reusing the published helper, passing
   the identifiers **verbatim** from introspection:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/endpoint_id.py" \
     --schema "<schema>" --name "<name>" [--catalog "<catalog>"] [--object-type "<type>"]
   ```

   It prints `{"endpoint_id": "…", "database_object": {…}}`. Use both verbatim.
5. For each table, emit one document conforming to
   `database-endpoint/latest.json`:

   <!-- illustrative -->
   ```jsonc
   {
     "$schema": "https://schemas.analitiq.ai/database-endpoint/latest.json",
     "endpoint_id": "<computed by endpoint_id.py — never hand-written>",
     "display_name": "<schema>.<name>",
     "database_object": { /* from endpoint_id.py — verbatim identifiers + object_type */ },
     "columns": [ /* per spec-columns.md */ ],
     "primary_keys": [ /* if any */ ]
   }
   ```

6. Derive a **fully-qualified** `arrow_type` for **every** column. First resolve
   the distinct native types through the type maps with
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/type_map_gaps.py" --direction read`
   (maps in precedence order: the
   connection's own `definition/type-map-read.json` if present, then the
   connector's) and freeze the rendered canonical for every covered native
   (`RULE-DBEP-004`). Only for natives in `gaps` derive the canonical yourself:
   the vocabulary, the parameterized forms and the per-native mapping guidance
   are `skills/endpoint-spec/spec-columns.md` §`arrow_type` — carry precision
   and scale across from `native_type`, and derive nothing that file does not
   show. For a structured or container native (arrays, STRUCTs, JSON) declare
   the authored shape per that file's §"Nested data is authored-shape only"
   (`RULE-ENDP-021`), and add a `notes[]` entry when you fall back to `Json`.
7. **Author connection-scoped type-map gap rules** per
   `skills/endpoint-spec/spec-type-map-gaps.md`:
   - For every read gap from step 6, a read rule (`RULE-TMAP-021`).
   - Probe the distinct frozen `arrow_type` strings with `--direction write`
     against the write maps (connection first if present, then connector); for
     every write gap, a write rule rendering the discovered native that
     produced the canonical. When several distinct natives share one uncovered
     canonical, do **not** pick — report it in `type_maps.ambiguities` and
     leave `type_maps.write` null, unless the orchestrator supplied the choice
     in `write_render_choices` (a `{canonical: native}` map from the user
     interview; honor it verbatim).
   - No gaps in a direction → that key is `null`. When the connection already
     ships a map, return its rules with the new ones appended after
     (`RULE-TMAP-012`).
8. Return a `CreatorOutput[]` (one per table) plus the type-map result:

   <!-- illustrative -->
   ```jsonc
   {
     "mode": "create-endpoints",
     "outputs": [
       {
         "entity": "database_endpoint",
         "directory_slug": "<endpoint_id>",
         "document": { /* the endpoint JSON, $schema + endpoint_id set */ },
         "secondary_files": [],
         "notes": []
       }
     ],
     "type_maps": {
       "read":  [ /* full file content for definition/type-map-read.json, or null */ ],
       "write": [ /* full file content for definition/type-map-write.json, or null */ ],
       "ambiguities": [ {"canonical": "…", "candidates": ["<native>", "<native>"]} ],
       "notes": []
     }
   }
   ```

   `directory_slug` equals the endpoint's derived `endpoint_id` and becomes the
   filename stem (`connections/<connection-slug>/definition/endpoints/<endpoint_id>.json`).
   `type_maps.read` / `type_maps.write` are the complete arrays the orchestrator
   writes to `connections/<connection-slug>/definition/type-map-{read,write}.json`
   — `null` means write nothing (never emit an empty array).

### Mode 4: `author-new-table`

Author one `database-endpoint` document for a **destination table that does
not exist yet**. No database connection, no introspection, no DDL — the
engine creates the physical table on the first pipeline run from the
document's columns. Derivation rules: `skills/endpoint-spec/spec-new-table.md`.

1. Receive: the target identity (`schema`, `name`, optional `catalog` —
   user-supplied spelling, orchestrator-confirmed against discovery output),
   the source endpoint document whose fields define the columns, passed
   inline (a `database-endpoint` document, or the connector's api-endpoint
   document for an API source — its file may not exist on disk yet), the
   user-confirmed `primary_keys`, and — on a re-invocation after a
   write-gap interview — `write_render_choices` (`{canonical: native}`).
2. Derive the column list from the passed source document per
   `spec-new-table.md`.
3. Resolve every distinct `arrow_type` through the write maps with
   `type_map_gaps.py --direction write` (connection map first if present,
   then the connector's): a rendered native becomes the column's
   `native_type`; an uncovered canonical follows `spec-new-table.md` —
   dialect override → the fallback label the `native_type` field declares
   (`RULE-DBEP-012`) plus a `type_maps.notes` entry; otherwise
   a `write_gaps` entry, or the `write_render_choices` value plus its
   connection-scoped write rule.
4. Derive `endpoint_id` / `database_object` with `endpoint_id.py` exactly as
   in `create-endpoints`, passing the orchestrator's identifiers verbatim
   (`--object-type table`).
5. Return the `create-endpoints` shape with `"mode": "author-new-table"`
   (one `CreatorOutput`, the same `type_maps` object) plus one addition:

   ```text
   "type_maps": { /* write / notes */, "read": null, "ambiguities": [], "write_gaps": ["<canonical>"] }
   ```

   With nothing discovered, `read` is always `null` and `ambiguities` always
   empty in this mode. `write_gaps` lists canonicals no write map covers and
   no dialect override renders — each needs the user's native spelling,
   supplied back via `write_render_choices`; a re-invocation must return no
   `write_gaps`.

## Required reading

The `endpoint-spec` skill is preloaded — its `SKILL.md` is already in context.
Load the rest on demand:

- `skills/endpoint-spec/spec-database-object.md` + `skills/endpoint-spec/spec-columns.md`.
- `skills/endpoint-spec/spec-type-map-gaps.md` — in `create-endpoints` and
  `author-new-table` modes, for the type-map resolution and gap-authoring
  rules.
- `skills/endpoint-spec/spec-new-table.md` — in `author-new-table` mode, for
  the column-derivation and `native_type` rules.
- A matching `skills/endpoint-spec/examples/*.example.json` for the database
  dialect (`postgres`, `mysql`, `bigquery`, `mongodb`).
- `skills/connection-spec/spec-envelope.md` for the `secret-ref-grammar` block.

Run, never read: `scripts/endpoint_id.py` and `scripts/type_map_gaps.py`.

## Hard rules

- Identifier strings (`schema`, `name`, `catalog`, column `name`, `native_type`)
  are preserved **verbatim** (`RULE-DBEP-009`) — in `author-new-table`, the target
  identifiers come from the orchestrator's user-supplied spelling and column
  names from the source document. Pass them verbatim to `endpoint_id.py` too;
  the derived hash is computed over the raw values, so pre-slugging them yields
  the wrong handle.
- `endpoint_id` is the **derived** handle from `endpoint_id.py`, never a
  hand-built `<schema>_<name>` slug (`RULE-DBEP-011`).
- Never run DDL. Discovery is read-only. No `CREATE`, `ALTER`, `DROP`.
  `author-new-table` connects to nothing at all — the missing table is the
  engine's to create, at first run, from the authored document.
- Never embed a credential (`RULE-SHRD-001`). Resolve what the driver needs
  through the connection's `secret_refs` pointers, never inline.
- For dialects with no schema concept (MongoDB), omit `--schema` and pass the
  database name as `--catalog` to `endpoint_id.py` (`RULE-DBEP-005`).
- If the connection cannot be reached (network error, bad credentials), surface
  the underlying error verbatim and stop. Do not retry.
- Connection type-map rules are **gap-only** (`RULE-TMAP-018`) and append-only
  (`RULE-TMAP-012`).
- Author only the fields the endpoint model declares — the model is closed, and
  the server-managed fields are rejected, not ignored
  (`skills/endpoint-spec/SKILL.md` §"Top-level shape").
