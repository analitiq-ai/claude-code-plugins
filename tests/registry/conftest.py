"""Make `from _pins import …` resolve when this suite runs standalone.

`_pins.py` lives in `tests/connector_builder/` — the single place the suite
states which contract it exercises (its own docstring carries the argument).
The modules here import it, which works in a full `pytest` run only as a side
effect of `tests/connector_builder/` being collected first and left on
`sys.path`. This conftest makes the import order-independent, so
`pytest tests/registry/` collects on its own.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "connector_builder"))
