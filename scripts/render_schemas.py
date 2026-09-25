#!/usr/bin/env python3
"""Render and publish versioned JSON Schema documents for Analitiq contracts.

Source of truth: Pydantic models in `analitiq.contracts.*`. A contract change
edits the models only; the committed tree changes in a schema release alone.
`release` renders every resource, has `schema-bump` (the org's shared tool)
decide the bump of each one that changed, and writes the new versions with a
bump record per bump under `schema-bumps/<resource>/<version>.json`. The
org's reusable `schema-release.yml` workflow runs it on every push to main
and keeps one release PR open with the result. A committed
`schema-bumps/<resource>/override.json` (`{"bump", "reason"}`) overrides the
models' bump of that resource in either direction; the release records it and
deletes the file.

Output trees:
    Rendered into the committed tree, uploaded to the serving bucket behind
    schemas.<domain> by `.github/workflows/schemas-publish.yml` (the bucket
    and CDN live in the infra repo's Terraform):
        schemas/<resource>/{X.Y.Z}.json   (immutable per version)
        schemas/<resource>/latest.json     (mutable; mirrors current X.Y.Z)
        schemas/<resource>/index.json       (manifest: latest + versions)
        schemas/arrow-types.json        (mutable; generated from the vendored
                                             engine grammar)
        schemas/contracts-version.json      (mutable; the analitiq-contract-models
                                             version the tree was released from)
    Every file above is written by `release` alone, which re-renders both
    versionless documents on every run.

Resources are declared in the `RESOURCES` registry below. Adding a schema is one
entry there; the next release publishes it at 1.0.0.

Subcommands:
    release     Publish every resource whose render differs from its
                latest.json: decide the bump, write {version}.json +
                latest.json + index.json + the bump record; then re-render
                arrow-types.json and the contracts-version.json stamp. Needs
                OPENROUTER_API_KEY when a released resource changed.
    check       Exit 1 unless the committed tree is self-consistent: latest.json
                mirrors the highest pinned version, index.json lists the pinned
                versions, every bump record justifies the versions it names,
                every override is one the release would apply, and the
                versionless documents have their render's shape (offline).
                `--released` also requires the models, the grammar and the
                contract-models version to render exactly that tree, as they do
                on a release PR.
    release-guard
                Read changed paths from stdin; exit 1 on any that only the
                release may change (CI runs it on every other PR).
    list        Print registered resource names (one per line) — used by CI.
    document-schemas
                Render analitiq/contracts/document_schemas.json — the resources
                whose root model declares `$schema`, loaded by the
                single-document request (covered by the full `check`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
import inspect
import types
import typing
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable
from typing import Any, Callable, Literal

REPO_ROOT = Path(__file__).resolve().parent.parent
# The authored public contract models, published as `analitiq-contract-models`.
# Public schemas render from these and ONLY these: every versioned resource is
# a `RESOURCES` entry, and `Resource.__post_init__` asserts a registered model
# tree never leaves `analitiq.contracts`.
CONTRACTS_SRC = REPO_ROOT / "packages" / "contract-models" / "src"
sys.path.insert(0, str(CONTRACTS_SRC))

from schema_bump import cascade  # noqa: E402
from schema_bump.diff import diff  # noqa: E402
from schema_bump.record import decide_record, record_problem  # noqa: E402
from schema_bump.semver import parse_semver  # noqa: E402

SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
# `DOMAIN` selects the host stamped into every `$id`. Set it BEFORE the contract
# imports below: `analitiq.contracts.shared.common` reads `os.environ["DOMAIN"]`
# at module load and raises KeyError without it.
#
# A non-default DOMAIN renders different `$id`s than the committed tree, so
# `check` would report every resource stale without saying why. Refuse instead.
_DEFAULT_DOMAIN = "analitiq.ai"
os.environ.setdefault("DOMAIN", _DEFAULT_DOMAIN)
if os.environ["DOMAIN"] != _DEFAULT_DOMAIN:
    raise SystemExit(
        f"DOMAIN={os.environ['DOMAIN']!r} is set in the environment, but the "
        f"committed schemas are rendered for {_DEFAULT_DOMAIN}. Unset it (or run "
        "in a clean shell) — otherwise every resource reports stale.")

from pydantic import BaseModel, TypeAdapter, ValidationError  # noqa: E402
from analitiq.contracts.shared.common import SCHEMA_BASE_URL, SLUG_PATTERN, schema_url_for  # noqa: E402

#: The `$id` host, owned by the contract package.
CANONICAL_BASE = SCHEMA_BASE_URL

from analitiq.contracts.connection import ConnectionInput  # noqa: E402
from analitiq.contracts.credentials_file import CredentialsFile  # noqa: E402
from analitiq.contracts.connector import Connector  # noqa: E402
from analitiq.contracts.endpoints import (  # noqa: E402
    ARROW_TYPE_PATTERN,
    WRITE_MODES,
    ApiEndpointDoc,
    DatabaseEndpointDoc,
)
from analitiq.contracts.shared.json_schema import (  # noqa: E402
    JSON_SCHEMA_LIST_OF_SCHEMA_KEYS,
    JSON_SCHEMA_SINGLE_SCHEMA_KEYS,
    JSON_SCHEMA_SUBSCHEMA_KEYS,
)
from analitiq.contracts.connection_package import ConnectionPackage  # noqa: E402
from analitiq.contracts.connector_package import ConnectorPackage  # noqa: E402
from analitiq.contracts.pipeline_manifest import PipelineManifest  # noqa: E402
from analitiq.contracts.pipeline_package import PipelinePackage  # noqa: E402
from analitiq.contracts.workspace import Workspace  # noqa: E402
from analitiq.contracts.type_map import ResolveTypesRequest, TypeMapDoc  # noqa: E402
from analitiq.contracts.pipelines.config import PipelineInput  # noqa: E402
from analitiq.contracts.pipelines.data_sync import (  # noqa: E402
    PipelineRunAcceptedResponse,
    PipelineRunRequest,
    PipelineRunStatusResponse,
    PipelineTerminateResponse,
)
from analitiq.contracts.stream import StreamInput  # noqa: E402
from analitiq.contracts.validation_requests import (  # noqa: E402
    DOCUMENT_SCHEMAS_KEY,
    DOCUMENT_SCHEMAS_PATH,
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
    ValidateWorkspaceRequest,
)
SCHEMAS_ROOT = REPO_ROOT / "schemas"
# Outside `schemas/`, so the publish never ships a record.
BUMP_RECORDS_ROOT = REPO_ROOT / "schema-bumps"
OVERRIDE_NAME = "override.json"
FIRST_VERSION = "1.0.0"

VERSIONED_FILENAME_RE = re.compile(r"^(\d+\.\d+\.\d+)\.json$")



# ---------------------------------------------------------------------------
# Resource registry
# ---------------------------------------------------------------------------


# The published public contract package. Everything a rendered schema is built
# from must live here — enforced by `Resource.__post_init__`.
_PUBLIC_MODEL_PKG = "analitiq.contracts"
# `_model_tree` also surfaces pydantic's own synthetic classes — a
# `TypeAdapter(RootModel[...])` root reports `pydantic.root_model`. Those are
# machinery, not contract models, and carry no vocabulary of ours.
_MODEL_PKG_ALLOWLIST = (_PUBLIC_MODEL_PKG, "pydantic")


def _module_allowed(module: str) -> bool:
    """Exact match or a dotted child — never a bare prefix.

    `startswith(("analitiq.contracts", "pydantic"))` would also admit
    `analitiq.contracts_internal` and `pydantic_extra_types.*`, neither of which
    this project owns.
    """
    return any(module == pkg or module.startswith(pkg + ".")
               for pkg in _MODEL_PKG_ALLOWLIST)


def _model_tree(root: Any) -> set[type[BaseModel]]:
    """Every Pydantic model reachable from a schema's root type.

    Walks fields AND unwraps typing generics (`list[X]`, `X | None`,
    `Annotated[X, ...]`, `RootModel[X]`), because a root is often not itself a
    model — `TypeAdapter(list[ProductPriceItem])` would otherwise report an
    empty tree and silently derive as public.
    """
    seen: set[type[BaseModel]] = set()

    def walk(node: Any) -> None:
        if inspect.isclass(node) and issubclass(node, BaseModel):
            if node in seen:
                return
            seen.add(node)
            for field in node.model_fields.values():
                walk(field.annotation)
            # Computed fields live outside `model_fields` but DO render under
            # mode="serialization" — which is exactly the surface the Data Sync
            # leak came through. Walking only `model_fields` would let a
            # computed_field returning a private model reach `$defs` unseen.
            for computed in node.model_computed_fields.values():
                walk(computed.return_type)
            return
        for arg in typing.get_args(node):
            walk(arg)

    walk(root)
    return seen


@dataclass(frozen=True)
class Resource:
    """A single schema published from a Pydantic root model.

    Attributes:
        name: URL slug under `/schemas/` (also the folder name on disk).
        title: Title stamped into the schema document.
        description: One-paragraph blurb stamped into the schema document.
        adapter: TypeAdapter wrapping the root model (or discriminated union).
            This also DECIDES the audience — see `private` below. There is no
            `visibility` field: the model's home is the fact, and stating it
            twice is how the two drift.
        mode: Pydantic JSON-Schema generation mode. Authored *input* contracts
            use "validation" (the default). *Output* wire contracts (e.g. push
            messages) use "serialization" so computed fields and the serialized
            shape are reflected in the published schema.
        post_process: Optional in-place mutator applied to the rendered body
            before stamping. Use this for surgery JSON-Schema generators don't
            do natively (e.g. forcing a discriminator field into `required[]`).
        source_paths: Repository paths associated with this resource, printed
            in the union `list --paths` reports (this script's own
            subcommand). Nothing in this repo invokes `list --paths` today —
            no CI workflow reads its output.
    """

    name: str
    title: str
    description: str
    adapter: TypeAdapter
    mode: Literal["validation", "serialization"] = "validation"
    post_process: Callable[[dict[str, Any]], None] | None = None
    source_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Every registered model must live in `analitiq.contracts`.

        The infra renderer DERIVED audience with the inverse of this check
        (`private` = does any reachable model come from `alq.`), because it
        served both trees. This copy renders only the public tree, so the same
        fact becomes an invariant: a resource whose model tree leaves the public
        contract package is a bug, not a different output path.

        Kept as a structural check rather than a declared flag for the reason
        the original gave — a declared field is a second statement of a fact the
        code already makes, and two statements drift. It drifted exactly this
        way before: the public Data Sync schemas were built from the frontend's
        payload classes and published "AWS Batch job ID" to the CDN. The
        declaration said public; the models said otherwise; nothing reconciled
        them.

        So making a schema public is not a keyword: it is moving its models into
        the public package, which is a reviewable, structural act.
        """
        # skipcq: PYL-W0212 — pydantic has no public accessor for a TypeAdapter's
        # root type; tests/schemas/test_render_schemas.py pins this usage.
        tree = _model_tree(self.adapter._type)
        if not tree:
            # An empty tree is indistinguishable from a clean one, so the check
            # below would pass without inspecting anything. `_model_tree`'s own
            # docstring names this hazard for `list[X]` roots; make the whole
            # class of it loud instead of patching instances. No registered
            # resource legitimately renders from zero models.
            raise ValueError(
                f"resource {self.name!r} has an empty model tree, so the "
                f"audience check cannot run (root: {self.adapter._type!r}). "  # skipcq: PYL-W0212
                "A registered resource must render from at least one model."
            )
        leaked = sorted(
            f"{m.__module__}.{m.__qualname__}"
            for m in tree
            if not _module_allowed(m.__module__)
        )
        if leaked:
            raise ValueError(
                f"resource {self.name!r} reaches models outside "
                f"{_PUBLIC_MODEL_PKG!r}: {leaked}. Public schemas render only "
                "from the published contract package; move the model there or "
                "drop the resource."
            )

    def dir(self) -> Path:
        return SCHEMAS_ROOT / self.name

    def base_url(self) -> str:
        return f"{CANONICAL_BASE}/{self.name}"


