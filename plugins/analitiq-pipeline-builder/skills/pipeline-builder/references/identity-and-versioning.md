# Identity and versioning

Pipelines, streams, and connections each carry an RFC-4122 UUID **identity
field** (`pipeline_id`, `stream_id`, `connection_id`) that the plugin authors
directly. Connectors and database endpoints carry handles instead of a UUID
identity field: the plugin writes a connection's `connector_id` as the
connector's slug, and an endpoint's `endpoint_id` is derived from its
`database_object`, never hand-written (`RULE-DBEP-011`). A `connector_id` the
plugin did not author — the registry also assigns UUID-shaped ones — is read as
given and never rewritten (`RULE-CTOR-045`); treating one as a defect and
"fixing" it is a rename. Directory names on disk stay
human-readable slugs and are independent of the UUID identity stored inside the
documents.

## Contents

- Identifier shapes
- Cross-document references — contract vs. plugin policy
- Metadata fields
- Directory layout vs. document identity
- "Lifecycle" means three unrelated things
- Server-managed `version` field

## Identifier shapes

<!-- BEGIN GENERATED: shared-vocabulary -->
| Concern | Published constant | Pattern |
|---|---|---|
| Slug (ids; directories by convention) | `analitiq.contracts.shared.common.SLUG_PATTERN` | `^[a-z0-9][a-z0-9_-]*$` |
| UUID (`*_id` identity fields) | `analitiq.contracts.shared.types.UUID_PATTERN` | `^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` |
| Cron expression | `analitiq.contracts.shared.common.CRON_PATTERN` | `^cron\(.+\)$` |
| No edge whitespace (`display_name`, tags) | `analitiq.contracts.shared.common.NO_EDGE_WHITESPACE_PATTERN` | `^\S(?:[\s\S]*\S)?$` |

| Bound | Value |
|---|---|
| `display_name` length | `1..120` |
| `description` max length | `2000` |
| `tags` max count | `50` |
| tag length | `1..64` |
<!-- END GENERATED: shared-vocabulary -->

**Directory-slug convention.** The contract's `SLUG_PATTERN` governs document
fields only; it does not constrain on-disk names. This plugin reuses the same
shape for every directory and file slug (see §"Directory layout vs. document
identity") **by convention**, so a directory name is always a legal identifier.
The convention is pinned by reference: if the published pattern changes, the
directory-slug rule follows it.

The plugin authors every identity field it can, even where the contract does not
require one, so that sibling documents written in the same run can
cross-reference each other; the orchestrator keeps the minted UUIDs in memory
for exactly that reason. Which fields are required is in each spec skill's
generated field table.

`connector_id` and `endpoint_id` are **immutable** (`RULE-CTOR-045`). In edit
mode, never rewrite an existing identifier in place; author a new artifact and
let the user retire the old one.

Do not read meaning out of a UUID (`RULE-SHRD-005`). In particular the pipeline's
server-managed integer `version` is a separate field and must never be encoded
into `pipeline_id`.

## Cross-document references — contract vs. plugin policy

Keep these two apart.

**What the contract says.** The shape and constraints of every reference field
are in the generated tables: `../../pipeline-spec/spec-connections.md`
(`fields-pipeline-connections`) and `../../stream-spec/SKILL.md`
(`fields-stream`). Read them there rather than from this page. Both a bare id
and a `_v<n>`-suffixed one satisfy those constraints.

**What this plugin does.** The plugin authors **bare UUIDs** in every reference —
it does not append a `_v<n>` suffix anywhere. That is a deliberate plugin
convention, not a contract requirement: the plugin never calls the registry, so
it has no version to pin to, and a bare id lets the engine resolve the current
version. Do not "fix" an authored bare reference into a versioned one, and do not
strip a `_v<n>` suffix from a reference the user supplied.

| Reference field | The plugin sets it to |
|---|---|
| `pipeline.connections.source` | the source `connection.connection_id` UUID |
| `pipeline.connections.destinations[]` | each destination `connection.connection_id` UUID |
| `pipeline.streams[]` | each child `stream.stream_id` UUID |
| `stream.pipeline_id` | the parent `pipeline.pipeline_id` UUID |
| `stream.source.endpoint_ref.connection_id` | the source `connection.connection_id` UUID |
| `stream.destinations[].endpoint_ref.connection_id` | each destination `connection.connection_id` UUID |

`RULE-PIPE-011`, `RULE-PIPE-012`, `RULE-STRM-032`, `RULE-STRM-033` and
`RULE-STRM-034` bind these values to the identities in the sibling documents.
Run the validator with `--bundle-root` so it has those siblings in hand.

## Metadata fields

`display_name` is a user-facing **label, not an identity key**: it may change
freely without changing identity, and nothing keys off it. `tags` are opaque
grouping labels the contract assigns no meaning to.

Top-level artifact metadata uses `display_name` — never `name` or `title`.

These rules govern **artifact metadata only**. They say nothing about
provider-owned names: a database column, an operation parameter, or a
connection-contract input key keeps whatever spelling the provider uses.

## Directory layout vs. document identity

Directories use human-readable slugs:

```
pipelines/<pipeline-slug>/pipeline.json
pipelines/<pipeline-slug>/streams/<stream-slug>.json
connections/<connection-slug>/connection.json
connections/<connection-slug>/definition/endpoints/<endpoint-slug>.json
```

The slug is **only** for file organization. Cross-document refs inside the JSON
use the identities above, never the directory slugs. The bundle checks find
stream files by walking `pipelines/<slug>/streams/` and then compare the values
inside the documents.

## "Lifecycle" means three unrelated things

Never conflate them:

1. **Connector template phases** — the connector contract's own staging of when
   each field it declares resolves. Connector-owned; this plugin only reads it.
2. **Authored artifact `status`** — the lifecycle of a pipeline or stream
   document. See `pipeline-spec`.
3. **Per-run operation lifecycle** — the state of one execution. Runtime-owned
   and absent from every authored document.

## Server-managed `version` field

`version` is server-managed on pipelines and streams: never author it, never
edit it. See `reserved-fields.md`.
