# Schema host

Each authored document declares `$schema` (`RULE-SHRD-003`) with the exact value
its row names below.

<!-- BEGIN GENERATED: schema-urls -->
| Entity | `$schema` value |
|---|---|
| Pipeline | `https://schemas.analitiq.ai/pipeline/latest.json` |
| Stream | `https://schemas.analitiq.ai/stream/latest.json` |
| Connection | `https://schemas.analitiq.ai/connection/latest.json` |
| Database endpoint | `https://schemas.analitiq.ai/database-endpoint/latest.json` |
| Connection type map | `https://schemas.analitiq.ai/type-map/latest.json` |

Where each document sits is defined by the workspace schema (`https://schemas.analitiq.ai/workspace/latest.json`), whose package entries point to each package's schema.
<!-- END GENERATED: schema-urls -->

To resolve a document's path, read those schemas: each `patternProperties` key
is a location, and its `$ref` names the schema of the package or document that
sits there. A package directory's segment is its slug
(`identity-and-versioning.md`), a package's root document is its schema's
`required` entry, and a location marked `x-secret` holds secret values.

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
