# Changelog

## [0.1.5](https://github.com/analitiq-ai/claude-plugin-connector/compare/v0.1.4...v0.1.5) (2026-07-20)


### Features

* capture decimal & temporal precision in authored read maps ([#35](https://github.com/analitiq-ai/claude-plugin-connector/issues/35)) ([b461b85](https://github.com/analitiq-ai/claude-plugin-connector/commit/b461b85970f9002ae6dde4af34baded4cc10dd06))
* derive endpoint_id from full resource locator ([#31](https://github.com/analitiq-ai/claude-plugin-connector/issues/31)) ([999ac7f](https://github.com/analitiq-ai/claude-plugin-connector/commit/999ac7f1b2a6412e520e9674538b8787e0e04d6d))
* **endpoint-creator:** require Content-Type on JSON request bodies ([796a3b1](https://github.com/analitiq-ai/claude-plugin-connector/commit/796a3b1abf70fbef17bd24999b607f73a71533cc))


### Bug Fixes

* adopt contract rc11 and drop the limitations it lifts ([#46](https://github.com/analitiq-ai/claude-plugin-connector/issues/46)) ([cbb0d53](https://github.com/analitiq-ai/claude-plugin-connector/commit/cbb0d53f898d36b089587b884798569c41a10c9e))
* align plugin with published schema — function catalog, OAuth PKCE, connection-contract drift ([#36](https://github.com/analitiq-ai/claude-plugin-connector/issues/36)) ([80afe17](https://github.com/analitiq-ai/claude-plugin-connector/commit/80afe173279da70a2aadb9c5ed24e23a7c3c5156))
* consolidate connector/endpoint prose against the rc10 contract ([#45](https://github.com/analitiq-ai/claude-plugin-connector/issues/45)) ([9264b85](https://github.com/analitiq-ai/claude-plugin-connector/commit/9264b85d99c9bd2041f0df6aa2f7fe1a5b4a260d))
* pin validator to rc10 and repoint drift guard at the pinned package ([#44](https://github.com/analitiq-ai/claude-plugin-connector/issues/44)) ([6db68e5](https://github.com/analitiq-ai/claude-plugin-connector/commit/6db68e52149190e03a074d6863a11315a5395096))
* stop treating param annotations as a type-map coverage source ([#29](https://github.com/analitiq-ai/claude-plugin-connector/issues/29)) ([a4ec725](https://github.com/analitiq-ai/claude-plugin-connector/commit/a4ec725a2f256d2c55595da40fd6352f82fd273e))

## [0.1.4](https://github.com/analitiq-ai/claude-plugin-connector/compare/v0.1.3...v0.1.4) (2026-07-02)


### Features

* author WriteOperation.idempotency blocks (api-endpoint 9.1.0) ([#27](https://github.com/analitiq-ai/claude-plugin-connector/issues/27)) ([ffd835b](https://github.com/analitiq-ai/claude-plugin-connector/commit/ffd835b540e09bcddc142ff5c7f0c638dfb47afe))

## [0.1.3](https://github.com/analitiq-ai/claude-plugin-connector/compare/v0.1.2...v0.1.3) (2026-06-30)


### Features

* enforce endpoint filename equals endpoint_id in validation ([#24](https://github.com/analitiq-ai/claude-plugin-connector/issues/24)) ([87061cb](https://github.com/analitiq-ai/claude-plugin-connector/commit/87061cbc373e8dc6e7740289b5b6f5ec279c69ec))

## [0.1.2](https://github.com/analitiq-ai/claude-plugin-connector/compare/v0.1.1...v0.1.2) (2026-06-30)


### Features

* enforce bare-marker arrow_type sibling-key rules in endpoint validation ([#19](https://github.com/analitiq-ai/claude-plugin-connector/issues/19)) ([74527ba](https://github.com/analitiq-ai/claude-plugin-connector/commit/74527ba9e4a28acc5f62a4fd4db15efdd58f9b46))
* package validator as installable analitiq-connector-validator for standalone CI ([#22](https://github.com/analitiq-ai/claude-plugin-connector/issues/22)) ([0e25b10](https://github.com/analitiq-ai/claude-plugin-connector/commit/0e25b10d4f0a5ef4397f3e27a29ea324cc2487a7))


### Bug Fixes

* position-aware response-extraction scopes; value_path as response path ([#18](https://github.com/analitiq-ai/claude-plugin-connector/issues/18)) ([f09395a](https://github.com/analitiq-ai/claude-plugin-connector/commit/f09395af2dc06ee2feafdcb500c983296c2d4d33))

## [0.1.1](https://github.com/analitiq-ai/claude-plugin-connector/compare/v0.1.0...v0.1.1) (2026-06-29)


### Features

* contract-derived research + endpoint fan-out (ProviderFacts from published schemas) ([4dbb381](https://github.com/analitiq-ai/claude-plugin-connector/commit/4dbb381e4470a5c9a516dc8d27e20b2ddbe0bbf6))
* implement contract-derived research + endpoint fan-out, fix drift surfaces ([04a9f6a](https://github.com/analitiq-ai/claude-plugin-connector/commit/04a9f6a7f28f9fc5214c29d40dfd2bf3ddf1d340))
* type-map rule — schemaless/container natives must map to a container canonical ([fe1018b](https://github.com/analitiq-ai/claude-plugin-connector/commit/fe1018be4666640e35f1b9d863ac915d21ba91d7))


### Bug Fixes

* address PR [#14](https://github.com/analitiq-ai/claude-plugin-connector/issues/14) review — validator error-handling, test coverage, prompt wiring ([71f0350](https://github.com/analitiq-ai/claude-plugin-connector/commit/71f0350e5e7d8adc06dc009a97bc50b62851f6c1))
* drop unconditional tz-aware API date-time row in spec-type-maps ([#16](https://github.com/analitiq-ai/claude-plugin-connector/issues/16)) ([00e4bed](https://github.com/analitiq-ai/claude-plugin-connector/commit/00e4bedee8b1c04f7959e6678e6f1761cbcdb420)), closes [#12](https://github.com/analitiq-ai/claude-plugin-connector/issues/12)

## [unreleased]

### Fixed
- `connector-spec-api/spec-replication.md` had drifted from the published
  api-endpoint contract: it documented `cursor_mappings` keys
  (`name`/`value`/`filter_param`/`filter_operator`) and a
  `supported_methods` value (`"full"`) plus a `default_method` key that the
  schema rejects, and it omitted the `WindowCursorMapping` variant
  entirely. Rewrote the page to match `#/$defs/Replication`,
  `#/$defs/SingleCursorMapping`, and `#/$defs/WindowCursorMapping`, and to
  defer to the schema as the source of truth instead of restating its shape
  as prose (issue #9).
- `connector-spec-api/spec-pagination.md` had drifted the same way (found
  by generalizing the new guard): `stop_when` was documented as a string
  (`"page_empty"`) where the contract requires a predicate object, and the
  `link` (`next_link`/`rel`) and `keyset` (`next_cursor`) shapes did not
  match `#/$defs/LinkPagination` / `#/$defs/KeysetPagination`. Rewrote all
  five strategies to match the contract.

### Added
- `tests/connector_validator/test_spec_doc_examples.py` — validates the
  JSON examples embedded in the API spec docs (`spec-replication.md`,
  `spec-pagination.md`) against the matching `$defs` of the live
  `api-endpoint` schema, so those docs can't silently drift from the
  contract again.
- `test_endpoint_example_passes_against_live_schema` — validates every
  `examples/*/endpoints/*.json` document against the live api-endpoint
  schema (Layer 1). These endpoint examples previously had no automated
  schema check.

## [0.1.0]

### Added
- Initial release of the standalone `analitiq-connector-builder` plugin,
  extracted from the `analitiq-ai/ai-plugins-official` monorepo into its
  own repository. Authors connector and endpoint JSON documents that
  conform to the published Analitiq schema contract at
  `schemas.analitiq.ai` (`kind: api` and `kind: database`; storage kinds
  `file`/`s3`/`stdout` are stubbed pending engine support).
- Agent chain: `connector-builder` (orchestrator skill) →
  `connector-provider-researcher` → `{api,db,storage}-connector-creator`
  → `endpoint-creator` (API, parallel) → `connector-schema-validator`
  (loop) → `connector-drift-classifier`.
- Orchestrator modes: `build` (default), `update` (re-author an existing
  connector from current docs and re-version it), and `validate`
  (read-only validation of an on-disk connector).
- `scripts/validate_connector.py` (Layer 1 JSON Schema + Layer 2 semantic
  validators) with the pytest suite under `tests/connector_validator/`.
