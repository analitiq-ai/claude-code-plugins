# `connections` block

<!-- BEGIN GENERATED: fields-pipeline-connections -->
`analitiq.contracts.pipelines.config.PipelineConnections` — closed (`additionalProperties: false`); required: `destinations`, `source`

| Field | Required | Type | Default | Constraints |
|---|---|---|---|---|
| `source` | **yes** | string | — | `pattern=\S`, `minLength=1` |
| `destinations` | **yes** | array of string | — | `minItems=1`, `uniqueItems=True`, `item pattern=\S`, `item minLength=1` |
<!-- END GENERATED: fields-pipeline-connections -->

Duplicate destinations: `RULE-PIPE-001`.

## Connection reference format

A connection reference is the `connection_id` UUID of the corresponding
connection document. Emitting the UUID rather than the directory slug is
**plugin policy** — references resolve at runtime against authored
`connection_id` values.

The directory slug is for file organization only, never for cross-document
identity — see `../pipeline-builder/references/identity-and-versioning.md`.

## Rules

- A destination reference may equal the source — that's a legitimate self-loop
  (e.g., copying data within a single database between schemas).
- A deployment may cap fan-out even where the contract does not, so treat a very
  wide destination list as a question for the user.
- Connection ownership: `RULE-PIPE-008`.

## What is NOT in this block

- Connection bodies. Those live in
  `connections/<connection-slug>/connection.json`.
- Connection credentials. Those live in
  `connections/<connection-slug>/.secrets/`.
- The connector reference. The pipeline references **connections**, not
  connectors — see `connection-spec`.
