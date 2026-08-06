---
paths: plugins/**/*.md
---

# Rule: what may enter plugin prose

Applies when editing any `.md` under `plugins/` — agent definitions, skills,
references, READMEs. These are distribution artifacts: agents execute them
verbatim, so a wrong sentence ships wrong authoring behavior to every user. The
*why* is the root `CLAUDE.md` → "Single source of truth (drift policy)"; the
general checklist is `no-drift-surfaces.md`. The failure class this rule
prevents: prose restating validator behavior, falsified by a `VALIDATOR_PIN`
bump.

## Classify every sentence: craft or fact

- **Craft** — judgment no schema expresses: when to apply a rule, what to ask
  the user, provider gotchas, orchestration order, what the plugin refuses.
  Hand-write it freely.
- **Fact** — anything a source of truth owns: field shapes, enums, patterns,
  bounds, defaults, `$schema` URLs, validator behavior — **including what it
  does NOT check** — engine/CDK behavior, version-coupled claims ("planned, not
  registered", "older releases…"). Negative and version-coupled claims are the
  most rot-prone facts, not exemptions from being facts.
- The test: could a contract release, a `VALIDATOR_PIN` bump, or an engine
  release falsify this sentence with nobody editing it? Then it is a fact, and
  it never enters prose by hand.

## Facts arrive by this ladder — stop at the first rung that fits

1. **Cite, don't state.** An `ADV-*` id, the generated advisory reference, a
   schema URL, a path to a validated `examples/` file. Citation is the pinned
   form of repetition — a dangling `ADV-*` id already fails the build.
2. **A "validates clean but breaks" warning is a validator gap.** Raise the
   contract gap; when the rule lands, its `ADV-*` entry carries the fact and
   the warning is deleted. Only a refused gap drops to rung 3.
3. **Generate it** into a `BEGIN GENERATED` block from the pinned packages —
   constants and models read directly, behavioral facts derived by probing the
   validator with minimal documents, never hand-measured. One block id rendered
   into several files is the sanctioned way to repeat a fact.
4. **Pin it hard.** A fact that must stay hand-typed (the exemptions listed
   under checklist item 2 of `no-drift-surfaces.md` — decision/mapping logic, a
   test's assertion target, a curated human-facing summary) needs a test that
   reads **this file at this
   site**. A test comparing the contract to its own constant, naming the prose
   only in a failure hint, pins nothing — the prose can rot while it passes.
5. **Declare the exemption.** A fact with no model in this repo (CDK surface,
   engine runtime behavior) is backed by a pinned vendored artifact (the
   `arrow_grammar` pattern) or by an allowlist entry in the relevant drift test
   naming the copy and why (the `ALLOWED_RESTATEMENTS` pattern).
   Unpinned-and-undeclared is the defect.

## Placement

- A fact binds only where the authoring agent reads it: its own definition, or
  a skill its instructions load. Correct content in a reference no agent opens
  teaches nothing.
- One canonical prose site per fact; every other mention is a citation (rung 1)
  or the same generated block (rung 3). When correcting a fact, fix the
  canonical site and every citing file in one commit.

## Fenced examples and excerpts

- Prefer a path to a file under `examples/` — CI runs every example through the
  validator, with a glob-coverage guard, making it a *pinned* copy rather than a
  drift surface. Pinned only as strongly as the validator is strict, though: an
  example inherits every validator blind spot (rung 2 tightens all examples for
  free); the gate grades in-repo source while users run the published pin, so
  hold new-shape examples back with the prose until `VALIDATOR_PIN` moves; and
  validity never proves the example still shows the recommended shape — that
  residue is review-owned craft.
- An example that is the *output of code* (derived ids, rendered DSNs) is
  computed by calling the real code, never transcribed.
- An inline `json` fence is either generated (transcluded from a validated
  file) or annotated with its verification contract:
  `validate: <resource>` (full document, run through the validator) ·
  `validate: <resource>#/<pointer>` (fragment, against the sub-model) ·
  `invalid: <ADV id>` (deliberately wrong, asserted to fail validation — a
  "don't do this" example that rots into valid is the most misleading rot
  there is; that the failure is the named rule's diagnostic stays
  review-enforced, since the validator reports model messages, not rule ids)
  · `illustrative` (explicit, reviewable exemption).
  Enforcement is split by plugin: for the pipeline plugin the annotation is
  machine-enforced by `tests/pipeline_builder/test_prose_snippets.py`, which
  grades every fence under that plugin's tree; for the connector plugin it
  is review-enforced until a matching gate lands — write it anyway so that
  gate can adopt existing fences without a sweep.
- Prefer minimal-complete documents over fragments — a fragment costs a harness
  or annotation forever. Fenced Python (CDK excerpts) must at least parse;
  anything beyond that is rung 5.

## Economy

Prose is loaded into an agent's context and executed, not read at leisure —
every sentence spends budget the authoring task needs, and every sentence is
review surface and potential rot.

- State each point once, in the fewest words that carry the obligation or the
  judgment. No hedging, no paraphrase of a neighboring sentence, no synonym for
  a term the contract already names.
- Keep a "why" only when it changes what the agent does next; rationale that
  does not alter behavior belongs in the commit message or the ADV registry,
  not in agent prose.
- A sentence that adds no new obligation, judgment, or example is deleted, not
  polished.

## Guard hygiene

- Extractors assert non-vacuity: zero matched citation/fence/example sites is a
  red build, not a silent exemption.
- Failure-message fix-hints are part of the guard: a hint naming a file or
  section that no longer carries the fact is a defect. Repoint hints in the
  same commit that moves a fact.
- **A guard reads marked text or a token, never a sentence.** The rule and its
  reasons are in the root `CLAUDE.md` → "Authoring rules"; what it means here is
  that a guard over plugin prose extracts backticked identifiers, fenced blocks,
  a named heading or a generated-block marker, and hands the verdict to the
  contract. If the check you want needs to read the English, it is a review
  item, not a test — the section below names the ones this repo has.

## Does the document still TEACH it?

Read for it. When you touch a plugin document, check each of these:

- **A rule the contract still needs stated.** `stream-creator.md` must rule out
  the dotted-string `get` path, because the array-of-segments shape is the one
  an agent will not guess. `test_prose_authoring_rules.py` asserts the contract
  half — `path` is still a list — and stops there.
- **A worked example beside a rule an agent must apply.** A rule with no example
  reads as an assertion to take on faith, and agents author accordingly.
- **A claim some guard reasons FROM.** `enum-mappers.md` reasons from a database
  endpoint carrying no `replication` block. Reword that away and
  `test_prose_absence_claims.py` sits green protecting a claim nobody makes; it
  can only check the token `replication` is still somewhere in the document.

The pattern: where a test pins a contract fact and the prose is what teaches it,
the test guards the fact and **you** guard the teaching.

## Quick test

For every new or changed sentence: **which rung carries this, and is it the
fewest words that carry it?** Craft needs no rung. A fact you cannot place on
one is the review finding you are about to receive — and after the next pin
bump, the wrong guidance an agent will follow.
