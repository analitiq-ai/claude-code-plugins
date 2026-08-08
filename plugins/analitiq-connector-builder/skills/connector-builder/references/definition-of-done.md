# Definition of Done

A self-check the creator agents run against their own output **before
returning `CreatorOutput`**. It is a gate, not a substitute for the
`connector-schema-validator` (whose agent file owns the authoritative
check/blind-spot list): this checklist covers **only what the validator cannot
enforce** — classification correctness, completeness against the provider's
documentation, the both-directions principle, driver-choice discipline, and
the non-JSON artifacts (package files, README) the in-plugin validator never
sees.

<!-- PROBE: connector-function-name-unchecked, write-body-path-typo-unresolved, tls-coherence-unchecked -->
Three things authors often assume are validated but are not — a `function`
name, a ref's resolvability outside a READ's `response.body` and either
operation's `response.metadata`, and TLS mode ↔ CA-certificate coherence.
Those belong on this list, not in the validator's column.

The kind-specific lists live at the end of each creator agent
(`api-connector-creator` / `db-connector-creator`); both also apply this
shared core.

## Shared core (both kinds)

- [ ] **Classification is correct.** `kind`, `auth.type`, and each
  `transport_type` match the provider's *actual* documented behavior —
  not merely a schema-valid value. (The validator checks the value is
  in-enum; it cannot check it is the right one.)
- [ ] **`connector_id` is the intended stable slug** (`RULE-CTOR-042`). (The
  schema checks the slug pattern, not that it is the slug the
  user/provider actually means.)
- [ ] **`display_name`, `description`, and `tags` are meaningful**, not
  placeholders.
- [ ] <!-- PROBE: read-map-completeness-unchecked, endpoint-pair-unresolved-through-read-map -->
  **The read map covers the provider's documented native
  vocabulary**, not just the subset that happened to appear in a sample.
  (Nothing checks read-map completeness for a database connector; for an
  API connector the validator only checks the natives the endpoints
  reference.)
- [ ] <!-- PROBE: connector-secret-literal-undetected -->
  **No secret value is embedded as a literal** anywhere (passwords,
  tokens, keys) — `RULE-SHRD-001`. (Nothing can tell a literal default from
  a leaked secret.)
- [ ] **No customer-specific value is baked into the connector**
  (`RULE-CTOR-028`) — no real host, tenant id, account id, or database
  name.
- [ ] <!-- PROBE: connector-function-name-unchecked, endpoint-function-name-unchecked -->
  **Every `function` name is in the registered catalog** (`RULE-SHRD-007`;
  the catalog is `value-expressions.md` §Function catalog). Nothing
  validates function names, so a typo or a planned-but-unregistered
  function ships silently and fails at connect.
- [ ] <!-- PROBE: write-body-path-typo-unresolved, scope-tail-unchecked -->
  **Every ref resolves to something a declaration produces.** What the
  validator proves is exactly the measured table in `value-expressions.md`
  (§Logical scopes) — read it before trusting any ref. Everything outside
  those cells is on you; the worst case is the write side, where a
  `success_when` typo makes the predicate hold unconditionally and every
  write reports success. Trace every unproved ref by hand
  (`lifecycle-phases.md`).
- [ ] **`default_transport` is the right default**, and any
  multi-transport split (auth / discovery / api origins) reflects the
  provider's real topology.
- [ ] **README is present** (`RULE-PKG-025`). (The in-plugin validator
  ignores README entirely.)
- [ ] **Both read and write land as a working unit for this system**
  (the both-directions-first-class *capability* principle) — scope was
  not cut to source-only or destination-only. This means the connector's
  read/write capability, not a write *type-map* file: an API connector
  realizes the write direction through endpoints/operations (and ships
  no write map), a database connector through its `pyproject.toml`
  entry-point registrations (`RULE-PKG-008`).
- [ ] **Version is consistent**: first release → `RULE-CTOR-032`; otherwise
  the drift verdict the orchestrator computed was applied.
