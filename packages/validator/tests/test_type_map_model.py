"""Cross-field rules of the type-map contract models — the single-document
validity the validator delegates to. The PR premise ("the model rejects it, so
the validator catches it") rests on these, so they are pinned directly.
"""
import string

import pytest
import re2
from pydantic import TypeAdapter, ValidationError

from analitiq.contracts import type_map
from analitiq.contracts.type_map import (
    TYPE_MAP_READ_SCHEMA_URL,
    TYPE_MAP_WRITE_SCHEMA_URL,
    TypeMapReadDoc,
    TypeMapWriteDoc,
    case_dead_atoms,
    compile_matcher,
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

    # StopIteration here is the failure signal working, not a case to guard.
    param = next(  # skipcq: PTC-W0063
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


def _wrap(adapter, rules):
    """A bare rule list wrapped in the published `{$schema, direction, rules}`
    type-map document shape, direction inferred from which adapter it targets."""
    direction = "read" if adapter is READ else "write"
    schema_url = TYPE_MAP_READ_SCHEMA_URL if adapter is READ else TYPE_MAP_WRITE_SCHEMA_URL
    return {
        "$schema": schema_url,
        "direction": direction,
        "rules": rules,
    }


def _accepts(adapter, rules):
    adapter.validate_python(_wrap(adapter, rules))


def _rejects(adapter, rules):
    with pytest.raises(ValidationError):
        adapter.validate_python(_wrap(adapter, rules))


def test_empty_array_rejected():
    _rejects(READ, [])
    _rejects(WRITE, [])


# ---------------------------------------------------------------------------
# The document envelope itself: `$schema` + `direction` + `rules`, each
# required and each pinned — the shape `_wrap`'s helpers exercise, never
# their own field-level rejections.
# ---------------------------------------------------------------------------

_ONE_READ_RULE = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]
_ONE_WRITE_RULE = [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}]


def test_missing_schema_url_rejected():
    doc = _wrap(READ, _ONE_READ_RULE)
    del doc["$schema"]
    with pytest.raises(ValidationError):
        READ.validate_python(doc)


def test_missing_direction_rejected():
    doc = _wrap(READ, _ONE_READ_RULE)
    del doc["direction"]
    with pytest.raises(ValidationError):
        READ.validate_python(doc)


def test_missing_rules_rejected():
    doc = _wrap(READ, _ONE_READ_RULE)
    del doc["rules"]
    with pytest.raises(ValidationError):
        READ.validate_python(doc)


def test_unknown_top_level_key_rejected():
    doc = _wrap(READ, _ONE_READ_RULE)
    doc["extra"] = 1
    with pytest.raises(ValidationError):
        READ.validate_python(doc)


def test_direction_literal_must_match_the_adapter():
    """The invariant `direction` exists to express: a document graded against
    one direction's adapter whose own `direction` names the other is a model
    error, not a silent resolution. That is what lets a caller holding a
    direction from somewhere other than the document — an external caller of
    `type_map_findings`, which takes the direction to grade against as an
    argument — grade a map against that direction and have the disagreement
    reported.
    """
    read_doc = _wrap(READ, _ONE_READ_RULE)
    read_doc["direction"] = "write"
    with pytest.raises(ValidationError):
        READ.validate_python(read_doc)

    write_doc = _wrap(WRITE, _ONE_WRITE_RULE)
    write_doc["direction"] = "read"
    with pytest.raises(ValidationError):
        WRITE.validate_python(write_doc)


def test_schema_url_literal_must_match_the_direction():
    read_doc = _wrap(READ, _ONE_READ_RULE)
    read_doc["$schema"] = _wrap(WRITE, _ONE_WRITE_RULE)["$schema"]
    with pytest.raises(ValidationError):
        READ.validate_python(read_doc)


def test_match_enum_and_required_keys():
    _rejects(READ, [{"match": "fuzzy", "native_type": "X", "arrow_type": "Utf8"}])
    _rejects(READ, [{"match": "exact", "native_type": "X"}])           # missing canonical
    _rejects(READ, [{"match": "exact", "native_type": "X", "arrow_type": "Utf8", "extra": 1}])


