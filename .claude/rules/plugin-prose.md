---
paths:
  - "plugins/**/*.md"
  - "rules/records/*.yaml"
---

# Rule: what may enter plugin prose

Applies when editing any `.md` under `plugins/` — agent definitions, skills,
references, READMEs. These are distribution artifacts: agents execute them
verbatim, so a wrong sentence ships wrong authoring behavior to every user. The
*why* is the root `CLAUDE.md` → "Single source of truth (drift policy)"; the
general checklist is `no-drift-surfaces.md`. The failure class this rule
prevents: prose restating validator behavior, falsified by a `VALIDATOR_PIN`
bump.

It applies to a rule record's `statement` for the same reason and by the same
route: `render_rule_reference.py` copies that sentence verbatim into the
plugin references, so a statement is plugin prose that has not been pasted
yet. Write it here and the rendered `.md` inherits it — including the defect,
which arrives in a generated block no one may hand-edit.

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

1. **Cite, don't state.** A `RULE-*` id, the generated rule reference, a
   schema URL, a path to a validated `examples/` file. Citation is the pinned
   form of repetition — a dangling `RULE-*` id already fails the build.

   The registry carries a rule whether or not anything applies it, so "no rule
   enforces this" is not a reason to restate a fact. Each record answers these
   separately: `tier` says what kind of rule it is, `validator` names what
   applies it and is absent when nothing does, and `severity` says what a
   violation costs. Tier is the rule's nature, not its enforcement — an
   `advisory` rule states that fields within one document must agree, a
   `structural` one that an artifact has a given shape, and either may be
   applied by something or by nothing. Those ids exist so prose stops copying
   enum members and patterns, and the rendered reference prints the members off
   the live model. A rule with no validator is applied by nobody here, and its
   `rationale` says what would have to be read to catch a violation.

   So an obligation with no id is a **missing registry entry**, not a licence
   to hand-write it. Add `rules/records/<id>.yaml` (schema: `rules/SCHEMA.md`), name
   this plugin in its `owners` so the reference renders it here, run
   `python3 scripts/render_rules.py write`, then cite it. What stays in prose
   beside the citation is the craft the record deliberately does not carry: the
   worked example, the consequence of getting it wrong, the decision procedure.
2. **A "validates clean but breaks" warning is a validator gap.** Raise the
   contract gap; when the rule lands, its `RULE-*` entry carries the fact and
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

## A sentence about what the validator checks

The most rot-prone fact class, and the one that adds a rung the ladder above
does not carry. A sentence stating what the validator **does or does not** check
takes the first of these that fits:

1. **A generated block** (rung 3), rendered by
   `scripts/render_validator_claims.py` — it moves with the measurement by
   construction, so prefer it whenever a whole section states validator
   behaviour.
2. **A probe fence.** Add a `Claim`/probe to that script and put
   `<!-- PROBE: <id> -->` directly above the sentence. Gated both ways: a fence
   naming an id no probe defines fails the build, and a probe nothing references
   fails the build. Nothing checks that the sentence beside the fence describes
   what the probe measures — that half is yours.
3. **Cite the `RULE-*`** that enforces the behaviour (rung 1).
4. **Do not make the claim.** "The validator does not check this" is rarely
   load-bearing guidance; state what the author must do instead.

Recognising that a sentence makes such a claim is the author's job: deciding it
from the wording took a list of hand-curated English regexes, which `guards.md`
bans and explains. Pin the claim in the commit that writes it. On the contract's
own surface the same sentence takes a census disposition instead —
`contract-prose.md`.

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
- Prefer minimal-complete documents over fragments — a fragment costs a harness
  or annotation forever. Fenced Python (CDK excerpts) must at least parse;
  anything beyond that is rung 5.

### The annotation convention

An inline `json` / `jsonc` fence is either generated (transcluded from a
validated file) or carries an HTML comment directly above it declaring how it is
verified. This is the normative home of that vocabulary, and it is the same in
both plugins:

- `<!-- validate: <resource> -->` — a complete document; must validate against
  that resource's contract.
- `<!-- validate: <resource>#/<pointer> -->` — a fragment, spliced into a host
  document at that pointer, so it is graded with the context around it. A
  fragment may show its enclosing key for context, braces or not
  (`"replication": { … }`); the pointer names the deepest shown node, and the
  gate unwraps the key before splicing.
- `<!-- invalid: <RULE id> -->` — deliberately wrong; the graded document must
  FAIL validation. A "don't do this" example that rots into valid is the most
  misleading rot there is, so that half is asserted. That the failure is the
  named rule's diagnostic stays review-enforced: the validator reports model
  messages, not rule ids.
- `<!-- illustrative -->` — outside the published contract's validation surface
  (plugin-internal I/O envelopes, shape sketches); an explicit, reviewable
  exemption, still required to parse.

Each plugin's tree is graded by its own gate —
`tests/connector_builder/test_prose_fences.py` and
`tests/pipeline_builder/test_prose_snippets.py`, whose docstrings own how that
gate hosts and splices a block. An unannotated fence fails the build, and the
gate classifies each block from its marker alone, so no registry of dispositions
can drift from the prose.

## Economy

Prose is loaded into an agent's context and executed, not read at leisure —
every sentence spends budget the authoring task needs, and every sentence is
review surface and potential rot.

- State each point once, in the fewest words that carry the obligation or the
  judgment. No hedging, no paraphrase of a neighboring sentence, no synonym for
  a term the contract already names.
- Keep a "why" only when it changes what the agent does next; rationale that
  does not alter behavior belongs in the commit message or the rule registry,
  not in agent prose.
- A sentence that adds no new obligation, judgment, or example is deleted, not
  polished.

## Does the document still TEACH it?

No guard can answer this — one reads marked text or a token, never a sentence
(`guards.md`). So read for it. When you touch a plugin document, check each of
these:

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
- **A sentence that closes a set.** The member list is pinned against the
  contract; that the sentence still says the list is exhaustive is yours —
  `no-cardinality-restatements.md` §Closure claims.

The pattern: where a test pins a contract fact and the prose is what teaches it,
the test guards the fact and **you** guard the teaching.

## Quick test

For every new or changed sentence: **which rung carries this, and is it the
fewest words that carry it?** Craft needs no rung. A fact you cannot place on
one is the review finding you are about to receive — and after the next pin
bump, the wrong guidance an agent will follow.
