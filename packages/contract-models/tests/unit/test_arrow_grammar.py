"""Guards for `analitiq.contracts.arrow_grammar` — the vendored engine manifest.

Four concerns, all offline:

1. **The pin**: the vendored `arrow_type_grammar.json` must hash to the sha256
   stated next to it, AND self-declare the pinned version. An edited/swapped
   vendored file fails here in any plain pytest run; the network half
   (published object byte-compare, conversion-matrix parity) lives in
   `scripts/check_engine_grammar_pin.py` (CI job `engine-grammar-pin-guard`).
2. **The envelope**: the vocabulary is read from the manifest's `families`
   key, never from the document, and a manifest that cannot yield a usable
   vocabulary must refuse to import rather than derive an empty one.
3. **Derivation**: everything the contract derives from the manifest —
   `ARROW_TYPE_PATTERN`, container heads, template dummies — must be a pure
   function of the manifest's families, so a pin bump re-derives all of it.
4. **The generators' failure modes**: unsupported manifest shapes (int ranges
   the builder can't render, unknown param kinds) must fail loudly, never
   silently misparse.

Acceptance/rejection of concrete type strings is exercised where the pattern
is consumed (test_endpoint_model.py, test_canonical_types_schema.py,
validator's test_type_map_model.py) — not restated here.
"""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from analitiq.contracts import arrow_grammar


def test_vendored_grammar_hashes_to_the_pin():
    digest = hashlib.sha256(arrow_grammar._GRAMMAR_PATH.read_bytes()).hexdigest()
    assert digest == arrow_grammar.ENGINE_GRAMMAR_SHA256, (
        f"vendored {arrow_grammar._GRAMMAR_PATH.name} hashes to {digest}, but "
        f"the pin says {arrow_grammar.ENGINE_GRAMMAR_SHA256}. The vendored "
        "file and the pin constants must move together (re-vendor the "
        "published object, then re-render schemas + regenerate docs)."
    )


def test_vendored_grammar_self_declares_the_pinned_version():
    """From v1.1.0 the manifest carries its own `version`. The pin must agree
    with it: `scripts/render_schemas.py` stamps `pinned at v{...}`
    into published prose from the PIN, so a pin that disagrees with the file it
    describes publishes a false provenance claim."""
    declared = arrow_grammar.GRAMMAR.get(arrow_grammar.ARTIFACT_VERSION_KEY)
    assert declared == arrow_grammar.ENGINE_GRAMMAR_VERSION, (
        f"vendored {arrow_grammar._GRAMMAR_PATH.name} declares version "
        f"{declared!r} but the pin says "
        f"{arrow_grammar.ENGINE_GRAMMAR_VERSION!r} — re-vendor the published "
        "object and move both pin constants together"
    )


def test_families_come_from_the_keyed_envelope_not_the_whole_document():
    """The manifest is an envelope — `families` beside `version`. Deriving the
    vocabulary from the document itself would admit envelope keys as families.

    Asserted behaviourally (equality, and no envelope sibling reaching the
    vocabulary) rather than by identity, so a defensive copy of the families
    map stays a free refactor.
    """
    assert (
        arrow_grammar.FAMILIES
        == arrow_grammar.GRAMMAR[arrow_grammar.GRAMMAR_FAMILIES_KEY]
    )
    siblings = set(arrow_grammar.GRAMMAR) - {arrow_grammar.GRAMMAR_FAMILIES_KEY}
    assert siblings, "manifest has no envelope siblings — this test is vacuous"
    for key in siblings:
        assert key not in arrow_grammar.FAMILY_NAMES
        assert key not in arrow_grammar.ARROW_TYPE_PATTERN


