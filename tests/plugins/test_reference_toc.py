"""Every long skill document's Contents section still matches its headings.

`scripts/render_reference_toc.py` derives the section from the document's own
`##` headings, so the list cannot be written wrong — only left behind. That is
the failure this module catches: a section renamed, added or removed with the
Contents above it untouched. Nothing in the reading path notices, because a
Contents section that has gone stale reads exactly like one that has not, and
the reader it misleads is an agent deciding whether the rest of the file is
worth loading.

Scoped to skill trees, matching the renderer. An agent definition is loaded
whole as a system prompt rather than previewed, so it needs no Contents and
gets none.

This is a locating check, not a deciding one (`.claude/rules/guards.md`): it
matches `^## ` to find headings and compares two lists of them. No verdict here
depends on what any sentence means.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "render_reference_toc.py"


def _renderer():
    spec = importlib.util.spec_from_file_location("render_reference_toc", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


toc = _renderer()


def test_the_scan_finds_documents_to_grade():
    """A green run must mean "every Contents is current", never "none found".

    The renderer globs `plugins/*/skills/**/*.md`. Move a skill tree or rename
    the directory and that glob quietly matches nothing, at which point every
    assertion below passes vacuously — the same fail-open shape the registry
    compiler once had.
    """
    assert toc.documents(), (
        f"no skill document over {toc.LONG_ENOUGH} lines was found under "
        f"{toc.PLUGINS_ROOT.relative_to(REPO_ROOT)} — the scan has stopped measuring")


@pytest.mark.parametrize(
    "path", toc.documents(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix()
)
def test_contents_section_matches_the_documents_headings(path: Path):
    current = path.read_text(encoding="utf-8")
    assert toc.render_text(current) == current, (
        f"{path.relative_to(REPO_ROOT).as_posix()}: the Contents section does not match "
        "the document's `##` headings. Run `python3 scripts/render_reference_toc.py write` "
        "(or, for a document generated in full, re-run its own renderer)")
