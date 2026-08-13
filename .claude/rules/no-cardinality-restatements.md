---
paths:
  - "plugins/**/*.md"
  - "packages/**/*.py"
  - "rules/records/*.yaml"
---

# Rule: state the mechanism, not the cardinality

Governs prose describing a shape a model carries — plugin markdown,
contract-model docstrings and field descriptions, and a rule record's
`statement` and `rationale`. `plugin-prose.md` classifies every sentence as
craft or fact: **a cardinality is always a fact**, and this rule is how that
fact class is written.

A record is where a count rots furthest from the reader who could catch it. Its
`statement` renders verbatim into the plugin references agents author against,
and its `rationale` ships to PyPI inside the wheel's `rules.json` — one count
reaching users through two surfaces, neither of which re-reads the sentence.

## The invariant

Prose never states **how many** members a declared shape has. It states what
makes something a member.

Counts are the restatement class this repo's mechanisms cannot see, which is
why this is absolute rather than a judgment call:

- A **wording ratchet** (the prose-census hash pin) fingerprints the sentence.
  Adding a sixth required field changes no word, so the hash still matches while
  the claim has gone false.
- A **name-level drift pin** compares member *names* against the contract. A new
  member updates the constant and the prose table together — correctly — and the
  count sentence beside them is never read.
- A **census-text lint** scans identifier-shaped tokens. Number words are not
  identifiers.

So a count is correct the day it is written, and adding a member falsifies it
*without changing a word of it*. A count in prose is a defect whether or not it
is accurate.

## The compliant form

| Instead of | Write |
|---|---|
| "all five shape facts are required" | "every shape fact is required" |
| "exactly three required keys and no others" | "exactly the keys named below and no others" |
| "the last four members are terminal" | "terminal members are those the engine never transitions a run out of" |
| "the two tracking identifiers" | name what makes an identifier a tracking one |
| "both caps may be omitted" | "each cap may be omitted" |
| "the three discovery actions" | "the discovery actions" |

The fix always has the same shape — the number is replaced by the **rule that
decides membership**, or defers to the enumeration that follows ("named below",
"in the table", "the sub-bullets"). A deferral is compliant only where a guard
pins that enumeration bidirectionally; otherwise the drift has only moved.

## Closure claims

A closure claim is that fix's finished form: "the block carries
`supported_methods` and `cursor_mappings`, nothing else". It states the
membership rule — an exhaustive list — instead of its size, and stays true as
long as the list does.

It is load-bearing wherever an authoring agent reads the enumeration as
exhaustive, which is most places one appears: plugin prose closes sets with "and
nothing else", "exposes exactly these", "owns exactly this much". An agent
reading a claim that has lost its closure authors extra keys, and the contract
models reject unknown keys, so the document fails at the validator instead of
teaching the shape.

**What a guard covers.** Only the contract half, and only where a test reads
that set back: `tests/connector_builder/test_schema_drift.py` pins
`Replication`, `ResourceDiscoveryTriggers` and the type-map rule keys, so a
member landing or leaving fails with the document whose sentence goes false. A
closure claim over a set no test reads has no mechanical half at all.

**What a reader covers.** Whether the sentence still closes the set. Deleting
"and nothing else" leaves a sentence that is weaker, still true, and no longer
teaching what it exists to teach. No check can take that verdict — `guards.md`
carries the argument.

So a live closure claim holds two properties at once: the sentence still says
the list is exhaustive, and the list still names exactly the contract's members.
A test tells you when the contract moved; never that the sentence went stale on
its own. A set deliberately opened loses its closure in the same change that
opens it, and any test pinning that set stays — it pins the members, not the
sentence.

## What is a cardinality

`one two three four five six seven eight nine ten`, `both`, `a pair of`, and
digits. `both` and `two` are easiest to miss because they read as ordinary
English: "both caps", "the two identifiers", "both required".

Not every number is a cardinality restatement. These are fine:

- Counts of things no model owns — orchestrator modes, package files, "two
  steps", "three consequences", a three-level object hierarchy.
- Distributive and selection phrasing — "one entry per field", "exactly one
  operator key per predicate", "one of `ref` / `template` / `literal`". These
  state a *rule*, not a set size, and stay true as the set grows.
- Bounds and literals the contract genuinely defines — a `max_bind_params` of
  2100, a version triple.

The discriminator: **if a member were added to the set, would this sentence
become false?** If yes, it is a cardinality and it does not go in prose.

## Where a count genuinely helps the reader

Rendered. A `BEGIN GENERATED` block fed from the pinned models is the only
sanctioned written-out count, because it moves with the contract by
construction (`plugin-prose.md` ladder, rung 3). A hand-typed count pinned by a
test is not an alternative: a test asserting "the prose says five and the model
has five" passes by being updated in the same commit that breaks the reader's
understanding.