def test_exact_canonical_vocabulary():
    _accepts(READ, [{"match": "exact", "native_type": "STRING", "arrow_type": "Utf8"}])
    _rejects(READ, [{"match": "exact", "native_type": "STRING", "arrow_type": "NotArrow"}])


def test_canonical_rejects_bare_parameterized_types():
    # Must match the endpoint arrow vocabulary: a parameterized family must
    # carry its params — a bare head like `Timestamp` names no type — and the
    # typed nested families are not executable vocabulary at all; only the
    # authored-shape markers are.
    for bad in ("Timestamp", "Decimal128", "Struct", "List(Int64)", "List<Int64>",
                "Struct<id:Int64>", "Map<Utf8, Int64>", "Interval(YEAR_MONTH)"):
        _rejects(READ, [{"match": "exact", "native_type": "X", "arrow_type": bad}])
    for ok in ("Timestamp(MICROSECOND)", "Decimal128(38, 9)", "Json"):
        _accepts(READ, [{"match": "exact", "native_type": "X", "arrow_type": ok}])


def test_canonical_rejects_trailing_newline():
    # `$` matches before a final newline; use fullmatch so `"Utf8\n"` is rejected
    # (consistent with the endpoint arrow_type check).
    _rejects(READ, [{"match": "exact", "native_type": "X", "arrow_type": "Utf8\n"}])


def test_templated_canonical_needs_literal_head():
    # A whole-value placeholder is invalid — substitutions are parameter-only.
    _rejects(READ, [{"match": "regex", "native_type": r"(?<type>\w+)", "arrow_type": "${type}"}])
    _accepts(READ, [{"match": "regex", "native_type": rf"DEC\((?<p>{_P128}),(?<s>{_S128})\)",
                     "arrow_type": "Decimal128(${p}, ${s})"}])


def test_templated_canonical_covers_all_temporal_enums():
    # The parameter-aware dummies must accept every temporal enum family, not
    # just microsecond-based ones (Time32 is SECOND/MILLISECOND).
    for base in ("Time32", "Time64", "Timestamp", "Duration"):
        _accepts(READ, [{"match": "regex", "native_type": rf"X\((?<u>{_units(base)})\)",
                         "arrow_type": f"{base}(${{u}})"}])


def test_literal_decimal_scale_must_not_exceed_precision():
    # Cross-parameter bound from the engine grammar (`scale <= precision`):
    # regex cannot express it, so the model enforces it wherever both sides
    # carry a value. Here that is the literal/literal case; the literal/capture
    # case is `test_literal_position_holds_against_the_capture_it_is_bounded_by`.
    _rejects(READ, [{"match": "exact", "native_type": "X", "arrow_type": "Decimal128(5, 6)"}])
    _rejects(WRITE, [{"match": "exact", "arrow_type": "Decimal256(10, 11)", "native_type": "NUMERIC"}])
    _accepts(READ, [{"match": "exact", "native_type": "X", "arrow_type": "Decimal128(5, 5)"}])
    # A literal scale of 9 needs a precision capture that cannot go below 9.
    _accepts(READ, [{"match": "regex", "native_type": r"DEC\((?<p>9|[12]\d|3[0-8])\)",
                     "arrow_type": "Decimal128(${p}, 9)"}])


def test_write_native_may_carry_column_hints():
    # `native_type` is free-form DDL: per-column hint placeholders are allowed on both
    # exact and regex write rules (the contract permits `VARCHAR(${length})`).
    _accepts(WRITE, [{"match": "exact", "arrow_type": "Utf8", "native_type": "VARCHAR(${length})"}])
    _accepts(WRITE, [{"match": "regex", "arrow_type": r"^Decimal128\((?<p>\d+)\)",
                      "native_type": "NUMERIC(${p}, ${length})"}])  # capture + free hint mixed


def test_exact_must_not_template():
    _rejects(READ, [{"match": "exact", "native_type": "X", "arrow_type": "Decimal128(${p})"}])


