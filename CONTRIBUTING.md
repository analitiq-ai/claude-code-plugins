# Contributing

This repo holds one contract surface: the Claude Code plugins that author
Analitiq artifacts, the Pydantic models that define those artifacts, the
validator that enforces them, and the JSON Schemas rendered from the models.
A rule changes in one place because those four are expressions of one rule set.

Read first, in this order:

- **`README.md`** — install, layout, and the development loop
  (`pip install -r requirements-dev.txt && pytest`).
- **`CLAUDE.md`** — what the repo is, the drift policy, the runtime validator
  pin, and the release model. It is the authority on all of those; nothing here
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

Adopted from the engine repo (analitiq-ai/analitiq-engine#392), where six
catalog-addressing issues from one review were consolidated only afterwards, and
where 19 of the first 183 issues were closed `obsolete` because instance-by-
instance filing lost the race against the refactors that ended the class.

The rule is about mechanisms, not batch size. Three findings from one review are
three issues when they are three mechanisms: #127 / #128 / #129 were filed as a
set from a single review precisely because each catches a different class — a
stated obligation enforced nowhere, the habit this document's second rule
addresses, and a contract field nothing reads. The test is *one mechanism*, not
*one review*.

## Close against the class, not the instances

*Applies at issue-closing time — when a PR claims an issue is done.*

Three clauses. Each is on the record here as a partial closure that came back.

### An issue does not close while its own body names a site it did not fix

analitiq-ai/analitiq-engine#346 was titled for four divergences — pagination,
**request binding**, replication, error classification. It fixed pagination and
closed. `path_params` and `transport_ref`, both named in its own body under
request binding, were rediscovered four months later and are open here as #124
and #125, alongside two more of the same class that had never been filed at all.

If a PR fixes part of an issue, it closes nothing. Either narrow the issue's
scope explicitly — edit the body, say what moved out and where it went — or
leave it open.

### A contract-divergence issue closes on a green guard for its whole surface

"The engine does not honour X" is a claim about a surface, not about the
instances a reviewer happened to notice. It closes when a check covering that
surface is green — a validator rule, a contract-model constraint with a test, a
drift guard under `tests/` — not when the reported instances stop reproducing.

The closing PR names the check that fails if the divergence returns. If no such
check can be written today, the closing comment says so and states what it would
take; that statement is itself a filing under the consolidation rule.

### A fix that narrows a rule records what it deliberately left wide

In the same PR, as a test or a follow-up issue.

#7 fixed an over-strict scope check and its own consolidating comment predicted
the failure mode of the obvious shortcut: a position-aware check was required,
not a blanket allowance. What landed is position-aware *directionally* but never
gained an existence check — and #123 is exactly the false negative that comment
predicted, in the one position the guardrail was not applied.

The model to copy is #111: the pagination-bound work (PR #109) tightened
`PageSize.default` and left `{"literal": 0}` able to walk around the bound.
Rather than closing quietly, that gap shipped with a test recording it
(`test_literal_expression_bypasses_the_bound`) and an issue that stayed open.
The narrowing was deliberate, so the residue was written down.

## Where these bind in the PR loop

`CLAUDE.md` → **PR Review Process** is the loop. The consolidation rule governs
its step 3b — a finding out of scope becomes a new issue only after checking
whether it is the third leak from a mechanism already filed. The close-the-class
rule governs the step after the loop ends: the PR body states, per issue it
closes, which clause above it satisfies.
