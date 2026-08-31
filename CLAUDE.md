# CLAUDE.md

## What This Repo Is

A **monorepo holding one contract surface**: the Claude Code plugins end users
run to author Analitiq artifacts, and the Python packages and JSON Schemas that
define what those artifacts must look like. The plugin prose, the Pydantic
contract models, the validator and the published JSON Schemas are expressions of
one set of rules. They live together so a rule changes in one place — every
boundary between them would be a surface a human has to keep in sync by hand.

It is also a **Claude Code plugin marketplace**: `.claude-plugin/marketplace.json`
declares `analitiq-claude-code-plugins`, each entry's `source` a relative path
into `plugins/`.


## Trees nothing here may author

**`schemas/` is generated.** `scripts/render_schemas.py` renders it from
`packages/contract-models`; `render_schemas.py check` re-renders and fails on any
diff, and CI runs it. Never hand-edit a file under `schemas/`. Two versionless
documents are generated the same way and covered by `check`:
`canonical-types.json` (from the vendored engine grammar) and
`contracts-version.json`, the provenance stamp recording which
`analitiq-contract-models` version the tree renders from plus a digest of it —
the `contracts-version-guard` CI job holds the published copy, and
`VALIDATOR_PIN`, to that stamp.

The hand-authored exceptions, outside the registry and never inspected by
`check`: `data-sync-api/openapi.json` (no version triple) and
`data-sync-run-response/1.0.0.json`. Every pinned `X.Y.Z.json` is immutable —
changing one means renaming to a new triple, never editing in place. The publish
(`.github/workflows/schemas-publish.yml`, OIDC via the `schemas` environment) is
additive: pinned objects are first-write-wins and byte-compared on re-runs,
nothing is deleted, and mutable pointers (`latest.json`, `index.json`, every
versionless document) rely on a short cache-control TTL, not invalidation.

Only the public resources render here; the internal-audience schemas stay in the
infra repo with the private half of the renderer. `Resource.__post_init__` fails
the build if a registered model tree reaches outside `analitiq.contracts`.

**The canonical Arrow type vocabulary is engine-owned.** The set of type families
the platform executes is an engine capability surface: analitiq-core publishes
`arrow-type-grammar` and `conversion-matrix` at `schemas.analitiq.ai`, and this
repo vendors one pinned grammar version at
`packages/contract-models/src/analitiq/contracts/arrow_type_grammar.json`.
`ARROW_TYPE_PATTERN`, the canonical-types `$defs` and the container-head set are
all derived from it; `analitiq.contracts.arrow_grammar` states the pin (version +
sha256) once. Both artifacts self-declare a top-level `version` and keep their
payload under a key of its own — the grammar's families under `families`, the
matrix's grid under `conversions`. Always read those keys, never the document
itself. `test_arrow_grammar.py` and the `engine-grammar-pin-guard` CI job hold the
vendored bytes and the self-declared versions to the pin. A family is added by
shipping it in the engine first, then bumping the pin here (re-vendor,
`render_schemas.py canonical-types`, re-render, re-run the plugin doc generator) —
never by hand-editing the vocabulary.

**`plugins/<name>/` is a distribution artifact.** Its contents are copied verbatim
into every user's plugin cache. Tests, scratch output, CI config and contributor
documentation do not belong inside it — that is why `tests/` and `contributing/`
sit at the repo root, namespaced per plugin. A `CLAUDE.md` there is doubly wrong:
Claude Code does not load one from a plugin root, so it ships to users and
instructs nobody. Each plugin's contributor guide — its agents, skills and
authoring rules — is `contributing/<plugin-name>.md`; read the one for the plugin
you are working inside. This file covers only what spans both.

## The contract, and the runtime pin

This repo is the **source** of `analitiq-contract-models` and
`analitiq-validator` (`packages/*/src`). The version of record is each package's
`pyproject.toml`; the two move in lockstep
(`packages/validator/tests/test_contract_models_pin.py`).

