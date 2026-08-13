# Rule: every referent resolves for the reader

Governs every comment, docstring, field description, `.md` and rule record this
repo tracks. A referent is anything a sentence points at instead of saying: a
ticket number, a path, a name, a moment in time, "the rule above", "the four
cases".

**The invariant:** a reader holding nothing but a clone of this repo can resolve
every referent in the sentence, or the sentence states the fact instead of
pointing at it.

A referent resolves only when it has been resolved — the file opened, the tests
counted, the paragraph above read, the regex run. One that "obviously" resolves
is exactly the one that has rotted before. One that resolves only because the
writer holds something the reader does not — an untracked file, a tracker login,
the pull request open in a tab — does not resolve.

Where it does not resolve, the sentence states the fact instead of pointing at
it: almost always shorter, and it cannot rot.

## What violates it

- **A ticket or pull-request reference.** A bare `(#123)`, `issue #89`, the
  cross-repo `analitiq-engine#406`, a tracker URL. These resolve only for
  someone with a login, and often not even then — the tracker outlives neither
  the argument nor the decision. State the mechanism the ticket stood in for.
  The exempt surfaces are the ones whose subject IS the tracker; the root
  `CLAUDE.md` lists them.
- **A path the reader's clone does not contain.** A `.claude/` path that
  `.gitignore` does not re-include (most of that tree is local state, so the
  citation resolved only on the machine that wrote it), or any path
  `.gitignore` excludes — `docs/`, `htmlcov/`, `dist/`. Name the artifact and
  the repo that owns it, or state the fact the path stood in for.
- **A pointer to something that does not exist.** "(the re-add policy above)"
  where nothing above states a re-add policy — which shipped into a published
  JSON Schema. "the breaking change below", resolving to a different change than
  the one meant. A cited path (`tests/schema_drift/`) that was never a path.
- **A count that does not match what it counts.** "the four cases above" over
  three tests. A comment saying the repo tracks ~300 files where it tracked 467
  — the number was the *scanned* count, and naming the wrong set made it wrong
  by half. Counts attached to a set are the `no-cardinality-restatements.md`
  class; counts attached to *nearby text* rot the same way.
- **Invented history.** A comment asserting a past event the repo does not
  record — "tried earlier and abandoned", "each gate carried its own copy" — for
  a state no commit ever held. Plausible, unfalsifiable, and wrong.
- **A stale phrase left beside its replacement.** The new clause is written, the
  old one is not deleted, and the sentence now says it twice — with the less
  accurate half surviving.
- **A justification that does not justify.** A comment explaining why a
  character in a regex is load-bearing, citing an example another rule handles
  anyway. The code is right and the stated reason is not, so the next person
  removes the character.
- **A referent that expires.** "the wiring this PR extended", "the hole this
  commit closed", "the round-3 finding". A file outlives the change that wrote
  it, so these point at a moment the reader is not in. The fix is always the
  same: say what changed, not when. A CI workflow or release script whose
  runtime subject genuinely IS the pull request under check is not this defect;
  both shapes exist in this repo and read identically.
- **An unmarked path in running prose.** `see docs/thing.md` in bare prose is as
  unresolvable as a backticked one and harder to spot. Cited paths are marked:
  it makes them legible, and it makes the citation visible to the next reader as
  a citation rather than a word with a slash in it.

## Bare deletion is not the fix

Removing a referent and leaving the sentence that depended on it is a different
defect, not a smaller one — the claim stays and its support is gone. The
replacement carries what the pointer carried: what the mechanism is, what the
decision was, what makes the count true. If you cannot say what the pointer was
carrying, it was not carrying anything and the whole sentence goes.