def _enforce_discriminator_required(schema: dict[str, Any]) -> None:
    """Add the discriminator field to each subclass's `required[]`.

    Pydantic excludes fields with defaults from `required`, but for an external
    JSON Schema validator the discriminated union is meaningless if the
    discriminator isn't required on each branch — without it, a payload missing
    the discriminator can match multiple `oneOf` branches.

    Also applies the same rule to the schema root: when a concrete (non-union)
    model is the published root and its discriminator field has a default, that
    field would otherwise be omitted from the root `required[]`, letting
    consumers submit kind-less payloads against the kind-specific schema.
    """
    # Walk every `$def` entry (covers union members).
    for defn in schema.get("$defs", {}).values():
        if isinstance(defn, dict):
            _add_const_props_to_required(defn)
    # Apply at the root too (covers concrete-root schemas).
    _add_const_props_to_required(schema)


def _add_const_props_to_required(node: dict[str, Any]) -> None:
    props = node.get("properties", {}) or {}
    for field_name, field_schema in props.items():
        if isinstance(field_schema, dict) and "const" in field_schema:
            required = node.setdefault("required", [])
            if field_name not in required:
                required.append(field_name)
                required.sort()


# Conditional `model_validator` rules that don't surface to JSON Schema by
# default. We mirror them as `allOf`/`oneOf` constraints on the relevant `$def`
# so external (JSON-Schema-only) consumers reject the same payloads our Pydantic
# validators reject. Spec: §Post-Auth Outputs, §Resource Discovery,
# §Connection Inputs (secret iff storage='secrets').
_CONDITIONAL_RULES: dict[str, list[dict[str, Any]]] = {
    "PostAuthOutput": [
        {
            "oneOf": [
                {
                    "properties": {
                        "mode": {"const": "user_selection"},
                        "storage": {"const": "connection.selections"},
                    },
                    "required": ["options_request"],
                    "not": {"required": ["discovery_request"]},
                },
                {
                    "properties": {
                        "mode": {"const": "auto_discovery"},
                        "storage": {"enum": ["connection.discovered", "secrets"]},
                    },
                    "required": ["discovery_request"],
                    "not": {
                        "anyOf": [
                            {"required": ["options_request"]},
                            {"required": ["options_path"]},
                            {"required": ["label_path"]},
                        ]
                    },
                },
            ]
        }
    ],
    "ResourceDiscoveryImplementation": [
        {
            "oneOf": [
                {
                    "properties": {"type": {"const": "connector_plugin"}},
                    "required": ["entrypoint"],
                },
                {
                    "properties": {"type": {"const": "builtin"}},
                    "not": {"required": ["entrypoint"]},
                },
            ]
        }
    ],
    "ConnectionContractInput": [
        {
            "oneOf": [
                {
                    "properties": {
                        "storage": {"const": "secrets"},
                        "secret": {"const": True},
                    },
                    "required": ["secret"],
                },
                {
                    "properties": {"storage": {"const": "connection.parameters"}},
                    "anyOf": [
                        {"not": {"required": ["secret"]}},
                        {"properties": {"secret": {"const": False}}},
                    ],
                },
            ]
        },
        # When `enum` is present it must be a non-empty list (spec: §Connection
        # Inputs — `enum` is the authoritative allowed-value list).
        {
            "if": {"required": ["enum"]},
            "then": {"properties": {"enum": {"minItems": 1}}},
        },
    ],
}


def _encode_conditional_rules(schema: dict[str, Any]) -> None:
    """Mirror `model_validator` constraints as JSON-Schema-level `allOf`/`oneOf`.

    Hard-fails when a registered `$def` is missing from the rendered schema —
    a silent skip would let a model-class rename quietly drop the conditional
    constraint from the published artifact, weakening the contract for
    external validators with no CI signal.
    """
    defs = schema.get("$defs", {})
    for def_name, constraints in _CONDITIONAL_RULES.items():
        defn = defs.get(def_name)
        if not isinstance(defn, dict):
            raise RuntimeError(
                f"_encode_conditional_rules: $def {def_name!r} is missing from the "
                "rendered schema. The Pydantic class was renamed/removed but "
                "_CONDITIONAL_RULES was not updated; external consumers would "
                "silently lose this conditional rule."
            )
        all_of = defn.setdefault("allOf", [])
        for c in constraints:
            if c not in all_of:
                all_of.append(c)


# The AUTHORED public contract models — their own source package, published to
# PyPI as `analitiq-contract-models`. That tree IS the published package.
_CONTRACTS_PREFIX = "packages/contract-models/src/analitiq/contracts"

def _connector_post_process(schema: dict[str, Any]) -> None:
    _enforce_discriminator_required(schema)
    _encode_conditional_rules(schema)
    _annotate_transport_inheritance(schema)


# Enforce canonical arrow_type inside API response.schema / input.schema.
# Pydantic models these as opaque `dict[str, Any]`, so the rendered schema treats
# them as `additionalProperties: true` blobs with no rules on `arrow_type`. Inject
# a recursive `$def` so external validators reject bare parameterized forms
# (`Timestamp`, `Decimal128`, …) at author time, matching the runtime walker on
# ResponseExtraction / WriteInput.
_JSON_SCHEMA_NODE_REF: dict[str, Any] = {"$ref": "#/$defs/JsonSchemaPropertyNode"}


def _json_schema_node_properties() -> dict[str, Any]:
    """The node's constrained keywords, DERIVED from the contract's own sets.

    The vocabulary has one owner, `analitiq.contracts.shared.json_schema`, and
    so does the descent over it — `walk_structural_positions`, which every
    check that reaches each node of an embedded schema loops over. This
    renderer imports the sets instead, because it declares the recursion in the
    published schema rather than visiting a node. Nothing here restates the
    vocabulary, so a keyword landing in the sets reaches this node and its
    description (below) together.
    """
    properties: dict[str, Any] = {
        "arrow_type": {"type": "string", "pattern": ARROW_TYPE_PATTERN},
        "native_type": {"type": "string"},
    }
    for key in JSON_SCHEMA_SUBSCHEMA_KEYS:
        properties[key] = {"type": "object", "additionalProperties": _JSON_SCHEMA_NODE_REF}
    for key in JSON_SCHEMA_LIST_OF_SCHEMA_KEYS:
        properties[key] = {"type": "array", "items": _JSON_SCHEMA_NODE_REF}
    for key in JSON_SCHEMA_SINGLE_SCHEMA_KEYS:
        # `additionalProperties` also takes the boolean short-form, and the
        # structural walk descends into it only when it is a dict.
        properties[key] = (
            {"anyOf": [{"type": "boolean"}, _JSON_SCHEMA_NODE_REF]}
            if key == "additionalProperties"
            else _JSON_SCHEMA_NODE_REF
        )
    return properties


def _json_schema_node_description() -> str:
    """The prose, derived from the same sets, so it cannot contradict them."""
    def names(keys: "frozenset[str]") -> str:
        return ", ".join(f"`{k}`" for k in sorted(keys))

    return (
        "JSON Schema Draft 2020-12 node carrying the Analitiq `native_type` / "
        "`arrow_type` annotations on typed field schemas. Recursive: every "
        "JSON Schema keyword whose value is itself a schema (or a map/list of "
        "schemas) is constrained back to this node. Specifically: "
        f"{names(JSON_SCHEMA_SUBSCHEMA_KEYS)} (maps); "
        f"{names(JSON_SCHEMA_LIST_OF_SCHEMA_KEYS)} (lists); "
        f"{names(JSON_SCHEMA_SINGLE_SCHEMA_KEYS)} (single). "
        "A canonical `arrow_type` "
        "must carry parameters when the type requires them; `native_type` and "
        "`arrow_type` are paired."
    )


_JSON_SCHEMA_PROPERTY_NODE_DEF: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "description": _json_schema_node_description(),
    "properties": _json_schema_node_properties(),
    "dependentRequired": {
        "arrow_type": ["native_type"],
        "native_type": ["arrow_type"],
    },
}


_API_ENDPOINT_SCHEMA_HOLDER_DEFS: tuple[str, ...] = ("ResponseExtraction", "WriteInput")


def _api_endpoint_post_process(schema: dict[str, Any]) -> None:
    """Constrain `response.schema` / `input.schema` to carry canonical arrow_type.

    Pydantic emits both fields as opaque object schemas. We swap them for a
    `$ref` to a recursive `JsonSchemaPropertyNode` `$def` that walks the embedded
    JSON Schema and rejects bare-parameterized arrow_type values and unpaired
    native_type/arrow_type leaves — mirroring the Pydantic-side runtime walker
    in `_validate_arrow_type_in_json_schema`.
    """
    _enforce_discriminator_required(schema)

    defs = schema.setdefault("$defs", {})
    defs["JsonSchemaPropertyNode"] = _JSON_SCHEMA_PROPERTY_NODE_DEF

    for def_name in _API_ENDPOINT_SCHEMA_HOLDER_DEFS:
        holder = defs.get(def_name)
        if not isinstance(holder, dict):
            raise RuntimeError(
                f"_api_endpoint_post_process: expected $def {def_name!r} in the "
                "rendered api-endpoint schema. The Pydantic model was renamed "
                "or removed; the JsonSchemaPropertyNode constraint cannot be "
                "wired up. Update _API_ENDPOINT_SCHEMA_HOLDER_DEFS."
            )
        props = holder.get("properties")
        if not isinstance(props, dict) or "schema" not in props:
            raise RuntimeError(
                f"_api_endpoint_post_process: $def {def_name!r} lost its "
                "`schema` property; the api-endpoint JSON-Schema-body shape "
                "changed and the canonical arrow_type constraint cannot be "
                "attached."
            )
        # Preserve the title/description rendered from the Pydantic Field so the
        # public schema documents the slot; replace the body with a $ref to the
        # recursive node so canonical arrow_type validation kicks in.
        original = props["schema"]
        replacement: dict[str, Any] = {
            "$ref": "#/$defs/JsonSchemaPropertyNode",
        }
        for carry_key in ("title", "description"):
            if carry_key in original:
                replacement[carry_key] = original[carry_key]
        props["schema"] = replacement

    _encode_write_mode_conflict_keys_rule(defs)


