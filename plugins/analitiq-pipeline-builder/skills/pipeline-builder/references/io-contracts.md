# I/O contracts between orchestrator and agents

Every cross-agent payload is a JSON object matching one of the shapes
below. The orchestrator checks a payload against its shape before
dispatching the next phase.

## Contents

- `PipelineFacts` (output of `pipeline-provider-researcher`)
- `MintedIdentities` (orchestrator-local, phase 3)
- `CreatorOutput` (output of every creator agent)
- `Diagnostics` (output of `pipeline-schema-validator`)
- `DriftVerdict` (output of `pipeline-drift-classifier`)

## `PipelineFacts` (output of `pipeline-provider-researcher`)

Discriminated by each side's `kind`. Each kind has its own required
sub-shape. The closed vocabularies the shape below defers to are in
`enum-mappers.md`'s generated vocabulary table — read the members there
before filling one in; a value outside it is an error to surface, not a
guess to make.

<!-- illustrative -->
```jsonc
{
  "pipeline_slug": "wise_to_postgresql",        // directory name only; shape per the directory-slug convention (identity-and-versioning.md)
  "display_name": "Wise to PostgreSQL",
  "description": "…",
  "source": {
    "connector_id": "wise",                     // connector slug; resolves in DIP registry
    "connection_slug": "wise",                  // its package directory name
    "kind": "api",                              // "api" or "database"; a connector of any
                                                // other kind is refused, not recorded (RULE-CTOR-037)
    "selected_endpoints": ["transfers"],        // endpoint_id list; required
    "replication": {
      "method": "incremental",                  // vocabulary per enum-mappers.md
      "cursor_field": "updated_at"              // the incremental shape requires it (RULE-STRM-017)
    }
  },
  "destination": {
    "connector_id": "postgresql",
    "connection_slug": "postgresql",
    "kind": "database",                         // "api" | "database"
    "schema": "public",                         // database only
    "write": {
      "mode": "upsert",
      "conflict_keys": ["id"]                   // flat field names; the keyed write shape requires them (RULE-STRM-016)
    }
  },
  "schedule": {
    "type": "manual",                           // vocabulary per enum-mappers.md
    "timezone": "UTC"                           // IANA zone name (RULE-PIPE-015)
  },
  "engine_overrides": null,                     // pipeline `engine` sub-shape or null
  "runtime_overrides": null                     // pipeline `runtime` sub-shape or null
}
```

## `MintedIdentities` (orchestrator-local, phase 3)

After classification, the orchestrator generates UUIDs and bundles them
so creator agents can cross-reference consistently.

<!-- illustrative -->
```jsonc
{
  "pipeline_id": "11111111-1111-4111-8111-111111111111",
  "connections": {
    "source":      {"connection_id": "22222222-…", "connection_slug": "wise"},
    "destinations": [{"connection_id": "33333333-…", "connection_slug": "postgresql"}]
  },
  "streams": [
    {"stream_id": "44444444-…", "stream_slug": "transfers_to_warehouse", "endpoint_id": "transfers"}
  ]
}
```

Reused on-disk connections contribute their **existing** `connection_id`
UUID (read from the on-disk connection document) instead of a freshly
minted one.

## `CreatorOutput` (output of every creator agent)

Each creator agent returns the JSON it would write, plus optional notes.
The orchestrator handles disk I/O.

<!-- illustrative -->
```jsonc
{
  "entity": "pipeline",                       // "pipeline" | "stream" | "connection" | "database-endpoint"
  "directory_slug": "wise_to_postgresql",     // names the location: package directory or filename stem
  "document": { /* the authored JSON, $schema set, no server-managed fields */ },
  "secondary_files": [                        // optional — e.g., a credentials template
    {"path": "<the connection package's credentials location>", "content": { /* … */ }}
  ],
  "notes": []                                 // human-readable rationale / caveats
}
```

The identity UUID (`pipeline_id`, `stream_id`, `connection_id`) lives
inside `document`; the orchestrator reads it from there for downstream
cross-references. Endpoint creators carry the slug identity in
`document.endpoint_id`.

`private-endpoint-creator`'s sub-modes wrap their `CreatorOutput[]` in a
mode-level envelope (`{"mode", "outputs", …}`, plus `type_map` in
`create-endpoints` / `author-new-table`) — that envelope is defined in the
agent file itself, not here.