**Never `pip install` those two packages into a dev environment.** A built wheel
ships a generated `analitiq/contracts/__init__.py`, making it a *regular* package,
while the in-repo tree deliberately has none, making it a *namespace portion* — and
a regular package wins regardless of `sys.path`, so an installed copy silently
shadows the source and the suite grades the wrong code. The repo-root `conftest.py`
puts both source trees on the path and sets `ANALITIQ_VALIDATOR_FROM_SOURCE=1` so a
helper run from a checkout does not bootstrap a venv and `os.execv` out of pytest.
`requirements-dev.txt` carries only the packages' runtime deps.

The plugins **self-install a published release at runtime** — end users have no
checkout, so the pin must name a version already on PyPI. **`VALIDATOR_PIN` in
`plugins/analitiq-pipeline-builder/scripts/_bootstrap.py` is the only place that
version is stated.** Never restate it. The one unavoidable second copy is the
self-install line in
`plugins/analitiq-connector-builder/agents/connector-schema-validator.md` (prose an
agent runs, so it cannot import the constant), pinned by
`test_connector_validator_agent_states_the_same_pin`.

The pin must be **at or behind** `packages/validator/pyproject.toml`. Equal is the
steady state; behind is tolerated for merging because the publish is a hand-pushed
tag firing before the version bump merges — the `contracts-version-guard` job reds
its strict runs while the pin lags, as the reminder to finish the release. A pin
**ahead** of what this repo ships is the dangerous direction: marketplace installs
track main HEAD, so every user's `pip install` fails and the plugin cannot run.
`PINNED_VERSION` in `tests/connector_builder/_pins.py` is a *different* value — what
this repo ships, not what the plugins install — so it runs ahead during a release
window. `scripts/check_validator_pin_contract.py` (CI job `pinned-validator-guard`)
guards the pin from the other side; its docstring owns the full semantics.

## The stores of record

Each store holds a different **unit**. That is what separates them — not subject
matter, which crosses all of them. A fact belongs where its unit belongs, and a
store that could only hold it by taking a second kind of unit is the wrong one.

| Store | Unit | It belongs here when |
|---|---|---|
| `packages/*/src` | a **model field** | a machine can reject one document with it |
| `schemas/` | a **resource version** | never — rendered, never authored |
| `rules/records/*.yaml` | an **obligation with an immutable id** | an artifact author can violate it, and something needs to cite it by name |
| `census/areas/*.py` | a **prose site** — one field description or docstring | it exists under `analitiq.contracts`; membership is exhaustive, not chosen |
| `census/consumption/dispositions.py` | an **unread contract field** | the pinned consumption manifest claims no read of it |
| `census/consumption/record_affirmations.py` | a **reader-affirmed record rationale** | a rule record's `targets`/`fields` govern an unread field; membership is computed, never chosen |
| `scripts/render_validator_claims.py` | a **measured outcome** | prose asserts what the validator does or does not check |
| `packages/contract-models/tests/fixtures/rules/` | a **document** | a record names a `fixture_model` |
| `plugins/**/*.md` | a **paragraph of craft** | the contract cannot express it — judgment, order, what to ask, provider gotchas |
| `.claude/rules/*.md` | an **obligation on a contributor here** | its verdict needs a person reading a sentence |

The vendored engine grammar is a store too; nothing here may author it.

**The registry and the census are halves of one question.** The registry
catalogues rules; the census catalogues the sentences the contract publishes about
itself. Every registry check starts from a registered rule, so an obligation stated
in a field description and never registered was invisible — the census is what
makes that case fail. Its entries carry no prose of their own: a `prose_hash`
fingerprints the wording, and a disposition names what rejects a document that
ignores it — a rule id, the model's own shape, a written waiver, or `descriptive`
for a sentence asking nothing. Re-affirm with `scripts/render_prose_census.py`
write/check; `tests/census/test_prose_census.py` enforces it bidirectionally.

