---
paths:
  - "tests/**/*.py"
  - "scripts/**/*.py"
  - ".github/workflows/*.yml"
---

# Rule: a guard never decides what a sentence asserts

Applies to every check this repo runs over prose it tracks — tests, render
scripts, CI steps. The prose side of the same split is `plugin-prose.md` (what
ships to users) and `contract-prose.md` (what the contract publishes about
itself); this file governs the mechanism that reads them.

**The invariant:** a guard may match text to *locate* something, never to
*decide* something. Locating is lexical: a backticked identifier, a fenced
block, a named heading, a generated-block marker, a probe id resolved against
its registry, a path handed to git. Deciding is semantic: does this sentence
assert that the validator checks X, does this paragraph still teach the rule, is
this claim true. A mechanism can do the first. Only a reader can do the second.

## What is banned

A list of hand-curated English regexes — or phrases, or vocabulary — whose
output is a verdict about what a sentence means. The shape is recognisable:

```python
CLAIM_TRIGGERS = (
    r"(?:is|are)\s+(?:not|never)\s+(?:checked|validated|enforced)",
    r"\bunchecked\b",
    r"slips?\s+past",
    …
)
```

A list of exactly that shape decided, in CI, whether a sentence in plugin prose
asserted validator behaviour. It is gone. Do not write more, in any file, for any
property — validator behaviour, destructive routes, "the doc still teaches it",
tone, completeness. If the check needs to know what the English means, it is a
rule in this directory and a review item, not code.

The shape is not always a tuple of regexes. A single `in` test is the same
thing, and so is one phrase used to locate the sentence a second assertion then
grades. These are all banned, and each names an obligation a reader carries
instead:

| matching this | to decide this | is `.claude/rules/…` |
|---|---|---|
| a ticket-shaped token | this reference will not resolve | `resolvable-referents.md` |
| a disclaimer's phrasing in a field description | the contract still declares this half unenforced | `contract-prose.md` |
| `"nothing else"`, `"no others"` beside an enumeration | this sentence still closes the set | `no-cardinality-restatements.md` |
| `must` / `every` / `only` / `defaults to` | this sentence states an obligation | `contract-prose.md` |
| a negation beside a validator's name | this sentence asserts validator behaviour | `plugin-prose.md` |

An English phrase used only as an **anchor** — find this sentence, then check
its backticked members against the contract — is not exempt. It decides which
sentence gets graded, so rewording the sentence silently grades nothing, and
the check reports success.

A waiver registry beside such a check is the diagnosis, not the remedy. An
empty one is worse than a full one: it is the machinery kept ready for
overrides nobody has needed yet.

## Why, from what this repo measured

- **It fails on improvement.** Rewriting a claim more clearly stops matching the
  trigger, so the build goes red and the failure message asks the author to put
  the worse wording back.
- **It cannot read polarity.** `"dotted string" in text` passes on a document
  saying a dotted string is fine exactly as it passes on one forbidding it. A
  trigger for "is not checked" cannot tell a claim from a denial of one.
- **Its coverage is undecidable.** The same claim phrased without a listed
  trigger does not match, and the set of phrasings that would is not
  enumerable. A green result means "no phrase matched", which reads as "no
  unpinned claim exists".
- **It grows a waiver registry.** Every false positive needs an exemption with a
  reason, and the exemption list is the admission that a reader was already
  deciding. Guards whose verdicts a human keeps overriding are review items
  wearing a test's clothes.

## Hygiene for the guards that remain

- **Assert non-vacuity.** Zero matched citation/fence/example sites is a red
  build, not a silent exemption. An extractor that finds nothing has stopped
  measuring, and says so in the same voice as one that finds nothing wrong.
- **Failure-message fix-hints are part of the guard.** A hint naming a file or
  section that no longer carries the fact is a defect. Repoint hints in the same
  commit that moves a fact.
- **Hand the verdict to the contract.** A guard over prose extracts backticked
  identifiers, fenced blocks, a named heading or a generated-block marker, then
  compares them against the live models, the registry or the filesystem. The
  comparison is what decides; the text match only says where to look.
- **Say what you did not check.** A guard reaching only the contract half of a
  fact names the reader's half in its docstring, and names the rule file that
  carries it. That sentence is how the next contributor learns the check is not
  the whole obligation.

## What the ban does not relax

The pinning requirement did not go away, only its detection. A sentence stating
what the validator does or does not check still has to be pinned when it is
written — `plugin-prose.md` § "A sentence about what the validator checks" is
the ladder, and `contract-prose.md` is the same obligation on the contract's own
surface. Recognising that a sentence makes such a claim is the author's job, and
this file is why.

## Quick test

> Could this check be wrong in a way that only a person reading the sentence
> would notice?

If yes, it is not a check. Write it in this directory instead.