def test_exact_write_native_render_placeholders_validated():
    # A write exact `native_type` render may carry `${length}` hints, but malformed
    # placeholders (empty / unclosed) must be rejected too: they render into a
    # DDL type the driver cannot parse, and nothing downstream re-checks them.
    _accepts(WRITE, [{"match": "exact", "arrow_type": "Utf8", "native_type": "VARCHAR(${length})"}])
    _rejects(WRITE, [{"match": "exact", "arrow_type": "Utf8", "native_type": "VARCHAR(${})"}])
    _rejects(WRITE, [{"match": "exact", "arrow_type": "Utf8", "native_type": "VARCHAR(${length)"}])
    # The rejection must cite the rule it violates, the same way every other
    # raise in this model does — RULE-TMAP-008's own statement claims this
    # half ("...the native_type DDL it renders MUST carry only well-formed
    # placeholders").
    with pytest.raises(ValidationError) as exc:
        WRITE.validate_python(_wrap(WRITE, [{"match": "exact", "arrow_type": "Utf8", "native_type": "VARCHAR(${})"}]))
    assert "RULE-TMAP-008" in str(exc.value)


def test_regex_write_native_render_placeholders_validated():
    # Same obligation, write regex side: RULE-TMAP-009 covers the identical
    # placeholder well-formedness check on `native_type`, mirroring
    # RULE-TMAP-008 for the exact rule above.
    _accepts(WRITE, [{"match": "regex", "arrow_type": r"^Decimal128\((?<p>\d+)\)",
                      "native_type": "NUMERIC(${p}, ${length})"}])
    _rejects(WRITE, [{"match": "regex", "arrow_type": r"^Decimal128\((?<p>\d+)\)",
                      "native_type": "NUMERIC(${})"}])
    with pytest.raises(ValidationError) as exc:
        WRITE.validate_python(_wrap(WRITE, [{"match": "regex", "arrow_type": r"^Decimal128\((?<p>\d+)\)",
                               "native_type": "NUMERIC(${})"}]))
    assert "RULE-TMAP-009" in str(exc.value)


def _refusal(adapter, rule) -> str:
    """The model's refusal of a one-rule document, as the text an author reads."""
    with pytest.raises(ValidationError) as exc:
        adapter.validate_python(_wrap(adapter, [rule]))
    return str(exc.value)


def _regex_rule(adapter, matcher):
    """A regex rule whose matcher is `matcher`, with a render the model accepts."""
    if adapter is READ:
        return {"match": "regex", "native_type": matcher, "arrow_type": "Utf8"}
    return {"match": "regex", "arrow_type": matcher, "native_type": "TEXT"}


def test_regex_rejects_python_named_group():
    for adapter, rule_id in ((READ, "RULE-TMAP-005"), (WRITE, "RULE-TMAP-009")):
        refusal = _refusal(adapter, _regex_rule(adapter, r"(?P<p>\d+)"))
        assert rule_id in refusal, refusal
        assert "(?<name>…)" in refusal, refusal


@pytest.mark.parametrize("matcher", [
    r"^INT\[\]{0}$",
    r"^INT(?:\[\]){0,0}$",
    r"^A(?:b){0}?$",
    # A flag group or an empty quote takes no place of its own, so the count
    # falls on what precedes it.
    r"^Ab(?i){0}$",
    r"^Ab\Q\E{0}$",
    r"^N\((?<p>[1-9]){0}[1-9]\)$",
])
def test_regex_rejects_a_zero_count_repetition(matcher):
    for adapter, rule_id in ((READ, "RULE-TMAP-005"), (WRITE, "RULE-TMAP-009")):
        refusal = _refusal(adapter, _regex_rule(adapter, matcher))
        assert rule_id in refusal, refusal
        assert "zero times" in refusal, refusal


def test_python_named_group_spelling_inside_a_quote_is_a_literal():
    # `\Q…\E` quotes its contents, so the spelling there opens no group. The
    # quoted `<…>` is literal container syntax, so the read rule renders `Json`.
    _accepts(READ, [{"match": "regex", "native_type": r"^\Q(?P<x>\E$", "arrow_type": "Json"}])
    _accepts(WRITE, [_regex_rule(WRITE, r"^\Q(?P<x>\E$")])


