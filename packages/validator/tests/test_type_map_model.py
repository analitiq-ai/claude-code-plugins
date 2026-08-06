"""Cross-field rules of the type-map contract models — the single-document
validity the validator delegates to. The PR premise ("the model rejects it, so
the validator catches it") rests on these, so they are pinned directly.
"""
import pytest
from pydantic import TypeAdapter, ValidationError

from analitiq.contracts.type_map import (
    TypeMapReadDoc,
    TypeMapWriteDoc,
    normalize_native_type,
)

READ = TypeAdapter(TypeMapReadDoc)
WRITE = TypeAdapter(TypeMapWriteDoc)

# Capture fragments bounded to what a `Decimal128` parameter position admits.
# A read rule feeding a decimal position from an unbounded `\d+` is refused
# (`test_templated_capture_must_stay_inside_its_position`), so every fixture
# that is meant to be ACCEPTED captures within the family's range.
_P128 = r"[1-9]|[12]\d|3[0-8]"   # precision 1-38
_S128 = r"\d|[12]\d|3[0-8]"      # scale 0-38


def _units(family: str) -> str:
    """Capture fragment admitting exactly the units `family` allows.

    Read from the grammar rather than typed out: a unit capture reaching a
    sibling family's unit is refused the same way an over-wide decimal capture
    is, so these fixtures have to move with the vocabulary.
    """
    from analitiq.contracts.arrow_grammar import FAMILIES

    param = next(
        p for p in FAMILIES[family]["params"] if p["kind"] == "unit"
    )
    return "|".join(param["allowed"])


@pytest.mark.parametrize("raw,expected", [
    ("varchar", "VARCHAR"),
    ("VARCHAR", "VARCHAR"),
    ("  character  varying ", "CHARACTER VARYING"),
    ("timestamp\twithout time  zone", "TIMESTAMP WITHOUT TIME ZONE"),
    ("Int64", "INT64"),
])
def test_normalize_native_type_canonical(raw, expected):
    """The platform's single source of truth for read-match normalization:
    trim → collapse internal whitespace runs → uppercase."""
    assert normalize_native_type(raw) == expected


def _accepts(adapter, rules):
    adapter.validate_python(rules)


def _rejects(adapter, rules):
    with pytest.raises(ValidationError):
        adapter.validate_python(rules)


def test_empty_array_rejected():
    _rejects(READ, [])
    _rejects(WRITE, [])


def test_match_enum_and_required_keys():
    _rejects(READ, [{"match": "fuzzy", "native": "X", "canonical": "Utf8"}])
    _rejects(READ, [{"match": "exact", "native": "X"}])           # missing canonical
    _rejects(READ, [{"match": "exact", "native": "X", "canonical": "Utf8", "extra": 1}])


def test_exact_canonical_vocabulary():
    _accepts(READ, [{"match": "exact", "native": "STRING", "canonical": "Utf8"}])
    _rejects(READ, [{"match": "exact", "native": "STRING", "canonical": "NotArrow"}])


def test_canonical_rejects_bare_parameterized_types():
    # Must match the endpoint arrow vocabulary: parameterized types carry params
    # (issue #424), and the typed nested families are not executable vocabulary
    # at all (issue #81) — only the authored-shape markers are.
    for bad in ("Timestamp", "Decimal128", "Struct", "List(Int64)", "List<Int64>",
                "Struct<id:Int64>", "Map<Utf8, Int64>", "Interval(YEAR_MONTH)"):
        _rejects(READ, [{"match": "exact", "native": "X", "canonical": bad}])
    for ok in ("Timestamp(MICROSECOND)", "Decimal128(38, 9)", "Json"):
        _accepts(READ, [{"match": "exact", "native": "X", "canonical": ok}])


def test_canonical_rejects_trailing_newline():
    # `$` matches before a final newline; use fullmatch so `"Utf8\n"` is rejected
    # (consistent with the endpoint arrow_type check).
    _rejects(READ, [{"match": "exact", "native": "X", "canonical": "Utf8\n"}])


