"""The reachability census — every contract field names its consumer.

The engine publishes ``contract-consumption``, the fields its run-time path
reads; ``census.consumption.pin`` vendors one pinned version. This suite is
offline throughout (the published-object byte-compare is the CI guard's
half) and covers:

1. **The pin**: the vendored manifest hashes to the sha256 stated beside it
   and self-declares the pinned version.
2. **The envelope**: ``load_manifest`` refuses a document that cannot define
   a census, rather than deriving an empty one that passes vacuously.
3. **The walk**: on synthetic models, coverage is what a root reaches
   through field annotations, an opaque model is recorded but not descended,
   and a model no root reaches is not covered.
4. **Classification**: only ``claims`` makes a field read; ``kit_reads`` and
   ``transport`` never do.
5. **The report**: each finding kind can go non-empty on synthetic data — a
   diff only ever asserted empty is a diff nobody has proven can fail.
6. **The live gate**: every unread field in the real contract tree carries a
   disposition, and every disposition names a field the census still holds.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

import pytest
from pydantic import BaseModel, Field

from census.consumption import pin
from census.consumption.disposition import FieldDisposition
from census.consumption.reachability import (
    census_report,
    classify,
    qualified_name,
    reachable_models,
    resolve_model,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 1. The pin
# ---------------------------------------------------------------------------


def test_vendored_manifest_hashes_to_the_pin():
    digest = hashlib.sha256(pin.MANIFEST_PATH.read_bytes()).hexdigest()
    assert digest == pin.CONSUMPTION_SHA256, (
        f"vendored {pin.MANIFEST_PATH.name} hashes to {digest}, but the pin "
        f"says {pin.CONSUMPTION_SHA256}. The vendored file and the pin "
        "constants must move together (re-vendor the published object, then "
        "re-disposition whatever the new manifest leaves unread)."
    )


def test_vendored_manifest_self_declares_the_pinned_version():
    declared = pin.load_manifest().get(pin.ARTIFACT_VERSION_KEY)
    assert declared == pin.CONSUMPTION_VERSION, (
        f"vendored {pin.MANIFEST_PATH.name} declares version {declared!r} but "
        f"the pin says {pin.CONSUMPTION_VERSION!r} — re-vendor the published "
        "object and move both pin constants together"
    )


def test_pin_module_is_stdlib_only():
    """The CI guard and the envelope check run before any contract model is
    importable, so importing the pin must load neither pydantic nor the
    contract tree — proven in a fresh interpreter, where nothing is cached."""
    probe = (
        "import sys; import census.consumption.pin; "
        "loaded = [m for m in sys.modules if m.split('.')[0] in ('pydantic', 'analitiq')]; "
        "assert not loaded, loaded"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# 2. The envelope
# ---------------------------------------------------------------------------

_WELL_FORMED = {
    "version": "0.0.0",
    "roots": ["a.B"],
    "claims": {},
    "opaque": {},
    "kit_reads": {},
    "transport": [],
}


def _write(tmp_path, document):
    path = tmp_path / pin.CONSUMPTION_FILENAME
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_load_manifest_accepts_a_well_formed_envelope(tmp_path):
    assert pin.load_manifest(_write(tmp_path, _WELL_FORMED)) == _WELL_FORMED


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"roots": []}, "`roots` is empty"),
        ({"roots": None}, "`roots` is missing"),
        ({"roots": "a.B"}, "`roots` is not a list"),
        ({"roots": [1]}, "a root is not a string"),
        ({"claims": []}, "`claims` is not a mapping"),
        ({"claims": None}, "`claims` is missing"),
        ({"opaque": []}, "`opaque` is not a mapping"),
        ({"kit_reads": []}, "`kit_reads` is not a mapping"),
        ({"transport": {}}, "`transport` is not a list"),
        ({"version": 1}, "`version` is not a string"),
        ({"version": None}, "`version` is missing"),
    ],
)
def test_load_manifest_refuses_a_malformed_envelope(tmp_path, mutation, reason):
    document = {**_WELL_FORMED, **mutation}
    document = {k: v for k, v in document.items() if v is not None}
    with pytest.raises(ValueError):
        pin.load_manifest(_write(tmp_path, document))


def test_load_manifest_refuses_a_non_object(tmp_path):
    with pytest.raises(ValueError):
        pin.load_manifest(_write(tmp_path, ["a.B"]))


# ---------------------------------------------------------------------------
# 3. The walk — synthetic models
# ---------------------------------------------------------------------------


class Leaf(BaseModel):
    value: int
    label: str


class Grammar(BaseModel):
    """Consumed whole — its fields are never read by attribute."""

    op: Literal["eq"]
    inner: Optional[Leaf] = None


class Tagged(BaseModel):
    kind: Literal["tagged"]
    payload: Leaf


class DictOnly(BaseModel):
    """Held only as a dict value, nested in a list — the deepest unwrap."""

    y: int


class ViaDict(BaseModel):
    entries: dict[str, list[DictOnly]]


class Root(BaseModel):
    direct: Leaf
    maybe: Optional[Grammar] = None
    either: Union[Tagged, int]
    annotated: Annotated[list[ViaDict], Field(min_length=0)]
    shape: Literal["root"] = "root"
    plain: str = ""


class Orphan(BaseModel):
    lonely: int


class Unclaimed(BaseModel):
    """Behind an opaque model only — reachable through no other route."""

    x: int


class Fence(BaseModel):
    """Opaque in the non-descent test; the only route to ``Unclaimed``."""

    behind: Unclaimed


class Gate(BaseModel):
    fence: Fence


_ROOT = qualified_name(Root)
_LEAF = qualified_name(Leaf)
_GRAMMAR = qualified_name(Grammar)
_TAGGED = qualified_name(Tagged)
_VIA_DICT = qualified_name(ViaDict)
_DICT_ONLY = qualified_name(DictOnly)
_ORPHAN = qualified_name(Orphan)


def _manifest(**overrides):
    document = {
        "version": "0.0.0",
        "roots": [_ROOT],
        "claims": {
            _ROOT: {"direct": ["src.a:1"], "either": ["src.a:2"]},
            _LEAF: {"value": ["src.a:3"]},
        },
        "opaque": {_GRAMMAR: {"consumer": "src.b", "dumps": [], "entries": []}},
        "kit_reads": {_LEAF: {"label": ["cdk.conformance:9"]}},
        "transport": ["src.c:4"],
    }
    document.update(overrides)
    return document


def test_resolve_model_imports_by_module_path():
    assert resolve_model(_ROOT) is Root


@pytest.mark.parametrize(
    "path", ["tests.census.test_contract_consumption.Missing", "no.such.module.X",
             "tests.census.test_contract_consumption._manifest"]
)
def test_resolve_model_refuses_what_the_tree_does_not_hold(path):
    with pytest.raises(LookupError):
        resolve_model(path)


def test_walk_reaches_models_through_every_container_shape():
    reached = reachable_models(_manifest())
    assert reached[_LEAF] is Leaf  # direct
    assert reached[_TAGGED] is Tagged  # Union member
    assert reached[_VIA_DICT] is ViaDict  # Annotated[list[...]]
    assert reached[_DICT_ONLY] is DictOnly  # dict[str, list[...]] through ViaDict


def test_walk_records_an_opaque_model_but_does_not_descend_into_it():
    reached = reachable_models(_manifest())
    assert _GRAMMAR in reached
    # Leaf is reachable anyway, through Root.direct — so prove non-descent
    # with a model held ONLY behind the opaque one.
    fence, gate = qualified_name(Fence), qualified_name(Gate)
    manifest = _manifest(
        roots=[gate], opaque={fence: {"consumer": "x", "dumps": [], "entries": []}}
    )
    reached = reachable_models(manifest)
    assert fence in reached
    assert qualified_name(Unclaimed) not in reached


def test_walk_does_not_cover_a_model_no_root_reaches():
    assert _ORPHAN not in reachable_models(_manifest())


def test_walk_refuses_a_root_the_tree_does_not_hold():
    with pytest.raises(LookupError):
        reachable_models(_manifest(roots=["tests.census.test_contract_consumption.Gone"]))


# ---------------------------------------------------------------------------
# 4. Classification
# ---------------------------------------------------------------------------


def test_classify_partitions_every_covered_field_exactly_once():
    classes = classify(_manifest())
    covered = classes["read"] | classes["opaque"] | classes["unread"]
    assert not (classes["read"] & classes["opaque"])
    assert not (classes["read"] & classes["unread"])
    assert not (classes["opaque"] & classes["unread"])
    assert (_ROOT, "direct") in classes["read"]
    assert (_LEAF, "value") in classes["read"]
    assert (_GRAMMAR, "op") in classes["opaque"]
    assert (_GRAMMAR, "inner") in classes["opaque"]
    assert (_ROOT, "plain") in classes["unread"]
    assert (_ROOT, "shape") in classes["unread"]
    assert (_TAGGED, "kind") in classes["unread"]
    assert (_VIA_DICT, "entries") in classes["unread"]
    assert (_DICT_ONLY, "y") in classes["unread"]
    assert not any(model == _ORPHAN for model, _ in covered)


def test_kit_reads_never_count_as_claims():
    classes = classify(_manifest())
    assert (_LEAF, "label") in classes["unread"]


def test_transport_never_counts_as_a_claim():
    manifest = _manifest(transport=[f"{_ROOT}.plain"])
    assert (_ROOT, "plain") in classify(manifest)["unread"]


def test_a_claim_with_no_sites_is_not_a_read():
    manifest = _manifest()
    manifest["claims"][_ROOT]["plain"] = []
    assert (_ROOT, "plain") in classify(manifest)["unread"]


def test_a_claim_on_an_opaque_model_does_not_make_its_fields_read():
    """Opaque models are consumed whole; a claim the engine also records on
    one is bookkeeping, not an attribute read the census could grade."""
    manifest = _manifest()
    manifest["claims"][_GRAMMAR] = {"op": ["src.b:1"]}
    classes = classify(manifest)
    assert (_GRAMMAR, "op") in classes["opaque"]
    assert (_GRAMMAR, "op") not in classes["read"]


# ---------------------------------------------------------------------------
# 5. The report — each finding kind on synthetic data
# ---------------------------------------------------------------------------

_LOCAL = "tests.census.test_contract_consumption"


def _entry(model: str, field: str, kind="authoring_only", reason="read by a person"):
    return FieldDisposition(model=model, field=field, kind=kind, reason=reason)


_SYNTHETIC = (Root, Leaf, Grammar, Tagged, ViaDict, DictOnly, Orphan, Unclaimed)
_SHADOW = "analitiq.contracts._synthetic_consumption"


@pytest.fixture
def shadowed(monkeypatch):
    """The synthetic models re-homed under ``analitiq.contracts`` for the
    duration of one test, so a ``FieldDisposition`` — whose ``model`` is an
    ``analitiq.contracts``-relative path by contract — can name them.
    ``monkeypatch`` undoes both the module registration and the
    ``__module__`` rewrite, so the prose census's own walk never sees them."""
    import sys
    import types

    shadow = types.ModuleType(_SHADOW)
    for cls in _SYNTHETIC:
        setattr(shadow, cls.__name__, cls)
        monkeypatch.setattr(cls, "__module__", _SHADOW)
    monkeypatch.setitem(sys.modules, _SHADOW, shadow)
    return _manifest(
        roots=[f"{_SHADOW}.Root"],
        claims={
            f"{_SHADOW}.Root": {"direct": ["s:1"], "either": ["s:2"]},
            f"{_SHADOW}.Leaf": {"value": ["s:3"]},
        },
        opaque={f"{_SHADOW}.Grammar": {"consumer": "x", "dumps": [], "entries": []}},
        kit_reads={},
    )


