"""How the contract models reach the rule registry, and how they cite it.

The rules live in ``rules/records/*.yaml``, one record per rule (schema:
``rules/SCHEMA.md``), compiled by ``scripts/render_rules.py`` into the JSON this
package ships and loaded by :mod:`rule_record`. This module exposes them —
:func:`all_rules` for the renderers, the census and the tests — and gives an
enforcer :func:`violation`, so a rejection arrives naming the rule its prose
cites.

**Enforcement is ordinary Python**, in one of two places depending on how much
a check must see. A rule one document settles on its own is a
``@model_validator`` on that model. A rule needing a second document in hand —
a sibling type map, the connector an endpoint ships beside, the streams an
assembled run pins — is a check in ``analitiq.validator``. Either way the
record's ``validator`` names the module that is imported and the symbol on it,
resolved against the live code by ``render_rules.py``. There is no data-driven
dispatch: a rule is applied by a symbol that exists or by nothing at all, and
the record cannot claim a third thing. A tier neither package enforces is
applied somewhere else entirely, or nowhere, and the record says which.

A rule binding several models that share a shape is a mixin here, so the check
is written once and every target inherits it.
"""
from __future__ import annotations

from functools import cache
from typing import Any, Callable

from pydantic import model_validator

from analitiq.contracts.value_expression import header_name_key

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
        # `Accept` in one list and `accept` — or ` Accept` — in the other is
        # the same contradiction, so both sides reduce to what the wire reads.
        overlap = sorted(
            {header_name_key(h) for h in headers}
            & {header_name_key(h) for h in removals}
        )
        if overlap:
            raise violation("RULE-HTTP-001", f"overlap={overlap!r}")
        return self


#: The header name RULE-HTTP-002 forbids, named rather than inline so the rule
#: registry can resolve and print it (`RuleRecord.symbol`, mechanism
#: `reserved_names`). A frozenset because that is the shape the renderer
#: expects, not a claim about how the forbidden set will grow.
FORBIDDEN_CONTENT_LENGTH_HEADER: frozenset[str] = frozenset({"content-length"})

#: The header name RULE-HTTP-003 forbids, same reasoning.
FORBIDDEN_CONTENT_TYPE_HEADER: frozenset[str] = frozenset({"content-type"})


class DeclaredHeaderNames:
    """A block that names an HTTP header the engine puts on a request.

    Enforces RULE-HTTP-002 and RULE-HTTP-003 for every such block. Mixed in
    rather than repeated, for the reason `HeaderMergeRules` is: each check is
    one check, and a model gains it by inheriting.

    A rule here is about the NAME a block writes down, never the value, so
    each reads the same list — which is why the mixin exposes the names rather
    than the map. A block that names a header somewhere other than a `headers`
    map overrides `declared_header_names` and inherits the checks unchanged —
    a block qualifies when some field of it becomes a header name on the wire,
    however the field is spelled, which is how the write mode's idempotency
    declaration joins.
    """

    @staticmethod
    def _matches(name: str, refused: frozenset[str]) -> bool:
        """Whether an authored name is one of `refused`, as a wire reader sees it.

        Reduced through `header_name_key`, so matching a name here and
        matching one anywhere else in the contract mean the same thing;
        letting the spelling decide whether a rule applies is what that
        function exists to prevent.
        """
        return header_name_key(name) in refused

    def declared_header_names(self) -> list[tuple[str, str]]:
        """Each header name this block names, paired with where it named it.

        The site travels with the name so the finding lands on the header the
        author wrote rather than on the block holding it — the same reason
        `ValueExpressionScopes` walks a map per entry.
        """
        # Direct attribute access, so a class mixing this in without the field
        # fails at construction rather than silently enforcing nothing.
        return [(name, f"headers.{name}") for name in (self.headers or {})]

    @model_validator(mode="after")
    def _no_content_length_header(self):
        for name, where in self.declared_header_names():
            if self._matches(name, FORBIDDEN_CONTENT_LENGTH_HEADER):
                raise violation("RULE-HTTP-002", where)
        return self

    @model_validator(mode="after")
    def _no_content_type_header(self):
        for name, where in self.declared_header_names():
            if self._matches(name, FORBIDDEN_CONTENT_TYPE_HEADER):
                raise violation("RULE-HTTP-003", where)
        return self
