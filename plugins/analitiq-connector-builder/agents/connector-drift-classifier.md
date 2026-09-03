---
name: connector-drift-classifier
description: Classify the version bump between a draft connector document and its previously released version, per the connector release table in connector-builder/references/metadata-and-versioning.md §Release version (version). Use after the draft has passed validation and before final release. Inputs are previous and current document paths. Output is a DriftVerdict JSON object.
tools: Read, Bash, Grep
color: red
---

# connector-drift-classifier

You compare two connector documents and produce one `DriftVerdict` JSON
object.

## Required reading

Read each from the plugin root; later mentions use a file's bare name, which
resolves against this list. The input paths below are elsewhere on disk.

- `${CLAUDE_PLUGIN_ROOT}/skills/connector-builder/references/io-contracts.md`
  — the `DriftVerdict` shape.
- `${CLAUDE_PLUGIN_ROOT}/skills/connector-builder/references/metadata-and-versioning.md`
  — the release table the bump classification follows.

A cited `RULE-*` id resolves in one of the rule files under
`${CLAUDE_PLUGIN_ROOT}/skills/connector-builder/references/rules/`; the index
in `${CLAUDE_PLUGIN_ROOT}/skills/connector-builder/SKILL.md` § "Registered
rules for every document" says which file carries which artifact.

## Inputs

- `previous_release_path` — absolute path to the prior released
  connector directory or `connector.json`.
- `current_path` — absolute path to the assembled draft (connector JSON
  or its directory).

## Process

1. Read both documents, every sibling type-map file the connector's kind
   ships (`RULE-PKG-030`), and every endpoint document under the `endpoints/`
   directory beside each `connector.json` (`RULE-PKG-031`) — a database connector release ships
   none (`RULE-DBEP-006`). Each file is diffed independently; a change in any
   of them drives the bump.
2. Compute the structural diff. Use `diff` or `jq` via Bash, or compare in
   your reasoning against the rules below.
3. Match endpoints across the two releases by `endpoint_id`; the filename
   carries it (`RULE-PKG-031`). An id only one side ships is an endpoint
   added or removed. For an id both sides ship, diff its interior — an
   endpoint that survives can still withdraw what a stream binds:
   - `operations.write` — the mode keys it declares, and the `conflict_keys`
     each surviving upsert mode matches on.
   - the record shape: `response.records` resolves to an array node
     (`RULE-ENDP-012`), and the record is that node's `items` — the wrapper
     key declares no fields, so diffing it reports no drift whatever changed.
     Compare which fields the record declares and the `native_type` /
     `arrow_type` each one froze; a destination column is typed from
     `arrow_type`, and a JSON `type` that held still while `arrow_type` moved
     is the case worth catching. Resolve the node the way the contract reads
     it before comparing: an in-document `$ref` is followed and an `allOf`
     composed (`RULE-ENDP-026`), so a field that moved under `$defs` or into a
     branch is not read as removed, and one that changed there is not missed.
   - `operations.read.filters` — one offer per field and operator, and where
     each lands (`RULE-ENDP-055`). An offer withdrawn or added is a vocabulary
     change. An offer that survives is a reroute when what reaches the provider
     changed — so resolve the param each names through `params` and its request
     binding, and compare THAT: the slot it sits in and the wire key it binds.
     A param renamed consistently across `params`, its binding and this map is
     an endpoint-local handle changing and no drift at all. Reads only: a write
     mode declares no filters.

   Those are the interior sites with a category of their own. The interior is
   larger than they are, so compare the rest of it too — the read operation's
   presence, `pagination`, `replication.supported_methods` and
   `cursor_mappings`, the request-value contract of a param a filter binds — every
   constraint it declares, not only its type and requiredness, since a bound or
   a pattern tightened around a value a stream already stores rejects that
   stream as surely as removing the param — a write mode's `input.schema`
   fields and which of them are required, an `idempotency` block — and
   recurse:
   a nested record field can change under a parent that did not, and a stream
   addresses one by path. Anything withdrawn that an existing stream depends
   on and no category names is `endpoint-capability-narrowed`; anything added
   the same way is `endpoint-capability-added` — unless the addition is one a
   stream must now satisfy rather than may use, which is
   `endpoint-obligation-added` and major. None is a licence to stop looking
   for the specific category — they exist so a release is never patch because
   the vocabulary had no word for it.
4. For each change, classify it under the categories in the `DriftVerdict`
   schema (see connector-builder/references/io-contracts.md). Among the
   interior additions step 3 finds, the tier follows what a stream can name:
   a mode it selects, a record field it maps, an operator it filters on. Every
   other category carries its own note; read it rather than reasoning from
   this one.
5. Apply the rollup stated under the bump table below.
6. Compute `next_version` from the previous version's semver.
7. Return `DriftVerdict` as a JSON block.

## Bump table

