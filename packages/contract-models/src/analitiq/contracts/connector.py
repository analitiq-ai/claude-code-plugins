"""
Connector models.

A connector definition declares how a provider is authenticated, configured,
and reached: its `kind` discriminator, an auth block, a connection contract
(the inputs a connection must supply), and a transport contract.

Fields typed `Any` and described as value-expressions accept the shared
value-expression grammar — refs, templates, literals, and functions —
resolved at runtime against the connection's stored values. The authored
contract is closed: `x-*` extension keys are rejected at every level.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    Tag as UnionTag,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)

from analitiq.contracts.shared.rules import HeaderMergeRules, violation
from analitiq.contracts.shared.common import (
    DESCRIPTION_MAX,
    DISPLAY_NAME_MAX,
    DISPLAY_NAME_MIN,
    NO_EDGE_WHITESPACE_PATTERN,
    SEMVER_PATTERN,
    SLUG_PATTERN,
    StrictModel,
    TrimmedTag,
    TAGS_MAX,
    schema_url_for,
    schema_url_pattern,
    validate_display_name,
    validate_tags,
)
from analitiq.contracts.shared.types import StrictPositiveInt
from analitiq.contracts.value_expression import (
    RESOLUTION_SCOPE_PATTERN,
    RESOLUTION_SCOPES,
    unqualified_tokens,
)

CONNECTOR_SCHEMA_URL = schema_url_for("connector")


def _dumped(node: Any) -> Any:
    """A parsed expression back in the plain-JSON shape the grammar walker reads.

    A field typed as an expression union hands back a model; one typed `Any`
    hands back what was authored. `unqualified_tokens` walks dicts and strings,
    so the model form is dumped and everything else passes through.
    """
    return node.model_dump() if isinstance(node, BaseModel) else node


class ValueExpressionScopes:
    """A block carrying value expressions a runtime resolves.

    Enforces RULE-CTOR-057 wherever it applies. Mixed in rather than repeated,
    for the reason `HeaderMergeRules` is: the check is one check, and a model
    gains it by inheriting.

    Each model names its own fields, because which of them a runtime resolves
    is not visible from the annotation. `rate_limit.time_window_seconds` is
    typed `Any` and described as taking an expression, and both consumers read
    it literally — so it is absent from every declaration below, and checking
    it would tell an author that scoping the token makes it resolve.

    The two declarations differ by what the field HOLDS. `EXPRESSION_FIELDS` is
    one expression; `EXPRESSION_MAPS` is a name-to-expression map, walked per
    entry so the failure names the header rather than the block.

    Keeping them apart is not only about the message. Walking an expression as
    though it were a map drops a check: the entries of `{"ref": "token"}` are
    the string `"token"`, and a bare string is the TEMPLATE form, so it carries
    no placeholder and passes. The reverse — walking a map whole — stays
    correct, because the grammar walker recurses through a plain object into
    the expressions under it, and costs only the key name in the message.
    """

    #: Fields whose whole value is one value expression.
    EXPRESSION_FIELDS: ClassVar[tuple[str, ...]] = ()
    #: Fields holding a map of name -> value expression.
    EXPRESSION_MAPS: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _expressions_qualified(self):
        for name in self.EXPRESSION_FIELDS:
            _reject_unqualified(_dumped(getattr(self, name)), name)
        for name in self.EXPRESSION_MAPS:
            for key, value in (getattr(self, name) or {}).items():
                _reject_unqualified(_dumped(value), f"{name}.{key}")
        return self


def _reject_unqualified(node: Any, where: str) -> None:
    """Refuse a value expression whose ref/placeholder names no resolution scope.

    `where` names the field — or, for a map, the key — so a transport declaring
    several headers reports the one that is wrong rather than the block.
    """
    unqualified = unqualified_tokens(node)
    if unqualified:
        raise ValueError(
            f"{where}: {', '.join(sorted(set(unqualified)))} "
            f"names no resolution scope ({', '.join(RESOLUTION_SCOPES)}); "
            "without one the value read is whatever the resolver finds under "
            "that bare name, or an error naming no placeholder — say where it "
            "comes from (spec: §Value Expressions)"
        )
# Host-tolerant matcher for the `$schema` field: a connector authored against
# the canonical `schemas.analitiq.ai` URL must still validate when the engine
# checks it against a per-environment schema (`schemas.analitiq.work` / `.dev`).
_CONNECTOR_SCHEMA_URL_PATTERN = schema_url_pattern("connector")



# --- Enums ---


class ConnectorKind(str, Enum):
    """Closed connector-kind discriminator."""
    API = "api"
    DATABASE = "database"
    NOSQL = "nosql"
    DOCUMENT = "document"
    FILE = "file"
    S3 = "s3"
    STDOUT = "stdout"


# --- Supporting Models ---


class FormFieldOption(StrictModel):
    """Option for select-style widgets."""

    value: str = Field(..., description="Option value")
    label: str = Field(..., description="Display label")


# --- Auth Models (discriminated union) ---


class AuthOperationTemplate(ValueExpressionScopes, HeaderMergeRules, StrictModel):
    """Operation template for auth `authorize` / `token_exchange` / `refresh` / `test`.

    The HTTP `base_url` lives on the named transport; this template selects the
    transport via `transport_ref` (omit to use `default_transport`) and supplies
    the per-operation `path`, headers, and body.
    """

    # The auth Lambdas resolve all three when they build the token exchange:
    # `path` joins the transport's base URL, `headers` is the last merge layer,
    # and `body` resolves as a string or walked as JSON.
    EXPRESSION_FIELDS: ClassVar[tuple[str, ...]] = ("path", "body")
    EXPRESSION_MAPS: ClassVar[tuple[str, ...]] = ("headers",)

    transport_ref: str | None = Field(
        default=None,
        description="Named transport this request is dispatched through; defaults to `default_transport`",
    )
    method: str | None = Field(
        default=None,
        description="HTTP method. Engines treat absence as GET; declare it explicitly for non-HTTP transports.",
    )
    path: str = Field(
        ...,
        min_length=1,
        description="Path or relative URL on the selected transport",
    )
    headers: dict[str, Any] | None = Field(
        default=None,
        description="Request headers; values may be literals, `{ref}`, `{template}`, or `{function}`",
    )
    headers_remove: list[str] | None = Field(
        default=None,
        description="Header names to delete from inherited transport defaults",
    )
    body: Any | None = Field(default=None, description="Request body; type depends on transport/encoding")


class ApiKeyAuth(StrictModel):
    """API key authentication. Required values declared in `connection_contract.inputs`."""

    type: Literal["api_key"] = Field(..., description="Auth type")
    test: AuthOperationTemplate | None = Field(default=None, description="Optional connection test template")


class BasicAuth(StrictModel):
    """Basic (username/password) authentication.

    Required user-entered values are declared in `connection_contract.inputs`
    (typically `username` and `password`). The connector should not declare
    field names here.
    """

    type: Literal["basic_auth"] = Field(..., description="Auth type")
    test: AuthOperationTemplate | None = Field(default=None, description="Optional connection test template")


class OAuth2AuthorizationCodeAuth(StrictModel):
    """OAuth2 authorization code flow.

    Spec: §Authentication — requires `authorize` and `token_exchange`; `refresh`
    optional.
    """

    type: Literal["oauth2_authorization_code"] = Field(..., description="Auth type")
    authorize: AuthOperationTemplate = Field(..., description="Authorization request template")
    token_exchange: AuthOperationTemplate = Field(..., description="Token exchange request template")
    refresh: AuthOperationTemplate | None = Field(default=None, description="Token refresh request template")
    test: AuthOperationTemplate | None = Field(default=None, description="Optional connection test template")


class OAuth2ClientCredentialsAuth(StrictModel):
    """OAuth2 client credentials flow.

    Spec: §Authentication — requires `token_exchange`; must omit `authorize`.
    """

    type: Literal["oauth2_client_credentials"] = Field(..., description="Auth type")
    token_exchange: AuthOperationTemplate = Field(..., description="Token exchange request template")
    refresh: AuthOperationTemplate | None = Field(default=None, description="Token refresh request template")
    test: AuthOperationTemplate | None = Field(default=None, description="Optional connection test template")


class JwtAuth(StrictModel):
    """JWT-based authentication. Signing inputs are declared in `connection_contract.inputs`."""

    type: Literal["jwt"] = Field(..., description="Auth type")
    test: AuthOperationTemplate | None = Field(default=None, description="Optional connection test template")


class DbAuth(StrictModel):
    """Database connection authentication. Inputs declared in `connection_contract.inputs`."""

    type: Literal["db"] = Field(..., description="Auth type")
    test: AuthOperationTemplate | None = Field(default=None, description="Optional connection test template")


class CredentialsAuth(StrictModel):
    """Provider-specific credentials bundle (use only when no narrower type fits)."""

    type: Literal["credentials"] = Field(..., description="Auth type")
    test: AuthOperationTemplate | None = Field(default=None, description="Optional connection test template")


class AwsIamAuth(StrictModel):
    """AWS IAM / role / profile / credential-chain auth.

    Spec: §Authentication — must not declare OAuth-specific children. User
    values (account, role, profile, …) live in `connection_contract.inputs`.
    """

    type: Literal["aws_iam"] = Field(..., description="Auth type")
    test: AuthOperationTemplate | None = Field(default=None, description="Optional connection test template")


class NoneAuth(StrictModel):
    """Marker for connectors that need no authentication workflow.

    Spec: §Authentication — must not declare `authorize`, `token_exchange`, or
    `refresh`. `test` remains optional per the shared `Auth object fields`
    table.
    """

    type: Literal["none"] = Field(..., description="Auth type")
    test: AuthOperationTemplate | None = Field(default=None, description="Optional connection test template")


Auth = Annotated[
    Union[
        Annotated[ApiKeyAuth, UnionTag("api_key")],
        Annotated[BasicAuth, UnionTag("basic_auth")],
        Annotated[OAuth2AuthorizationCodeAuth, UnionTag("oauth2_authorization_code")],
        Annotated[OAuth2ClientCredentialsAuth, UnionTag("oauth2_client_credentials")],
        Annotated[JwtAuth, UnionTag("jwt")],
        Annotated[DbAuth, UnionTag("db")],
        Annotated[CredentialsAuth, UnionTag("credentials")],
        Annotated[AwsIamAuth, UnionTag("aws_iam")],
        Annotated[NoneAuth, UnionTag("none")],
    ],
    Discriminator("type"),
]


# --- Connection Contract (spec: §Connection Contract) ---


class InputSource(str, Enum):
    """How a connection contract input is provisioned.

    Closed enum: `user` or `platform`. Values produced after authentication
    belong in `post_auth_outputs`, not in `inputs` with a post-auth source.
    """
    USER = "user"
    PLATFORM = "platform"


class InputPhase(str, Enum):
    """Lifecycle phase a connection contract input is provisioned in.

    Closed enum: `pre_auth` or `auth`. Values produced after authentication
    belong in `post_auth_outputs`.
    """
    PRE_AUTH = "pre_auth"
    AUTH = "auth"


# Where a connection contract input is stored — closed enum:
# `connection.parameters` (non-secret) or `secrets` (referenced via `secret_refs`).
ContractInputStorage = Literal["connection.parameters", "secrets"]


# Where a post-auth output is stored — closed enum: `connection.selections`,
# `connection.discovered`, or `secrets`.
PostAuthOutputStorage = Literal[
    "connection.selections",
    "connection.discovered",
    "secrets",
]


class PostAuthOutputMode(str, Enum):
    """How a post-auth output's value is produced. Spec: §Post-Auth Outputs."""
    USER_SELECTION = "user_selection"
    AUTO_DISCOVERY = "auto_discovery"


