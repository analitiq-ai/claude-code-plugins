# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What This Repo Is

A **monorepo holding one contract surface**: the Claude Code plugins end users
run to author Analitiq artifacts, and (as they land) the Python packages and
JSON Schemas that define what those artifacts must look like.

These are not independent projects that happen to share a directory. The plugin
prose, the Pydantic contract models, the validator, and the published JSON
Schemas are four expressions of one set of rules. They live together so a rule
changes in one place — every boundary between them would be a surface a human
has to keep in sync by hand.

This repo is also a **Claude Code plugin marketplace**. `.claude-plugin/marketplace.json`
declares the marketplace `analitiq-claude-code-plugins`; each entry's `source`
is a relative path into `plugins/`.

## Layout

```
.claude-plugin/marketplace.json   # marketplace catalog; one entry per plugin
plugins/
  analitiq-connector-builder/     # authors connectors + API endpoints
  analitiq-pipeline-builder/      # authors pipelines, streams, connections
packages/
  contract-models/                # -> analitiq-contract-models (PyPI); the contract
  validator/                      # -> analitiq-validator (PyPI)
schemas/                          # RENDERED public JSON Schemas -> schemas.analitiq.ai
scripts/
  render_schemas.py               # renders schemas/ from packages/contract-models
tests/
  connector_builder/              # suite per plugin; package suites live in packages/*/tests
  pipeline_builder/
conftest.py                       # puts packages/*/src on sys.path - see "The contract"
requirements-dev.txt              # runtime deps of the packages + pytest
```