**The reachability census asks the same question of fields.** Nothing here can know
what the engine reads, so the engine publishes it: `contract-consumption`, a
versioned artifact at `schemas.analitiq.ai` listing the models it hands to its
run-time path (`roots`), the fields it reads by attribute (`claims`), and the models
it consumes whole as a JSON grammar (`opaque`). This repo vendors one pinned version
at `census/consumption/contract_consumption.json`, with the pin stated once in
`census/consumption/pin.py`. `census/consumption/reachability.py` walks the live
models from the roots through their field annotations, never descending into an
opaque model; every reachable field the manifest does not claim is *unread* and
carries a `FieldDisposition` in `census/consumption/dispositions.py` whose kind
(`census/consumption/disposition.py`) says what consumes it or which side owes the
fix. A model no root reaches is unknown, not unread. Guards:
`tests/census/test_contract_consumption.py`,
`scripts/render_contract_consumption.py check`, and the
`contract-consumption-pin-guard` CI job. Adopting a newer publication is a pin bump
— see `.claude/rules/reachability-dispositions.md` — never an edit to the manifest.

A rule record whose `targets`/`fields` govern an unread field carries a
`RecordAffirmation` in `census/consumption/record_affirmations.py`, pinned to the
refs located and the sha256 of the rationale wording a reader judged; membership is
computed, never chosen, and `census/consumption/records.py` owns that mechanism.
`tests/census/test_record_affirmations.py` gates the record half and the same script
prints its report. Judge one under the record-affirmation section of
`.claude/rules/reachability-dispositions.md`.

**Where a new fact goes.** Something a document must satisfy is a model field, and a
name for it is a record. Something an author must judge is plugin craft. Something a
contributor must do while editing this repo is `.claude/rules/`. A sentence added to
a contract field description is a census site whether or not you catalogue it — the
lint finds it either way.

## Single source of truth (drift policy)