class ConnectionContractInputUI(StrictModel):
    """UI hints for a connection contract input. Spec: §Connection Inputs.

    The spec calls these "Label, help text, widget, defaults, validation hints".
    `default` here is the displayed-default-in-the-form value (which may differ
    from the contract-resolution default on `ConnectionContractInput.default`).
    """


    label: str | None = Field(default=None, description="Display label for the input")
    help_text: str | None = Field(default=None, description="Inline help text shown to the user")
    widget: str | None = Field(default=None, description="Widget hint (text, password, select, textarea, number, ...)")
    placeholder: str | None = Field(default=None, description="Placeholder text shown in empty inputs")
    default: Any | None = Field(default=None, description="Default value to pre-fill in the form widget")
    options: list[FormFieldOption] | None = Field(
        default=None,
        description=(
            "Options for select-style widgets. When both `options` and the "
            "input's `enum` are provided they must enumerate the same value "
            "set — `ui.options` may not omit any `enum` value or add a value "
            "not in `enum` (spec: §Connection Inputs)."
        ),
    )


class ConnectionContractInput(StrictModel):
    """One submitted/provisioned value declared by the connection contract.

    Spec: §Connection Inputs. The combination of `name` and `storage` determines
    the runtime reference path (e.g. `connection.parameters.host`,
    `secrets.api_key`).
    """


    source: InputSource = Field(
        ...,
        description="How the value is provisioned (closed enum: user, platform).",
    )
    phase: InputPhase = Field(
        ...,
        description=(
            "Lifecycle phase the value is provisioned in (closed enum: "
            "pre_auth, auth)."
        ),
    )
    storage: ContractInputStorage = Field(
        ...,
        description=(
            "Where the resolved value is durably stored (closed enum: "
            "connection.parameters, secrets)."
        ),
    )
    type: Literal["string", "integer", "number", "boolean", "array", "object"] = Field(
        ...,
        description=(
            "JSON value type used for validation and coercion. Spec: "
            "§Connection Inputs — closed vocabulary."
        ),
    )
    required: bool = Field(..., description="Whether resolution must produce a value")
    default: Any | None = Field(default=None, description="Connector-defined default for optional inputs")
    enum: list[Any] | None = Field(
        default=None,
        description="Authoritative allowed-value list for scalar inputs (non-empty when present).",
    )
    secret: bool | None = Field(
        default=None,
        description=(
            "Required when `storage` is `secrets`; must be `true` iff `storage` "
            "is `secrets`. Otherwise omitted or `false`."
        ),
    )
    format: str | None = Field(default=None, description="Format hint (e.g. 'uri', 'date-time')")
    pattern: str | None = Field(default=None, description="Regex pattern for string validation")
    ui: ConnectionContractInputUI | None = Field(default=None, description="UI rendering hints")

    @model_validator(mode="after")
    def _consistency(self) -> "ConnectionContractInput":
        is_secret_storage = self.storage == "secrets"
        if is_secret_storage and self.secret is not True:
            raise ValueError(
                "storage='secrets' requires secret=true "
                "(spec: §Connection Inputs — secret iff storage='secrets')"
            )
        if not is_secret_storage and self.secret is True:
            raise ValueError(
                "secret=true requires storage='secrets' "
                "(spec: §Connection Inputs — secret iff storage='secrets')"
            )
        if self.enum is not None and len(self.enum) == 0:
            raise ValueError(
                "enum must be non-empty when present "
                "(spec: §Connection Inputs — enum is the authoritative "
                "allowed-value list)"
            )
        return self

    @model_validator(mode="after")
    def _options_offer_the_enum(self) -> "ConnectionContractInput":
        """RULE-CONN-001: the picker offers exactly what the contract accepts."""
        offered = [o.value for o in (self.ui.options if self.ui else None) or ()]
        if not offered or not self.enum:
            return self
        # Compared element-wise rather than as sets: an enum member may be an
        # object or an array, which a set cannot hold.
        extra = [v for v in offered if v not in self.enum]
        missing = [v for v in self.enum if v not in offered]
        if extra or missing:
            raise violation("RULE-CONN-001", f"extra={extra!r}; missing={missing!r}")
        return self

    @model_validator(mode="after")
    def _default_is_an_enum_member(self) -> "ConnectionContractInput":
        """RULE-CONN-002: the fallback the platform supplies is a legal value."""
        if self.default is None or not self.enum:
            return self
        if self.default not in self.enum:
            raise violation(
                "RULE-CONN-002", f"value={self.default!r} not in {self.enum!r}"
            )
        return self


# Python attribute names of the mutually-exclusive operator keys — exactly one
# of them may be set per predicate; `in_` is aliased to the `in` grammar key.
_CONDITION_OPERATOR_FIELDS = ("eq", "in_", "not_in", "present", "regex")
_CONDITION_OPERATOR_ALIASES = {"in_": "in"}

# A predicate operand is a JSON scalar — never a container, null, or a
# non-finite float. NaN/Infinity are not JSON numbers and the published schema
# rejects them; `model_dump_json` would also emit them as `null`, silently
# dropping the operator (see `_exactly_one_operator`). A null operand does not
# count as a declared operator either.
ConditionScalar = str | int | Annotated[float, Field(allow_inf_nan=False)] | bool

# Structural mirror of `_exactly_one_operator` for the published JSON Schema, so
# author-time schema validation agrees with the model. `oneOf` matches exactly
# one branch; each branch requires its operator present AND non-null, so a
# predicate with zero or multiple non-null operators fails schema validation too
# — not only the Pydantic model. Keys are the wire aliases (`in`, not `in_`).
_PREDICATE_EXACTLY_ONE_OPERATOR: dict[str, Any] = {
    "oneOf": [
        {"required": [alias], "properties": {alias: {"not": {"type": "null"}}}}
        for alias in ("eq", "in", "not_in", "present", "regex")
    ]
}


class ConnectionConditionPredicate(StrictModel):
    """A single connection-input test used in
    `connection_contract.validation.rules[].when`: declares `field` and exactly
    one operator key (`eq`/`in`/`not_in`/`present`/`regex`).
    """

    model_config = ConfigDict(
        extra="forbid", json_schema_extra=_PREDICATE_EXACTLY_ONE_OPERATOR
    )

    field: str = Field(
        ...,
        min_length=1,
        description="Declared connection input name the predicate tests.",
    )
    eq: ConditionScalar | None = Field(
        default=None, description="Scalar value the field must equal."
    )
    in_: list[ConditionScalar] | None = Field(
        default=None,
        alias="in",
        min_length=1,
        description="Non-empty array of allowed scalar values.",
    )
    not_in: list[ConditionScalar] | None = Field(
        default=None,
        min_length=1,
        description="Non-empty array of disallowed scalar values.",
    )
    present: Literal[True] | None = Field(
        default=None,
        description="Boolean literal true; the field must resolve to a non-empty value.",
    )
    regex: str | None = Field(
        default=None,
        min_length=1,
        description="Regular expression matched against the resolved string value.",
    )

    @field_validator("present", mode="before")
    @classmethod
    def _present_is_strictly_boolean(cls, v: Any) -> Any:
        # `Literal[True]` compares by equality, so `1`/`1.0` (== True) would slip
        # through; the wire grammar and the schema's `const: true` require a real
        # boolean. Reject any non-bool before the Literal check.
        if v is not None and not isinstance(v, bool):
            raise ValueError("present must be the boolean literal true")
        return v

    @model_validator(mode="after")
    def _exactly_one_operator(self) -> "ConnectionConditionPredicate":
        # Count operators by usable (non-null) value, not key presence. A
        # serialized predicate (a `by_alias` dump) carries the four unused
        # operators as explicit `null`, and an authored `eq: null` is not a
        # usable test — connection inputs are strings/enums/secrets, never JSON
        # null — so a null-valued operator key never counts as declared.
        declared = sorted(
            _CONDITION_OPERATOR_ALIASES.get(f, f)
            for f in _CONDITION_OPERATOR_FIELDS
            if getattr(self, f) is not None
        )
        if len(declared) != 1:
            raise ValueError(
                "a connection condition predicate must declare exactly one "
                "operator key (eq/in/not_in/present/regex); "
                f"got {declared or 'none'}"
            )
        return self


class ConnectionContractValidationRule(StrictModel):
    """Cross-input declarative validation rule. Spec: §Cross-Input Validation."""

    when: ConnectionConditionPredicate = Field(
        ..., description="Predicate that decides whether the rule applies"
    )
    require: list[str] | None = Field(default=None, description="Fields required when predicate matches")
    forbid: list[str] | None = Field(default=None, description="Fields forbidden when predicate matches")
    message: str | None = Field(default=None, description="Human-readable validation error")


class ConnectionContractValidation(StrictModel):
    """Cross-input validation block. Spec: §Cross-Input Validation."""


    rules: list[ConnectionContractValidationRule] = Field(
        default_factory=list,
        description="Cross-input validation rules; per-input rules belong on the input itself",
    )


class PostAuthOperationRequest(ValueExpressionScopes, StrictModel):
    """Request template used by `options_request` / `discovery_request` to populate
    a post-auth output. Spec: §Post-Auth Outputs.
    """

    # Resolved through the same auth-request path that serves the auth block.
    # `PostAuthOutput`'s `value_path` / `label_path` / `options_path` sit beside
    # this and are NOT expressions — they are extraction paths walked against
    # the response.
    EXPRESSION_FIELDS: ClassVar[tuple[str, ...]] = ("path", "body")
    EXPRESSION_MAPS: ClassVar[tuple[str, ...]] = ("headers",)


    transport_ref: str | None = Field(
        default=None,
        description="Named transport this request is dispatched through; defaults to `default_transport`",
    )
    method: str | None = Field(
        default=None,
        description=(
            "HTTP method. Engines treat absence as GET; declare it explicitly to "
            "signal the intent to non-HTTP transports."
        ),
    )
    path: str = Field(
        ...,
        min_length=1,
        description="Path or relative URL on the selected transport",
    )
    headers: dict[str, Any] | None = Field(
        default=None,
        description="Request headers; values may be literals, `{ref}`, `{template}`, or `{function}`",
    )
    body: Any | None = Field(default=None, description="Request body; type depends on transport/encoding")


