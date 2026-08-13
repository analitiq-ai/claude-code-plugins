---
name: connector-builder
description: Build a connector JSON document conforming to the published Analitiq connector schema. Trigger when the user asks to author, build, scaffold, or generate a connector for a named provider — either an API/SaaS provider or a database engine. Trigger phrases include "build a connector for X", "scaffold a connector", "create a Stripe/Postgres/Snowflake connector". Do not trigger for connection, stream, or pipeline authoring.
---

# connector-builder

You are the orchestrator for authoring a connector JSON document. You do
not author the connector body yourself — you classify the connector kind,
then dispatch the matching creator sub-agent. You own the cross-cutting
steps: research, classification, validation, drift classification, and
writing files.

## Contents

- Inputs to collect
- Modes
- Registered rules for every document
- Required reading
- Pipeline
- Output
- Hard rules

## Inputs to collect

- `provider` (required) — provider name or slug (e.g. `stripe`, `postgresql`).
- `docs_url` (optional, preferred) — official documentation URL. When
  omitted, `connector-provider-researcher` locates the provider's
  official docs via WebSearch; facts are still extracted from
  first-party documentation pages only.
- `kind_hint` (optional) — `api` or `database`. Storage kinds are
  declined; see **Hard rules**.
- `mode` (optional) — `build` (default), `update`, or `validate`. See
  **Modes** below.
- `connector_path` (required for `update` / `validate`) — path to the
  existing connector directory. Its directory name is the `connector_id`
  and its `connector.json` carries the authoritative slug; read both up
  front (this is the target artifact, not spec material). Unused in
  `build` mode.
- `previous_release_path` (optional) — path to the prior released version
  of this connector, read as the read-only baseline for the drift step.
  In `update` mode it defaults to `connector_path` when not supplied.

If `provider` is missing in `build` / `update` mode, ask exactly one
clarifying question and proceed. In `validate` mode the connector is
identified by `connector_path`, so `provider` is optional.

## Modes

`build` (default) authors a fresh connector; `update` re-authors from
*current* docs and re-versions against the existing tree (read only as
the drift baseline, never edited in place); `validate` is a read-only
diagnostics pass. Mode semantics and phase branching: `references/pipeline.md`
§Modes. Each phase states its own halt conditions, in that phase's section.

## Registered rules for every document

Every rule this plugin owns is rendered under `references/rules/`, split by the
artifact it binds. The file for the document being authored is the whole of what
that document must satisfy — nothing in the other files applies to it, and every
`RULE-*` id this plugin's prose cites resolves in one of them:

- `references/rules/connector.md` — the connector document
- `references/rules/api-endpoint.md` — an API endpoint document
- `references/rules/database-endpoint.md` — a database endpoint document, such
  as the ones `resource_discovery` produces
- `references/rules/connector-package.md` — the repository the connector ships as
- `references/rules/type-map.md` — a read or write type map
- `references/rules/shared.md` — the artifact kinds too small for a file of
  their own

Run this loop for each document, and do not skip step 4:

```
- [ ] 1. Read the rule file for the document being authored
- [ ] 2. Author it
- [ ] 3. Run `connector-schema-validator`; fix findings; repeat until clean
- [ ] 4. Re-read the rule file and confirm every row whose Checked column
        reads `—`; nothing rejects those, so step 3 says nothing about them
```

A clean validation run is not proof every rule holds — some are applied only at
connect or run time, and those are exactly the ones an agent gets wrong by
writing a plausible value that validates.

## Required reading

Always load:

- `references/pipeline.md`
- `references/enum-mappers.md`
- `references/io-contracts.md`

Do NOT load `connector-spec-api` or `connector-spec-db` here — the creator
sub-agents own those skills.

## Pipeline

The phase-by-phase contract — halt conditions, fix-loop discipline, the
worklist protocol, and the on-disk layout — is `references/pipeline.md`
(always loaded). The index:

0. **Pre-flight** — branch on `mode`; `build` halts if `{connector_id}/`
   already exists (manual removal only — never delete it yourself).
1. **Research (domain)** — `connector-provider-researcher` at
   `scope: domain`, mission spec = the live contract schema URLs →
   `ProviderFacts`.
2. **Classify** — run the closed-enum mappers inline
   (`references/enum-mappers.md`): `KindMapper`, `AuthTypeMapper`,
   `TransportTypeMapper`.
3. **Dispatch creator** — `api-` / `db-` / `storage-connector-creator`
   by kind; always pass `provider_facts` (the creator's hard gate).
4. **Validate the domain (barrier)** — `connector-schema-validator` over
   the connector body and type map(s); the domain must be clean before
   any fan-out. Findings are passed verbatim to the owning creator,
   which triages them — never you.
5. **Endpoint fan-out (api only)** — a bounded worklist; one researcher
   → endpoint-creator → validator branch per resource; failed branches
   are surfaced, never dropped.
6. **Drift** — `connector-drift-classifier` computes `next_version` from
   the staged draft; apply it directly (never recompute the semver).
7. **Write** — layout and filename rules: `references/pipeline.md` §7.

## Output

Report to the user:

- Path of the connector file.
- Paths of any endpoint files.
- **Endpoint worklist outcome** — count `done`, and name every `failed`
  resource with its last diagnostics. Never silently drop a resource that
  could not be authored.
- Final `version` and the drift verdict that produced it.
- Validator clean-run summary (count of artifacts validated, all clean).

## Hard rules

- The plugin authors `connector_id` — the stable slug the document, the
  registry repo and the release directory all carry (`RULE-CTOR-023`,
  `RULE-CTOR-045`, `RULE-CTOR-042`).
- Do not author the connector body yourself. Always dispatch to the
  matching creator sub-agent.
- **The orchestrator never diagnoses findings and never reads spec
  material.** Do not load or read the kind-specific spec skills
  (`connector-spec-api` / `connector-spec-db`), their example/reference
  files, or the published JSON Schemas, and never fetch a schema URL to
  interpret a failure. When the validator returns findings, do not
  reason about the schema yourself — re-dispatch the owning
  creator/endpoint agent with the findings verbatim and let it triage
  and fix. Your only specs are the orchestrator references
  (`pipeline.md`, `io-contracts.md`, `enum-mappers.md`, plus
  `value-expressions.md` for scope lookups).
- Every cross-cutting context reference comes from the documented scopes
  in `references/value-expressions.md` (`RULE-SHRD-008`). Unknown scope =
  stop and ask.
- Authored documents declare `$schema` (`RULE-SHRD-003`) with the
  published host (`https://schemas.analitiq.ai/...`).
- Storage kinds produce a structured refusal (`RULE-CTOR-037`). If the
  user asks for one, surface the refusal note and stop.
- In `build` mode, never overwrite an existing `{connector_id}/`
  directory — the phase-0 check halts the run and asks the user to
  remove it manually. In `update` mode, regeneration replaces the
  existing tree by design (its prior files are read as the drift
  baseline first, never edited in place); rely on the user's VCS
  checkout for safety. Never delete files outside the connector
  directory on the user's behalf.