**`schemas/` is generated, not authored.** It is rendered from
`packages/contract-models` by `scripts/render_schemas.py`; `render_schemas.py check`
re-renders and fails on any diff, and CI runs it. Never hand-edit a file under
`schemas/`. `canonical-types.json` is versionless and outside the registry but
still generated: `render_schemas.py canonical-types` builds it from the
vendored engine grammar (see "The canonical Arrow type vocabulary is
engine-owned" below), and `check` covers
it. Two files are exceptions, hand-authored and outside the registry:
`data-sync-api/openapi.json`, which has no version triple, and
`data-sync-run-response/1.0.0.json`, which is versioned but hand-maintained —
the publish treats every pinned `X.Y.Z.json` as immutable, so changing it means
renaming to a new triple, never editing in place. They are served only because
the publish workflow globs `**/*.json`, and `render_schemas.py check` never
inspects them.

**The canonical Arrow type vocabulary is engine-owned.** The set of type
families the platform executes is a capability surface owned by the engine, not
by this repo: analitiq-core publishes it as versioned artifacts
(`arrow-type-grammar`, `conversion-matrix`
at `schemas.analitiq.ai`), and this repo vendors one pinned grammar version at
`packages/contract-models/src/analitiq/contracts/arrow_type_grammar.json`.
`ARROW_TYPE_PATTERN`, the canonical-types `$defs`, and the container-head set
are all derived from it (`analitiq.contracts.arrow_grammar` states the pin —
version + sha256 — once). Both artifacts also self-declare their version in a
top-level `version` key, and each keeps its payload under a key of its own —
the grammar's families under `families`, the matrix's grid under `conversions`
(v2.0.0 moved it there). Always read those keys, never the document itself.
Guards: `test_arrow_grammar.py` re-hashes the vendored file offline, checks its
self-declared version against the pin, and pins the refusal to import a
manifest whose `families` is absent or empty; the `engine-grammar-pin-guard`
CI job byte-compares the vendored file against the published immutable object,
asserts the matrix's self-declared version directly and the grammar's via the
vendored copy those bytes are identical to, and cross-checks the
conversion-matrix family keys. A family is added by shipping it in the engine
first, then bumping the pin here (re-vendor, `render_schemas.py
canonical-types`, re-render the affected resources, re-run the plugin doc
generator) — never by hand-editing the vocabulary.

`.github/workflows/schemas-publish.yml` uploads the tree to the serving bucket
(defined in the infra repo's Terraform) on pushes to main touching `schemas/`.
The publish is additive — pinned `X.Y.Z.json` objects are first-write-wins
(byte-compared on re-runs; divergence fails the publish) and never overwritten,
nothing is ever deleted — and mutable pointers (`latest.json`, `index.json`,
and `data-sync-api/openapi.json`, the one hand-authored file with no version
triple) rely on a 5-minute TTL, not CloudFront invalidation. Auth is OIDC via the `schemas` environment — see "Credentials".

Only the 13 public resources render here. The ~40 internal-audience schemas stay
in the infra repo with the private half of the renderer;
`Resource.__post_init__` fails the build if a registered model tree reaches
outside `analitiq.contracts`.

**`plugins/<name>/` is a distribution artifact.** Its contents are copied
verbatim into every user's plugin cache when they install. Tests, scratch
output, and CI config do not belong inside it — that is why `tests/` sits at
the repo root, namespaced per plugin, rather than under each plugin.

Each plugin carries its own `CLAUDE.md` with its agents, skills, and authoring
rules. Read `plugins/<name>/CLAUDE.md` when working inside that plugin; this
file covers only what spans both.

## The contract, and the runtime pin

This repo is the **source** of `analitiq-contract-models` and
`analitiq-validator` (`packages/*/src`). The version of record is each package's
`pyproject.toml`; the two move in lockstep, enforced by
`packages/validator/tests/test_contract_models_pin.py`.

**Never `pip install` those two packages into a dev environment.** A built wheel
ships a generated `analitiq/contracts/__init__.py`, making it a *regular*
package, while the in-repo tree deliberately has none, making it a *namespace
portion* — and a regular package wins regardless of `sys.path`. An installed copy
therefore silently shadows the source and the suite grades the wrong code. The
repo-root `conftest.py` puts both source trees on the path;
`requirements-dev.txt` carries only their runtime deps.
`test_suite_exercises_in_repo_source_not_an_installed_wheel` guards this.

Separately, the plugins **self-install a published release at runtime** — end
users have no checkout, so the pin must name a version already on PyPI.
**`VALIDATOR_PIN` in `plugins/analitiq-pipeline-builder/scripts/_bootstrap.py` is
the only place that version is stated.** Never restate it — not here, not in a
README. The one unavoidable second copy is the self-install line in
`plugins/analitiq-connector-builder/agents/connector-schema-validator.md`
(prose an agent runs, so it cannot import the constant); it is pinned to
`VALIDATOR_PIN` by `test_connector_validator_agent_states_the_same_pin`.

The pin must be **at or behind** `packages/validator/pyproject.toml`
(`test_validator_pin_matches_the_package_this_repo_ships`). Equal is allowed and
is the steady state; behind is tolerated because the pin names a version that
must already be **on PyPI**, and the publish is a hand-pushed tag that fires
*after* the version bump merges. The dangerous direction is a pin **ahead** of
what this repo ships: marketplace installs track main HEAD, so a pin naming an
unpublished version means every user's `pip install` fails outright and the
plugin cannot run at all.

`PINNED_VERSION` in `tests/connector_builder/_pins.py` is a *different* value —
what this repo **ships** (`packages/contract-models/pyproject.toml`), not what
the plugins install — so it runs ahead of the pin during a release window.

`scripts/check_validator_pin_contract.py` (CI job `pinned-validator-guard`)
guards the pin from the other side: it installs the pinned release into an
isolated venv and fails if that **published** wheel rejects the canonical
`dialect+driver` values the plugin prose teaches. Its docstring owns the full
semantics — the strictness windows, the exit codes, and the live-settings caveat
that no branch protection currently requires the check.

Running a plugin helper from a checkout would otherwise trigger the bootstrap
(build a venv, install the published wheel, `os.execv` into it). The root
conftest sets `ANALITIQ_VALIDATOR_FROM_SOURCE=1` to short-circuit that; without
it, the bootstrap would replace the pytest process mid-run.

## Single source of truth (drift policy)

The published schema is the single source of truth. **Never restate what it
defines — reference or load it.** Carry only craft the schema can't express
(judgment, idioms, gotchas, workflow). That splits everything into **contract**
(don't duplicate — field shapes, enums, vocabularies, `$schema` URLs) and
**craft** (keep — *how* to choose, the "why", provider gotchas). Three mechanisms:

- **The live schema is the contract — enforce it, don't restate it.** The
  validator checks each document against the contract models
  (`analitiq-contract-models`, the same models the published JSON Schemas are
  generated from) **offline** — no runtime schema fetch — so authoring and
  validation agree on one contract. Where a plugin must restate a schema-owned
  enum as decision logic (e.g. the DSN-binding `encoding` set), the drift-check
  CI below pins it to the pinned contract models.
- **The rule registry is the source of truth.** The registry contains one
  machine-readable record per rule, with an immutable ID, where everything else
  (docs, checker manifests, prose citations) is generated from or validated
  against that record. The registry is the source of truth; prose is commentary
  that cites it, never the reverse.

  It lives in `rules/adv/*.yaml`, one file per rule, schema in
  `rules/SCHEMA.md`. A record carries three independent axes — `tier` (what
  kind of rule: structural, advisory, referential, procedural, judgment),
  `mechanized`/`checker` (whether anything here rejects a violation, and what),
  and `severity` (what a violation costs) — plus `data` for its parameters, so
  the statement never restates a value the contract owns.

  `scripts/render_rules.py` validates every record, resolves every `checker`
  against the live models, and compiles `analitiq/contracts/shared/rules.json`,
  the copy the wheel ships. `render_advisory.py` and the pipeline plugin's
  `gen_contract_docs.py` render the registry into each plugin's prose. An
  obligation with no record is a missing record, not a sentence to hand-write;
  a citation that stops resolving fails the build.
- **Fetch-once, pass-down** — an orchestrator hands the live contract schema URLs
  to its researcher (the mission spec) and the creators read the same schemas as
  vocabulary, so authoring and validation agree on one contract.
- **Drift-check CI** for anything that must stay duplicated as decision logic
  (e.g. the `enum-mappers` that map provider facts onto schema enums):
  `tests/connector_builder/test_schema_drift.py` reads the enum sets from the
  pinned `analitiq-contract-models` package and fails the build if a plugin's
  enum targets diverge. The pipeline plugin solves the same problem by
  *generating* contract-owned facts into its prose — see its `CLAUDE.md`.
  Prose statements about **what the validator does or does not check** are a
  fourth surface, pinned by executable probes in
  `scripts/render_validator_claims.py` — see the connector plugin's
  `CLAUDE.md` ("Validator-behavior claims").

Enum lists appearing in this file or in skill prose are **illustrative**; the
authoritative definition is always the live schema (or, for canonical Arrow
types, the engine-published grammar manifest vendored and pinned in
`analitiq.contracts.arrow_grammar` — see "The canonical Arrow type vocabulary
is engine-owned" above). Craft the schema never defined (the `ssl_mode`
vocabulary, the driver-selection decision order, datetime naive/tz judgment) is
not drift-exposed and stays.

## Authoring rules

**Never name a ticket or a pull request in anything this repo tracks** —
comments, docstrings, field descriptions, prose. Not a bare number, not a
keyword-prefixed one, not the cross-repo `org/repo` form, not a tracker URL, and
not "the pull request you are in", "this commit", or a review round.

The exception is a surface that IS a tracker surface: commit message bodies, PR
descriptions and issue threads, plus four tracked files whose subject is the
tracker itself — `CONTRIBUTING.md`, which teaches the consolidation rule by
walking real issues; the two release-please changelogs, whose entries link into
the tracker and which are machine-written anyway; and
`.github/pull_request_template.md`, where "this PR" is the runtime subject
rather than a referent that expires. `.claude/rules/resolvable-referents.md`
names the forbidden shapes in the course of forbidding them, which is the same
exception one level up.

The file outlives the change that wrote it, and the reader has the file, not the
change. So state what is true, never when it became true — "creators are routed
to their spec skill", not "the wiring this change extended".
`.claude/rules/resolvable-referents.md` is the checklist for both halves.

**A check may match text to LOCATE something. It may never match text to DECIDE
something.** Locating is lexical — a backticked identifier, a fenced block, a
named heading, a generated-block marker, a name the contract owns, a probe id
resolved against its registry. Deciding is semantic: does this sentence assert
that the validator checks X, does this paragraph still teach the rule. If the verdict needs to know
what the English means, it belongs in `.claude/rules/`, applied by a reader, not
in a test. `.claude/rules/validator-claims.md` owns this rule and the worked
cases; the short version is that hand-curated English regexes and phrase lists
are banned outright, whatever property they claim to measure.

Two reasons observed here rather than predicted, and one that follows from
them. A phrase pin reddens the build when prose is *improved*, and the failure
it prints asks the author to reword it back. It cannot read polarity: a document
saying a shape is fine satisfies a substring check exactly as well as one
forbidding it. Between them those make its coverage undecidable — the rule file
carries that argument and the waiver-registry one in full.

`.claude/rules/` holds the how-to-behave checklists this policy implies, tracked
so they reach anyone with a clone (the rest of `.claude/` is local Claude Code
state and stays ignored). Read the one that matches what you are editing:

- `no-drift-surfaces.md` — before hardcoding a value another source owns.
- `no-cardinality-restatements.md` — before writing how many members a shape
  has. Counts are the one restatement class every guard here is blind to. Also
  owns closure claims — the "and nothing else" a set's enumeration ends with.
- `plugin-prose.md` — before editing any `.md` under `plugins/`, which ships
  verbatim to users and is executed by agents.
- `contract-prose.md` — before writing a field description or docstring under
  `analitiq.contracts`. It renders into a published schema, and a published
  `X.Y.Z.json` is immutable. Choosing its census disposition is the judgment
  the census itself cannot make.
- `resolvable-referents.md` — before writing any pointer: a ticket, a path, a
  count, "the rule above". The PR template asks you to attest you applied it.
- `validator-claims.md` — before writing a sentence about what a tool checks or
  refuses, and before writing any check that reads prose.

## Conventions

- JSON Schema Draft 2020-12 throughout.
- Test org_id: `d7a11991-2795-49d1-a858-c7e58ee5ecc6`.
- Agents must never author JSON that belongs to another agent's responsibility.

## Tests

The connector drift guards honour
`DRIFT_REQUIRE_CONTRACT_MODELS=1`, which turns a missing contract package into a
hard failure instead of an all-skipped green run — CI sets it so the gate can
never pass without actually running.

## Releases and credentials

Four artifacts, four tag prefixes. The two plugins are release-please-managed —
never bump a `plugin.json` version by hand. The two packages are released by
hand as ONE PR, merged with a merge commit, never a squash. Publishing is OIDC
only — never add a static credential as a repo or environment secret, and never
use `pull_request_target` with a checkout of PR code.

Full procedure, commit-type rules, and the `pypi` / `schemas` environment
settings live in the `releasing` skill — Claude Code tooling under an ignored
directory, so it ships with the maintainer's checkout and not with a clone. If
you have it, invoke it by name; if you do not, the environment settings are on
the GitHub settings pages and the rest is the four rules above.

## PR Review Process

After creating a PR, follow these steps. Continue invoking the PR review process
until no more errors are raised. If raised errors are not relevant to the PR, ask
if you should create a GitHub issue for the raised error.

`CONTRIBUTING.md` owns the two rules this loop does not: the **consolidation
rule** (three findings sharing one mechanism become one abstraction issue —
governs step 3b below) and **close against the class, not the instances** (what
a PR must satisfy before it closes an issue). Read it before filing an issue out
of a review, and before closing one.

1. Use `/pr-review-toolkit` to review the PR after you have implemented all changes.
2. Wait for feedback from the review executor.
3. Determine if the raised issues are legitimate or not.
   a. if the issue is legitimate and relevant to the PR, fix it.
   b. if the issue is outside the scope of the PR, check if there is a related
      issue in the GitHub issue tracker. If not, ask whether to file one — and
      when filing, apply the consolidation rule in `CONTRIBUTING.md` first: if
      this is the third leak from a mechanism already filed, file the
      consolidation issue and close the instances into it rather than adding
      another instance. Then move on.
   c. If the issue is not a legitimate problem, summarize your thoughts on the
      point and move on.
4. Once you fixed all issues that need fixing, commit fixes, push to the branch.
5. Use `/pr-review-toolkit` to review again.
6. Continue doing this cycle until the PR is approved by the review executor.
7. Once the PR is approved, run the tests to make sure they all pass.
