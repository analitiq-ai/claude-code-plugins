# Contributing

Read first, in this order:

- **`README.md`** — what this repo is, install, layout, and the development loop.
- **`CLAUDE.md`** — the drift policy, the runtime validator pin, the PR review
  loop, and the release model. It is the authority on all of those; nothing here
  restates them.
- **`plugins/<name>/CLAUDE.md`** — the authoring rules for the plugin you are
  working inside.

This document carries only what those do not: **how a class of defect gets
closed.** Two rules — one for filing, one for closing.

## The consolidation rule

*Applies at review and triage time — when findings become issues.*

> **Three leaks means one abstraction.** When three or more findings share one
> underlying mechanism, stop filing instances. File a single consolidation issue
> that (1) names the invariant or missing abstraction, (2) enumerates every site
> it covers, and (3) closes the instance issues into it. Fix the class in one PR
> and test the set, not the single case.

Corollary for reviewers: a review that produces three or more findings pointing
at the same mechanism says so in the review summary, instead of emitting them as
independent items.

Adopted verbatim from the engine repo (analitiq-ai/analitiq-engine#392). The
precedent it cites is engine #348, which consolidated six catalog-addressing
issues filed separately out of one PR review. The same issue measured the cost
of not consolidating: of the 183 issues filed in that repo as of 2026-07, 19
closed `obsolete` — instance-by-instance filing lost the race against the
refactors that ended the class.

The trigger is a shared mechanism, not a batch size. Three findings from one
review are three issues when they are three mechanisms; three findings spread
across three reviews are one issue when they are one. The test is *one
mechanism*, not *one review*.

## Close against the class, not the instances

*Applies at issue-closing time — when a PR claims an issue is done.*

Three clauses. The first two are cases where the class outlived its closure; the
third is the case that was handled correctly and is the model to copy.

### An issue does not close while it names a site it did not fix

Anywhere it names one — body, title, or a comment that scoped the work.

analitiq-ai/analitiq-engine#346 was titled for four divergences: pagination,
**request binding**, replication, error classification. Its body narrated
pagination only; a survey comment on it enumerated the request-binding sites by
name — `transport_ref`, `path_params`, `headers`, `headers_remove`, `query` —
and proposed splitting them out. Nobody split them. The issue fixed pagination
and closed 2026-07-22.

Eleven days later engine PR #451 was working the same list from scratch, and the
sites that survey had already named were filed a second time, two weeks after
the issue that named them closed: #124 here (`request.transport_ref` is
contract-valid and read by nothing), engine #452 (`request.headers` /
`headers_remove` / `in: header`) and engine #453 (`request.query`'s key map
dropped on the wire).

If a PR fixes part of an issue, it closes nothing. Either narrow the issue's
scope explicitly — edit the body, say what moved out and where it went, file the
remainder — or leave it open.

### A contract-divergence issue closes on a green guard for its whole surface

"The engine does not honour X" is a claim about a surface, not about the
instances a reviewer happened to notice. It closes when a check covering that
surface is green — not when the reported instances stop reproducing.

Which check depends on where the divergence lives:

- **Connector behaviour** — the engine's **Connector Conformance Kit**, shipped
  as the `conformance` extra of the `analitiq-cdk` package and runnable against
  any connector directory:

  ```bash
  pip install "analitiq-cdk[conformance]"
  pytest -p cdk.conformance.plugin --pyargs cdk.conformance.tier1 --connector-dir .
  pytest -p cdk.conformance.plugin --pyargs cdk.conformance.tier2 --connector-dir . \
      --live-connection ci/live-connection.json
  ```

  The kit is tier-scoped, not per-finding: a run executes every check that
  applies to the target connector's `kind`, so green means the class passed, not
  the instance. It also refuses to report a false green — a run that collects no
  applicable check for the target's `kind` fails outright and says so, which is
  how you find out the kit does not yet cover your surface.

- **Document shape** — a check in this repo: a validator rule, a contract-model
  constraint with a test, or a drift guard under `tests/`.

Either way the closing PR names the check that fails if the divergence returns.
When no check covers the surface, the closing comment says which check would
have to exist — and that statement gets filed as an issue, subject to the
consolidation rule, before the divergence issue closes.

The case on record: #7 fixed an over-strict scope check in the expression
resolver — 91 false positives on one 25-endpoint connector — and closed once
those stopped reproducing. Its own consolidating comment predicted one way the
blanket fix could go wrong: appending `response` to the global scope list would
then accept `response.*` refs in request-construction positions, so the check
had to stay position-aware. The fix avoided exactly that failure and left its
sibling standing — refs became position-aware but never gained an *existence*
check. #123 is that gap: a pagination ref into `response.body` that matches no
node in `response.schema` passes, and a typo silently truncates a sync. A guard
over the scope-check surface would have caught both; satisfaction that the 91
false positives were gone caught one.

### A fix that narrows a rule records what it deliberately left wide

In the same PR, as a test or a follow-up issue. This binds any PR that narrows a
rule, whether or not it closes an issue.

The model to copy is #111. PR #109 bounded `PageSize.default` with `gt=0` and
left `{"literal": 0}` able to walk around the bound. Rather than closing
quietly, that gap shipped with a test recording it
(`test_literal_expression_bypasses_the_bound`) and an issue that stayed open on
purpose. The narrowing was deliberate, so the residue was written down.

## Where these bind in the PR loop

`CLAUDE.md` → **PR Review Process** is the loop.

- **Filing** — step 3b. A finding out of scope becomes a new issue only after
  checking whether it is the third leak from a mechanism already filed. If it
  is, file the consolidation issue instead: name the invariant, enumerate every
  site, and close the earlier instance issues into it.
- **Closing** — the PR body. Any PR that claims to close an issue states which
  clause above it satisfies, and any PR that narrows a rule records what it left
  wide, per the third clause.

Issue references above are cited as of 2026-08 and describe the state at the
time each case was written down; open issues named here may since have closed.
