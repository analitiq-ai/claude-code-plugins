"""
Connection models and validators.

Aligned with the published Analitiq schema documentation.
Cross-references:
- the published Analitiq schema documentation (display_name/tags)
- the published Analitiq schema documentation (`x-*` extensions)
- the published Analitiq schema documentation (server-managed/reserved)

Two private bases plus four public concrete models for the
read/write/persisted/patch concerns. Reads and writes share the same
storage-map shape per the spec
(the published Analitiq schema documentation):
both expose `parameters` / `selections` / `discovered` / `secret_refs`
directly. The legacy `values` write envelope is retained only on
`ConnectionPatch` for now.

- `_ConnectionAuthored` — private base carrying metadata fields
  (`display_name`, `description`, `connector_id`, `tags`) shared by
  every variant.
- `_ConnectionStoredMaps` — private mixin carrying the storage maps
  (`parameters` / `selections` / `discovered` / `secret_refs`). Mixed
  into `ConnectionInput`, `ConnectionConfig`, and `ConnectionDocument`
  so the public contract and the persisted record agree.
  `ConnectionPatch` is intentionally excluded — it still carries the
  legacy flat `values` envelope (see below).
- `ConnectionConfig` (read-side) — extends the base with the storage
  maps + optional server-managed fields and `extra="allow"` so DDB
  items round-trip without spurious errors.
- `ConnectionInput` (write-side) — strict POST/PUT body contract with
  the storage maps; rejects server-managed fields and unknown
  top-level keys. Source of `connection/latest.json`.
- `ConnectionDocument` — persisted-record contract; internal
  read-side validator for `GET /connections/{id}`. Server-managed
  fields are required. Not published.
- `ConnectionPatch` — partial-update body for PATCH/PUT routes
  carrying the legacy flat `values` envelope; `connector_id` is
  immutable. Migrating PATCH onto the typed buckets is a follow-up.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    field_validator,
    model_validator,
)

from k2m.models.shared.common import (
    CorruptedPlaceholderBase,
    DESCRIPTION_MAX,
    DISPLAY_NAME_MAX,
    DISPLAY_NAME_MIN,
    NO_EDGE_WHITESPACE_PATTERN,
    SEMVER_PATTERN,
    TAGS_MAX,
    make_authored_top_level_check,
    schema_url_for,
    schema_url_pattern,
    validate_display_name as _validate_display_name,
    validate_tags as _validate_tags,
)
from k2m.models.shared.common import Tag as _Tag
from k2m.models.shared.common import TrimmedTag as _TrimmedTag
from k2m.models.shared.types import UUID_PATTERN
from k2m.models.connector_client import ResolvedConnector

CONNECTION_SCHEMA_URL = schema_url_for("connection")

# Underscore aliases for in-module readability.
_DESCRIPTION_MAX = DESCRIPTION_MAX
_DISPLAY_NAME_MIN = DISPLAY_NAME_MIN
_DISPLAY_NAME_MAX = DISPLAY_NAME_MAX
_TAGS_MAX = TAGS_MAX

# Server-managed top-level fields per
# the published Analitiq schema documentation
# §Server-Managed and Reserved Fields.
# `connection_id` is intentionally excluded so externally-authored connection
# documents (publicly managed artifacts) can supply their own UUID through the
# published validation schema.
SERVER_MANAGED_FIELDS: frozenset[str] = frozenset({
    "version",
    "org_id",
    "connector_version",
    "auth_state",
    "created_at",
    "updated_at",
})

_check_authored_top_level = make_authored_top_level_check(
    SERVER_MANAGED_FIELDS,
    spec_doc="the published Analitiq schema documentation",
)


# --- Secret-shape detection (heuristic guards on non-secret maps) ---

# Names that look secret in any field that should hold non-secret context
# (`parameters`, `selections`, `discovered`). Spec: §Secret Storage Materialization
# — secret material lives in the secret store and is referenced via `secret_refs`.
_NON_SECRET_FIELD_KEY_PATTERN = re.compile(
    r"(?:^|_)(?:access|refresh|id|bearer|oauth|auth|session|sas|api)_?token$"
    r"|(?:^|_)(?:api|access|secret|signing|private|encryption|account|service_account)_?key$"
    r"|cert_(?:pem|key)$"
    r"|(?:^|_)assertion$"
    r"|(?:^|_)secret(?:_|$)"
    r"|(?:^|_)password(?:_|$)"
    r"|(?:^|_)(?:pwd|pat|pkey|creds)(?:_|$)"
    r"|(?:^|_)passwd(?:_|$)"
    r"|(?:^|_)client_secret(?:_|$)"
    r"|(?:^|_)credentials?(?:_|$)"
    r"|(?:^|_)connection_string(?:_|$)",
    re.IGNORECASE,
)
_NON_SECRET_FIELD_KEY_EXACT = frozenset({
    "secret", "password", "passwd", "pwd", "key", "token", "credential",
    "credentials", "creds", "pat", "pkey", "assertion",
})


def _looks_secret(key: str) -> bool:
    return (
        key.lower() in _NON_SECRET_FIELD_KEY_EXACT
        or _NON_SECRET_FIELD_KEY_PATTERN.search(key) is not None
    )


# secret_refs values must look like a reference into a secret store, not raw
# secret material. Accepted forms:
#   connections/<alias>/<name>             — relative path inside analitiq-secrets-{env}
#   secrets/<alias>/<name>                 — alias for legacy callers using `secrets/` prefix
#   ssm:/path/to/parameter                 — SSM parameter store
#   arn:aws:secretsmanager:<region>:<acct>:secret:<id>
#   arn:aws:ssm:<region>:<acct>:parameter/<id>
#   s3://<bucket>/<key>                    — explicit S3 reference
#
# Note: this regex is structural (does the value look like a known reference
# scheme?), not security-validating. Resolvers MUST canonicalize the path and
# scope-check it against the per-org secret prefix before fetching, since the
# regex does not prevent traversal sequences such as `..` inside the path
# component.
SECRET_REF_VALUE_PATTERN = (
    r"^(?:"
    r"(?:connections|secrets)/[A-Za-z0-9_./\-]+"
    r"|ssm:/[A-Za-z0-9_./\-]+"
    r"|arn:aws:secretsmanager:[A-Za-z0-9\-]+:\d+:secret:[A-Za-z0-9/_\-+=.@]+"
    r"|arn:aws:ssm:[A-Za-z0-9\-]+:\d+:parameter/[A-Za-z0-9_./\-]+"
    r"|s3://[A-Za-z0-9._\-]+/[A-Za-z0-9_./\-]+"
    r")$"
)
SecretRefValue = Annotated[str, StringConstraints(pattern=SECRET_REF_VALUE_PATTERN)]


# --- AuthState (read-side, server-managed) ---


AuthStatusLiteral = Literal[
    "active", "draft", "pending_authorization", "needs_post_auth_setup",
    "expired", "needs_refresh", "invalid",
]


class _BaseAuthState(BaseModel):
    """Common non-secret lifecycle/status fields for `auth_state`."""

    model_config = ConfigDict(extra="forbid")

    status: AuthStatusLiteral = Field(default="active", description="Connection auth status")
    last_validated_at: datetime | None = Field(
        default=None, description="Last successful test/validation timestamp"
    )
    account_label: str | None = Field(
        default=None,
        description="Displayable provider account identifier — must NOT be a secret",
    )
    error: str | None = Field(
        default=None,
        max_length=500,
        description="Human-readable error from last verification attempt. Must not contain credentials.",
    )


class ApiKeyAuthState(_BaseAuthState):
    type: Literal["api_key"] = Field(description="Auth type discriminator")


class BasicAuthState(_BaseAuthState):
    type: Literal["basic_auth"] = Field(description="Auth type discriminator")


class OAuth2AuthCodeAuthState(_BaseAuthState):
    """Lifecycle/status payload for an OAuth2 authorization-code connection."""

    type: Literal["oauth2_authorization_code"] = Field(
        description="Auth type discriminator",
    )
    granted_scopes: list[str] | None = Field(
        default=None, description="Scopes granted by the authorization server"
    )
    expires_at: datetime | None = Field(default=None, description="Access token expiry (UTC)")
    last_refresh_at: datetime | None = Field(
        default=None, description="Most recent token refresh attempt timestamp"
    )
    last_refresh_status: Literal["ok", "failed"] | None = Field(
        default=None, description="Outcome of the last refresh attempt"
    )


class OAuth2ClientCredsAuthState(_BaseAuthState):
    type: Literal["oauth2_client_credentials"] = Field(
        description="Auth type discriminator",
    )
    expires_at: datetime | None = Field(default=None, description="Access token expiry (UTC)")
    last_refresh_at: datetime | None = Field(
        default=None, description="Most recent token refresh attempt timestamp"
    )
    last_refresh_status: Literal["ok", "failed"] | None = Field(
        default=None, description="Outcome of the last refresh attempt"
    )


class JwtAuthState(_BaseAuthState):
    type: Literal["jwt"] = Field(description="Auth type discriminator")
    key_id: str | None = Field(
        default=None, description="JWT header `kid` claim — non-secret signing key id"
    )
    expires_at: datetime | None = Field(default=None, description="Current signed-token expiry (UTC)")


class DbAuthState(_BaseAuthState):
    type: Literal["db"] = Field(description="Auth type discriminator")


class CredentialsAuthState(_BaseAuthState):
    type: Literal["credentials"] = Field(description="Auth type discriminator")


class AwsIamAuthState(_BaseAuthState):
    type: Literal["aws_iam"] = Field(description="Auth type discriminator")


class NoneAuthState(_BaseAuthState):
    type: Literal["none"] = Field(description="Auth type discriminator")


AuthState = Annotated[
    Union[
        Annotated[ApiKeyAuthState, Tag("api_key")],
        Annotated[BasicAuthState, Tag("basic_auth")],
        Annotated[OAuth2AuthCodeAuthState, Tag("oauth2_authorization_code")],
        Annotated[OAuth2ClientCredsAuthState, Tag("oauth2_client_credentials")],
        Annotated[JwtAuthState, Tag("jwt")],
        Annotated[DbAuthState, Tag("db")],
        Annotated[CredentialsAuthState, Tag("credentials")],
        Annotated[AwsIamAuthState, Tag("aws_iam")],
        Annotated[NoneAuthState, Tag("none")],
    ],
    Discriminator("type"),
]


# --- Authored shape ---


class _ConnectionAuthored(BaseModel):
    """Authored connection metadata shared by input and persisted models.

    Storage maps (`parameters` / `selections` / `discovered` / `secret_refs`)
    are part of the authored shape per
    the published Analitiq schema documentation
    and live on `_ConnectionStoredMaps`. `_ConnectionStoredMaps` is mixed
    into both `ConnectionInput` (the published JSON Schema source) and
    `ConnectionConfig` / `ConnectionDocument` (the persisted read-side
    models), so the public contract and the on-disk shape carry the same
    storage-map keys.
    """

    schema_url: Literal[CONNECTION_SCHEMA_URL] | None = Field(
        default=None,
        alias="$schema",
        description="Connection schema URL (optional in API payloads, required for standalone files).",
    )

    display_name: str | None = Field(
        default=None,
        min_length=_DISPLAY_NAME_MIN,
        max_length=_DISPLAY_NAME_MAX,
        description="User-facing connection label (1-120 chars, no leading/trailing whitespace).",
    )
    description: str | None = Field(
        default=None, max_length=_DESCRIPTION_MAX, description="User-facing summary."
    )

    connector_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Identifier of the connector this connection configures. "
            "Non-empty string; the registry assigns connector identifiers "
            "(UUID or slug — not constrained at the schema level)."
        ),
    )

    tags: list[_Tag] | None = Field(
        default=None,
        max_length=_TAGS_MAX,
        description="Grouping/search labels (max 50, each 1-64 chars, unique, trimmed).",
    )

    @field_validator("display_name")
    @classmethod
    def _validate_display_name_field(cls, v: str | None) -> str | None:
        return _validate_display_name(v)

    @field_validator("tags")
    @classmethod
    def _validate_tags_field(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)


class _ConnectionStoredMaps(BaseModel):
    """Storage maps shared by every storage-map-bearing connection variant.

    Mixed into the write-side `ConnectionInput` and the read-side
    `ConnectionConfig` / `ConnectionDocument`. `ConnectionPatch` does
    NOT inherit this — it still carries the legacy flat `values`
    envelope (see its docstring). Spec:
    the published Analitiq schema documentation.
    """

    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Non-secret submitted values keyed by connection-contract input key. "
            "Per-key value vocabularies (enums, formats, etc.) are authored "
            "on the owning connector's `connection_contract.inputs.<key>` and "
            "enforced by the connections Lambda on save."
        ),
    )
    selections: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Durable user-selected post-auth values keyed by post-auth output key. "
            "Spec: §Post-Auth Outputs."
        ),
    )
    discovered: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Durable provider-discovered non-secret values keyed by post-auth "
            "output key. Spec: §Post-Auth Outputs."
        ),
    )
    secret_refs: dict[str, SecretRefValue] = Field(
        default_factory=dict,
        description=(
            "Opaque secret-store pointers keyed by connection-contract input or "
            "post-auth output key. Values must match a known reference scheme: "
            "`connections/<path>`, `secrets/<path>`, `ssm:/<path>`, "
            "`arn:aws:secretsmanager:<region>:<acct>:secret:<id>`, "
            "`arn:aws:ssm:<region>:<acct>:parameter/<id>`, or "
            "`s3://<bucket>/<key>`. Spec: §Secret Storage Materialization."
        ),
    )

    @model_validator(mode="after")
    def _validate_no_secret_keys(self) -> "_ConnectionStoredMaps":
        _validate_non_secret_maps(
            parameters=self.parameters,
            selections=self.selections,
            discovered=self.discovered,
        )
        return self


def _validate_non_secret_maps(
    *,
    parameters: dict[str, Any] | None,
    selections: dict[str, Any] | None,
    discovered: dict[str, Any] | None = None,
) -> None:
    """Enforce secret-shaped-key rules on the non-secret maps.

    Spec: §Secret Storage Materialization — non-secret maps must not contain
    secret material; refs go in `secret_refs`.

    Scope: this check inspects map *keys* only (e.g. rejects `"password"`,
    `"api_key"`). It does NOT scan values, so a raw secret stored under an
    innocuous key (e.g. `{"region": "AKIA..."}`) will pass. Value-shape
    scanning is out of scope for v1.

    Per-key value vocabularies (e.g. `ssl_mode`) are authored on the
    owning connector at `connection_contract.inputs.<key>` (for
    values landing in `parameters` / `secret_refs`) or
    `connection_contract.post_auth_outputs.<key>` (for values landing
    in `selections` / `discovered`), and validated by the connections
    Lambda on save — not here. Imposing a single canonical enum at
    the connection-contract level would conflict with driver-native
    vocabularies (e.g. MySQL's `PREFERRED` vs libpq's `prefer`).

    Called from `_ConnectionStoredMaps._validate_no_secret_keys`, which
    is mixed into the storage-map-bearing variants (`ConnectionInput`,
    `ConnectionConfig`, `ConnectionDocument`). `ConnectionPatch`'s
    legacy `values` envelope is not validated here.
    """
    for field_name, payload in (
        ("parameters", parameters),
        ("selections", selections),
        ("discovered", discovered),
    ):
        if not payload:
            continue
        leaked = sorted(k for k in payload if _looks_secret(str(k)))
        if leaked:
            raise ValueError(
                f"{field_name} must not contain secret-shaped keys {leaked}; "
                "move them to secret storage and reference via `secret_refs` "
                "(spec: §Secret Storage Materialization)"
            )




class ConnectionConfig(_ConnectionAuthored, _ConnectionStoredMaps):
    """Read-side model. Round-trips persisted DDB documents.

    `extra="allow"` lets DDB-internal attributes (`pk`, `sk`, GSI keys) flow
    through without validation errors. Server-managed business fields are
    explicit declarations so consumers can read them typed.

    API write paths use `ConnectionInput` / `ConnectionPatch` instead, which
    apply the strict `x-*` extension policy and reject server-managed authoring.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    connection_id: str | None = Field(
        default=None,
        pattern=UUID_PATTERN,
        description="Stable base connection UUID (server-managed); RFC-4122 form per §Identifier Forms.",
    )
    version: int | None = Field(
        default=None, ge=1, description="Connection configuration version (server-managed)."
    )
    org_id: str | None = Field(
        default=None,
        pattern=UUID_PATTERN,
        description="Tenant identifier derived from auth context (server-managed); RFC-4122 form.",
    )
    connector_version: str | None = Field(
        default=None,
        pattern=SEMVER_PATTERN,
        description=(
            "Connector release semantic version recorded at save time. Drift "
            "detection rides on this field's semver against the current "
            "connector release (server-managed)."
        ),
    )
    auth_state: AuthState | None = Field(  # type: ignore[type-arg]
        default=None,
        description=(
            "Per-auth-type non-secret lifecycle/status payload (server-managed). "
            "Strictly typed (extra=forbid on every variant)."
        ),
    )
    created_at: datetime | None = Field(default=None, description="Creation timestamp (server-managed).")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp (server-managed).")