def test_templated_canonical_needs_literal_head():
    # A whole-value placeholder is invalid — substitutions are parameter-only.
    _rejects(READ, [{"match": "regex", "native": r"(?<type>\w+)", "canonical": "${type}"}])
    _accepts(READ, [{"match": "regex", "native": rf"DEC\((?<p>{_P128}),(?<s>{_S128})\)",
                     "canonical": "Decimal128(${p}, ${s})"}])


def test_templated_canonical_covers_all_temporal_enums():
    # The parameter-aware dummies must accept every temporal enum family, not
    # just microsecond-based ones (Time32 is SECOND/MILLISECOND).
    for base in ("Time32", "Time64", "Timestamp", "Duration"):
        _accepts(READ, [{"match": "regex", "native": rf"X\((?<u>{_units(base)})\)",
                         "canonical": f"{base}(${{u}})"}])


def test_literal_decimal_scale_must_not_exceed_precision():
    # Cross-parameter bound from the engine grammar (`scale <= precision`):
    # regex cannot express it, so the model enforces it wherever both sides
    # carry a value. Here that is the literal/literal case; the literal/capture
    # case is `test_literal_position_holds_against_the_capture_it_is_bounded_by`.
    _rejects(READ, [{"match": "exact", "native": "X", "canonical": "Decimal128(5, 6)"}])
    _rejects(WRITE, [{"match": "exact", "canonical": "Decimal256(10, 11)", "native": "NUMERIC"}])
    _accepts(READ, [{"match": "exact", "native": "X", "canonical": "Decimal128(5, 5)"}])
    # A literal scale of 9 needs a precision capture that cannot go below 9.
    _accepts(READ, [{"match": "regex", "native": r"DEC\((?<p>9|[12]\d|3[0-8])\)",
                     "canonical": "Decimal128(${p}, 9)"}])


def test_write_native_may_carry_column_hints():
    # `native` is free-form DDL: per-column hint placeholders are allowed on both
    # exact and regex write rules (the contract permits `VARCHAR(${length})`).
    _accepts(WRITE, [{"match": "exact", "canonical": "Utf8", "native": "VARCHAR(${length})"}])
    _accepts(WRITE, [{"match": "regex", "canonical": r"^Decimal128\((?<p>\d+)\)",
                      "native": "NUMERIC(${p}, ${length})"}])  # capture + free hint mixed


def test_exact_must_not_template():
    _rejects(READ, [{"match": "exact", "native": "X", "canonical": "Decimal128(${p})"}])


def test_exact_write_native_render_placeholders_validated():
    # A write exact `native` render may carry `${length}` hints, but malformed
    # placeholders (empty / unclosed) must be rejected too — Codex round 5.
    _accepts(WRITE, [{"match": "exact", "canonical": "Utf8", "native": "VARCHAR(${length})"}])
    _rejects(WRITE, [{"match": "exact", "canonical": "Utf8", "native": "VARCHAR(${})"}])
    _rejects(WRITE, [{"match": "exact", "canonical": "Utf8", "native": "VARCHAR(${length)"}])


def test_regex_rejects_python_named_group():
    _rejects(READ, [{"match": "regex", "native": "(?P<p>.*)", "canonical": "Utf8"}])


def test_regex_ecma_named_backreference_accepted():
    # An ECMA named backreference `\k<name>` is valid contract syntax; it must be
    # translated to Python's `(?P=name)` (not rejected as uncompilable) — Codex r4.
    _accepts(READ, [{"match": "regex", "native": r"(?<x>\w+)_\k<x>", "canonical": "Utf8"}])


def test_regex_must_compile():
    _rejects(READ, [{"match": "regex", "native": "([", "canonical": "Utf8"}])


def test_placeholder_needs_matching_capture():
    _rejects(READ, [{"match": "regex", "native": "NUMERIC", "canonical": "Timestamp(${unit})"}])
    _accepts(READ, [{"match": "regex",
                     "native": rf"TS\((?<unit>{_units('Timestamp')})\)",
                     "canonical": "Timestamp(${unit})"}])