def test_regex_named_backreference_rejected():
    # RE2 has no backreferences, so `\k<name>` is refused with the parse error
    # RE2 gives.
    refusal = _refusal(READ, _regex_rule(READ, r"(?<x>\w+)_\k<x>"))
    assert "RULE-TMAP-005" in refusal, refusal
    assert r"invalid escape sequence: \k" in refusal, refusal


@pytest.mark.parametrize("matcher,re2_error", [
    (r"(?<t>VARCHAR)_\k<t>", r"invalid escape sequence: \k"),
    (r"VARCHAR(?=\()\(\d+\)", "invalid perl operator: (?="),
    (r"(A)\1", r"invalid escape sequence: \1"),
    (r"(?<=X)A", "invalid perl operator: (?<="),
    (r"(?>A+)B", "invalid perl operator: (?>"),
    (r"A{1001}", "invalid repetition size: {1001}"),
])
@pytest.mark.parametrize("adapter,rule_id", [(READ, "RULE-TMAP-005"), (WRITE, "RULE-TMAP-009")],
                         ids=["read", "write"])
def test_regex_outside_re2_is_rejected_with_re2s_own_error(adapter, rule_id, matcher, re2_error):
    refusal = _refusal(adapter, _regex_rule(adapter, matcher))
    assert rule_id in refusal, refusal
    assert re2_error in refusal, refusal


@pytest.mark.parametrize("adapter", [READ, WRITE], ids=["read", "write"])
def test_regex_only_re2_accepts_is_accepted(adapter):
    _accepts(adapter, [_regex_rule(adapter, r"\p{L}+")])


def test_named_group_compiles_as_written():
    # No `(?<name>` → `(?P<name>` rewrite stands between the author and RE2: the
    # compiled matcher carries the capture under the name the rule spells.
    assert compile_matcher(r"(?<p>\d+)X").regex.groupindex == {"p": 1}


def test_regex_must_compile():
    _rejects(READ, [{"match": "regex", "native_type": "([", "arrow_type": "Utf8"}])


def test_placeholder_needs_matching_capture():
    _rejects(READ, [{"match": "regex", "native_type": "NUMERIC", "arrow_type": "Timestamp(${unit})"}])
    _accepts(READ, [{"match": "regex",
                     "native_type": rf"TS\((?<unit>{_units('Timestamp')})\)",
                     "arrow_type": "Timestamp(${unit})"}])


def test_read_captured_native_must_not_discard_params_to_hardcoded_canonical():
    # A native that NAMES captures but maps to a literal
    # parameterized canonical silently coerces every source precision/scale/unit
    # to a by-example constant. Flag it (reverse of the placeholder→capture check).
    _rejects(READ, [{"match": "regex", "native_type": r"NUMERIC\((?<p>\d+),(?<s>\d+)\)",
                     "arrow_type": "Decimal128(38, 9)"}])
    _rejects(READ, [{"match": "regex", "native_type": r"TS\((?<u>\d+)\)",
                     "arrow_type": "Timestamp(MICROSECOND)"}])
    # Referencing the captures (templated canonical) is the correct mapping.
    _accepts(READ, [{"match": "regex", "native_type": rf"DEC\((?<p>{_P128}),(?<s>{_S128})\)",
                     "arrow_type": "Decimal128(${p}, ${s})"}])
    # A non-capturing group is the escape hatch for match-and-discard.
    _accepts(READ, [{"match": "regex", "native_type": r"NUMERIC\((?:\d+),(?:\d+)\)",
                     "arrow_type": "Decimal128(38, 9)"}])
    # A named capture mapping to a NON-parameterized canonical drops nothing.
    _accepts(READ, [{"match": "regex", "native_type": r"VARCHAR\((?<n>\d+)\)", "arrow_type": "Utf8"}])
    # The container canonical `Json` (no `()`) is not param-lossy here.
    _accepts(READ, [{"match": "regex", "native_type": r"ARR<(?<t>\w+)>", "arrow_type": "Json"}])
    # The reverse check is read-only: a write rule renders free-form native DDL,
    # so a canonical-side capture unused in the native is allowed.
    _accepts(WRITE, [{"match": "regex", "arrow_type": r"^Decimal128\((?<p>\d+)\)",
                      "native_type": "NUMERIC(20, 4)"}])