class ConnectionInput(_ConnectionAuthored, _ConnectionStoredMaps):
    """Strict API input variant for POST/PUT bodies.

    Source of the published `connection/latest.json`. Server-managed fields
    (`version`, `org_id`, `connector_version`, `auth_state`, `created_at`,
    `updated_at`) are not declared here; `_check_authored_top_level`
    rejects them on input by name (alongside unknown top-level keys).

    `connection_id` is an optional authored identifier (RFC-4122 UUID) so
    externally-authored connection definitions can supply their own UUID;
    the service assigns one when the create payload omits it.

    The storage maps inherited from `_ConnectionStoredMaps`
    (`parameters` / `selections` / `discovered` / `secret_refs`) are
    authored per
    the published Analitiq schema documentation
    — the flat `values` write envelope was a legacy authoring shape and
    has been removed in favor of the typed buckets that match the
    persisted record and the engine's runtime expectation.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connection_id: str | None = Field(
        default=None,
        pattern=UUID_PATTERN,
        description=(
            "Connection UUID (RFC-4122). Optional in authored definitions so "
            "external publishers can assign their own identifier; "
            "service-assigned on ingest when omitted."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_top_level(cls, data: Any) -> Any:
        return _check_authored_top_level(cls, data)


class ConnectionDocument(_ConnectionAuthored, _ConnectionStoredMaps):
    """Persisted-record variant — internal read-side validator for `GET /connections/{id}`.

    Authored fields plus server-managed `connection_id`/`version`/`org_id`/
    `connector_id`/`connector_version`/`auth_state`/timestamps and the
    persisted storage maps (`parameters` / `selections` / `discovered` /
    `secret_refs`). NOT published as a JSON Schema; the public
    `connection/latest.json` is rendered from `ConnectionInput`. Drift
    detection rides on `connector_version` semver. `ConnectionConfig` is
    the read-side variant for DDB round-trip with `pk`/`sk`/GSI internal
    keys.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connection_id: str = Field(
        ...,
        pattern=UUID_PATTERN,
        description="Stable base connection UUID (server-managed); RFC-4122 form per §Identifier Forms.",
    )
    version: int = Field(..., ge=1, description="Connection configuration version (server-managed).")
    org_id: str = Field(
        ...,
        pattern=UUID_PATTERN,
        description="Tenant identifier (server-managed); RFC-4122 form.",
    )
    connector_version: str = Field(
        ...,
        pattern=SEMVER_PATTERN,
        description=(
            "Connector release semantic version recorded at save time. Drift "
            "detection rides on this field's semver against the current "
            "connector release (server-managed)."
        ),
    )
    auth_state: AuthState | None = Field(  # type: ignore[type-arg]
        default=None,
        description="Per-auth-type non-secret lifecycle/status payload (server-managed).",
    )
    created_at: datetime = Field(..., description="Creation timestamp (server-managed).")
    updated_at: datetime = Field(..., description="Last update timestamp (server-managed).")


