"""Stream-document validation — the `stream` authored artifact kind.

A stream document is validated wholly against its contract model (`StreamInput`,
the same model the published `stream` JSON Schema is generated from):
`TypeAdapter(...).validate_python` enforces its structure *and* every cross-field
rule (endpoint-ref shape, unique destinations, the authored-top-level guard)
offline, no schema fetch, no drift. There is no cross-document or referential check a
stream document needs in isolation — its wiring is checked across documents by
`analitiq.validator.document_set` and the pipeline-bundle kind. The one check the
model cannot carry is RULE-SHRD-003, which reports a `warning` — a severity no
`@model_validator` can carry (`rules/SCHEMA.md`, `validator`) — so this kind's
validator combines the two.

At import this module registers that validator with the core registry, so `_core` never
hard-codes a stream branch — a new kind is a new module.
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
    from analitiq.contracts.stream import StreamInput

_STREAM_ADAPTER = TypeAdapter(StreamInput)


def _validate_stream_document(doc: Any) -> list[dict]:
    return _model_findings(doc, _STREAM_ADAPTER) + _missing_schema_url_findings(doc)


register_document_validator("stream", _validate_stream_document)