def test_read_captured_native_must_not_discard_params_to_hardcoded_canonical():
    # Issue #917 Gap 1: a native that NAMES captures but maps to a literal
    # parameterized canonical silently coerces every source precision/scale/unit
    # to a by-example constant. Flag it (reverse of the placeholder→capture check).
    _rejects(READ, [{"match": "regex", "native": r"NUMERIC\((?<p>\d+),(?<s>\d+)\)",
                     "canonical": "Decimal128(38, 9)"}])
    _rejects(READ, [{"match": "regex", "native": r"TS\((?<u>\d+)\)",
                     "canonical": "Timestamp(MICROSECOND)"}])
    # Referencing the captures (templated canonical) is the correct mapping.
    _accepts(READ, [{"match": "regex", "native": rf"DEC\((?<p>{_P128}),(?<s>{_S128})\)",
                     "canonical": "Decimal128(${p}, ${s})"}])
    # A non-capturing group is the escape hatch for match-and-discard.
    _accepts(READ, [{"match": "regex", "native": r"NUMERIC\((?:\d+),(?:\d+)\)",
                     "canonical": "Decimal128(38, 9)"}])
    # A named capture mapping to a NON-parameterized canonical drops nothing.
    _accepts(READ, [{"match": "regex", "native": r"VARCHAR\((?<n>\d+)\)", "canonical": "Utf8"}])
    # The container canonical `Json` (no `()`) is not param-lossy here.
    _accepts(READ, [{"match": "regex", "native": r"ARR<(?<t>\w+)>", "canonical": "Json"}])
    # The reverse check is read-only: a write rule renders free-form native DDL,
    # so a canonical-side capture unused in the native is allowed.
    _accepts(WRITE, [{"match": "regex", "canonical": r"^Decimal128\((?<p>\d+)\)",
                      "native": "NUMERIC(20, 4)"}])


def test_templated_read_canonical_head_validated():
    # The non-placeholder head + shape of a templated read canonical is validated
    # (one placeholder per parameter position).
    _rejects(READ, [{"match": "regex", "native": rf"DEC\((?<p>{_P128}),(?<s>{_S128})\)", "canonical": "Decmal128(${p}, ${s})"}])
    _accepts(READ, [{"match": "regex", "native": rf"DEC\((?<p>{_P128}),(?<s>{_S128})\)", "canonical": "Decimal128(${p}, ${s})"}])


def test_schemaless_container_must_not_collapse_to_scalar():
    # Detection is DB-agnostic (by shape): a native written with container syntax
    # (`<...>` or `[]`) must not map to a scalar canonical. Bare vendor names
    # (`JSONB`) are intentionally not special-cased.
    _rejects(READ, [{"match": "exact", "native": "array<int>", "canonical": "Utf8"}])
    _accepts(READ, [{"match": "exact", "native": "array<int>", "canonical": "Json"}])
    _rejects(READ, [{"match": "exact", "native": "integer[]", "canonical": "Utf8"}])
    _accepts(READ, [{"match": "exact", "native": "JSONB", "canonical": "Utf8"}])  # bare name: not flagged


def test_schemaless_native_maps_to_container_canonicals_only():
    # The typed nested families are rejected outright (issue #81 — outside the
    # vocabulary). `Object`/`List` still VALIDATE as renders (a string rule
    # can't carry the sibling sub-schemas they need, but the model can't know
    # that) — Json-only for read renders is craft guidance in
    # spec-type-maps.md; the ENFORCED rule is "structured native must map to a
    # container canonical, never a scalar".
    _accepts(READ, [{"match": "exact", "native": "union<int, str>", "canonical": "Json"}])
    for canonical in ("DenseUnion<a:Int64>", "SparseUnion<a:Int64>",
                      "Dictionary<Int32, Utf8>", "RunEndEncoded<Int32, Int64>"):
        _rejects(READ, [{"match": "exact", "native": "union<int, str>", "canonical": canonical}])
    # ...and a structured native → scalar canonical is still flagged.
    _rejects(READ, [{"match": "exact", "native": "union<int, str>", "canonical": "Utf8"}])


