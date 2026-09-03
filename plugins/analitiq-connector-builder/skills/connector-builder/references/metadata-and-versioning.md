# Metadata and versioning

The authored top-level shape of a connector document, and how its `version`
moves between releases. Field shapes are owned by the contract models; the
release table is the plugin's own policy.

## Authored top-level fields

Which top-level fields exist and which are required is the contract's own
statement, not restated here — the closest worked example under your spec
skill's `examples/` tree is a full, CI-validated document showing the shape, and
the validator's findings name any required field you omitted. The notes below
carry only what that statement does not say.

| Field | Note |
|---|---|
| `$schema` | Author it (`RULE-SHRD-003`); §Schema URL declaration below. |
| `kind` | Routing per value: `enum-mappers.md` §KindMapper. |
| `connector_id` | The stable slug `^[a-z0-9][a-z0-9_-]*$` (`RULE-CTOR-023`), unchanging across releases (`RULE-CTOR-045`). |
| `default_transport` | Names a transport this document declares (`RULE-CTOR-001`). |
| `sql_capabilities` | A `database` connector declares it (`RULE-CTOR-040`); authoring: `connector-spec-db/spec-sql-write-path.md`. |

Note: the connector's type maps are **not** top-level fields. They ship as
separate sibling artifacts, one per direction — authoring:
`connector-spec-db/spec-type-maps.md`; on-disk paths and schema URLs:
`pipeline.md` §4 and §7.

## Authoring `connector_id`

The plugin authors `connector_id` on every connector document, so the contract
path `connectors/{connector_id}/definition/connector.json`, the registry repo
name and the plugin's output directory are one string (`RULE-CTOR-042`,
`RULE-CTOR-045`). Pattern: `RULE-CTOR-023`.

## Registry-stamped fields

`created_at` and `updated_at` are stamped by the registry on insert/update; an
authored document declares neither (`RULE-CTOR-064`).

Reserving a field name at the **document** level does not reserve it inside a
provider-owned namespace. A provider response legitimately containing a
`created_at` field is fine: `response.schema` describes the provider's data, not
the Analitiq document envelope. Only the document's own top level is reserved.

## Release version (`version`)

`version` bumps according to the connector release table:

<!-- BEGIN GENERATED: release-table -->
| Bump | Meaning | Examples |
|---|---|---|
| Patch | No connection drift. | Bug fixes, doc fixes, transport implementation tuning, top-level capability block introduced where the connector carried none (`sql_capabilities`, `error_map`), type-map rule reordered (when the reorder does not change first-match resolution for any existing input). |
| Minor | Additive, non-drifting. | Optional input added, optional discovery output added, optional endpoint added, write mode added to a kept endpoint, record field added, filter operators widened, kept endpoint offers something more a stream can bind, type-map rule added. |
| Major | Possible connection drift. | Input removed, input renamed, input type changed, input enum narrowed, storage moved, non-optional input added, auth-shape change, discovery-shape change, `sql_capabilities` shape fact narrowed, removed, or replaced with one an existing connection may not satisfy (any `stage.scope` or `stage.schema` change), endpoint removed, write mode removed from a kept endpoint, record field removed, record field type changed, filter operators narrowed, filter binding rerouted on a kept operator, conflict keys changed on a kept write mode, kept endpoint withdrew something a stream binds, type-map rule removed, render side changed for an existing matcher (read map: `canonical` changed for an existing `native`; write map: `native` changed for an existing `canonical`), kept endpoint now demands something of an existing document. |
<!-- END GENERATED: release-table -->

The drift-classifier sub-agent computes this bump from a diff between
the previous release and the new draft.

## First release

If no `previous_release_path` is supplied, set `version: "1.0.0"`
(`RULE-CTOR-032`).

## Schema URL declaration

Authored connector files declare `$schema` (`RULE-SHRD-003`) as
`https://schemas.analitiq.ai/connector/latest.json`.

The document families differ, so don't generalize from one to another:

<!-- PROBE: connector-schema-optional, endpoint-schema-host-locked -->
| Document | `$schema` | Enforced? |
|---|---|---|
| Connector | Author it; matched by pattern, tolerating any environment host (`schemas.analitiq.<tld>`). | Partly. The *pattern* is enforced when present, but the field is optional — a connector omitting `$schema` entirely validates clean. Always writing it is our convention, not a contract rule. |
| API endpoint | Locked to the `.ai` URL by a `const`. | Yes — required, and a different host is rejected. |
| Type maps | None — both maps are bare JSON arrays with no envelope. | N/A; direction comes from the filename. |
