"""Prose-census lint — the rule registry's missing other half.

`test_rule_registry.py` verifies the integrity of rules that EXIST; every
one of its checks starts from a registered rule, so an obligation stated in
prose and never registered is invisible to it — the hole the unenforced
pagination `response.body.*` rule shipped through. This suite closes that hole
from the prose side: EVERY prose site in ``analitiq.contracts`` — each field
description and docstring of every pydantic model, and the docstring of every
``Enum``, membership by category, mechanical and judgment-free (for public
enums pydantic publishes the class docstring into the schema description;
private helper enums ride along under the same category rather than requiring
a per-class publishability judgment that would rot; exception classes and
other plain classes publish nothing and are out of scope, as are enum MEMBER
docstrings, which pydantic does not publish) — must carry a
:class:`ProseObligation` entry binding it to an ``RULE-*`` rule, a structural
mechanism, an explicit waiver, or a ``descriptive=True`` marking, and pinning
its exact wording by content hash.

The lint is bidirectional so the census cannot rot in either direction: an
uncatalogued site fails (the unenforced-obligation direction), and so do a
stale entry and a broken hash pin (prose re-worded since its disposition was
affirmed). All three groups come from one computation —
:func:`census.sites.census_report`, the same diff
``scripts/render_prose_census.py`` prints — so the lint and the maintenance
tool can never disagree.

Every one of those is a set or hash comparison. Whether an entry declares the
RIGHT disposition — whether this sentence states an obligation at all — is a
judgment, and it lives in ``.claude/rules/contract-prose.md``. A hash mismatch
is how this suite summons the person who makes it.
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

from analitiq.contracts.shared.rules import all_rules
from census.obligation import ProseObligation
from analitiq.contracts.shared.introspect import contract_classes
from census.sites import (
    ProseSite,
    SiteKey,
    census_report,
    prose_fingerprint,
)
from census import PROSE_OBLIGATIONS

REPORT = census_report()


def test_census_has_no_duplicate_sites():
    keys = [o.key for o in PROSE_OBLIGATIONS]
    dupes = sorted({k.label for k in keys if keys.count(k) > 1})
    assert not dupes, f"duplicate census entries: {dupes}"


def test_every_prose_site_is_catalogued():
    """The unenforced-obligation direction: prose bound to nothing.

    Fix by cataloguing each site in its ``census/areas`` module — bind it
    to the ``RULE-*`` rule enforcing it, to the structural mechanism carrying
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
        "census (sites are keyed to the class "
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


def test_census_rule_ids_resolve():
    known = {r.id for r in all_rules()}
    for entry in PROSE_OBLIGATIONS:
        unknown = sorted(set(entry.rule_ids) - known)
        assert not unknown, (
            f"{entry.site}: rule ids not in the rule registry: {unknown}"
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


# --- Enum MEMBER docstrings: the census's blind spot, guarded by a reader ----
#
# The census covers every Enum's CLASS docstring; member docstrings are out of
# scope because pydantic does not publish them into the schema. That exclusion
# is safe only while no member docstring states an obligation — a judgment
# about what a sentence says, so it is an authoring obligation in
# `.claude/rules/contract-prose.md`, read by a person, and there is no guard
# here for it. `.claude/rules/validator-claims.md` argues the general case.


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
        {"rule_ids": ("RULE-ENDP-009",)},
        {"structural": "s"},
        {"waiver": "w"},
    ):
        with pytest.raises(ValueError, match="descriptive"):
            ProseObligation(
                model="X", field="y", prose_hash=_HASH, descriptive=True, **kwargs
            )
    # descriptive=True alone is a complete disposition
    ProseObligation(model="X", field="y", prose_hash=_HASH, descriptive=True)


def test_census_stays_importable_without_pydantic():
    """The census is registry data — tooling must be able to read it without
    the contract models' dependency stack. A stray top-level import would
    silently couple every census consumer to pydantic."""
    repo_root = Path(__file__).resolve().parents[2]
    src = repo_root / "packages" / "contract-models" / "src"
    # The census lives at the repo root, the models under `src` — the
    # subprocess needs both, and inherits neither.
    env = dict(os.environ, PYTHONPATH=f"{src}{os.pathsep}{repo_root}")
    env.setdefault("DOMAIN", "analitiq.ai")
    code = (
        "import sys\n"
        "import census.obligation\n"
        "import census\n"
        "assert 'pydantic' not in sys.modules, 'census import pulled in pydantic'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


# --- The detectors must be provably able to fire ------------------------------
#
# The real census is asserted CLEAN above, so on a healthy tree no report group
# ever goes non-empty there — these synthetic diffs are the proof each detector
# still fires at all.


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
