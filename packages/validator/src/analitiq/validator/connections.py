"""The connection package's kinds and its cross-document check.

A connection document is validated wholly against its contract model
(`ConnectionInput`, the same model the published `connection` JSON Schema is
generated from), plus RULE-SHRD-003, which reports a `warning` — a severity no
`@model_validator` can carry (`rules/SCHEMA.md`, `validator`). A credentials
file is validated wholly against `CredentialsFile`; it declares no `$schema`.
The package check holds each database endpoint to the file name it is located
by (RULE-PKG-031).
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from ._core import _model_findings, contract_model_domain, register_kind, register_model_and_schema_kind
from .connectors import endpoint_filename_findings
from .document_set import keys_of, register_package_check

# Import the contract models under the shared DOMAIN guard (the model binds the
# `$schema` host at import; see `contract_model_domain`).
with contract_model_domain():
    from pydantic import TypeAdapter
    from analitiq.contracts.connection import ConnectionInput
    from analitiq.contracts.connection_package import ConnectionPackage
    from analitiq.contracts.credentials_file import CredentialsFile

_CREDENTIALS_ADAPTER = TypeAdapter(CredentialsFile)


def _connection_package_findings(documents: dict[str, Any]) -> list[tuple[str, dict]]:
    return [(key, f)
            for key in keys_of(ConnectionPackage, documents, "database-endpoint")
            for f in endpoint_filename_findings(documents[key], PurePosixPath(key).name)]


register_model_and_schema_kind("connection", TypeAdapter(ConnectionInput))
register_kind("credentials", lambda doc: _model_findings(doc, _CREDENTIALS_ADAPTER))
register_package_check("connection-package", _connection_package_findings)