class PostAuthOutput(StrictModel):
    """Durable post-auth output produced after authentication. Spec: §Post-Auth Outputs.

    The post-auth output field set is closed:
    `mode`, `storage`, `type`, `format`, `ui`, `options_request`,
    `options_path`, `discovery_request`, `value_path`, `label_path`. `source`,
    `phase`, `required`, and `secret` are explicitly NOT valid post-auth
    output fields — `source`/`phase` are inherent to the enclosing
    `post_auth_outputs` map; activation enforcement lives on
    `required_for_activation`; secrecy is determined by `storage`.
    """

    mode: PostAuthOutputMode = Field(
        ..., description="Closed enum: `user_selection` or `auto_discovery`."
    )
    storage: PostAuthOutputStorage = Field(
        ...,
        description=(
            "Closed enum. Must be `connection.selections` for `user_selection`, "
            "or `connection.discovered`/`secrets` for `auto_discovery`."
        ),
    )
    type: Literal["string", "integer", "number", "boolean", "array", "object"] = Field(
        ...,
        description=(
            "JSON value type used for validation and coercion. Spec: "
            "§Post-Auth Outputs — same closed vocabulary as input `type`."
        ),
    )
    format: str | None = Field(default=None, description="Optional format constraint such as `uri`.")
    ui: ConnectionContractInputUI | None = Field(default=None, description="UI rendering hints")
    options_request: PostAuthOperationRequest | None = Field(
        default=None,
        description=(
            "Request that returns selectable options. Required for "
            "`user_selection`; forbidden for `auto_discovery`."
        ),
    )
    options_path: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Response body path to the option array. Defaults to the response "
            "body root. Optional for `user_selection`; forbidden for "
            "`auto_discovery`."
        ),
    )
    discovery_request: PostAuthOperationRequest | None = Field(
        default=None,
        description=(
            "Request that returns the value to auto-discover. Required for "
            "`auto_discovery`; forbidden for `user_selection`."
        ),
    )
    value_path: str = Field(
        ...,
        min_length=1,
        description="Response path used to extract the stored value",
    )
    label_path: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Response path used as the option label. Optional for "
            "`user_selection`; forbidden for `auto_discovery`."
        ),
    )

    @model_validator(mode="after")
    def _mode_consistency(self) -> "PostAuthOutput":
        if self.mode is PostAuthOutputMode.USER_SELECTION:
            if self.options_request is None:
                raise ValueError(
                    "mode='user_selection' requires `options_request` "
                    "(spec: §Post-Auth Outputs)"
                )
            if self.discovery_request is not None:
                raise ValueError(
                    "mode='user_selection' must omit `discovery_request` "
                    "(spec: §Post-Auth Outputs)"
                )
            if self.storage != "connection.selections":
                raise ValueError(
                    "mode='user_selection' requires storage='connection.selections' "
                    "(spec: §Post-Auth Outputs)"
                )
        else:
            if self.discovery_request is None:
                raise ValueError(
                    "mode='auto_discovery' requires `discovery_request` "
                    "(spec: §Post-Auth Outputs)"
                )
            for forbidden, name in (
                (self.options_request, "options_request"),
                (self.options_path, "options_path"),
                (self.label_path, "label_path"),
            ):
                if forbidden is not None:
                    raise ValueError(
                        f"mode='auto_discovery' must omit `{name}` "
                        "(spec: §Post-Auth Outputs)"
                    )
            if self.storage not in ("connection.discovered", "secrets"):
                raise ValueError(
                    "mode='auto_discovery' requires storage='connection.discovered' "
                    "or 'secrets' (spec: §Post-Auth Outputs)"
                )
        return self


class ConnectionContract(StrictModel):
    """Connector-level contract for what a saved connection must contribute.

    Source of truth for connection form rendering, save-time validation, drift
    detection, and template reference validation. Spec: §Connection Contract.

    No standalone `version` field — drift detection rides on `connector_version`
    semver: patch = no shape change, minor = additive shape change, major =
    breaking shape change.
    """

    inputs: dict[str, ConnectionContractInput] = Field(
        default_factory=dict,
        description="Declared submitted/provisioned inputs keyed by their runtime `name`",
    )
    post_auth_outputs: dict[str, PostAuthOutput] = Field(
        default_factory=dict,
        description="Declared post-auth outputs keyed by their runtime `name`",
    )
    required_for_activation: list[str] = Field(
        default_factory=list,
        description=(
            "Runtime reference paths that must resolve before the connection can "
            "be marked active (e.g. 'connection.parameters.host', 'secrets.password')"
        ),
    )
    validation: ConnectionContractValidation | None = Field(
        default=None,
        description="Cross-input validation block; per-input rules belong on the input",
    )


# --- Resource Discovery (spec: §Resource Discovery) ---


class ResourceDiscoveryImplementation(StrictModel):
    """Discovery strategy implementation source. Spec: §Resource Discovery."""


    type: Literal["builtin", "connector_plugin"] = Field(..., description="Implementation kind")
    entrypoint: str | None = Field(
        default=None,
        description=(
            "Plugin entrypoint string (e.g. `analitiq_acme.discovery:AcmeCatalogDiscovery`). "
            "Required when type=connector_plugin; must be omitted when type=builtin."
        ),
    )

    @model_validator(mode="after")
    def _entrypoint_matches_type(self) -> "ResourceDiscoveryImplementation":
        if self.type == "connector_plugin" and not self.entrypoint:
            raise ValueError(
                "type='connector_plugin' requires `entrypoint` (spec: §Resource Discovery)"
            )
        if self.type == "builtin" and self.entrypoint is not None:
            raise ValueError(
                "type='builtin' must not declare `entrypoint` (spec: §Resource Discovery)"
            )
        return self


class ResourceDiscoveryTriggers(StrictModel):
    """When list/describe discovery actions run."""


    list_resources: Literal[
        "on_activation",
        "on_connection_selected",
        "on_resource_selected",
        "on_demand",
        "scheduled",
    ] | None = Field(default=None, description="Trigger for the list-resources action")
    describe_resource: Literal[
        "on_activation",
        "on_connection_selected",
        "on_resource_selected",
        "on_demand",
        "scheduled",
    ] | None = Field(default=None, description="Trigger for the describe-resource action")


class ResourceDiscovery(StrictModel):
    """Declarative resource discovery for connection-scoped private endpoints.

    Spec: §Resource Discovery. Produces connection-scoped endpoints and type
    maps under `connection.endpoints` / `connection.type_map`.
    """


    transport_ref: str | None = Field(
        default=None,
        description="Named transport used for discovery; defaults to `default_transport`",
    )
    strategy: str = Field(..., description="Registered discovery strategy ID")
    implementation: ResourceDiscoveryImplementation | None = Field(
        default=None,
        description="Strategy implementation source; omit to use a builtin strategy",
    )
    triggers: ResourceDiscoveryTriggers | None = Field(
        default=None,
        description="When list/describe discovery actions run",
    )
    produces: list[Literal["connection.endpoints", "connection.type_map"]] = Field(
        default_factory=list,
        description="Artifacts written by discovery",
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description="Strategy-specific declarative options (e.g. exclude_schemas)",
    )


# --- Function Expressions ---


class BasicAuthDerivedInput(StrictModel):
    """Input shape for `basic_auth` function expression."""

    username: Any = Field(..., description="Username value-expression (`{ref}`, `{template}`, or literal)")
    password: Any = Field(..., description="Password value-expression (`{ref}`, `{template}`, or literal)")


class BasicAuthDerived(StrictModel):
    """`basic_auth` — build a Basic auth credential from username/password."""

    function: Literal["basic_auth"] = Field(description="Function discriminator")
    input: BasicAuthDerivedInput = Field(..., description="Username/password value-expressions")


class Base64EncodeDerived(StrictModel):
    """`base64_encode` — base64-encode a string or bytes value."""

    function: Literal["base64_encode"] = Field(description="Function discriminator")
    input: Any = Field(..., description="Scalar value-expression to encode")


class LookupDerived(StrictModel):
    """`lookup` — map an input value through a connector-declared inline map."""

    function: Literal["lookup"] = Field(description="Function discriminator")
    input: Any = Field(..., description="Scalar value-expression (typically `{ref: connection.parameters.<X>}`)")
    map: dict[str, Any] = Field(
        ...,
        description="Inline value map: input-value → mapped-output JSON value",
    )


class UrlEncodeDerived(StrictModel):
    """`url_encode` — percent-encode a scalar for use inside a URL component."""

    function: Literal["url_encode"] = Field(description="Function discriminator")
    input: Any = Field(..., description="Scalar value-expression to encode")
    safe: str | None = Field(
        default=None,
        description="Characters to leave unencoded (default empty string — encode everything)",
    )


# `pkce_challenge_s256` and `jwt_sign` are `planned` in the callable-function
# catalog: connectors must not reference them yet. Add their Pydantic shapes
# when the engine ships them; until then they intentionally have no model.
#
# Having no model rejects one only where this union is the annotation, which is
# `base_url` by way of `UrlValueExpression`: naming an unmodelled function there
# fails `model_validate`. Every other site a function expression reaches is
# loosely typed — a transport header, a param `default`, a request body — so the
# union never grades it and an unregistered name is accepted, then fails when
# the engine resolves it at connect. RULE-SHRD-007 is that gap. It carries no
# validator because what it requires is membership in the ENGINE's registry,
# which nothing here can read; this union's members coincide with that registry
# by maintenance, not by construction.


DerivedValue = Annotated[
    Union[
        Annotated[BasicAuthDerived, UnionTag("basic_auth")],
        Annotated[Base64EncodeDerived, UnionTag("base64_encode")],
        Annotated[LookupDerived, UnionTag("lookup")],
        Annotated[UrlEncodeDerived, UnionTag("url_encode")],
    ],
    Discriminator("function"),
]
"""Resolution-time function expression, discriminated by `function`.

Per-function input shapes are enforced at the model level — connectors that
reference a function must use that function's required input shape, and `map`
is exclusive to `lookup`.
"""