def test_templated_read_canonical_head_validated():
    # The non-placeholder head + shape of a templated read canonical is validated
    # (one placeholder per parameter position).
    _rejects(READ, [{"match": "regex", "native_type": rf"DEC\((?<p>{_P128}),(?<s>{_S128})\)", "arrow_type": "Decmal128(${p}, ${s})"}])
    _accepts(READ, [{"match": "regex", "native_type": rf"DEC\((?<p>{_P128}),(?<s>{_S128})\)", "arrow_type": "Decimal128(${p}, ${s})"}])


def test_schemaless_container_must_not_collapse_to_scalar():
    # Detection is DB-agnostic (by shape): a native written with container syntax
    # (`<...>` or `[]`) must not map to a scalar canonical. Bare vendor names
    # (`JSONB`) are intentionally not special-cased.
    _rejects(READ, [{"match": "exact", "native_type": "array<int>", "arrow_type": "Utf8"}])
    _accepts(READ, [{"match": "exact", "native_type": "array<int>", "arrow_type": "Json"}])
    _rejects(READ, [{"match": "exact", "native_type": "integer[]", "arrow_type": "Utf8"}])
    _accepts(READ, [{"match": "exact", "native_type": "JSONB", "arrow_type": "Utf8"}])  # bare name: not flagged


@pytest.mark.parametrize("native,is_container", [
    # A class's members are a set, not a parameterization, however the class
    # is spelled.
    (r"^A[<>]B$", False),
    (r"^A[^]<>]B$", False),
    (r"^A[\]<>]B$", False),
    (r"^A[[:alpha:]<>]B$", False),
    # Braces RE2 cannot read as a repetition count are literals, so the native
    # does not end in `[]`.
    ("^INT\\[\\]{\u0661}$", False),
    (r"^INT\[\]{01}$", False),
    (r"^INT\[\]{1000000000}$", False),
    # A repetition count is no literal, so the native still ends in `[]`.
    (r"^INT\[\]{2}$", True),
    (r"^INT\[\]{1,3}$", True),
    # A count that admits an occurrence leaves the `[]` matchable.
    (r"^INT\[\]{0,}$", True),
    (r"^INT\[\]{0,1}$", True),
    (r"^INT(\[\])?$", True),
    (r"^ARRAY<(?<t>[A-Z]+)>$", True),
    # However a literal `<`, `>`, `[` or `]` is spelled, it is that character.
    (r"^INT\[\]$", True),
    (r"^ARRAY\<INT\>$", True),
    (r"^ARRAY\Q<INT>\E$", True),
    (r"^ARRAY\Q<INT>", True),
    (r"^ARRAY\x{3C}INT\x3E$", True),
    (r"^ARRAY\74INT\076$", True),
    # A range may end at `[`; what follows the class is literal again.
    (r"^ARRAY<[+-[:]>:]X]$", True),
    (r"^ARRAY<[+-[:]>:]$", True),
])
def test_container_syntax_is_read_from_a_regex_rules_literals_only(native, is_container):
    rule = {"match": "regex", "native_type": native, "arrow_type": "Utf8"}
    if is_container:
        refusal = _refusal(READ, rule)
        assert "RULE-TMAP-002" in refusal, refusal
    else:
        _accepts(READ, [rule])


def test_the_tokenizer_reads_every_escape_re2_compiles():
    # The tokenizer restates RE2's escape grammar, so RE2 says which escapes
    # exist; one the tokenizer does not know raises instead of compiling.
    options = re2.Options()
    options.log_errors = False
    escapes = []
    for char in string.ascii_letters + string.digits:
        try:
            re2.compile(rf"^A\{char}B$", options=options)
        except re2.error:
            continue
        escapes.append(rf"^A\{char}B$")
    assert escapes, "RE2 compiled no escape; the probe no longer measures anything"
    for pattern in escapes:
        compile_matcher(pattern)


def test_an_atom_dead_in_every_case_is_no_case_finding():
    # No normalized native contains a tab or a newline in any case, so the
    # atom is dead for a reason its case does not explain.
    assert case_dead_atoms(r"^A\tB$") == ()
    assert case_dead_atoms(r"^A\nB$") == ()


