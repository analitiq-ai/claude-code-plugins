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


def rule_by_id(rule_id: str) -> RuleRecord:
    """The record naming this id.

    The public door onto the same index :func:`violation` reads, so a caller
    outside this module — the validator's own ``finding()``, deriving a
    finding's severity from the rule it names — resolves an id through the
    same path ``violation`` does, rather than a second lookup that could
    disagree with it. Raises ``KeyError`` for an id no record defines.
    """
    return _load()[1][rule_id]


class RuleViolation(ValueError):
    """The error an enforcer raises, with the rule and complaint it names
    carried as attributes rather than encoded into the message text.

    Pydantic re-wraps a ``@model_validator``/``@field_validator``'s raised
    ``ValueError`` — prefixing its rendered message with ``"Value error, "``
    and flattening every one raised the same way to the generic error type
    ``"value_error"`` — but preserves the original exception object itself at
    ``ValidationError.errors()[i]["ctx"]["error"]``. A finding built from a
    model rejection reads `rule_id`/`message_id` off that original object
    rather than parsing the wrapped string, which pydantic's own wrapping
    would defeat.

    `rule_id` is `None` for a complaint no record claims — the
    ``rule=None`` framework fallback ``rules/SCHEMA.md`` documents, minted
    directly rather than through :func:`violation`, which requires a
    resolvable id. `path` is `None` unless the enforcer knows a location more
    precise than pydantic's own ``err["loc"]`` (`rules/SCHEMA.md`'s Findings
    section names no format for it); ``_model_findings`` only reads it off a
    violation it unpacks from a :class:`MultiRuleViolation` — a bare
    `RuleViolation` raised on its own resolves its finding's `path` from
    ``err["loc"]`` alone, whether or not it sets one.
    """

    def __init__(
        self, rule_id: str | None, message_id: str, message: str, *,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.rule_id = rule_id
        self.message_id = message_id
        self.message = message
        self.path = path


class MultiRuleViolation(ValueError):
    """Every :class:`RuleViolation` found while validating one document,
    where a ``@model_validator`` gets exactly one raise to speak for all of
    them.

    Pydantic's `mode="after"` validator has two outcomes — return the model,
    or raise once — so an enforcer that walks a whole document and finds
    several unrelated complaints cannot raise once per complaint. It does not
    follow that the document can only be told about one of them: this
    exception carries the whole list, and ``_model_findings``
    (`analitiq.validator._core`) unpacks it into one finding per entry — each
    with its own `rule_id`, `message_id` and `path` — rather than folding
    them into a single attributed-to-one, joined-text finding. An enforcer
    with only one complaint raises a bare `RuleViolation` or `ValueError`
    instead; ``_model_findings`` recognises both shapes, each through its own
    branch.
    """

    def __init__(self, violations: list[RuleViolation]) -> None:
        if not violations:
            # An empty list has nothing for `_model_findings` to expand into a
            # finding — pydantic still recorded a `ValidationError` for this
            # raise, so a caller reaching here with nothing to report would
            # make that rejection surface as zero findings, and a document
            # pydantic refused would read as passed. Refusing to construct is
            # what keeps that impossible rather than merely unlikely.
            raise ValueError("MultiRuleViolation requires at least one RuleViolation")
        super().__init__("; ".join(v.message for v in violations))
        self.violations = violations


def violation(
    rule_id: str, message_id: str, detail: str, *, path: str | None = None,
) -> RuleViolation:
    """The error an enforcer raises, with the rule and complaint it applies
    already named.

    A finding is actionable when it carries the rule id, because that is what
    the plugin prose cites, and the statement, because the id alone says
    nothing to a reader without the reference open. `message_id` is which of
    this rule's distinct complaints this one is (`rules/SCHEMA.md`,
    "Findings") — a rule can be violated in more than one way, and a consumer
    branches on this rather than parsing `detail`. Both the statement and the
    rule id are read from the record, so a reworded rule rewords its own
    diagnostic. An id no record defines raises ``KeyError`` here rather than
    emitting a citation that resolves to nothing. `path` is optional; see
    `RuleViolation` for what omitting it means for the resulting finding.
    """
    rule = rule_by_id(rule_id)
    message = f"[{rule.id}] {' '.join(rule.statement.split())} ({detail})"
    return RuleViolation(rule.id, message_id, message, path=path)


def unattributed_violation(detail: str, *, path: str | None = None) -> RuleViolation:
    """A complaint no registered rule covers: `rule_id=None`,
    `message_id="value_error"` — the same pairing `_model_findings`
    (`analitiq.validator._core`) already falls back to for a plain
    `ValueError` reaching it, since pydantic flattens any `ValueError`'s
    `err["type"]` to that literal string. One place to mint that pairing
    rather than every call site retyping it.
    """
    return RuleViolation(None, "value_error", detail, path=path)


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
            raise violation("RULE-HTTP-001", "header-set-and-removed", f"overlap={overlap!r}")
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
                raise violation("RULE-HTTP-002", "content-length-header-declared", where)
        return self

    @model_validator(mode="after")
    def _no_content_type_header(self):
        for name, where in self.declared_header_names():
            if self._matches(name, FORBIDDEN_CONTENT_TYPE_HEADER):
                raise violation("RULE-HTTP-003", "content-type-header-declared", where)
        return self
