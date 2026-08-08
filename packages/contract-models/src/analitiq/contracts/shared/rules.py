"""How the contract models reach the rule registry, and how they cite it.

The rules live in ``rules/records/*.yaml``, one record per rule (schema:
``rules/SCHEMA.md``), compiled by ``scripts/render_rules.py`` into the JSON this
package ships and loaded by :mod:`rule_record`. This module exposes them —
:func:`all_rules` for the renderers, the census and the tests — and gives an
enforcer :func:`violation`, so a rejection arrives naming the rule its prose
cites.

**Enforcement is ordinary Python.** A rule a model rejects in-process is a
``@model_validator`` on that model, named by the record's ``validator`` and
resolved against the live class by ``render_rules.py``. There is no data-driven
dispatch: a rule is applied by a symbol that exists or by nothing at all, and
the record cannot claim a third thing. Every other tier is enforced somewhere
else entirely, or nowhere, and the record says which.

A rule binding several models that share a shape is a mixin here, so the check
is written once and every target inherits it.
"""
from __future__ import annotations

from functools import cache
from typing import Any, Callable

from pydantic import model_validator

from .rule_record import RuleRecord, load_records


# --- The registry -----------------------------------------------------------


@cache
def _load() -> tuple[list[RuleRecord], dict[str, RuleRecord]]:
    """Read the compiled registry once, indexed by id.

    ``functools.cache`` makes this run-once without a module-global flag.
    """
    records = load_records()
    return records, {record.id: record for record in records}


def all_rules() -> list[RuleRecord]:
    """The whole registry — every tier, every scope, ordered by id."""
    return list(_load()[0])


def violation(rule_id: str, detail: str) -> ValueError:
    """The error an enforcer raises, with the rule it applies already named.

    A finding is actionable when it carries the id, because the id is what the
    plugin prose cites, and the statement, because the id alone says nothing to
    a reader without the reference open. Both are read from the record, so a
    reworded rule rewords its own diagnostic. An id no record defines raises
    ``KeyError`` here rather than emitting a citation that resolves to nothing.
    """
    rule = _load()[1][rule_id]
    return ValueError(f"[{rule.id}] {' '.join(rule.statement.split())} ({detail})")


# --- Shared primitives ------------------------------------------------------


def find_duplicates(seq: Any, key: Callable[[Any], Any] | None = None) -> list:
    """Return the sorted, de-duplicated keys that appear more than once in seq.

    The one uniqueness primitive — every rule about a list holding no repeats
    calls it, so the algorithm is defined exactly once and the several rules
    cannot disagree about what "the same" means.
    """
    seen: set = set()
    dups: set = set()
    for el in seq or ():
        k = key(el) if key else el
        if k in seen:
            dups.add(k)
        else:
            seen.add(k)
    return sorted(dups)


# --- Rules binding several models -------------------------------------------


class HeaderMergeRules:
    """A block that both declares HTTP headers and names headers to remove.

    Enforces RULE-HTTP-001 for every such block — a request, an auth operation
    template, a transport, and the transport defaults all resolve headers the
    same way, so they can all contradict themselves the same way. Mixed in
    rather than repeated: one check, and a model gains it by inheriting.
    """

    @model_validator(mode="after")
    def _headers_not_both_set_and_removed(self):
        # Direct attribute access, so a class mixing this in without the fields
        # fails at construction rather than silently enforcing nothing.
        headers, removals = self.headers, self.headers_remove
        if not headers or not removals:
            return self
        # Header names are case-insensitive on the wire, so `Accept` in one list
        # and `accept` in the other is the same contradiction.
        overlap = sorted({h.lower() for h in headers} & {h.lower() for h in removals})
        if overlap:
            raise violation("RULE-HTTP-001", f"overlap={overlap!r}")
        return self
