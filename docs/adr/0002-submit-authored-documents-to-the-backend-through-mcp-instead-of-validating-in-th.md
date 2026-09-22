---
schemaVersion: 0.1.0
id: "0002"
title: "Submit authored documents to the backend through MCP instead of validating in the plugins"
status: draft
date: 2026-09-22
deciders: []
tags: [validator, plugins]
scope: domain
reversibility: two-way-door
blastRadius: cross-team
relatesTo: ["0001"]
affects:
  - type: path
    pattern: "plugins/**"
  - type: path
    pattern: "contributing/analitiq-pipeline-builder.md"
  - type: path
    pattern: "rules/SCHEMA.md"
  - type: path
    pattern: "evals/run_evals.py"
  - type: path
    pattern: "conftest.py"
  - type: path
    pattern: "scripts/gen_pipeline_docs.py"
  - type: path
    pattern: "scripts/render_validator_claims.py"
  - type: path
    pattern: "scripts/_guard_lib.py"
  - type: path
    pattern: "scripts/check_validator_pin_contract.py"
  - type: path
    pattern: "scripts/check_contracts_version_pin.py"
  - type: path
    pattern: "tests/pipeline_builder/**"
  - type: path
    pattern: "tests/connector_builder/test_validator_pin_guard.py"
  - type: path
    pattern: "tests/connector_builder/test_schema_drift.py"
  - type: path
    pattern: "tests/schemas/test_contracts_version_guard.py"
  - type: path
    pattern: "tests/plugins/test_eval_runner.py"
  - type: path
    pattern: "tests/plugins/test_plugin_root_references.py"
  - type: path
    pattern: ".github/workflows/tests.yml"
  - type: path
    pattern: ".github/workflows/validator-release.yml"
  - type: path
    pattern: ".github/workflows/contract-models-release.yml"
  - type: path
    pattern: "CLAUDE.md"
  - type: path
    pattern: ".claude/rules/plugin-prose.md"
review:
  tier: arb
  tierReason: Moves the authoring gate for both plugins and retires the runtime pin and its release rules.
provenance:
  authoredBy: agent
---

# ADR-0002: Submit authored documents to the backend through MCP instead of validating in the plugins

## Context

The connector and pipeline plugins graded their own output. Each installed `analitiq-validator` from
PyPI at run time, pinned by `VALIDATOR_PIN` in the pipeline plugin's bootstrap and by a second copy in
the connector agent's self-install. Marketplace installs track main, so that pin must name a version
already on PyPI and must never run ahead of what the repo ships; CI guards, a drift test and release
rules exist only to hold it there.

The backend also grades what a plugin submits, with its own installed validator. So an authored
document had two gates at two independently chosen versions, which can disagree on the same document.

## Decision

We will make the backend the plugins' only gate. Both plugins submit what they author through MCP —
single documents during authoring, packages when complete, and for a pipeline the whole project (its
pipeline, connection and connector packages) — and act on the findings returned. The plugins do not
install, import or pin the validator. Every document, whatever its kind, is submitted and graded the
same way; a type map gets no operation of its own. An identity derived from a document (a database
endpoint's id) is computed by the backend. What a plugin selects and submits follows ADR-0001's
selection rule, which leaves out every secret location.

## Options considered

### Option A: Submit through MCP, no local validator (chosen)

| Dimension | Assessment |
|---|---|
| Gates per document | One, at the backend's version |
| Version management | No runtime pin in the plugins; validator release decoupled from plugin release |
| Plugin footprint | No venv bootstrap, no self-install, no local adapter scripts |
| Dependency | Authoring needs the backend reachable |

### Option B: Keep local validation in the plugins (status quo)

**Pros:** Works without the backend; fast local feedback.
**Cons:** A second gate whose version is chosen separately from the backend's; the pin machinery and
its guards stay; every plugin run pays a bootstrap install.

### Option C: Local pre-check plus backend submission

**Pros:** Fast feedback before the round trip.
**Cons:** Two validators over the same shape is split-brain: a document can pass locally and fail at
the backend, or the reverse. Keeps all of Option B's pin machinery.

## Trade-offs

Authoring stops when the backend is unreachable; there is no offline mode. Each check is a network
round trip. The backend must accept every submission shape before the plugins switch, or the plugins
lose their only gate.

## Consequences

- Easier: one verdict per document; validator releases no longer coordinate with plugin releases; the
  plugins shed their bootstrap and adapter code.
- Harder: the plugins depend on backend availability; the backend owns the MCP interface and must keep
  it in step with the plugins.
- **How we would know this was wrong:** authoring runs routinely fail on backend unavailability, or
  users need to author without access to the backend.
- Revisit if: an offline authoring use case appears.

## Action items

Each item lands only after the items before it, and updates every in-repo reader of what it changes in
the same PR.

1. [ ] The backend accepts single-document, package and pipeline-project submissions through MCP, and
   derives database endpoint ids.
2. [ ] Every in-repo caller that reaches the validator through the pipeline plugin's validation adapter
   or its command line — the doc generator, the validator-claim probes, the eval grader and the
   pipeline tests — calls the library's request entry points instead.
3. [ ] After ADR-0001 item 2 as well: remove the plugins' validation adapter, gap script and endpoint-id
   helper, the connector agent's self-install, and the tests and prose that exist only for them; agents
   submit through MCP. The bootstrap stays, unimported, because the pin guards still read its pin.
4. [ ] In their own PR: retire every guard, test and release rule that reads `VALIDATOR_PIN` —
   `pinned-validator-guard`, the pin leg of `contracts-version-guard` and its shared reader, the release
   workflows' lockstep lists, the eval runner's pin reference, and the pin rules in `CLAUDE.md` and the
   plugin-prose rule.
5. [ ] Remove the bootstrap and the `ANALITIQ_VALIDATOR_FROM_SOURCE` plumbing that only it reads: the
   repo-root `conftest.py`, the eval runner's grader environment, its test and the `CLAUDE.md` sentence.