def test_a_tokenizer_failure_on_a_matcher_re2_accepts_is_raised_not_refused(monkeypatch):
    def unreadable(pattern):
        raise ValueError(f"cannot read {pattern!r}")

    monkeypatch.setattr(type_map, "_tokenize", unreadable)
    with pytest.raises(RuntimeError):
        _accepts(READ, [{"match": "regex", "native_type": "^A$", "arrow_type": "Utf8"}])


def test_a_fragment_re2_refuses_is_raised_not_refused():
    # A fragment is cut out of a matcher RE2 accepted, so RE2 refusing one is
    # the tokenizer's defect, never the author's.
    with pytest.raises(RuntimeError):
        type_map._compile_fragment("(", "^(A)$")


def test_schemaless_native_maps_to_container_canonicals_only():
    # The typed nested families are rejected outright — they are outside the
    # canonical vocabulary. `Object`/`List` still VALIDATE as renders (a string
    # rule can't carry the sibling sub-schemas they need, but the model can't know
    # that) — Json-only for read renders is craft guidance in
    # spec-type-maps.md; the ENFORCED rule is "structured native must map to a
    # container canonical, never a scalar".
    _accepts(READ, [{"match": "exact", "native_type": "union<int, str>", "arrow_type": "Json"}])
    for canonical in ("DenseUnion<a:Int64>", "SparseUnion<a:Int64>",
                      "Dictionary<Int32, Utf8>", "RunEndEncoded<Int32, Int64>"):
        _rejects(READ, [{"match": "exact", "native_type": "union<int, str>", "arrow_type": canonical}])
    # ...and a structured native → scalar canonical is still flagged.
    _rejects(READ, [{"match": "exact", "native_type": "union<int, str>", "arrow_type": "Utf8"}])


def test_write_direction_matches_canonical_renders_native():
    # write: canonical is the matcher (vocabulary-checked on exact), native is free-form DDL.
    _accepts(WRITE, [{"match": "exact", "arrow_type": "Int64", "native_type": "BIGINT"}])
    _rejects(WRITE, [{"match": "exact", "arrow_type": "NotArrow", "native_type": "BIGINT"}])
    _accepts(WRITE, [{"match": "regex", "arrow_type": r"^Decimal128\((?<p>\d+)\)",
                      "native_type": "NUMERIC(${p})"}])


def test_templated_capture_must_stay_inside_its_position():
    """A `${name}` render is only as safe as the capture feeding it.

    An unbounded `\\d+` in a decimal position renders canonicals the contract
    itself rejects, and nothing downstream re-checks a substituted type on the
    destination path — so the rule is refused at authoring, not at write time.
    """
    _rejects(READ, [{"match": "regex",
                     "native_type": rf"N\((?<p>{_P128}),(?<s>\d+)\)",
                     "arrow_type": "Decimal128(${p}, ${s})"}])
    _rejects(READ, [{"match": "regex",
                     "native_type": rf"N\((?<p>\d+),(?<s>{_S128})\)",
                     "arrow_type": "Decimal128(${p}, ${s})"}])
    # Leading zeros are not canonical spellings either.
    _rejects(READ, [{"match": "regex", "native_type": r"N\((?<p>\d{2})\)",
                     "arrow_type": "Decimal128(${p}, 0)"}])
    _accepts(READ, [{"match": "regex",
                     "native_type": rf"N\((?<p>{_P128}),(?<s>{_S128})\)",
                     "arrow_type": "Decimal128(${p}, ${s})"}])
    # Decimal256 carries the same bound at its own ceiling.
    _rejects(READ, [{"match": "regex", "native_type": r"N\((?<p>\d+),(?<s>\d+)\)",
                     "arrow_type": "Decimal256(${p}, ${s})"}])
    _accepts(READ, [{"match": "regex",
                     "native_type": r"N\((?<p>39|[4-6]\d|7[0-6]),(?<s>\d|[1-6]\d|7[0-6])\)",
                     "arrow_type": "Decimal256(${p}, ${s})"}])