# --- String-valued value expressions (spec: §Value Expressions) ---
#
# A field that must resolve to a non-empty URL string models its
# value-expression object forms as these typed models — NOT a bare
# `dict[str, Any]` — so the PUBLISHED JSON Schema constrains the shape exactly
# as the Pydantic model does.
#
# The SHAPE is expressible as schema and stays aligned by construction. The
# resolution-scope vocabulary is expressible for a `ref`, which carries it as a
# published pattern, and not for a template, where it is a property of each
# `${...}` the string contains rather than of the string: asserting it needs a
# negative lookahead, which pydantic-core's regex engine rejects, so it would
# publish a pattern the model could not run. `_expressions_qualified` is
# therefore the complete gate for a template — the validator, not `latest.json`.


class TemplateExpression(ValueExpressionScopes, StrictModel):
    """`{template}` form: a `${scope.path}`-bearing string resolved at runtime."""

    # The wrapper says this string IS an expression, so it carries the rule on
    # its own rather than only where a field of this type is checked.
    EXPRESSION_FIELDS: ClassVar[tuple[str, ...]] = ("template",)

    template: Annotated[str, StringConstraints(min_length=1)] = Field(
        description="Template string carrying `${scope.path}` placeholders."
    )


class RefExpression(StrictModel):
    """`{ref}` form: a dotted path into the resolution context."""

    ref: Annotated[
        str, StringConstraints(min_length=1, pattern=RESOLUTION_SCOPE_PATTERN)
    ] = Field(
        description=(
            "Dotted reference path beginning with a resolution scope: "
            + ", ".join(RESOLUTION_SCOPES)
            + " (e.g. `connection.parameters.host`)."
        ),
    )


class LiteralStringExpression(StrictModel):
    """`{literal}` form constrained to a non-empty string — the shape a
    URL-valued field accepts (a general `{literal}` may wrap any value, but a
    URL must be a string)."""

    literal: Annotated[str, StringConstraints(min_length=1)] = Field(
        description="A verbatim, non-empty string."
    )


UrlValueExpression = Union[
    Annotated[str, StringConstraints(min_length=1)],
    TemplateExpression,
    RefExpression,
    LiteralStringExpression,
    DerivedValue,
]
"""A value expression that resolves to a non-empty URL string: a literal string
or one of the typed object forms above. Used where a bare `dict[str, Any]`
would leave the published schema unconstrained."""


# --- Transport Contracts (spec: §Transport Contracts) ---


class TransportRateLimit(StrictModel):
    """Rate limit declaration for a transport. Spec: §Transport Contracts."""


    max_requests: StrictPositiveInt = Field(..., description="Maximum requests allowed per window")
    time_window_seconds: Any = Field(..., description="Window length in seconds (int or value-expression)")


class HttpTransport(ValueExpressionScopes, HeaderMergeRules, StrictModel):
    """HTTP transport contract. Spec: §Transport Contracts."""

    transport_type: Literal["http"] = Field(description="Transport type discriminator")
    base_url: "UrlValueExpression | None" = Field(
        default=None,
        description=(
            "Base URL: a non-empty literal string, or a value-expression "
            "(`{template}`/`{ref}`/`{literal}`/function) resolving to one at "
            "connection-materialization time (e.g. a per-tenant host taken from "
            "`connection.parameters` or discovered post-auth via "
            "`connection.discovered`). May be omitted when this entry exists "
            "only to extend `transport_defaults`."
        ),
    )
    headers: dict[str, Any] | None = Field(
        default=None,
        description="Default request headers; values may be literals or expressions",
    )

    headers_remove: list[str] | None = Field(
        default=None,
        description="Header names to delete from inherited defaults (case-insensitive)",
    )
    timeout_seconds: StrictPositiveInt | None = Field(
        default=None,
        description="Request timeout in seconds",
    )
    rate_limit: "TransportRateLimit | None" = Field(
        default=None, description="Rate-limit policy"
    )

    # Both resolved at transport materialization, by the engine's transport
    # factory and by the auth Lambdas' header merge. `timeout_seconds` and
    # `rate_limit` sit beside them and are read literally by both.
    EXPRESSION_FIELDS: ClassVar[tuple[str, ...]] = ("base_url",)
    EXPRESSION_MAPS: ClassVar[tuple[str, ...]] = ("headers",)


class DsnBinding(ValueExpressionScopes, StrictModel):
    """Single binding entry inside a `url_template` DSN. Spec: §Transport Contracts."""

    # Resolved when the engine renders the DSN from its template.
    EXPRESSION_FIELDS: ClassVar[tuple[str, ...]] = ("value",)

    value: Any = Field(..., description="Value-expression resolving to the raw binding value")
    encoding: Literal[
        "raw", "host", "url_userinfo", "url_path_segment", "url_query_key", "url_query_value"
    ] = Field(..., description="Generic encoding applied before substitution into the template")


class UrlTemplateDsn(StrictModel):
    """Connector-authored URL template DSN with structured bindings."""

    kind: Literal["url_template"] = Field(description="DSN kind")
    template: str = Field(
        ...,
        min_length=1,
        description="Connector-authored URL template containing `{binding}` placeholders.",
        # `{binding}` placeholders are the DSN grammar; the value-expression
        # `${...}` template syntax is not permitted in a DSN URL template. The
        # ECMA negative-lookahead pattern lives in json_schema_extra ONLY —
        # pydantic-core's rust regex rejects lookahead, so it cannot be a
        # `pattern=` StringConstraint; `_reject_template_expressions` is the
        # runtime mirror.
        json_schema_extra={"pattern": r"^(?![\s\S]*\$\{)[\s\S]*$"},
    )
    bindings: dict[str, DsnBinding] = Field(
        ...,
        min_length=1,
        description="Map keyed by placeholder name; each binding declares value + encoding.",
    )

    @field_validator("template")
    @classmethod
    def _reject_template_expressions(cls, v: str) -> str:
        if "${" in v:
            raise ValueError(
                "DSN template must not contain ${...} value-expression syntax; "
                "use {binding} placeholders declared in `bindings`"
            )
        return v

    @model_validator(mode="after")
    def _validate_placeholder_bindings(self) -> "UrlTemplateDsn":
        # Every `{placeholder}` in the template must resolve to a binding, and
        # every binding must be referenced by the template. `${...}` is already
        # rejected by `_reject_template_expressions`, so a bare `{name}` is
        # unambiguous. Spec: §Transport Contracts — DSN url_template.
        placeholders = set(re.findall(r"\{([^{}]+)\}", self.template))
        binding_keys = set(self.bindings)
        missing = placeholders - binding_keys
        if missing:
            raise ValueError(
                f"url_template references placeholder(s) {sorted(missing)} with "
                "no matching entry in `bindings`"
            )
        unused = binding_keys - placeholders
        if unused:
            raise ValueError(
                f"url_template declares binding(s) {sorted(unused)} not "
                "referenced by the template"
            )
        return self


class DatabaseTls(ValueExpressionScopes, StrictModel):
    """Database transport TLS declaration. Spec: §Transport Contracts.

    Both fields resolve to plain strings at runtime. The interpretation of
    those strings (libpq vocabulary, MySQL vocabulary, etc.) is owned by the
    connector package's dialect — the schema is vocabulary-agnostic. No
    canonical mode set is enforced here; the connector's
    ``connection_contract.inputs[<field>].enum`` is the user-facing constraint.
    """

    EXPRESSION_FIELDS: ClassVar[tuple[str, ...]] = ("mode", "ca_certificate")

    mode: Any = Field(
        ...,
        description=(
            "TLS mode value-expression. Resolves to a plain string at runtime; "
            "interpretation (e.g. libpq `verify-full` vs MySQL `REQUIRED`) is "
            "owned by the connector package's dialect."
        ),
    )
    ca_certificate: Any | None = Field(
        default=None,
        description=(
            "CA certificate value-expression. Resolves to a plain string at "
            "runtime; required when `mode` implies certificate verification."
        ),
    )


class SqlAlchemyTransport(StrictModel):
    """SQLAlchemy database transport contract. Spec: §Transport Contracts."""

    transport_type: Literal["sqlalchemy"] = Field(description="Transport type discriminator")
    driver: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*\+[a-z][a-z0-9_]*$",
        description=(
            "SQLAlchemy driver in `dialect+driver` form "
            "(e.g. `postgresql+asyncpg`, `mysql+aiomysql`, "
            "`redshift+redshift_connector`). May name a sync or an async "
            "DBAPI — no driver allow-list is imposed. The named driver must "
            "be a real SQLAlchemy dialect registration; that is checked at "
            "transport build, not here. Optional — SQLAlchemy can derive "
            "the driver from the DSN scheme — but declare it so the "
            "sync/async choice is explicit to a reader."
        ),
    )
    dsn: UrlTemplateDsn | None = Field(
        default=None,
        description="Structured URL-template DSN with bindings and encodings.",
    )
    tls: DatabaseTls | None = Field(
        default=None,
        description="Generic TLS declaration; runtime materializes driver-specific args.",
    )
    options: dict[str, Any] | None = Field(
        default=None,
        description="Engine options (pool_size, isolation_level, etc.)",
    )