@pytest.mark.parametrize("payload,reason", [
    ('{"families": {}}', "`families` is empty"),
    ('{"version": "1.1.0"}', "no `families` key"),
    ('{"families": []}', "`families` is list, expected object"),
    ('[]', "top level is list, expected object"),
    ('{"families": {"Utf8": "not a spec"}}', "family specs are not objects"),
])
def test_an_unusable_manifest_refuses_to_import(monkeypatch, payload, reason):
    """Each shape parses as JSON, so nothing upstream rejects it, and each
    would either crash three imports deep (`KeyError: 'families'`,
    `AttributeError: 'str' object has no attribute 'get'`) or derive a
    vocabulary accepting NO canonical type — while importing cleanly.

    The published wheel reaches users who never run the CI guard or this suite,
    so the floor belongs at import and must name its remediation. The
    renamed-key case is not hypothetical: the sibling conversion-matrix
    artifact did exactly this in its v2.0.0.
    """
    monkeypatch.setattr(arrow_grammar, "load_grammar", lambda: json.loads(payload))
    with pytest.raises(RuntimeError) as exc:
        arrow_grammar._load_families()
    message = str(exc.value)
    assert reason in message
    # Remediation, not just diagnosis — this is what the reader acts on.
    assert "re-vendor" in message
    assert arrow_grammar.ENGINE_GRAMMAR_RESOURCE in message


def test_unparseable_manifest_names_its_remediation(monkeypatch):
    """The non-JSON path, chained so the underlying cause survives."""
    def _bad():
        raise json.JSONDecodeError("boom", "{", 0)

    monkeypatch.setattr(arrow_grammar, "load_grammar", _bad)
    with pytest.raises(RuntimeError, match="not valid JSON") as exc:
        arrow_grammar._load_families()
    assert isinstance(exc.value.__cause__, json.JSONDecodeError)


def test_pattern_is_a_pure_derivation_of_the_manifest():
    """The published pattern is EXACTLY the composition of the per-family
    fragments in sorted family order — nothing hand-appended can hide in it,
    and each family behaves as its spec says (bare accepted iff no params)."""
    assert arrow_grammar.ARROW_TYPE_PATTERN == (
        "^(?:"
        + "|".join(
            arrow_grammar.family_pattern(name)
            for name in sorted(arrow_grammar.FAMILIES)
        )
        + ")$"
    )
    compiled = re.compile(arrow_grammar.ARROW_TYPE_PATTERN)
    for name, spec in arrow_grammar.FAMILIES.items():
        if spec.get("params"):
            assert not compiled.fullmatch(name), f"bare {name!r} must be rejected"
        else:
            assert compiled.fullmatch(name), f"bare {name!r} must be accepted"


def test_no_dead_families_in_the_manifest():
    """The executable-vocabulary trim: none of the families the engine cannot
    execute may reappear in the vendored manifest without the engine shipping
    them first — at which point this list is consciously edited, which is the
    point."""
    # Bare `List`/`Object` are the authored-shape structural markers and are
    # NOT in this set — the trimmed families are the typed/encoded ones.
    dead = {
        "Interval", "LargeList", "FixedSizeList", "Struct", "Map",
        "SparseUnion", "DenseUnion", "Dictionary", "RunEndEncoded",
    }
    present = dead & set(arrow_grammar.FAMILY_NAMES)
    assert not present, (
        f"families {sorted(present)} are back in the vendored manifest — if the "
        "engine now executes them, update this list together with the re-add "
        "(prose, examples, and canonical-types groups all need the same pass)"
    )


def test_container_heads_derive_from_structural_families():
    structural = {
        name for name, spec in arrow_grammar.FAMILIES.items() if spec.get("structural")
    }
    assert arrow_grammar.CONTAINER_CANONICAL_HEADS == structural | {"Json"}
    assert structural == {"Object", "List"}


def test_template_dummies_cover_every_param_kind():
    """Substituting each dummy for every placeholder must resolve at least one
    dummy per parameterized family — the property `_validate_type_map_canonical`
    relies on."""
    compiled = re.compile(arrow_grammar.ARROW_TYPE_PATTERN)
    for name in arrow_grammar.PARAMETERIZED_FAMILY_NAMES:
        params = arrow_grammar.FAMILIES[name]["params"]
        required = [p for p in params if not p.get("optional")]
        template = name + "(" + ", ".join("${x}" for _ in required) + ")"
        assert any(
            compiled.fullmatch(template.replace("${x}", dummy))
            for dummy in arrow_grammar.TEMPLATE_DUMMY_SUBSTITUTIONS
        ), f"no dummy resolves {template!r}"


