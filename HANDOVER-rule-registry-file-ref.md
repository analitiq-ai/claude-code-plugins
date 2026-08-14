# Handover — rule registry reference split by scope

**Branch:** `rule_registry_file_ref` · **Base:** `c626d856` (main, 2026-08-13)
**Delivery:** `rules-scope-split.patch` at the repo root — one commit, 318 files.

```bash
git am < rules-scope-split.patch && rm rules-scope-split.patch
```

The work was done in a clean clone in a cloud sandbox because this session's
mount of the local folder failed. **The test suite never ran** — see
[Verification](#verification) before merging.

---

## Contents

- Why
- What changed
- Verification
- What still needs doing
- Design decisions and where they are recorded
- Reference

---

## Why

Two plugins solved the same problem two ways, and both diverged from the
published skill-authoring guidance
(<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices>):

| | before | guidance |
|---|---|---|
| connector | one 277-line `references/rules.md`, all 195 owned rules | separate file ✅, but read-all-or-nothing for a 195-row table |
| pipeline | rule tables inlined into 6 documents | tables >10 rows belong in a separate file ❌ |
| `pipeline-builder/SKILL.md` | 497 lines | ≤ 500 ⚠️ |
| pipeline rule index | none — an id cited outside its block resolved nowhere | — |

The premise driving the shape: **every rule is load-bearing**. A reader must be
able to satisfy the document in front of them from one file, without deciding
which of several files to open.

## What changed

### 1. `scope` → `scopes` (schema)

`packages/contract-models/src/analitiq/contracts/shared/rule_record.py`

`scope: str` became `scopes: tuple[str, ...]`, joining `owners`/`targets`/`fields`
in `_SEQUENCES`. All 270 records migrated (`scope: x` → `scopes: [x]`), and
`scripts/render_rules.py` emits the list in canonical `SCOPES` order.

Five refusals, each exercised directly against `load_registry`:

| input | refused with |
|---|---|
| `scopes: [nonsense]` | `unknown scope(s)` |
| `scopes: []` | `name the artifact kind(s) this rule binds` |
| `scopes: connector` | `is a list of names, not the string` |
| `scopes: [connector, connector]` | `scopes repeats an entry` |
| `scopes: [any, connector]` | `'any' already covers every authored document` |

And the capability this was for: `scopes: [connector, api-endpoint]` renders
into both files, so both authors meet the rule. Under the scalar, one of them
silently did not.

**No record names an agent or a document.** `owners` decides which plugin's set
a rule joins; `scopes` decides which file inside it. Agent names in a record
would put the plugin's agent roster into the wheel the engine installs, and
every new subagent would mean editing YAML.

### 2. One generator, one file per scope

`scripts/render_rule_reference.py` — was connector-only with module-level
`PLUGIN_OWNER` / `OUTPUT_PATH`; now serves both plugins from `OUTPUT_DIRS`.

```
skills/<skill>/references/rules/
  connector.md  api-endpoint.md  database-endpoint.md
  connector-package.md  type-map.md  shared.md
```

- **13 files**, 94–160 lines each, Contents section in every one.
- **`BUCKET_FLOOR = 5`** — a scope with fewer owned rules folds into
  `shared.md`, which also absorbs any `SCOPES` member the renderer has never
  seen, so a new scope surfaces rather than vanishing.
- **`any` is not a bucket.** Those rules append to *every* file, because the
  premise is that one file is the whole obligation. 13 duplicated rows per
  connector file, 15 per pipeline file — generated, so a rendering duplicate,
  not a second source.
- **Coverage assertion replaces "exactly once".** That invariant cannot hold
  once a rule names two scopes. `render_all` raises when an owned rule reaches
  no file — that is an id prose can cite and no agent can resolve.
- **`Checked` column**, read off `mechanized`. 115 of 270 rules have no
  validator; each file's header counts its own.
- `write` mode deletes files for buckets that stopped existing; `check` reports
  them as stale.

Both plugins' sets are rendered by this one script. `--plugin=<owner>` narrows it.

### 3. Pipeline plugin: inline tables retired, generator moved to repo root

`plugins/analitiq-pipeline-builder/scripts/gen_contract_docs.py` moved to
`scripts/gen_pipeline_docs.py` and lost
`_RULE_BLOCKS`, `_block_rules()`, `_rule_block()` and the six
`render_rule_reference_*` wrappers — 79 lines. Everything else it generates
(enums, regexes, bounds, defaults, probes) is untouched.

Rule blocks stripped from six documents; each orphaned "satisfy every rule
below" lead-in repointed at the file that now carries them:

`connection-spec/SKILL.md` · `pipeline-spec/SKILL.md` · `stream-spec/SKILL.md` ·
`endpoint-spec/spec-columns.md` · `endpoint-spec/spec-type-map-gaps.md` ·
`pipeline-builder/SKILL.md`

Moving it out of the plugin is what keeps the two plugins on **one** contract
source. Inside the plugin it bootstrapped the published pin
(`analitiq-validator==1.0.0rc21`) while `render_rule_reference.py` reads
`packages/*/src`, so the pipeline plugin would have taken its rules from the
repo and its enums from the wheel whenever the pin lagged. At repo root it
bootstraps the in-repo trees like every other renderer, and the CI step no
longer needs `ANALITIQ_VALIDATOR_FROM_SOURCE=1`.

`_bootstrap.py` stays in the plugin for `validate.py`, `endpoint_id.py` and
`type_map_gaps.py` — those are user-facing and legitimately want the published
pin. `gen_pipeline_docs.py` was the only maintainer tool in there.

**`pipeline-builder/SKILL.md`: 497 → 475 lines.**

### 4. Wiring and the compliance loop

Both `pipeline-builder/SKILL.md` and `connector-builder/SKILL.md` open with a
**Registered rules for every document** section: the file index, one level deep,
plus the loop.

```
- [ ] 1. Read the rule file for the document being authored
- [ ] 2. Author it
- [ ] 3. Run <plugin>-schema-validator; fix findings; repeat until clean
- [ ] 4. Re-read the rule file and confirm every row whose Checked column
        reads `—`; nothing rejects those, so step 3 says nothing about them
```

Step 4 is the point. Placement does not make a rule load-bearing — the
validator loop does, and for the 115 rules it cannot reach, a read-and-compare
pass is the only thing that does.

15 prose cross-references repointed by artifact: `endpoint-creator` →
`api-endpoint.md`, `db-connector-creator` → four files, `api-connector-creator`
→ two, and so on. `${CLAUDE_PLUGIN_ROOT}` anchoring preserved.

### 5. Guards

**New** — `tests/registry/test_rule_files_are_reachable.py`

- every rule file must be named by a SKILL.md or agent definition (a file
  reachable only through another reference gets previewed, not read)
- non-vacuity per plugin
- no SKILL.md over 500 lines

**Updated**

| file | change |
|---|---|
| `test_rule_reference_sync.py` | grades the whole set per plugin; catches stale, missing and orphaned files; new `test_every_owned_rule_reaches_a_file` |
| `test_rule_reachability.py` | asks the one renderer for both owners; the per-plugin `rendered_ids` shim is gone |
| `test_gen_pipeline_docs.py` | two block-partition tests removed (41 lines); renamed with the generator |
| `test_prose_vocabulary.py` | `rules-*` dropped from `REQUIRED_BLOCKS` |
| `test_prose_fences.py` / `test_prose_snippets.py` | resolve a marker to the scope naming a hosted document; ambiguity now fails loudly |
| `test_render_rules.py` | six new scope tests; `scope` removed from the two scalar-vocabulary parametrizations |

**Moved** — `test_rule_reference_sync.py`, `test_rule_reachability.py` and
`test_rule_files_are_reachable.py` now live in `tests/registry/`. They grade the
registry across both plugins; sitting under `tests/connector_builder/` said
otherwise. `test_gen_contract_docs.py` → `test_gen_pipeline_docs.py`.

**Docs** — `rules/SCHEMA.md` row rewritten for `scopes`; `CLAUDE.md` updated to
describe the split; `.github/workflows/tests.yml` comments repointed.

---

## Verification

**Passing** — all seven offline generators in `check` mode:

```
render_rules · render_rule_reference · render_reference_toc · render_prose_census
render_schemas · render_validator_claims · gen_pipeline_docs --check
```

Reachability run by hand: connector cites 195 ids, pipeline 119, **zero
unresolvable in either**. All 13 rule files reachable from a SKILL.md. No
SKILL.md over 500 lines. Every changed `.py` compiles.

**NOT run — do this first**

1. **`pytest`.** Not installable in the sandbox (no PyPI). Five test modules
   were edited without ever executing them. This is the single largest risk in
   the change.
2. **`check_engine_grammar_pin`** — needs `schemas.analitiq.ai`.
3. **`check_validator_pin_contract`** — needs to pip-install the pin.
4. **The eval scenarios** under `evals/scenarios/`. They reference rule ids;
   worth a before/after comparison since the prose an agent reads has moved.

---

## What still needs doing

### Before merge

- [ ] Run the suite. Expect fallout in the five edited modules, most likely
      `test_prose_fences.py` / `test_prose_snippets.py`, where the
      marker-to-entity resolution changed shape.
- [ ] Run the two network guards.
- [ ] Read one generated file end to end — e.g.
      `connector-builder/references/rules/api-endpoint.md` — and check the
      prose around the tables still reads correctly now that the tier intros
      are repeated per file rather than appearing once.
- [ ] Check the `.claude/rules/` prose guards (no-restatement,
      no-cardinality-restatements) still pass over the new files. They are
      enforced by tests that did not run here.

### Deliberately left out

- [ ] **Redirect stub at the old `references/rules.md` path.** One file, avoids
      breaking any external link. Skipped because nothing in-repo points there
      any more.

### Worth considering later

- [ ] `BUCKET_FLOOR = 5` is a judgement call. Connector's `shared.md` currently
      holds two real rules (one `connection`, one `stream`) plus the 13 `any`
      rules. If either scope grows past five it gets its own file automatically.
- [ ] No rule uses multi-scope yet — all 270 records are single-scope. The
      capability is in place for the case you raised; the first real
      two-scope rule will exercise the fan-out for the first time.
- [ ] Per-agent rule files were considered and rejected. `scopes` already maps
      near 1:1 onto the creator agents (`endpoint-creator` ↔ `api-endpoint`,
      `stream-creator` ↔ `stream`, …), and an `authors:` field would be a third
      axis stating a fact the first two already imply.

---

## Design decisions and where they are recorded

Each is argued in a docstring or comment at the point it binds, not here:

| decision | where |
|---|---|
| why `scopes` is plural | `rule_record.py`, `SCOPES` docstring and the field comment |
| why `owners` never names an agent | `render_rule_reference.py`, `OUTPUT_DIRS` comment |
| why `any` appends to every file | `render_rule_reference.py`, `ANY_SCOPE` comment |
| why the floor exists | `render_rule_reference.py`, `BUCKET_FLOOR` comment |
| why coverage replaced uniqueness | `render_all()` docstring |
| why the example id is derived, not typed | `render()`, above `example =` |
| why one level deep, why 500 lines | `test_rule_files_are_reachable.py` module docstring |

## Reference

**Registry today** — 270 rules · connector-plugin owns 195, pipeline-plugin 119,
engine 206 · 155 mechanized, 115 not.

**Rules per file** (owned + `any`):

| connector-plugin | | pipeline-plugin | |
|---|---|---|---|
| `connector.md` | 81 | `stream.md` | 52 |
| `api-endpoint.md` | 71 | `type-map.md` | 36 |
| `connector-package.md` | 41 | `pipeline.md` | 32 |
| `type-map.md` | 32 | `database-endpoint.md` | 28 |
| `database-endpoint.md` | 20 | `connection.md` | 23 |
| `shared.md` | 15 | `api-endpoint.md` | 20 |
| | | `shared.md` | 18 |

**Both plugins, one mechanism.** After this change every generator bootstraps
`packages/*/src` the same way and lives at repo root:

| | connector-plugin | pipeline-plugin |
|---|---|---|
| rule renderer | `scripts/render_rule_reference.py` | same script, `--plugin=pipeline-plugin` |
| contract source | `packages/*/src` | `packages/*/src` |
| output shape | `references/rules/<scope>.md` | `references/rules/<scope>.md` |
| other generated blocks | `render_validator_claims.py` | `scripts/gen_pipeline_docs.py` |
| rule guards | `tests/registry/` | `tests/registry/` |

**Regenerate everything:**

```bash
export DOMAIN=analitiq.ai
export PYTHONPATH=packages/contract-models/src:packages/validator/src
python3 scripts/render_rules.py write
python3 scripts/render_rule_reference.py write          # both plugins
python3 scripts/render_reference_toc.py write
python3 scripts/gen_pipeline_docs.py
```