def _encode_write_mode_conflict_keys_rule(defs: dict[str, Any]) -> None:
    """Publish the per-mode `conflict_keys` rule in the JSON Schema.

    The Pydantic `Operations._conflict_keys_by_mode` validator requires
    `conflict_keys` on the `upsert` write mode and forbids it on every other
    mode, but model validators are not emitted into the rendered schema — so a
    connector author validating an authored document against the published
    `api-endpoint` contract would not see the rule. Encode it structurally:
    `operations.write` renders as `anyOf[{object}, {null}]` with the WriteOperation
    `$ref` under `additionalProperties`; pin per-mode `properties` that keep the
    `$ref` (via `allOf`) and add the `required`/`not-required` constraint.
    """
    operations = defs.get("Operations")
    if not isinstance(operations, dict):
        raise RuntimeError(
            "_encode_write_mode_conflict_keys_rule: expected $def Operations; "
            "the model was renamed/reshaped."
        )
    write_prop = operations.get("properties", {}).get("write")
    branches = write_prop.get("anyOf") if isinstance(write_prop, dict) else None
    obj_branch = next(
        (b for b in branches if isinstance(b, dict) and b.get("type") == "object"),
        None,
    ) if isinstance(branches, list) else None
    if obj_branch is None:
        raise RuntimeError(
            "_encode_write_mode_conflict_keys_rule: operations.write object branch "
            "not found (expected anyOf[{type:object}, {type:null}]); the model "
            "reshaped — update this post-processor."
        )
    write_op_ref = {"$ref": "#/$defs/WriteOperation"}
    # The model rule (`Operations._conflict_keys_by_mode`) is a falsy check:
    # `upsert` requires a truthy `conflict_keys`, every other mode forbids a
    # truthy one (a `null`/absent value is fine on a non-upsert mode). Mirror
    # that exactly — `required`/`not-required` alone would diverge on the
    # field's nullable `default: null`:
    #   - upsert: pin the value to a non-empty array (so `null` is rejected, not
    #     just satisfied by key presence).
    #   - every other mode: forbid only a *non-null* value (so `null`/absent
    #     pass, matching the model, while a real key is rejected).
    # The non-upsert pin to `type: null` is satisfiable only because the base
    # `WriteOperation.conflict_keys` $ref renders as `anyOf[array, null]`; if that
    # field is ever narrowed to array-only, those branches become unsatisfiable.
    # Built by iterating WRITE_MODES, never by listing the modes here. The
    # hand-written version enumerated `insert`/`upsert`, so widening the
    # vocabulary updated the derived `propertyNames.enum` while this mirror
    # silently kept its two branches — and the published schema then ACCEPTED
    # `truncate_insert.conflict_keys`, which the model rejects. A rendered
    # mirror of a model rule has to be derived from the same vocabulary the
    # model reads, or it is exactly the drift surface the repo forbids.
    obj_branch["properties"] = {
        mode: {"allOf": [write_op_ref, (
            {
                "required": ["conflict_keys"],
                "properties": {"conflict_keys": {"type": "array", "minItems": 1}},
            }
            if mode == "upsert"
            else {"properties": {"conflict_keys": {"type": "null"}}}
        )]}
        for mode in WRITE_MODES
    }


_CONNECTOR_DOCUMENT_DEF_NAMES: frozenset[str] = frozenset({
    "ApiConnector",
    "DatabaseConnector",
    "NosqlConnector",
    "DocumentConnector",
    "FileConnector",
    "S3Connector",
    "StdoutConnector",
})


def _annotate_transport_inheritance(schema: dict[str, Any]) -> None:
    """Document the runtime-only `transport_type` inheritance from defaults.

    The Pydantic models fill `transport_type` on `transports.<name>` entries
    from `transport_defaults.transport_type` (see `ConnectorBase._inherit_transport_type`).
    The discriminated-union JSON Schema cannot express that contextual
    defaulting, so external validators reject entries that omit
    `transport_type`. Authors targeting this published schema must declare
    `transport_type` per entry.

    Scoped to the known connector-document `$defs` so the note never lands on
    an unrelated property that happens to be named `transports`. Hard-fails
    when *any* registered `$def` is missing (rename guard) or when *none* of
    them carry a `transports` property (model-shape guard). Tracking matches
    instead of stamps means the function is idempotent: a second call on an
    already-annotated schema is a no-op rather than a misleading raise.
    """
    note = (
        "Each transport entry must declare its own `transport_type`. The "
        "Pydantic runtime accepts entries that omit it and inherits the value "
        "from `transport_defaults.transport_type`, but this JSON Schema does "
        "not — declare `transport_type` per entry for portable validation."
    )
    defs = schema.get("$defs", {})
    missing = sorted(_CONNECTOR_DOCUMENT_DEF_NAMES - set(defs))
    if missing:
        raise RuntimeError(
            "_annotate_transport_inheritance: registered connector-document "
            f"$defs {missing!r} were not found in the rendered schema. The "
            "model classes were renamed/removed but _CONNECTOR_DOCUMENT_DEF_NAMES "
            "was not updated; the runtime-only transport_type inheritance "
            "note would be silently dropped from the published schema."
        )

    non_dict = sorted(d for d in _CONNECTOR_DOCUMENT_DEF_NAMES if not isinstance(defs[d], dict))
    if non_dict:
        raise RuntimeError(
            f"_annotate_transport_inheritance: registered $defs {non_dict!r} "
            "are not dict schemas (likely a `$ref`-only entry or a list). "
            "The annotation cannot be attached; investigate the renderer "
            "rather than relying on a fallthrough."
        )

    matched: list[str] = []
    missing_props: list[str] = []
    has_props_no_transports: list[str] = []
    for def_name in _CONNECTOR_DOCUMENT_DEF_NAMES:
        defn = defs[def_name]
        if "properties" not in defn:
            missing_props.append(def_name)
            continue
        props = defn["properties"]
        if not isinstance(props, dict):
            raise RuntimeError(
                f"_annotate_transport_inheritance: $def {def_name!r} has a "
                f"non-dict `properties` field (got {type(props).__name__}). "
                "The renderer produced an unexpected shape; investigate "
                "rather than relying on a fallthrough."
            )
        transports = props.get("transports")
        if transports is None:
            has_props_no_transports.append(def_name)
            continue
        if not isinstance(transports, dict):
            raise RuntimeError(
                f"_annotate_transport_inheritance: $def {def_name!r} has a "
                f"non-dict `transports` property (got {type(transports).__name__}). "
                "The annotation cannot attach to a `$ref`-only or scalar "
                "schema entry; investigate rather than relying on a fallthrough."
            )
        matched.append(def_name)
        # Always set the canonical note. `$comment` is owned by this
        # function on the transports property; not skipping when a
        # `$comment` is already present means a pre-seeded value (from
        # a stale rendered file or an external tool) gets normalized
        # to the current canonical text instead of silently passing.
        transports["$comment"] = note
    if not matched:
        raise RuntimeError(
            "_annotate_transport_inheritance: none of the registered "
            f"connector-document $defs ({sorted(_CONNECTOR_DOCUMENT_DEF_NAMES)!r}) "
            "expose a `transports` property (defs without `properties`: "
            f"{sorted(missing_props)!r}; defs with `properties` but no "
            f"`transports` key: {sorted(has_props_no_transports)!r}). The "
            "connector model shape changed in a way that drops the transports "
            "map; the runtime-only transport_type inheritance note has "
            "nothing to attach to."
        )


def _collapse_nullable_anyof(node: Any) -> None:
    """Collapse `anyOf: [X, {"type": "null"}]` to X and drop `default: null`.

    For wire contracts serialized with `exclude_none=True`: an absent field is
    *omitted*, never null, so the published schema must not advertise null —
    Pydantic's `field: T | None = None` idiom renders nullable, but null never
    reaches the wire. Recurses through the whole document.
    """
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list) and {"type": "null"} in any_of:
            remaining = [b for b in any_of if b != {"type": "null"}]
            if len(remaining) == 1 and isinstance(remaining[0], dict):
                node.pop("anyOf")
                # Merge the surviving branch; node-level siblings (title,
                # description) win over branch keys.
                for key, value in remaining[0].items():
                    node.setdefault(key, value)
            else:
                node["anyOf"] = remaining
        if "default" in node and node["default"] is None:
            del node["default"]
        for value in node.values():
            _collapse_nullable_anyof(value)
    elif isinstance(node, list):
        for item in node:
            _collapse_nullable_anyof(item)


def _normalize_database_object_namespaces(schema: dict[str, Any]) -> None:
    """Mirror `DatabaseObject._reject_explicit_null_namespaces` into the schema.

    `catalog` and `schema` are modeled `str | None`, so Pydantic renders a
    nullable `anyOf`, but the runtime validator REJECTS an explicit null for them
    (they are omit-when-absent, never null). Drop the null branch (and any
    `default: null`) so a client validating against the published schema cannot
    pass `{schema: null}` only to be 400'd by `validate_stream_input`. `name` is
    required, and `object_type` is legitimately nullable (the validator does not
    reject its null), so both are left untouched.

    Runs for every resource (self-guarding: a no-op when the schema does not
    embed `DatabaseObject`), and is idempotent — schemas whose own
    post-processor already collapsed nullables (e.g. the read contracts) simply
    have nothing left to drop. Keeps the published `DatabaseObject` perfectly
    aligned with the model wherever it appears.
    """
    defn = schema.get("$defs", {}).get("DatabaseObject")
    if not isinstance(defn, dict):
        return
    props = defn.get("properties")
    if not isinstance(props, dict):
        return
    for name in ("catalog", "schema"):
        prop = props.get(name)
        if not isinstance(prop, dict):
            continue
        any_of = prop.get("anyOf")
        if not isinstance(any_of, list) or {"type": "null"} not in any_of:
            continue  # already non-null (collapsed) — nothing to drop
        non_null = [b for b in any_of if b != {"type": "null"}]
        if len(non_null) == 1 and isinstance(non_null[0], dict):
            merged = dict(non_null[0])
            for carry in ("title", "description"):
                if carry in prop:
                    merged.setdefault(carry, prop[carry])
            props[name] = merged  # replaces the prop → drops anyOf + default:null
        else:
            prop["anyOf"] = non_null
            prop.pop("default", None)






















def _data_sync_response_post_process(schema: dict[str, Any]) -> None:
    # `success: Literal[True]` carries a `const` — force it into `required[]`
    # so external validators reject a body that omits it. The envelope dumps
    # with `exclude_none=True` (absent `message`/`data` omitted, never null),
    # so the nullable anyOf branches collapse to their non-null shape.
    _enforce_discriminator_required(schema)
    _collapse_nullable_anyof(schema)


def _data_sync_terminate_post_process(schema: dict[str, Any]) -> None:
    # The shared response post-process, plus the documented terminate invariant
    # the type system can't express: an idempotent no-op omits `data` and is
    # "distinguished by `message`", so when `data` is absent `message` MUST be
    # present. pipeline-invoker always sends a message, so this mirrors the
    # runtime; encoding it as a JSON-Schema `if/then` (the same model_validator
    # -> schema mirroring as `_CONDITIONAL_RULES`) lets external validators
    # enforce the promise the contract documents.
    _data_sync_response_post_process(schema)
    schema["if"] = {"not": {"required": ["data"]}}
    schema["then"] = {"required": ["message"]}






