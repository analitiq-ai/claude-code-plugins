"""Connection-document validation — the `connection` authored artifact kind.

A connection document is validated wholly against its contract model
(`ConnectionInput`, the same model the published `connection` JSON Schema is
generated from): `TypeAdapter(...).validate_python` enforces its structure *and*
every cross-field rule (the storage-map / `secret_refs` scheme rules, the
authored-top-level guard) offline, no schema fetch, no drift. There is no
cross-file or referential check a connection document needs in isolation — its
place in an assembled run is checked by the pipeline-bundle kind. The one check
the model cannot carry is RULE-SHRD-003, which reports a `warning` — a severity
no `@model_validator` can carry (`rules/SCHEMA.md`, `validator`) — so this kind
registers a combined validator.

At import this module registers that validator under the published document
schema name `connection`, so `_core` never hard-codes a connection branch — a
new kind is a new module registering the name it owns.
"""
from __future__ import annotations

from ._core import contract_model_domain, register_model_and_schema_kind

# Import the contract model under the shared DOMAIN guard (the model binds the
# `$schema` host at import; see `contract_model_domain`).
with contract_model_domain():
    from pydantic import TypeAdapter
    from analitiq.contracts.connection import ConnectionInput

_CONNECTION_ADAPTER = TypeAdapter(ConnectionInput)


register_model_and_schema_kind("connection", _CONNECTION_ADAPTER)
