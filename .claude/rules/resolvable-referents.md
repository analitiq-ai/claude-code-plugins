# Rule: every referent must resolve for the reader

Applies to every comment, docstring, field description and `.md` this repo
tracks. A referent is anything a sentence points at instead of saying: a ticket
number, a path, a name, a moment in time, "the rule above", "the four cases".

**The invariant:** a reader holding nothing but a clone of this repo can resolve
every referent in the sentence, or the sentence states the fact instead of
pointing at it.

Two halves, and only one is mechanical.

## The mechanical half — already gated, do not hand-check

`tests/hygiene/test_ticket_references.py` fails the build on shapes it can match
literally: `issue #89`, `analitiq-engine#406`, a bare `(#123)`, a GitHub URL, a
path under `.claude/` that is not tracked, and "this PR" / "this commit". Its
module docstring owns the pattern set and every deliberate narrowing. You do not
need to look for these — CI does, on every file git tracks.

## The semantic half — this is what the rule is for

The gate matches shapes. It cannot read. Everything below passes it and is the
same defect, and every one of these is a real finding from this repo's review
history, not an invented example:

- **A pointer to something that does not exist.** "(the re-add policy above)"
  where nothing above states a re-add policy — which shipped into a published
  JSON Schema. "the breaking change below", resolving to a different change than
  the one meant. A cited path (`tests/schema_drift/`) that was never a path.
- **A count that does not match what it counts.** "the four cases above" over
  three tests. "the repo tracks ~300" when it tracks 467. Counts attached to a
  set are the [[no-cardinality-restatements]] class; counts attached to *nearby
  text* rot the same way and no census hash sees either.
- **Invented history.** A comment asserting a past event the repo does not
  record — "tried earlier and abandoned", "each gate carried its own copy" — for
  a state no commit ever held. Plausible, unfalsifiable, and wrong.
- **A stale phrase left beside its replacement.** The new clause is written, the
  old one is not deleted, and the sentence now says it twice — with the less
  accurate half surviving.
- **A justification that does not justify.** A comment explaining why a
  character in a regex is load-bearing, citing an example that another rule
  handles anyway. The code is right and the stated reason is not, so the next
  person removes the character.
- **A referent that expires.** "the wiring this PR extended", "the round-3
  hole". Review rounds and pull requests are not addressable from a file.

## How to apply it

On every prose or comment edit, for each referent in the changed text:

1. **Name what it points at**, concretely — a file, a symbol, a paragraph, a
   number.
2. **Resolve it, don't assume it.** Open the file. Count the tests. Read the
   paragraph above. Run the regex. A referent that "obviously" resolves is
   exactly the one that has rotted before.
3. If it does not resolve, **state the fact instead of pointing at it.** That is
   almost always shorter, and it cannot rot.
4. If it resolves only because you have something the reader does not — an
   untracked file, a tracker login, the PR open in a tab — treat it as not
   resolving.

## Quick test

> Hand this file to someone with a fresh clone and no other context. Can they
> follow every pointer in the sentence you just wrote?

If the answer needs "well, they'd also need…", the sentence states the fact
instead.

## Bare deletion is not the fix

Removing a referent and leaving the sentence that depended on it is a different
defect, not a smaller one — the claim stays and its support is gone. The
replacement carries what the pointer carried: what the mechanism is, what the
decision was, what makes the count true. If you cannot say what the pointer was
carrying, it was not carrying anything and the whole sentence goes.