RESOURCES: tuple[Resource, ...] = (
    Resource(
        name="connector",
        title="Analitiq Connector",
        description=(
            "Public JSON Schema contract for Analitiq connector documents — "
            "the authored shape used in source control, PR review, and "
            "author-time tooling. `connector_id` is the authored canonical "
            f"identifier (slug pattern `{SLUG_PATTERN}`). Only "
            "`created_at` and `updated_at` are server-managed and absent in "
            "the authored shape. "
            "Source of truth: analitiq.contracts.connector.Connector (Pydantic)."
        ),
        adapter=TypeAdapter(Connector),
        post_process=_connector_post_process,
        source_paths=(f"{_CONTRACTS_PREFIX}/connector.py",),
    ),
    Resource(
        name="connection",
        title="Analitiq Connection",
        description=(
            "Public JSON Schema contract for Analitiq connection documents — "
            "the authored shape used in source control, PR review, and "
            "author-time tooling. Server-managed fields (connection_id, "
            "version, org_id, connector_id, connector_version, auth_state, "
            "created_at, updated_at) are forbidden in the authored shape and "
            "assigned by the connection service on ingest. The persisted-"
            "record shape is internal and not published. "
            "Source of truth: analitiq.contracts.connection.ConnectionInput (Pydantic)."
        ),
        adapter=TypeAdapter(ConnectionInput),
        post_process=_enforce_discriminator_required,
        source_paths=(f"{_CONTRACTS_PREFIX}/connection.py",),
    ),
    Resource(
        name="credentials",
        title="Analitiq Connection Credentials",
        description=(
            "Public JSON Schema contract for a connection's local secrets file "
            "(`credentials.json`) — a flat map of connection-input (or post-auth "
            "output) name to that input's secret value (any JSON type; the engine "
            "string-coerces on read, so prefer strings). A connection document's "
            "`secret_refs.<name>` entry, via the `sidecar:<name>` scheme, resolves "
            "to the value stored here under the same `<name>`. Kept out of source "
            "control; the "
            "shape is published so a plugin author can validate a credentials "
            "template against it. "
            "Source of truth: analitiq.contracts.credentials_file.CredentialsFile (Pydantic)."
        ),
        adapter=TypeAdapter(CredentialsFile),
        source_paths=(f"{_CONTRACTS_PREFIX}/credentials_file.py",),
    ),
    Resource(
        name="api-endpoint",
        title="Analitiq API Endpoint",
        description=(
            "Public JSON Schema contract for API endpoint documents (owned by "
            "connectors with `kind: 'api'`) — authored shape only. "
            "Endpoint documents have no top-level `kind` field; the owning "
            "connector's `kind` selects this schema. Reserved server-managed "
            "fields (endpoint_id, connector_id, connector_version, "
            "connection_id, schema_hash) are forbidden in the authored shape. "
            "The persisted catalog-row shape is internal and not published. "
            "Source of truth: analitiq.contracts.endpoints.ApiEndpointDoc (Pydantic)."
        ),
        adapter=TypeAdapter(ApiEndpointDoc),
        post_process=_api_endpoint_post_process,
        source_paths=(f"{_CONTRACTS_PREFIX}/endpoints.py",),
    ),
    Resource(
        name="database-endpoint",
        title="Analitiq Database Endpoint",
        description=(
            "Public JSON Schema contract for database endpoint documents "
            "(owned by connectors with `kind` in {'database', 'nosql', "
            "'document'}) — authored shape only. Endpoint documents have no "
            "top-level `kind` field; the owning connector's `kind` selects "
            "this schema. Reserved server-managed fields are forbidden in the "
            "authored shape. "
            "Source of truth: analitiq.contracts.endpoints.DatabaseEndpointDoc (Pydantic)."
        ),
        adapter=TypeAdapter(DatabaseEndpointDoc),
        post_process=_enforce_discriminator_required,
        source_paths=(f"{_CONTRACTS_PREFIX}/endpoints.py",),
    ),
    Resource(
        name="type-map",
        title="Analitiq Type Map",
        description=(
            "Public JSON Schema contract for a connector's or connection's "
            "`type-map.json` — a top-level object carrying, keyed by direction, "
            "an ordered, non-empty rule list: `read` rules match a `native_type` "
            "and render an `arrow_type`, `write` rules match an `arrow_type` and "
            "render a `native_type`; order is significant (first match wins) and "
            "at least one direction is present. The full per-rule contract "
            "(RE2 regex, `${name}` capture correspondence, Arrow vocabulary, "
            "schemaless-container handling) lives in the model and is enforced by "
            "the connector validator; this published schema is the structural "
            "projection. Source of truth: "
            "analitiq.contracts.type_map.TypeMapDoc (Pydantic)."
        ),
        adapter=TypeAdapter(TypeMapDoc),
        source_paths=(
            f"{_CONTRACTS_PREFIX}/type_map.py",
        ),
    ),
    Resource(
        name="pipeline",
        title="Analitiq Pipeline",
        description=(
            "Public JSON Schema contract for Analitiq pipeline documents — "
            "the authored shape used in source control, PR review, and "
            "author-time tooling. Server-managed fields (pipeline_id, "
            "version, org_id, created_at, updated_at) are forbidden in the "
            "authored shape and assigned by the pipeline service on ingest. "
            "The persisted-record shape is internal and not published. "
            "Source of truth: analitiq.contracts.pipelines.config.PipelineInput (Pydantic)."
        ),
        adapter=TypeAdapter(PipelineInput),
        source_paths=(f"{_CONTRACTS_PREFIX}/pipelines/config.py",),
    ),
    Resource(
        name="stream",
        title="Analitiq Stream",
        description=(
            "Public JSON Schema contract for Analitiq stream documents — "
            "the authored shape used in source control, PR review, and "
            "author-time tooling. Server-managed fields (stream_id, version, "
            "org_id, created_at, updated_at) are forbidden in the authored "
            "shape and assigned by the stream service on ingest. The "
            "persisted-record shape is internal and not published. "
            "Source of truth: analitiq.contracts.stream.StreamInput (Pydantic)."
        ),
        adapter=TypeAdapter(StreamInput),
        source_paths=(f"{_CONTRACTS_PREFIX}/stream.py",),
    ),
    # ---- Public Data Sync API (rest.<domain>/v1, API-key) ------------------
    # PUBLIC, customer-facing request/response contracts for the API-key Data
    # Sync API. Unlike the internal `pipeline-run-accepted` / `pipeline-run-action`
    # these model the FULL response body so a public consumer can validate
    # an entire HTTP payload directly.
    # terminate data shapes as the private contracts — one source of truth, no
    # drift.
    Resource(
        name="data-sync-run-request",
        title="Analitiq Data Sync API — Pipeline Run Request",
        description=(
            "PUBLIC JSON Schema contract for the request body of the Data Sync "
            "API POST /pipelines/{pipeline_id}/run (rest.<domain>/v1, API-key "
            "auth). The pipeline is taken from the path and the org from the "
            "API key, so the only client-supplied field is the optional "
            "`terminate_existing_sync` flag; an empty body is valid and unknown "
            "keys are rejected. Served at schemas.<domain> for external API-key "
            "consumers."
        ),
        adapter=TypeAdapter(PipelineRunRequest),
        source_paths=(f"{_CONTRACTS_PREFIX}/pipelines/data_sync.py",),
    ),
    Resource(
        name="data-sync-run-accepted",
        title="Analitiq Data Sync API — Pipeline Run Accepted",
        description=(
            "PUBLIC JSON Schema contract for the SUCCESS (202 Accepted) response "
            "body of the Data Sync API POST /pipelines/{pipeline_id}/run — the "
            "canonical `{success, message?, data}` envelope where `data` carries "
            "the run-accepted tracking identifiers (invocation_id, pipeline_id), "
            "always present on a successful accept. The run is dispatched "
            "asynchronously (the Batch job is submitted in the background), so "
            "there is no job_id yet; poll run history by invocation_id for the "
            "final outcome. Error responses use the canonical error envelope and "
            "are documented in the Data Sync OpenAPI rather than here. Absent "
            "optional fields are omitted from the wire, never null. Served at "
            "schemas.<domain> for external API-key consumers."
        ),
        adapter=TypeAdapter(PipelineRunAcceptedResponse),
        mode="serialization",
        post_process=_data_sync_response_post_process,
        source_paths=(
            f"{_CONTRACTS_PREFIX}/pipelines/data_sync.py",
        ),
    ),
    Resource(
        name="data-sync-terminate-response",
        title="Analitiq Data Sync API — Pipeline Terminate Response",
        description=(
            "PUBLIC JSON Schema contract for the SUCCESS response body of the "
            "Data Sync API POST /pipelines/{pipeline_id}/terminate — the "
            "canonical `{success, message?, data?}` envelope. Terminate is "
            "idempotent: `data` (resolved pipeline_id, plus job_id when a "
            "running job was stopped) is present when a pipeline was acted on "
            "and omitted on no-op outcomes, which are distinguished by "
            "`message`. Error responses use the canonical error envelope and "
            "are documented in the Data Sync OpenAPI rather than here. Absent "
            "optional fields are omitted from the wire, never null. Served at "
            "schemas.<domain> for external API-key consumers."
        ),
        adapter=TypeAdapter(PipelineTerminateResponse),
        mode="serialization",
        post_process=_data_sync_terminate_post_process,
        source_paths=(
            f"{_CONTRACTS_PREFIX}/pipelines/data_sync.py",
        ),
    ),
    Resource(
        name="data-sync-run-status",
        title="Analitiq Data Sync API — Pipeline Run Status",
        description=(
            "PUBLIC JSON Schema contract for the SUCCESS (200) response body of "
            "the Data Sync API GET /pipelines/{pipeline_id}/runs/{invocation_id} "
            "(rest.<domain>/v1, API-key auth) — the canonical "
            "`{success, message?, data}` envelope where `data` carries the run's "
            "public status: a coarse `status`, run timestamps, record counts, "
            "and — on failure — a customer-safe error category. A deliberately "
            "small, stable projection of the internal run-log that exposes no "
            "infrastructure detail (batch job ids, raw error strings, ...). A "
            "client that accepted a run (202) polls this by invocation_id for the "
            "outcome. Error responses use the canonical error envelope and are "
            "documented in the Data Sync OpenAPI rather than here. Absent optional "
            "fields are omitted from the wire, never null. Served at "
            "schemas.<domain> for external API-key consumers."
        ),
        adapter=TypeAdapter(PipelineRunStatusResponse),
        mode="serialization",
        post_process=_data_sync_response_post_process,
        source_paths=(f"{_CONTRACTS_PREFIX}/pipelines/data_sync.py",),
    ),
    Resource(
        name="connector-package",
        title="Analitiq Connector Package",
        description=(
            "Public JSON Schema contract for the authored documents of a "
            "connector package: where each sits, read from the connector's own "
            "directory (the one holding `definition/`), and the published schema "
            "it is written against. It holds locations only; each document's "
            "shape is the schema it points at. Other files a package carries are "
            "outside this schema. "
            "Source of truth: analitiq.contracts.connector_package.ConnectorPackage "
            "(Pydantic)."
        ),
        adapter=TypeAdapter(ConnectorPackage),
        source_paths=(f"{_CONTRACTS_PREFIX}/connector_package.py",),
    ),
    Resource(
        name="connection-package",
        title="Analitiq Connection Package",
        description=(
            "Public JSON Schema contract for the authored documents of a "
            "connection package: where each sits, read from the connection's own "
            "directory (the one holding `connection.json`), and the published "
            "schema it is written against. It holds locations only; each "
            "document's shape is the schema it points at, and a location marked "
            "`x-secret` holds secret values. Other files a package carries are "
            "outside this schema. "
            "Source of truth: analitiq.contracts.connection_package.ConnectionPackage "
            "(Pydantic)."
        ),
        adapter=TypeAdapter(ConnectionPackage),
        source_paths=(f"{_CONTRACTS_PREFIX}/connection_package.py",),
    ),
    Resource(
        name="pipeline-manifest",
        title="Analitiq Pipeline Manifest",
        description=(
            "Public JSON Schema contract for the index of the pipelines a "
            "workspace holds: each pipeline's identifier, lifecycle status and "
            "the path of its `pipeline.json` from the index's directory. "
            "Source of truth: analitiq.contracts.pipeline_manifest.PipelineManifest "
            "(Pydantic)."
        ),
        adapter=TypeAdapter(PipelineManifest),
        source_paths=(f"{_CONTRACTS_PREFIX}/pipeline_manifest.py",),
    ),
    Resource(
        name="pipeline-package",
        title="Analitiq Pipeline Package",
        description=(
            "Public JSON Schema contract for the authored documents of a "
            "pipeline package: where each sits, read from the pipeline's own "
            "directory (the one holding `pipeline.json`), and the published "
            "schema it is written against. It holds locations only; each "
            "document's shape is the schema it points at. Other files a package "
            "carries are outside this schema. "
            "Source of truth: analitiq.contracts.pipeline_package.PipelinePackage "
            "(Pydantic)."
        ),
        adapter=TypeAdapter(PipelinePackage),
        source_paths=(f"{_CONTRACTS_PREFIX}/pipeline_package.py",),
    ),
    Resource(
        name="workspace",
        title="Analitiq Workspace",
        description=(
            "Public JSON Schema contract for where each package of a workspace "
            "sits, read from the workspace root, and the published schema it is "
            "written against: a key ending in `/` is a package's own directory "
            "whose value is that package's documents, checked by its package "
            "schema; any other key is a document the workspace holds directly. "
            "It holds locations only. Other files a workspace carries are "
            "outside this schema. "
            "Source of truth: analitiq.contracts.workspace.Workspace (Pydantic)."
        ),
        adapter=TypeAdapter(Workspace),
        source_paths=(f"{_CONTRACTS_PREFIX}/workspace.py",),
    ),
    Resource(
        name="resolve-types-request",
        title="Analitiq Resolve Types Request",
        description=(
            "Public JSON Schema contract for a request to translate types through "
            "type maps: a direction, the types to translate, and the type maps as "
            "file text in precedence order. The schema gates the request's shape; "
            "whether each map is a type map is judged when the request is built. "
            "Source of truth: analitiq.contracts.type_map.ResolveTypesRequest "
            "(Pydantic)."
        ),
        adapter=TypeAdapter(ResolveTypesRequest),
        source_paths=(f"{_CONTRACTS_PREFIX}/type_map.py",),
    ),
    Resource(
        name="validate-package-request",
        title="Analitiq Validate Package Request",
        description=(
            "Public JSON Schema contract for a request to validate one package, "
            "named by the kind of its root document and supplied as its documents: each document's file "
            "text keyed by its relative path in the package. The schema gates the "
            "request's shape only; the documents' content is not judged by it. "
            "Source of truth: analitiq.contracts.validation_requests."
            "ValidatePackageRequest (Pydantic)."
        ),
        adapter=TypeAdapter(ValidatePackageRequest),
        source_paths=(
            f"{_CONTRACTS_PREFIX}/validation_requests.py",
            f"{_CONTRACTS_PREFIX}/workspace.py",
        ),
    ),
    Resource(
        name="validate-single-document-request",
        title="Analitiq Validate Single Document Request",
        description=(
            "Public JSON Schema contract for a request to validate one document "
            "supplied as its file text, together with the name of the published "
            "document schema it is written against. The schema gates the "
            "request's shape only; the document's content is not judged by it. "
            "Source of truth: analitiq.contracts.validation_requests."
            "ValidateSingleDocumentRequest (Pydantic)."
        ),
        adapter=TypeAdapter(ValidateSingleDocumentRequest),
        source_paths=(
            f"{_CONTRACTS_PREFIX}/validation_requests.py",
            f"{_CONTRACTS_PREFIX}/document_schemas.json",
        ),
    ),
    Resource(
        name="validate-workspace-request",
        title="Analitiq Validate Workspace Request",
        description=(
            "Public JSON Schema contract for a request to validate a workspace, "
            "supplied as its documents: each document's file text keyed by its "
            "relative path from the workspace root, the packages it holds located "
            "by the workspace schema. The schema gates the "
            "request's shape only; the documents' content is not judged by it. "
            "Source of truth: analitiq.contracts.validation_requests."
            "ValidateWorkspaceRequest (Pydantic)."
        ),
        adapter=TypeAdapter(ValidateWorkspaceRequest),
        source_paths=(
            f"{_CONTRACTS_PREFIX}/validation_requests.py",
            f"{_CONTRACTS_PREFIX}/workspace.py",
        ),
    ),
)

