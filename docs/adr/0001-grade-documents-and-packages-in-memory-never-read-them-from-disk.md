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
affects:
  - type: path
    pattern: "packages/validator/**"
  - type: path
    pattern: "packages/contract-models/src/analitiq/contracts/validation_requests.py"
  - type: path
    pattern: "packages/contract-models/src/analitiq/contracts/shared/common.py"
  - type: path
    pattern: "packages/contract-models/src/analitiq/contracts/*_package.py"
  - type: path
    pattern: "schemas/validate-package-request/**"
  - type: path
    pattern: "schemas/*-package/**"
  - type: path
    pattern: "rules/records/**"
  - type: path
    pattern: "plugins/*/skills/*/references/rules/**"
  - type: path
    pattern: "scripts/gen_pipeline_docs.py"
  - type: path
    pattern: "scripts/render_validator_claims.py"
  - type: path
    pattern: "tests/connector_builder/test_examples_validate.py"
  - type: path
    pattern: ".github/workflows/validator-release.yml"
review:
  tier: async
  tierReason: One component and its callers; reversible by restoring a path entry point.
provenance:
  authoredBy: agent
---

# ADR-0001: Grade documents and packages in memory, never read them from disk

## Context

`analitiq-validator` is the one gate for Analitiq documents. It runs in the Engine before a pipeline
runs, in the backend that grades what a plugin submits, and in DIP CI. The Engine and the backend
already hold documents in memory; only DIP CI and the plugins hold files.

The validator read packages from disk with its own idea of where each file sits. That made it own
filesystem behaviour — links, permissions, encodings, paths leading outside a package — none of which
decides whether a document is correct. That surface produced most of the review churn on the
validator: each fix to the reader exposed another filesystem edge.

Two published contracts settle most of what the reader used to guess: the request models
(`ValidateSingleDocumentRequest`, `ValidatePackageRequest`) carry document text keyed by path, and each
package schema's location table (its `patternProperties`) says where a document sits and what kind it
is. They do not yet settle the rest: the package request names no package, so nothing says which
location table applies; no package model states which document is its root; and nothing marks the
connection package's credentials location as holding secret values.

## Decision

We will make the validator a pure grader. Its only inputs are the published request models and, for
cross-package references, the pipeline bundle. No entry point takes a path and grading performs no
filesystem access.

A package request names its package, and the named package's model states its location table and its
root. A caller holding files selects them by that published location table, skipping every location the
contract marks secret, reads them as UTF-8, reports its own read errors, and builds the request. The
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
- Revisit if: a consumer appears that can only hand paths and cannot build a request, or one that can
  only call a command line.

## Action items

Each item lands only after the items before it, and updates every in-repo reader of what it changes in
the same PR. Items 5 and 6 also wait for ADR-0002 item 3, which moves the plugins off the validator.

1. [ ] The package request names its package, restricted to the published package schema names, and
   each package model states its root document.
2. [ ] The package contract marks the credentials location secret.
3. [ ] The package request refuses a key at a secret location of the package it names, as the caller's
   error.
4. [ ] DIP CI builds requests by selecting files with the named package's published location table,
   skipping secret locations.
5. [ ] Remove every path-taking entry point, the disk reader and the validator's own filename constants
   from `analitiq.validator`, with the findings and rule records only they produce.
6. [ ] Remove the `analitiq-validate` console script and its `main()`.
