# Rule: what this repo adds to the PR review loop

The loop itself (review rounds, triage, resolving threads, waiting on checks) is
the contributor's own workflow and is not restated here. This repo adds:

- A finding that is out of scope becomes a new issue only after applying the
  **consolidation rule** in `CONTRIBUTING.md`: three findings sharing one mechanism
  become one abstraction issue, and the instances close into it.
- `CONTRIBUTING.md` owns **close against the class, not the instances** — what a PR
  must satisfy before it closes an issue. Read it before filing an issue out of a
  review, and before closing one.
- Run the tests and make sure they all pass before calling a PR ready.
