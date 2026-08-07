# CLAUDE.md — analitiq-connector-builder

Contributor guidance for this plugin. Repo-wide concerns — layout, tests, the
contract pin, releases, credentials, drift policy — live in the root `CLAUDE.md`
and are not repeated here.

## What this plugin does

Authors connector and endpoint JSON documents conforming to the published
Analitiq contract at `schemas.analitiq.ai`. Connectors may be published to the
`analitiq-dip-registry` GitHub org as individual repos named `{connector_id}`.

## What it refuses

- **`connection` and `pipeline` documents** — runtime credentials for a connector
  instance, and the full integration definition. Owned by the sibling
  `analitiq-pipeline-builder` plugin.
- **Database endpoints** — connection-scoped, produced by the connector's
  `resource_discovery` workflow at runtime, never authored here.
- **Storage kinds** (`file`, `s3`, `stdout`) — accepted by the schema, but the
  engine does not execute them, so `storage-connector-creator` returns a
  structured refusal until support lands.

Agents must never author JSON that belongs to another agent's responsibility.

## Agent chain

```
connector-builder (skill, orchestrator)
  → connector-provider-researcher        scope: domain
  → {api|db|storage}-connector-creator
  → connector-schema-validator           ← domain barrier: must pass before fan-out
  → endpoint fan-out (API only), per resource, bounded concurrency (default 10):
        connector-provider-researcher (scope: endpoint)
          → endpoint-creator
          → connector-schema-validator
  → connector-drift-classifier           optional
  → write files
```

Database connectors author no endpoints and skip the fan-out entirely.

| Agent | Owns |
|---|---|
| `connector-builder` (skill) | Orchestration: classify kind, dispatch the creator, run the validator loop, run drift classification, write files. |
| `connector-provider-researcher` | Fact extraction from the provider's official docs, never authoring. Two scopes: `domain` → `ProviderFacts`, `endpoint` → `EndpointFacts` for one resource. Prefers a user-supplied docs URL; otherwise locates official docs via `WebSearch`. First-party pages only. |
| `api-connector-creator` | `kind: "api"` connector bodies. |
| `db-connector-creator` | `kind: "database"` packages: connector body, both type maps, and the Python package files. |
| `storage-connector-creator` | Stub — structured refusal (see above). |
| `endpoint-creator` | One API endpoint document per invocation. |
| `connector-schema-validator` | Structural + semantic validation of JSON documents only; package files are registry CI's job. |
| `connector-drift-classifier` | Diffs the draft against `previous_release_path`, emits a `DriftVerdict` so the orchestrator bumps `version` correctly. |

## Orchestrator modes

- **`build`** (default) — author a fresh connector; halts if a `{connector_id}/`
  directory already exists.
- **`update`** — re-author from *current* docs and re-version by diffing the
  fresh draft against the existing connector. The existing tree is a read-only
  versioning baseline, never edited in place; the tree is regenerated and the
  version bumps from the prior release. Run inside a VCS checkout so the
  regeneration is reviewable via `git diff`.
- **`validate`** — read-only pass over an on-disk connector. Reports diagnostics
  without researching, authoring, or writing. To fix findings, re-run `update`.

## Validator-behavior claims — the CI gate

<!-- PROBE: write-body-path-typo-unresolved, connector-function-name-unchecked -->
Prose in BOTH plugins states what the validator checks and does not check
("a `success_when` typo validates clean", "function names are never checked").
Such a sentence must be pinned to `scripts/render_validator_claims.py`, which
carries an executable **probe** per claim — a document run through
the in-repo validator with an asserted outcome. Pin it when you write it;
the rules below say how.

```bash
python3 scripts/render_validator_claims.py write   # regenerate marked blocks
python3 scripts/render_validator_claims.py check   # CI: probes + blocks + fences
```

Rules when editing prose in this plugin (and validator claims in the sibling):

- **Never hand-edit between a `BEGIN GENERATED` / `END GENERATED` marker
  pair** — the script overwrites it and CI fails.
- A sentence asserting validator behavior outside a generated block must have
  a `PROBE:` fence comment naming the probe(s) that prove it placed directly
  above it, or cite the `ADV-*` rule that enforces it in the same sentence.
  Recognising that a sentence makes a claim is the author's job, and
  `.claude/rules/validator-claims.md` says why: deciding it from the wording
  took a list of hand-curated English regexes, which is banned. Pin the claim
  when you write it.
- A probe that stops matching the contract means the contract moved: update
  the prose AND the probe together, then re-run `write`.
- Do NOT restate validator rules in `references/definition-of-done.md`: if an
  item is mechanically checkable, it belongs in the validator, not on that
  list.
- The same script renders one non-claim block family: the release-policy
  projections (the release table, the drift classifier's bump table, the
  `DriftVerdict` envelope), owned as data by
  `scripts/connector_release_table.py`. Change the bump policy there, then
  re-run `write` — never in the prose. (`write` verifies the probes before
  rendering anything, so they must all be green first.)
- `tests/connector_builder/test_validator_claims.py` runs the same predicate
  in pytest.

## Fenced JSON examples — the annotation convention

Prefer pointing prose at a validated file under a skill's `examples/` tree
(gated by `tests/*/test_examples*.py`) over an inline fence. An inline
`json` / `jsonc` fence that stays carries an HTML comment directly above it
declaring how it is verified (applies to BOTH plugins; machine-enforced for
the pipeline plugin by `tests/pipeline_builder/test_prose_snippets.py`,
review-enforced here until a matching gate lands):

- `<!-- validate: <resource> -->` — a full document; must validate against
  that resource's contract.
- `<!-- validate: <resource>#/<pointer> -->` — a fragment; must validate
  against the sub-model at that pointer. A fragment may show its enclosing
  key for context (`"replication": { … }`, wrapped or not) — the pointer
  names the deepest shown node, and a gate unwraps the key before
  validating.
- `<!-- invalid: <ADV id> -->` — deliberately wrong; must fail validation
  (that half is what a gate asserts — a "don't do this" example that rots
  into valid is the most misleading rot there is). That the failure is the
  named rule's diagnostic stays review-enforced: the validator reports
  model messages, not rule ids.
- `<!-- illustrative -->` — outside the published contract's validation
  surface (plugin-internal I/O envelopes, shape sketches); an explicit,
  reviewable exemption.

## Where the authoring rules live

Every rule about *how* to author lives in `skills/`, loaded by the agent that
needs it. **This file deliberately does not restate any of it** — a second copy
is a drift surface (root `CLAUDE.md` → drift policy).

| Topic | Skill |
|---|---|
| Orchestration, endpoint identity, I/O contracts, lifecycle phases, value expressions, connection contract, metadata + versioning | `skills/connector-builder/` and its `references/` |
| Auth flows, HTTP transports, pagination, replication | `skills/connector-spec-api/` |
| Driver selection, DSN bindings, TLS, resource discovery, read/write type maps, the SQL write path (`sql_capabilities` + the dialect renderers), connector package files | `skills/connector-spec-db/` |
| Storage-kind stub | `skills/connector-spec-storage/` |

The published schema is the authority over all of it. Enum lists appearing in
skill prose are illustrative; the live schema and the pinned
`analitiq-contract-models` package are normative.
