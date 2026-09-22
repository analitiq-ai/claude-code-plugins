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

`analitiq-validator` is the one gate for Analitiq documents. It runs in the Engine before a pipeline
runs, in the backend that grades what a plugin submits, and in DIP CI. The backend holds documents
in memory, and so does the Engine as it stands; a consumer that holds files instead builds a
request, as DIP CI and the plugins do.

The validator reads packages from disk with its own idea of where each file sits. That makes it own
filesystem behaviour — links, permissions, encodings, paths leading outside a package — none of which
decides whether a document is correct, and each of which is a separate defect surface inside the gate.

Two published contracts settle most of what the reader guesses: `ValidatePackageRequest` carries
document text keyed by path inside the package, `ValidateSingleDocumentRequest` carries one document's
text and its schema name, and each package schema's location table (its `patternProperties`) says
where a document sits and what kind it is. They do not yet settle the rest: the package request names no package, so nothing says which
location table applies; no package model states which document is its root; and nothing marks the
connection package's credentials location as holding secret values.

## Decision

We will make the validator a pure grader. Its only inputs are the published request models and, for
cross-package references, the pipeline bundle. No entry point takes a path and grading reads no
document or package from a filesystem.

A package request names its package, and the named package's model states its location table and its
root. A caller holding files selects them by that published location table, skipping every location the
contract marks secret, reads them as UTF-8, and builds the request. It follows no link and reads no
entry whose resolved path leaves the package root; such an entry, like an unreadable file, is its own
finding to report and stays out of the request. The
contract marks the credentials location secret, and a package request holding a key at a secret
location of its package is refused as the caller's error, so no credential enters a request.

The validator ships no command-line interface. Every consumer calls it as a library; a command line
would need file input, which is the disk reader this record removes.

## Options considered

### Option A: In-memory only (chosen)

| Dimension | Assessment |
|---|---|
| Fit to consumers | Engine and backend hold documents in memory already |
| Determinism | Pure function of the request; same request, same verdict everywhere |
| Surface | Filesystem class of defects leaves the validator entirely |
| Cost to file holders | DIP CI must build the request itself |

### Option B: Keep a disk reader beside the in-memory path

**Pros:** DIP CI and local runs can hand a directory.
**Cons:** Two readers give two answers to "which files are in the package". The reader keeps the whole
filesystem surface inside the gate, and it is used only by callers that can build the request.

### Option C: Do nothing (disk reader only)

**Pros:** No change for current callers.
**Cons:** The backend and Engine must write documents to disk to grade them. The filesystem defect
class stays in the gate.

### Option D: In-memory library plus a CLI reading a request on stdin

**Pros:** Shell and CI scripts can grade without writing Python.
**Cons:** Once the plugins submit through the backend (ADR-0002) and DIP CI builds requests as a
library caller, no consumer needs a command line. It is a second entry surface to version and test.

## Trade-offs

Each file holder writes a small reader. Their agreement rests on all of them selecting by the same
contract rule (the published location table and its secret marks), not on shared code. A file the
caller cannot read never reaches the validator, so reporting it is the caller's job. A shell user must
write a few lines of Python to grade a package.

## Consequences

- Easier: testing (no fixtures on disk), reasoning about verdicts, running in any host.
- Harder: DIP CI owns reading files and reporting read errors.
- **How we would know this was wrong:** two file holders build different requests from the same
  directory, or a filesystem-derived defect has to be fixed inside the validator again.
- Ordering: the path-taking entry points and the command line are removed only after every caller has
  moved to the request entry points, the plugins by ADR-0002 and DIP CI by building requests.
- Revisit if: a consumer appears that can only hand paths and cannot build a request, or one that can
  only call a command line.
