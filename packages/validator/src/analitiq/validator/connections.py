"""Connection-document validation — the `connection` authored artifact kind.

A connection document is validated wholly against its contract model
(`ConnectionInput`, the same model the published `connection` JSON Schema is
generated from): `TypeAdapter(...).validate_python` enforces its structure *and*
every cross-field rule (the storage-map / `secret_refs` scheme rules, the
authored-top-level guard) offline, no schema fetch, no drift. There is no
cross-document or referential check a connection document needs in isolation — its
place among other documents is checked by `analitiq.validator.document_set` and
the pipeline-bundle kind. The one check
the model cannot carry is RULE-SHRD-003, which reports a `warning` — a severity
no `@model_validator` can carry (`rules/SCHEMA.md`, `validator`) — so this kind's
validator combines the two.

At import this module registers that validator with the core registry, so `_core`
never hard-codes a connection branch — a new kind is a new module.
"""
from __future__ import annotations

from typing import Any

from ._core import (
    _missing_schema_url_findings,
    _model_findings,
    contract_model_domain,
    register_document_validator,
)

# Import the contract model under the shared DOMAIN guard (the model binds the
# `$schema` host at import; see `contract_model_domain`).
with contract_model_domain():
    from pydantic import TypeAdapter
    from analitiq.contracts.connection import ConnectionInput

_CONNECTION_ADAPTER = TypeAdapter(ConnectionInput)


def _validate_connection_document(doc: Any) -> list[dict]:
    return _model_findings(doc, _CONNECTION_ADAPTER) + _missing_schema_url_findings(doc)


register_document_validator("connection", _validate_connection_document)
