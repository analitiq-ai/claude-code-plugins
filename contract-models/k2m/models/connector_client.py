"""Client-facing connector projection (`_resolved_connector` sidecars +
the `connector-catalog-item` catalog contract).

Wire shape of `k2m.connector_spec.client_shape()` — the bounded connector
projection the connections and pipelines Lambdas inject into read responses
(`_resolved_connector` on a connection, `_resolved_connectors` values on a
pipeline). It is a *convenience sidecar*, not the connector contract itself:
the authored connector document is published separately as the PUBLIC
`connector/latest.json` and consumers needing full strictness validate
against that.

The connectors Lambda returns the same projection as the catalog itself
(`GET /connectors`, `GET /connectors/{id}`) plus two handler-stamped fields
(`active`, `auth.platform_ready`) — `ConnectorCatalogItem` below extends the
sidecar shape with exactly those, and the PRIVATE `connector-catalog-item`
contract is rendered from the `ConnectorCatalogItemRecord` union.

Deliberately tolerant: every field is optional because the projection is
built from a thin DDB row merged with an S3-hosted spec that can degrade to
thin-row-only (`k2m.connector_spec.read_connector_spec_from_s3` returns None
on any read failure), and complex sub-documents (`connection_contract`,
`transports`, …) pass through verbatim. `extra="ignore"` makes the model a
projection: dumping a validated record carries exactly the declared fields.

`tests/unit/test_read_contract_projections.py::TestResolvedConnectorDriftPin`
pins `connector_spec._CLIENT_SHAPE_FIELDS` == this model's field set so a
projection change without a contract bump fails CI.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from k2m.models.shared.common import CorruptedPlaceholderBase


class ResolvedConnectorAuth(BaseModel):
    """`auth` narrowed by `connector_spec._project_auth`.

    Only the discriminator and the optional `test` template survive the
    projection — OAuth templating (`authorize`, `token_exchange`,
    `refresh`) is server-side only and must never reach a client.
    """

    model_config = ConfigDict(extra="ignore")

    type: str | None = Field(default=None, description="Auth type discriminator.")
    test: dict[str, Any] | None = Field(
        default=None,
        description="Optional connection-test request template (FE test affordance).",
    )


class ResolvedConnector(BaseModel):
    """One `client_shape()`-projected connector record."""

    model_config = ConfigDict(extra="ignore")

    connector_id: str | None = Field(
        default=None, description="Registry connector identifier (DDB key)."
    )
    slug: str | None = Field(default=None, description="Authored connector slug.")
    connector_name: str | None = Field(
        default=None,
        description="Legacy alias of `display_name` (FE catalog still reads it).",
    )
    connector_type: str | None = Field(
        default=None, description="Legacy alias of `kind` (FE catalog still reads it)."
    )
    display_name: str | None = Field(default=None, description="User-facing connector name.")
    description: str | None = Field(default=None, description="User-facing summary.")
    documentation_url: str | None = Field(
        default=None, description="Connector documentation link."
    )
    tags: list[str] | None = Field(default=None, description="Catalog grouping labels.")
    version: str | None = Field(
        default=None, description="Connector release semver (spec wins over thin row)."
    )
    kind: str | None = Field(
        default=None, description="BaseConnector `kind` discriminator identifying the connector's resource/runtime family."
    )
    connection_contract: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Connector connection contract (inputs / post_auth_outputs / "
            "required_for_activation), passed through verbatim with "
            "synthesised `ui.options` for enum-only inputs. Authored shape "
            "is pinned by the PUBLIC connector contract."
        ),
    )
    auth: ResolvedConnectorAuth | None = Field(
        default=None, description="Auth block narrowed to `{type, test}`."
    )
    transports: dict[str, Any] | None = Field(
        default=None, description="Named transport definitions (verbatim)."
    )
    transport_defaults: dict[str, Any] | None = Field(
        default=None, description="Transport defaults (verbatim)."
    )
    resource_discovery: dict[str, Any] | None = Field(
        default=None, description="Resource-discovery block (verbatim)."
    )


class ConnectorCatalogAuth(ResolvedConnectorAuth):
    """Catalog `auth` block — the sidecar projection plus `platform_ready`.

    `platform_ready` is stamped post-projection by the connectors Lambda on
    the detail GET only (issue #641): the OAuth-flow credential guard
    collapsed to a boolean, computed from the platform secrets entry. It is
    never authored — `connector_spec._project_auth` strips any spec-authored
    value, so the flag can only ever be the computed one. Absent on list
    items.
    """

    platform_ready: bool | None = Field(
        default=None,
        description=(
            "True when every contract-required platform input is provisioned "
            "for this connector (detail GET only; presence-only check). The "
            "FE gates the OAuth connect button on it."
        ),
    )


class ConnectorCatalogItem(ResolvedConnector):
    """One connector as returned by the catalog API — PRIVATE
    `connector-catalog-item` contract.

    The `data` payload of `GET /connectors/{id}` and the item shape of
    `GET /connectors`: the `client_shape()` sidecar projection plus the two
    handler-stamped fields (`active` on list items, `auth.platform_ready`
    on the detail GET). `tests/unit/test_read_contract_projections.py`
    pins the field delta over `ResolvedConnector` to exactly `{active}` so
    the catalog contract cannot silently fork from the sidecar projection.
    """

    auth: ConnectorCatalogAuth | None = Field(
        default=None,
        description="Auth block narrowed to `{type, test}` plus `platform_ready`.",
    )
    active: bool | None = Field(
        default=None,
        description=(
            "Handler-derived availability flag on list items (currently "
            "always true for every cataloged connector). Absent on the "
            "detail GET."
        ),
    )


class CorruptedConnectorPlaceholder(CorruptedPlaceholderBase):
    """Per-row degrade marker for nonconforming catalog rows.

    The catalog listing replaces a row that fails `ConnectorCatalogItem`
    validation with this closed shape (and logs the defect server-side) so
    one corrupt registry row cannot take down the whole connector catalog.
    The single-connector GET never returns it — it fails loud with a 500
    instead.
    """

    connector_id: str | None = Field(
        default=None, description="Registry connector identifier when readable, else omitted."
    )


# Published union for the PRIVATE `connector-catalog-item` contract. The
# closed, discriminated placeholder variant comes first so tolerant-union
# consumers match it before the open item shape.
ConnectorCatalogItemRecord = CorruptedConnectorPlaceholder | ConnectorCatalogItem


class ConnectorPublicCatalogItem(BaseModel):
    """One connector as returned by the PUBLIC catalog API
    (`GET /connectors_public`) — PRIVATE `connector-public-catalog-item`
    contract.

    The `connector_spec.public_catalog_shape()` projection a marketing/landing
    site renders pre-login: the shared catalog core (identity + BaseConnector
    public surface, `auth` narrowed to `{type, test}`) plus the registry
    release-metadata signals `published_at`, `license`, and the API `endpoints`
    capability summary. No legacy `connector_name`/`connector_type` aliases and
    no server-side `transport_defaults`/`resource_discovery`; and none of the
    client-catalog handler stamps (`active`, `auth.platform_ready`).

    Deliberately tolerant (every field optional, `extra="ignore"`) for the same
    reason as `ResolvedConnector`: the projection is built from a thin DDB row
    merged with an S3 spec that can degrade to thin-row-only.

    `tests/unit/test_read_contract_projections.py` pins this model's field set
    == `connector_spec._PUBLIC_CATALOG_FIELDS` so the contract and the
    projection cannot drift apart.
    """

    model_config = ConfigDict(extra="ignore")

    connector_id: str | None = Field(
        default=None, description="Registry connector identifier (DDB key)."
    )
    slug: str | None = Field(default=None, description="Authored connector slug.")
    display_name: str | None = Field(default=None, description="User-facing connector name.")
    description: str | None = Field(default=None, description="User-facing summary.")
    documentation_url: str | None = Field(
        default=None, description="Connector documentation link."
    )
    tags: list[str] | None = Field(default=None, description="Catalog grouping labels.")
    version: str | None = Field(
        default=None, description="Connector release semver (spec wins over thin row)."
    )
    kind: str | None = Field(
        default=None, description="BaseConnector `kind` discriminator identifying the connector's resource/runtime family."
    )
    connection_contract: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Connector connection contract (inputs / post_auth_outputs / "
            "required_for_activation), passed through verbatim with synthesised "
            "`ui.options` for enum-only inputs. Authored shape is pinned by the "
            "PUBLIC connector contract."
        ),
    )
    auth: ResolvedConnectorAuth | None = Field(
        default=None, description="Auth block narrowed to `{type, test}`."
    )
    transports: dict[str, Any] | None = Field(
        default=None, description="Named transport definitions (verbatim)."
    )
    published_at: str | None = Field(
        default=None, description="Registry release timestamp (ISO 8601)."
    )
    license: str | None = Field(
        default=None,
        description="SPDX license identifier detected from the connector repo, when present.",
    )
    endpoints: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Landing-page summary of the connector's API endpoints (API "
            "connectors only): per-endpoint `endpoint_id`, `display_name`, "
            "`operations`, `read_sync_modes`."
        ),
    )


class CorruptedConnectorPublicCatalogPlaceholder(CorruptedPlaceholderBase):
    """Per-row degrade marker for nonconforming public-catalog rows.

    Mirrors `CorruptedConnectorPlaceholder`: the public catalog listing
    replaces a row that fails `ConnectorPublicCatalogItem` validation with this
    closed shape (and logs the defect server-side) so one corrupt registry row
    cannot take down the whole public catalog.
    """

    connector_id: str | None = Field(
        default=None, description="Registry connector identifier when readable, else omitted."
    )


# Published union for the PRIVATE `connector-public-catalog-item` contract.
# Closed, discriminated placeholder variant first so tolerant-union consumers
# match it before the open item shape.
ConnectorPublicCatalogItemRecord = (
    CorruptedConnectorPublicCatalogPlaceholder | ConnectorPublicCatalogItem
)