_M = _SHADOW.removeprefix("analitiq.contracts.")

_COMPLETE = (
    _entry(f"{_M}.Root", "plain"),
    _entry(f"{_M}.Root", "maybe"),
    _entry(f"{_M}.Root", "annotated"),
    _entry(f"{_M}.Root", "shape", kind="structural", reason="schema-pinned literal"),
    _entry(f"{_M}.Leaf", "label"),
    _entry(f"{_M}.Tagged", "kind", kind="structural", reason="discriminator"),
    _entry(f"{_M}.Tagged", "payload"),
    _entry(f"{_M}.ViaDict", "entries"),
    _entry(f"{_M}.DictOnly", "y"),
)


def test_a_complete_disposition_set_is_ok(shadowed):
    report = census_report(shadowed, _COMPLETE)
    assert report.ok, report.render()
    assert "complete and current" in report.render()


def test_unread_field_without_disposition_is_a_finding(shadowed):
    report = census_report(shadowed, _COMPLETE[1:])
    assert not report.ok
    assert report.unread_without_disposition == (
        (f"analitiq.contracts.{_M}.Root", "plain"),
    )
    assert "Root.plain" in report.render()


def test_disposition_of_a_claimed_field_is_a_finding(shadowed):
    stale = _entry(f"{_M}.Root", "direct")
    report = census_report(shadowed, _COMPLETE + (stale,))
    assert not report.ok
    assert report.disposition_now_claimed == (stale,)
    assert "Root.direct" in report.render()


