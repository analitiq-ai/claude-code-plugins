# Rule: a claim about the engine names how it was learned

Governs every sentence in this repo asserting what the engine does at run
time — a rule record's `rationale`, plugin prose, a docstring, a comment, a
finding message. No `paths:` because the sentence class rots wherever it is
written.

**The invariant:** a sentence about engine behaviour is a fact this repo
cannot check. It is either pinned to something that moves with the engine, or
it says plainly that it is a reading of the engine as it stands — and never
carries a check whose correctness depends on it silently.

## Why it is not simply banned

Rule rationales lean on it everywhere, and rightly: "why does this rule exist"
is usually "because of what happens when the engine reads it". Stripping that
leaves records that state an obligation and cannot say what it buys. The
problem is not that the claims exist; it is that they read as verified when
nothing verifies them.

## The rungs

1. **Pinned to a published artifact.** The engine publishes the fact, this
   repo vendors one version with a sha256, and CI byte-compares the copy
   against the published object. `analitiq.contracts.arrow_grammar` is the
   worked example — a family the engine stops executing reddens the build. Any
   engine fact worth enforcing a rule on belongs here.
2. **Stated as a reading, in a record's `rationale`.** No artifact exists, so
   the sentence explains the rule rather than enforcing it. Write it as what
   the engine does, not as what it must always do, and keep the check correct
   on its own terms — see below.
3. **Cited, not restated.** Plugin prose names the `RULE-*` id. Prose that
   ships to users is the surface where a stale engine claim does the most
   damage and the least work.

## The half a reader has to carry

**A check must not be wrong when the claim goes stale.** This is the part no
mechanism covers. A rule whose finding is correct only while the engine
behaves a certain way starts rejecting working documents the day it changes,
and nothing here goes red — the rule's own tests keep passing, because they
test the check, not the engine.

So when a check rests on an engine reading, prefer a predicate that is
defensible from the contract too: a field typed where nothing in this repo can
read it either is a weaker claim than a field typed where the engine will not
look, and it survives the engine changing. Where that is not possible, the
record's `rationale` says which way the check fails if the reading goes stale.

**Ask, do not infer.** The engine's behaviour is knowable — there is an agent
for it, and it answers with file paths. A claim written from a guess has been
wrong here more than once: a rule justified by "the engine hangs on this" was
built and reverted after the engine turned out never to follow the construct
at all. Ask first; the answer is minutes, and the rule you write is different.

**Naming a symbol is not pinning.** `resolve_field_arrow_type` in a docstring
resolves for nobody holding this clone and reddens nothing when it is renamed.
State the behaviour, not the function that implements it.