@pytest.mark.parametrize("lo,hi", [(1, 38), (1, 76), (0, 38), (12, 45), (10, 10)])
def test_int_range_pattern_is_exhaustively_correct(lo, hi):
    """Property check over every value near the range (covers the decade
    decomposition for lo >= 10, which the manifest doesn't exercise yet)."""
    pattern = re.compile(arrow_grammar._int_range_pattern(lo, hi))
    for v in range(0, 130):
        assert (pattern.fullmatch(str(v)) is not None) == (lo <= v <= hi), (
            f"[{lo},{hi}] wrong at {v}"
        )


def test_int_range_pattern_unbounded_and_leading_zeros():
    unbounded = arrow_grammar._int_range_pattern(1, None)
    assert re.fullmatch(unbounded, "1") and re.fullmatch(unbounded, "12345")
    assert not re.fullmatch(unbounded, "0") and not re.fullmatch(unbounded, "01")
    zero = arrow_grammar._int_range_pattern(0, None)
    assert re.fullmatch(zero, "0") and re.fullmatch(zero, "10")
    assert not re.fullmatch(zero, "-1") and not re.fullmatch(zero, "007")
    bounded_zero = arrow_grammar._int_range_pattern(0, 38)
    assert not re.fullmatch(bounded_zero, "05")


def test_cross_ref_int_bound_resolves_to_referenced_ceiling():
    """`"max": "precision"` caps a literal scale at precision's own max — the
    satisfiable envelope — so `Decimal128(38, 99)` fails the pattern outright
    (and with it the unsatisfiable template `Decimal128(${p}, 99)`)."""
    compiled = re.compile(arrow_grammar.ARROW_TYPE_PATTERN)
    assert compiled.fullmatch("Decimal128(38, 38)")
    assert not compiled.fullmatch("Decimal128(38, 39)")
    assert compiled.fullmatch("Decimal256(76, 76)")
    assert not compiled.fullmatch("Decimal256(76, 77)")
    templated = re.compile(
        "^" + arrow_grammar.family_pattern("Decimal128", templated=True) + "$"
    )
    assert templated.fullmatch("Decimal128(${p}, 20)")
    assert not templated.fullmatch("Decimal128(${p}, 99)")


def test_cross_ref_bound_resolves_to_the_literal_sibling_present():
    """The family-level pattern can only know the referenced param's own
    ceiling; a caller holding one concrete canonical knows the literal sibling,
    and that is the bound the position actually admits."""
    params = arrow_grammar.FAMILIES["Decimal128"]["params"]
    # StopIteration here is the failure signal working, not a case to guard.
    scale = next(p for p in params if p["name"] == "scale")  # skipcq: PTC-W0063

    assert arrow_grammar.resolved_int_bounds(scale, params) == (0, 38)
    assert arrow_grammar.resolved_int_bounds(scale, params, {"precision": "5"}) == (0, 5)
    # A templated sibling carries no literal, so the envelope stands.
    assert arrow_grammar.resolved_int_bounds(
        scale, params, {"precision": "${p}"}) == (0, 38)

    admits = re.compile(arrow_grammar._param_literal_pattern(scale, params, {"precision": "5"}))
    assert admits.fullmatch("5") and not admits.fullmatch("6")


def test_unsatisfiable_resolved_bound_admits_nothing():
    """A resolved ceiling below the floor is an empty position, not a crash."""
    params = [
        {"kind": "int", "min": 1, "max": 38, "name": "precision"},
        {"kind": "int", "min": 2, "max": "precision", "name": "scale"},
    ]
    pattern = re.compile(
        arrow_grammar._param_literal_pattern(params[1], params, {"precision": "1"}))
    assert not any(pattern.fullmatch(str(v)) for v in range(0, 40))