def test_partially_templated_bound_resolves_to_the_literal_sibling():
    """The ceiling for `${s}` in `Decimal128(5, ${s})` is 5, not the family's 38.

    Resolving it to the static ceiling is what let a hardcoded precision of 5
    admit a scale of 38.
    """
    _rejects(READ, [{"match": "regex", "native_type": r"N\((?<s>\d|[12]\d|3[0-8])\)",
                     "arrow_type": "Decimal128(5, ${s})"}])
    _accepts(READ, [{"match": "regex", "native_type": r"N\((?<s>[0-5])\)",
                     "arrow_type": "Decimal128(5, ${s})"}])


def test_literal_position_holds_against_the_capture_it_is_bounded_by():
    """`Decimal128(${p}, 38)` is satisfiable only at `p == 38`.

    No literal sibling exists to resolve the bound against, so the decision
    comes from the capture: any precision it can match below the literal scale
    renders a canonical the contract rejects.
    """
    _rejects(READ, [{"match": "regex", "native_type": rf"N\((?<p>{_P128})\)",
                     "arrow_type": "Decimal128(${p}, 38)"}])
    _accepts(READ, [{"match": "regex", "native_type": r"N\((?<p>38)\)",
                     "arrow_type": "Decimal128(${p}, 38)"}])
    # A scale of 0 is below every precision the family admits, so it holds.
    _accepts(READ, [{"match": "regex", "native_type": rf"N\((?<p>{_P128})\)",
                     "arrow_type": "Decimal128(${p}, 0)"}])


def test_both_positions_templated_is_left_wide_on_purpose():
    """What the narrowing deliberately does not reach.

    With both sides of the bound templated, each capture is judged against its
    own position only — so a pair like `(1, 38)`, reachable from these two
    captures, is not refused. Deciding it needs the joint language of two
    captures over one native string, which the check does not compute.
    """
    _accepts(READ, [{"match": "regex",
                     "native_type": rf"N\((?<p>{_P128}),(?<s>{_S128})\)",
                     "arrow_type": "Decimal128(${p}, ${s})"}])


def test_a_capture_wider_than_the_alphabet_is_still_refused():
    """"Matches no probe" is a proof, not a gap.

    The probe alphabet holds every value an in-scope position admits, so a
    capture matching none of it renders nothing admissible however wide it is —
    a `\\d{7,}` precision can only ever produce `Decimal128(1234567, 0)`.
    """
    _rejects(READ, [{"match": "regex", "native_type": r"N\((?<p>\d{7,})\)",
                     "arrow_type": "Decimal128(${p}, 0)"}])
    # Non-digit spellings land in the same place, for the same reason.
    _rejects(READ, [{"match": "regex", "native_type": r"N\((?<p>0x[0-9A-F]+)\)",
                     "arrow_type": "Decimal128(${p}, 0)"}])


def test_a_placeholder_parameter_position_must_carry_nothing_else():
    """A concatenated or literal-prefixed position renders the product of its
    parts, which no single capture's language answers — so it is refused rather
    than filed under literals, where the digit guard would wave it through."""
    _rejects(READ, [{"match": "regex", "native_type": r"N\((?<a>\d)(?<b>\d)\)",
                     "arrow_type": "Decimal128(${a}${b}, 0)"}])
    _rejects(READ, [{"match": "regex", "native_type": r"N\((?<p>\d)\)",
                     "arrow_type": "Decimal128(1${p}, 0)"}])


def test_backreference_into_a_capture_is_refused_before_the_bound_check():
    """RE2 has no backreferences, so the rule fails to compile before any
    capture of it is interrogated."""
    refusal = _refusal(READ, {"match": "regex",
                              "native_type": r"(?<a>[0-3])N\((?<p>[1-9]\k<a>)\)",
                              "arrow_type": "Decimal128(${p}, 0)"})
    assert "RULE-TMAP-005" in refusal, refusal
    assert "RULE-TMAP-010" not in refusal, refusal