class ConnectionRead(_ConnectionAuthored, _ConnectionStoredMaps):
    """Wire projection of one stored connection — PRIVATE `connection-read` contract.

    The shape the connections API returns everywhere a full record crosses
    the wire: `GET /connections/{id}`, items of `GET /connections`, and the
    `data` payload of POST/PUT/PATCH responses (DELETE returns its own
    minimal `{connection_id, connector_id}` acknowledgement and is NOT
    covered by this contract). Replaces the
    hand-rolled `_SAFE_RESPONSE_FIELDS` allow-list: `extra="ignore"` makes
    the model itself the projection, so dumping a validated record carries
    exactly the declared fields and raw secret values can never leak
    (`secret_refs` is the only secret-adjacent field declared).

    Server-managed fields are optional — `connector_version` is absent when
    the connector release carries no version, and legacy rows predate
    several fields. Distinct from `ConnectionConfig` (DDB round-trip,
    `extra="allow"`) and `ConnectionDocument` (persisted-record spec pin,
    `extra="forbid"`).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Any-host pattern (not the env-pinned Literal the WRITE models use):
    # the published contract already accepts any `schemas.analitiq.<tld>`
    # host, and a stored document imported across environments must read
    # back rather than degrade — `$schema` is an informational pointer.
    schema_url: str | None = Field(  # type: ignore[assignment]
        default=None,
        alias="$schema",
        pattern=schema_url_pattern("connection"),
        description="Connection schema URL when stored (any environment host).",
    )

    connection_id: str = Field(
        ...,
        pattern=UUID_PATTERN,
        description="Stable base connection UUID (server-managed).",
    )
    version: int | None = Field(
        default=None, ge=1, description="Connection configuration version (server-managed)."
    )
    org_id: str | None = Field(
        default=None, description="Tenant identifier (server-managed)."
    )
    connector_version: str | None = Field(
        default=None,
        pattern=SEMVER_PATTERN,
        description="Connector release semver recorded at save time (server-managed).",
    )
    auth_state: AuthState | None = Field(  # type: ignore[type-arg]
        default=None,
        description="Per-auth-type non-secret lifecycle/status payload (server-managed).",
    )
    created_at: datetime | None = Field(default=None, description="Creation timestamp (server-managed).")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp (server-managed).")

    resolved_connector: ResolvedConnector | None = Field(
        default=None,
        alias="_resolved_connector",
        description=(
            "Server-injected sidecar (`with_connector=true`): the owning "
            "connector projected to the client shape. Omitted when the "
            "connector could not be resolved."
        ),
    )


class ConnectionListItem(BaseModel):
    """One item of `GET /connections?skip_connection_details=1` — the
    deliberately tiny dashboard listing shape (PRIVATE `connection-list-item`
    contract)."""

    # `populate_by_name=True` mirrors `ConnectionRead`: both carry the same
    # aliased `resolved_connector` (`_resolved_connector`) sidecar, so they
    # must populate it identically — by alias or by field name.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    connection_id: str = Field(..., description="Stable base connection UUID.")
    connector_id: str | None = Field(default=None, description="Owning connector identifier.")
    display_name: str | None = Field(default=None, description="User-facing connection label.")
    auth_state: AuthState | None = Field(  # type: ignore[type-arg]
        default=None, description="Per-auth-type non-secret lifecycle/status payload."
    )
    created_at: datetime | None = Field(default=None, description="Creation timestamp (server-managed).")

    resolved_connector: ResolvedConnector | None = Field(
        default=None,
        alias="_resolved_connector",
        description=(
            "Server-injected sidecar (`with_connector=true`): the owning "
            "connector projected to the client shape. Omitted when the "
            "connector could not be resolved."
        ),
    )


class CorruptedConnectionPlaceholder(CorruptedPlaceholderBase):
    """Per-row degrade marker for nonconforming stored connections.

    A list response replaces a row that fails `ConnectionRead` /
    `ConnectionListItem` validation with this closed shape (and logs the
    defect server-side) so one corrupt row cannot take down the whole
    dashboard listing. Single-resource GETs never return it — they fail
    loud with a 500 instead.
    """

    connection_id: str | None = Field(
        default=None, description="Base connection UUID when readable, else omitted."
    )
    connector_id: str | None = Field(
        default=None, description="Owning connector identifier when readable, else omitted."
    )


# Published unions for the PRIVATE read contracts. The closed, discriminated
# placeholder variant comes first so tolerant-union consumers match it before
# the open record shapes.
ConnectionReadRecord = CorruptedConnectionPlaceholder | ConnectionRead
ConnectionListItemRecord = CorruptedConnectionPlaceholder | ConnectionListItem


class PostAuthOption(BaseModel):
    """One rendered picker option for a `user_selection` post-auth output."""

    model_config = ConfigDict(extra="forbid")

    value: Any = Field(
        ...,
        description=(
            "Provider value the FE submits back via the connection write "
            "surface. Any JSON value; never null (null-valued items are "
            "dropped server-side)."
        ),
    )
    label: Any = Field(
        ...,
        description="Display label; falls back to `value` when the provider has none.",
    )


class PostAuthOptions(BaseModel):
    """`data` payload of GET /connections/{id}/post-auth-options/{output_key}
    (PRIVATE `post-auth-options` contract)."""

    model_config = ConfigDict(extra="forbid")

    options: list[PostAuthOption] = Field(
        ..., description="Rendered picker options, in provider order."
    )


# Request-surface annotated types: declarative mirrors of the imperative
# `_validate_display_name` / `_validate_tags` validators, declared on the
# type so they render INSIDE the string/array branch of the contract's
# `anyOf` (a field-level `json_schema_extra` would land as an `anyOf`
# sibling, which the zod generator drops silently). The rendered request
# contract must never approve a payload the gate rejects.
_TrimmedDisplayName = Annotated[
    str,
    StringConstraints(
        min_length=_DISPLAY_NAME_MIN,
        max_length=_DISPLAY_NAME_MAX,
        pattern=NO_EDGE_WHITESPACE_PATTERN,
    ),
]
_UniqueTrimmedTags = Annotated[
    list[_TrimmedTag],
    Field(max_length=_TAGS_MAX, json_schema_extra={"uniqueItems": True}),
]


class ConnectionPatch(BaseModel):
    """Partial-update model for PATCH/PUT /connections/{id}.

    Authored fields only — `connector_id` is immutable per the spec and is
    not patchable. Server-managed fields (`discovered`, `secret_refs`,
    `auth_state`, `connector_version`, `created_at`, `updated_at`, …) are
    rejected: they are derived from the contract + verify/discover pipeline
    and must not drift via an external patch.

    `values` is the legacy flat write envelope: `{<input_key>:
    <value | null>}`. The connections Lambda routes each key into
    `parameters` / `selections` / `secret_refs` per the connector
    contract. `None` deletes the underlying entry; an omitted key
    leaves the stored value alone. PATCH still carries this envelope
    while POST/PUT (`ConnectionInput`) has moved to the typed buckets
    directly — migrating PATCH onto the typed buckets is a follow-up.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    display_name: _TrimmedDisplayName | None = Field(
        default=None,
        description="User-facing connection label (1-120 chars, no leading/trailing whitespace).",
    )
    description: str | None = Field(
        default=None, max_length=_DESCRIPTION_MAX, description="User-facing summary."
    )
    values: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Flat write envelope keyed by connection-contract input (or "
            "post-auth output) names; the Lambda routes each entry into "
            "`parameters` / `selections` / secret storage per the connector "
            "contract. `null` deletes the stored entry; an omitted key "
            "leaves it alone; any other value (including `\"\"`, `0`, "
            "`false`) is taken verbatim."
        ),
    )
    tags: _UniqueTrimmedTags | None = Field(
        default=None,
        description="Grouping/search labels (max 50, each 1-64 chars, unique, trimmed).",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_top_level(cls, data: Any) -> Any:
        return _check_authored_top_level(cls, data)

    @field_validator("display_name")
    @classmethod
    def _validate_display_name_field(cls, v: str | None) -> str | None:
        return _validate_display_name(v)

    @field_validator("tags")
    @classmethod
    def _validate_tags_field(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tags(v)


class CreateConnectionPayload(ConnectionPatch):
    """FE wire payload for POST /connections.

    Source of the PRIVATE `connection-create-payload` contract — the shape
    the frontend submits, NOT the server's full acceptance gate: the Lambda
    pops `values` and validates the remainder through `ConnectionInput`,
    which additionally accepts externally-authored fields (`connection_id`,
    the typed storage buckets) that the FE never sends. Extends the patch
    payload with the one create-only requirement: `connector_id`.
    """

    connector_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Identifier of the connector this connection configures. "
            "Non-empty string; the registry assigns connector identifiers "
            "(UUID or slug — not constrained at the schema level)."
        ),
    )


def validate_connection_config(payload: dict) -> dict:
    """Validate and normalize a connection input payload.

    Returns the validated, coerced payload as a JSON-serializable dict using
    public alias names (e.g. `$schema`). Raises `pydantic.ValidationError` on
    invalid payloads.
    """
    config = ConnectionInput.model_validate(payload)
    return config.model_dump(exclude_none=True, mode="json", by_alias=True)
