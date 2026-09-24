# Schema host

Each authored document declares `$schema` (`RULE-SHRD-003`) with the exact value
its row names below.

<!-- BEGIN GENERATED: schema-urls -->
| Entity | Authored file | `$schema` value |
|---|---|---|
| Pipeline | `pipelines/<slug>/pipeline.json` | `https://schemas.analitiq.ai/pipeline/latest.json` |
| Stream | `pipelines/<slug>/streams/<stream-slug>.json` | `https://schemas.analitiq.ai/stream/latest.json` |
| Connection | `connections/<slug>/connection.json` | `https://schemas.analitiq.ai/connection/latest.json` |
| Database endpoint | `connections/<slug>/definition/endpoints/<endpoint_id>.json` | `https://schemas.analitiq.ai/database-endpoint/latest.json` |
| Connection type map | `connections/<slug>/definition/type-map.json` | `https://schemas.analitiq.ai/type-map/latest.json` |
<!-- END GENERATED: schema-urls -->

<!-- PROBE: pipeline-schema-pinned-url-rejected -->
There is no authorable pinned form. Only the `latest.json` URL above validates;
a version-pinned `…/<X.Y.Z>.json` variant is rejected outright.

## How validation works

The plugin submits its documents to the `analitiq-validator` MCP server it
ships, which validates each against the Pydantic contract models — the same
source of truth the published JSON Schemas are rendered from. The
`pipeline-schema-validator` agent's definition carries the submission.

The validator never fetches a document's declared `$schema` URL: it is a label,
not a fetch target — keep it in sync with the document kind so the file stays
self-describing.
