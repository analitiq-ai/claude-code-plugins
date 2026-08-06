# Rule: a guard never decides what a sentence asserts

Applies to every check this repo runs over prose it tracks — tests, render
scripts, CI steps — and to the prose those checks read.

**The invariant:** a guard may match text to *locate* something, never to
*decide* something. Locating is lexical: a backticked identifier, a fenced
block, a named heading, a generated-block marker, a path handed to git. Deciding
is semantic: does this sentence assert that the validator checks X, does this
paragraph still teach the rule, is this claim true. A mechanism can do the
first. Only a reader can do the second.

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
rule here and a review item, not code.

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

## What replaces it — the authoring obligation

The pinning requirement did not go away, only its detection. When you write a
sentence in plugin prose stating what the validator **does or does not** check:

1. **Prefer a generated block.** `<!-- BEGIN GENERATED: <id> -->` rendered from
   `scripts/render_validator_claims.py` moves with the measurement by
   construction.
2. **Otherwise pin it to a probe.** Add a `Claim`/probe in that script and put a
   `<!-- PROBE: <id> -->` fence above the sentence. The fence is still gated —
   naming an id no probe defines fails the build, and a probe nothing references
   fails the build.
3. **Or cite the `ADV-*` rule** that enforces the behaviour.
4. **Or do not make the claim.** "The validator does not check this" is rarely
   load-bearing guidance; state what the author must do instead.

## The sweep

When you touch plugin prose, read the sentences you changed and ask: does this
one assert something about what a tool checks, refuses, or lets through? If yes,
it needs one of the four above.

For contract prose — field descriptions and docstrings under
`analitiq.contracts` — the same sentence takes a census disposition instead;
`contract-prose.md` is the rule, and the `prose_hash` ratchet is what brings
the question back to a reader when the wording moves.

## Quick test

> Could this check be wrong in a way that only a person reading the sentence
> would notice?

If yes, it is not a check. Write it here.
