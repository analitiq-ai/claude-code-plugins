"""The authoring rules — the waived tier, kept in the same census.

:mod:`advisory_rules` holds the rules about a *document*: what the contract
models accept, whether by their own shape or by a relational check over it.
This module holds the rules that govern everything else an author produces —
the connector's Python package, the choice between transports that both
validate, the match between a document and a file beside it.

They are here rather than in plugin prose alone because they were only ever in
plugin prose, restated per file, drifting apart from each other. Registering
them gives each one an id, one wording, and a declared reason it is
unenforceable; the prose that taught them keeps the judgment and cites the id.

Every entry is ``tier="waiver"``: nothing in this repo rejects a violation.
``governs`` says which surface would have to be read to catch it, and the
waiver text says why that surface is out of reach. Registry CI and the CDK
conformance kit enforce most of the ``connector-package`` entries — out there,
after the plugin has already shipped the wrong thing — which is exactly the
distance the tier records.

Ids follow the registry's ``ADV-<AREA>-NNN`` scheme, stable and never reused.

This module imports no contract models (the :mod:`advisory` convention).
"""
from __future__ import annotations

from .advisory import AdvisoryRule, register

AUTHORING_RULES: list[AdvisoryRule] = [
    # --- api-endpoint document --------------------------------------------
    AdvisoryRule(
        id="ADV-ENDP-040", tier="waiver", resource="api-endpoint",
        prose=(
            "An endpoint declares only where the idempotency key goes; the "
            "key's value is engine-owned and appears in no value expression, "
            "input schema, or request slot."
        ),
        governs="engine-runtime",
        targets=("Idempotency", "WriteOperation"),
        waiver=(
            "An author-supplied key is an ordinary header or body field the "
            "contract cannot tell from any other — ADV-ENDP-016 catches only a "
            "name collision, not a hand-authored value under a different name."
        ),
    ),
    AdvisoryRule(
        id="ADV-ENDP-041", tier="waiver", resource="api-endpoint",
        prose=(
            "An endpoint request addresses a path relative to the transport its "
            "`transport_ref` names and never carries its own absolute base URL."
        ),
        governs="engine-runtime",
        targets=("_RequestBase",),
        waiver=(
            "`request.path` is an open string, and whether a produced URL stays "
            "on the transport's origin is settled only when the engine builds "
            "the request — the ORIGIN half `_RequestBase.transport_ref`'s "
            "description already declares unenforced."
        ),
    ),
    AdvisoryRule(
        id="ADV-ENDP-042", tier="waiver", resource="api-endpoint",
        prose=(
            "An endpoint declares how a watermark is sent and encodes no sync "
            "policy — no lookback, no overlap, no backfill depth baked into a "
            "cursor mapping."
        ),
        governs="engine-runtime",
        targets=("Replication",),
        waiver=(
            "Sync policy is decided per run by the engine, and a fudge factor "
            "baked into a mapping is indistinguishable in the document from a "
            "legitimate operator or format choice."
        ),
    ),
    AdvisoryRule(
        id="ADV-ENDP-043", tier="waiver", resource="api-endpoint",
        prose=(
            "A released `endpoint_id` is never renamed: a resource whose "
            "locator changes ships as a new endpoint document alongside the "
            "removal of the old one."
        ),
        governs="cross-artifact",
        waiver=(
            "a rename is only visible against the previously released version "
            "of the same connector and against the streams pinning the id, "
            "neither of which any single-release validation run has in hand."
        ),
    ),
    AdvisoryRule(
        id="ADV-ENDP-044", tier="waiver", resource="api-endpoint",
        prose=(
            "A keyset pagination block omits `initial` when there is no "
            "first-page value; an explicit null is not that absence."
        ),
        governs="cross-artifact",
        targets=("Keyset",),
        waiver=(
            "Known enforcement gap: `Keyset.initial` admits null as an ordinary "
            "value, so the document cannot distinguish an omitted first-page "
            "key from one authored as null — the refusal ADV-ENDP-031 makes for "
            "namespace fields has no counterpart here."
        ),
    ),
    AdvisoryRule(
        id="ADV-ENDP-045", tier="waiver", resource="api-endpoint",
        prose=(
            "An operation's `request.path` is a path resolved against the "
            "selected transport's origin, never an absolute URL."
        ),
        governs="cross-artifact",
        targets=("_RequestBase",),
        waiver=(
            "Known enforcement gap: `_RequestBase.path` constrains only "
            "placeholder syntax and emptiness, and the validator's "
            "`endpoint-id-locator` check compares the derived handle rather "
            "than the form of the path, so an absolute URL whose `endpoint_id` "
            "was derived consistently passes."
        ),
    ),
    # --- connection document ----------------------------------------------
    AdvisoryRule(
        id="ADV-CONN-005", tier="waiver", resource="connection",
        prose=(
            "A connection document leaves its `discovered` map to the service "
            "that produces it and never authors a value into it."
        ),
        governs="engine-runtime",
        targets=("ConnectionStoredMaps", "ConnectionInput"),
        waiver=(
            "`discovered` is a declared field of the connection model, so a "
            "client-supplied value validates here and is refused only by the "
            "connections API on ingest."
        ),
    ),
    AdvisoryRule(
        id="ADV-CONN-006", tier="waiver", resource="connection",
        prose=(
            "Every `connection_contract` input and post-auth output is authored "
            "into the connection map named by the last segment of the `storage` "
            "its declaration carries."
        ),
        governs="cross-artifact",
        targets=("ConnectionAuthored", "ConnectionInput"),
        waiver=(
            "the routing target is declared in the connector document; a "
            "connection carries no view of the contract it was authored "
            "against, so a value filed into the wrong map validates cleanly."
        ),
    ),
    AdvisoryRule(
        id="ADV-CONN-007", tier="waiver", resource="connection",
        prose=(
            "A connection authors each value in the JSON type its "
            "`connection_contract` input declares, uncoerced, and where that "
            "input declares an enum the value is one of that enum's members."
        ),
        governs="cross-artifact",
        targets=("ConnectionAuthored", "ConnectionInput"),
        waiver=(
            "`parameters` is an untyped map and the declaring input lives in "
            "the connector document, so per-key type and vocabulary are checked "
            "server-side on save — as `_validate_non_secret_maps` already says "
            "of itself."
        ),
    ),
    AdvisoryRule(
        id="ADV-CONN-008", tier="waiver", resource="connection",
        prose=(
            "A database connection that selects a certificate-verifying TLS "
            "mode also supplies the connector's CA-material input."
        ),
        governs="cross-artifact",
        targets=("ConnectionAuthored", "ConnectionInput"),
        waiver=(
            "both the mode vocabulary and the CA input are connector-declared "
            "and the pairing spans two keys of an untyped `parameters` map; a "
            "driver silently falling back to the host trust store makes the "
            "omission look healthy."
        ),
    ),
    AdvisoryRule(
        id="ADV-CONN-009", tier="waiver", resource="connection",
        prose=(
            "A secret value never appears in a connection document: an input "
            "the connector marks as secret storage is authored as a pointer in "
            "`secret_refs`."
        ),
        governs="cross-artifact",
        targets=("ConnectionAuthored", "ConnectionInput"),
        waiver=(
            "`SECRET_REF_VALUE_PATTERN` rejects a bare token in `secret_refs` "
            "and `ADV-CONN-004` rejects secret-shaped keys in the non-secret "
            "maps, but which keys are secret is connector-declared and nothing "
            "inspects a value for being a real secret filed under an innocuous "
            "key."
        ),
    ),
    AdvisoryRule(
        id="ADV-CONN-010", tier="waiver", resource="connection",
        prose=(
            "A `sidecar:` pointer is authored only against a credentials file "
            "keyed by connection-contract input name, never against the "
            "env-var-keyed template the plugin emits."
        ),
        governs="cross-artifact",
        targets=("ConnectionAuthored", "ConnectionInput"),
        waiver=(
            "the pointer and the file it resolves against are separate "
            "artifacts and the `sidecar:` scheme constrains nothing after its "
            "prefix, so a wrong key validates cleanly and fails only when the "
            "engine resolves it."
        ),
    ),
    # --- connector document -----------------------------------------------
    AdvisoryRule(
        id="ADV-CTOR-026", tier="waiver", resource="connector",
        prose=(
            "Every provider fact a connector declares is grounded in the target "
            "system's own documentation, a wire-compatible system's "
            "documentation is never evidence for it, and a fact the docs do not "
            "establish is reported as a gap rather than inferred."
        ),
        governs="authoring-choice",
        waiver=(
            "Provenance is research conduct: no authored document records which "
            "system a fact came from, so a fact borrowed from a wire-compatible "
            "neighbour produces a connector that validates cleanly and fails "
            "against the real system."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-027", tier="waiver", resource="connector",
        prose=(
            "A database connector's transport and driver are selected by "
            "applying the decision order in `spec-driver-selection.md` and "
            "stopping at the first tier the target system satisfies."
        ),
        governs="authoring-choice",
        waiver=(
            "Every tier of the order yields a `transports` block the contract "
            "accepts, so nothing can distinguish a connector that skipped a "
            "tier from one whose target system genuinely fell through it."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-028", tier="waiver", resource="connector",
        prose=(
            "A connector document declares the shape of an input and never its "
            "value; the connection that instantiates the connector supplies the "
            "value."
        ),
        governs="authoring-choice",
        waiver=(
            "A real customer host and a placeholder are the same string to "
            "every model, so only a reader can tell a reusable connector from "
            "one baked to a single tenant."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-029", tier="waiver", resource="connector",
        prose=(
            "A connector whose `ssl_mode` input enum admits a mode that "
            "verifies the server certificate also declares a CA-certificate "
            "input in its connection contract."
        ),
        governs="authoring-choice",
        waiver=(
            "Which mode names imply certificate verification is the connector's "
            "own researched vocabulary, and the contract's TLS block is "
            "deliberately vocabulary-agnostic, so nothing here can classify a "
            "mode string."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-030", tier="waiver", resource="connector",
        prose=(
            "A connector's `resource_discovery` strategy exposes every level of "
            "the target system's real object hierarchy, since a strategy that "
            "flattens a level hides every object outside that level's default."
        ),
        governs="authoring-choice",
        waiver=(
            "`resource_discovery.strategy` is a registered strategy id and "
            "`options` is an open map, so the document encodes no hierarchy "
            "depth for anything to compare against the target system's."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-031", tier="waiver", resource="connector",
        prose=(
            "A connector's `sql_capabilities.catalog` level states whether one "
            "connection can address across catalogs rather than how deep the "
            "system's object hierarchy is, and a system whose statements can "
            "name a catalog exposes that level in `resource_discovery` too."
        ),
        governs="authoring-choice",
        waiver=(
            "Whether one connection can reach across a target system's catalogs "
            "is an external fact no document declares, so no checker can tell a "
            "wrong `catalog` level from a right one — and the discovery half it "
            "obliges is only a strategy id, which encodes no depth to compare "
            "against."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-032", tier="waiver", resource="connector",
        prose="A connector's first release declares `version` as `1.0.0`.",
        governs="authoring-choice",
        waiver=(
            "`version` accepts any semantic version, so a first release cut at "
            "another number validates identically; the starting point is a "
            "release convention no document records."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-033", tier="waiver", resource="connector",
        prose=(
            "A document-store or NoSQL provider is authored as a `database` "
            "connector, and this plugin authors nothing under the contract's "
            "separate document-store kinds even though the contract models "
            "them."
        ),
        governs="authoring-choice",
        waiver=(
            "The contract ships fully-shaped models for the document-store "
            "kinds, so a connector authored under one validates cleanly; only "
            "this plugin's scope makes it the wrong choice, which means an "
            "author reading the schema alone reaches the opposite conclusion."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-034", tier="waiver", resource="connector",
        prose=(
            "A DSN binding's `value` resolves to the unencoded value and the "
            "declared `encoding` is applied once, by the runtime, so an author "
            "never pre-encodes it."
        ),
        governs="engine-runtime",
        targets=("DsnBinding",),
        waiver=(
            "A pre-encoded value is a well-formed value expression; the double "
            "encoding appears only in the DSN the runtime renders at connection "
            "time."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-035", tier="waiver", resource="connector",
        prose=(
            "A DSN binding's `value` is a value expression the runtime resolves "
            "at connection time, in one of the forms the value-expression "
            "grammar defines."
        ),
        governs="engine-runtime",
        targets=("DsnBinding",),
        waiver=(
            "`DsnBinding.value` is typed open so any expression form can pass "
            "through, so a shape outside the grammar validates here and fails "
            "when the DSN is rendered."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-036", tier="waiver", resource="connector",
        prose=(
            "A `resource_discovery.strategy` names a strategy the engine has "
            "registered, unless the connector ships its own through a "
            "`connector_plugin` implementation."
        ),
        governs="engine-runtime",
        targets=("ResourceDiscovery",),
        waiver=(
            "The strategy registry is the engine's and `strategy` is an open "
            "string here, so an unregistered id validates and fails when "
            "discovery runs."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-037", tier="waiver", resource="connector",
        prose=(
            "A connector, or a stream binding, for a connector kind the engine "
            "does not execute is declined rather than authored, even though the "
            "contract accepts the kind."
        ),
        governs="engine-runtime",
        waiver=(
            "Which kinds the engine executes is engine state no artifact here "
            "tracks; the contract models accept every declared kind, so this is "
            "a plugin policy an engine release retires with nobody editing the "
            "prose."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-038", tier="waiver", resource="connector",
        prose=(
            "Each `post_auth_outputs` entry resolves on its own; an authored "
            "output never depends on another entry having run first."
        ),
        governs="engine-runtime",
        targets=("PostAuthOutput", "ConnectorBase"),
        waiver=(
            "The block is a map and the engine decides dispatch order at "
            "connect time, so a chain of outputs referencing one another is "
            "indistinguishable in the document from independent ones."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-039", tier="waiver", resource="connector",
        prose=(
            "A declared SQLAlchemy `driver` names a registration that actually "
            "exists for the system's dialect."
        ),
        governs="engine-runtime",
        targets=("SqlAlchemyTransport",),
        waiver=(
            "Only the `dialect+driver` form is structural here; whether the "
            "pair is registered is resolved when the engine builds the "
            "transport."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-040", tier="waiver", resource="connector",
        prose=(
            "A database connector declares its `sql_capabilities` block, which "
            "the contract leaves optional but the engine requires before it "
            "will run any write mode."
        ),
        governs="engine-runtime",
        targets=("DatabaseConnector",),
        waiver=(
            "The field is deliberately optional on `DatabaseConnector` so a "
            "partial document still validates; the refusal happens at "
            "handshake, against a live system this repo never sees."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-041", tier="waiver", resource="connector",
        prose=(
            "A capability fact the provider's documentation does not establish "
            "is left undeclared and reported as a gap, never inferred from a "
            "similar or wire-compatible system."
        ),
        governs="engine-runtime",
        targets=("SqlCapabilities",),
        waiver=(
            "An inferred value is a well-formed declaration; only the live "
            "system contradicts it, and the engine refuses rather than guesses "
            "at handshake."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-042", tier="waiver", resource="connector",
        prose=(
            "The directory a connector release ships as is named for the "
            "`connector_id` its `connector.json` declares."
        ),
        governs="cross-artifact",
        waiver=(
            "the directory a connector release ships as is not visible to a "
            "single-document validation run — no contract model sees the path, "
            "and the validator resolves siblings relative to whatever path it "
            "was handed rather than comparing that path's name to "
            "`connector_id`; only registry CI can compare the two."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-043", tier="waiver", resource="connector",
        prose=(
            "An `api` connector's release ships definition documents only and "
            "carries no connector-package Python files."
        ),
        governs="cross-artifact",
        waiver=(
            "the absence of a file is a property of the release directory, not "
            "of any document — the validator checks which type maps a connector "
            "ships but never looks for package files, so an `api` connector "
            "carrying a dialect passes every check here."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-044", tier="waiver", resource="connector",
        prose=(
            "A `database` connector's release ships no endpoint documents, "
            "because a database endpoint is produced from resource-discovery "
            "output rather than authored."
        ),
        governs="cross-artifact",
        waiver=(
            "nothing inspects a database connector's release for files it "
            "should not contain — the coverage walk only enumerates "
            "`endpoints/` for `api` connectors, so a hand-authored database "
            "endpoint shipped beside `connector.json` is never looked at."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-045", tier="waiver", resource="connector",
        prose=(
            "A connector's slug names the same entity in its document, its "
            "registry repository and its on-disk directory, and never changes: "
            "rewriting a `connector_id` or a derived `endpoint_id` mints a "
            "different entity instead of editing this one."
        ),
        governs="cross-artifact",
        waiver=(
            "the identity spans a document field, a remote repository name and "
            "a filesystem path, and immutability is a property across "
            "successive versions of a document rather than of any one instance "
            "a validator sees."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-046", tier="waiver", resource="connector",
        prose=(
            "A connector document reaches credential material only through the "
            "`secrets` scope: a DSN binding's `value` refs a secret rather than "
            "a connection parameter, and a discovery `options` block carries no "
            "secret-shaped key."
        ),
        governs="cross-artifact",
        targets=("DsnBinding", "ResourceDiscovery"),
        waiver=(
            "Known enforcement gap: "
            "`ConnectionStoredMaps._validate_no_secret_keys` (ADV-CONN-004) "
            "runs the secret-shaped-key detector on the connection document "
            "only, and nothing inspects `DsnBinding.value` or the free-form "
            "`ResourceDiscovery.options` in the connector document."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-047", tier="waiver", resource="connector",
        prose=(
            "A transport's `tls.mode` resolves to a value declared by the same "
            "connector's ssl-mode connection-contract input, and that input "
            "declares the `enum` the mode is drawn from."
        ),
        governs="cross-artifact",
        targets=("DatabaseTls", "ConnectionContractInput"),
        waiver=(
            "Known enforcement gap: `DatabaseTls` is deliberately "
            "vocabulary-agnostic and no validator follows the `mode` expression "
            "from the transport into `connection_contract.inputs`, so neither "
            "side of the pair is checked against the other."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-048", tier="waiver", resource="connector",
        prose=(
            "Every transport family keyed in `sql_capabilities.bulk_load` names "
            "a transport the same connector declares in `transports`."
        ),
        governs="cross-artifact",
        targets=("SqlBulkLoad", "DatabaseConnector"),
        waiver=(
            "Known enforcement gap: `SqlBulkLoad` validates each family key "
            "against the bulk-load vocabulary in isolation, and nothing at "
            "connector level relates those keys to the declared transports."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-049", tier="waiver", resource="connector",
        prose=(
            "A database connector that ships a write path declares "
            "`sql_capabilities`."
        ),
        governs="cross-artifact",
        targets=("DatabaseConnector",),
        waiver=(
            "Known enforcement gap: write capability comes from the protocol "
            "the class in `connector.py` satisfies, which no contract model "
            "sees, so nothing can tell a write-capable connector from a "
            "discovery-only one and the field stays optional."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-050", tier="waiver", resource="connector",
        prose=(
            "A transport is invoked no earlier than the phase at which every "
            "scope its expressions reference becomes available, and an input a "
            "transport references declares a `phase` no later than that "
            "transport's first use."
        ),
        governs="cross-artifact",
        targets=("ConnectorBase",),
        waiver=(
            "Known enforcement gap: phase resolvability is a property of the "
            "whole connector document read as an ordered workflow — no model "
            "sees a transport, the operations that invoke it and the inputs it "
            "consumes together — and connector-document refs are not resolved "
            "at all."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-051", tier="waiver", resource="connector",
        prose=(
            "A `runtime.oauth.*` reference appears only in the auth operation "
            "for which that value exists, and only on a connector whose auth "
            "type produces it."
        ),
        governs="cross-artifact",
        targets=("ConnectorBase",),
        waiver=(
            "Known enforcement gap: which runtime OAuth value exists in which "
            "auth operation is engine-owned and stated in no model, and no "
            "validator resolves a connector-document ref, so the reference and "
            "its producing operation are never compared."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-052", tier="waiver", resource="connector",
        prose=(
            "Every connector-internal ref — a secret, a connection parameter, a "
            "discovered value — names something the connection contract "
            "declares as an input or a post-auth output."
        ),
        governs="cross-artifact",
        targets=("ConnectorBase",),
        waiver=(
            "Known enforcement gap: the registered internal-ref rules resolve "
            "only the connection contract's own back-references (ADV-CTOR-006 "
            "through ADV-CTOR-009); refs authored in transports, auth templates "
            "and discovery requests are resolved nowhere."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-053", tier="waiver", resource="connector",
        prose=(
            "A `lookup` function's inline `map` declares a key for every value "
            "of the referenced input's `enum` and no key outside it."
        ),
        governs="cross-artifact",
        targets=("FunctionExpression", "ConnectionContractInput"),
        waiver=(
            "Known enforcement gap: the function expression carries no link "
            "back to the input it maps, so the map keys and the input's `enum` "
            "are never compared and an uncovered member simply resolves to "
            "nothing."
        ),
    ),
    AdvisoryRule(
        id="ADV-CTOR-054", tier="waiver", resource="connector",
        prose=(
            "A DSN binding's `value` applies no wire-encoding function; the "
            "binding's declared `encoding` owns that encoding."
        ),
        governs="cross-artifact",
        targets=("DsnBinding",),
        waiver=(
            "Known enforcement gap: `DsnBinding.value` is an untyped expression "
            "that nothing walks for function calls, so the double encoding is "
            "invisible until the DSN is built — unlike the equivalent "
            "path_params rule (ADV-ENDP-027), which has an enforcer."
        ),
    ),
    # --- connector package files ------------------------------------------
    AdvisoryRule(
        id="ADV-PKG-001", tier="waiver", resource="connector-package",
        prose=(
            "A connector's dialect overrides a structural default only where "
            "the CDK's portable form is genuinely invalid on the target system, "
            "and overrides type rendering only for logic the write map cannot "
            "express."
        ),
        governs="authoring-choice",
        waiver=(
            "An unnecessary override is a legal Python method on the dialect "
            "that usually works; only the target system's own SQL decides "
            "whether the CDK's base form was already valid, and no contract "
            "model sees the package at all."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-002", tier="waiver", resource="connector-package",
        prose=(
            "A database connector's repository root is its importable Python "
            "package: the definition documents sit under `definition/`, and "
            "`__init__.py`, `connector.py`, `requirements.txt` and "
            "`pyproject.toml` sit at the root beside it."
        ),
        governs="connector-package",
        waiver=(
            "The layout is a directory tree on disk, not a document any "
            "contract model parses; registry CI's `pip wheel --no-deps .` build "
            "is the first thing that sees whether the root is actually the "
            "package."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-003", tier="waiver", resource="connector-package",
        prose=(
            "A connector's dialect implements every hook the transports and "
            "`sql_capabilities` it declares require, in the form the CDK's hook "
            "surface defines."
        ),
        governs="engine-runtime",
        waiver=(
            "No contract model sees `connector.py`, and the hook names and "
            "signatures are engine-owned and unpinnable here; the CDK "
            "conformance kit run by registry CI is what catches a missing or "
            "mis-signed hook, after the plugin has already shipped it."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-005", tier="waiver", resource="connector-package",
        prose=(
            "A write-path renderer on a connector's dialect returns statement "
            "text and performs no I/O; only `bulk_land` receives a live "
            "connection and the records."
        ),
        governs="engine-runtime",
        waiver=(
            "The method bodies live in `connector.py`, which no contract model "
            "reads, so a renderer that opens a connection is caught only by the "
            "CDK conformance kit or at run time."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-006", tier="waiver", resource="connector-package",
        prose=(
            "A database connector's `pyproject.toml` declares its dependencies "
            "dynamically from `requirements.txt` rather than restating them, so "
            "the driver is pinned in exactly one file."
        ),
        governs="connector-package",
        waiver=(
            "`pyproject.toml` is a package file no contract model parses, and a "
            "second, hand-restated dependency list still builds a valid wheel — "
            "so registry CI passes it too and only a stale driver pin ever "
            "reveals the divergence."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-007", tier="waiver", resource="connector-package",
        prose=(
            "Every name in a database connector's `pyproject.toml` derives from "
            "its `connector_id`: the distribution "
            "`analitiq-connector-{connector_id}`, the importable package "
            "`analitiq_connector_{connector_id}` mapped to the repository root, "
            "and the entry-point name `{connector_id}` the engine resolves."
        ),
        governs="connector-package",
        waiver=(
            "The derivation lives in TOML no contract model reads; registry "
            "CI's wheel build catches a broken package mapping, but a "
            "distribution or entry-point name that merely disagrees with "
            "`connector_id` builds cleanly and fails only when the engine tries "
            "to resolve the connector."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-008", tier="waiver", resource="connector-package",
        prose=(
            "A database connector registers its connector class under "
            "`connector_id` in the `analitiq.source_connectors` entry-point "
            "group and in the `analitiq.destination_connectors` group, so it "
            "lands read and write as one working unit."
        ),
        governs="connector-package",
        waiver=(
            "Registry CI checks the built wheel's entry points, which is after "
            "the plugin has already authored and returned a one-directional "
            "connector; nothing in this repo sees `pyproject.toml`."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-009", tier="waiver", resource="connector-package",
        prose=(
            "A database connector's `__init__.py` re-exports the package's "
            "connector class and its dialect class."
        ),
        governs="connector-package",
        waiver=(
            "`__init__.py` is Python no contract model imports; registry CI's "
            "wheel build succeeds with an empty one, and only an importer "
            "reaching for the dialect class discovers it is missing."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-010", tier="waiver", resource="connector-package",
        prose=(
            "`connector.py` defines the connector's dialect class on the CDK "
            "`SqlDialect` base and its connector class on "
            "`GenericSQLConnector`, and that connector class carries "
            "`dialect_class` and no other member, not even an `__init__`."
        ),
        governs="connector-package",
        waiver=(
            "`connector.py` is a file no contract model opens; the CDK "
            "conformance kit's surface audit rejects an extra member on the "
            "connector class, but only once the package reaches registry CI."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-011", tier="waiver", resource="connector-package",
        prose=(
            "A connector's `connector.py` imports only the CDK, the standard "
            "library, its own driver, and — inside an async `bulk_land` — "
            "`sqlalchemy.util.await_only`; it never imports another connector, "
            "any other third-party package, or an engine or runtime."
        ),
        governs="connector-package",
        waiver=(
            "Imports live in Python no contract model parses; the CDK "
            "conformance kit is what would catch a reach into another connector "
            "or an engine, and only after the package has been written and "
            "pushed to registry CI."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-012", tier="waiver", resource="connector-package",
        prose=(
            "A connector's dialect overrides only the public hooks `SqlDialect` "
            "itself declares — never a private CDK member, and never the "
            "framework-owned `table_address` factory or `capabilities` "
            "attribute — and adds no public attribute of its own, giving each "
            "helper it needs a leading underscore and a name the base does not "
            "use."
        ),
        governs="connector-package",
        waiver=(
            "The override surface is a Python class this repo's validator never "
            "opens; the CDK conformance kit's surface check is the enforcer, "
            "and it runs at registry CI after the connector is authored."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-013", tier="waiver", resource="connector-package",
        prose=(
            "An override accepts every call shape the base admits — the same "
            "parameter names, the same keyword-only markers, and the same "
            "synchronous form, since every dialect hook is called "
            "synchronously."
        ),
        governs="connector-package",
        waiver=(
            "Nothing in this repo reads a dialect's method signatures; the "
            "conformance kit compares shapes against the base at registry CI, "
            "which is the first moment a renamed parameter or an `async def` "
            "override is refused."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-015", tier="waiver", resource="connector-package",
        prose=(
            "A connector's `empty_table_sql` never renders `TRUNCATE`, whose "
            "implicit commit breaks the staged write cycle."
        ),
        governs="connector-package",
        waiver=(
            "The statement text is rendered by `connector.py`, which no "
            "contract model sees, and no conformance test asserts which "
            "statement a dialect chooses to empty a table — the implicit commit "
            "shows up as a broken write cycle at run time."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-016", tier="waiver", resource="connector-package",
        prose=(
            "A database connector's dialect implements exactly the hooks its "
            "`sql_capabilities` declaration obliges: a declared fact with no "
            "hook behind it, and a hook nothing routes to, are both violations."
        ),
        governs="connector-package",
        waiver=(
            "The declaration is JSON this repo validates and the hooks are "
            "Python it never sees, so only the CDK conformance kit at registry "
            "CI can compare them — a mismatched pair validates clean here and "
            "is refused at engine handshake."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-017", tier="waiver", resource="connector-package",
        prose=(
            "Every write-capable database connector implements "
            "`stage_table_sql`, whatever the rest of its `sql_capabilities` "
            "declare."
        ),
        governs="connector-package",
        waiver=(
            "Same surface as the rest of the pairing: the conformance kit at "
            "registry CI is the only thing that can notice a missing "
            "`stage_table_sql`, and until then the connector's JSON validates "
            "clean."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-018", tier="waiver", resource="connector-package",
        prose=(
            "`merge_statement_sql` renders a valid statement when every landed "
            "column is a conflict key, degrading to its form's insert-only "
            "variant rather than an empty update clause."
        ),
        governs="connector-package",
        waiver=(
            "The rendered statement is a string `connector.py` returns; nothing "
            "here executes or reads it, and the conformance kit's explicit test "
            "of this case is the only enforcement, at registry CI."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-019", tier="waiver", resource="connector-package",
        prose=(
            "A `bulk_land` implementation targets the stage's full address "
            "rather than its bare table name, so a real-scope stage lands in "
            "the schema the engine qualified it with."
        ),
        governs="connector-package",
        waiver=(
            "Caught by nobody: the engine verifies the landed row count and not "
            "where the rows landed, no contract model sees `bulk_land`, and a "
            "mechanism that resolves against the session default works on a "
            "temp-scope stage and silently mislands on a real-scope one."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-020", tier="waiver", resource="connector-package",
        prose=(
            "A connector never invents a stage name; stage names are "
            "engine-generated and deterministic, and inventing one breaks the "
            "idempotency a retried batch depends on."
        ),
        governs="connector-package",
        waiver=(
            "Stage naming happens inside `connector.py` at run time; nothing in "
            "this repo and no conformance test observes the name a dialect "
            "uses, so a broken retry only shows up as a duplicate or a leftover "
            "relation in production."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-021", tier="waiver", resource="connector-package",
        prose=(
            "A connector declaring a transport `tls` block ships a package "
            "whose dialect implements the TLS connect-argument hook and, with "
            "it, the post-connect TLS-state verification hook; a connector with "
            "no package declares no `tls` at all."
        ),
        governs="connector-package",
        waiver=(
            "The `tls` block is JSON this repo validates while the hooks are "
            "dialect methods it never sees; the CDK base raises for every mode, "
            "so a declaration with no hook behind it fails loudly at connect in "
            "the engine rather than anywhere in this repo."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-022", tier="waiver", resource="connector-package",
        prose=(
            "A dialect's TLS hook raises rather than connecting when a "
            "certificate-verification mode resolves with an empty CA "
            "certificate."
        ),
        governs="connector-package",
        waiver=(
            "The fail-closed behaviour lives in dialect code no contract model "
            "sees, and no conformance test supplies an empty CA — a connector "
            "that silently downgrades to an unverified session is caught by "
            "nobody."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-023", tier="waiver", resource="connector-package",
        prose=(
            "A connector's write direction lives in `type-map-write.json`: it "
            "ships no Python type-rendering table, and a `render_column_type` "
            "override exists only for logic the map's rules cannot express, "
            "delegating every other type back to the map."
        ),
        governs="connector-package",
        waiver=(
            "The map is a document this repo validates while the override is "
            "Python it never sees, so nothing here can tell a delegating "
            "override from a rendering table transcribed into code; the "
            "conformance kit audits the override surface, not what a hook "
            "computes."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-025", tier="waiver", resource="connector-package",
        prose=(
            "A connector package ships a README describing the system it "
            "connects to, its authentication, and any setup a user must "
            "perform."
        ),
        governs="connector-package",
        waiver=(
            "The in-plugin validator ignores README entirely and the registry "
            "wheel build does not require one, so a connector that ships "
            "without documentation is caught by nobody."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-027", tier="waiver", resource="connector-package",
        prose=(
            "A connector package's `requirements.txt` ships the driver "
            "distribution each transport its `connector.json` declares needs — "
            "the DBAPI named by a SQLAlchemy transport's `dialect+driver`, and "
            "the `adbc-driver-{driver}` wheel plus `adbc-driver-manager` for an "
            "ADBC transport's `driver` — and nothing else: no engine pin, and "
            "never the CDK, which the engine environment provides. The engine "
            "image ships no database drivers, so this file is the only place a "
            "connector's driver is pinned."
        ),
        governs="cross-artifact",
        waiver=(
            "`requirements.txt` is a package file no contract model sees, so "
            "nothing here can check that the distribution it pins is the one "
            "the transport declared in the sibling `connector.json` actually "
            "imports at run time."
        ),
    ),
    AdvisoryRule(
        id="ADV-PKG-029", tier="waiver", resource="connector-package",
        prose=(
            "The mode vocabulary a connector's `ssl_mode` input declares and "
            "the vocabulary its dialect's TLS hook interprets are the same set: "
            "every declared mode is one the dialect handles, and the dialect "
            "handles no mode the input does not declare."
        ),
        governs="cross-artifact",
        waiver=(
            "the mode vocabulary is authored into `connector.json`'s input enum "
            "while its interpretation lives in the package's dialect hook, and "
            "no validator opens the dialect to see which modes it actually "
            "handles."
        ),
    ),
    # --- database-endpoint document ---------------------------------------
    AdvisoryRule(
        id="ADV-DBEP-004", tier="waiver", resource="database-endpoint",
        prose=(
            "A column's frozen `arrow_type` and `native_type` are the values "
            "the type maps render for it, not a re-derivation; judgment "
            "supplies a value only for a native or canonical no map covers."
        ),
        governs="engine-runtime",
        targets=("Column", "DatabaseEndpointDoc"),
        waiver=(
            "The maps are separate documents the engine resolves with at "
            "discovery and at DDL render, so a frozen value that disagrees with "
            "them validates here and diverges silently at run time."
        ),
    ),
    AdvisoryRule(
        id="ADV-DBEP-005", tier="waiver", resource="database-endpoint",
        prose=(
            "A discovered object records every namespace level its system "
            "actually has and invents none it lacks, so `catalog` comes back "
            "populated where the system has one and absent where it does not."
        ),
        governs="engine-runtime",
        targets=("DatabaseObject",),
        waiver=(
            "The objects are produced by the engine's discovery strategy at "
            "connection time and the namespace fields are optional, so a "
            "flattened or padded result validates as readily as a faithful one."
        ),
    ),
    AdvisoryRule(
        id="ADV-DBEP-006", tier="waiver", resource="database-endpoint",
        prose=(
            "A database endpoint document is produced by the connector's "
            "`resource_discovery` at connection time and is never authored into "
            "a connector release."
        ),
        governs="engine-runtime",
        targets=("DatabaseEndpointDoc",),
        waiver=(
            "`DatabaseEndpointDoc` is a fully authorable model — only where the "
            "document came from makes a hand-written one wrong, and nothing in "
            "the document records its origin."
        ),
    ),
    AdvisoryRule(
        id="ADV-DBEP-007", tier="waiver", resource="database-endpoint",
        prose=(
            "A derived `endpoint_id` is an opaque lookup handle: database "
            "identity is read from `database_object` and never parsed back out "
            "of the handle."
        ),
        governs="engine-runtime",
        targets=(
            "DatabaseObject", "ConnectionEndpointRef",
            "DatabaseEndpointDoc"
        ),
        waiver=(
            "Decoding happens in whatever consumer reads the id rather than in "
            "any document; slugging is lossy and the trailing hash is not "
            "reversible, so a wrong decode surfaces only as a wrong target at "
            "run time."
        ),
    ),
    AdvisoryRule(
        id="ADV-DBEP-008", tier="waiver", resource="database-endpoint",
        prose=(
            "An authored endpoint declares no column the engine synthesises at "
            "table creation, and drops such columns from a mirrored source's "
            "column list."
        ),
        governs="engine-runtime",
        targets=("Column", "DatabaseEndpointDoc"),
        waiver=(
            "The synthetic names are the engine's and `Column.name` accepts any "
            "non-empty string, so the duplicate surfaces only when the engine "
            "creates the table on the first run."
        ),
    ),
    AdvisoryRule(
        id="ADV-DBEP-009", tier="waiver", resource="database-endpoint",
        prose=(
            "A database endpoint records every provider identifier exactly as "
            "the source reports it, with no case-folding, quoting or other "
            "normalization."
        ),
        governs="cross-artifact",
        targets=("DatabaseObject", "DatabaseEndpointDoc"),
        waiver=(
            "correctness is judged against the live database, which no document "
            "records; the server-managed `schema_hash` and the derived "
            "`endpoint_id` both assume verbatim identifiers and neither can "
            "detect a normalized one."
        ),
    ),
    AdvisoryRule(
        id="ADV-DBEP-010", tier="waiver", resource="database-endpoint",
        prose=(
            "A new-table database endpoint targets a namespace that discovery "
            "returned."
        ),
        governs="cross-artifact",
        targets=("DatabaseObject", "DatabaseEndpointDoc"),
        waiver=(
            "the legal target set comes from a discovery result rather than "
            "from any document, so nothing an authored endpoint carries records "
            "which namespaces exist."
        ),
    ),
    # --- pipeline document ------------------------------------------------
    AdvisoryRule(
        id="ADV-PIPE-007", tier="waiver", resource="pipeline",
        prose=(
            "A stream's batching override may lower the resolved batch size but "
            "never raise it past the destination endpoint's declared provider "
            "capacity."
        ),
        governs="engine-runtime",
        # `Batching` is deliberately not a target: the name is carried by both
        # `endpoints` and `pipelines.config`, and targets resolve by bare class
        # name. The stream-side override is the half an author writes.
        targets=("Execution",),
        waiver=(
            "The documents carrying the default, the override and the ceiling "
            "are composed at run time, and the ceiling is applied by capping "
            "rather than rejection, so an over-large override validates "
            "everywhere and is silently reduced."
        ),
    ),
    AdvisoryRule(
        id="ADV-PIPE-008", tier="waiver", resource="pipeline",
        prose=(
            "Every connection a pipeline references belongs to the same "
            "organization as the pipeline."
        ),
        governs="engine-runtime",
        targets=("PipelineConnections",),
        waiver=(
            "Ownership is registry state and no authored document carries an "
            "org id, so a mismatch is refused only at save time."
        ),
    ),
    AdvisoryRule(
        id="ADV-PIPE-009", tier="waiver", resource="pipeline",
        prose=(
            "A `cron_expression`'s inner spec is authored valid for the "
            "scheduler that runs it; the contract checks the wrapper only."
        ),
        governs="engine-runtime",
        targets=("Schedule",),
        waiver=(
            "Inner-spec validity is the scheduler's verdict at run time, so an "
            "expression this repo accepts can still be rejected downstream."
        ),
    ),
    AdvisoryRule(
        id="ADV-PIPE-010", tier="waiver", resource="pipeline",
        prose=(
            "A pipeline's `streams` array carries no execution order, so "
            "ordering never encodes a dependency between streams and is never "
            "presented to the user as one."
        ),
        governs="engine-runtime",
        targets=("PipelineAuthored",),
        waiver=(
            "Order is the runtime's to ignore and every permutation validates "
            "identically, so nothing can tell an ordering-as-dependency from an "
            "arbitrary one."
        ),
    ),
    # --- shared across document families ----------------------------------
    AdvisoryRule(
        id="ADV-SHRD-001", tier="waiver", resource="shared",
        prose=(
            "A credential appears in an authored document only as a reference "
            "expression into the secret scope, never as a literal value."
        ),
        governs="authoring-choice",
        waiver=(
            "A leaked secret and a benign literal default are the same string, "
            "so no model can tell them apart — the connector-side probe "
            "`connector-secret-literal-undetected` already records that nothing "
            "detects it."
        ),
    ),
    AdvisoryRule(
        id="ADV-SHRD-002", tier="waiver", resource="shared",
        prose=(
            "A temporal field's declared Arrow type carries a zone only when a "
            "real wire sample carries one, and a `date-time` is never defaulted "
            "to zone-aware."
        ),
        governs="authoring-choice",
        waiver=(
            "Both the bare and the zoned spelling are canonical Arrow types the "
            "engine grammar admits, so only the provider's actual wire value "
            "decides which is true and no authored document records that value."
        ),
    ),
    AdvisoryRule(
        id="ADV-SHRD-003", tier="waiver", resource="shared",
        prose=(
            "Every document a plugin authors declares `$schema` with the "
            "published canonical URL, including the families whose contract "
            "leaves the field optional."
        ),
        governs="authoring-choice",
        waiver=(
            "The contract leaves `$schema` optional on most document families, "
            "so an omitted declaration validates; requiring it everywhere is a "
            "plugin house rule no validator can express."
        ),
    ),
    AdvisoryRule(
        id="ADV-SHRD-004", tier="waiver", resource="shared",
        prose=(
            "A default the contract or the connector already declares is not "
            "copied into an authored document; a value is authored only where "
            "the user asked for one."
        ),
        governs="authoring-choice",
        waiver=(
            "A copied default validates identically to an omission, so nothing "
            "distinguishes a deliberate user choice from a redundant "
            "restatement of a value the contract or the connector already "
            "supplies."
        ),
    ),
    AdvisoryRule(
        id="ADV-SHRD-005", tier="waiver", resource="shared",
        prose=(
            "An identity handle is opaque: no version, tenant, or object "
            "identity is encoded into one or parsed back out of one."
        ),
        governs="authoring-choice",
        waiver=(
            "Any handle matching its field's pattern validates whatever meaning "
            "an author read into it, so a document built on a parsed handle "
            "stays correct only until the derivation changes."
        ),
    ),
    AdvisoryRule(
        id="ADV-SHRD-006", tier="waiver", resource="shared",
        prose=(
            "A `${...}` placeholder appears only where the value-expression "
            "grammar resolves a `template`; every other slot takes the "
            "characters literally."
        ),
        governs="engine-runtime",
        targets=("_RequestBase", "UrlTemplateDsn"),
        waiver=(
            "The resolver substitutes into any bare string, so outside the two "
            "fields that refuse a placeholder structurally "
            "(`_RequestBase.path`, `UrlTemplateDsn.template`) an out-of-place "
            "`${...}` validates and simply never resolves."
        ),
    ),
    AdvisoryRule(
        id="ADV-SHRD-007", tier="waiver", resource="shared",
        prose=(
            "A `function` expression names a function the engine's registry "
            "declares; a name the engine has not registered is authored "
            "nowhere, including one the docs describe as planned."
        ),
        governs="engine-runtime",
        waiver=(
            "The registry is the engine's and is consulted at connect time; no "
            "contract model checks a function name, so a typo or an unreleased "
            "function validates clean and fails at connect."
        ),
    ),
    AdvisoryRule(
        id="ADV-SHRD-008", tier="waiver", resource="shared",
        prose=(
            "A ref path is authored only from the scope paths the engine "
            "documents as supplied; the contract patterns the leading token "
            "alone, so an invented tail validates and resolves to nothing."
        ),
        governs="engine-runtime",
        waiver=(
            "What each scope actually holds is the engine's resolution context "
            "at run time, which no document records — a path naming a sub-scope "
            "or family the engine does not supply is well-formed here."
        ),
    ),
    AdvisoryRule(
        id="ADV-SHRD-009", tier="waiver", resource="shared",
        prose=(
            "A value the platform can derive at connection time is declared as "
            "a `function` expression and never authored as a pre-computed "
            "literal."
        ),
        governs="engine-runtime",
        targets=("ConnectorBase",),
        waiver=(
            "A pre-computed literal is a valid literal expression; only "
            "resolution at connection time reveals that it is stale, or right "
            "for the connection it was computed against and wrong for this one."
        ),
    ),
    AdvisoryRule(
        id="ADV-SHRD-010", tier="waiver", resource="shared",
        prose=(
            "An inherited header is removed with `headers_remove`; a header "
            "whose value resolves to null or empty is not a deletion."
        ),
        governs="engine-runtime",
        targets=("HttpTransport", "TransportDefaults", "_RequestBase"),
        waiver=(
            "How the engine treats an empty resolved header is deliberately "
            "undecided by the contract and the headers map is typed open, so an "
            "explicit null validates and behaves however the runtime happens "
            "to."
        ),
    ),
    # --- stream document --------------------------------------------------
    AdvisoryRule(
        id="ADV-STRM-018", tier="waiver", resource="stream",
        prose=(
            "A connection-scoped `endpoint_ref` carries the derived "
            "`endpoint_id` whenever the plugin can compute it, so the "
            "cross-document bundle check can resolve the reference."
        ),
        governs="authoring-choice",
        waiver=(
            "The contract derives the handle when it is omitted and verifies it "
            "when supplied, so both an omission and a correct value validate; "
            "only the downstream bundle check notices which one was authored."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-019", tier="waiver", resource="stream",
        prose=(
            "A mapping's `assignments` are applied in the order authored, so an "
            "authored order is preserved and never re-sorted."
        ),
        governs="engine-runtime",
        targets=("StreamMapping",),
        waiver=(
            "Application order is an execution behaviour; every permutation of "
            "the same assignments is the same document to a validator."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-020", tier="waiver", resource="stream",
        prose=(
            "An assignment across a conversion pair the engine classifies as "
            "explicit names that conversion function in a `pipe`; a bare `get` "
            "across the pair is refused, not coerced."
        ),
        governs="engine-runtime",
        targets=("Assignment", "StreamMapping"),
        waiver=(
            "The conversion matrix is engine-published and nothing local "
            "resolves a source column's type against its target, so the "
            "assignment validates here and is refused when the stream runs."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-021", tier="waiver", resource="stream",
        prose=(
            "A validation rule's `value` takes the shape its `type` requires; "
            "ADV-STRM-009 settles only whether a `value` is present."
        ),
        governs="engine-runtime",
        targets=("ValidationRule",),
        waiver=(
            "`ValidationRule.value` is typed open so each rule type can carry "
            "its own payload, so a payload of the wrong shape validates here "
            "and fails only when the rule runs."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-022", tier="waiver", resource="stream",
        prose=(
            "Every field name a stream references resolves to a field the "
            "endpoint document on that side of the transfer declares."
        ),
        governs="cross-artifact",
        targets=(
            "StreamSource", "Filter", "AssignmentTarget",
            "DatabaseConflictKeyedWrite"
        ),
        waiver=(
            "the declaring endpoint document is not part of the stream, so no "
            "in-process check can resolve the name; resolution happens "
            "server-side when the stream is saved."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-023", tier="waiver", resource="stream",
        prose=(
            "A stream reproduces every source-endpoint field name exactly as "
            "the endpoint document records it, with no case-folding, trimming, "
            "quoting or other normalization."
        ),
        governs="cross-artifact",
        targets=("StreamSource",),
        waiver=(
            "the authoritative spelling lives in the source endpoint document, "
            "which the stream model never sees; the contract compares the "
            "strings literally, so a normalized name is a valid document naming "
            "nothing."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-024", tier="waiver", resource="stream",
        prose=(
            "An API destination's `write.mode` is a key the referenced "
            "api-endpoint document declares under `operations.write`."
        ),
        governs="cross-artifact",
        targets=("ApiWrite",),
        waiver=(
            "the model bounds `mode` to the write-mode vocabulary, but which of "
            "those keys a given endpoint declares is a fact in the endpoint "
            "document the stream only references."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-025", tier="waiver", resource="stream",
        prose=(
            "An API source's `replication.method` is one the referenced "
            "endpoint declares in "
            "`operations.read.replication.supported_methods`."
        ),
        governs="cross-artifact",
        targets=("IncrementalReplication", "FullRefreshReplication"),
        waiver=(
            "the declared support set lives in the connector's api-endpoint "
            "document, which the stream model cannot read; the stream-side "
            "vocabulary check cannot narrow to it."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-026", tier="waiver", resource="stream",
        prose=(
            "A filter on an API source targets a read parameter the endpoint "
            "declares as filterable — one carrying `operators` and no "
            "`controlled_by`."
        ),
        governs="cross-artifact",
        targets=("Filter",),
        waiver=(
            "filterability is declared per parameter in the api-endpoint "
            "document; the stream carries no view of it, and only the registry "
            "checks a filter against that parameter's subset on save."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-027", tier="waiver", resource="stream",
        prose=(
            "A filter's `value` carries the type the referenced field declares, "
            "and a membership operator carries an array of such values."
        ),
        governs="cross-artifact",
        targets=("Filter",),
        waiver=(
            "`Filter.value` is untyped in the contract and the field's declared "
            "type lives in the endpoint document the stream references, so "
            "nothing local can compare the two."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-028", tier="waiver", resource="stream",
        prose=(
            "An assignment `target.arrow_type` reproduces the destination "
            "column's declared type exactly, parameters included."
        ),
        governs="cross-artifact",
        targets=("AssignmentTarget",),
        waiver=(
            "the destination column's declared type sits in the endpoint "
            "document the stream references, so a type that differs only in its "
            "parameters is invisible to every local check."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-029", tier="waiver", resource="stream",
        prose=(
            "A stream source omits `replication` only when the source endpoint "
            "supports full refresh."
        ),
        governs="cross-artifact",
        targets=("StreamSource",),
        waiver=(
            "endpoint capability is not visible to the stream document, so the "
            "correctness of the omission default is decided by an artifact the "
            "model cannot read and the rejection lands server-side."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-030", tier="waiver", resource="stream",
        prose=(
            "A stream declares `source.primary_keys` exactly when the source "
            "endpoint carries no primary-key metadata of its own and the "
            "transfer needs record identity, never as keys contradicting the "
            "endpoint's."
        ),
        governs="cross-artifact",
        targets=("StreamSource",),
        waiver=(
            "the endpoint's primary-key metadata sits in a document the stream "
            "only references, and whether record identity is needed is decided "
            "by the destination's write mode in a third document."
        ),
    ),
    AdvisoryRule(
        id="ADV-STRM-031", tier="waiver", resource="stream",
        prose=(
            "A stream references an API endpoint with connector scope only; "
            "connection scope is for database endpoints."
        ),
        governs="cross-artifact",
        targets=("ConnectionEndpointRef", "ConnectorEndpointRef"),
        waiver=(
            "whether the referenced endpoint is an API or a database one is a "
            "fact in that endpoint's own document, and the ref model only "
            "requires that a connection-scope ref carry a `database_object`."
        ),
    ),
    # --- type maps --------------------------------------------------------
    AdvisoryRule(
        id="ADV-TMAP-011", tier="waiver", resource="type-map",
        prose=(
            "A type map declares no wildcard or catch-all fallback rule, "
            "leaving an uncovered native or canonical to hard-error at runtime "
            "so the coverage gap stays visible."
        ),
        governs="authoring-choice",
        waiver=(
            "A catch-all is a well-formed rule like any other, so only the "
            "author's intent separates a deliberate fallback from a coverage "
            "gap papered over."
        ),
    ),
    AdvisoryRule(
        id="ADV-TMAP-012", tier="waiver", resource="type-map",
        prose=(
            "New rules on a connection-scoped type map are appended after the "
            "rules already present, and an existing rule is never removed, "
            "reordered, or edited."
        ),
        governs="authoring-choice",
        waiver=(
            "A rewritten map validates exactly like an appended one and only "
            "the previous file shows the difference, while rule order is "
            "semantic — so a reorder silently changes resolution for every "
            "stream already running on that connection."
        ),
    ),
    AdvisoryRule(
        id="ADV-TMAP-013", tier="waiver", resource="type-map",
        prose=(
            "A type map's rules are authored in the order they must resolve — "
            "the reader stops at the first rule whose matcher hits — so a "
            "narrow rule is placed ahead of any broader rule that would also "
            "match its input."
        ),
        governs="engine-runtime",
        waiver=(
            "Ordering is applied by the engine when it walks the map; every "
            "permutation of the same rules is an equally valid document, so "
            "nothing here can see a rule shadowed by a broader sibling above "
            "it."
        ),
    ),
    AdvisoryRule(
        id="ADV-TMAP-014", tier="waiver", resource="type-map",
        prose=(
            "A read map's `regex` rule is matched against the probe after the "
            "engine's native-type normalization while the pattern itself is "
            "used exactly as authored, so a literal written in any other form "
            "yields a rule that validates and never fires."
        ),
        governs="engine-runtime",
        targets=("TypeMapReadRegexRule",),
        waiver=(
            "Normalization happens in the reader at lookup time; a mis-cased "
            "literal is a well-formed pattern, and the validator can only warn, "
            "so nothing rejects a rule that can never match."
        ),
    ),
    AdvisoryRule(
        id="ADV-TMAP-015", tier="waiver", resource="type-map",
        prose=(
            "A write map's `canonical` matcher is authored in the exact casing "
            "the canonical Arrow vocabulary uses, because write-side matching "
            "preserves case where read-side matching does not."
        ),
        governs="engine-runtime",
        targets=("TypeMapWriteExactRule", "TypeMapWriteRegexRule"),
        waiver=(
            "Case handling belongs to the reader, and only the exact form is "
            "structurally checked — a mis-cased `regex` canonical is a valid "
            "pattern that silently never fires."
        ),
    ),
    AdvisoryRule(
        id="ADV-TMAP-016", tier="waiver", resource="type-map",
        prose=(
            "Every `${name}` in a write rule's rendered native names a capture "
            "group declared by that same rule's `canonical` matcher."
        ),
        governs="engine-runtime",
        targets=("TypeMapWriteRegexRule",),
        waiver=(
            "Only the read direction's backing is checked (ADV-TMAP-003); on "
            "the write side the correspondence is first exercised when the "
            "engine renders DDL, which no document here holds."
        ),
    ),
    AdvisoryRule(
        id="ADV-TMAP-017", tier="waiver", resource="type-map",
        prose=(
            "A connector's write map declares a rendering for every canonical "
            "type a source can hand its system, including the container markers "
            "an API source emits as literal canonicals, and a family is left "
            "uncovered only where the connector's dialect renders it in code."
        ),
        governs="engine-runtime",
        waiver=(
            "Completeness is only observable when the engine probes the map "
            "with a real column's canonical at stream configuration; the "
            "validator's coverage check samples, and a dialect override is a "
            "legitimate gap, so no check here can prove the map complete."
        ),
    ),
    AdvisoryRule(
        id="ADV-TMAP-018", tier="waiver", resource="type-map",
        prose=(
            "A connection-scoped type map declares a rule only for a native or "
            "canonical its connector's map leaves unresolved; a rule repeating "
            "one the connector already covers replaces the connector's "
            "rendering for every stream on that connection."
        ),
        governs="engine-runtime",
        waiver=(
            "The two maps are separate documents the engine concatenates at "
            "resolution time, so nothing validating a connection map can see "
            "what the connector map already covers."
        ),
    ),
    AdvisoryRule(
        id="ADV-TMAP-019", tier="waiver", resource="type-map",
        prose=(
            "A canonical family a connector's `type-map-write.json` leaves "
            "unrendered is one the connector's dialect renders itself through a "
            "`render_column_type` override, never one left out to cut scope."
        ),
        governs="cross-artifact",
        waiver=(
            "whether a missing family is a deliberate hand-off or an oversight "
            "can only be settled by reading the package's dialect for a "
            "`render_column_type` override, which no validator opens — the "
            "write-coverage probe can therefore report a gap only as a warning."
        ),
    ),
    AdvisoryRule(
        id="ADV-TMAP-021", tier="waiver", resource="type-map",
        prose=(
            "A connection-scoped read rule renders the same canonical type the "
            "endpoint document froze for the native it matches."
        ),
        governs="cross-artifact",
        targets=("TypeMapReadExactRule", "TypeMapReadRegexRule"),
        waiver=(
            "the agreement spans the endpoint document and the connection's "
            "type-map file; both are on disk, so this is a gap a bundle-aware "
            "check could close rather than an inherently unreachable one."
        ),
    ),
]

register(AUTHORING_RULES)