The published schema is the single source of truth. **Never restate what it
defines — reference or load it.** Carry only craft the schema can't express
(judgment, idioms, gotchas, workflow). That splits everything into **contract**
(don't duplicate — field shapes, enums, vocabularies, `$schema` URLs) and **craft**
(keep — *how* to choose, the "why", provider gotchas). The mechanisms:

- **The live schema is the contract — enforce it, don't restate it.** The validator
  checks each document against the contract models **offline**, no runtime schema
  fetch, so authoring and validation agree on one contract.
- **The rule registry is the source of truth.** One machine-readable record per rule
  in `rules/records/*.yaml` (schema in `rules/SCHEMA.md`), with an immutable id;
  docs, rendered references and prose citations are generated from or validated
  against it, never the reverse. A record answers independently: `tier` (structural,
  advisory, referential, procedural, judgment), `validator` (what rejects a
  violation, absent when nothing does) and `severity`. Enforcement is ordinary
  Python: a rule one document settles alone is a `@model_validator` raising through
  `rules.violation`; a rule needing a second document in hand is a check in
  `analitiq.validator`, which is why that package exists. Nothing is dispatched from
  the record, so a rule is applied by a symbol that exists or by nothing at all.
  `scripts/render_rules.py` validates every record, resolves every `validator`
  against the live models and validator, and compiles the `rules.json` the wheel
  ships; `render_rule_reference.py` renders one reference file per artifact kind
  into each plugin, and `gen_pipeline_docs.py` renders the remaining contract-owned
  facts into its prose blocks. An obligation with no record is a missing record, not
  a sentence to hand-write.
- **Fetch-once, pass-down** — an orchestrator hands the live contract schema URLs to
  its researcher, and the creators read the same schemas as vocabulary.
- **Drift-check CI** for anything that must stay duplicated as decision logic (the
  `enum-mappers`, say): `tests/connector_builder/test_schema_drift.py` reads the enum
  sets from the pinned contract package and fails on divergence. The pipeline plugin
  solves the same problem by *generating* contract-owned facts into its prose. Prose
  about **what the validator does or does not check** is pinned by executable probes
  in `scripts/render_validator_claims.py`.

Enum lists in this file or in skill prose are **illustrative**; the authoritative
definition is always the live schema, or the vendored grammar for Arrow types.
Craft the schema never defined (the `ssl_mode` vocabulary, driver-selection order,
datetime naive/tz judgment) is not drift-exposed and stays.

## Authoring rules

**Never name a ticket or a pull request in anything this repo tracks** — comments,
docstrings, field descriptions, prose. Not a bare number, not a keyword-prefixed
one, not the cross-repo `org/repo` form, not a tracker URL, and not "the pull
request you are in", "this commit", or a review round. The exception is a surface
that IS a tracker surface: commit message bodies, PR descriptions and issue threads,
plus `CONTRIBUTING.md`, the two release-please changelogs, and
`.github/pull_request_template.md`.

**State what is true, never when it became true.** The file outlives the change that
wrote it, and the reader has the file, not the change — "creators are routed to their
spec skill", not "the wiring this change extended".

**A check may match text to LOCATE something. It may never match text to DECIDE
something.** Locating is lexical — a backticked identifier, a fenced block, a named
heading, a generated-block marker. Deciding is semantic: does this sentence assert
that the validator checks X. If the verdict needs to know what the English means, it
belongs in `.claude/rules/`, applied by a reader, not in a test. Hand-curated English
regexes and phrase lists are banned outright, whatever property they claim to
measure; `.claude/rules/guards.md` carries the argument.

`.claude/rules/` holds the invariants this policy implies, tracked so they reach
anyone with a clone (the rest of `.claude/` is local state). Read the one that
matches what you are editing:

- `no-drift-surfaces.md` — before hardcoding a value another source owns.
- `no-cardinality-restatements.md` — before writing how many members a shape has.
  Also owns closure claims — the "and nothing else" a set's enumeration ends with.
- `plugin-prose.md` — before editing any `.md` under `plugins/`, which ships verbatim
  to users and is executed by agents.
- `contract-prose.md` — before writing a field description or docstring under
  `analitiq.contracts`, which renders into an immutable published schema.
- `resolvable-referents.md` — before writing any pointer: a ticket, a path, a count,
  "the rule above".
- `guards.md` — before writing any check that reads prose this repo tracks.
- `engine-behaviour-claims.md` — before writing any sentence about what the engine
  does at run time, and before resting a check on one.
- `reachability-dispositions.md` — before writing or re-affirming a `FieldDisposition`
  under `census/consumption/`.

The first four and the last are keyed to the surface you are editing;
`resolvable-referents.md`, `engine-behaviour-claims.md` and
`no-cardinality-restatements.md` to a class of sentence that rots on any surface;
`guards.md` to the mechanism that reads them.

## Conventions

- JSON Schema Draft 2020-12 throughout.
- Test org_id: `d7a11991-2795-49d1-a858-c7e58ee5ecc6`.
- Agents must never author JSON that belongs to another agent's responsibility.
- The connector drift guards honour `DRIFT_REQUIRE_CONTRACT_MODELS=1`, which turns a
  missing contract package into a hard failure instead of an all-skipped green run.
  CI sets it.

## Releases and credentials

Each publishable artifact has its own tag prefix. The plugins are
release-please-managed — never bump a `plugin.json` version by hand. The packages are
released by hand as ONE PR, merged with a merge commit, never a squash. Publishing is
OIDC only — never add a static credential as a repo or environment secret, and never
use `pull_request_target` with a checkout of PR code.

Full procedure, commit-type rules and the `pypi` / `schemas` environment settings live
in the `releasing` skill (Claude Code tooling under an ignored directory, so it ships
with the maintainer's checkout and not with a clone). If you have it, invoke it by
name; if you do not, the environment settings are on the GitHub settings pages and the
rest is the four rules above.

## PR Review Process

After creating a PR, follow these steps. Continue invoking the PR review process until
no more errors are raised.

1. Use `/pr-review-toolkit` to review the PR after implementing all changes.
2. Wait for feedback from the review executor.
3. Determine whether each raised issue is legitimate.
   a. Legitimate and in scope — fix it.
   b. Out of scope — check the GitHub issue tracker for a related issue; if none, ask
      whether to file one, applying the **consolidation rule** in `CONTRIBUTING.md`
      first (three findings sharing one mechanism become one abstraction issue, and
      the instances close into it). Then move on.
   c. Not a real problem — summarize your thinking on the point and move on.
4. Commit the fixes and push to the branch.
5. Review again, and repeat until the review executor approves.
6. Run the tests and make sure they all pass.

`CONTRIBUTING.md` owns the consolidation rule and **close against the class, not the
instances** (what a PR must satisfy before it closes an issue). Read it before filing
an issue out of a review, and before closing one.