For unsupported cases (e.g., a connector kind the engine can't run —
`RULE-CTOR-037`), the creator returns:

<!-- illustrative -->
```jsonc
{
  "entity": "stream",
  "directory_slug": null,
  "document": null,
  "notes": [
    "The engine does not execute this connector kind, so no stream binding is authored for it (RULE-CTOR-037)."
  ]
}
```

## `Diagnostics` (output of `pipeline-schema-validator`)

The envelope an `analitiq-validator` MCP validation tool answers. A finding
carries `message_id`, `kind`, `path` and `message`, `rule` where it applies
one, and `severity` only for `kind: "fail"`. `passed` is `false` when a `fail`
finding is `severity: "error"`, or when a `notApplicable` finding names a rule
that is `error`-tier (or names none at all) — a check that could not run does
not get the benefit of the doubt. A `warning` and a `notApplicable` naming a
lesser-tier rule do not fail validation.

`path` is a JSON pointer into the document for a single-document request. For
a package or workspace request it is `<key>#<pointer>`, `<key>` being the
document's path from the directory submitted, percent-encoded.

<!-- illustrative -->
```jsonc
{
  "passed": false,
  "findings": [
    {
      "message_id": "missing",
      "kind": "fail",
      "severity": "error",
      "path": "<pipeline document key>#/schedule/interval_minutes",
      "message": "Field required"
    },
    {
      "rule": "RULE-SHRD-003",
      "message_id": "schema-url-missing",
      "kind": "fail",
      "severity": "warning",
      "path": "<connection document key>#/$schema",
      "message": "document declares no `$schema`; declare it with the published canonical URL for this family."
    }
  ]
}
```

<!-- BEGIN GENERATED: validator-ids -->
Rule ids on the documents this plugin authors that a validation request can surface beyond what a contract model rejects on its own, whether the check needs a second document in hand (references across a package or workspace, filename↔id) or grades one document as a plain function rather than a `@model_validator` (a database endpoint's id, a type-map's own rule warnings) — what a contract model rejects is catalogued per model in `references/rules/` instead of restated here:

`RULE-CONN-011`, `RULE-DBEP-011`, `RULE-PIPE-011`, `RULE-PIPE-012`, `RULE-PIPE-013`, `RULE-PIPE-014`, `RULE-PIPE-018`, `RULE-PKG-031`, `RULE-PKG-032`, `RULE-STRM-032`, `RULE-STRM-033`, `RULE-STRM-034`, `RULE-STRM-042`, `RULE-STRM-044`, `RULE-TMAP-014`, `RULE-TMAP-017`, `RULE-TMAP-018`, `RULE-TMAP-022`
<!-- END GENERATED: validator-ids -->

Validate the stitched pipeline as a workspace; that is what runs the
cross-document checks (the `RULE-PIPE-*`/`RULE-STRM-*`/`RULE-CONN-011`
referential rules above) and makes their findings reachable.

The agent adds a finding of its own when the server never graded the request:

- `validation-not-run` — `notApplicable`, `path: ""`, and the only finding in
  an envelope whose `passed` is false: the builder failed, the tool is not
  available (the user authorizes the `analitiq-validator` server with `/mcp`),
  the call was refused (`isError`), or the answer carries no `passed`.
  `message` carries the builder's stderr, the missing tool's name, or the
  server's text verbatim. It is never a verdict on the documents: the
  orchestrator halts on it, with no retry and no result of its own making.

Some findings name the rule they apply, as a leading `[RULE-<AREA>-NNN]` in
`message`. Quote the id verbatim whenever one is present — `pipeline-spec` and
`stream-spec` list the rules by id.

## `DriftVerdict` (output of `pipeline-drift-classifier`)

Informational only; the plugin authors no `version` (see
`identity-and-versioning.md`). The verdict's role is to flag structural
changes the user should think about before publishing.

<!-- illustrative -->
```jsonc
{
  "changes": [
    {"kind": "stream_added", "stream_slug": "balances"},
    {"kind": "write_mode_changed", "stream_slug": "transfers", "from": "insert", "to": "upsert"},
    {"kind": "mapping_target_added", "stream_slug": "transfers", "path": "currency"}
  ],
  "summary": "1 stream added; 1 write-mode change; 1 mapping target added."
}
```
