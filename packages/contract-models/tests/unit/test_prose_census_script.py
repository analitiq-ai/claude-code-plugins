"""The maintenance script's file surgery, proven on real bytes.

``scripts/render_prose_census.py write`` edits census area files in place;
these tests pin the three properties that make that safe: a restamp changes
EXACTLY the targeted 12-char hash, a clean census is a byte-exact no-op with
exit 0, and an entry the regex cannot restamp is reported for manual work
with a nonzero exit — never a success line.
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

from analitiq.contracts.shared.introspect import (
    CensusReport,
    HashMismatch,
    ProseSite,
    SiteKey,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "render_prose_census.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("render_prose_census", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: A canonical-format area file: a one-line entry, a multi-line entry, and a
#: multi-line entry whose ``prose_hash`` is NOT a string literal — the shape
#: ``_restamp``'s substitution cannot rewrite.
_AREA_FILE = '''"""Synthetic census area file for the script tests."""
from analitiq.contracts.shared.prose_obligation import ProseObligation

_PLACEHOLDER = "0" * 12

PROSE_OBLIGATIONS = (
    ProseObligation(model="A", field="x", prose_hash="aaaaaaaaaaaa", descriptive=True),
    ProseObligation(
        model="B",
        prose_hash="bbbbbbbbbbbb",
        waiver="engine-owned",
    ),
    ProseObligation(
        model="C", field="y",
        prose_hash=_PLACEHOLDER,
        descriptive=True,
    ),
)
'''


def _tmp_census(tmp_path, monkeypatch, module):
    census_dir = tmp_path / "prose_census"
    census_dir.mkdir()
    area = census_dir / "synthetic.py"
    area.write_text(_AREA_FILE, encoding="utf-8")
    monkeypatch.setattr(module, "CENSUS_DIR", census_dir)
    return area


def test_restamp_changes_exactly_the_targeted_hash(tmp_path, monkeypatch):
    module = _load_script()
    area = _tmp_census(tmp_path, monkeypatch, module)
    before = area.read_text(encoding="utf-8")

    path = module._restamp(SiteKey(model="B", field=None), "cccccccccccc")

    assert path == area
    after = area.read_text(encoding="utf-8")
    assert after == before.replace('prose_hash="bbbbbbbbbbbb"', 'prose_hash="cccccccccccc"')
    assert before.count('prose_hash="bbbbbbbbbbbb"') == 1


def test_restamp_reports_a_block_it_cannot_rewrite(tmp_path, monkeypatch, capsys):
    """The no-op-substitution path: the entry is FOUND but carries no
    substitutable hash literal — that must surface as manual work (nonzero
    exit, the restamp-by-hand line), never as a silent success."""
    module = _load_script()
    area = _tmp_census(tmp_path, monkeypatch, module)
    before = area.read_bytes()

    assert module._restamp(SiteKey(model="C", field="y"), "dddddddddddd") is None
    assert area.read_bytes() == before

    site = ProseSite(
        key=SiteKey(model="C", field="y"), module="synthetic", text="changed prose"
    )
    report = CensusReport(
        missing=(),
        stale=(),
        hash_mismatches=(HashMismatch(site=site, recorded="0" * 12),),
    )
    assert module.write(report) == 1
    out = capsys.readouterr().out
    assert "restamp by hand" in out
    assert "->" not in out  # the per-entry success line


def test_write_reports_a_stale_only_state(tmp_path, monkeypatch, capsys):
    """A stale entry is manual work `write` cannot do itself; a stale-only
    report must surface the findings and exit non-zero — never print nothing
    and exit 0 while ``report.clean`` is False."""
    module = _load_script()
    area = _tmp_census(tmp_path, monkeypatch, module)
    before = area.read_bytes()

    report = CensusReport(
        missing=(), stale=(SiteKey(model="A", field="x"),), hash_mismatches=()
    )
    assert module.write(report) == 1
    assert area.read_bytes() == before  # only restamps are ever auto-edited

    out = capsys.readouterr().out
    assert "stale entries" in out
    assert "A.x" in out
    assert "nothing to do" not in out


def test_restamp_returns_none_for_an_unknown_entry(tmp_path, monkeypatch):
    module = _load_script()
    _tmp_census(tmp_path, monkeypatch, module)
    assert module._restamp(SiteKey(model="Nowhere", field=None), "e" * 12) is None


def test_block_key_ignores_field_tokens_inside_disposition_texts():
    module = _load_script()
    block = (
        "    ProseObligation(\n"
        '        model="B",\n'
        '        prose_hash="bbbbbbbbbbbb",\n'
        '        waiver=\'the text quotes field="decoy" verbatim\',\n'
        "    ),\n"
    )
    assert module._block_key(block) == SiteKey(model="B", field=None)


def test_entry_spans_reports_an_unclosed_block():
    module = _load_script()
    lines = ["    ProseObligation(", '        model="B",']
    try:
        module._entry_spans(lines, "synthetic.py")
    except ValueError as err:
        assert "synthetic.py" in str(err)
        assert "ProseObligation" in str(err)
    else:
        raise AssertionError("unclosed block did not raise")


def _census_dir_digest() -> dict[str, str]:
    census_dir = (
        REPO_ROOT
        / "packages"
        / "contract-models"
        / "src"
        / "analitiq"
        / "contracts"
        / "shared"
        / "prose_census"
    )
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(census_dir.glob("*.py"))
    }


def test_write_on_a_clean_census_is_a_byte_exact_noop():
    before = _census_dir_digest()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "write"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing to do" in result.stdout
    assert _census_dir_digest() == before
