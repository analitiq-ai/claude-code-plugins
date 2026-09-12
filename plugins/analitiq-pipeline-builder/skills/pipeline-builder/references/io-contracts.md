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

A finding may arrive in either of two shapes: locally minted by this adapter
(`validator`, `severity`, `path`, `message`), or forwarded unchanged from
`analitiq.validator` (`rule`, `message_id`, `kind`, `path`, `message`, with
`severity` present only for `kind: "fail"`). `passed` is
computed by the same fail-closed predicate over either shape: `false` when a
`fail` finding is `severity: "error"`, or when a `notApplicable` finding names
a rule that is `error`-tier (or names none at all) — a check that could not
run does not get the benefit of the doubt. A `warning` and a `notApplicable`
naming a lesser-tier rule do not fail validation.

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
    },
    {
      "message_id": "coverage-check-skipped-no-path",
      "kind": "notApplicable",
      "path": "/",
      "message": "type-map coverage skipped: no filesystem-anchored document path."
    }
  ]
}
```

<!-- BEGIN GENERATED: validator-ids -->
Rule ids a cross-document check in `analitiq.validator` can emit:

`RULE-CONN-011`, `RULE-DBEP-011`, `RULE-ENDP-046`, `RULE-ENDP-047`, `RULE-ENDP-048`, `RULE-ENDP-063`, `RULE-PIPE-011`, `RULE-PIPE-012`, `RULE-PIPE-013`, `RULE-PIPE-014`, `RULE-PIPE-018`, `RULE-PIPE-019`, `RULE-PKG-030`, `RULE-PKG-031`, `RULE-PKG-032`, `RULE-PKG-033`, `RULE-PKG-035`, `RULE-STRM-032`, `RULE-STRM-033`, `RULE-STRM-034`, `RULE-STRM-042`, `RULE-TMAP-014`, `RULE-TMAP-017`, `RULE-TMAP-022`
<!-- END GENERATED: validator-ids -->

Pass `--bundle-root` when validating the stitched pipeline; that is what runs
the cross-document checks (the `RULE-PIPE-*`/`RULE-STRM-*`/`RULE-CONN-011`
referential rules above) and makes their findings reachable.

The adapter adds ids of its own, for checks the published bundle validator
structurally cannot make:

- `connector-endpoint-ref` — **warning-only**: a `scope: "connector"` stream ref
  naming an endpoint the downloaded connector does not publish. The message
  carries an alignment suggestion. See `stream-spec/spec-endpoint-refs.md`.
- `connection-type-map` — **error**: file-level gates on the connection-scoped
  type maps the engine loads beside `connection.json`. See
  `endpoint-spec/spec-type-map-gaps.md`.

One further id names not a check but a failure mode: `adapter-crash` —
**error**: the run could not be evaluated normally. Either a containment guard
inside `scripts/validate.py` fired — the document was not evaluated for that
stage, and `path`/`message` name which stage crashed and why — or the process
printed no `Diagnostics` JSON at all and the driving agent reconstructed this
finding from a stderr excerpt, carried in `message` with `path` empty.

This id names only a crash reaching a guard in this adapter. A crash inside
the published validator's own single-document dispatch (the `database_endpoint`
/ `type_map_read` / `type_map_write` routes, which call it directly) is already
caught there and returned as an ordinary `contract-model` finding whose
`message` says the check itself crashed — that finding never reaches this
adapter as an exception, so no guard here fires and it is not relabeled.

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
