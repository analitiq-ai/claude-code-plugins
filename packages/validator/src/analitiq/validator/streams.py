"""The `stream` kind.

A stream document is validated wholly against its contract model (`StreamInput`,
the same model the published `stream` JSON Schema is generated from), plus
RULE-SHRD-003, which reports a `warning` — a severity no `@model_validator` can
carry (`rules/SCHEMA.md`, `validator`). Its wiring to its pipeline is the
pipeline package's check.
"""
from __future__ import annotations

from ._core import contract_model_domain, register_model_and_schema_kind

# Import the contract model under the shared DOMAIN guard (the model binds the
# `$schema` host at import; see `contract_model_domain`).
with contract_model_domain():
    from pydantic import TypeAdapter
    from analitiq.contracts.stream import StreamInput

register_model_and_schema_kind("stream", TypeAdapter(StreamInput))
