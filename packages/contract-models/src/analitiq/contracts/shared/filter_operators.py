"""The filter-operator vocabulary, owned once.

More than one authored document speaks this vocabulary and none of them may own
it. A stream names the comparison it wants (`stream.Filter.operator`); an
api-endpoint names where each comparison lands on the wire
(`endpoints.ReadOperation.filters`). `stream` already imports `endpoints`, so the
vocabulary cannot live in either without one of them carrying a hand-maintained
copy of the other's list — the drift surface
`.claude/rules/no-drift-surfaces.md` forbids.

The split is by read path, not by preference, and it is visible in the members
themselves: the database-only members are SQL's own spellings, which a provider has
no general way to be asked for, and the API-only members are ordinary string
predicates a dialect expresses through its pattern operator instead. Read as a claim
about a run — a database read becoming a predicate, an API read becoming a request —
it is a reading of the engine as it stands; the vocabularies stand on the members
either way.
"""
from typing import Literal, get_args

#: Comparisons every read path can express.
COMMON_FILTER_OPERATORS: tuple[str, ...] = (
    "eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in",
)

#: Comparisons only a dialect can compile.
DB_ONLY_FILTER_OPERATORS: tuple[str, ...] = ("is_null", "is_not_null", "like", "ilike")

#: Comparisons only a provider can be asked for.
API_ONLY_FILTER_OPERATORS: tuple[str, ...] = ("contains", "starts_with", "ends_with")

DB_FILTER_OPERATORS: frozenset[str] = frozenset(
    COMMON_FILTER_OPERATORS + DB_ONLY_FILTER_OPERATORS
)
API_FILTER_OPERATORS: frozenset[str] = frozenset(
    COMMON_FILTER_OPERATORS + API_ONLY_FILTER_OPERATORS
)

#: Operators taking no operand: a filter naming one carries no `value`. A tuple,
#: not a set — it renders into a published schema's `enum`, where member order is
#: part of the bytes.
UNARY_FILTER_OPERATORS: tuple[str, ...] = ("is_null", "is_not_null")

#: The structural floor a stream filter is typed against — the union of the scope
#: vocabularies, because only the source binding knows which read path a filter
#: will be rendered onto. `StreamSource` narrows it to the scope's subset.
FilterOperator = Literal[
    "eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in",  # common
    "is_null", "is_not_null", "like", "ilike",              # database-only
    "contains", "starts_with", "ends_with",                 # api-only
]

#: The subset an api-endpoint may name a landing site for.
ApiFilterOperator = Literal[
    "eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in",
    "contains", "starts_with", "ends_with",
]

# Single-source guards: each Literal must equal the set built from the member
# tuples. Adding an operator to one place but not the other then fails loudly at
# import, not silently at runtime.
if set(get_args(FilterOperator)) != DB_FILTER_OPERATORS | API_FILTER_OPERATORS:
    raise AssertionError(
        "FilterOperator Literal must equal the union of the DB and API operator vocabularies"
    )
if set(get_args(ApiFilterOperator)) != API_FILTER_OPERATORS:
    raise AssertionError(
        "ApiFilterOperator Literal must equal the API operator vocabulary"
    )
if not set(UNARY_FILTER_OPERATORS) <= DB_FILTER_OPERATORS:
    raise AssertionError("every unary operator must be a member of a scope vocabulary")
