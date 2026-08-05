"""Prose-census lint — the advisory census's missing other half.

`test_advisory_registry.py` verifies the integrity of rules that EXIST; every
one of its checks starts from a registered rule, so an obligation stated in
prose and never registered is invisible to it — the hole the unenforced
pagination `response.body.*` rule shipped through. This suite closes that hole
from the prose side: EVERY prose site in ``analitiq.contracts`` — each model
field description, each model docstring, and each enum docstring (pydantic
publishes both docstring kinds into the JSON Schema; non-model, non-enum
classes such as the exception types publish no prose and are out of scope),
not just sites matching a modal
vocabulary — must carry a :class:`ProseObligation` entry binding it to an
``ADV-*`` rule, a structural mechanism, an explicit waiver, or a
``descriptive=True`` marking, and pinning its exact wording by content hash.

The lint is bidirectional so the census cannot rot in either direction: an
uncatalogued site fails (the unenforced-obligation direction), and so do a
stale entry, a broken hash pin (prose re-worded since its disposition was
affirmed), and a tripwire hit (``descriptive=True`` on modal prose). All four
groups come from one computation —
:func:`analitiq.contracts.shared.introspect.census_report`, the same diff
``scripts/render_prose_census.py`` prints — so the lint and the maintenance
tool can never disagree.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import get_args

import pytest

from analitiq.contracts.shared.advisory import all_rules
from analitiq.contracts.shared.advisory_prose import (
    NORMATIVE_PATTERN,
    ProseObligation,
)
from analitiq.contracts.shared.introspect import (
    ProseSite,
    SiteKey,
    census_report,
    contract_classes,
    prose_fingerprint,
)
from analitiq.contracts.shared.prose_census import PROSE_OBLIGATIONS

REPORT = census_report()


def test_census_has_no_duplicate_sites():
    keys = [o.key for o in PROSE_OBLIGATIONS]
    dupes = sorted({k.label for k in keys if keys.count(k) > 1})
    assert not dupes, f"duplicate census entries: {dupes}"


def test_every_prose_site_is_catalogued():
    """The unenforced-obligation direction: prose bound to nothing.

    Fix by cataloguing each site in its ``prose_census`` area module — bind it
    to the ``ADV-*`` rule enforcing it, to the structural mechanism carrying
    it, to a waiver saying why it is not mechanisable, or mark it
    ``descriptive=True`` when it states no obligation at all.
    ``scripts/render_prose_census.py write`` prints ready-to-paste skeletons
    with the computed hash; judge the real disposition before committing one.
    """
    lines = [
        f"  {site.label} [{site.module}]: hash {site.fingerprint}"
        for site in REPORT.missing
    ]
    assert not REPORT.missing, (
        "prose sites with no census entry — catalogue each in "
        "analitiq.contracts.shared.prose_census (sites are keyed to the class "
        "that DEFINES the prose, not a subclass; "
        "scripts/render_prose_census.py write prints skeletons):\n"
        + "\n".join(lines)
    )


def test_no_stale_census_entries():
    """The rot direction: an entry whose prose site no longer exists.

    Fires when the site's class or field was removed or renamed, or when an
    entry names an inheriting subclass instead of the class that defines the
    prose. Remove or re-key the entry — a census carrying dead entries stops
    being reviewable. ``scripts/render_prose_census.py check`` prints the same
    list.
    """
    lines = [f"  {key.label}" for key in REPORT.stale]
    assert not REPORT.stale, (
        "census entries with no matching prose site:\n" + "\n".join(lines)
    )


def test_census_hashes_match_live_prose():
    """The ratchet: re-worded prose must be re-affirmed, not silently kept.

    A changed description or docstring may state a new obligation the old
    disposition does not carry. Re-read the prose, adjust the entry's
    disposition if needed, then restamp the hash with
    ``scripts/render_prose_census.py write``.
    """
    lines = [
        f"  {m.site.label} [{m.site.module}]: census has {m.recorded}, "
        f"live prose hashes to {m.site.fingerprint}"
        for m in REPORT.hash_mismatches
    ]
    assert not REPORT.hash_mismatches, (
        "census entries whose prose changed since their disposition was "
        "affirmed — re-affirm each, then run "
        "scripts/render_prose_census.py write to restamp:\n" + "\n".join(lines)
    )


def test_descriptive_entries_carry_no_modal_language():
    """The tripwire: ``descriptive=True`` may not sit on modal prose.

    Prose matching ``NORMATIVE_PATTERN`` that genuinely states no obligation
    takes ``waiver=DESCRIPTIVE`` instead — marking modal text harmless must
    cost an explicit waiver, never a one-word flag.
    """
    lines = [f"  {site.label} [{site.module}]" for site in REPORT.tripwires]
    assert not REPORT.tripwires, (
        "descriptive=True entries whose live prose carries a modal marker — "
        "use waiver=DESCRIPTIVE (or a real disposition) for these:\n"
        + "\n".join(lines)
    )


def test_census_rule_ids_resolve():
    known = {r.id for r in all_rules()}
    for entry in PROSE_OBLIGATIONS:
        unknown = sorted(set(entry.rule_ids) - known)
        assert not unknown, (
            f"{entry.site}: rule ids not in the advisory registry: {unknown}"
        )


# --- The census's own texts must not rot ------------------------------------


#: Identifier-shaped tokens inside structural/waiver texts: helper and
#: validator names, snake_case fields/functions/vocabulary values, ALL_CAPS
#: constants, multi-hump CamelCase class names — plus ANY backticked word.
#: A single plain word ("headers", "authorize", "Expression") is
#: indistinguishable from English by shape, so it is checked only when
#: backticked: backtick every identifier a census text names.
_IDENTIFIER_TOKENS = re.compile(
    r"(?<=`)[A-Za-z_][A-Za-z0-9_]*(?=`)"
    r"|\b_[a-z][a-z0-9_]*\b"
    r"|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"
    r"|\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"
    r"|\b(?:[A-Z][a-z0-9]+){2,}\b"
)

#: Identifier-shaped tokens that deliberately name nothing in the contract.
_FOREIGN_TOKENS = {
    # the legacy key the Expression union rejects — its absence is the point
    "response_path",
}


def _literal_strings(annotation) -> set[str]:
    """Every string member of any Literal reachable inside an annotation."""
    out: set[str] = set()
    for arg in get_args(annotation):
        if isinstance(arg, str):
            out.add(arg)
        elif arg is not None:
            out |= _literal_strings(arg)
    return out


def test_census_texts_reference_live_names():
    """Structural/waiver texts name validators, constants and classes; a
    rename of any identifier-shaped or backticked token must fail here
    instead of leaving the census pointing at nothing. (The texts
    deliberately never restate constraint VALUES — those live in the model,
    the single source — so names are the only thing that can rot.)
    """
    universe: set[str] = set(_FOREIGN_TOKENS)
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("analitiq.contracts"):
            continue
        universe.update(dir(module))
        for name in dir(module):
            obj = getattr(module, name, None)
            if isinstance(obj, type) and issubclass(obj, Enum):
                universe.update(
                    m.value for m in obj if isinstance(m.value, str)
                )
    for cls in contract_classes():
        universe.update(dir(cls))
        universe.update(cls.model_fields)
        for info in cls.model_fields.values():
            if info.alias:
                universe.add(info.alias)
            universe |= _literal_strings(info.annotation)
    for entry in PROSE_OBLIGATIONS:
        for text in (entry.structural, entry.waiver):
            if not text:
                continue
            unknown = sorted(
                {t for t in _IDENTIFIER_TOKENS.findall(text) if t not in universe}
            )
            assert not unknown, (
                f"{entry.site}: census text names identifiers that do not "
                f"resolve in analitiq.contracts: {unknown}"
            )


_HASH = "0" * 12  # format-valid placeholder for the refusal probes


def test_prose_obligation_refuses_unbound_and_blank_dispositions():
    """__post_init__ is what makes 'catalogued' mean 'bound' — the site tests
    check only key membership, so an unbound entry would satisfy them."""
    with pytest.raises(ValueError, match="declares nothing"):
        ProseObligation(model="X", field="y", prose_hash=_HASH)
    with pytest.raises(ValueError, match="empty structural"):
        ProseObligation(model="X", field="y", prose_hash=_HASH, structural="   ")
    with pytest.raises(ValueError, match="empty waiver"):
        ProseObligation(model="X", prose_hash=_HASH, waiver=" ")


def test_prose_obligation_refuses_a_malformed_hash():
    """The ratchet only works when every entry carries a real fingerprint —
    a blank, truncated, or uppercase hash would pin nothing."""
    for bad in ("", "0" * 11, "0" * 13, "ABCDEF012345", "not-a-hash!!"):
        with pytest.raises(ValueError, match="prose_hash"):
            ProseObligation(model="X", field="y", prose_hash=bad, waiver="w")


def test_prose_obligation_refuses_descriptive_combined_with_a_disposition():
    """descriptive=True asserts there is nothing to enforce; pairing it with
    a rule, mechanism, or waiver would make the entry self-contradictory."""
    for kwargs in (
        {"rule_ids": ("ADV-ENDP-009",)},
        {"structural": "s"},
        {"waiver": "w"},
    ):
        with pytest.raises(ValueError, match="descriptive"):
            ProseObligation(
                model="X", field="y", prose_hash=_HASH, descriptive=True, **kwargs
            )
    # descriptive=True alone is a complete disposition
    ProseObligation(model="X", field="y", prose_hash=_HASH, descriptive=True)


def test_normative_pattern_tolerates_wrapped_modal_phrases():
    """Docstrings wrap; a two-word modal split across a newline must still
    match, or the first wrapped modal phrase silently escapes the tripwire."""
    for phrase in (
        "defaults\n    to",
        "may\n  not",
        "is\n  required\n  to",
        "MUST NOT",
    ):
        assert NORMATIVE_PATTERN.search(f"lead {phrase} trail"), (
            f"modal phrase not detected when wrapped: {phrase!r}"
        )


def test_normative_pattern_source_is_frozen():
    # The freeze is deliberate: the census no longer detects obligations by
    # modal vocabulary — every site is catalogued and the per-entry hash
    # ratchet catches new phrasings — so this pin failing means someone
    # widened (or narrowed) the frozen pattern. The answer to a review
    # proposing a wider modal set is: rely on the ratchet, not this pattern.
    assert NORMATIVE_PATTERN.pattern == (
        r"\bmust\b|\bevery\b|\brequires\b|\bmay\s+not\b|\bdefaults\s+to\b"
        r"|\bis\s+required\s+to\b|\bonly\b"
    )


def test_census_stays_importable_without_pydantic():
    """The census is registry data — tooling must be able to read it without
    the contract models' dependency stack. A stray top-level import would
    silently couple every census consumer to pydantic."""
    src = Path(__file__).resolve().parents[2] / "src"
    env = dict(os.environ, PYTHONPATH=str(src))
    env.setdefault("DOMAIN", "analitiq.ai")
    code = (
        "import sys\n"
        "import analitiq.contracts.shared.advisory_prose\n"
        "import analitiq.contracts.shared.prose_census\n"
        "assert 'pydantic' not in sys.modules, 'census import pulled in pydantic'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


# --- The detectors must be provably able to fire ------------------------------
#
# The real census is asserted CLEAN above, so on a healthy tree none of the
# four report groups ever goes non-empty there — these synthetic diffs are the
# proof each detector still fires at all.


def _synthetic_site(model: str, field: str | None, text: str) -> ProseSite:
    return ProseSite(key=SiteKey(model=model, field=field), module="synthetic", text=text)


def test_census_report_flags_an_uncatalogued_site():
    site = _synthetic_site("SyntheticModel", "f", "plain prose")
    report = census_report(live={site.key: site}, census=())
    assert report.missing == (site,)
    assert not report.clean


def test_census_report_flags_a_stale_entry():
    entry = ProseObligation(model="GoneModel", prose_hash="0" * 12, descriptive=True)
    report = census_report(live={}, census=(entry,))
    assert report.stale == (SiteKey(model="GoneModel", field=None),)
    assert not report.clean


def test_census_report_flags_a_hash_mismatch():
    site = _synthetic_site("SyntheticModel", "f", "reworded prose")
    stale_hash = "0" * 12
    assert stale_hash != site.fingerprint
    entry = ProseObligation(
        model="SyntheticModel", field="f", prose_hash=stale_hash, descriptive=True
    )
    report = census_report(live={site.key: site}, census=(entry,))
    assert [(m.site, m.recorded) for m in report.hash_mismatches] == [(site, stale_hash)]
    assert not report.clean


def test_census_report_flags_a_descriptive_tripwire():
    site = _synthetic_site("SyntheticModel", None, "authors must do a thing")
    entry = ProseObligation(
        model="SyntheticModel", prose_hash=site.fingerprint, descriptive=True
    )
    report = census_report(live={site.key: site}, census=(entry,))
    assert report.tripwires == (site,)
    assert not report.hash_mismatches
    assert not report.clean


def test_census_report_is_clean_on_a_matching_pair():
    site = _synthetic_site("SyntheticModel", "f", "plain prose")
    entry = ProseObligation(
        model="SyntheticModel", field="f", prose_hash=site.fingerprint, descriptive=True
    )
    assert census_report(live={site.key: site}, census=(entry,)).clean


# --- prose_fingerprint: the ratchet's hash function ----------------------------


def test_prose_fingerprint_is_rewrap_invariant():
    assert prose_fingerprint("a  b\n   c") == prose_fingerprint("a b c")


def test_prose_fingerprint_is_word_sensitive():
    assert prose_fingerprint("a b c") != prose_fingerprint("a b d")


def test_prose_fingerprint_shape():
    assert re.fullmatch(r"[0-9a-f]{12}", prose_fingerprint("any prose at all"))


def test_prose_fingerprint_known_vector():
    # sha256("a b c")[:12] — pins the normalization + truncation convention
    # every stamped prose_hash in the census depends on.
    assert prose_fingerprint("a b c") == "0e9f64031fcb"
