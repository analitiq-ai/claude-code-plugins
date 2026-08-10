"""One census module per contract source area; :mod:`census` aggregates them.

Their own subpackage so the aggregation walks areas and nothing else — a
module that is not an area cannot be mistaken for one that forgot its
``PROSE_OBLIGATIONS``.
"""