def test_write_direction_matches_canonical_renders_native():
    # write: canonical is the matcher (vocabulary-checked on exact), native is free-form DDL.
    _accepts(WRITE, [{"match": "exact", "canonical": "Int64", "native": "BIGINT"}])
    _rejects(WRITE, [{"match": "exact", "canonical": "NotArrow", "native": "BIGINT"}])
    _accepts(WRITE, [{"match": "regex", "canonical": r"^Decimal128\((?<p>\d+)\)",
                      "native": "NUMERIC(${p})"}])


def test_templated_capture_must_stay_inside_its_position():
    """A `${name}` render is only as safe as the capture feeding it.

    An unbounded `\\d+` in a decimal position renders canonicals the contract
    itself rejects, and nothing downstream re-checks a substituted type on the
    destination path — so the rule is refused at authoring, not at write time.
    """
    _rejects(READ, [{"match": "regex",
                     "native": rf"N\((?<p>{_P128}),(?<s>\d+)\)",
                     "canonical": "Decimal128(${p}, ${s})"}])
    _rejects(READ, [{"match": "regex",
                     "native": rf"N\((?<p>\d+),(?<s>{_S128})\)",
                     "canonical": "Decimal128(${p}, ${s})"}])
    # Leading zeros are not canonical spellings either.
    _rejects(READ, [{"match": "regex", "native": r"N\((?<p>\d{2})\)",
                     "canonical": "Decimal128(${p}, 0)"}])
    _accepts(READ, [{"match": "regex",
                     "native": rf"N\((?<p>{_P128}),(?<s>{_S128})\)",
                     "canonical": "Decimal128(${p}, ${s})"}])
    # Decimal256 carries the same bound at its own ceiling.
    _rejects(READ, [{"match": "regex", "native": r"N\((?<p>\d+),(?<s>\d+)\)",
                     "canonical": "Decimal256(${p}, ${s})"}])
    _accepts(READ, [{"match": "regex",
                     "native": r"N\((?<p>39|[4-6]\d|7[0-6]),(?<s>\d|[1-6]\d|7[0-6])\)",
                     "canonical": "Decimal256(${p}, ${s})"}])


def test_partially_templated_bound_resolves_to_the_literal_sibling():
    """The ceiling for `${s}` in `Decimal128(5, ${s})` is 5, not the family's 38.

    Resolving it to the static ceiling is what let a hardcoded precision of 5
    admit a scale of 38.
    """
    _rejects(READ, [{"match": "regex", "native": r"N\((?<s>\d|[12]\d|3[0-8])\)",
                     "canonical": "Decimal128(5, ${s})"}])
    _accepts(READ, [{"match": "regex", "native": r"N\((?<s>[0-5])\)",
                     "canonical": "Decimal128(5, ${s})"}])


def test_literal_position_holds_against_the_capture_it_is_bounded_by():
    """`Decimal128(${p}, 38)` is satisfiable only at `p == 38`.

    No literal sibling exists to resolve the bound against, so the decision
    comes from the capture: any precision it can match below the literal scale
    renders a canonical the contract rejects.
    """
    _rejects(READ, [{"match": "regex", "native": rf"N\((?<p>{_P128})\)",
                     "canonical": "Decimal128(${p}, 38)"}])
    _accepts(READ, [{"match": "regex", "native": r"N\((?<p>38)\)",
                     "canonical": "Decimal128(${p}, 38)"}])
    # A scale of 0 is below every precision the family admits, so it holds.
    _accepts(READ, [{"match": "regex", "native": rf"N\((?<p>{_P128})\)",
                     "canonical": "Decimal128(${p}, 0)"}])


def test_both_positions_templated_is_left_wide_on_purpose():
    """What the narrowing deliberately does not reach.

    With both sides of the bound templated, each capture is judged against its
    own position only — so a pair like `(1, 38)`, reachable from these two
    captures, is not refused. Deciding it needs the joint language of two
    captures over one native string, which the check does not compute.
    """
    _accepts(READ, [{"match": "regex",
                     "native": rf"N\((?<p>{_P128}),(?<s>{_S128})\)",
                     "canonical": "Decimal128(${p}, ${s})"}])


