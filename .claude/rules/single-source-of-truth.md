# Rule: the published schema is the single source of truth (drift policy)

Governs every surface that could restate what the contract defines. The general
invariant is `no-drift-surfaces.md`; this file is the policy it serves and the
mechanisms that carry it.

The published schema is the single source of truth. **Never restate what it
defines — reference or load it.** Carry only craft the schema can't express
(judgment, idioms, gotchas, workflow). That splits everything into **contract**
(don't duplicate — field shapes, enums, vocabularies, `$schema` URLs) and **craft**
(keep — *how* to choose, the "why", provider gotchas). The mechanisms:

- **The live schema is the contract — enforce it, don't restate it.** The validator
  checks each document against the contract models **offline**, no runtime schema
  fetch, so authoring and validation agree on one contract.
- **The rule registry is the source of truth.** One machine-readable record per rule
  in `rules/records/*.yaml` (schema in `rules/SCHEMA.md`), with an immutable id;
  docs, rendered references and prose citations are generated from or validated
  against it, never the reverse. A record answers independently: `tier` (shape,
  coherence, reference, process, choice), `validator` (what rejects a
  violation, absent when nothing does) and `severity`. Enforcement is ordinary
  Python: a rule one document settles alone is a `@model_validator` raising through
  `rules.violation`; a rule needing a second document in hand is a check in
  `analitiq.validator`, which is why that package exists. Nothing is dispatched from
  the record, so a rule is applied by a symbol that exists or by nothing at all.
  `scripts/render_rules.py` validates every record, resolves every `validator`
  against the live models and validator, and compiles the `rules.json` the wheel
  ships; `render_rule_reference.py` renders one reference file per artifact kind
  into each plugin, and `gen_pipeline_docs.py` renders the remaining contract-owned
  facts into its prose blocks. An obligation with no record is a missing record, not
  a sentence to hand-write.
- **Fetch-once, pass-down** — an orchestrator hands the live contract schema URLs to
  its researcher, and the creators read the same schemas as vocabulary.
- **Drift-check CI** for anything that must stay duplicated as decision logic (the
  `enum-mappers`, say): `tests/connector_builder/test_schema_drift.py` reads the enum
  sets from the pinned contract package and fails on divergence. The pipeline plugin
  solves the same problem by *generating* contract-owned facts into its prose. Prose
  about **what the validator does or does not check** is pinned by executable probes
  in `scripts/render_validator_claims.py`.

Enum lists in rule files, `CLAUDE.md` or skill prose are **illustrative**; the authoritative
definition is always the live schema, or the vendored grammar for Arrow types.
Craft the schema never defined (the `ssl_mode` vocabulary, driver-selection order,
datetime naive/tz judgment) is not drift-exposed and stays.