class AdbcTransport(ValueExpressionScopes, StrictModel):
    """ADBC (Arrow Database Connectivity) database transport contract. Spec: §Transport Contracts."""

    model_config = ConfigDict(
        extra="forbid",
        # Surface the `_require_dsn_or_kwargs` model-validator constraint
        # in the published JSON Schema too, so external consumers
        # validating against the JSON Schema alone (FE, connector-author
        # tooling, third-party validators) catch the empty-transport case
        # before it reaches Pydantic runtime.
        #
        # JSON Schema `required` only checks property existence — it
        # accepts `{"dsn": null}`. Mirror Pydantic's `_require_dsn_or_kwargs`
        # by also asserting the present branch is not null (and, for
        # `db_kwargs`, not empty), so a schema-only validator rejects the
        # same payloads Pydantic does instead of letting them slip
        # through and explode at the backend.
        json_schema_extra={
            "anyOf": [
                {
                    "required": ["dsn"],
                    "properties": {"dsn": {"not": {"type": "null"}}},
                },
                {
                    "required": ["db_kwargs"],
                    "properties": {
                        "db_kwargs": {
                            "not": {"type": "null"},
                            "minProperties": 1,
                        },
                    },
                },
            ],
        },
    )

    transport_type: Literal["adbc"] = Field(description="Transport type discriminator")
    # Resolved per entry by the engine's transport factory, unlike the
    # SQLAlchemy `options` map beside it, which is read literally.
    EXPRESSION_MAPS: ClassVar[tuple[str, ...]] = ("db_kwargs",)

    driver: Literal["postgresql", "snowflake", "bigquery"] = Field(
        ...,
        description=(
            "ADBC driver family identifier. Closed to the drivers the engine "
            "actually ships. Required at the connector layer "
            "(unlike `SqlAlchemyTransport.driver`); the ADBC dispatcher "
            "selects the matching driver and cannot "
            "fall back. Not to be confused with the format-dialect inputs "
            "on file/s3 connectors."
        ),
    )
    dsn: UrlTemplateDsn | None = Field(
        default=None,
        description=(
            "Structured URL-template DSN with bindings and encodings. "
            "Connector-authored layout; the pipeline runtime renders and "
            "substitutes bindings before invoking the ADBC driver. "
            "Note: schema introspection takes a separate path — it rebuilds "
            "the URI from canonical credential fields and does NOT consume "
            "the connector-authored DSN template (symmetric to the SQLAlchemy "
            "introspection path). Optional individually, but `AdbcTransport` "
            "requires at least one of `dsn` / `db_kwargs`; ADBC drivers "
            "that accept all connection state via `db_kwargs` (e.g. "
            "Snowflake) may omit `dsn`."
        ),
    )
    db_kwargs: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Driver-specific keyword arguments passed to the ADBC driver on "
            "connect (e.g. Snowflake account/warehouse, Postgres options). "
            "Values may be literals or value expressions (`{\"ref\": \"...\"}`, "
            "`{\"template\": \"...\"}`, `{\"function\": \"...\"}`); the "
            "pipeline runtime resolves them before invoking the driver. "
            "Note: schema introspection (`list_schemas`, `test_credentials`, "
            "…) takes a separate path — it constructs driver kwargs from "
            "canonical credential fields (`account`, `warehouse`, `username`, "
            "…) and does NOT consume connector-authored `db_kwargs`. This "
            "matches the SQLAlchemy introspection path, which ignores the "
            "connector DSN template and rebuilds the URI from credential fields. "
            "Optional individually, but `AdbcTransport` requires at least "
            "one of `dsn` / `db_kwargs`."
        ),
    )

    @model_validator(mode="after")
    def _require_dsn_or_kwargs(self) -> "AdbcTransport":
        if self.dsn is None and not self.db_kwargs:
            raise ValueError(
                "AdbcTransport requires at least one of `dsn` or `db_kwargs` "
                "— the transport must carry some connection state."
            )
        return self


class S3CredentialsBlock(StrictModel):
    """Static S3 credentials block (access key + secret access key). Spec: §Transport Contracts."""

    access_key_id: str = Field(..., min_length=1, description="Access key id")
    secret_access_key: Any = Field(..., description="Secret access key value-expression")
    session_token: Any | None = Field(default=None, description="Optional session token value-expression")


class S3Transport(StrictModel):
    """S3 transport contract."""

    transport_type: Literal["s3"] = Field(description="Transport type discriminator")
    bucket: str = Field(..., min_length=1, description="Bucket name")
    region: Any = Field(..., description="Region value-expression")
    prefix: Any | None = Field(
        default=None,
        description=(
            "Object key prefix or template value-expression. May embed "
            "`{stream_alias}` / `{run_id}` / `{date}` placeholders."
        ),
    )
    format: Any = Field(
        ...,
        description=(
            "Output format value-expression. Closed value-level enum: "
            "`csv` | `jsonl`."
        ),
    )
    dialect: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Format-specific dialect options as a value-expression bundle. "
            "Schema is `format`-dependent — see spec §Output Format."
        ),
    )
    credentials: S3CredentialsBlock = Field(
        ...,
        description="Static AWS credentials; required for the v1 contract.",
    )


class FileTransport(StrictModel):
    """Filesystem transport contract."""

    transport_type: Literal["file"] = Field(description="Transport type discriminator")
    path: str = Field(
        ...,
        min_length=1,
        description=(
            "Target output path. May embed `{stream_alias}` / `{run_id}` / "
            "`{date}` placeholders."
        ),
    )
    format: Any = Field(
        ...,
        description=(
            "Output format value-expression. Closed value-level enum: "
            "`csv` | `jsonl`."
        ),
    )
    dialect: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Format-specific dialect options as a value-expression bundle. "
            "Schema is `format`-dependent — see spec §Output Format."
        ),
    )


class StdoutTransport(StrictModel):
    """Stdout transport contract."""

    transport_type: Literal["stdout"] = Field(description="Transport type discriminator")
    format: Any | None = Field(
        default=None,
        description=(
            "Output format value-expression. Closed value-level enum: "
            "`csv` | `jsonl`. Default `jsonl`."
        ),
    )


Transport = Annotated[
    Union[
        Annotated[HttpTransport, UnionTag("http")],
        Annotated[SqlAlchemyTransport, UnionTag("sqlalchemy")],
        Annotated[AdbcTransport, UnionTag("adbc")],
        Annotated[S3Transport, UnionTag("s3")],
        Annotated[FileTransport, UnionTag("file")],
        Annotated[StdoutTransport, UnionTag("stdout")],
    ],
    Discriminator("transport_type"),
]
"""Named transport contract entry. Spec: §Transport Contracts."""


class TransportDefaults(ValueExpressionScopes, HeaderMergeRules, StrictModel):
    """Defaults merged into every entry of `transports`. Spec: §Transport Contracts."""

    # Merge layer one: this map is folded into every transport entry before any
    # of it resolves, so an unscoped token here reaches every request the
    # connector makes. `options` beside it is driver configuration, read
    # literally.
    EXPRESSION_MAPS: ClassVar[tuple[str, ...]] = ("headers",)

    transport_type: Literal["http", "sqlalchemy", "adbc", "s3", "file", "stdout"] | None = Field(
        default=None,
        description=(
            "Default transport type inherited by every entry of `transports` that "
            "does not declare its own."
        ),
    )
    headers: dict[str, Any] | None = Field(
        default=None,
        description="Default request headers shared across HTTP transport entries.",
    )
    headers_remove: list[str] | None = Field(
        default=None,
        description="Header names to delete from any inherited defaults",
    )
    timeout_seconds: StrictPositiveInt | None = Field(
        default=None,
        description="Default request timeout in seconds",
    )
    rate_limit: "TransportRateLimit | None" = Field(
        default=None, description="Default rate-limit policy"
    )
    options: dict[str, Any] | None = Field(default=None, description="Default driver/engine options")


# --- Connector Models ---


# file/s3/stdout connectors carry no post-auth workflow, so their
# `connection_contract` must declare neither `post_auth_outputs` nor
# `required_for_activation` (spec-normative; previously unenforced). The schema
# fragment pins both to empty for schema-only consumers;
# `_reject_post_auth_contract` is the runtime half.
_FILE_LIKE_CONNECTION_CONTRACT_RULES: dict[str, Any] = {
    "allOf": [
        {
            "properties": {
                "connection_contract": {
                    "properties": {
                        "post_auth_outputs": {"maxProperties": 0},
                        "required_for_activation": {"maxItems": 0},
                    }
                }
            }
        }
    ]
}


def _reject_post_auth_contract(contract: "ConnectionContract", kind: str) -> None:
    if contract.post_auth_outputs:
        raise ValueError(
            f"{kind} connectors must not declare "
            "connection_contract.post_auth_outputs"
        )
    if contract.required_for_activation:
        raise ValueError(
            f"{kind} connectors must not declare "
            "connection_contract.required_for_activation"
        )


# --- Declared capabilities: SQL write path, write unit, capability block v2
# (engine ADR §5) ---
#
# `sql_capabilities` — SQL-shape capabilities are DECLARED, not guessed. The
# engine's SQL write path ("refuse, don't guess", settled in the SQL write path
# v2 ADR that analitiq-core owns) reads these facts from the connector
# definition and refuses any needed-but-undeclared fact at config/handshake
# time, instead of probing the live database. A declared block is COMPLETE —
# every top-level shape fact is required — because a partial declaration is a
# config error, not a request for implicit defaults.
# (`stage.dedicated_schema` is the one conditional field: required iff
# `stage.schema == "dedicated"`.)
#
# `write_unit` — a connector-level, non-SQL coalescing PREFERENCE (not a
# refuse-don't-guess fact): a destination declares the batch size it wants, and
# absence simply means "no preference". At least one of `rows`/`bytes` is
# required so a declared block is never an empty no-op.
#
# Both blocks are OPTIONAL at the schema level; omission is legal.
#
# Capability block v2 adds three ADDITIVE declarations: `error_map` and
# `concurrency` (connector-level) and `sql_capabilities.limits`. Additive
# means no refusal ever hinges on them being present — absence (of a block, a
# family, or a single cap) is legal and means "no declared mapping / no
# declared cap"; current engine behavior applies. That contrasts with the
# shape facts above (a missing `merge_form` blocks an upsert) and with
# `write_unit`'s at-least-one-bound rule: here an EMPTY block (`{}`) is legal
# and equivalent to omission. Declared content is still validated fail-loud:
# an off-vocabulary category, a malformed identifier, or an unknown field is a
# config error. Connectors declare DRIVER FACTS only; the engine alone derives
# verdicts (ack status, failure category, error code) — these models must never
# grow verdict-shaped fields.


# Closed failure-category vocabulary (capability block v2), mirrored by the
# engine's typed parser (`cdk/declarations.py`). The schema rendered from this
# Literal is the published contract; a vocabulary change is a coordinated
# engine + contract revision, never a local edit.
ErrorCategory = Literal[
    "transient", "config", "auth", "unreachable", "rate_limited", "write_rejected"
]

# Per-family identifier grammars, mirrored from the engine parser: a 2-char
# SQLSTATE class or full 5-char state (uppercase alphanumeric only); a Python
# exception class name; a signed integer vendor code; a 3-digit HTTP status
# (100-599 — string-typed on the wire, like every JSON object key).
_SQLSTATE_KEY_PATTERN = r"^[0-9A-Z]{2}([0-9A-Z]{3})?$"
_EXCEPTION_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_VENDOR_CODE_KEY_PATTERN = r"^-?[0-9]+$"
_HTTP_STATUS_KEY_PATTERN = r"^[1-5][0-9]{2}$"

# Pydantic renders a patterned-key dict as `patternProperties` alone, under
# which a JSON-Schema-only consumer would ACCEPT the off-grammar keys the model
# rejects (patternProperties constrains matching keys; non-matching keys fall
# through to an unset additionalProperties). This callable closes two gaps so
# schema and model agree — the same schema-parity discipline as the
# `json_schema_extra` mirrors above:
#
# 1. Inject `additionalProperties: false` as a sibling, so off-grammar keys
#    are rejected rather than falling through.
# 2. Publish the key patterns with a true-end assertion `(?![\s\S])` in place
#    of the trailing `$`. Python-`re`-based schema validators (`jsonschema`)
#    let `$` match before a trailing newline, admitting keys like `"429\n"`
#    that pydantic-core's Rust regex (end-of-haystack `$`) and conformant
#    ECMA validators reject; the lookahead is true-end in BOTH regex
#    dialects. Lookahead cannot live in the StringConstraints pattern —
#    pydantic-core's Rust regex rejects it — so, as with the DSN
#    `url_template` pattern above, the ECMA-safe form goes in the published
#    schema only and the Rust `$` (already true-end) is the runtime mirror.
#
# The four aliases stay explicit (not factory-built) so static type checkers
# keep covering the fields; the callable is shared so both invariants are
# stated once.
def _closed_true_end_keys(schema: dict[str, Any]) -> None:
    pattern_props = schema.pop("patternProperties", None)
    if pattern_props:
        schema["patternProperties"] = {
            (key[:-1] + r"(?![\s\S])" if key.endswith("$") else key): value
            for key, value in pattern_props.items()
        }
    schema["additionalProperties"] = False
