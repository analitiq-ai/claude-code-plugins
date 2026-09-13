"""Stream-document validation — the `stream` authored artifact kind.

A stream document is validated wholly against its contract model (`StreamInput`,
the same model the published `stream` JSON Schema is generated from):
`TypeAdapter(...).validate_python` enforces its structure *and* every cross-field
rule (endpoint-ref shape, unique destinations, the authored-top-level guard)
offline, no schema fetch, no drift. There is no cross-file or referential check a
stream document needs in isolation — its wiring within an assembled run is checked
by the pipeline-bundle kind. The one check the model cannot carry is
RULE-SHRD-003 (`$schema` omission is a `warning`, and a `@model_validator`
rejection always surfaces as `error`), so this kind registers a combined
validator.

At import this module registers its detector -> validator pair with the core
dispatch registry, so `_core` never hard-codes a stream branch — a new kind is a
new module.
"""
from __future__ import annotations

from typing import Any

from ._core import contract_model_domain, register_kind, _missing_schema_url_findings, _model_findings

# Import the contract model under the shared DOMAIN guard (the model binds the
# `$schema` host at import; see `contract_model_domain`).
with contract_model_domain():
    from pydantic import TypeAdapter
    from analitiq.contracts.stream import StreamInput

_STREAM_ADAPTER = TypeAdapter(StreamInput)


def is_stream_doc(doc: Any) -> bool:
    """A stream binds a read `source` to write `destinations`: it is the only
    authored kind carrying both top-level. (A pipeline nests its source/destination
    connection refs under `connections`; it has no top-level `source`.)"""
    return isinstance(doc, dict) and "source" in doc and "destinations" in doc


def _validate_stream(doc: Any, doc_path=None, schema_url=None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
    return _model_findings(doc, _STREAM_ADAPTER) + _missing_schema_url_findings(doc)


register_kind(is_stream_doc, _validate_stream)
