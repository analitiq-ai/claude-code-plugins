"""The prose census data — one module per contract source area, aggregated
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

Aggregation is dynamic — every module in this package is imported (in sorted
name order, so the tuple is deterministic) and its ``PROSE_OBLIGATIONS``
concatenated. A hand-kept module list would make a new area file on disk
silently dead: never aggregated, its entries invisible to the lint yet still
"restampable" by the maintenance script. With dynamic aggregation a stray
file's entries surface immediately (as duplicates or as entries for unknown
sites), and a module without a ``PROSE_OBLIGATIONS`` tuple fails loudly here.
"""
from __future__ import annotations

import importlib
import pkgutil

from analitiq.contracts.shared.advisory_prose import ProseObligation


def _aggregate() -> tuple[ProseObligation, ...]:
    entries: list[ProseObligation] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        module = importlib.import_module(f"{__name__}.{info.name}")
        entries.extend(module.PROSE_OBLIGATIONS)
    return tuple(entries)


PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = _aggregate()