_SqlstateFamily = Annotated[
    dict[Annotated[str, StringConstraints(pattern=_SQLSTATE_KEY_PATTERN)], ErrorCategory],
    Field(json_schema_extra=_closed_true_end_keys),
]
_ExceptionFamily = Annotated[
    dict[Annotated[str, StringConstraints(pattern=_EXCEPTION_KEY_PATTERN)], ErrorCategory],
    Field(json_schema_extra=_closed_true_end_keys),
]
_VendorCodeFamily = Annotated[
    dict[Annotated[str, StringConstraints(pattern=_VENDOR_CODE_KEY_PATTERN)], ErrorCategory],
    Field(json_schema_extra=_closed_true_end_keys),
]
_HttpStatusFamily = Annotated[
    dict[Annotated[str, StringConstraints(pattern=_HTTP_STATUS_KEY_PATTERN)], ErrorCategory],
    Field(json_schema_extra=_closed_true_end_keys),
]


class ErrorMap(StrictModel):
    """Driver-fact error classification map.

    Maps driver-reported identifiers onto the engine's closed failure-category
    vocabulary, one map per identifier family. A subset of families (including
    none — an empty block declares nothing) is legal, and so is an empty family
    map. Additive: absence never blocks anything. Connectors declare driver
    facts only; the engine alone derives verdicts (ack status, failure
    category, error code) from them.
    """

    sqlstate: _SqlstateFamily | None = Field(
        default=None,
        description=(
            "SQLSTATE → failure category. Keys are a 2-char SQLSTATE class "
            "(e.g. `08`) or a full 5-char state (e.g. `28000`), uppercase "
            "alphanumeric."
        ),
    )
    exception: _ExceptionFamily | None = Field(
        default=None,
        description=(
            "Python exception class name → failure category "
            "(e.g. `OperationalError`)."
        ),
    )
    vendor_code: _VendorCodeFamily | None = Field(
        default=None,
        description=(
            "Vendor error code → failure category. Keys are signed integers "
            "in string form (e.g. `1045`, `-803`)."
        ),
    )
    http: _HttpStatusFamily | None = Field(
        default=None,
        description=(
            "HTTP status → failure category. Keys are 3-digit statuses "
            "100-599 in string form (e.g. `429`)."
        ),
    )


class Concurrency(StrictModel):
    """Connector-level concurrency declaration.

    Additive: absence of the block or of `max_connections` — including an
    empty block — means "no declared cap" and current engine behavior applies.
    A declared cap is validated strictly (positive integer, booleans rejected).
    """

    max_connections: StrictPositiveInt | None = Field(
        default=None,
        description=(
            "Maximum concurrent connections the engine may open to the target "
            "system (integer ≥ 1). Absent means no declared cap."
        ),
    )


class SqlLimits(StrictModel):
    """Declared SQL driver caps.

    The one additive member of `sql_capabilities`: unlike the required
    shape facts, absence of the block or of any single cap — including an
    empty block — is legal and means "no declared cap", and absence never
    blocks a write. Declared values are validated strictly (positive
    integers, booleans rejected) and are enforced by the engine.
    """

    max_bind_params: StrictPositiveInt | None = Field(
        default=None,
        description=(
            "Maximum bind parameters per statement the driver accepts "
            "(integer ≥ 1), e.g. 2100 for SQL Server. Absent means no "
            "declared cap."
        ),
    )
    max_identifier_len: StrictPositiveInt | None = Field(
        default=None,
        description=(
            "Maximum SQL identifier length in bytes (integer ≥ 1), e.g. 63 "
            "for Postgres (NAMEDATALEN − 1). Absent means no declared cap."
        ),
    )


class SqlStageCapabilities(StrictModel):
    """Staging-relation capabilities for a SQL destination's write path (ADR §5).

    Describes how the engine may materialize an intermediate stage relation
    before merging into the target. `scope`, `schema`, and `transactional_ddl`
    are always required; `dedicated_schema` is required iff `schema` is
    `dedicated`, and must be omitted or null otherwise
    (`_dedicated_schema_matches_scope`).
    """

    model_config = ConfigDict(
        extra="forbid",
        # Mirror `_dedicated_schema_matches_scope` for JSON-Schema-only
        # consumers (FE, third-party validators): a non-empty `dedicated_schema`
        # is present iff `schema == "dedicated"`. Uses the same in-model
        # `json_schema_extra` + `oneOf` technique as `ConnectionConditionPredicate`
        # (`_PREDICATE_EXACTLY_ONE_OPERATOR`). A `target` stage may omit the key
        # or send null, never a real name — so the target branch is null-aware.
        # Keys are wire names (`schema`, the alias), not the Python `schema_`.
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "schema": {"const": "dedicated"},
                        "dedicated_schema": {"type": "string", "minLength": 1},
                    },
                    "required": ["dedicated_schema"],
                },
                {
                    "properties": {"schema": {"const": "target"}},
                    "anyOf": [
                        {"not": {"required": ["dedicated_schema"]}},
                        {"properties": {"dedicated_schema": {"type": "null"}}},
                    ],
                },
            ]
        },
    )

    scope: Literal["temp", "real"] = Field(
        ...,
        description=(
            "Stage-relation scope: `temp` for a session/transaction-scoped "
            "temporary table, `real` for an ordinary persistent table the "
            "engine creates and drops around the write."
        ),
    )
    schema_: Literal["target", "dedicated"] = Field(
        ...,
        alias="schema",
        description=(
            "Where stage relations live: `target` co-locates them in the "
            "target's own schema; `dedicated` places them in a separate schema "
            "named by `dedicated_schema`."
        ),
    )
    dedicated_schema: str | None = Field(
        default=None,
        min_length=1,
        pattern=NO_EDGE_WHITESPACE_PATTERN,
        description=(
            "Name of the dedicated staging schema — the engine emits it as a SQL "
            "identifier, so it must be a non-blank name with no leading/trailing "
            "whitespace. Required when `schema` is `dedicated`; must be omitted "
            "or null otherwise."
        ),
    )
    transactional_ddl: bool = Field(
        ...,
        description=(
            "Whether the destination runs stage DDL (CREATE/DROP) inside the "
            "write transaction. `false` for engines that auto-commit DDL "
            "(e.g. MySQL), which forces a non-transactional staging strategy."
        ),
    )

    @model_validator(mode="after")
    def _dedicated_schema_matches_scope(self) -> "SqlStageCapabilities":
        if self.schema_ == "dedicated" and self.dedicated_schema is None:
            raise ValueError(
                "stage.schema='dedicated' requires `dedicated_schema` "
                "(engine ADR §5 — a dedicated staging schema must be named)"
            )
        if self.schema_ == "target" and self.dedicated_schema is not None:
            raise ValueError(
                "stage.schema='target' must omit `dedicated_schema` (or set it "
                "null) (engine ADR §5 — dedicated_schema is meaningful only for "
                "schema='dedicated')"
            )
        return self


# Bulk-load vocabulary, mirrored from the engine's typed parser
# (`cdk/sql/capabilities.py`: `SQL_TRANSPORT_TYPES`,
# `DIALECT_IMPLEMENTED_BULK_MECHANISMS`, `BULK_MECHANISMS_BY_TRANSPORT`). A
# bulk mechanism is a fact about a SQL transport, not the connector as a whole
# — `copy_from` needs the driver's wire connection, `adbc_ingest` an ADBC
# cursor — so `bulk_load` maps each family to its mechanism instead of one
# connector-wide value only one family could run. The dialect-implemented
# mechanisms are valid on either family; `adbc_ingest` is the ADBC backend's
# own native landing (no dialect code involved) and exists only under `adbc`,
# so the unrunnable pairing is unrepresentable at parse time rather than
# checked-for downstream.
_DialectBulkMechanism = Literal["copy_from", "load_data_local_infile", "load_job"]
_AdbcBulkMechanism = Literal[
    "adbc_ingest", "copy_from", "load_data_local_infile", "load_job"
]


# `Literal[...] | None` publishes as `anyOf: [enum, null]` with `default:
# null` — under which a JSON-Schema-only consumer would accept the explicit
# `"sqlalchemy": null` the engine parser refuses (a declared mechanism is a
# string; absence of the KEY is the only "none"). Collapse the published field
# to the bare per-family enum so schema and model refuse null identically —
# the same schema-parity discipline as `_closed_true_end_keys` above. The
# model-side mirror is `SqlBulkLoad._null_is_not_a_mechanism`. Exactly one
# non-null branch must exist: if the field annotation ever changes shape, the
# render fails loudly here instead of publishing a silently merged schema.
def _enum_branch_only(schema: dict[str, Any]) -> None:
    branches = [b for b in schema.pop("anyOf", ()) if b.get("type") != "null"]
    if len(branches) != 1:
        raise ValueError(
            f"_enum_branch_only expects exactly one non-null anyOf branch, "
            f"got {branches!r} — the field annotation changed shape"
        )
    schema.update(branches[0])
    schema.pop("default", None)


