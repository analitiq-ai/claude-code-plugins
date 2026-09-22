---
schemaVersion: 0.1.0
id: "0001"
title: "Grade documents and packages in memory, never read them from disk"
status: draft
date: 2026-09-22
deciders: []
tags: [validator, contracts]
scope: component
reversibility: two-way-door
blastRadius: component
relatesTo: ["0002"]
review:
  tier: async
  tierReason: One component and its callers; reversible by restoring a path entry point.
provenance:
  authoredBy: agent
---

# ADR-0001: Grade documents and packages in memory, never read them from disk

## Context

`analitiq-validator` grades Analitiq documents for the Engine, for the backend that receives what the
plugins author, and for DIP CI. It reads packages from disk with its own idea of where each file sits,
so it carries filesystem behaviour that has nothing to do with whether a document is correct.

The published contracts describe most of what the validator needs: request models carrying document
text, and package schemas saying where each document sits and what kind it is. The package request
does not yet name its package, and no location is marked as holding secrets.

## Decision

We will make the validator grade only the published request models, in memory. A package request
names its package, and that package's published schema decides which files belong to it and where each
sits. No entry point takes a path. A caller that holds files builds the request from that schema and
leaves out every location the contract marks secret. The validator ships no command-line interface.

## Options considered

### Option A: In-memory only (chosen)

**Pros:** One input shape for every caller; filesystem handling leaves the validator.
**Cons:** Callers that hold files must build the request themselves.

### Option B: Keep a disk reader beside the in-memory path

**Pros:** DIP CI and local runs can hand a directory.
**Cons:** Two readers can disagree on which files are in a package, and the filesystem handling stays
in the validator.

### Option C: Do nothing (disk reader only)

**Pros:** No change for current callers.
**Cons:** Callers holding documents in memory must write them to disk to grade them.

### Option D: In-memory library plus a command line reading a request on stdin

**Pros:** Shell and CI scripts can grade without writing Python.
**Cons:** No consumer needs it once the plugins submit through the backend (ADR-0002) and DIP CI calls
the library; it is a second surface to version and test.

## Trade-offs

Each caller that holds files writes a small reader, and those readers agree only because they follow
the same published schema, not shared code. A shell user must write a few lines of Python to grade a
package.

## Consequences

- Easier: testing without fixtures on disk; one input shape to reason about.
- Harder: DIP CI owns reading files and reporting what it cannot read.
- The path-taking entry points and the command line are removed only after every caller has moved to
  the request entry points.
- **How we would know this was wrong:** two callers build different requests from the same directory.
- Revisit if: a consumer appears that can only hand paths or only call a command line.
