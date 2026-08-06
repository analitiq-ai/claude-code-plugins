# Rule: every referent must resolve for the reader

Applies to every comment, docstring, field description and `.md` this repo
tracks. A referent is anything a sentence points at instead of saying: a ticket
number, a path, a name, a moment in time, "the rule above", "the four cases".

**The invariant:** a reader holding nothing but a clone of this repo can resolve
every referent in the sentence, or the sentence states the fact instead of
pointing at it.

Two halves, and only one is mechanical.

## The mechanical half — already gated, do not hand-check

`tests/hygiene/test_ticket_references.py` fails the build on the referents whose
verdict comes from somewhere other than the meaning of the sentence:

- a ticket reference, which is a lexical token — `issue #89`,
  `analitiq-engine#406`, a bare `(#123)`, a GitHub URL;
- a **marked** citation of a path git does not track under `.claude/`, or of any
  path `.gitignore` excludes — `` `docs/…` ``, `` `htmlcov/` ``, `` `dist/` ``.
  Marked means backticked or a markdown link target; git supplies the verdict.

Its module docstring owns the pattern set and every deliberate narrowing. You do
not need to look for these — CI does, on every file git tracks.

**Everything else in this rule is yours**, including things that look mechanical.
The gate deliberately does not match "this PR", because no pattern can separate
the dangling referent from a CI job whose runtime subject genuinely is the pull
request. Nor does it judge whether an unmarked `docs/thing.md` in running prose
was meant as a path. Those verdicts need a reader.

## The semantic half — this is what the rule is for

The gate matches shapes. It cannot read. Everything below passes it and is the
same defect, and every one of these is a real finding from this repo's review
history, not an invented example:

- **A pointer to something that does not exist.** "(the re-add policy above)"
  where nothing above states a re-add policy — which shipped into a published
  JSON Schema. "the breaking change below", resolving to a different change than
  the one meant. A cited path (`tests/schema_drift/`) that was never a path.
- **A count that does not match what it counts.** "the four cases above" over
  three tests. A comment saying the repo tracks ~300 files where it tracked 467
  — the number was the *scanned* count, and naming the wrong set made it wrong
  by half. Counts attached to a set are the `no-cardinality-restatements.md`
  class; counts attached to *nearby text* rot the same way and no census hash
  sees either.
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
- **A referent that expires.** "the wiring this PR extended", "the hole this
  commit closed", "the round-3 finding". A file outlives the change that wrote
  it, so these point at a moment the reader is not in. **Nothing in CI catches
  this class** — a pattern for "this PR" cannot tell the dangling referent from
  a CI workflow or release script whose runtime subject really is the pull
  request under check, and both exist in this repo. Sweep it by hand on every
  edit. The fix is always the same: say what changed, not when.
- **An unmarked path in running prose.** The gate only reads paths the author
  marked — backticked, or a markdown link. `see docs/thing.md` in bare prose is
  invisible to it and just as unresolvable to the reader. Mark paths you cite,
  which both makes them legible and puts them under the gate.

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
