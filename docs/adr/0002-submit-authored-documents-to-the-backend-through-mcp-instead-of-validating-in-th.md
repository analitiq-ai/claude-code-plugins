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
  tierReason: Moves the authoring gate for both plugins and retires the plugins' runtime validator pin.
provenance:
  authoredBy: agent
---

# ADR-0002: Submit authored documents to the backend through MCP instead of validating in the plugins

## Context

The connector and pipeline plugins grade their own output with a copy of `analitiq-validator` they
install at run time, at a pinned version. The backend grades what the plugins submit with its own
copy. An authored document therefore has two gates at independently chosen versions, and keeping the
plugins' pin publishable takes CI guards and release rules of its own.

## Decision

We will make the backend the plugins' only gate. The plugins submit what they author to the backend
through MCP and act on the findings it returns. They do not install, import or pin the validator.

## Options considered

### Option A: Submit through MCP, no local validator (chosen)

**Pros:** One gate per document; the plugins lose their bootstrap install and pin.
**Cons:** Authoring needs the backend reachable.

### Option B: Keep local validation in the plugins (status quo)

**Pros:** Works without the backend; fast local feedback.
**Cons:** A second gate at a separately chosen version; the pin and its release rules stay; every
plugin run pays a bootstrap install.

### Option C: Local pre-check plus backend submission

**Pros:** Fast feedback before the round trip.
**Cons:** Two validators over the same documents can disagree, and it keeps all of Option B's pin
machinery.

## Trade-offs

Authoring stops when the backend is unreachable, and each check is a network round trip. The plugins
still teach the contract from this repo while the backend enforces the validator it has deployed, so
the backend must deploy a contract change before the plugins teach it.

## Consequences

- Easier: one verdict per document; the plugins shed their bootstrap and adapter code.
- Harder: the plugins depend on the backend being available; the backend owns the MCP interface and
  must accept every submission the plugins make, and run every check the plugins run today, before
  they switch to it.
- **How we would know this was wrong:** authoring routinely fails on backend unavailability, or users
  need to author without access to the backend.
- Revisit if: an offline authoring use case appears.
