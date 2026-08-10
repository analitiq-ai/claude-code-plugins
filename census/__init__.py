"""The prose census data — one module per contract source area, aggregated
here into :data:`PROSE_OBLIGATIONS`.

This is the canonical home of the census entries;
:mod:`census.obligation` keeps the datum
(:class:`ProseObligation`) and the shared waiver reasons. Area modules import only
``census.obligation`` — never a contract model — so the census stays readable
without pydantic. The live-vs-census diff both consumers assert on is
:func:`census.sites.census_report`.

``connector.py`` is large enough to warrant two area files, split at its
value-expression section; every other area file covers one source module (or,
for :mod:`census.areas.connection_shared` and :mod:`census.areas.pipelines`, a
few small ones).

Aggregation is dynamic — every module in :mod:`census.areas` is imported (in
sorted name order, so the tuple is deterministic) and its ``PROSE_OBLIGATIONS``
concatenated. A hand-kept module list would make a new area file on disk
silently dead: never aggregated, its entries invisible to the lint yet still
"restampable" by the maintenance script. With dynamic aggregation a stray
file's entries surface immediately (as duplicates or as entries for unknown
sites), and a module without a ``PROSE_OBLIGATIONS`` tuple fails loudly here.
"""
from __future__ import annotations

import importlib
import pkgutil

from census import areas
from census.obligation import ProseObligation


def _aggregate() -> tuple[ProseObligation, ...]:
    entries: list[ProseObligation] = []
    for info in sorted(pkgutil.iter_modules(areas.__path__), key=lambda i: i.name):
        module = importlib.import_module(f"{areas.__name__}.{info.name}")
        obligations = getattr(module, "PROSE_OBLIGATIONS", None)
        if obligations is None:
            raise AttributeError(
                f"census area module {module.__name__!r} exports no "
                "PROSE_OBLIGATIONS — every module in this package is "
                "aggregated automatically and must export a "
                "PROSE_OBLIGATIONS tuple of ProseObligation entries"
            )
        entries.extend(obligations)
    return tuple(entries)


PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = _aggregate()