def test_every_parameterized_family_is_reached_by_the_bound_check():
    """Scope comes from the manifest, not from a listed set of families.

    Anything the grammar parameterizes is interrogated where its position has a
    probe alphabet; nothing selects a subset of families by name. A family
    arriving with no interrogable position at all fails here on purpose — that
    is an alphabet to design, not a default to inherit.
    """
    reached = {
        name
        for name, spec in arrow_grammar.FAMILIES.items()
        if any(
            arrow_grammar.param_probe_values(param)
            for param in spec.get("params") or ()
        )
    }
    parameterized = {
        name for name, spec in arrow_grammar.FAMILIES.items() if spec.get("params")
    }
    assert parameterized  # non-vacuous
    assert reached == parameterized


def test_template_bounds_reach_a_family_without_a_cross_bound():
    """Scope is every position with a probe alphabet, not only the bounded ones.

    A cross-parameter bound decides what a position admits; it is not what makes
    the position worth interrogating. `Time64` admits neither `SECOND` nor
    `MILLISECOND`, and a capture reaching them renders a non-canonical exactly
    the way an over-wide decimal capture does.
    """
    with pytest.raises(ValueError, match="does not admit"):
        arrow_grammar.validate_template_bounds(
            "Time64(${u})", lambda name, probes: frozenset(probes))
    with pytest.raises(ValueError, match="does not admit"):
        arrow_grammar.validate_template_bounds(
            "FixedSizeBinary(${n})", lambda name, probes: frozenset(probes))

    # StopIteration here is the failure signal working, not a case to guard.
    fine = frozenset(
        next(p for p in arrow_grammar.FAMILIES["Time64"]["params"]  # skipcq: PTC-W0063
             if p["kind"] == "unit")["allowed"]
    )
    arrow_grammar.validate_template_bounds(
        "Time64(${u})", lambda name, probes: fine)


def test_a_position_with_no_probe_alphabet_is_not_interrogated():
    """A timezone's admissible set is an open pattern, so the capture stands."""
    arrow_grammar.validate_template_bounds(
        "Timestamp(SECOND, ${tz})", lambda name, probes: frozenset(probes))


def test_an_unbounded_position_never_refuses_on_an_empty_language():
    """The empty-language conclusion needs the whole admissible set in hand.

    `FixedSizeBinary` byte_width has no ceiling, so a capture matching no probe
    may still render something admissible — a width past the last witness. Only
    the finite positions get that verdict.
    """
    arrow_grammar.validate_template_bounds(
        "FixedSizeBinary(${n})", lambda name, probes: frozenset())
    with pytest.raises(ValueError, match="matches no value"):
        arrow_grammar.validate_template_bounds(
            "Time64(${u})", lambda name, probes: frozenset())


def test_template_bounds_skip_an_unreadable_capture():
    """None means "cannot be read" and must not be read as "matches nothing"."""
    arrow_grammar.validate_template_bounds(
        "Decimal128(5, ${s})", lambda name, probes: None)
    with pytest.raises(ValueError, match="does not admit"):
        arrow_grammar.validate_template_bounds(
            "Decimal128(5, ${s})", lambda name, probes: frozenset(probes))


def test_a_capture_matching_no_probe_renders_nothing_admissible():
    """An empty language is a decision, not an absence of one.

    The alphabet holds every value an in-scope position admits, so a capture
    matching none of it can only ever render a value the position refuses —
    whatever width that capture is. Reporting it safe is how a `\\d{7,}`
    capture used to pass.
    """
    with pytest.raises(ValueError, match="matches no value"):
        arrow_grammar.validate_template_bounds(
            "Decimal128(${p}, 0)", lambda name, probes: frozenset())


def test_a_placeholder_position_must_carry_nothing_else():
    """A concatenation has no single capture whose language answers for it."""
    for canonical in ("Decimal128(${a}${b}, 0)", "Decimal128(1${p}, 0)"):
        with pytest.raises(ValueError, match="must be that placeholder alone"):
            arrow_grammar.validate_template_bounds(
                canonical, lambda name, probes: frozenset(probes))