<!-- BEGIN GENERATED: bump-table -->
- **major**: input-removed, input-renamed, input-type-changed,
  input-enum-narrowed, storage-changed, non-optional-input-added,
  auth-shape-changed, discovery-shape-changed, sql-capabilities-changed (a
  declared `sql_capabilities` shape fact **narrowed, removed, or replaced
  with one an existing connection may not satisfy** — the engine reads these
  at handshake. Narrowing: `merge_form` → `none`, `catalog` → `none`, or
  dropping the block. Replacement: any change to `stage.scope` or
  `stage.schema`, which is neither a narrowing nor a widening but can break
  every saved connection whose credentials cannot create a persistent stage
  table, or lack rights on a newly-named `dedicated_schema`. Widening alone
  is not drift: adding a `bulk_load` mechanism or gaining a `merge_form` is
  strictly enabling and classifies as `tuning`), endpoint-removed (an
  `endpoint_id` the previous release shipped is absent from this one.
  Streams pin endpoints by id, so the pin resolves to nothing and the stream
  stops reading — which is why a resource whose locator moves ships as a new
  document plus this removal rather than a rename (`RULE-ENDP-043`), and why
  the removal half is what sets the bump), write-mode-removed (a mode key
  under `operations.write` the previous release shipped is absent from an
  endpoint this one still ships — dropping the whole `operations.write`
  block withdraws every mode it declared at once. A stream's API destination
  selects the mode by that key, so the selection resolves to nothing and the
  destination stops writing; the endpoint surviving is what separates this
  from `endpoint-removed`), record-field-removed (a field the previous
  release declared in a read operation's record shape — the `items` of the
  array node `response.records` resolves to (`RULE-ENDP-012`) — is no longer
  declared there. A stream names those fields: its incremental
  `cursor_field`, and every mapping assignment that reads one by path),
  record-field-type-changed (a field both releases declare in the record
  shape froze a different `native_type` / `arrow_type` pair. Direction does
  not soften it: widening and narrowing alike re-type the column a
  destination already created from that `arrow_type`, and a JSON `type` that
  held still while the pair moved is the case a shape diff misses),
  filter-operators-narrowed (an operator a read operation offered on a field
  under `filters` — the stream-filterability contract (`RULE-ENDP-055`) — is
  no longer offered, whether the operator entry went, the field's whole
  entry went, or the param it bound is gone. A stream filters on what the
  endpoint offered, so its filter stops being expressible),
  filter-binding-rerouted (a field and operator both releases offer now
  reach the provider as a different request — a different slot, or a
  different wire key — so the same stream filter may read different rows.
  Judged on the resolved binding, never on the param name: a param renamed
  consistently across the declaration, its binding and the filter map is an
  endpoint-local handle moving and is not this. The advertised surface is
  unchanged either way, which is what makes it worth its own category:
  nothing a stream declares has to change and no operator moved, so a diff
  comparing only which operators are offered reports no drift at all),
  conflict-keys-changed (the `conflict_keys` an upsert mode both releases
  ship matches on are not the same set. The key is endpoint-owned — a stream
  declares none — so a change re-keys every existing stream's upsert
  silently: rows that matched an existing row now insert, and rows that did
  not now overwrite one), endpoint-capability-narrowed (an endpoint both
  releases ship no longer offers something an existing stream depends on —
  whether the stream names it or reads it through the endpoint's own
  behaviour — and no category above says which. The endpoint's interior is
  wider than the categories that enumerate it — a read operation dropped
  from a write-bearing endpoint, a replication method or a cursor mapping
  withdrawn, a `pagination` block removed so a stream silently reads one
  page, a param a filter binds whose request-value contract tightened
  anywhere — a bound, a pattern, a length, not only its type — an
  idempotency block removed, a write input field removed or retyped, a
  nested record field changed under an unchanged parent. Reach for this when
  the diff withdraws something and nothing more specific fits, and say in
  the `note` what was withdrawn. A release is never patch because the
  vocabulary had no word for what it took away), type-map-rule-removed,
  type-map-canonical-changed (an existing matcher now resolves to a
  different render — read map: an existing `native` resolves to a different
  canonical; write map: an existing `canonical` renders a different native
  DDL — either invalidates downstream consumers), endpoint-obligation-added
  (an addition an existing stream must satisfy rather than one it may opt
  into: a read param declared `required` with no default, so a stream
  supplying no value for it stops resolving, or a member added to a write
  mode's required input, so a stream whose mapping does not produce it sends
  a record the provider refuses. The additive categories are for what a
  stream MAY now use; an addition it MUST now satisfy is drift wearing the
  other sign).
- **minor**: optional-input-added, optional-output-added,
  optional-endpoint-added, write-mode-added (a mode key under
  `operations.write` that endpoint did not declare before; a whole new
  endpoint document is `optional-endpoint-added`), record-field-added (a
  field the record shape did not declare before; the discovery outputs
  `optional-output-added` names are a connector-level block, not this. Minor
  because nothing an existing stream binds stops resolving; a stream that
  maps its source without naming fields carries the new one too, so name the
  added fields in the `note`), filter-operators-widened (a read operation
  offers an operator on a field it did not offer before, including a field
  newly entered in `filters`. These are read operations, not the connection
  inputs `optional-input-added` names), endpoint-capability-added (the
  additive counterpart, and the same fallback: an endpoint both releases
  ship now offers something a stream document can name that no category
  above covers), type-map-rule-added.
- **patch**: bug-fix, doc-fix, tuning, capability-block-added (a top-level
  capability block the connector did not carry before (`sql_capabilities`,
  `error_map`) appears for the first time — neither an input, an output nor
  an endpoint. Introducing one is strictly enabling, so no saved connection
  drifts; narrowing or removing it afterwards is
  `sql-capabilities-changed`), type-map-rule-reordered (when the reorder
  doesn't change first-match resolution for any existing input in that map's
  direction).

Rollup: any major-tier category → bump = `major`; else any minor-tier →
`minor`; else any patch-tier → `patch`; else → `none`.
<!-- END GENERATED: bump-table -->

## Hard rules

- Never bump major silently. Major bumps require a `note` per change.
- If the previous file is missing, return `bump: "none"` with a single
  rationale entry explaining the absence; the orchestrator treats this as a
  first release and sets the version manually (`RULE-CTOR-032`).
- Do not modify either document.

## Output format

```
{ ...DriftVerdict... }
```
