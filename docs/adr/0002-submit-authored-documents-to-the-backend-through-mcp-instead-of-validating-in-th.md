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
    pattern: "scripts/**"
  - type: path
    pattern: "tests/**"
  - type: path
    pattern: "evals/**"
  - type: path
    pattern: "conftest.py"
  - type: path
    pattern: "packages/validator/tests/**"
  - type: path
    pattern: ".github/workflows/**"
  - type: path
    pattern: "CLAUDE.md"
  - type: path
    pattern: "contributing/**"
  - type: path
    pattern: "rules/SCHEMA.md"
  - type: path
    pattern: ".claude/rules/**"
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

Each item lands only after the items before it, and in the same PR updates or removes every in-repo reference to what it changes, as `git grep` finds them when it lands.

1. [ ] After ADR-0001 items 1-4 (the request names its package, the secret mark, the refusal, grading by
   the named package's location table): the backend accepts single-document, package and
   pipeline-project submissions through MCP, and derives database endpoint ids.
2. [ ] Every reference to the pipeline plugin's validation adapter and to the validator's command line
   moves to the library's request entry points; one that exists only to test the
   adapter is deleted with it in item 3.
3. [ ] Remove the plugins' validation adapter, gap script and endpoint-id helper, the connector agent's
   self-install, and everything that exists only for them; agents submit through MCP. The bootstrap
   stays, unimported, because the pin guards still read its pin.
4. [ ] In their own PR: retire every reference to `VALIDATOR_PIN`.
5. [ ] Remove the bootstrap and every reference to `ANALITIQ_VALIDATOR_FROM_SOURCE`.
