# Changelog

## [0.2.2](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-pipeline-builder-v0.2.1...analitiq-pipeline-builder-v0.2.2) (2026-09-04)


### Features

* structural records name their shape device where the constant exists ([77baef4](https://github.com/analitiq-ai/claude-code-plugins/commit/77baef42f7b21464e18952e8c8b50a1d1fab266f))
* structural records name their shape device where the constant exists ([8e3561a](https://github.com/analitiq-ai/claude-code-plugins/commit/8e3561a41644f6b4ccdfece52a275b65bc712922))
* **validator:** grade a recorded sample against the node declaring it ([bd20654](https://github.com/analitiq-ai/claude-code-plugins/commit/bd20654880161cbe07839a90648f6091389b3cf2))
* **validator:** grade a recorded sample against the node declaring it ([e7da677](https://github.com/analitiq-ai/claude-code-plugins/commit/e7da6779c6ccfbc798a9841a999fade208faba16))

## [0.2.1](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-pipeline-builder-v0.2.0...analitiq-pipeline-builder-v0.2.1) (2026-08-14)


### Features

* bump the runtime validator to rc20 and teach the write path_param record binding ([e18d5e8](https://github.com/analitiq-ai/claude-code-plugins/commit/e18d5e861bc3035d750c1e6e239a717fc46aa7c8))
* bump the runtime validator to rc20 and teach the write path_param record binding ([71cbb09](https://github.com/analitiq-ai/claude-code-plugins/commit/71cbb09b9e3944e3b4aebedd2ea8dc193705a2ba))
* **contract-models:** enforce database-only read features, catalogue three unregistered enforcers ([d3abea5](https://github.com/analitiq-ai/claude-code-plugins/commit/d3abea5c03541630f309987c43c02cada15759fa))
* **contract-models:** resolve response.body paths, let a write path_param read the record, gate endpoint transport_ref ([5d7f6a5](https://github.com/analitiq-ai/claude-code-plugins/commit/5d7f6a5e23971a5e9ca465de6370443394799602))
* derive the gated vocabulary set from the models instead of listing it ([8e15f3f](https://github.com/analitiq-ai/claude-code-plugins/commit/8e15f3f2894364a0292ee8a6a52250b1ce99e5d2))
* give every closed vocabulary an id prose can cite ([6b01a67](https://github.com/analitiq-ai/claude-code-plugins/commit/6b01a67128c9cb9434a076d8732ffd8dd0e63e9e))
* give every rule an id, whether or not anything enforces it ([c26883b](https://github.com/analitiq-ai/claude-code-plugins/commit/c26883b1e73f3f1d8c417ace14d293a08bd625a8))
* give every rule an id, whether or not anything enforces it ([372272c](https://github.com/analitiq-ai/claude-code-plugins/commit/372272ca84acd99d3d36db8bb09bce433d029e38))
* pin every validator-behavior claim in plugin prose to an executable probe ([5607766](https://github.com/analitiq-ai/claude-code-plugins/commit/560776636dd532709fbef535d8113883df8a5d00))
* pin every validator-behavior claim in plugin prose to an executable probe ([261a361](https://github.com/analitiq-ai/claude-code-plugins/commit/261a36190d6c0198f90d9874b45af7904073615a))
* register the rules the validator enforces ([ff2a4ce](https://github.com/analitiq-ai/claude-code-plugins/commit/ff2a4ceb82a97dc6712d2f558b907557272b1699))
* sweep the second pydantic decorator, and register what it was hiding ([a1cef6c](https://github.com/analitiq-ai/claude-code-plugins/commit/a1cef6cdb185b98c73a7b9794bb4db033a9c4ed0))


### Bug Fixes

* **analitiq-connector-builder:** collapse the runtime validator pin to one owner ([#122](https://github.com/analitiq-ai/claude-code-plugins/issues/122)) ([cccc8ae](https://github.com/analitiq-ai/claude-code-plugins/commit/cccc8ae0ef30649b2382646a10d5be6d9053a439))
* **analitiq-connector-builder:** register endpoint-transport-ref and regenerate the advisory reference ([378c8e1](https://github.com/analitiq-ai/claude-code-plugins/commit/378c8e12b4dfa39033e93d2c7f99240e6e58be92))
* **analitiq-pipeline-builder:** retire the last "no enum can enumerate it" claims ([acf1976](https://github.com/analitiq-ai/claude-code-plugins/commit/acf1976fcf2fb3d01b05ed26301fcd831876d7da))
* apply round-one review findings across records, renderer, hooks and guards ([b0b7104](https://github.com/analitiq-ai/claude-code-plugins/commit/b0b7104fbbea6bb9565ea85b07f2ce9240cc9ff5))
* apply the round-1 review — pin the census's own texts, widen ADV-STRM-014, guard the enforcer direction ([ad2c0a3](https://github.com/analitiq-ai/claude-code-plugins/commit/ad2c0a3dd19ae92c9a69e30a6708963ff4296cd9))
* apply the round-1 review — pin the closure claims, close the optional-member gap ([cf5ae7d](https://github.com/analitiq-ai/claude-code-plugins/commit/cf5ae7d0d8ccd3e97b887100de6f845608703ab8))
* apply the round-2 review — one table parser, and own the claims nobody read ([06f0042](https://github.com/analitiq-ai/claude-code-plugins/commit/06f0042ad6291947f39e618c71ba00cc67c8b84e))
* apply the round-2 review — the write side compiles ECMA-262 matchers too ([c5954e8](https://github.com/analitiq-ai/claude-code-plugins/commit/c5954e8c92222cf1107ef1b3a1437bd374b4e6e4))
* apply the round-3 review — one properties helper, and stop claiming a derivation ([a654446](https://github.com/analitiq-ai/claude-code-plugins/commit/a654446883645bdfed8d5a8b2638ab045a91fce4))
* bump the runtime validator pin to 1.0.0rc19 ([#119](https://github.com/analitiq-ai/claude-code-plugins/issues/119)) ([d92705e](https://github.com/analitiq-ai/claude-code-plugins/commit/d92705e70d59e18c81cc99f59dc5de0c69a602f3))
* close the gate's own blind spots the review hunt proved reachable ([72924bd](https://github.com/analitiq-ai/claude-code-plugins/commit/72924bdb90b9ae0a6e78fade10c35871194119f8))
* close the scope-mismatch and citation-reachability classes, not just their instances ([ef37a73](https://github.com/analitiq-ai/claude-code-plugins/commit/ef37a736045dfa2f812278c0e0fa864a12f51448))
* **contract-models:** close the API write-mode hole, and address every declarable field ([60a5005](https://github.com/analitiq-ai/claude-code-plugins/commit/60a500572a75c15da5649ebd820fa5d4dd0ed8ee))
* **contract-models:** make the illegal destination and validation-rule shapes unrepresentable ([45c7c2c](https://github.com/analitiq-ai/claude-code-plugins/commit/45c7c2caf66c76e7eb0b5eea00f6c3ce7799dcfd))
* **contract-models:** make the illegal destination and validation-rule shapes unrepresentable ([faf8434](https://github.com/analitiq-ai/claude-code-plugins/commit/faf843484d2e2f45f00a51d6f64104c3eeaf6708)), closes [#112](https://github.com/analitiq-ai/claude-code-plugins/issues/112) [#113](https://github.com/analitiq-ai/claude-code-plugins/issues/113)
* **contract-models:** materialize by memoized fold, and pin it against a naive one ([da1172d](https://github.com/analitiq-ai/claude-code-plugins/commit/da1172d558da5f2e035a6f1777a9203ac5248217))
* harden the round-two guards and retier the lookup-map rule ([3b94bdc](https://github.com/analitiq-ai/claude-code-plugins/commit/3b94bdccd1197772c79de09804117b5921caa8b2))
* **pipeline-builder:** mark the destination and source sketches as validating fragments ([ffd7ff4](https://github.com/analitiq-ai/claude-code-plugins/commit/ffd7ff4a8668bd2fd6445b11d30e5941da42dbd5))
* **pipeline-builder:** retag the four pseudo-JSON agent fences ([16510bf](https://github.com/analitiq-ai/claude-code-plugins/commit/16510bf6c1e256808e769d0281859b29aaa628ca))
* **plugins:** anchor agent paths, preload spec skills, unship contributor guides ([b3a762e](https://github.com/analitiq-ai/claude-code-plugins/commit/b3a762ec5036dedb81f9bcec44b5cdee9b76fedc))
* **prose:** state the mechanism, not the cardinality ([bf6a0a3](https://github.com/analitiq-ai/claude-code-plugins/commit/bf6a0a39c5282c834cb0ec695d2bd03bddd051be))
* **prose:** state the mechanism, not the cardinality ([00c7595](https://github.com/analitiq-ai/claude-code-plugins/commit/00c7595143e5909a0c4cc57bb0a441973105dbd5))
* release contract-models and validator 1.0.0rc21, move the runtime pin ([74f5ea4](https://github.com/analitiq-ai/claude-code-plugins/commit/74f5ea439540d8c45a89b42095dcd4e1553d282b))
* resolve the DeepSource findings and two prose leftovers ([bebedba](https://github.com/analitiq-ai/claude-code-plugins/commit/bebedbac2a630b9855de700e51aae5eacff92502))

## [0.2.0](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-pipeline-builder-v0.1.3...analitiq-pipeline-builder-v0.2.0) (2026-07-31)


### ⚠ BREAKING CHANGES

* **contract-models:** token-array get paths, single-segment targets, explicit value kind, truncate_insert, drop max_concurrent_batches, bound page size (rc19) ([#109](https://github.com/analitiq-ai/claude-code-plugins/issues/109))

### Features

* **contract-models:** token-array get paths, single-segment targets, explicit value kind, truncate_insert, drop max_concurrent_batches, bound page size (rc19) ([#109](https://github.com/analitiq-ai/claude-code-plugins/issues/109)) ([c0069a8](https://github.com/analitiq-ai/claude-code-plugins/commit/c0069a80a5b54e204b14f37ebdda5710f08c1e64))

## [0.1.3](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-pipeline-builder-v0.1.2...analitiq-pipeline-builder-v0.1.3) (2026-07-23)


### Bug Fixes

* **analitiq-pipeline-builder:** trigger the CA-material rule on mode meaning, not libpq spellings ([#84](https://github.com/analitiq-ai/claude-code-plugins/issues/84)) ([7cea32d](https://github.com/analitiq-ai/claude-code-plugins/commit/7cea32daf5fcfe3eb837b23c5865eaee6dc7207b))
* trim the canonical-type vocabulary to the engine-executable set, generated from the published grammar manifest ([#86](https://github.com/analitiq-ai/claude-code-plugins/issues/86)) ([b94cafa](https://github.com/analitiq-ai/claude-code-plugins/commit/b94cafad3ccc2fa924d29752eb919b6ffad2a685))

## [0.1.2](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-pipeline-builder-v0.1.1...analitiq-pipeline-builder-v0.1.2) (2026-07-23)


### Bug Fixes

* bump the runtime validator pin to 1.0.0rc14 and guard it in CI ([e22bff4](https://github.com/analitiq-ai/claude-code-plugins/commit/e22bff44c9a224f87425fe0ea75deeef1d4a48b8))

## [0.1.1](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-pipeline-builder-v0.1.0...analitiq-pipeline-builder-v0.1.1) (2026-07-22)


### Features

* **analitiq-pipeline-builder:** add new-destination-table flow (author-new-table sub-mode) ([#64](https://github.com/analitiq-ai/claude-code-plugins/issues/64)) ([cd449ae](https://github.com/analitiq-ai/claude-code-plugins/commit/cd449aea4f0dcc2b173288c0711e55e3dce6931f))
* **analitiq-pipeline-builder:** author connection-scoped type maps for discovered private-endpoint natives ([#62](https://github.com/analitiq-ai/claude-code-plugins/issues/62)) ([2ca9379](https://github.com/analitiq-ai/claude-code-plugins/commit/2ca9379e00da422076aacd7507a8cb17434c10a3))
* restructure as a multi-plugin monorepo owning the contract surface ([#51](https://github.com/analitiq-ai/claude-code-plugins/issues/51)) ([8c75001](https://github.com/analitiq-ai/claude-code-plugins/commit/8c750017a414eee2f2f423fe27a3befd0fb9d128))


### Bug Fixes

* **analitiq-pipeline-builder:** state the directory-slug convention once, gate hand-typed patterns ([#57](https://github.com/analitiq-ai/claude-code-plugins/issues/57)) ([937e8f7](https://github.com/analitiq-ai/claude-code-plugins/commit/937e8f7b8a0fbc7304deb5fd9f25774f8aceea6f))
* bump the runtime validator pin to 1.0.0rc13 ([#68](https://github.com/analitiq-ai/claude-code-plugins/issues/68)) ([eb88e23](https://github.com/analitiq-ai/claude-code-plugins/commit/eb88e23f7224879caf8ee34abc82397ee27dad13))
* resolve the DeepSource python baseline ([#56](https://github.com/analitiq-ai/claude-code-plugins/issues/56)) ([359e7fb](https://github.com/analitiq-ai/claude-code-plugins/commit/359e7fbb0e6059549f4e03d25472ed93fd52221d))

## [unreleased]

### Changed
- Bumped the consumed contract to `analitiq-validator==1.0.0rc6`
  (`analitiq-contract-models==1.0.0rc6` transitively). Draft pipeline bundles now
  validate with `require_runnable=False`, so a not-yet-runnable draft produces no
  finding (runnability is enforced only once the pipeline is `active`); documented
  the `sidecar:` `secret_refs` scheme. rc5 added the `endpoint-filename` gate for
  stem-addressed database endpoints in `validate_document`; rc6 exports
  `endpoint_filename_findings` publicly, which the bundle path now reuses (below).
- Replaced the bundled `scripts/validate_pipeline.py` with a thin adapter
  (`src/scripts/validate.py`) over the published, offline `analitiq-validator` +
  `analitiq-contract-models` packages; it self-installs the pinned version into a
  managed virtualenv on first use. Added `src/scripts/endpoint_id.py` for the
  derived database-endpoint identity.
- Aligned all authoring to the current published contracts: connection
  `parameters`/`selections`/`secret_refs` (secrets as `env:` pointers, no
  `values` envelope); stream discriminated `endpoint_ref` carrying
  `database_object`, flat `conflict_keys`, and the `get`/`pipe`/`fn` expression
  grammar; database-endpoint derived `endpoint_id`.
- Moved the plugin package (`.claude-plugin/`, `agents/`, `skills/`, `scripts/`)
  under `src/`, separating it from repo-management files.

### Added
- Bundle validation now flags an `endpoint-filename` finding when a connection-scoped
  private endpoint file is not named `<endpoint_id>.json` — the engine locates
  endpoints by filename stem, so a mis-named file (correct id inside, wrong name)
  passes referential checks but fails at runtime. It calls the published
  `endpoint_filename_findings` helper (the bundle validator runs on a filename-less
  dict and can't reach the gate itself). Edit mode also validates a changed
  pipeline/stream's referenced closure (its connections and their private
  endpoints), so a stale or mis-named referenced artifact surfaces at edit time.
- Edit mode in the `pipeline-builder` orchestrator — surgical, in-place changes
  to an existing pipeline / stream / connection / database-endpoint.

## [0.1.0]

### Added
- Initial release of the standalone `analitiq-pipeline-builder` plugin,
  extracted from the `analitiq-ai/ai-plugins-official` monorepo into its
  own repository. Authors pipeline, stream, connection, and
  database-endpoint JSON documents that conform to the published Analitiq
  schema contract at `schemas.analitiq.ai`. Downloads connectors from the
  DIP registry and wires them into complete pipelines; it does not create
  connectors and calls no registration APIs.
- Agent chain: `pipeline-builder` (orchestrator skill) →
  `pipeline-provider-researcher` → `registry-browser` →
  `connection-creator` → `private-endpoint-creator` (DB only) →
  `pipeline-creator` → `stream-creator` (parallel) →
  `pipeline-schema-validator` → `pipeline-drift-classifier`.
- `scripts/validate_pipeline.py` (Layer 1 JSON Schema + Layer 2 semantic
  validators) with the pytest suite under `tests/pipeline_validator/`.
