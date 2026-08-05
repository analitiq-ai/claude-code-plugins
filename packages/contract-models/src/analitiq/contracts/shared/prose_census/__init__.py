"""The prose census data — one module per contract source area, concatenated
here into :data:`PROSE_OBLIGATIONS`.

This is the canonical home of the census entries;
:mod:`analitiq.contracts.shared.advisory_prose` keeps the datum
(:class:`ProseObligation`), the shared waiver reasons, and the frozen
:data:`NORMATIVE_PATTERN` tripwire. Area modules import only
``advisory_prose`` — never a contract model — so the census stays readable
without pydantic. The live-vs-census diff both consumers assert on is
:func:`analitiq.contracts.shared.introspect.census_report`.

``connector.py`` is large enough to warrant two area files, split at its
value-expression section; every other area file covers one source module (or,
for :mod:`.connection_shared` and :mod:`.pipelines`, a few small ones).
"""
from __future__ import annotations

from analitiq.contracts.shared.advisory_prose import ProseObligation

from . import (
    connection_shared,
    connector_auth_contract,
    connector_transports_document,
    endpoints,
    pipelines,
    stream,
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    *connection_shared.PROSE_OBLIGATIONS,
    *connector_auth_contract.PROSE_OBLIGATIONS,
    *connector_transports_document.PROSE_OBLIGATIONS,
    *endpoints.PROSE_OBLIGATIONS,
    *stream.PROSE_OBLIGATIONS,
    *pipelines.PROSE_OBLIGATIONS,
)
