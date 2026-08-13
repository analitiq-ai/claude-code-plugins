# Evals — grading what the plugins produce

Everything else in this repo grades the **instructions**: that a rule id
resolves, that a probe fence names a live probe, that an enum in prose still
matches the contract. None of it runs an agent, so none of it can say whether an
agent reading those instructions authors a correct document. A rule can be cited
correctly in every file and still be worded so the model ignores it.

An eval closes that. One realistic request, run in an empty directory, graded on
the files that appear.

## Contents

- Why a scenario exists
- Running them
- Reading a result
- What a scenario grades
- Coverage
- Adding one

## Why a scenario exists

The registry carries obligations no validator applies — records whose
`validator` is null, where nothing rejects a violation. A document can satisfy
the contract and violate them, so "the tests pass" says nothing about them.
Those are what the scenarios aim at, and `coverage` reports which ones are still
unwatched.

Scenarios are chosen to sweep as many of those as one realistic request can
reach, rather than one scenario per rule. A single pipeline run touches dozens,
and each extra assertion on a run that has already happened is nearly free.

| Scenario | Reaches |
|---|---|
| `pipeline-api-to-new-table` | connection routing by declared storage, secret pointers, CA material for a verifying TLS mode, and the defaults an agent copies from a worked example |
| `pipeline-db-incremental-upsert` | the incremental and upsert block shapes, cursor and conflict keys, endpoint handles reused rather than recomputed |
| `connector-refuses-storage-kind` | the decline path, which leaves nothing on disk for any validator to grade |
| `connector-database-package` | the package files — entry points, driver, both type maps — which the validator explicitly does not cover |
| `connector-api-oauth2` | value expressions, ref paths and function names, plus the endpoint fan-out |

## Running them

```bash
python3 evals/run_evals.py list                                  # what exists, and why
python3 evals/run_evals.py coverage                              # which obligations are watched
python3 evals/run_evals.py run --runs 5                          # offline scenarios only
python3 evals/run_evals.py run --network --runs 5                # include the ones that fetch
python3 evals/run_evals.py run --scenario pipeline-api-to-new-table --keep
```

Each run gets a fresh temporary directory, seeded from the scenario, and invokes
`claude -p --plugin-dir <plugin>` there. `--keep` leaves the directory behind so
you can read what the agent actually wrote.

Every finished run is appended to `eval-results.jsonl` (`--results` moves it) as
it completes, one JSON line carrying the scenario, the verdict, the failures and
the elapsed seconds. That file is the record, not the terminal output: a run
takes long enough that jobs get killed, and a pass rate computed only at the end
is a pass rate you lose. It is appended and never truncated, so rates can be
counted across invocations. It is gitignored — a measurement of a moment, not a
tracked artifact.

This is not part of `pytest`. It takes minutes per run, spends tokens, and the
networked scenarios reach a provider's live documentation — three things the
suite deliberately avoids. Run it on a schedule or on demand.
`tests/plugins/test_eval_scenarios.py` is the part that does run on every
build: it checks the scenarios are well-formed and still cite things that exist,
without invoking anything.

## Reading a result

The model is not deterministic, so one run decides nothing. Run each scenario
several times and read the pass rate.

- **Every run passes** — the prose is being followed.
- **Some runs pass** — the prose reads two ways. This is the signal worth having:
  the failing assertion names the rule, and the rule's prose is where to look.
- **No run passes** — either the prose is wrong or the scenario is. Check the
  scenario against the contract before editing the plugin.

A wavering scenario is a finding, not a flake to retry away. `--fail-under`
exists for a caller that wants a gate, but the default is a report.

## What a scenario grades

Two halves, and the split matters.

**The validator**, on every document produced. Objective, already owned by this
repo, and free. It runs against the in-repo source rather than the published
pin — the same choice `conftest.py` makes for the suite.

**Assertions**, for what the validator cannot see. Each names the `RULE-*` it
stands for, or `intent` where it is checking that the run did what the prompt
asked rather than that it satisfied an obligation. Keeping those apart is what
makes the coverage report mean something: choosing `upsert` because the user
said upsert is not a rule anything owns.

Fixtures are **seeded from worked examples** already validated by an existing
gate, patched in place where a scenario needs a different id. Committing a
fixture connector here would create exactly the unchecked second copy the rest
of the repo is built to avoid.

## Coverage

`coverage` reads the registry and sorts every unenforced rule into one of three
states, each listed with its ids and its size:

- **graded** — a violation fails a run.
- **exercised but not graded** — the run touches it and stays green anyway.
- **untouched** — no scenario reaches it.

Three states, deliberately not merged. *Exercised* is not *graded*: the run
walks through that rule and would stay green while it was violated. Reading the
two together would claim coverage this harness does not have.

The `untouched` list is the backlog. Work down it by cost of silent failure, not
by count — and by asking whether a realistic prompt can reach the rule at all,
because some cannot be reached this way and need a different mechanism.

## Adding one

Pick the obligation first, from the `untouched` list. Then find the smallest
realistic request that reaches it, and add assertions for everything else that
request happens to pass on the way — a run that has already happened is the
expensive part.

The scenario format is documented in `run_evals.py`'s module docstring, which is
the file that implements it. Two things worth stating twice:

- **Leave nothing to ask about.** A headless run cannot answer a clarifying
  question. An underspecified prompt grades whether the agent guesses well,
  which is not what any of these are for.
- **Prefer offline.** A scenario that seeds its inputs is repeatable and can run
  in a sandbox. Reach for `network: true` only where the thing under test *is*
  the research.