def test_capture_whose_class_opens_with_a_bracket_is_read_whole():
    # A `]` first in a class is a member, not the class's end, so the `)`
    # after it is a member too and the capture runs on to its real closer.
    refusal = _refusal(READ, {"match": "regex", "native_type": r"^N\((?<p>[])0-9]+)\)$",
                              "arrow_type": "Decimal128(${p}, 0)"})
    assert "RULE-TMAP-010" in refusal, refusal
    _accepts(READ, [{"match": "regex", "native_type": r"^N\((?<p>[])1-9])\)$",
                     "arrow_type": "Decimal128(${p}, 0)"}])


def test_a_repeated_group_name_is_read_as_the_group_re2_binds():
    # RE2 binds a repeated name to the group opened first, which here is the
    # outer, unbounded one, so that is the capture the bound is checked on.
    refusal = _refusal(READ, {"match": "regex",
                              "native_type": r"^N\((?<p>(?<p>[1-9])\d*)\)$",
                              "arrow_type": "Decimal128(${p}, 0)"})
    assert "RULE-TMAP-010" in refusal, refusal


def test_capture_is_read_under_the_inline_flags_in_force():
    # The unit probes are spelt in uppercase; a lowercase capture under `(?i)`
    # matches them, so the capture's language is taken case-insensitively too.
    _accepts(READ, [{"match": "regex",
                     "native_type": rf"(?i)^ts\((?<u>{_units('Timestamp').lower()})\)$",
                     "arrow_type": "Timestamp(${u})"}])


def test_families_without_a_cross_bound_are_capture_checked_too():
    """A cross-parameter bound is not what makes a position worth reading.

    Every position states what it admits; a capture that can reach past it
    renders a non-canonical whether or not a sibling is involved. `Time64`
    admits neither of `Time32`'s units, `FixedSizeBinary` admits no width of 0,
    and a word-class capture reaches values no unit position admits at all.
    """
    _rejects(READ, [{"match": "regex", "native_type": r"TS\((?<u>[A-Z]+)\)",
                     "arrow_type": "Timestamp(${u})"}])
    _rejects(READ, [{"match": "regex",
                     "native_type": rf"T\((?<u>{_units('Timestamp')})\)",
                     "arrow_type": "Time64(${u})"}])
    _rejects(READ, [{"match": "regex", "native_type": r"BINARY\((?<n>\d+)\)",
                     "arrow_type": "FixedSizeBinary(${n})"}])
    _accepts(READ, [{"match": "regex", "native_type": r"BINARY\((?<n>[1-9]\d*)\)",
                     "arrow_type": "FixedSizeBinary(${n})"}])


def test_a_position_with_no_probe_alphabet_stands():
    """A timezone's admissible set is an open pattern the check cannot
    enumerate, so a capture feeding one is not interrogated."""
    _accepts(READ, [{"match": "regex", "native_type": r"TS\((?<z>[A-Z/a-z_]+)\)",
                     "arrow_type": "Timestamp(SECOND, ${z})"}])


@pytest.mark.parametrize("native,name,expected", [
    (r"^N\((?<p>\d+)\)$", "p", r"\d+"),
    # A `)` inside a character class does not close the group.
    (r"^N(?<p>[0-9)]+)$", "p", "[0-9)]+"),
    # Neither does an escaped one.
    (r"^N(?<p>\d+\))$", "p", r"\d+\)"),
    # Nested groups are consumed whole.
    (r"^N(?<p>(?:1|2)(?<q>\d))$", "p", r"(?:1|2)(?<q>\d)"),
    # A quoted `)` is a literal, and so is a `]` opening a class.
    (r"^X(?<t>\Q)\E|A)$", "t", r"\Q)\E|A"),
    (r"^X(?<t>[]A)]+)$", "t", "[]A)]+"),
    # The inline flags in force at the opener travel with the source.
    (r"(?i)X(?<t>ab)", "t", "(?i:ab)"),
])
def test_named_group_source_extraction(native, name, expected):
    from analitiq.contracts.type_map import _named_group_source

    assert _named_group_source(native, name) == expected


def test_named_group_source_of_an_absent_group_raises():
    from analitiq.contracts.type_map import _named_group_source

    with pytest.raises(KeyError):
        _named_group_source(r"^N\((?<p>\d+)\)$", "absent")