class SqlBulkLoad(StrictModel):
    """Per-transport bulk-load declaration.

    Maps a SQL transport family (`sqlalchemy` / `adbc`) to the bulk mechanism
    its connections land with. An absent family lands via executemany — the
    default, needing no declaration; there is no `"none"` member because
    absence of the key IS none — and an empty object is legal, declaring no
    bulk mechanism anywhere. An explicit `null` mechanism is refused in both
    layers (`_null_is_not_a_mechanism` / `_enum_branch_only`), and
    `adbc_ingest` exists only under `adbc`: it is the ADBC backend's own
    native landing and can never run on the SQLAlchemy transport.
    Serialization holds the same rule in the emit direction: undeclared
    families are omitted from dumps (`_undeclared_families_stay_absent`), so
    the model never emits a document it would itself refuse.
    """

    sqlalchemy: _DialectBulkMechanism | None = Field(
        default=None,
        json_schema_extra=_enum_branch_only,
        description=(
            "Bulk mechanism for connections on the SQLAlchemy transport: "
            "Postgres `copy_from` (`COPY FROM`), MySQL "
            "`load_data_local_infile` (`LOAD DATA LOCAL INFILE`), or "
            "BigQuery-style `load_job`. Omit the key to land via executemany."
        ),
    )
    adbc: _AdbcBulkMechanism | None = Field(
        default=None,
        json_schema_extra=_enum_branch_only,
        description=(
            "Bulk mechanism for connections on the ADBC transport: the ADBC "
            "backend's native `adbc_ingest`, or a dialect-implemented "
            "`copy_from` / `load_data_local_infile` / `load_job`. Omit the "
            "key to land via executemany."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _null_is_not_a_mechanism(cls, data: Any) -> Any:
        # Scans the raw mapping's VALUES, not `cls.model_fields`, so a family
        # that is ever aliased or added cannot let an explicit null slip
        # through to the null-accepting `| None` annotation. A null under an
        # unknown family reports here (naming the key) rather than as an
        # unknown-key error — both are refusals.
        if isinstance(data, dict):
            for family, mechanism in data.items():
                if mechanism is None:
                    raise ValueError(
                        f"bulk_load.{family} is null; declare a mechanism or "
                        "omit the key — an absent family is the only 'none' "
                        "(it lands via executemany)"
                    )
        return data

    @model_serializer(mode="wrap")
    def _undeclared_families_stay_absent(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        # "Absence is the only none" must hold in the emit direction too: a
        # plain `model_dump()` of `... | None` fields would emit the explicit
        # `"family": null` this very model refuses, so a consumer re-emitting
        # a parsed connector would produce a contract-invalid document unless
        # it remembered `exclude_none=True`. Drop undeclared families so
        # every dump is valid input for the model and the published schema.
        return {k: v for k, v in handler(self).items() if v is not None}


class SqlCapabilities(StrictModel):
    """SQL write-path capabilities declared by a database connector (ADR §5).

    "Refuse, don't guess": the engine reads these facts instead of probing the
    live database, and refuses at handshake time when a needed fact was not
    declared. Optional as a block, but when present every shape fact is
    required — a partial declaration is a config error. `limits` is the one
    additive member: it and each cap inside it may be omitted, meaning "no
    declared cap".
    """

    catalog: Literal["none", "read", "full"] = Field(
        ...,
        description=(
            "Catalog (multi-database) support: `none` — no catalog concept; "
            "`read` — the catalog is addressable but not creatable; `full` — "
            "the engine may create/drop catalogs."
        ),
    )
    session_targeting: Literal["per_statement", "session_default"] = Field(
        ...,
        description=(
            "How the write target schema/catalog is selected: `per_statement` "
            "— fully qualified on each statement; `session_default` — set once "
            "as a session default (e.g. `search_path` / `USE`) and inherited."
        ),
    )
    merge_form: Literal[
        "merge", "insert_on_conflict", "insert_on_duplicate_key", "none"
    ] = Field(
        ...,
        description=(
            "The upsert grammar the destination supports: SQL-standard `merge` "
            "(`MERGE`), Postgres-style `insert_on_conflict` "
            "(`INSERT … ON CONFLICT`), MySQL-style `insert_on_duplicate_key` "
            "(`INSERT … ON DUPLICATE KEY`), or `none` (no native upsert)."
        ),
    )
    bulk_load: SqlBulkLoad = Field(
        ...,
        description=(
            "Per-transport bulk-ingest declaration: "
            "maps a SQL transport family (`sqlalchemy` / `adbc`) to the bulk "
            "mechanism its connections land with. An absent family lands via "
            "executemany; an empty object declares no bulk mechanism "
            "anywhere. Required as an object — the block's all-facts-required "
            "rule — but its members are each optional."
        ),
    )
    stage: SqlStageCapabilities = Field(
        ...,
        description=(
            "Staging-relation capabilities for the merge/upsert write path."
        ),
    )
    limits: SqlLimits | None = Field(
        default=None,
        description=(
            "Declared SQL driver caps. The "
            "one additive member of this block: unlike the required "
            "shape facts, absence (of the block or any single cap) is legal "
            "and means \"no declared cap\"."
        ),
    )


class WriteUnit(StrictModel):
    """Preferred write-batch coalescing unit for a destination.

    Connector-level, not a SQL-only fact: any destination whose write cost is
    per-write-operation may declare the batch size it wants the engine's
    coalescer to target. At least one of `rows` / `bytes` must be given;
    absence of the whole block means "no coalescing preference". Consumed by
    the engine's batch coalescer.
    """

    model_config = ConfigDict(
        extra="forbid",
        # Mirror `_at_least_one_bound` for JSON-Schema-only consumers: at least
        # one of `rows` / `bytes` present AND non-null. Same shape as
        # AdbcTransport's `dsn`/`db_kwargs` rule.
        json_schema_extra={
            "anyOf": [
                {
                    "required": ["rows"],
                    "properties": {"rows": {"not": {"type": "null"}}},
                },
                {
                    "required": ["bytes"],
                    "properties": {"bytes": {"not": {"type": "null"}}},
                },
            ]
        },
    )

    rows: StrictPositiveInt | None = Field(
        default=None,
        description="Preferred number of rows per write operation (≥ 1).",
    )
    bytes: StrictPositiveInt | None = Field(
        default=None,
        description="Preferred payload size in bytes per write operation (≥ 1).",
    )

    @model_validator(mode="after")
    def _at_least_one_bound(self) -> "WriteUnit":
        if self.rows is None and self.bytes is None:
            raise ValueError(
                "write_unit requires at least one of `rows` or `bytes` "
                "(an empty write_unit expresses no preference; "
                "omit the block entirely instead)"
            )
        return self


class ConnectorBase(StrictModel):
    """Base connector model — fields shared by every connector kind.

    `connector_id` is the connector's canonical identifier and its registry
    repo name (e.g. `postgres`, `xero`, `pipedrive`).
    """

    schema_url: Annotated[
        str, StringConstraints(pattern=_CONNECTOR_SCHEMA_URL_PATTERN)
    ] | None = Field(
        default=None,
        alias="$schema",
        description=(
            "Connector schema URL (optional in API payloads, required for "
            "standalone files). Accepts the published URL on any environment "
            "host (schemas.analitiq.<tld>) so a document authored against the "
            "canonical analitiq.ai URL validates against the per-environment "
            "schema the engine fetches at runtime."
        ),
    )

    connector_id: str = Field(
        ...,
        pattern=SLUG_PATTERN,
        description=(
            "Connector's canonical identifier and registry repo name "
            "(e.g. `postgres`, `xero`, `pipedrive`). Must be lowercase "
            "alphanumeric with hyphens/underscores, starting with a letter "
            "or a digit."
        ),
    )

    display_name: str | None = Field(
        default=None,
        min_length=DISPLAY_NAME_MIN,
        max_length=DISPLAY_NAME_MAX,
        pattern=NO_EDGE_WHITESPACE_PATTERN,
        description="User-facing connector label (1-120 chars, no leading/trailing whitespace).",
    )
    description: str | None = Field(
        default=None,
        max_length=DESCRIPTION_MAX,
        description="Human-readable connector description (≤2000 chars).",
    )
    documentation_url: str | None = Field(
        default=None,
        max_length=2048,
        pattern=r"^https?://",
        description=(
            "URI string pointing at the upstream provider or spec documentation "
            "for this connector. Connector-only metadata. Must be an absolute "
            "http(s) URL of ≤2048 characters when present."
        ),
        json_schema_extra={"format": "uri"},
    )
    tags: list[TrimmedTag] | None = Field(
        default=None,
        max_length=TAGS_MAX,
        json_schema_extra={"uniqueItems": True},
        description="Grouping/search labels (max 50 unique trimmed strings of 1-64 chars).",
    )

    version: str = Field(
        ...,
        pattern=SEMVER_PATTERN,
        description=(
            "Connector release semantic version. Saved connections record this "
            "version for drift detection."
        ),
    )

    default_transport: str = Field(
        ...,
        min_length=1,
        description="Name of the entry in `transports` used when an operation omits `transport_ref`.",
    )
    transports: dict[str, "Transport"] = Field(
        ...,
        min_length=1,
        description=(
            "Named transport contracts discriminated by `transport_type` "
            "(http | sqlalchemy | adbc | s3 | file | stdout). Each entry inherits "
            "`transport_defaults` and supplies type-specific fields."
        ),
    )
    transport_defaults: TransportDefaults | None = Field(
        default=None,
        description=(
            "Defaults merged into every entry of `transports` (object-valued "
            "fields like `headers` deep-merge; scalars override per-entry)."
        ),
    )
    auth: Auth = Field(
        ...,
        description="Authentication workflow definition.",
    )
    connection_contract: "ConnectionContract" = Field(
        ...,
        description=(
            "Connector-level connection contract: declared inputs, post-auth "
            "outputs, activation requirements, and cross-input validation."
        ),
    )
    resource_discovery: ResourceDiscovery | None = Field(
        default=None,
        description="Resource discovery declarations for dynamic or post-auth resources.",
    )
    write_unit: WriteUnit | None = Field(
        default=None,
        description=(
            "Preferred write-batch coalescing unit for this destination. "
            "Connector-level because it is not a SQL-only fact: "
            "any destination whose write cost is per-write-operation may "
            "declare the batch size the engine's coalescer should target. "
            "Absent means no coalescing preference."
        ),
    )
    error_map: ErrorMap | None = Field(
        default=None,
        description=(
            "Driver-fact error classification map: "
            "per-family identifier → failure-category facts "
            "(sqlstate, exception, vendor_code, http). Connector-level "
            "because families span kinds (http for API connectors, sqlstate/"
            "vendor_code for databases). Additive — absence never blocks "
            "anything; the engine alone derives verdicts from these facts."
        ),
    )
    concurrency: Concurrency | None = Field(
        default=None,
        description=(
            "Connector-level concurrency declaration. "
            "Additive — absence means no declared cap."
        ),
    )

    @field_validator("display_name")
    @classmethod
    def _validate_display_name_field(cls, v: str | None) -> str | None:
        return validate_display_name(v)

    @field_validator("tags")
    @classmethod
    def _validate_tags_field(cls, v: list[str] | None) -> list[str] | None:
        return validate_tags(v)

    @model_validator(mode="before")
    @classmethod
    def _inherit_transport_type(cls, data: Any) -> Any:
        """Fill in `transport_type` on each `transports.<name>` entry from `transport_defaults.transport_type`.

        Spec: §Transport Contracts. The Pipedrive multi-origin example declares
        `transport_type: http` once on `transport_defaults` and omits it on
        per-entry objects; without this pre-merge the discriminated union
        dispatch would reject those entries.

        Mirrors `_annotate_transport_inheritance` in `scripts/render_schemas.py`:
        a malformed `transport_defaults` or `transports` raises here rather
        than silently passing through to a misleading "missing transport_type"
        error from the discriminator.
        """
        if not isinstance(data, dict):
            return data
        defaults = data.get("transport_defaults")
        if defaults is not None and not isinstance(defaults, dict):
            raise ValueError("transport_defaults must be an object")
        transports = data.get("transports")
        if transports is not None and not isinstance(transports, dict):
            raise ValueError("transports must be an object keyed by transport name")
        default_kind = defaults.get("transport_type") if isinstance(defaults, dict) else None
        if not default_kind or not isinstance(transports, dict):
            return data
        for _name, entry in transports.items():
            if isinstance(entry, dict) and "transport_type" not in entry:
                entry["transport_type"] = default_kind
        return data

    @model_validator(mode="after")
    def _default_transport_declared(self) -> "ConnectorBase":
        """RULE-CTOR-001: the transport every operation falls back to exists."""
        if self.default_transport not in self.transports:
            raise violation(
                "RULE-CTOR-001",
                f"value={self.default_transport!r} "
                f"not in {sorted(self.transports)!r}",
            )
        return self

    @model_validator(mode="after")
    def _transport_refs_resolvable(self) -> "ConnectorBase":
        """Every `transport_ref` site must point at a declared transport.

        Spec: §Transport Selection — auth ops, post-auth requests, and resource
        discovery may declare `transport_ref`; an unresolved reference would
        otherwise survive Pydantic validation and only fail at runtime.
        """
        transports = set(self.transports.keys())

        def _check(ref: str | None, where: str) -> None:
            if ref is None or ref in transports:
                return
            raise ValueError(
                f"{where} transport_ref={ref!r} is not declared in `transports` "
                f"(declared: {sorted(transports)!r}; spec: §Transport Selection)"
            )

        for op_name in ("authorize", "token_exchange", "refresh", "test"):
            op = getattr(self.auth, op_name, None)
            if op is not None:
                _check(op.transport_ref, f"auth.{op_name}")

        if self.resource_discovery is not None:
            _check(self.resource_discovery.transport_ref, "resource_discovery")

        for name, output in self.connection_contract.post_auth_outputs.items():
            for req_name in ("options_request", "discovery_request"):
                req = getattr(output, req_name)
                if req is not None:
                    _check(
                        req.transport_ref,
                        f"connection_contract.post_auth_outputs.{name}.{req_name}",
                    )
        return self

    @model_validator(mode="after")
    def _connection_contract_internal_refs(self) -> "ConnectorBase":
        contract = self.connection_contract
        # Spec: §Connection Inputs — "No two declarations in one
        # connection_contract may write the same saved storage path. ... applies
        # only to storage='secrets'." Build the path set with explicit duplicate
        # detection so collisions surface instead of silently collapsing.
        secret_paths: list[str] = []
        non_secret_paths: set[str] = set()
        for name, inp in contract.inputs.items():
            path = f"{inp.storage}.{name}"
            if inp.storage == "secrets":
                secret_paths.append(path)
            else:
                non_secret_paths.add(path)
        for name, out in contract.post_auth_outputs.items():
            path = f"{out.storage}.{name}"
            if out.storage == "secrets":
                secret_paths.append(path)
            else:
                non_secret_paths.add(path)

        duplicate_secrets = sorted({p for p in secret_paths if secret_paths.count(p) > 1})
        if duplicate_secrets:
            raise ValueError(
                f"connection_contract declares the same secret storage path more than once: "
                f"{duplicate_secrets!r} (spec: §Connection Inputs — secret-storage uniqueness)"
            )

        all_paths = set(secret_paths) | non_secret_paths
        unresolved = sorted(p for p in contract.required_for_activation if p not in all_paths)
        if unresolved:
            raise ValueError(
                f"connection_contract.required_for_activation paths {unresolved} do not "
                "resolve to any declared input or post_auth_output "
                "(spec: §Save-Time Validation)"
            )

        if contract.validation is not None:
            input_names = set(contract.inputs.keys())
            for idx, rule in enumerate(contract.validation.rules):
                when_field = rule.when.field
                if when_field not in input_names:
                    raise ValueError(
                        f"connection_contract.validation.rules[{idx}].when.field "
                        f"references undeclared input '{when_field}' (spec: §Cross-Input Validation)"
                    )
                for kind in ("require", "forbid"):
                    refs = getattr(rule, kind) or []
                    bad = [r for r in refs if r not in input_names]
                    if bad:
                        raise ValueError(
                            f"connection_contract.validation.rules[{idx}].{kind} "
                            f"references undeclared inputs {bad} (spec: §Cross-Input Validation)"
                        )

        return self


class ApiConnector(ConnectorBase):
    """API-type connector. Provider configuration lives on `transports.<name>` (HttpTransport)."""

    kind: Literal[ConnectorKind.API] = Field(
        description="Connector kind discriminator"
    )


# A database connector's transports are the SQL families only (never http/s3/
# file/stdout). Narrowing the inherited `transports` value type to this union
# publishes a per-kind `oneOf` into the schema, so an external validator rejects
# a `sqlalchemy`-typed transport under, say, a `stdout` connector — which the
# broad `Transport` union on `ConnectorBase` would have accepted.
_DatabaseTransport = Annotated[
    Union[
        Annotated[SqlAlchemyTransport, UnionTag("sqlalchemy")],
        Annotated[AdbcTransport, UnionTag("adbc")],
    ],
    Discriminator("transport_type"),
]


class DatabaseConnector(ConnectorBase):
    """Relational (SQL) database connector. Provider configuration lives on `transports.<name>` (SqlAlchemyTransport or AdbcTransport)."""

    kind: Literal[ConnectorKind.DATABASE] = Field(
        description="Connector kind discriminator"
    )
    transports: dict[str, _DatabaseTransport] = Field(
        ...,
        min_length=1,
        description=(
            "Named database transport contracts (`sqlalchemy` | `adbc`), "
            "discriminated by `transport_type`."
        ),
    )
    sql_capabilities: SqlCapabilities | None = Field(
        default=None,
        description=(
            "Declared SQL write-path capabilities (engine ADR §5). SQL-specific "
            "— not present on other connector kinds. Optional; when omitted the "
            "engine refuses any needed-but-undeclared fact at handshake time. "
            "When present, every shape fact is required; `limits` is the "
            "one additive member and may be omitted."
        ),
    )


class NosqlConnector(ConnectorBase):
    """NoSQL database connector — wide-column, key-value, or graph stores
    (e.g. Cassandra, DynamoDB, Redis). Owns the same reusable-definition shape
    as `DatabaseConnector` and selects the `database-endpoint` document schema;
    the transport family is provider-specific and declared on `transports`."""

    kind: Literal[ConnectorKind.NOSQL] = Field(
        description="Connector kind discriminator"
    )


class DocumentConnector(ConnectorBase):
    """Document-store connector (e.g. MongoDB and other document databases).
    Owns the same reusable-definition shape as `DatabaseConnector` and selects
    the `database-endpoint` document schema; the transport family is
    provider-specific and declared on `transports`."""

    kind: Literal[ConnectorKind.DOCUMENT] = Field(
        description="Connector kind discriminator"
    )


class FileConnector(ConnectorBase):
    """Filesystem-backed connector. Provider configuration lives on `transports.<name>` (FileTransport)."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra=_FILE_LIKE_CONNECTION_CONTRACT_RULES
    )

    kind: Literal[ConnectorKind.FILE] = Field(
        description="Connector kind discriminator"
    )
    transports: dict[str, FileTransport] = Field(
        ...,
        min_length=1,
        max_length=1,
        description="The single named `file` transport (spec: §Transport Contracts).",
    )
    auth: NoneAuth = Field(
        ...,
        description="Filesystem connectors carry no auth workflow (`type: 'none'`).",
    )

    @model_validator(mode="after")
    def _validate_no_post_auth_contract(self) -> "FileConnector":
        _reject_post_auth_contract(self.connection_contract, "file")
        return self


class S3Connector(ConnectorBase):
    """S3-backed connector. Provider configuration lives on `transports.<name>` (S3Transport)."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra=_FILE_LIKE_CONNECTION_CONTRACT_RULES
    )

    kind: Literal[ConnectorKind.S3] = Field(
        description="Connector kind discriminator"
    )
    transports: dict[str, S3Transport] = Field(
        ...,
        min_length=1,
        max_length=1,
        description="The single named `s3` transport (spec: §Transport Contracts).",
    )
    auth: CredentialsAuth = Field(
        ...,
        description="S3 connectors authenticate with static AWS credentials (`type: 'credentials'`).",
    )

    @model_validator(mode="after")
    def _validate_no_post_auth_contract(self) -> "S3Connector":
        _reject_post_auth_contract(self.connection_contract, "s3")
        return self


class StdoutConnector(ConnectorBase):
    """Stdout connector — debug/print destination. `StdoutTransport` is a marker."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra=_FILE_LIKE_CONNECTION_CONTRACT_RULES
    )

    kind: Literal[ConnectorKind.STDOUT] = Field(
        description="Connector kind discriminator"
    )
    transports: dict[str, StdoutTransport] = Field(
        ...,
        min_length=1,
        max_length=1,
        description="The single named `stdout` transport (spec: §Transport Contracts).",
    )
    auth: NoneAuth = Field(
        ...,
        description="Stdout connectors carry no auth workflow (`type: 'none'`).",
    )

    @model_validator(mode="after")
    def _validate_no_post_auth_contract(self) -> "StdoutConnector":
        _reject_post_auth_contract(self.connection_contract, "stdout")
        return self


# --- Discriminated union (single contract entry point) ---


Connector = Annotated[
    Union[
        Annotated[ApiConnector, UnionTag("api")],
        Annotated[DatabaseConnector, UnionTag("database")],
        Annotated[NosqlConnector, UnionTag("nosql")],
        Annotated[DocumentConnector, UnionTag("document")],
        Annotated[FileConnector, UnionTag("file")],
        Annotated[S3Connector, UnionTag("s3")],
        Annotated[StdoutConnector, UnionTag("stdout")],
    ],
    Discriminator("kind"),
]


_CONNECTOR_ADAPTER: TypeAdapter[ConnectorBase] = TypeAdapter(Connector)


def parse_connector(data: dict[str, Any]) -> ConnectorBase:
    """Parse a connector dict into the correct subclass via the discriminated union.

    Args:
        data: Deserialized connector record.

    Returns:
        ApiConnector, DatabaseConnector, NosqlConnector, DocumentConnector,
        FileConnector, S3Connector, or StdoutConnector based on the `kind`
        discriminator.

    Raises:
        pydantic.ValidationError: If the data fails validation, including a
            missing or unknown `kind`.
    """
    return _CONNECTOR_ADAPTER.validate_python(data)
