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