RESOURCES_BY_NAME: dict[str, Resource] = {r.name: r for r in RESOURCES}


def get_resource(name: str) -> Resource:
    try:
        return RESOURCES_BY_NAME[name]
    except KeyError:
        valid = ", ".join(r.name for r in RESOURCES)
        raise SystemExit(f"unknown resource {name!r}; valid: {valid}") from None


# ---------------------------------------------------------------------------
# arrow-types.json — generated from the vendored engine grammar
# ---------------------------------------------------------------------------
# Not a registry Resource: the document is versionless and mutable (no
# {X.Y.Z}/latest/index triple — it rides the publish workflow's `**/*.json`
# glob). Its ACCEPTED SET is generated from the engine-published, vendored
# grammar manifest (`analitiq.contracts.arrow_grammar`); only the
# prose below — titles, descriptions, display grouping — is authored here.
# Only the release writes it; the `check` entry of the module docstring says
# what each check mode holds it to.
#
# This document was published as `canonical-types.json` before it was renamed.
# The publish is additive and deletes nothing, so that path keeps serving the
# last document rendered under it — frozen, and describing the `type_map`
# key spelling the contract no longer accepts. Retiring it is a delete on the
# serving side, which this repo cannot perform and no check here can observe;
# until it happens the stale object stays reachable. Renaming this document
# again inherits the same debt.

from analitiq.contracts import arrow_grammar  # noqa: E402

ARROW_TYPES_PATH = SCHEMAS_ROOT / "arrow-types.json"


def _units(family: str) -> str:
    """The family's allowed unit identifiers, comma-joined — interpolated into
    prose so a manifest unit change regenerates the description instead of
    leaving it lying next to a regenerated-correct pattern."""
    (param,) = [
        p for p in arrow_grammar.FAMILIES[family]["params"] if p["kind"] == "unit"
    ]
    return ", ".join(param["allowed"])


def _precision_bounds(family: str) -> str:
    """`min-max` of the family's precision param, from the manifest."""
    # Absence must raise: a family routed here without a precision param is a
    # hard failure of the render, not a case to default.
    param = next(  # skipcq: PTC-W0063
        p
        for p in arrow_grammar.FAMILIES[family]["params"]
        if p["name"] == "precision"
    )
    return f"{param['min']}-{param['max']}"

#: Display grouping + prose for the published `$defs`. Membership is validated
#: against the grammar at build time: every engine family appears in exactly
#: one group and no group names an unknown family — so trimming or adding a
#: family in the vendored manifest fails this render loudly instead of
#: silently publishing a stale vocabulary.
_ARROW_GROUPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "null_type",
        "Null",
        "Arrow Null logical type. Column contains only null values. Zero storage.",
        ("Null",),
    ),
    (
        "boolean_type",
        "Boolean",
        "Arrow Boolean logical type. 1-bit values: true or false.",
        ("Boolean",),
    ),
    (
        "integer_type",
        "Int / UInt",
        "Arrow signed and unsigned integer logical types at fixed widths of 8, "
        "16, 32, or 64 bits. Width should reflect the source system's declared "
        "width — do not upcast `Int32` to `Int64` unless the engine requires it.",
        ("Int8", "Int16", "Int32", "Int64", "UInt8", "UInt16", "UInt32", "UInt64"),
    ),
    (
        "floating_type",
        "FloatingPoint",
        "Arrow IEEE-754 floating-point logical types. Float16 is half-precision; "
        "Float32 is single-precision; Float64 is double-precision.",
        ("Float16", "Float32", "Float64"),
    ),
    (
        "binary_type",
        "Binary",
        "Arrow binary logical types. `Binary` is variable-length with 32-bit "
        "offsets; `LargeBinary` is variable-length with 64-bit offsets; "
        "`FixedSizeBinary(byte_width)` is fixed-length.",
        ("Binary", "LargeBinary", "FixedSizeBinary"),
    ),
    (
        "string_type",
        "Utf8",
        "Arrow UTF-8 string logical types. `Utf8` uses 32-bit offsets (capped "
        "~2 GiB total per array); `LargeUtf8` uses 64-bit offsets.",
        ("Utf8", "LargeUtf8"),
    ),
    (
        "date_type",
        "Date",
        "Arrow Date logical types. `Date32` is days since the Unix epoch; "
        "`Date64` is milliseconds since the Unix epoch (must be a multiple of "
        "86_400_000).",
        ("Date32", "Date64"),
    ),
    (
        "time_type",
        "Time",
        "Arrow time-of-day logical types. `Time32` supports "
        f"{_units('Time32')} units; `Time64` supports {_units('Time64')} units.",
        ("Time32", "Time64"),
    ),
    (
        "timestamp_type",
        "Timestamp",
        "Arrow Timestamp logical type. Unit is one of "
        f"{_units('Timestamp')}. Timezone is optional; when omitted, the type "
        "is zone-naive / local. When present, the timezone is either `null` "
        "(explicit zone-naive), an IANA-shaped zone name (`UTC`, "
        "`America/New_York`, `Etc/GMT±N` — the pattern gates the shape only; "
        "real zone membership is validated by the engine against the tzdb at "
        "runtime), or a fixed `±HH:MM` offset. When mapping from a source "
        "that declares a zoned type (e.g., Postgres `TIMESTAMP WITH TIME "
        "ZONE`), use `UTC` unless there is a source-specific reason otherwise.",
        ("Timestamp",),
    ),
    (
        "duration_type",
        "Duration",
        "Arrow Duration logical type. Elapsed time with unit "
        f"{_units('Duration')}.",
        ("Duration",),
    ),
    (
        "decimal_type",
        "Decimal",
        "Arrow Decimal128 or Decimal256 logical type. `Decimal128(precision, "
        f"scale)` supports precision {_precision_bounds('Decimal128')}; "
        "`Decimal256(precision, scale)` supports precision "
        f"{_precision_bounds('Decimal256')}. Scale is 0 <= scale <= precision: "
        "the patterns cap a literal scale at the family's precision ceiling, "
        "and the scale-vs-precision relation itself is a cross-parameter rule "
        "enforced by the validator API and the contract models. Use "
        "`Decimal128` unless the source precision exceeds "
        f"{_precision_bounds('Decimal128').split('-')[1]}.",
        ("Decimal128", "Decimal256"),
    ),
    (
        "authored_shape_type",
        "Authored-shape JSON container",
        "Bare authored-shape JSON container markers — the vocabulary's ONLY "
        "nested-data grammar. `Object` declares a JSON object with a sibling "
        "`properties` map describing each child (recursive). `List` declares a "
        "JSON array with a sibling `items` field spec describing the element "
        "(recursive). `Json` declares an opaque JSON object or array with no "
        "inner shape — no `properties` or `items` permitted. Sibling-key "
        "enforcement is performed by the validator API and the contract models "
        "(`analitiq.contracts`), not by this string vocabulary.",
        ("Object", "List", "Json"),
    ),
)

#: Examples embedded in the top-level description. Each is validated against
#: the generated pattern at render time, so a stale example fails the render.
_ARROW_EXAMPLES: tuple[str, ...] = (
    "Utf8",
    "Int64",
    "Boolean",
    "Date32",
    "Decimal128(38, 9)",
    "Decimal256(76, 0)",
    "Timestamp(MICROSECOND)",
    "Timestamp(MICROSECOND, UTC)",
    "Timestamp(MILLISECOND, +05:30)",
    "Time32(SECOND)",
    "Time64(NANOSECOND)",
    "Duration(MICROSECOND)",
    "FixedSizeBinary(16)",
    "Object",
    "List",
    "Json",
)