@pytest.mark.parametrize(
    "entry",
    [
        _entry(f"{_M}.Orphan", "lonely"),  # no root reaches it
        _entry(f"{_M}.Grammar", "op"),  # opaque
        _entry(f"{_M}.Root", "vanished"),  # field the model does not declare
        _entry(f"{_M}.NoSuchModel", "x"),  # model that does not exist
    ],
)
def test_disposition_of_an_unknown_field_is_a_finding(shadowed, entry):
    report = census_report(shadowed, _COMPLETE + (entry,))
    assert not report.ok
    assert report.disposition_of_unknown_field == (entry,)
    assert entry.field in report.render()


def test_duplicate_dispositions_are_a_finding(shadowed):
    report = census_report(shadowed, _COMPLETE + (_COMPLETE[0],))
    assert not report.ok
    assert report.duplicate_dispositions == (
        (f"analitiq.contracts.{_M}.Root", "plain"),
    )
    assert "duplicate" in report.render()


def test_structural_disposition_on_a_non_literal_field_is_a_finding(shadowed):
    wrong = _entry(f"{_M}.Root", "plain", kind="structural", reason="claimed literal")
    report = census_report(shadowed, _COMPLETE[1:] + (wrong,))
    assert not report.ok
    assert report.structural_not_literal == (wrong,)
    assert "not Literal-typed" in report.render()


def test_structural_disposition_on_a_literal_field_is_accepted(shadowed):
    report = census_report(shadowed, _COMPLETE)
    assert report.structural_not_literal == ()


# ---------------------------------------------------------------------------
# 6. The live gate
# ---------------------------------------------------------------------------


def test_every_root_and_claimed_model_is_in_the_live_tree():
    manifest = pin.load_manifest()
    reached = reachable_models(manifest)
    for root in manifest[pin.ROOTS_KEY]:
        assert root in reached
    unreached_claims = sorted(set(manifest[pin.CLAIMS_KEY]) - set(reached))
    assert not unreached_claims, (
        "the manifest claims fields on models no root reaches — the engine "
        "and the live contract tree disagree about what is reachable: "
        f"{unreached_claims}"
    )


def test_every_unread_contract_field_carries_a_disposition():
    """The gate. An unread field with no entry, an entry for a field the
    engine now reads, an entry outside coverage, a duplicate, or a
    ``structural`` claim on a field pydantic does not settle — each fails
    here, with the same report ``scripts/render_contract_consumption.py
    check`` prints."""
    from census.consumption.dispositions import DISPOSITIONS

    report = census_report(pin.load_manifest(), DISPOSITIONS)
    assert report.ok, "\n" + report.render()
