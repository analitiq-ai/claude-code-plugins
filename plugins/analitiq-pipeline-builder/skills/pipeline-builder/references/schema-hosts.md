# Schema host

Each authored document declares `$schema` (`RULE-SHRD-003`) with the exact value
its row names below.

<!-- BEGIN GENERATED: schema-urls -->
| Entity | Authored file | `$schema` value |
|---|---|---|
| pipeline | `pipelines/<dir>/pipeline.json` | `https://schemas.analitiq.ai/pipeline/latest.json` |
| stream | `pipelines/<dir>/streams/<name>.json` | `https://schemas.analitiq.ai/stream/latest.json` |
| connection | `connections/<dir>/connection.json` | `https://schemas.analitiq.ai/connection/latest.json` |
| type-map | `connections/<dir>/definition/type-map.json` | `https://schemas.analitiq.ai/type-map/latest.json` |
| database-endpoint | `connections/<dir>/definition/endpoints/<name>.json` | `https://schemas.analitiq.ai/database-endpoint/latest.json` |
<!-- END GENERATED: schema-urls -->

<!-- PROBE: pipeline-schema-pinned-url-rejected -->
There is no authorable pinned form. Only the `latest.json` URL above validates;
a version-pinned `…/<X.Y.Z>.json` variant is rejected outright.

## How validation works

The plugin does **not** fetch or cache schemas. `analitiq-validator` validates
each document against the bundled Pydantic contract models — the same source of
truth the published JSON Schemas are rendered from. The
`pipeline-schema-validator` agent runs the plugin's adapter (`scripts/validate.py`),
which self-installs the pinned validator into a managed virtualenv on first use
and is offline thereafter. That agent's definition carries the invocation.

Because validation is offline, a document's declared `$schema` URL is a label,
not a fetch target — keep it in sync with the entity so the file stays
self-describing.