def _arrow_types_description() -> str:
    examples = "\n".join(f"  {e}" for e in _ARROW_EXAMPLES)
    return (
        "Analitiq's profile of the Apache Arrow logical type system. Arrow "
        "types are strings that identify an Arrow logical type in the form used "
        "by `arrow_type` fields throughout the schema contracts — on connector "
        "type-map rules and on endpoint columns alike.\n\n"
        "Ownership: the ACCEPTED SET below is generated from the engine-"
        "published Arrow type grammar manifest "
        f"(`https://schemas.analitiq.ai/{arrow_grammar.ENGINE_GRAMMAR_RESOURCE}/latest.json`, "
        f"pinned at v{arrow_grammar.ENGINE_GRAMMAR_VERSION}) — the set of "
        "families the platform executes end-to-end. A family appears here only "
        "after the engine executes it; the contract adopts it by consuming the "
        "new manifest version, never by hand-editing this document.\n\n"
        "Standard (one screen):\n\n"
        "Base form: PascalCase Arrow type name from `arrow/format/Schema.fbs`, "
        "or an Analitiq authored-shape container marker (`Object`, `List`, "
        "`Json`).\n\n"
        "Two parameter shapes:\n"
        "  - Bare name, no params — scalar types and the authored-shape JSON "
        "container markers: `Utf8`, `Int64`, `Boolean`, `Date32`, `Binary`, "
        "`Object`, `List`, `Json`.\n"
        "  - Parens `( )` for value params — parameterized scalars (units, "
        "precision/scale, byte widths): `Decimal128(38, 9)`, "
        "`Timestamp(MICROSECOND, UTC)`, `FixedSizeBinary(16)`.\n\n"
        "Nested data is declared with the authored-shape markers only: "
        "`Object` / `List` carry their inner shape in sibling `properties` / "
        "`items` keys of the OWNING document (endpoint column, stream field "
        "spec), and `Json` is opaque. There are no fully-typed nested type "
        "strings in this vocabulary.\n\n"
        "Unit values inside parens are the literal Flatbuffers enum "
        "identifiers — uppercase:\n"
        f"  - `TimeUnit`: {_units('Timestamp')}.\n\n"
        "Timezone (Timestamp only): optional second arg. `null`, IANA-shaped "
        "name (including `Etc/GMT±N`; the pattern gates the shape only — real "
        "zone membership is the engine's runtime check), or fixed `±HH:MM` "
        "offset.\n\n"
        "Full Arrow type examples:\n"
        f"{examples}\n\n"
        "Why uppercase unit names instead of PyArrow shorthand (`us`/`ms`/`ns`): "
        "the uppercase identifiers are the only spelling that's actually in the "
        "Arrow specification (the Flatbuffers `TimeUnit` enum). PyArrow's "
        "shortcodes are a Python-library convenience and read ambiguously in "
        "spec text (`us` parses as 'United States' to a casual reader or LLM). "
        "The engine tolerates the short forms on input; this authoring "
        "vocabulary does not. The translation to PyArrow constructor args is "
        "the runtime's job, not the spec's.\n\n"
        "Why parens: parens hold values (integers, enums). The vocabulary has "
        "no type-parameter (angle-bracket) forms — nested shapes belong to the "
        "owning document's sibling keys, not to the type string.\n\n"
        "Reference: https://arrow.apache.org/docs/format/Columnar.html#logical-types.\n\n"
        "Scope note: `Decimal128/256` scale <= precision is a cross-parameter "
        "bound regex cannot express; the validator API and the contract models "
        "enforce it (the patterns here cap a literal scale at the family's "
        "precision ceiling, the satisfiable envelope). Sibling-key rules for "
        "`Object`/`List`/`Json` are likewise the owning document's contract, "
        "not this string vocabulary's.\n\n"
        "Version note: Arrow view types (`Utf8View`, `BinaryView`, `ListView`, "
        "`LargeListView`), the typed nested families (`List<T>`, `LargeList<T>`, "
        "`FixedSizeList<T>[n]`, `Struct<...>`, `Map<K, V>`), unions, encodings "
        "(`Dictionary`, `RunEndEncoded`), and `Interval` are not part of this "
        "Arrow vocabulary: the platform does not execute them end-to-end. "
        "They return, if ever, by shipping in the engine first and re-consuming "
        "the grammar manifest — never by editing this document."
    )


def _arrow_group_schema(members: tuple[str, ...]) -> dict[str, Any]:
    """Schema node for one display group, from the grammar fragments."""
    branches: list[dict[str, Any]] = []
    for family in members:
        if arrow_grammar.FAMILIES[family].get("params"):
            branches.append(
                {
                    "type": "string",
                    "pattern": "^" + arrow_grammar.family_pattern(family) + "$",
                }
            )
        else:
            branches.append({"const": family})
    if all("const" in b for b in branches):
        if len(branches) == 1:
            return {"const": members[0]}
        return {"enum": list(members)}
    if len(branches) == 1:
        return branches[0]
    return {"oneOf": branches}


def build_arrow_types_doc() -> dict[str, Any]:
    """Build the full arrow-types.json document: generated accepted set
    (from the vendored engine grammar) + authored prose."""
    grouped = [f for _, _, _, members in _ARROW_GROUPS for f in members]
    if sorted(grouped) != sorted(arrow_grammar.FAMILY_NAMES) or len(grouped) != len(
        set(grouped)
    ):
        raise RuntimeError(
            "arrow-types display grouping is out of sync with the vendored "
            "engine grammar: every family must appear in exactly one group. "
            f"grammar={sorted(arrow_grammar.FAMILY_NAMES)} grouped={sorted(grouped)}"
        )
    pattern_re = re.compile(arrow_grammar.ARROW_TYPE_PATTERN)
    for example in _ARROW_EXAMPLES:
        if not pattern_re.fullmatch(example):
            raise RuntimeError(
                f"arrow-types example {example!r} does not match the "
                "generated ARROW_TYPE_PATTERN — update _ARROW_EXAMPLES"
            )

    defs: dict[str, Any] = {
        "arrow_type": {
            "title": "arrow_type",
            "description": (
                "An Arrow type string. Must match one of the Arrow logical "
                "type families below — exactly the families the engine "
                "executes, per the pinned grammar manifest."
            ),
            "type": "string",
            "oneOf": [
                {"$ref": f"#/$defs/{def_name}"}
                for def_name, _, _, _ in _ARROW_GROUPS
            ],
        }
    }
    for def_name, title, description, members in _ARROW_GROUPS:
        node: dict[str, Any] = {"title": title, "description": description}
        node.update(_arrow_group_schema(members))
        defs[def_name] = node

    templated_branches: list[dict[str, Any]] = [
        {"$ref": "#/$defs/arrow_type"}
    ]
    for family in arrow_grammar.PARAMETERIZED_FAMILY_NAMES:
        params = ", ".join(
            p["name"] for p in arrow_grammar.FAMILIES[family]["params"]
        )
        templated_branches.append(
            {
                "description": (
                    f"Templated {family}({params}) — each parameter position "
                    "accepts its literal grammar (at its valid range/enum, "
                    "exactly as the strict vocabulary requires) or a `${name}` "
                    "placeholder."
                ),
                "pattern": "^"
                + arrow_grammar.family_pattern(family, templated=True)
                + "$",
            }
        )
    defs["arrow_type_or_template"] = {
        "title": "arrow_type_or_template",
        "description": (
            "An Arrow type string OR a templated Arrow type carrying "
            "`${name}` placeholders in parameter positions. Used by type-map "
            "regex rules where parameters are substituted from named capture "
            "groups (e.g. `Decimal128(${precision}, ${scale})`). Each "
            "parameter position accepts either a LITERAL Arrow value at "
            "its valid range/enum (`38`, `MICROSECOND`, `UTC`) or a `${name}` "
            "placeholder; a literal out of range is rejected exactly as the "
            "strict vocabulary rejects it, so `Decimal128(999, 0)` and "
            "`Time32(NANOSECOND)` do NOT match. A `${name}` placeholder must "
            "be a valid identifier (`[A-Za-z_][A-Za-z0-9_]*`), matching the "
            "native capture-group naming it resolves from; `${1bad}` / `${ }` "
            "do not match. Outside parameter positions, the Arrow "
            "base name must appear verbatim — `not an arrow type ${precision}` "
            "does not match. Templated branches use `anyOf` (not `oneOf`) "
            "because a literal parameterized Arrow type (e.g. "
            "`Timestamp(MICROSECOND, UTC)`) intentionally matches both the "
            "strict arrow_type vocabulary AND the templated branch — both "
            "readings are correct, and `anyOf` reflects that. This vocabulary "
            "mirrors the runtime `_validate_type_map_arrow_type` shape check "
            "plus the placeholder-name rule its sibling validators enforce; a "
            "differential parity test pins that alignment."
        ),
        "type": "string",
        "anyOf": templated_branches,
    }

    return {
        "$schema": SCHEMA_DRAFT,
        "$id": f"{CANONICAL_BASE}/arrow-types.json",
        "title": "Analitiq Arrow types",
        "description": _arrow_types_description(),
        "$comment": (
            "GENERATED by scripts/render_schemas.py from the vendored engine "
            "grammar manifest (analitiq.contracts.arrow_grammar) — do not "
            "hand-edit; the schema release (`render_schemas.py release`) "
            "re-renders it. Validating a value directly against this document's URL "
            "checks it against the strict arrow_type vocabulary. Type-map "
            "regex rules that permit ${name} templates reference the "
            "#/$defs/arrow_type_or_template fragment explicitly."
        ),
        "$ref": "#/$defs/arrow_type",
        "$defs": defs,
    }


def _arrow_types_text() -> str:
    return json.dumps(build_arrow_types_doc(), indent=2) + "\n"


_RELEASE_WRITES_IT = "the schema release (`render_schemas.py release`) writes it"


def _versionless_problem(path: Path, render: Callable[[], str], *, released: bool) -> str | None:
    """Why the committed versionless document at `path` fails the check, or None.

    Only the release writes it, so between releases it may trail its sources:
    bare `check` asks only for the render's top-level keys, and `released`
    for the render's exact bytes.
    """
    if not path.exists():
        return f"{path.name} is missing; {_RELEASE_WRITES_IT}"
    try:
        rendered = render()
    except RuntimeError as exc:
        return f"cannot render {path.name} — {exc}"
    committed = path.read_text()
    if released:
        if committed != rendered:
            return f"{path.name} is not what its sources render; {_RELEASE_WRITES_IT}"
        return None
    try:
        keys = json.loads(committed).keys()
    except (ValueError, AttributeError):
        return f"{path.name} is not a JSON object; {_RELEASE_WRITES_IT}"
    if keys != json.loads(rendered).keys():
        return f"{path.name} does not have its render's keys; {_RELEASE_WRITES_IT}"
    return None


def check_arrow_types(*, released: bool) -> tuple[bool, str]:
    """(ok, message) — see `_versionless_problem`. A builder failure is a
    normal check failure, so it participates in `cmd_check`'s aggregate run
    instead of truncating it mid-way."""
    problem = _versionless_problem(ARROW_TYPES_PATH, _arrow_types_text, released=released)
    return (problem is None, f"arrow-types: {problem or 'OK'}")


# ---------------------------------------------------------------------------
# contracts-version.json — the tree's provenance stamp
# ---------------------------------------------------------------------------
# Not a registry Resource: versionless and mutable, like arrow-types.json
# (it rides the publish workflow's `**/*.json` glob as a mutable pointer).
# The document carries the facts a consumer needs to check that the schema it
# fetched and the validator it pinned came from the same contract: the
# `analitiq-contract-models` version the contract source tree DECLARED when
# the release rendered the tree (READ from the package's `pyproject.toml`,
# never hand-maintained), plus a digest of every other document in the tree.
# The digest is what makes the stamp move with EVERY release — the version
# alone changes only on a package bump, so without it a publish that failed
# to land a release would be undetectable from the stamp. Only the release
# writes it; the `check` entry of the module docstring says what each check
# mode holds it to, and `scripts/check_contracts_version_pin.py` owns what the
# published copy witnesses.

