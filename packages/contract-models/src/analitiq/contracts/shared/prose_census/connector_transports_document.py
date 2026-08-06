"""Census entries for ``analitiq.contracts.connector``, part 2 of 2: the value
expressions, transports, capability blocks, and the connector document
classes. Part 1 is :mod:`.connector_auth_contract`."""
from __future__ import annotations

from analitiq.contracts.shared.advisory_prose import (
    ENGINE_CONDUCT,
    ProseObligation,
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    # === connector: transports + expressions =================================
    ProseObligation(
        model="AdbcTransport", field="dsn", rule_ids=("ADV-CTOR-004",),
        prose_hash="4f35f3c711a1",
    ),
    ProseObligation(
        model="AdbcTransport", field="db_kwargs", rule_ids=("ADV-CTOR-004",),
        prose_hash="161a0fcb9905",
    ),
    ProseObligation(
        model="SqlAlchemyTransport", field="driver",
        prose_hash="adabaa1db2eb",
        waiver=(
            "whether the named driver is a real SQLAlchemy dialect "
            "registration is checked at engine transport build (the prose "
            "says so itself); it is not knowable offline from the document"
        ),
    ),
    ProseObligation(
        model="HttpTransport", field="base_url", descriptive=True,
        prose_hash="04961e5771bc",
    ),
    ProseObligation(
        model="TransportDefaults", field="transport_type",
        prose_hash="662ba40aaefa",
        structural=(
            "ConnectorBase._inherit_transport_type stamps it onto every "
            "transports entry that declares none, before discriminated-union "
            "dispatch"
        ),
    ),
    ProseObligation(
        model="TransportDefaults",
        prose_hash="fa5fb9fa4af8",
        structural=(
            "the transport_type half of the merge is stamped in-model by "
            "ConnectorBase._inherit_transport_type"
        ),
        waiver=(
            "the deep-merge of the remaining defaults (`headers`, "
            "`headers_remove`, `timeout_seconds`, `rate_limit`, `options`) is "
            "engine-owned at configure time"
        ),
    ),
    ProseObligation(
        model="LiteralStringExpression",
        prose_hash="3e6139a0f186",
        structural=(
            "`literal` is typed as a required, non-empty-constrained string"
        ),
    ),
    # === connector: document + capabilities ==================================
    ProseObligation(
        model="ConnectorKind",
        prose_hash="0d2449f64148",
        structural="the `ConnectorKind` `Enum`'s own closed membership",
    ),
    ProseObligation(
        model="ConnectorBase", descriptive=True,
        prose_hash="a77be2f3abf3",
    ),
    ProseObligation(
        model="ConnectorBase", field="connector_id",
        prose_hash="5655a4364ea4",
        structural="Field(pattern=SLUG_PATTERN)",
    ),
    ProseObligation(
        model="ConnectorBase", field="documentation_url",
        prose_hash="0c4aad78cafb",
        structural="a Field length cap and an http(s)-prefix pattern",
    ),
    ProseObligation(
        model="ConnectorBase", field="transport_defaults",
        prose_hash="961260af0a46",
        structural=(
            "the transport_type half of the merge is stamped in-model by "
            "ConnectorBase._inherit_transport_type"
        ),
        waiver=(
            "the deep-merge of the remaining defaults (`headers`, "
            "`headers_remove`, `timeout_seconds`, `rate_limit`, `options`) is "
            "engine-owned at configure time"
        ),
    ),
    ProseObligation(
        model="ConnectorBase", field="write_unit", descriptive=True,
        prose_hash="037b6892bfbe",
    ),
    ProseObligation(
        model="WriteUnit", rule_ids=("ADV-CTOR-014",),
        prose_hash="78ad4437c5c6",
    ),
    ProseObligation(
        model="SqlStageCapabilities", rule_ids=("ADV-CTOR-013",),
        prose_hash="332e2d32b12a",
    ),
    ProseObligation(
        model="SqlStageCapabilities", field="dedicated_schema",
        prose_hash="6bf44305b2e9",
        rule_ids=("ADV-CTOR-013",),
        structural=(
            "the non-blank shape is a Field length floor plus "
            "NO_EDGE_WHITESPACE_PATTERN"
        ),
    ),
    ProseObligation(
        model="SqlBulkLoad", rule_ids=("ADV-CTOR-015",),
        prose_hash="4eff5e074de9",
        structural=(
            "per-family Literal types keep adbc_ingest out of the "
            "`sqlalchemy` family; the _undeclared_families_stay_absent "
            "serializer omits undeclared families from dumps"
        ),
    ),
    ProseObligation(
        model="ErrorMap", waiver=ENGINE_CONDUCT,
        prose_hash="a15cfabb6c44",
    ),
    # === connector: remaining transport, capability + document sites =========
    ProseObligation(model="AdbcTransport", prose_hash="f6a5cb5e119c", descriptive=True),
    ProseObligation(
        model="AdbcTransport", field="driver",
        prose_hash="9dd703c028aa",
        structural=(
            "a required (non-optional) field whose closed Literal carries the "
            "engine-shipped driver-family vocabulary"
        ),
    ),
    ProseObligation(model="AdbcTransport", field="transport_type", prose_hash="82ac4d2aa34a", descriptive=True),
    ProseObligation(
        model="ApiConnector",
        prose_hash="98827fbef8ca",
        waiver=(
            "nothing narrows an `api` connector's `transports` to the HTTP "
            "family: the inherited `Transport` union accepts every transport "
            "type, so the parenthesized HttpTransport expectation is "
            "unenforced authoring guidance"
        ),
    ),
    ProseObligation(model="ApiConnector", field="kind", prose_hash="4d3ab3ae7c10", descriptive=True),
    ProseObligation(
        model="Concurrency",
        prose_hash="978ee0a3b0fd",
        structural=(
            "`max_connections` is typed `StrictPositiveInt`, the strict "
            "lower-bounded integer annotation"
        ),
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="Concurrency", field="max_connections",
        prose_hash="b1bebfade44f",
        structural=(
            "typed `StrictPositiveInt`, the strict lower-bounded integer "
            "annotation"
        ),
    ),
    ProseObligation(model="ConnectorBase", field="auth", prose_hash="0e36e023b46c", descriptive=True),
    ProseObligation(model="ConnectorBase", field="concurrency", prose_hash="9f4b26f0cb75", descriptive=True),
    ProseObligation(model="ConnectorBase", field="connection_contract", prose_hash="350ca0dfeb9b", descriptive=True),
    ProseObligation(
        model="ConnectorBase", field="default_transport", rule_ids=("ADV-CTOR-001",),
        prose_hash="94e91832aeaf",
    ),
    ProseObligation(
        model="ConnectorBase", field="description",
        prose_hash="08df0e46784c",
        structural="a Field length cap (`DESCRIPTION_MAX`)",
    ),
    ProseObligation(
        model="ConnectorBase", field="display_name",
        prose_hash="7c950e43803c",
        structural=(
            "Field length bounds and `NO_EDGE_WHITESPACE_PATTERN`, plus the "
            "`validate_display_name` field validator"
        ),
    ),
    ProseObligation(
        model="ConnectorBase", field="error_map", waiver=ENGINE_CONDUCT,
        prose_hash="9e28b733fc2c",
    ),
    ProseObligation(model="ConnectorBase", field="resource_discovery", prose_hash="847ff0961beb", descriptive=True),
    ProseObligation(
        model="ConnectorBase", field="schema_url",
        prose_hash="4c06816b3717",
        structural=(
            "StringConstraints(pattern=_CONNECTOR_SCHEMA_URL_PATTERN), the "
            "host-tolerant matcher"
        ),
        waiver=(
            "whether a document travels as a standalone file (where the "
            "pointer is demanded) or as an API payload is not knowable from "
            "the document itself"
        ),
    ),
    ProseObligation(
        model="ConnectorBase", field="tags",
        prose_hash="8b2385924d71",
        structural=(
            "`TrimmedTag` items behind a Field length cap (`TAGS_MAX`) and "
            "the `validate_tags` field validator"
        ),
    ),
    ProseObligation(
        model="ConnectorBase", field="transports",
        prose_hash="371e0d0567f5",
        structural=(
            "entries dispatch through the `Transport` discriminated union on "
            "`transport_type`, behind a Field length floor; the "
            "transport_type half of the inheritance is stamped in-model by "
            "ConnectorBase._inherit_transport_type"
        ),
        waiver=(
            "the deep-merge of the remaining defaults (`headers`, "
            "`headers_remove`, `timeout_seconds`, `rate_limit`, `options`) is "
            "engine-owned at configure time"
        ),
    ),
    ProseObligation(
        model="ConnectorBase", field="version",
        prose_hash="3d85dbccddc4",
        structural="Field(pattern=SEMVER_PATTERN)",
    ),
    ProseObligation(
        model="DatabaseConnector",
        prose_hash="a503b4217fec",
        structural=(
            "the `transports` value type is narrowed to the "
            "`_DatabaseTransport` union"
        ),
    ),
    ProseObligation(model="DatabaseConnector", field="kind", prose_hash="4d3ab3ae7c10", descriptive=True),
    ProseObligation(
        model="DatabaseConnector", field="sql_capabilities",
        prose_hash="49dfe00625c7",
        structural=(
            "the other kinds' closed models reject `sql_capabilities` as an "
            "unknown key; the required shape facts are `SqlCapabilities`'s "
            "own non-optional fields, with `limits` defaulted"
        ),
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="DatabaseConnector", field="transports",
        prose_hash="ee122464858e",
        structural=(
            "the `_DatabaseTransport` discriminated union closes entries to "
            "the SQL transport families"
        ),
    ),
    ProseObligation(
        model="DatabaseTls",
        prose_hash="5ed0d62315d5",
        waiver=(
            "dialect-owned vocabulary: the prose itself declares that no "
            "canonical mode set is enforced here — interpretation belongs to "
            "the connector package's dialect, and the user-facing constraint "
            "is the connector's own `connection_contract` input enum"
        ),
    ),
    ProseObligation(
        model="DatabaseTls", field="ca_certificate",
        prose_hash="19d7fdd13c5f",
        waiver=(
            "dialect-owned and statically unknowable: which `mode` strings "
            "imply certificate verification is a driver vocabulary the "
            "document does not carry, so the conditional requirement cannot "
            "be checked offline"
        ),
    ),
    ProseObligation(
        model="DatabaseTls", field="mode",
        prose_hash="62cfa5410851",
        waiver=(
            "runtime- and dialect-owned: the expression's resolution to a "
            "plain string happens at connection materialization, and the "
            "resolved vocabulary belongs to the connector package's dialect"
        ),
    ),
    ProseObligation(model="DocumentConnector", prose_hash="51b76a08d558", descriptive=True),
    ProseObligation(model="DocumentConnector", field="kind", prose_hash="4d3ab3ae7c10", descriptive=True),
    ProseObligation(model="DsnBinding", prose_hash="4273be7159ae", descriptive=True),
    ProseObligation(model="DsnBinding", field="encoding", prose_hash="e51510dbf8a0", descriptive=True),
    ProseObligation(model="DsnBinding", field="value", prose_hash="8fc1a5b9a6a5", descriptive=True),
    ProseObligation(
        model="ErrorMap", field="exception",
        prose_hash="5099fa850437",
        structural=(
            "keys are pinned by the `_ExceptionFamily` patterned-key "
            "annotation; values by the closed `ErrorCategory` vocabulary"
        ),
    ),
    ProseObligation(
        model="ErrorMap", field="http",
        prose_hash="744f15344540",
        structural=(
            "keys are pinned by the `_HttpStatusFamily` patterned-key "
            "annotation; values by the closed `ErrorCategory` vocabulary"
        ),
    ),
    ProseObligation(
        model="ErrorMap", field="sqlstate",
        prose_hash="6328e5551e7a",
        structural=(
            "keys are pinned by the `_SqlstateFamily` patterned-key "
            "annotation; values by the closed `ErrorCategory` vocabulary"
        ),
    ),
    ProseObligation(
        model="ErrorMap", field="vendor_code",
        prose_hash="482a1d01c129",
        structural=(
            "keys are pinned by the `_VendorCodeFamily` patterned-key "
            "annotation; values by the closed `ErrorCategory` vocabulary"
        ),
    ),
    ProseObligation(
        model="FileConnector",
        prose_hash="9188ee202bb0",
        structural="`transports` values are typed `FileTransport`",
    ),
    ProseObligation(
        model="FileConnector", field="auth",
        prose_hash="cf43edc1ee5e",
        structural=(
            "`auth` is typed `NoneAuth`, whose `type` Literal admits no other "
            "workflow"
        ),
    ),
    ProseObligation(model="FileConnector", field="kind", prose_hash="4d3ab3ae7c10", descriptive=True),
    ProseObligation(
        model="FileConnector", field="transports",
        prose_hash="bb67bdd294f7",
        structural=(
            "carried by the `Field` length bounds on the entry list, "
            "value-typed `FileTransport`"
        ),
    ),
    ProseObligation(model="FileTransport", prose_hash="4f24c2f5126d", descriptive=True),
    ProseObligation(
        model="FileTransport", field="dialect",
        prose_hash="94207c0aafd4",
        waiver=(
            "the `format`-dependent option schema is engine-owned at "
            "configure time; the document carries only an open "
            "value-expression bundle"
        ),
    ),
    ProseObligation(
        model="FileTransport", field="format",
        prose_hash="cb1d22dbf9e9",
        waiver=(
            "the closed output-format vocabulary binds the RESOLVED value: "
            "the field admits any value-expression and resolution is "
            "engine-owned at run time, so nothing offline checks even a "
            "literal string against the vocabulary"
        ),
    ),
    ProseObligation(model="FileTransport", field="path", prose_hash="881a6ba38ecc", descriptive=True),
    ProseObligation(model="FileTransport", field="transport_type", prose_hash="82ac4d2aa34a", descriptive=True),
    ProseObligation(model="HttpTransport", prose_hash="440e5523e2da", descriptive=True),
    ProseObligation(model="HttpTransport", field="headers", prose_hash="580f7f1bab66", descriptive=True),
    ProseObligation(model="HttpTransport", field="headers_remove", prose_hash="7cd88ee5594c", descriptive=True),
    ProseObligation(model="HttpTransport", field="rate_limit", prose_hash="077966296254", descriptive=True),
    ProseObligation(model="HttpTransport", field="timeout_seconds", prose_hash="41f6dbfe7136", descriptive=True),
    ProseObligation(model="HttpTransport", field="transport_type", prose_hash="82ac4d2aa34a", descriptive=True),
    ProseObligation(
        model="LiteralStringExpression", field="literal",
        prose_hash="4ff750507e45",
        structural=(
            "a StringConstraints length floor makes an empty string "
            "unrepresentable"
        ),
    ),
    ProseObligation(model="NosqlConnector", prose_hash="b002416e0dec", descriptive=True),
    ProseObligation(model="NosqlConnector", field="kind", prose_hash="4d3ab3ae7c10", descriptive=True),
    ProseObligation(
        model="S3Connector",
        prose_hash="199f9ae7547a",
        structural="`transports` values are typed `S3Transport`",
    ),
    ProseObligation(
        model="S3Connector", field="auth",
        prose_hash="8a11b096086b",
        structural=(
            "`auth` is typed `CredentialsAuth`; its `type` Literal pins the "
            "credentials workflow"
        ),
    ),
    ProseObligation(model="S3Connector", field="kind", prose_hash="4d3ab3ae7c10", descriptive=True),
    ProseObligation(
        model="S3Connector", field="transports",
        prose_hash="624ebea61176",
        structural=(
            "carried by the `Field` length bounds on the entry list, "
            "value-typed `S3Transport`"
        ),
    ),
    ProseObligation(model="S3CredentialsBlock", prose_hash="042ae089f80b", descriptive=True),
    ProseObligation(model="S3CredentialsBlock", field="access_key_id", prose_hash="7f9585767cd0", descriptive=True),
    ProseObligation(model="S3CredentialsBlock", field="secret_access_key", prose_hash="ec0f17c41872", descriptive=True),
    ProseObligation(model="S3CredentialsBlock", field="session_token", prose_hash="4ed825a17c84", descriptive=True),
    ProseObligation(model="S3Transport", prose_hash="61533d0a0b6f", descriptive=True),
    ProseObligation(model="S3Transport", field="bucket", prose_hash="800f2e137fa1", descriptive=True),
    ProseObligation(
        model="S3Transport", field="credentials",
        prose_hash="51fa5a98fec1",
        structural="a required (non-optional) `S3CredentialsBlock` field",
    ),
    ProseObligation(
        model="S3Transport", field="dialect",
        prose_hash="94207c0aafd4",
        waiver=(
            "the `format`-dependent option schema is engine-owned at "
            "configure time; the document carries only an open "
            "value-expression bundle"
        ),
    ),
    ProseObligation(
        model="S3Transport", field="format",
        prose_hash="cb1d22dbf9e9",
        waiver=(
            "the closed output-format vocabulary binds the RESOLVED value: "
            "the field admits any value-expression and resolution is "
            "engine-owned at run time, so nothing offline checks even a "
            "literal string against the vocabulary"
        ),
    ),
    ProseObligation(model="S3Transport", field="prefix", prose_hash="e6237c89285a", descriptive=True),
    ProseObligation(model="S3Transport", field="region", prose_hash="94e3e3ffdd8b", descriptive=True),
    ProseObligation(model="S3Transport", field="transport_type", prose_hash="82ac4d2aa34a", descriptive=True),
    ProseObligation(model="SqlAlchemyTransport", prose_hash="4bb802699c56", descriptive=True),
    ProseObligation(model="SqlAlchemyTransport", field="dsn", prose_hash="f7513a7c8de4", descriptive=True),
    ProseObligation(model="SqlAlchemyTransport", field="options", prose_hash="f5bfb8ff221d", descriptive=True),
    ProseObligation(model="SqlAlchemyTransport", field="tls", prose_hash="b5b682061076", descriptive=True),
    ProseObligation(model="SqlAlchemyTransport", field="transport_type", prose_hash="82ac4d2aa34a", descriptive=True),
    ProseObligation(
        model="SqlBulkLoad", field="adbc",
        prose_hash="39076a71bb13",
        rule_ids=("ADV-CTOR-015",),
        structural=(
            "the `_AdbcBulkMechanism` Literal closes this family's mechanism "
            "vocabulary"
        ),
    ),
    ProseObligation(
        model="SqlBulkLoad", field="sqlalchemy",
        prose_hash="ad78664bc797",
        rule_ids=("ADV-CTOR-015",),
        structural=(
            "the `_DialectBulkMechanism` Literal closes this family's "
            "mechanism vocabulary"
        ),
    ),
    ProseObligation(
        model="SqlCapabilities",
        prose_hash="6b83bfc7f0a6",
        structural=(
            "the required shape facts are non-optional fields on the closed "
            "model; `limits` defaults"
        ),
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="SqlCapabilities", field="bulk_load",
        prose_hash="0dc7daa93dcf",
        structural=(
            "a required (non-optional) `SqlBulkLoad` field whose members are "
            "individually defaulted"
        ),
    ),
    ProseObligation(
        model="SqlCapabilities", field="catalog",
        prose_hash="c630412ea34e",
        structural="the field's closed Literal vocabulary",
    ),
    ProseObligation(
        model="SqlCapabilities", field="limits",
        prose_hash="e0c5f78fb10a",
        structural="a defaulted (optional) field on the otherwise-required `SqlCapabilities` shape",
    ),
    ProseObligation(
        model="SqlCapabilities", field="merge_form",
        prose_hash="7a6c2512b531",
        structural="the field's closed Literal vocabulary",
    ),
    ProseObligation(
        model="SqlCapabilities", field="session_targeting",
        prose_hash="165a3d29474f",
        structural="the field's closed Literal vocabulary",
    ),
    ProseObligation(model="SqlCapabilities", field="stage", prose_hash="31cbee74695d", descriptive=True),
    ProseObligation(
        model="SqlLimits",
        prose_hash="5b0f29ace860",
        structural=(
            "the caps are typed `StrictPositiveInt`, the strict lower-bounded "
            "integer annotation"
        ),
        waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="SqlLimits", field="max_bind_params",
        prose_hash="935e80f50d25",
        structural=(
            "typed `StrictPositiveInt`, the strict lower-bounded integer "
            "annotation"
        ),
    ),
    ProseObligation(
        model="SqlLimits", field="max_identifier_len",
        prose_hash="563bba60bf19",
        structural=(
            "typed `StrictPositiveInt`, the strict lower-bounded integer "
            "annotation"
        ),
    ),
    ProseObligation(
        model="SqlStageCapabilities", field="schema_", rule_ids=("ADV-CTOR-013",),
        prose_hash="880f4dc8f45a",
    ),
    ProseObligation(
        model="SqlStageCapabilities", field="scope",
        prose_hash="0a879f0b96fa",
        structural="the field's closed Literal vocabulary",
    ),
    ProseObligation(model="SqlStageCapabilities", field="transactional_ddl", prose_hash="1929336b2bbb", descriptive=True),
    ProseObligation(model="StdoutConnector", prose_hash="70736aa12113", descriptive=True),
    ProseObligation(
        model="StdoutConnector", field="auth",
        prose_hash="d567fbfb0645",
        structural=(
            "`auth` is typed `NoneAuth`, whose `type` Literal admits no other "
            "workflow"
        ),
    ),
    ProseObligation(model="StdoutConnector", field="kind", prose_hash="4d3ab3ae7c10", descriptive=True),
    ProseObligation(
        model="StdoutConnector", field="transports",
        prose_hash="7a6970c1705e",
        structural=(
            "carried by the `Field` length bounds on the entry list, "
            "value-typed `StdoutTransport`"
        ),
    ),
    ProseObligation(model="StdoutTransport", prose_hash="3e76035541f9", descriptive=True),
    ProseObligation(
        model="StdoutTransport", field="format",
        prose_hash="a3cdfad7772b",
        waiver=(
            "the closed output-format vocabulary binds the RESOLVED value: "
            "the field admits any value-expression and resolution is "
            "engine-owned at run time, so nothing offline checks even a "
            "literal string against the vocabulary; the stated fallback is "
            "substituted by the engine — the document records only the "
            "field's absence"
        ),
    ),
    ProseObligation(model="StdoutTransport", field="transport_type", prose_hash="82ac4d2aa34a", descriptive=True),
    ProseObligation(model="TransportDefaults", field="headers", prose_hash="77fa4e146ccc", descriptive=True),
    ProseObligation(model="TransportDefaults", field="headers_remove", prose_hash="55647d01f9dc", descriptive=True),
    ProseObligation(model="TransportDefaults", field="options", prose_hash="05559bfa5afe", descriptive=True),
    ProseObligation(model="TransportDefaults", field="rate_limit", prose_hash="56d8cdd9b80c", descriptive=True),
    ProseObligation(model="TransportDefaults", field="timeout_seconds", prose_hash="dbad2246f1c7", descriptive=True),
    ProseObligation(model="TransportRateLimit", prose_hash="190ee80dd951", descriptive=True),
    ProseObligation(model="TransportRateLimit", field="max_requests", prose_hash="a5428dfbd649", descriptive=True),
    ProseObligation(model="TransportRateLimit", field="time_window_seconds", prose_hash="8b74b8aa24ff", descriptive=True),
    ProseObligation(model="UrlTemplateDsn", prose_hash="6157c864874b", descriptive=True),
    ProseObligation(
        model="UrlTemplateDsn", field="bindings", rule_ids=("ADV-CTOR-011",),
        prose_hash="30908833846f",
    ),
    ProseObligation(model="UrlTemplateDsn", field="kind", prose_hash="b6d76f4de216", descriptive=True),
    ProseObligation(
        model="UrlTemplateDsn", field="template", rule_ids=("ADV-CTOR-011",),
        prose_hash="17e78642d3da",
    ),
    ProseObligation(
        model="WriteUnit", field="bytes",
        prose_hash="88834c6400d8",
        structural="a Field ge lower bound",
    ),
    ProseObligation(
        model="WriteUnit", field="rows",
        prose_hash="62e370fef38a",
        structural="a Field ge lower bound",
    ),
    ProseObligation(model="connector.RefExpression", prose_hash="302bf6735c2a", descriptive=True),
    ProseObligation(model="connector.RefExpression", field="ref", prose_hash="c6bff4fda865", descriptive=True),
    ProseObligation(model="connector.TemplateExpression", prose_hash="54e41c6b1776", descriptive=True),
    ProseObligation(model="connector.TemplateExpression", field="template", prose_hash="c955414c0650", descriptive=True),
)