def test_a_capture_wider_than_the_alphabet_is_still_refused():
    """"Matches no probe" is a proof, not a gap.

    The probe alphabet holds every value an in-scope position admits, so a
    capture matching none of it renders nothing admissible however wide it is —
    a `\\d{7,}` precision can only ever produce `Decimal128(1234567, 0)`.
    """
    _rejects(READ, [{"match": "regex", "native": r"N\((?<p>\d{7,})\)",
                     "canonical": "Decimal128(${p}, 0)"}])
    # Non-digit spellings land in the same place, for the same reason.
    _rejects(READ, [{"match": "regex", "native": r"N\((?<p>0x[0-9A-F]+)\)",
                     "canonical": "Decimal128(${p}, 0)"}])


def test_a_placeholder_parameter_position_must_carry_nothing_else():
    """A concatenated or literal-prefixed position renders the product of its
    parts, which no single capture's language answers — so it is refused rather
    than filed under literals, where the digit guard would wave it through."""
    _rejects(READ, [{"match": "regex", "native": r"N\((?<a>\d)(?<b>\d)\)",
                     "canonical": "Decimal128(${a}${b}, 0)"}])
    _rejects(READ, [{"match": "regex", "native": r"N\((?<p>\d)\)",
                     "canonical": "Decimal128(1${p}, 0)"}])


def test_unreadable_capture_proves_nothing():
    """A capture the check cannot read must not be reported as refusable."""
    # A backreference to a group declared OUTSIDE the capture does not compile
    # on its own; the rule stays accepted rather than being refused on a
    # failure to analyse it.
    _accepts(READ, [{"match": "regex",
                     "native": r"(?<a>[0-3])N\((?<p>[1-9]\k<a>)\)",
                     "canonical": "Decimal128(${p}, 0)"}])


def test_families_without_a_cross_bound_are_capture_checked_too():
    """A cross-parameter bound is not what makes a position worth reading.

    Every position states what it admits; a capture that can reach past it
    renders a non-canonical whether or not a sibling is involved. `Time64`
    admits neither of `Time32`'s units, `FixedSizeBinary` admits no width of 0,
    and a word-class capture reaches values no unit position admits at all.
    """
    _rejects(READ, [{"match": "regex", "native": r"TS\((?<u>[A-Z]+)\)",
                     "canonical": "Timestamp(${u})"}])
    _rejects(READ, [{"match": "regex",
                     "native": rf"T\((?<u>{_units('Timestamp')})\)",
                     "canonical": "Time64(${u})"}])
    _rejects(READ, [{"match": "regex", "native": r"BINARY\((?<n>\d+)\)",
                     "canonical": "FixedSizeBinary(${n})"}])
    _accepts(READ, [{"match": "regex", "native": r"BINARY\((?<n>[1-9]\d*)\)",
                     "canonical": "FixedSizeBinary(${n})"}])


def test_a_position_with_no_probe_alphabet_stands():
    """A timezone's admissible set is an open pattern the check cannot
    enumerate, so a capture feeding one is not interrogated."""
    _accepts(READ, [{"match": "regex", "native": r"TS\((?<z>[A-Z/a-z_]+)\)",
                     "canonical": "Timestamp(SECOND, ${z})"}])


@pytest.mark.parametrize("native,name,expected", [
    (r"^N\((?<p>\d+)\)$", "p", r"\d+"),
    # A `)` inside a character class does not close the group.
    (r"^N(?<p>[0-9)]+)$", "p", "[0-9)]+"),
    # Neither does an escaped one.
    (r"^N(?<p>\d+\))$", "p", r"\d+\)"),
    # Nested groups are consumed whole.
    (r"^N(?<p>(?:1|2)(?<q>\d))$", "p", r"(?:1|2)(?<q>\d)"),
    (r"^N\((?<p>\d+)\)$", "absent", None),
    (r"^N\((?<p>\d+$", "p", None),  # never closes
])
def test_named_group_source_extraction(native, name, expected):
    from analitiq.contracts.type_map import _named_group_source

    assert _named_group_source(native, name) == expected
