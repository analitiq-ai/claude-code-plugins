"""The identity of one prose site, and nothing else.

Its own module so :mod:`census.obligation` can name a site without importing
:mod:`census.sites`. That edge was what closed the import cycle: ``census``
aggregates the entries, an entry needs the key type, and ``sites`` reaches back
for the aggregated entries to diff against. Here the dependency runs one way —
this module imports nothing from ``census`` and nothing from the contract
package, so every other census module can depend on it and none of them can
depend on each other through it.

Keeping it stdlib-only is load-bearing for the same reason ``obligation`` is:
tooling reads the census by class name, without pulling in the contract models
or pydantic to do it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteKey:
    """Identity of one prose site — the ONE definition of the site label.

    ``field`` is ``None`` for a class docstring. Every surface that names a
    site (``ProseSite``, ``ProseObligation``, the lint's failure lines, the
    maintenance script's output) formats through :attr:`label`, so the
    rendering can never fork between them.
    """

    model: str
    field: str | None = None

    @property
    def label(self) -> str:
        return f"{self.model}.{self.field}" if self.field else f"{self.model} (docstring)"