def test_int_probe_alphabet_covers_every_admissible_value():
    """The load-bearing property: completeness over the ADMISSIBLE side.

    Reach past the ceiling is a convenience (it makes a diagnostic name a
    plausible witness); what the check stands on is that no value an in-scope
    position admits is missing from the alphabet. That is what makes "this
    capture matches no probe" mean "it renders nothing admissible" rather than
    "the alphabet was too narrow to see it".
    """
    probes = arrow_grammar.param_probe_values(
        {"kind": "int", "min": 0, "max": 38, "name": "scale"})
    ceilings = [
        param["max"]
        for spec in arrow_grammar.FAMILIES.values()
        for param in spec.get("params") or ()
        if param["kind"] == "int" and isinstance(param.get("max"), int)
    ]
    assert ceilings  # non-vacuous: read from the manifest, not typed out here
    assert set(probes) >= {str(v) for v in range(0, max(ceilings) + 1)}
    assert "00" in probes  # leading-zero spellings the grammar forbids

    units = arrow_grammar.param_probe_values({"kind": "unit", "name": "u"})
    assert set(units) >= {
        unit
        for spec in arrow_grammar.FAMILIES.values()
        for param in spec.get("params") or ()
        if param["kind"] == "unit"
        for unit in param["allowed"]
    }
    # …and a witness no unit position admits, or an over-wide capture (`[A-Z]+`)
    # would look identical to one bounded to the family's own units.
    assert set(units) > set(arrow_grammar._NON_UNIT_PROBE_VALUES)

    # A timezone states an open pattern, not a member list; no finite alphabet
    # interrogates a capture over it.
    assert arrow_grammar.param_probe_values({"kind": "timezone", "name": "tz"}) == ()


@pytest.mark.parametrize("widen", [
    # A bounded int position pushed past the alphabet…
    lambda param: {**param, "max": 500}
    if param["kind"] == "int" and isinstance(param.get("max"), int) else param,
    # …and a unit vocabulary the alphabet was built before.
    lambda param: {**param, "allowed": [*param["allowed"], "FORTNIGHT"]}
    if param["kind"] == "unit" else param,
])
def test_probe_alphabet_completeness_is_checked_against_the_manifest(monkeypatch, widen):
    """A manifest widening a position past the alphabet must be caught at
    import, not silently turn the bound check into a rubber stamp."""
    assert arrow_grammar._uncovered_admissible_values() == []

    widened = {
        name: {**spec, "params": [widen(p) for p in spec["params"]]}
        if spec.get("params") else spec
        for name, spec in arrow_grammar.FAMILIES.items()
    }
    monkeypatch.setattr(arrow_grammar, "FAMILIES", widened)
    assert arrow_grammar._uncovered_admissible_values()


def test_unsupported_manifest_shapes_fail_loudly():
    with pytest.raises(ValueError):
        arrow_grammar._int_range_pattern(1, 100)  # beyond the two-digit builder
    with pytest.raises(ValueError):
        arrow_grammar._int_range_pattern(2, None)  # unsupported unbounded min
    with pytest.raises(ValueError):
        arrow_grammar._param_literal_pattern({"kind": "mystery", "name": "x"}, [])


@pytest.mark.parametrize("params", [
    # leading optional before a required param
    [
        {"kind": "int", "min": 1, "max": None, "name": "a", "optional": True},
        {"kind": "int", "min": 1, "max": None, "name": "b"},
    ],
    # ALL-optional multi-param list: `\((?:A)?(?:\s*,\s*B)?\)` would accept
    # the malformed `(,B)`, since both groups may be skipped — must refuse too
    [
        {"kind": "int", "min": 1, "max": None, "name": "a", "optional": True},
        {"kind": "int", "min": 1, "max": None, "name": "b", "optional": True},
    ],
])
def test_non_trailing_optional_param_fails_loudly(monkeypatch, params):
    """A leading/middle optional param — or an all-optional multi-param list —
    would silently generate a wrong grammar (the comma rides inside each
    non-first piece); the generator must refuse instead."""
    monkeypatch.setattr(
        arrow_grammar, "FAMILIES", {**arrow_grammar.FAMILIES, "Bad": {"params": params}}
    )
    with pytest.raises(ValueError, match="non-trailing optional"):
        arrow_grammar.family_pattern("Bad")