CONTRACTS_VERSION_PATH = SCHEMAS_ROOT / "contracts-version.json"
CONTRACT_MODELS_PYPROJECT = (
    REPO_ROOT / "packages" / "contract-models" / "pyproject.toml"
)
#: The document's fact key: the PyPI distribution name, so the fetched
#: object is unambiguous to a consumer holding nothing else.
#: `scripts/check_contracts_version_pin.py` reads the published copy under
#: this same key (it cannot import this module — guard jobs are stdlib-only,
#: and this module imports pydantic);
#: `tests/schemas/test_contracts_version_render.py` pins the copies equal.
CONTRACTS_VERSION_KEY = "analitiq-contract-models"  # skipcq: SCT-A000 — a PyPI distribution name, not a credential


def contract_models_version() -> str:
    """The version `packages/contract-models/pyproject.toml` declares.

    tomllib rather than a regex: the pyproject is the owning source, and a
    parse that silently mis-read it would stamp a wrong provenance fact into
    the published tree. Every failure shape is re-raised as the RuntimeError
    the check/CLI paths classify, so an unreadable or malformed pyproject
    reports as "cannot render" rather than a raw traceback.
    """
    try:
        data = tomllib.loads(CONTRACT_MODELS_PYPROJECT.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"cannot read {CONTRACT_MODELS_PYPROJECT}: {exc}") from exc
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError(
            f"{CONTRACT_MODELS_PYPROJECT} declares no [project] version — "
            "the provenance stamp cannot be rendered"
        )
    return version


def schemas_tree_digest() -> str:
    """sha256 over the sorted paths and bytes of every `schemas/**/*.json`
    except the stamp itself, hand-authored documents included."""
    hasher = hashlib.sha256()
    for path in sorted(SCHEMAS_ROOT.rglob("*.json")):
        if path == CONTRACTS_VERSION_PATH:
            continue
        hasher.update(path.relative_to(SCHEMAS_ROOT).as_posix().encode())
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def build_contracts_version_doc() -> dict[str, Any]:
    return {
        "$comment": (
            "GENERATED by scripts/render_schemas.py — the "
            "analitiq-contract-models version the contract source tree "
            "declared when this schema tree was released, plus a sha256 over "
            "every other document in the tree (so the stamp changes with "
            "every release, not only on a version bump). Do not hand-edit; "
            "`render_schemas.py release` re-renders it."
        ),
        CONTRACTS_VERSION_KEY: contract_models_version(),
        "tree_sha256": schemas_tree_digest(),
    }


def _contracts_version_text() -> str:
    return json.dumps(build_contracts_version_doc(), indent=2, sort_keys=True) + "\n"


def check_contracts_version(*, released: bool) -> tuple[bool, str]:
    """(ok, message) — see `_versionless_problem`."""
    problem = _versionless_problem(CONTRACTS_VERSION_PATH, _contracts_version_text, released=released)
    return (problem is None, f"contracts-version: {problem or 'OK'}")


# ---------------------------------------------------------------------------
# document_schemas.json — the single-document request's `document_kind` vocabulary
# ---------------------------------------------------------------------------
# Generated into the contract package rather than the schemas/ tree: the
# request model loads it to build its `document_kind` Literal, and a model cannot read
# RESOURCES itself, which lives in this script and imports the models. The
# selection is a property of the models, so a resource becomes a label by
# declaring `$schema` on its root model, never by being listed.


def _root_types(root: Any) -> tuple[Any, ...]:
    """The types a document can be at its root: every member of a root union,
    or the root itself. A container root stays whole, so its item models never
    count as the document."""
    origin = typing.get_origin(root)
    if origin is typing.Annotated:
        return _root_types(typing.get_args(root)[0])
    if origin in (typing.Union, types.UnionType):
        return tuple(member for arg in typing.get_args(root) for member in _root_types(arg))
    return (root,)


def _schema_url_adapter(root: Any) -> TypeAdapter | None:
    if not (inspect.isclass(root) and issubclass(root, BaseModel)):
        return None
    field = next((f for f in root.model_fields.values() if f.alias == "$schema"), None)
    if field is None:
        return None
    return TypeAdapter(
        typing.Annotated[(field.annotation, *field.metadata)] if field.metadata
        else field.annotation)


def _accepts(adapter: TypeAdapter, value: str) -> bool:
    try:
        adapter.validate_python(value)
    except ValidationError:
        return False
    return True


def document_schema_names(resources: Iterable[Resource]) -> list[str]:
    """Names of the resources whose root model declares a `$schema` field.

    Raises when a root union declares it on only some members, or when a
    root's `$schema` refuses its own resource's URL or accepts another
    selected resource's URL — either would make a label name a schema other
    than the document's own.
    """
    selected: dict[str, list[TypeAdapter]] = {}
    for resource in resources:
        adapters = [_schema_url_adapter(member)
                    for member in _root_types(resource.adapter._type)]  # skipcq: PYL-W0212
        declared = [adapter for adapter in adapters if adapter is not None]
        if not declared:
            continue
        if len(declared) != len(adapters):
            raise ValueError(
                f"resource {resource.name!r}: only some root union members declare "
                "`$schema`, so it is neither a document schema nor not one")
        selected[resource.name] = declared
    for name, adapters in selected.items():
        for adapter in adapters:
            if not _accepts(adapter, schema_url_for(name)):
                raise ValueError(
                    f"resource {name!r}: a root model's `$schema` refuses "
                    f"{schema_url_for(name)!r}, its own schema URL")
            others = [other for other in selected
                      if other != name and _accepts(adapter, schema_url_for(other))]
            if others:
                raise ValueError(
                    f"resource {name!r}: a root model's `$schema` also accepts "
                    f"the schema URL of {others}")
    return list(selected)


def _document_schemas_text() -> str:
    doc = {
        "$comment": (
            "GENERATED by scripts/render_schemas.py from the RESOURCES whose root "
            "model declares `$schema`. Do not hand-edit; "
            "`render_schemas.py document-schemas` re-renders it."
        ),
        DOCUMENT_SCHEMAS_KEY: document_schema_names(RESOURCES),
    }
    return json.dumps(doc, indent=2) + "\n"


def check_document_schemas() -> tuple[bool, str]:
    """(ok, message) — committed document_schemas.json vs rendered output."""
    hint = "`scripts/render_schemas.py document-schemas`"
    if not DOCUMENT_SCHEMAS_PATH.exists():
        return (False, f"document-schemas: {DOCUMENT_SCHEMAS_PATH} is missing; run {hint}")
    try:
        rendered = _document_schemas_text()
    except ValueError as exc:
        return (False, f"document-schemas: cannot render — {exc}")
    if DOCUMENT_SCHEMAS_PATH.read_text() != rendered:
        return (
            False,
            "document-schemas: document_schemas.json is stale or hand-edited; "
            f"re-run {hint}; the next schema release publishes it",
        )
    return (True, "document-schemas: OK — document_schemas.json matches RESOURCES")


def _refresh(path: Path, rendered: str) -> None:
    if not path.exists() or path.read_text() != rendered:
        path.write_text(rendered)
        print(f"wrote {_shown(path)}")


def _refresh_document_schemas() -> None:
    _refresh(DOCUMENT_SCHEMAS_PATH, _document_schemas_text())


def cmd_document_schemas(args: argparse.Namespace) -> int:
    if args.check:
        ok, msg = check_document_schemas()
        print(msg, file=None if ok else sys.stderr)
        return 0 if ok else 1
    try:
        rendered = _document_schemas_text()
    except ValueError as exc:
        print(f"document-schemas: cannot render — {exc}", file=sys.stderr)
        return 2
    DOCUMENT_SCHEMAS_PATH.write_text(rendered)
    print(f"wrote {DOCUMENT_SCHEMAS_PATH.relative_to(REPO_ROOT)}")
    return 0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_schema(resource: Resource, version: str, *, identity: str | None = None) -> dict[str, Any]:
    """Render a resource's JSON Schema, stamped with $id/$schema/version.

    By default the `$id` points at the immutable pinned URL for `version`. Pass
    `identity` to override (used when writing `latest.json`, whose canonical URL
    differs from the pinned doc it currently mirrors).
    """
    body = resource.adapter.json_schema(mode=resource.mode, ref_template="#/$defs/{model}")
    if resource.post_process is not None:
        resource.post_process(body)
    # Universal, self-guarding alignment pass: keep the published `DatabaseObject`
    # in lockstep with the model wherever it is embedded (runs after the
    # resource's own post-processor; no-op when `DatabaseObject` is absent).
    _normalize_database_object_namespaces(body)
    stamped: dict[str, Any] = {
        "$schema": SCHEMA_DRAFT,
        "$id": identity or f"{resource.base_url()}/{version}.json",
        "version": version,
    }
    stamped.update(body)
    # Stamp human-friendly title/description AFTER the body merge so they win
    # over Pydantic-generated values (e.g. class-name-derived titles like
    # "ConnectionDocument" — the public schema should advertise the resource
    # name from the registry instead).
    stamped["title"] = resource.title
    stamped["description"] = resource.description
    return stamped


def render_pinned(resource: Resource, version: str) -> dict[str, Any]:
    """Render the immutable doc for `version` (canonical $id = pinned URL)."""
    return render_schema(resource, version)


def render_latest(resource: Resource, version: str) -> dict[str, Any]:
    """Render the mutable `latest.json` mirror for `version` (canonical $id = latest URL)."""
    return render_schema(
        resource, version, identity=f"{resource.base_url()}/latest.json"
    )


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def list_published_versions(resource: Resource) -> list[str]:
    """Sorted list of X.Y.Z files published under schemas/<resource>/."""
    if not resource.dir().exists():
        return []
    found: list[str] = []
    for f in resource.dir().glob("*.json"):
        m = VERSIONED_FILENAME_RE.match(f.name)
        if m:
            found.append(m.group(1))
    return sorted(found, key=parse_semver)


def load_latest(resource: Resource) -> dict | None:
    path = resource.dir() / "latest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def build_index(resource: Resource) -> dict[str, Any]:
    """Build the resource-level manifest from on-disk versions."""
    versions = list_published_versions(resource)
    if not versions:
        return {resource.name: {"latest": None, "versions": []}}
    return {resource.name: {"latest": versions[-1], "versions": versions}}


def _load_pinned(resource: Resource, version: str) -> dict:
    return json.loads((resource.dir() / f"{version}.json").read_text())


def rendered_latest(name: str) -> dict[str, Any]:
    """The `latest.json` the models render now, stamped at the committed
    version. The committed tree changes only in a release, so a test asking
    what the contract publishes reads this, not the committed file."""
    resource = get_resource(name)
    committed = load_latest(resource)
    return render_latest(resource, committed["version"] if committed else FIRST_VERSION)


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------


def bump_record_path(resource: Resource, version: str) -> Path:
    return BUMP_RECORDS_ROOT / resource.name / f"{version}.json"


def override_path(resource: Resource) -> Path:
    return BUMP_RECORDS_ROOT / resource.name / OVERRIDE_NAME


