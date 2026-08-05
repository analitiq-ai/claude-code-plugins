"""The prose-obligation census — every normative statement in the contract's
own prose, bound to what enforces it (issue #127).

:mod:`advisory_rules` is the census of relational rules; this module is its
missing other half. The registry's tests verify the integrity of rules that
EXIST — every check starts from a registered rule, so an obligation stated in a
field description or model docstring and never registered was invisible (that
is exactly how #123 shipped: the pagination `response.body.*` rule lived in
``ResponseExtraction.schema``'s description for months, enforced by nothing).

Every field description and model docstring in ``analitiq.contracts`` whose
text matches :data:`NORMATIVE_PATTERN` must carry exactly one
:class:`ProseObligation` entry here, binding the prose to at least one of:

- ``rule_ids`` — the ``ADV-*`` advisory rule(s) enforcing the obligation;
- ``structural`` — the model's own structure carries it (a Field pattern /
  bound / default / ``Literal``, a discriminated union, a closed
  ``extra='forbid'`` shape, a single-field validator) — the tier that renders
  into the published JSON Schema, below the advisory registry;
- ``waiver`` — why the obligation is NOT mechanisable (engine-owned at
  configure/run time, cross-document, authoring judgment, or no obligation at
  all — a descriptive use of a modal word).

``tests/unit/test_advisory_prose.py`` enforces the census bidirectionally:
normative prose with no entry fails the build, and an entry whose prose site
disappeared or was reworded below the modal threshold also fails. A waiver is
therefore *data* — a declared, reviewable state — never a comment or an
absence nobody can review.

This module imports no contract models (the :mod:`advisory` convention):
entries bind to their prose sites by class name, so tooling can read the
census without pulling in pydantic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Normative-language detector — the union modal set from #126/#127. A field
#: description or model docstring matching this states an obligation and must
#: be catalogued in :data:`PROSE_OBLIGATIONS`. Two-word phrases tolerate any
#: whitespace (docstrings wrap); ``\bmust\b`` covers "must not".
NORMATIVE_PATTERN = re.compile(
    r"\bmust\b|\bevery\b|\brequires\b|\bmay\s+not\b|\bdefaults\s+to\b"
    r"|\bis\s+required\s+to\b|\bonly\b",
    re.IGNORECASE,
)


# --- Shared waiver reasons ---------------------------------------------------
#
# Named so the census is countable by category, and so one edit re-words a
# category everywhere. A bespoke reason is still the right choice when the
# site's situation is not one of these.

#: The contract's unknowable→skip convention, as registry data (issue #127
#: asked for exactly this: the convention lived only in the docstrings of
#: ``resolve_read_record_schema`` and ``_walk_input_schema_path``). Where an
#: enforced rule resolves an authored path against a declared schema, a path
#: the document provably contradicts is an error, and a path the document
#: simply does not decide is SKIPPED — the engine owns the resolved shape at
#: configure time, so refusing to guess is the correct static behavior.
UNKNOWABLE_SKIP = (
    "bounded by the unknowable-skip convention: the enforcing rule checks what "
    "the document declares and skips what is statically unknowable — the "
    "engine owns the resolved shape at configure time (see "
    "resolve_read_record_schema / _walk_input_schema_path in endpoints.py)"
)

#: The prose states a default the ENGINE substitutes at configure/dispatch
#: time; the document records only absence, so there is no shape to check.
ENGINE_OWNED_DEFAULTING = (
    "engine-owned defaulting: the stated default is substituted by the engine "
    "at configure/dispatch time; the document records only the field's absence"
)

#: The sentence binds the engine's (or the producing service's) runtime
#: behavior, not a checkable shape of this document.
ENGINE_CONDUCT = (
    "engine-conduct obligation: the sentence binds the engine's or the "
    "producing service's runtime behavior, not a checkable shape of this "
    "document"
)

#: The modal word is descriptive prose (a role, a permission, a scope note);
#: the sentence states no obligation an instance could violate.
DESCRIPTIVE = (
    "no obligation stated: the modal word is descriptive prose, not a "
    "requirement an instance could violate"
)


# --- Census datum ------------------------------------------------------------


@dataclass(frozen=True)
class ProseObligation:
    """One normative prose site, bound to what enforces it — or a waiver.

    ``model`` names the class that DEFINES the prose (never an inheriting
    subclass); ``field`` is the model field name whose description carries it,
    or ``None`` for the class docstring. At least one disposition is required;
    a mixed description (several obligations, differently carried) may combine
    them — the waiver then names the unenforced remainder.
    """

    model: str
    field: str | None = None
    rule_ids: tuple[str, ...] = ()
    structural: str | None = None
    waiver: str | None = None

    def __post_init__(self) -> None:
        if not (self.rule_ids or self.structural or self.waiver):
            raise ValueError(
                f"{self.site}: an obligation must be bound to a rule, a "
                "structural mechanism, or an explicit waiver — an unbound "
                "entry declares nothing"
            )
        for value, label in ((self.structural, "structural"), (self.waiver, "waiver")):
            if value is not None and not value.strip():
                raise ValueError(f"{self.site}: empty {label} is not a declaration")

    @property
    def site(self) -> str:
        return f"{self.model}.{self.field}" if self.field else f"{self.model} (docstring)"


# --- The census --------------------------------------------------------------

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    # === connection ==========================================================
    ProseObligation(
        model="ConnectionStoredMaps", field="secret_refs",
        structural=(
            "every value is typed SecretRefValue, whose "
            "SECRET_REF_VALUE_PATTERN enumerates exactly the cloud-free "
            "schemes the description lists"
        ),
    ),
    # === connector: transports + expressions =================================
    ProseObligation(
        model="AdbcTransport", field="dsn", rule_ids=("ADV-CTOR-004",),
    ),
    ProseObligation(
        model="AdbcTransport", field="db_kwargs", rule_ids=("ADV-CTOR-004",),
    ),
    ProseObligation(
        model="SqlAlchemyTransport", field="driver",
        waiver=(
            "whether the named driver is a real SQLAlchemy dialect "
            "registration is checked at engine transport build (the prose "
            "says so itself); it is not knowable offline from the document"
        ),
    ),
    ProseObligation(
        model="HttpTransport", field="base_url", waiver=DESCRIPTIVE,
    ),
    ProseObligation(
        model="AuthOperationTemplate", field="transport_ref",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="PostAuthOperationRequest", field="transport_ref",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="ResourceDiscovery", field="transport_ref",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="TransportDefaults", field="transport_type",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="TransportDefaults", waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="LiteralStringExpression",
        structural=(
            "literal is typed Annotated[str, StringConstraints(min_length=1)] "
            "— required and non-empty"
        ),
    ),
    # === connector: auth =====================================================
    ProseObligation(
        model="AwsIamAuth",
        structural=(
            "closed StrictModel declaring no OAuth children; extra='forbid' "
            "rejects them as unknown keys"
        ),
    ),
    ProseObligation(
        model="NoneAuth",
        structural=(
            "closed StrictModel declaring neither authorize, token_exchange "
            "nor refresh; extra='forbid' rejects them as unknown keys"
        ),
    ),
    ProseObligation(
        model="OAuth2AuthorizationCodeAuth",
        structural="authorize and token_exchange are required (non-optional) fields",
    ),
    ProseObligation(
        model="OAuth2ClientCredentialsAuth",
        structural=(
            "token_exchange is a required field; authorize is absent from the "
            "closed model, so extra='forbid' rejects it"
        ),
    ),
    ProseObligation(
        model="CredentialsAuth",
        waiver=(
            "authoring guidance ('use only when no narrower type fits') — a "
            "judgment between types no validator can arbitrate"
        ),
    ),
    # === connector: document + connection contract ===========================
    ProseObligation(
        model="ConnectorBase", waiver=DESCRIPTIVE,
    ),
    ProseObligation(
        model="ConnectorBase", field="connector_id",
        structural="Field(pattern=SLUG_PATTERN)",
    ),
    ProseObligation(
        model="ConnectorBase", field="documentation_url",
        structural=r"Field(max_length=2048, pattern='^https?://')",
    ),
    ProseObligation(
        model="ConnectorBase", field="transport_defaults",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="ConnectorBase", field="write_unit", waiver=DESCRIPTIVE,
    ),
    ProseObligation(
        model="WriteUnit", rule_ids=("ADV-CTOR-014",),
    ),
    ProseObligation(
        model="SqlStageCapabilities", rule_ids=("ADV-CTOR-013",),
    ),
    ProseObligation(
        model="SqlStageCapabilities", field="dedicated_schema",
        rule_ids=("ADV-CTOR-013",),
        structural=(
            "the non-blank shape is Field(min_length=1, "
            "pattern=NO_EDGE_WHITESPACE_PATTERN)"
        ),
    ),
    ProseObligation(
        model="SqlBulkLoad", rule_ids=("ADV-CTOR-015",),
        structural=(
            "per-family Literal types keep adbc_ingest out of the sqlalchemy "
            "family; the _undeclared_families_stay_absent serializer omits "
            "undeclared families from dumps"
        ),
    ),
    ProseObligation(
        model="ErrorMap", waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="ConnectionContract", waiver=DESCRIPTIVE,
    ),
    ProseObligation(
        model="ConnectionContract", field="required_for_activation",
        rule_ids=("ADV-CTOR-007",),
        waiver=(
            "reference validity is ADV-CTOR-007's; whether the paths RESOLVE "
            "before activation is engine-owned runtime state"
        ),
    ),
    ProseObligation(
        model="ConnectionContractInput", field="required", waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="ConnectionContractInput", field="secret", rule_ids=("ADV-CONN-003",),
    ),
    ProseObligation(
        model="ConnectionContractInputUI", field="options", rule_ids=("ADV-CONN-001",),
    ),
    ProseObligation(
        model="PostAuthOutput", field="storage", rule_ids=("ADV-CTOR-002",),
    ),
    ProseObligation(
        model="PostAuthOutput", field="options_path",
        rule_ids=("ADV-CTOR-002",),
        waiver=(
            "the user_selection/auto_discovery gating is ADV-CTOR-002's; the "
            "response-body-root default is engine-owned at request execution"
        ),
    ),
    ProseObligation(
        model="ResourceDiscoveryImplementation", field="entrypoint",
        rule_ids=("ADV-CTOR-003",),
    ),
    ProseObligation(
        model="ConnectionConditionPredicate", field="eq", waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="ConnectionConditionPredicate", field="present", waiver=ENGINE_CONDUCT,
    ),
    # === api-endpoint: params, request binding ===============================
    ProseObligation(
        model="Param", field="required", waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="RefExpression", field="ref",
        structural=(
            "Field(pattern=_RESOLUTION_SCOPE_PATTERN), built from the "
            "RESOLUTION_SCOPES tuple the description itself enumerates"
        ),
    ),
    ProseObligation(
        model="_RequestBase", field="transport_ref",
        waiver=(
            "the NAME half is cross-document — enforced by analitiq-validator's "
            "endpoint-transport-ref check, which a single-document model cannot "
            "run; the ORIGIN half is enforced by nothing today (the prose says "
            "so; analitiq-engine#454 / #124); the defaulting to "
            "default_transport is engine-owned"
        ),
    ),
    ProseObligation(
        model="_RequestBase", field="path_params",
        rule_ids=("ADV-ENDP-024", "ADV-ENDP-027"),
    ),
    ProseObligation(
        model="WriteRequest", field="path_params",
        rule_ids=("ADV-ENDP-024", "ADV-ENDP-025", "ADV-ENDP-027", "ADV-ENDP-028"),
        waiver=UNKNOWABLE_SKIP,
    ),
    # === api-endpoint: pagination ============================================
    ProseObligation(
        model="ReadOperation", field="pagination", rule_ids=("ADV-ENDP-023",),
    ),
    ProseObligation(
        model="Cursor", field="next_cursor",
        structural=(
            "typed as the Expression discriminated union; a bare string or a "
            "response_path shape selects no branch"
        ),
    ),
    ProseObligation(
        model="Link", field="next_url",
        structural=(
            "typed as the Expression discriminated union; a bare string or a "
            "response_path shape selects no branch"
        ),
    ),
    ProseObligation(
        model="LinkPagination", field="limit", rule_ids=("ADV-ENDP-010",),
    ),
    ProseObligation(
        model="OffsetCursor", field="increment_by",
        structural="required, with no default (the leading ... sentinel)",
        waiver=(
            "which step value is correct (records-returned vs requested-window "
            "offsets) depends on provider semantics the document cannot state — "
            "authoring judgment"
        ),
    ),
    ProseObligation(
        model="PageCursor", field="increment_by", waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="Keyset", field="order_by_field",
        structural="Field(pattern=RECORD_FIELD_PATH_PATTERN)",
    ),
    # === api-endpoint: response ==============================================
    ProseObligation(
        model="ResponseExtraction", field="records",
        rule_ids=("ADV-ENDP-012",),
        structural=(
            "typed RefExpression (never template/literal/function); the "
            "response.body anchor is enforced by ResponseExtraction._validate "
            "and mirrored as a pattern in the published schema"
        ),
    ),
    ProseObligation(
        model="ResponseExtraction", field="schema_",
        rule_ids=(
            "ADV-ENDP-012", "ADV-ENDP-013", "ADV-ENDP-023", "ADV-ENDP-026",
        ),
    ),
    ProseObligation(
        model="ResponseExtraction", field="metadata", rule_ids=("ADV-ENDP-023",),
    ),
    # === api-endpoint: write =================================================
    ProseObligation(
        model="WriteOperation", field="conflict_keys",
        rule_ids=("ADV-ENDP-014", "ADV-ENDP-019"),
    ),
    ProseObligation(
        model="Batching", field="max_records", structural="Field(ge=2)",
    ),
    ProseObligation(
        model="Idempotency",
        structural=(
            "the closed model declares no value slot at all — location and "
            "name only, extra='forbid'"
        ),
    ),
    ProseObligation(
        model="Idempotency", field="location",
        waiver=(
            "body-objectness is known only when the request body is assembled; "
            "the prose states the engine rejects non-object bodies at "
            "configure time"
        ),
    ),
    # === api-endpoint: columns + documents ===================================
    ProseObligation(
        model="Column", field="arrow_type",
        structural=(
            "Field(pattern=ARROW_TYPE_PATTERN), generated from the vendored "
            "engine grammar — parameterized families require their parameters"
        ),
    ),
    ProseObligation(
        model="ApiEndpointDoc", field="schema_url",
        structural="required Literal pinned to schema_url_for('api-endpoint')",
    ),
    ProseObligation(
        model="DatabaseEndpointDoc", field="schema_url",
        structural="required Literal pinned to schema_url_for('database-endpoint')",
    ),
    ProseObligation(
        model="DatabaseObject", field="object_type", waiver=ENGINE_CONDUCT,
    ),
    # === stream ==============================================================
    ProseObligation(
        model="ConnectionEndpointRef", field="endpoint_id",
        rule_ids=("ADV-STRM-003",),
    ),
    ProseObligation(
        model="StreamSource", field="selected_columns", rule_ids=("ADV-STRM-014",),
    ),
    ProseObligation(
        model="_ReplicationBase", field="tie_breaker_fields",
        rule_ids=("ADV-STRM-014",),
    ),
    ProseObligation(
        model="StreamSource", field="replication",
        waiver=(
            "cross-document: the supported replication methods live on the "
            "endpoint document (endpoints.Replication.supported_methods), "
            "which this document cannot see"
        ),
    ),
    ProseObligation(
        model="StreamSource", field="database_pagination",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="PipeExpression", rule_ids=("ADV-STRM-005",),
        structural=(
            "args carries min_length=2 and the prefixItems rewrite publishes "
            "the same positional grammar"
        ),
    ),
    ProseObligation(
        model="ArrowFieldSpec", field="arrow_type",
        structural=(
            "Field(pattern=ARROW_TYPE_PATTERN), generated from the vendored "
            "engine grammar — parameterized families require their parameters"
        ),
    ),
    ProseObligation(
        model="AssignmentTarget", field="arrow_type",
        structural=(
            "Field(pattern=ARROW_TYPE_PATTERN), generated from the vendored "
            "engine grammar — parameterized families require their parameters"
        ),
    ),
    ProseObligation(
        model="ConstantValue", field="arrow_type",
        structural=(
            "Field(pattern=ARROW_TYPE_PATTERN), generated from the vendored "
            "engine grammar — parameterized families require their parameters"
        ),
    ),
    ProseObligation(
        model="ValidationRule", field="value", rule_ids=("ADV-STRM-009",),
    ),
    # === pipeline ============================================================
    ProseObligation(
        model="PipelineConnections", waiver=DESCRIPTIVE,
    ),
    ProseObligation(
        model="Schedule", field="interval_minutes",
        rule_ids=("ADV-PIPE-002",), structural="Field(ge=1)",
    ),
    ProseObligation(
        model="Schedule", field="cron_expression",
        rule_ids=("ADV-PIPE-002",), structural="Field(pattern=CRON_PATTERN)",
    ),
    ProseObligation(
        model="ErrorHandling", field="retry_delay_seconds",
        rule_ids=("ADV-RETRY-001",),
        structural="the effective-delay defaulting is _default_retry_delay's",
    ),
    # === data-sync ===========================================================
    ProseObligation(
        model="PipelineRunRequest",
        structural=(
            "StrictModel whose only field is terminate_existing_sync — an "
            "unknown key is rejected, an empty body validates"
        ),
    ),
    ProseObligation(
        model="PipelineRunAcceptedResponse",
        structural=(
            "the closed PipelineRunAcceptedData shape declares exactly "
            "invocation_id and pipeline_id — no job_id field exists"
        ),
    ),
    ProseObligation(
        model="PipelineRunAcceptedResponse", field="data",
        structural=(
            "data is required; PipelineRunAcceptedData declares exactly the "
            "two tracking identifiers"
        ),
    ),
    ProseObligation(
        model="PipelineRunStatusData", field="error", rule_ids=("ADV-DSYNC-001",),
    ),
    ProseObligation(
        model="PublicRunError", rule_ids=("ADV-DSYNC-001",),
    ),
    ProseObligation(
        model="PipelineTerminateData", waiver=ENGINE_CONDUCT,
    ),
    ProseObligation(
        model="PipelineTerminateData", field="job_id", waiver=ENGINE_CONDUCT,
    ),
    # === shared ==============================================================
    ProseObligation(
        model="StrictModel",
        structural="extra='forbid' rejects x-* keys like any unknown key",
    ),
    ProseObligation(
        model="RetryErrorHandlingBase",
        waiver=(
            "authoring convention about this module's own layering (a subclass "
            "re-declares a field only to attach a public description) — binds "
            "contract authors, not instances"
        ),
    ),
    ProseObligation(
        model="CorruptedPlaceholderBase", field="corrupted",
        structural="required Literal[True]",
    ),
    # === type-map ============================================================
    ProseObligation(
        model="_TypeMapRuleBase", waiver=DESCRIPTIVE,
    ),
)