def test_sole_optional_param_is_supported(monkeypatch):
    monkeypatch.setattr(
        arrow_grammar,
        "FAMILIES",
        {
            **arrow_grammar.FAMILIES,
            "Solo": {"params": [
                {"kind": "int", "min": 1, "max": None, "name": "a", "optional": True},
            ]},
        },
    )
    pattern = re.compile("^" + arrow_grammar.family_pattern("Solo") + "$")
    assert pattern.fullmatch("Solo()") and pattern.fullmatch("Solo(3)")
    assert not pattern.fullmatch("Solo(,3)")


def test_multiple_trailing_optionals_nest(monkeypatch):
    """Two trailing optionals must derive the NESTED form — supplying a later
    optional while skipping an earlier one is malformed and must not match."""
    monkeypatch.setattr(
        arrow_grammar,
        "FAMILIES",
        {
            **arrow_grammar.FAMILIES,
            "TwoOpt": {"params": [
                {"kind": "int", "min": 1, "max": None, "name": "a"},
                {"kind": "unit", "allowed": ["SECOND"], "name": "b", "optional": True},
                {"kind": "int", "min": 1, "max": None, "name": "c", "optional": True},
            ]},
        },
    )
    pattern = re.compile("^" + arrow_grammar.family_pattern("TwoOpt") + "$")
    assert pattern.fullmatch("TwoOpt(1)")
    assert pattern.fullmatch("TwoOpt(1, SECOND)")
    assert pattern.fullmatch("TwoOpt(1, SECOND, 5)")
    assert not pattern.fullmatch("TwoOpt(1, 5)")  # middle optional skipped
    assert not pattern.fullmatch("TwoOpt(1, , 5)")


def test_dangling_cross_ref_bound_fails_loudly():
    """A typo'd `"max": "presicion"` would otherwise disable the bound at BOTH
    layers — unbounded pattern here, silent skip in validate_cross_params."""
    with pytest.raises(ValueError, match="unknown sibling param"):
        arrow_grammar._param_literal_pattern(
            {"kind": "int", "min": 0, "max": "presicion", "name": "scale"},
            [{"kind": "int", "min": 1, "max": 38, "name": "precision"}],
        )


def test_the_vendored_manifest_is_where_the_loader_expects_it():
    """`arrow_grammar.py` loads the JSON at import time, so a wheel without it
    cannot import at all. That it SHIPS is settled by tracking — see
    `test_wheel_packages.py` — and this pins the other half: the constant the
    loader reads names the file the loader opens."""
    assert arrow_grammar._GRAMMAR_PATH.name == arrow_grammar.ENGINE_GRAMMAR_FILENAME
    assert arrow_grammar._GRAMMAR_PATH.is_file()


def test_cross_params_checks_literals_only():
    with pytest.raises(ValueError):
        arrow_grammar.validate_cross_params("Decimal128(5, 6)")
    with pytest.raises(ValueError):
        arrow_grammar.validate_cross_params("Decimal256(10, 11)")
    # Equal is allowed; templated / non-cross-ref / non-parameterized are ignored.
    arrow_grammar.validate_cross_params("Decimal128(5, 5)")
    arrow_grammar.validate_cross_params("Decimal128(${p}, 9)")
    arrow_grammar.validate_cross_params("Timestamp(SECOND, UTC)")
    arrow_grammar.validate_cross_params("Utf8")
    arrow_grammar.validate_cross_params("not a type at all")


