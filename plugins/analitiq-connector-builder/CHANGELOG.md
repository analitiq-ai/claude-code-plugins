# Changelog

## [0.2.2](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-connector-builder-v0.2.1...analitiq-connector-builder-v0.2.2) (2026-08-20)


### Bug Fixes

* land the rules audit's prose findings ([23343f2](https://github.com/analitiq-ai/claude-code-plugins/commit/23343f27a72d4120efa87d33287732714bc39fcd))
* shape-check the connector document's untyped expression sites ([e485461](https://github.com/analitiq-ai/claude-code-plugins/commit/e48546108858b9c5157de301c674b49f5b530a48))
* shape-check the connector document's untyped expression sites ([5a7d481](https://github.com/analitiq-ai/claude-code-plugins/commit/5a7d48128aa0b975f6891a75130a828c51110a33)), closes [#172](https://github.com/analitiq-ai/claude-code-plugins/issues/172)
* teach the case rule for API read maps — uppercase exact, cased regex ([5c2caf9](https://github.com/analitiq-ai/claude-code-plugins/commit/5c2caf95e84d2735671702a69774a3ca3cdee6e3))
* teach the case rule for API read maps — uppercase exact, never regex ([135921f](https://github.com/analitiq-ai/claude-code-plugins/commit/135921ffbb2f1d1b360478df66854de184464fbe))

## [0.2.1](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-connector-builder-v0.2.0...analitiq-connector-builder-v0.2.1) (2026-08-14)


### Features

* **analitiq-connector-builder:** author the rc17 SQL write path, not the removed rc13 hooks ([#96](https://github.com/analitiq-ai/claude-code-plugins/issues/96)) ([e629717](https://github.com/analitiq-ai/claude-code-plugins/commit/e629717ea8e76c158b551131e68161d15cf5c5fa))
* bump the runtime validator to rc20 and teach the write path_param record binding ([e18d5e8](https://github.com/analitiq-ai/claude-code-plugins/commit/e18d5e861bc3035d750c1e6e239a717fc46aa7c8))
* bump the runtime validator to rc20 and teach the write path_param record binding ([71cbb09](https://github.com/analitiq-ai/claude-code-plugins/commit/71cbb09b9e3944e3b4aebedd2ea8dc193705a2ba))
* **contract-models:** enforce database-only read features, catalogue three unregistered enforcers ([d3abea5](https://github.com/analitiq-ai/claude-code-plugins/commit/d3abea5c03541630f309987c43c02cada15759fa))
* **contract-models:** resolve response.body paths, let a write path_param read the record, gate endpoint transport_ref ([5d7f6a5](https://github.com/analitiq-ai/claude-code-plugins/commit/5d7f6a5e23971a5e9ca465de6370443394799602))
* give every closed vocabulary an id prose can cite ([6b01a67](https://github.com/analitiq-ai/claude-code-plugins/commit/6b01a67128c9cb9434a076d8732ffd8dd0e63e9e))
* give every rule an id, whether or not anything enforces it ([c26883b](https://github.com/analitiq-ai/claude-code-plugins/commit/c26883b1e73f3f1d8c417ace14d293a08bd625a8))
* give every rule an id, whether or not anything enforces it ([372272c](https://github.com/analitiq-ai/claude-code-plugins/commit/372272ca84acd99d3d36db8bb09bce433d029e38))
* hold a connector's value expressions to the scope vocabulary ([ae332c2](https://github.com/analitiq-ai/claude-code-plugins/commit/ae332c246282fff4ad4d0757f6ae7e0c0bc9d291))
* pin every validator-behavior claim in plugin prose to an executable probe ([5607766](https://github.com/analitiq-ai/claude-code-plugins/commit/560776636dd532709fbef535d8113883df8a5d00))
* pin every validator-behavior claim in plugin prose to an executable probe ([261a361](https://github.com/analitiq-ai/claude-code-plugins/commit/261a36190d6c0198f90d9874b45af7904073615a))
* register the request vocabularies the endpoint prose was carrying by hand ([6779a5a](https://github.com/analitiq-ai/claude-code-plugins/commit/6779a5a0f1b6fa1d828ef093770a2719423fa0c9))
* register the rules the validator enforces ([ff2a4ce](https://github.com/analitiq-ai/claude-code-plugins/commit/ff2a4ceb82a97dc6712d2f558b907557272b1699))
* sweep the second pydantic decorator, and register what it was hiding ([a1cef6c](https://github.com/analitiq-ai/claude-code-plugins/commit/a1cef6cdb185b98c73a7b9794bb4db033a9c4ed0))


### Bug Fixes

* **analitiq-connector-builder:** add keyset to the spec-api index's pagination list ([583013d](https://github.com/analitiq-ai/claude-code-plugins/commit/583013d48a9b9cc587884e9b7a4487718711981f))
* **analitiq-connector-builder:** collapse the runtime validator pin to one owner ([#122](https://github.com/analitiq-ai/claude-code-plugins/issues/122)) ([cccc8ae](https://github.com/analitiq-ai/claude-code-plugins/commit/cccc8ae0ef30649b2382646a10d5be6d9053a439))
* **analitiq-connector-builder:** register endpoint-transport-ref and regenerate the advisory reference ([378c8e1](https://github.com/analitiq-ai/claude-code-plugins/commit/378c8e12b4dfa39033e93d2c7f99240e6e58be92))
* **analitiq-connector-builder:** say which response sub-scopes are proved, not "response.*" ([2d03a3a](https://github.com/analitiq-ai/claude-code-plugins/commit/2d03a3a321d5bc39d5fd1bc63c43df3f3435f1ad))
* **analitiq-connector-builder:** state the response-scope guarantees from the measured table ([0a21b1d](https://github.com/analitiq-ai/claude-code-plugins/commit/0a21b1dd84adbb227aa844389259be611e88d81c))
* **analitiq-connector-builder:** teach the write path_param binding where the endpoint agent reads ([547d732](https://github.com/analitiq-ai/claude-code-plugins/commit/547d7322e41a17ed5b600acbd21b5a2bdbba8a8b))
* apply round-one review findings across records, renderer, hooks and guards ([b0b7104](https://github.com/analitiq-ai/claude-code-plugins/commit/b0b7104fbbea6bb9565ea85b07f2ce9240cc9ff5))
* apply the round-1 review — guard the ungated projections, gate the exemption ([d8c12ea](https://github.com/analitiq-ai/claude-code-plugins/commit/d8c12eabd91d74301d1fbd7a4985d78aff2fea2b))
* apply the round-1 review — pin the census's own texts, widen ADV-STRM-014, guard the enforcer direction ([ad2c0a3](https://github.com/analitiq-ai/claude-code-plugins/commit/ad2c0a3dd19ae92c9a69e30a6708963ff4296cd9))
* apply the round-1 review — pin the closure claims, close the optional-member gap ([cf5ae7d](https://github.com/analitiq-ai/claude-code-plugins/commit/cf5ae7d0d8ccd3e97b887100de6f845608703ab8))
* apply the round-1 review — restore the orchestrator's enum copy, pin the predicate keys, correct four citations ([bb9f542](https://github.com/analitiq-ai/claude-code-plugins/commit/bb9f54294748ea166c76a1c194ec3d0626685f8a))
* apply the round-2 review — one no-token-split policy for every fill ([51332c9](https://github.com/analitiq-ai/claude-code-plugins/commit/51332c9b1de2e08e8f70d0e5e2e2529e76515e43))
* apply the round-2 review — one table parser, and own the claims nobody read ([06f0042](https://github.com/analitiq-ai/claude-code-plugins/commit/06f0042ad6291947f39e618c71ba00cc67c8b84e))
* apply the round-2 review — the write side compiles ECMA-262 matchers too ([c5954e8](https://github.com/analitiq-ai/claude-code-plugins/commit/c5954e8c92222cf1107ef1b3a1437bd374b4e6e4))
* apply the round-3 review — one properties helper, and stop claiming a derivation ([a654446](https://github.com/analitiq-ai/claude-code-plugins/commit/a654446883645bdfed8d5a8b2638ab045a91fce4))
* apply the round-8 review — an identity axis for the fixture, and a gate for every ignored tree ([cc8bc5d](https://github.com/analitiq-ai/claude-code-plugins/commit/cc8bc5df258b7cba070853e46ea8952225882ec3))
* apply the round-four findings — six prose and dead-code leftovers ([6774c3e](https://github.com/analitiq-ai/claude-code-plugins/commit/6774c3ee7de4f9af2b47f30a3ff2dd9065b771c0))
* bound every numeric contract field in every spelling it has ([f5af9a5](https://github.com/analitiq-ai/claude-code-plugins/commit/f5af9a551d56daf160f913d0c8eab539e6188dd5))
* bound every numeric contract field in every spelling it has ([77c17e0](https://github.com/analitiq-ai/claude-code-plugins/commit/77c17e0061bc1147fa393cf84ee3bfc26411ff9b)), closes [#116](https://github.com/analitiq-ai/claude-code-plugins/issues/116) [#111](https://github.com/analitiq-ai/claude-code-plugins/issues/111)
* bump the runtime validator pin to 1.0.0rc19 ([#119](https://github.com/analitiq-ai/claude-code-plugins/issues/119)) ([d92705e](https://github.com/analitiq-ai/claude-code-plugins/commit/d92705e70d59e18c81cc99f59dc5de0c69a602f3))
* close every rendered rule row on its own line ([2e7ff71](https://github.com/analitiq-ai/claude-code-plugins/commit/2e7ff71514376861fbfd5de0ab36b11014c0e6e7))
* close the gate's own blind spots the review hunt proved reachable ([72924bd](https://github.com/analitiq-ai/claude-code-plugins/commit/72924bdb90b9ae0a6e78fade10c35871194119f8))
* close the scope-mismatch and citation-reachability classes, not just their instances ([ef37a73](https://github.com/analitiq-ai/claude-code-plugins/commit/ef37a736045dfa2f812278c0e0fa864a12f51448))
* **connector-builder:** cite the release-version heading exactly ([3575b95](https://github.com/analitiq-ai/claude-code-plugins/commit/3575b95bf96008ec9478ff976346c76e068b65b3))
* **connector-builder:** close the reference guard's frontmatter blind spot and the dangling citation hiding in it ([0697a30](https://github.com/analitiq-ai/claude-code-plugins/commit/0697a303b7c706f5eabb5e43a24ea5bd5c0031a0))
* **connector-builder:** render the release policy from one structured source ([9ef8565](https://github.com/analitiq-ai/claude-code-plugins/commit/9ef8565e3fae584c798dff85ddf8a718cf93c97e))
* **connector-builder:** render the release policy from one structured source ([a198ad3](https://github.com/analitiq-ai/claude-code-plugins/commit/a198ad3e09181573f2b23bda5246bf94a721f4d5))
* **connector-builder:** repoint the drift-classifier description citation to the real owner of the release table ([983dfbc](https://github.com/analitiq-ai/claude-code-plugins/commit/983dfbc00d2389584ae8fd8a269fdf5d21d7f05a))
* **connector-builder:** say "bare integer" where "literal" now names a refused form ([666a2ae](https://github.com/analitiq-ai/claude-code-plugins/commit/666a2ae6908815b5dd5a31b94ed026aa2a9a2452))
* **contract-models:** close the holes the PR [#131](https://github.com/analitiq-ai/claude-code-plugins/issues/131) review found in declared-path resolution ([0508fe5](https://github.com/analitiq-ai/claude-code-plugins/commit/0508fe547de2c7ac1b1749b09a94d9323e9db4b0))
* **contract-models:** one batch-size bound, and a probe per slot the prose names ([f16fc18](https://github.com/analitiq-ai/claude-code-plugins/commit/f16fc189250944f3b6d24aea3d295cccee8d5ed2))
* correct six claims the review caught in the vocabulary work ([48c8d3b](https://github.com/analitiq-ai/claude-code-plugins/commit/48c8d3b99eb920212a47c6d3739a3ca080127f5c))
* decide templated Decimal bounds against the matcher, and derive the write probes ([aab9ac4](https://github.com/analitiq-ai/claude-code-plugins/commit/aab9ac4868a8611110b6c32f64b40838c945dc0c))
* decide templated Decimal bounds against the matcher, and derive the write probes ([ab8845c](https://github.com/analitiq-ai/claude-code-plugins/commit/ab8845c4dc04f371336596432b87086d67fbab29)), closes [#103](https://github.com/analitiq-ai/claude-code-plugins/issues/103)
* fence the README's clean-run claim like its SKILL.md twin ([d78d5b5](https://github.com/analitiq-ai/claude-code-plugins/commit/d78d5b55b402f73a1339568868577cbc1d16efe3))
* finish the pointer sweep, and say `in` where the statement says `location` ([fd1fb1a](https://github.com/analitiq-ai/claude-code-plugins/commit/fd1fb1a0b6bacb2909be2f27f643d9d84248248d))
* harden the round-two guards and retier the lookup-map rule ([3b94bdc](https://github.com/analitiq-ai/claude-code-plugins/commit/3b94bdccd1197772c79de09804117b5921caa8b2))
* judge every templated position against what it admits, and grade prose examples ([2fb8b34](https://github.com/analitiq-ai/claude-code-plugins/commit/2fb8b34c2190155cfe72f23f5ce1e3ce4ee452f9))
* label a rendered vocabulary with the key an author actually types ([a217991](https://github.com/analitiq-ai/claude-code-plugins/commit/a2179910fce16c826dfa3dcd5f474ba37e6c054e))
* **plugins:** anchor agent paths, preload spec skills, unship contributor guides ([b3a762e](https://github.com/analitiq-ai/claude-code-plugins/commit/b3a762ec5036dedb81f9bcec44b5cdee9b76fedc))
* **prose:** state the mechanism, not the cardinality ([bf6a0a3](https://github.com/analitiq-ai/claude-code-plugins/commit/bf6a0a39c5282c834cb0ec695d2bd03bddd051be))
* **prose:** state the mechanism, not the cardinality ([00c7595](https://github.com/analitiq-ai/claude-code-plugins/commit/00c7595143e5909a0c4cc57bb0a441973105dbd5))
* release contract-models and validator 1.0.0rc21, move the runtime pin ([74f5ea4](https://github.com/analitiq-ai/claude-code-plugins/commit/74f5ea439540d8c45a89b42095dcd4e1553d282b))
* render every vocabulary a rule points at, and say which field owns each ([2398c7f](https://github.com/analitiq-ai/claude-code-plugins/commit/2398c7ff606279367acaf568001a06743f97e7d8))
* restore the value-expressions conversion the mutation self-check reverted ([e70a8dc](https://github.com/analitiq-ai/claude-code-plugins/commit/e70a8dcdfa851f3587ac961cb740c9d5e838e686))
* scope-check the connector fields a runtime resolves, and only those ([bd0ee6b](https://github.com/analitiq-ai/claude-code-plugins/commit/bd0ee6bf336f3b61ff3da8d172e6b802f49b6df3))
* state the input vocabularies once, where the live model prints them ([10b051a](https://github.com/analitiq-ai/claude-code-plugins/commit/10b051a2bd79cf51e47ef0b67f99107663ab4c5b))
* state the metadata write-side check in the generated intro, pin the waiver location condition ([4d243c1](https://github.com/analitiq-ai/claude-code-plugins/commit/4d243c12cc636c1d31b661ea37e90923c70353f5))
* state what decides membership, not how many members there are ([22ab814](https://github.com/analitiq-ai/claude-code-plugins/commit/22ab814ae32d9499845f14f9fea6991a15493cb6))
* stop the required-reading pointer filtering rules out of the file ([e25a985](https://github.com/analitiq-ai/claude-code-plugins/commit/e25a9850be53f8054c95f6b3a849eb5ba85e69be))

## [0.2.0](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-connector-builder-v0.1.8...analitiq-connector-builder-v0.2.0) (2026-07-31)


### ⚠ BREAKING CHANGES

* **contract-models:** token-array get paths, single-segment targets, explicit value kind, truncate_insert, drop max_concurrent_batches, bound page size (rc19) ([#109](https://github.com/analitiq-ai/claude-code-plugins/issues/109))

### Features

* **contract-models:** token-array get paths, single-segment targets, explicit value kind, truncate_insert, drop max_concurrent_batches, bound page size (rc19) ([#109](https://github.com/analitiq-ai/claude-code-plugins/issues/109)) ([c0069a8](https://github.com/analitiq-ai/claude-code-plugins/commit/c0069a80a5b54e204b14f37ebdda5710f08c1e64))

## [0.1.8](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-connector-builder-v0.1.7...analitiq-connector-builder-v0.1.8) (2026-07-23)


### Bug Fixes

* **analitiq-connector-builder:** document bare Object/List as write-map canonicals the engine actually emits ([#82](https://github.com/analitiq-ai/claude-code-plugins/issues/82)) ([d576fef](https://github.com/analitiq-ai/claude-code-plugins/commit/d576fefbd5b21e18cdd4a3bff88ded05b872ef89))
* trim the canonical-type vocabulary to the engine-executable set, generated from the published grammar manifest ([#86](https://github.com/analitiq-ai/claude-code-plugins/issues/86)) ([b94cafa](https://github.com/analitiq-ai/claude-code-plugins/commit/b94cafad3ccc2fa924d29752eb919b6ffad2a685))

## [0.1.7](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-connector-builder-v0.1.6...analitiq-connector-builder-v0.1.7) (2026-07-23)


### Bug Fixes

* **analitiq-connector-builder:** ground TLS vocabularies in author-time research, not per-DB reference tables ([#78](https://github.com/analitiq-ai/claude-code-plugins/issues/78)) ([4a30104](https://github.com/analitiq-ai/claude-code-plugins/commit/4a301046a4398a51b2ec080fb1d89a223d15a263))
* **analitiq-connector-builder:** repoint fix-loop tracking link to claude-code-plugins[#3](https://github.com/analitiq-ai/claude-code-plugins/issues/3) ([6cf5d56](https://github.com/analitiq-ai/claude-code-plugins/commit/6cf5d56fad01c258988447d903bdc49b67f4b1cc))

## [0.1.6](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-connector-builder-v0.1.5...analitiq-connector-builder-v0.1.6) (2026-07-23)


### Features

* **analitiq-connector-builder:** accept sync SQLAlchemy drivers in the transport contract ([d78b8b5](https://github.com/analitiq-ai/claude-code-plugins/commit/d78b8b55b07b6a4800f61bcf2ffd2947fc405d8e))


### Bug Fixes

* bump the runtime validator pin to 1.0.0rc14 and guard it in CI ([e22bff4](https://github.com/analitiq-ai/claude-code-plugins/commit/e22bff44c9a224f87425fe0ea75deeef1d4a48b8))

## [0.1.5](https://github.com/analitiq-ai/claude-code-plugins/compare/analitiq-connector-builder-v0.1.4...analitiq-connector-builder-v0.1.5) (2026-07-22)


### Features

* **analitiq-connector-builder:** add first-request-only limit to LinkPagination ([#52](https://github.com/analitiq-ai/claude-code-plugins/issues/52)) ([#63](https://github.com/analitiq-ai/claude-code-plugins/issues/63)) ([33499e3](https://github.com/analitiq-ai/claude-code-plugins/commit/33499e33c7e6bc2b6161e96f4bfd8360bc229b8d))
* **analitiq-pipeline-builder:** author connection-scoped type maps for discovered private-endpoint natives ([#62](https://github.com/analitiq-ai/claude-code-plugins/issues/62)) ([2ca9379](https://github.com/analitiq-ai/claude-code-plugins/commit/2ca9379e00da422076aacd7507a8cb17434c10a3))
* restructure as a multi-plugin monorepo owning the contract surface ([#51](https://github.com/analitiq-ai/claude-code-plugins/issues/51)) ([8c75001](https://github.com/analitiq-ai/claude-code-plugins/commit/8c750017a414eee2f2f423fe27a3befd0fb9d128))


### Bug Fixes

* **analitiq-connector-builder:** consolidate slug-pattern copies and pin them to SLUG_PATTERN ([#59](https://github.com/analitiq-ai/claude-code-plugins/issues/59)) ([8a116a3](https://github.com/analitiq-ai/claude-code-plugins/commit/8a116a33a73f3e4acbfc6db987d5344b973249ef))
* bump the runtime validator pin to 1.0.0rc13 ([#68](https://github.com/analitiq-ai/claude-code-plugins/issues/68)) ([eb88e23](https://github.com/analitiq-ai/claude-code-plugins/commit/eb88e23f7224879caf8ee34abc82397ee27dad13))

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
