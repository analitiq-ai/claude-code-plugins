---
paths:
  - "plugins/**/*.md"
  - "packages/**/*.py"
---

# Rule: state the mechanism, not the cardinality

Applies when editing prose that describes a shape a model carries — plugin
markdown, contract-model docstrings and field descriptions. Companion to
`plugin-prose.md`, which classifies every sentence as craft or fact: **a
cardinality is always a fact**, and this rule is how that one fact class is
written.

## The invariant

Never state **how many** members a declared shape has. State what makes
something a member.

Counts are the restatement class every existing guard is blind to, which is why
this is absolute rather than a judgment call:

- A **wording ratchet** (the prose-census hash pin) fingerprints the sentence.
  Adding a sixth required field changes no word, so the hash still matches while
  the claim has gone false.
- A **name-level drift pin** compares member *names* against the contract. A new
  member updates the constant and the prose table together — correctly — and the
  count sentence beside them is never read.
- A **census-text lint** scans identifier-shaped tokens. Number words are not
  identifiers.

So a count is correct the day it is written, and nothing anywhere notices when
it stops being. Adding a member falsifies the sentence *without changing a word
of it*.

## Rewrite recipes

| Instead of | Write |
|---|---|
| "all five shape facts are required" | "every shape fact is required" |
| "exactly three required keys and no others" | "exactly the keys named below and no others" |
| "the last four members are terminal" | "terminal members are those the engine never transitions a run out of" |
| "the two tracking identifiers" | name what makes an identifier a tracking one |
| "both caps may be omitted" | "each cap may be omitted" |
| "the three discovery actions" | "the discovery actions" |

The first row is not hypothetical — that sentence shipped in this repo's prose
and was swept out, which is why it heads the list.

The shape of the fix is always the same: replace the number with the **rule that
decides membership**, or defer to the enumeration that follows ("named below",
"in the table", "the sub-bullets"). Where you defer to an enumeration, make sure
a guard pins *that* — otherwise the drift has only moved.

## Counting words to watch

`one two three four five six seven eight nine ten`, `both`, `a pair of`, and
digits. `both` and `two` are the easiest to miss because they read as ordinary
English: "both caps", "the two identifiers", "both required".

Not every number is a cardinality restatement. These are fine:

- Counts of things no model owns — orchestrator modes, package files, "two
  steps", "three consequences", a three-level object hierarchy.
- Distributive and selection phrasing — "one entry per field", "exactly one
  operator key per predicate", "one of `ref` / `template` / `literal`". These
  state a *rule*, not a set size, and stay true as the set grows.
- Bounds and literals the contract genuinely defines — a `max_bind_params` of
  2100, a version triple.

The test: **if a member were added to the set, would this sentence become
false?** If yes, it is a cardinality and it does not go in prose.

## Where a count genuinely helps the reader

Render it. A `BEGIN GENERATED` block fed from the pinned models is the only
sanctioned form of a written-out count, because it moves with the contract by
construction (`plugin-prose.md` ladder, rung 3). Never hand-type one and pin it
with a test — a test asserting "the prose says five and the model has five"
passes by being updated in the same commit that breaks the reader's
understanding.

## The sweep

On every prose edit, and whenever a model gains or loses a member:

1. Read the changed file for the counting words above.
2. For each, name the set it counts.
3. If a model owns that set, rewrite per the recipes — even when the number is
   currently correct. Correctness today is not the property being protected.
4. If the rewrite defers to an enumeration ("the table below"), confirm a guard
   pins that enumeration bidirectionally; if none does, the deferral is a new
   drift surface, not a fix.

A count found in prose during review is a defect regardless of whether it is
accurate.