def test_timestamp_offset_uses_the_manifest_pattern_verbatim():
    """The fixed-offset grammar is manifest-owned; the derived pattern must
    embed it unchanged — no hand-edited hour range, which would accept or reject
    offsets the engine does not."""
    # StopIteration here is the failure signal working, not a case to guard.
    tz_param = next(  # skipcq: PTC-W0063
        p
        for p in arrow_grammar.FAMILIES["Timestamp"]["params"]
        if p["kind"] == "timezone"
    )
    assert tz_param["fixed_offset_pattern"] in arrow_grammar.ARROW_TYPE_PATTERN


class TestRecordedWireSamples:
    """`declares_zone` / `sample_carries_zone` — the readings RULE-ENDP-063
    compares. Each answers `None` for "cannot say", and the rule turns on that
    third value: absence of evidence must never be read as evidence a
    declaration is right."""

    def test_which_families_carry_a_zone_comes_from_the_manifest(self):
        """Not from a family name written down here: a family the engine later
        gives a timezone position is read correctly without an edit."""
        zoned = {
            name for name, spec in arrow_grammar.FAMILIES.items()
            if any(p["kind"] == arrow_grammar.TIMEZONE_PARAM_KIND
                   for p in spec.get("params") or ())
        }
        assert zoned, (
            "no family in the vendored grammar carries a timezone position. "
            "The manifest may have renamed the param kind, or dropped the "
            "position; whatever the cause, every assertion below now matches "
            "nothing and passes, so the extraction is failed here rather "
            "than reported as agreement"
        )
        for name in zoned:
            assert arrow_grammar.declares_zone(f"{name}(SECOND)") is False
        for name in set(arrow_grammar.FAMILIES) - zoned:
            # Asked through a parameterized application, not a bare name: a
            # bare one is refused at the head, so it would answer `None` for
            # every family whether or not it carries the position.
            assert arrow_grammar.declares_zone(f"{name}(SECOND)") is None, name

    @pytest.mark.parametrize("value, expected", [
        ("Timestamp(MICROSECOND, UTC)", True),
        ("Timestamp(MICROSECOND, Europe/Berlin)", True),
        ("Timestamp(MICROSECOND, +02:00)", True),
        # The position is optional; omitting it declares a zone-naive instant.
        ("Timestamp(MICROSECOND)", False),
        # The manifest's own sentinel for "explicitly no zone".
        ("Timestamp(MICROSECOND, null)", False),
        # A templated position carries no value yet, so it contradicts nothing.
        ("Timestamp(MICROSECOND, ${zone})", None),
        # Families with no timezone position at all.
        ("Date32", None),
        ("Time64(NANOSECOND)", None),
        ("Decimal128(38, 9)", None),
    ])
    def test_declares_zone(self, value, expected):
        assert arrow_grammar.declares_zone(value) is expected

    @pytest.mark.parametrize("sample, expected", [
        ("2024-01-02T03:04:05Z", True),
        ("2024-01-02t03:04:05z", True),
        ("2024-01-02 03:04:05-05:00", True),
        ("2024-01-02T03:04:05.123456+0200", True),
        # Spellings ISO-8601 allows and providers emit: an hour-only offset,
        # and the comma fraction separator.
        ("2024-01-02T03:04:05+02", True),
        ("2024-01-02T03:04:05,123Z", True),
        # Anchored and read with `fullmatch`, so a trailing newline is not a
        # date-time literal rather than a clean one.
        ("2024-01-02T03:04:05Z\n", None),
        ("2024-01-02T03:04:05", False),
        ("2024-01-02T03:04", False),
        # Not a date-time literal: a date, an epoch, a provider spelling, and a
        # value that merely contains one. None of them answers the question.
        ("2024-01-02", None),
        ("1712345678", None),
        ("02/01/2024 03:04:05", None),
        ("seen at 2024-01-02T03:04:05Z", None),
        # Non-strings carry no zone-awareness a reader can see.
        (1712345678, None),
        (True, None),
        (None, None),
        ({"at": "2024-01-02T03:04:05Z"}, None),
    ])
    def test_sample_carries_zone(self, sample, expected):
        assert arrow_grammar.sample_carries_zone(sample) is expected
