"""Stream-document validation — the `stream` authored artifact kind.

A stream document is validated wholly against its contract model (`StreamInput`,
the same model the published `stream` JSON Schema is generated from):
`TypeAdapter(...).validate_python` enforces its structure *and* every cross-field
rule (endpoint-ref shape, unique destinations, the authored-top-level guard)
offline, no schema fetch, no drift. There is no cross-file or referential check a
stream document needs in isolation — its wiring within an assembled run is checked
by the pipeline-bundle kind. The one check the model cannot carry is
RULE-SHRD-003, which reports a `warning` — a severity no `@model_validator` can
carry (`rules/SCHEMA.md`, `validator`) — so this kind registers a combined
validator.

At import this module registers that validator under the published document
schema name `stream`, so `_core` never hard-codes a stream branch — a new kind
is a new module registering the name it owns.
"""
from __future__ import annotations

from ._core import contract_model_domain, register_model_and_schema_kind

# Import the contract model under the shared DOMAIN guard (the model binds the
# `$schema` host at import; see `contract_model_domain`).
with contract_model_domain():
    from pydantic import TypeAdapter
    from analitiq.contracts.stream import StreamInput

_STREAM_ADAPTER = TypeAdapter(StreamInput)


register_model_and_schema_kind("stream", _STREAM_ADAPTER)
