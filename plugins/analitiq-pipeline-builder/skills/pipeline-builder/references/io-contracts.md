# I/O contracts between orchestrator and agents

Every cross-agent payload is a JSON object matching one of the shapes
below. The orchestrator checks a payload against its shape before
dispatching the next phase.

## Contents

- `PipelineFacts` (output of `pipeline-provider-researcher`)
- `MintedIdentities` (orchestrator-local, phase 3)
- `CreatorOutput` (output of every creator agent)
- `Diagnostics` (output of `scripts/validate.py`)
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
    "connection_slug": "wise",                  // directory name for connections/<slug>/
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
UUID (read from the on-disk `connection.json`) instead of a freshly
minted one.

## `CreatorOutput` (output of every creator agent)

Each creator agent returns the JSON it would write, plus optional notes.
The orchestrator handles disk I/O.

<!-- illustrative -->
```jsonc
{
  "entity": "pipeline",                       // "pipeline" | "stream" | "connection" | "database_endpoint"
  "directory_slug": "wise_to_postgresql",     // matching directory name under pipelines/ etc.
  "document": { /* the authored JSON, $schema set, no server-managed fields */ },
  "secondary_files": [                        // optional — e.g., .secrets templates
    {"path": ".secrets/credentials.json", "content": { /* … */ }}
  ],
  "notes": []                                 // human-readable rationale / caveats
}
```

The identity UUID (`pipeline_id`, `stream_id`, `connection_id`) lives
inside `document`; the orchestrator reads it from there for downstream
cross-references. Endpoint creators carry the slug identity in
`document.endpoint_id`.

`private-endpoint-creator`'s sub-modes wrap their `CreatorOutput[]` in a
mode-level envelope (`{"mode", "outputs", …}`, plus `type_maps` in
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

## `Diagnostics` (output of `scripts/validate.py`)

<!-- illustrative -->
```jsonc
{
  "passed": false,
  "findings": [
    {
      "validator": "contract-model",
      "severity": "error",
      "path": "/schedule/interval_minutes",
      "message": "Field required"
    }
  ]
}
```

`passed` is `true` only when no `error`-severity finding exists — a `warning`
does not fail validation.

<!-- BEGIN GENERATED: validator-ids -->
Finding ids the validator can emit:

`bundle-connection-ref`, `bundle-connector-ref`, `bundle-endpoint-ref`, `bundle-pipeline`, `bundle-stream-ref`, `contract-model`, `document`, `embedded-json-schema`, `embedded-schema-example`, `endpoint-filename`, `endpoint-id-locator`, `endpoint-id-unique`, `endpoint-transport-ref`, `record-field-unreadable`, `type-map-coverage`, `type-map-rule`, `type-map-write-coverage`
<!-- END GENERATED: validator-ids -->

Pass `--bundle-root` when validating the stitched pipeline; that is what runs
the cross-document checks and what makes the `bundle-*` ids reachable.

The adapter adds ids of its own, for checks the published bundle validator
structurally cannot make:

- `connector-endpoint-ref` — **warning-only**: a `scope: "connector"` stream ref
  naming an endpoint the downloaded connector does not publish. The message
  carries an alignment suggestion. See `stream-spec/spec-endpoint-refs.md`.
- `connection-type-map` — **error**: file-level gates on the connection-scoped
  type maps the engine loads beside `connection.json`. See
  `endpoint-spec/spec-type-map-gaps.md`.

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