def _shown(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def load_override(resource: Resource) -> dict | None:
    """The committed override of the next bump of `resource`, or None.

    Raises ValueError naming the file when it is not a bump with a reason.
    """
    path = override_path(resource)
    if not path.exists():
        return None
    try:
        override = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{_shown(path)}: not valid JSON ({exc})") from exc
    problem = cascade.override_problem(override)
    if problem:
        raise ValueError(f"{_shown(path)}: {problem}")
    return override


def misplaced_overrides() -> list[str]:
    """Every override file no registered resource would read, as a problem.

    Only `override_path` of a registered resource is applied, so any other
    file of that name — a misspelled resource, the records root — would
    otherwise be ignored by every release while looking committed.
    """
    registered = {override_path(resource) for resource in RESOURCES}
    return [
        f"{_shown(path)}: no registered resource reads this override; "
        f"it belongs at {_shown(BUMP_RECORDS_ROOT)}/<resource>/{OVERRIDE_NAME}"
        for path in sorted(BUMP_RECORDS_ROOT.rglob(OVERRIDE_NAME))
        if path not in registered
    ]


@dataclass(frozen=True)
class PendingRelease:
    """A resource whose render differs from its committed `latest.json`."""

    resource: Resource
    #: None for a resource never released.
    committed: dict | None
    #: The render, stamped at the committed version so only the models' change diffs.
    rendered: dict
    override: dict | None


def pending_release(resource: Resource) -> PendingRelease | None:
    """What the next release publishes for `resource`; None when nothing changed.

    Raises ValueError when a committed override cannot apply: it is malformed,
    nothing changed, or the resource is new and has no bump to override.
    """
    override = load_override(resource)
    committed = load_latest(resource)
    if committed is None:
        if override is not None:
            raise ValueError(
                f"{_shown(override_path(resource))}: {resource.name} is first released at "
                f"{FIRST_VERSION}; there is no bump to override")
        return PendingRelease(resource, None, render_latest(resource, FIRST_VERSION), None)
    rendered = render_latest(resource, committed["version"])
    if not diff(committed, rendered):
        if override is not None:
            raise ValueError(
                f"{_shown(override_path(resource))}: {resource.name} has not changed since "
                f"{committed['version']}; there is nothing to override")
        return None
    return PendingRelease(resource, committed, rendered, override)


def _publish(resource: Resource, version: str, record: dict | None) -> None:
    write_json(resource.dir() / f"{version}.json", render_pinned(resource, version))
    write_json(resource.dir() / "latest.json", render_latest(resource, version))
    write_json(resource.dir() / "index.json", build_index(resource))
    if record is not None:
        write_json(bump_record_path(resource, version), record)
    override_path(resource).unlink(missing_ok=True)


def cmd_release(args: argparse.Namespace) -> int:
    """Publish every resource the models have changed since its last release,
    then re-render the versionless documents, the stamp last.

    Every override is validated and every source rendered before the first
    model call, and every bump is decided before the first write, so a failure
    leaves the tree untouched.
    """
    pending: list[PendingRelease] = []
    problems = misplaced_overrides()
    for resource in RESOURCES:
        try:
            release = pending_release(resource)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if release is not None:
            pending.append(release)
    try:
        arrow_types = _arrow_types_text()
        contract_models_version()
    except RuntimeError as exc:
        problems.append(f"cannot render the versionless documents — {exc}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2

    post = None
    if any(release.committed is not None for release in pending):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            print("OPENROUTER_API_KEY is not set; deciding a bump needs it.", file=sys.stderr)
            return 2
        post = cascade.openrouter_post(api_key)
    decided: list[tuple[Resource, str, dict | None]] = []
    for release in pending:
        if release.committed is None:
            decided.append((release.resource, FIRST_VERSION, None))
            print(f"{release.resource.name}: first release at {FIRST_VERSION}")
            continue
        try:
            record, cost = decide_record(
                release.resource.name, release.committed["version"],
                release.committed, release.rendered, post, release.override)
        except cascade.BumpClassificationError as exc:
            print(f"{release.resource.name}: the change could not be classified: {exc}", file=sys.stderr)
            return 2
        decided.append((release.resource, record["to"], record))
        decided_by = "override" if release.override else "models"
        print(f"{release.resource.name}: {record['from']} → {record['to']} "
              f"({record['final']}, decided by the {decided_by}, cost ${cost:.4f})")
    if not pending:
        print("No resource changed: every resource renders as its latest.json.")

    for resource, version, record in decided:
        _publish(resource, version, record)
    _refresh(ARROW_TYPES_PATH, arrow_types)
    _refresh_document_schemas()
    # Last: the stamp's digest covers every other document in the tree.
    _refresh(CONTRACTS_VERSION_PATH, _contracts_version_text())
    return 0


def _check_bump_records(resource: Resource) -> list[str]:
    """Every file under the resource's record directory but its override: a
    record justifying the pinned versions it names."""
    problems: list[str] = []
    for path in sorted((BUMP_RECORDS_ROOT / resource.name).glob("*")):
        where = _shown(path)
        if path.name == OVERRIDE_NAME:
            continue
        match = VERSIONED_FILENAME_RE.match(path.name)
        if not match:
            problems.append(f"{where}: neither {OVERRIDE_NAME} nor named <version>.json")
            continue
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{where}: not valid JSON ({exc})")
            continue
        from_version = record.get("from") if isinstance(record, dict) else None
        pinned = [resource.dir() / f"{v}.json" for v in (from_version, match.group(1))]
        if not isinstance(from_version, str) or not all(p.exists() for p in pinned):
            problems.append(f"{where}: names a version with no pinned schema")
            continue
        old, new = (json.loads(p.read_text()) for p in pinned)
        problem = record_problem(record, resource.name, from_version, match.group(1), old, new)
        if problem:
            problems.append(f"{where}: the bump record {problem}")
    return problems


def _check_resource(resource: Resource, *, released: bool) -> tuple[bool, str]:
    """Return (ok, message).

    The committed tree must be self-consistent: `latest.json` mirrors the
    highest pinned version, `index.json` lists what is pinned, and every bump
    record justifies the versions it names, and a committed override is one
    the next release would apply. With `released`, the models must also
    render exactly that tree, which holds only once a release has published
    every change (and so consumed every override).
    """
    release_hint = "the schema release writes it; never edit it by hand"
    if override_path(resource).exists():
        try:
            pending_release(resource)
        except ValueError as exc:
            return (False, str(exc))
    versions = list_published_versions(resource)
    if not versions:
        if released:
            return (False, f"{resource.name}: registered but never released")
        return (True, f"{resource.name}: OK — not released yet")

    committed_latest = load_latest(resource)
    if committed_latest is None:
        return (False, f"{resource.name}: latest.json is missing; {release_hint}")
    version = committed_latest.get("version")
    if version != versions[-1]:
        return (
            False,
            f"{resource.name}: latest.json names {version!r}, not the highest pinned "
            f"version {versions[-1]}; {release_hint}",
        )
    pinned = _load_pinned(resource, version)
    if committed_latest != {**pinned, "$id": f"{resource.base_url()}/latest.json"}:
        return (False, f"{resource.name}: latest.json does not mirror {version}.json; {release_hint}")

    # index.json is published to the CDN exactly like the other two, so it
    # needs the same gate.
    index_path = resource.dir() / "index.json"
    if not index_path.exists() or json.loads(index_path.read_text()) != build_index(resource):
        return (False, f"{resource.name}: index.json is missing or stale; {release_hint}")

    record_problems = _check_bump_records(resource)
    if record_problems:
        return (False, "\n".join(record_problems))

    if released:
        if pinned != render_pinned(resource, version):
            return (False, f"{resource.name}: {version}.json is not what the models render; "
                           "the release is stale")
        return (True, f"{resource.name}: OK — {version} released, matching the models")
    return (True, f"{resource.name}: OK — {version} is consistent with its pinned versions and records")


def cmd_check(args: argparse.Namespace) -> int:
    targets = (
        [get_resource(args.resource)] if args.resource else list(RESOURCES)
    )
    if not targets:
        print("no resources registered", file=sys.stderr)
        return 1
    results = [_check_resource(resource, released=args.released) for resource in targets]
    results += [(False, problem) for problem in misplaced_overrides()]
    # arrow-types.json, contracts-version.json and document_schemas.json are
    # generated but not registry Resources (versionless + mutable); a full
    # check covers them so CI needs no extra invocation.
    if not args.resource:
        results += [
            check_arrow_types(released=args.released),
            check_contracts_version(released=args.released),
            check_document_schemas(),
        ]
    else:
        print(
            "note: arrow-types.json, contracts-version.json and "
            "document_schemas.json not checked with --resource; run a full `check` (CI does) to cover them"
        )
    for ok, msg in results:
        print(msg, file=None if ok else sys.stderr)
    return 0 if all(ok for ok, _ in results) else 1


def release_only_paths(paths: Iterable[str]) -> list[str]:
    """The paths in `paths` that only the schema release may change: a
    registered resource's published tree, the versionless documents, and
    everything under the bump records but a registered resource's override."""
    owned = tuple(f"{_shown(resource.dir())}/" for resource in RESOURCES)
    versionless = {_shown(ARROW_TYPES_PATH), _shown(CONTRACTS_VERSION_PATH)}
    records = f"{_shown(BUMP_RECORDS_ROOT)}/"
    overrides = {_shown(override_path(resource)) for resource in RESOURCES}
    return [
        path for path in paths
        if path.startswith(owned)
        or path in versionless
        or (path.startswith(records) and path not in overrides)
    ]


def cmd_release_guard(args: argparse.Namespace) -> int:
    """Read changed repo-relative paths from stdin; exit 1 on any only a release may change."""
    blocked = release_only_paths(line.strip() for line in sys.stdin if line.strip())
    if blocked:
        print(
            "Only the schema release writes schema versions, the versionless "
            "documents and bump records. "
            f"Revert these, and commit a {OVERRIDE_NAME} under "
            f"{_shown(BUMP_RECORDS_ROOT)}/<resource>/ to override a bump:",
            file=sys.stderr,
        )
        for path in blocked:
            print(f"  {path}", file=sys.stderr)
        return 1
    print("No release-only path changed.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    if args.paths:
        seen: set[str] = set()
        for resource in RESOURCES:
            for p in resource.source_paths:
                seen.add(p)
            seen.add(f"{resource.dir().relative_to(REPO_ROOT).as_posix()}/**")
        seen.add("scripts/render_schemas.py")
        seen.add(f"{BUMP_RECORDS_ROOT.relative_to(REPO_ROOT).as_posix()}/**")
        seen.add(".github/workflows/tests.yml")
        # Generated arrow-types.json + the vendored grammar it renders from.
        seen.add("schemas/arrow-types.json")
        seen.add(f"{_CONTRACTS_PREFIX}/arrow_grammar.py")
        seen.add(f"{_CONTRACTS_PREFIX}/arrow_type_grammar.json")
        for p in sorted(seen):
            print(p)
        return 0
    for resource in RESOURCES:
        print(resource.name)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_release = sub.add_parser(
        "release",
        help="publish every resource the models changed since its last release",
    )
    p_release.set_defaults(func=cmd_release)

    p_check = sub.add_parser(
        "check",
        help="exit 1 unless the committed tree is self-consistent (all resources by default)",
    )
    p_check.add_argument(
        "--resource",
        help="check just one resource; default checks every registered resource",
    )
    p_check.add_argument(
        "--released",
        action="store_true",
        help="also require the models to render exactly the committed tree",
    )
    p_check.set_defaults(func=cmd_check)

    p_guard = sub.add_parser(
        "release-guard",
        help="read changed paths from stdin; exit 1 on any only the release may change",
    )
    p_guard.set_defaults(func=cmd_release_guard)

    p_ds = sub.add_parser(
        "document-schemas",
        help="render the contract package's document_schemas.json — the "
        "single-document request's `document_kind` vocabulary, selected from RESOURCES",
    )
    p_ds.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed file differs from rendered output "
        "(also part of the full `check` run)",
    )
    p_ds.set_defaults(func=cmd_document_schemas)

    p_list = sub.add_parser(
        "list",
        help="print registered resource names (one per line); "
        "with --paths, print the union of source/output paths this render "
        "depends on",
    )
    p_list.add_argument(
        "--paths",
        action="store_true",
        help="print the union of source/output paths this render depends "
        "on, one per line",
    )
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
