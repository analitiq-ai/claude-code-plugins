"""Authored code and prose must state their mechanism, not cite a ticket.

A ticket reference — `issue #89`, `analitiq-engine#406`, a bare `(#123)` — is a
pointer to an explanation that lives somewhere else. It rots two ways at once.
The tracker it names can renumber, migrate, or be private to a reader who has
the code but not the org, so the pointer stops resolving. And even while it
resolves, it stands in for the reasoning the comment was supposed to carry: the
sentence reads as if it explained something, and the reader who follows the link
finds a thread, not a rule. Contract prose makes it worse still — field
descriptions render into the JSON Schemas published at the public schema host,
so an internal ticket number ships to every consumer of the contract.

The rule is not "never mention history". It is: **state the fact, the decision,
or the mechanism, self-containedly.** Ticket numbers belong to GitHub-native
surfaces — commit messages, PR bodies, issue threads — where the tracker is the
medium rather than a dangling reference out of one.

Nothing enforced this before, so the refs accumulated to roughly 250 sites
across 40-odd files. Sweeping them without a guard would just restart the
accumulation, which is why this file exists: it is the half of the fix that
keeps the class closed.

## What counts as a ticket reference

Three shapes, all of which appear in the swept sites:

- `issue #89`, `Issue #424`, `issues #87, #89`, `PR #131` — a keyword followed
  by a number.
- `analitiq-engine#454`, `engine#413`, `infrastructure#1018` — the
  cross-repo form GitHub itself renders as a link.
- A bare `#108` / `(#890)` — the dominant form in this repo's older prose.

## What it deliberately leaves wide

A bare `#N` is only recognised at **two or more digits**. Single-digit `#N` is
overwhelmingly ordinal in this codebase's prose — `no-drift rule #3`, `point #2`
— and no ticket this repo cites has a single-digit number, so narrowing here
buys a real reduction in false positives at no cost to the invariant today. If a
`#7`-style ticket ref ever needs catching, widen `_BARE_TICKET`; the keyword and
cross-repo forms already catch `issue #7` and `analitiq-engine#7` regardless of
digit count.

## What is out of scope, and why

`_EXCLUDED_PATHS` carries the exclusions; each entry is a recorded decision
rather than a convenience, and `test_exclusions_are_all_live` fails if one stops
matching anything so the list cannot quietly outlive its reasons.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Roots holding text this repo authors. `schemas/` is absent on purpose — see
# `_EXCLUDED_PATHS`.
_SCANNED_ROOTS = (
    "packages",
    "plugins",
    "scripts",
    "tests",
    ".github",
)

# Text formats. Anything else in these trees (fixtures, binaries) carries no
# prose a reader would mistake for an explanation.
_SCANNED_SUFFIXES = frozenset({".py", ".md", ".yml", ".yaml", ".json", ".toml", ".txt"})

# Repo-relative posix path prefixes (or exact paths) that the sweep does not
# reach. Every entry names a surface where a ticket number is either
# GitHub-native, immutable, or not ours to edit.
_EXCLUDED_PATHS = (
    # Release-please writes these, and every entry is a link into the tracker:
    # the changelog IS the GitHub-native surface, and hand-editing it desyncs
    # the release train from its own history.
    "plugins/analitiq-connector-builder/CHANGELOG.md",
    "plugins/analitiq-pipeline-builder/CHANGELOG.md",
    # Vendored from the engine and sha256-pinned by
    # `analitiq.contracts.arrow_grammar`. Editing a byte of it fails that pin.
    "packages/contract-models/src/analitiq/contracts/arrow_type_grammar.json",
    # This file quotes every shape it rejects, in the patterns and in the
    # synthetic acceptance fixtures below. Scanning itself is guaranteed
    # circular. `test_the_guard_excludes_only_itself_by_file` pins that this is
    # the only whole-file self-exemption.
    "tests/hygiene/test_ticket_references.py",
)


# `issue #89` / `Issue #424` / `PR #131` / `pull request #7`. The keyword makes
# the intent unambiguous, so no digit floor applies.
_KEYWORD_TICKET = re.compile(
    r"\b(?:issues?|PRs?|pull\s+requests?)\s+#\d+",
    re.IGNORECASE,
)

# `analitiq-engine#406`, `engine#413`, `analitiq-ai/analitiq-engine#392`. A word
# character immediately before `#` is what distinguishes this from a bare ref;
# the charset covers the org/repo slug forms GitHub links.
_CROSS_REPO_TICKET = re.compile(r"\b[A-Za-z][A-Za-z0-9._/-]*#\d+")

# A bare `#108`. Two-digit floor per the module docstring. The lookbehind keeps
# it from re-matching the tail of a cross-repo ref and from firing on `##123`.
_BARE_TICKET = re.compile(r"(?<![\w#/.-])#\d{2,}\b")

_PATTERNS = (_KEYWORD_TICKET, _CROSS_REPO_TICKET, _BARE_TICKET)


def _is_excluded(relpath: str) -> bool:
    return any(
        relpath == excluded or relpath.startswith(excluded + "/")
        for excluded in _EXCLUDED_PATHS
    )


def _scanned_files() -> list[Path]:
    """Every authored text file the guard reaches, sorted."""
    return sorted(
        path
        for root in _SCANNED_ROOTS
        for path in (REPO_ROOT / root).rglob("*")
        if path.is_file()
        and path.suffix in _SCANNED_SUFFIXES
        and not _is_excluded(path.relative_to(REPO_ROOT).as_posix())
    )


def _matches_in_line(line: str) -> list[str]:
    """The ticket refs on one line, longest form wins.

    The three patterns overlap by construction: `issue #81` is matched whole by
    `_KEYWORD_TICKET` and its tail again by `_BARE_TICKET`. Reporting both would
    make one ref read as two sites, so a match contained in another match's span
    is dropped.
    """
    spans = sorted(
        (match.start(), match.end(), match.group(0))
        for pattern in _PATTERNS
        for match in pattern.finditer(line)
    )
    return [
        matched
        for start, end, matched in spans
        if not any(
            other_start <= start and end <= other_end and (other_start, other_end) != (start, end)
            for other_start, other_end, _ in spans
        )
    ]


def scan_text(text: str) -> list[tuple[int, str]]:
    """Every (lineno, matched-text) ticket reference in one document."""
    return [
        (lineno, matched)
        for lineno, line in enumerate(text.splitlines(), 1)
        for matched in _matches_in_line(line)
    ]


def _references() -> list[tuple[str, int, str]]:
    """Every (relpath, lineno, matched-text) ticket reference in the repo."""
    return [
        (path.relative_to(REPO_ROOT).as_posix(), lineno, matched)
        for path in _scanned_files()
        for lineno, matched in scan_text(path.read_text(encoding="utf-8"))
    ]


def test_no_ticket_references_in_authored_text() -> None:
    """A ticket ref is an explanation the reader cannot read."""
    found = _references()
    assert not found, (
        "ticket references found in authored code or prose:\n"
        + "\n".join(f"  {rel}:{lineno} -> {matched}" for rel, lineno, matched in found)
        + "\nState the mechanism, the decision, or the fact itself instead. "
        "Ticket numbers belong in commit messages, PR bodies, and issue "
        "threads — not in files a reader has without the tracker."
    )


def test_the_guard_reaches_the_trees_it_claims_to() -> None:
    """Guard the guard: a root that resolves to nothing passes vacuously.

    The whole gate is an assertion that a list is empty, so a scan that walks
    zero files is indistinguishable from a clean repo. Pin per-root file counts
    rather than a single total, so deleting or renaming one root is caught even
    while the others keep the total healthy.
    """
    scanned = {path.relative_to(REPO_ROOT).as_posix() for path in _scanned_files()}
    for root in _SCANNED_ROOTS:
        assert any(rel.startswith(root + "/") for rel in scanned), (
            f"scanned root {root!r} matched no files — it was renamed or moved, "
            "and the gate is now blind to everything under it."
        )
    # The contract prose that renders into the published JSON Schemas is the
    # highest-stakes surface in scope; name two of its files outright so a
    # package-layout change cannot silently drop them.
    assert "packages/contract-models/src/analitiq/contracts/connector.py" in scanned
    assert "packages/contract-models/src/analitiq/contracts/endpoints.py" in scanned


def test_the_guard_excludes_only_itself_by_file() -> None:
    """Only this module may exempt a whole Python/Markdown file of authored prose.

    The other exclusions are a generated changelog and a vendored, hash-pinned
    manifest. If a third authored file ever lands here, the exemption is the
    finding — a file nobody may lint is where the refs come back.
    """
    authored_exemptions = [
        excluded
        for excluded in _EXCLUDED_PATHS
        if not excluded.endswith(("CHANGELOG.md", "arrow_type_grammar.json"))
    ]
    assert authored_exemptions == ["tests/hygiene/test_ticket_references.py"]


def test_exclusions_are_all_live() -> None:
    """An exclusion matching nothing is dead config that can mask a later file."""
    stale = [
        excluded
        for excluded in _EXCLUDED_PATHS
        if not (REPO_ROOT / excluded).exists()
    ]
    assert not stale, (
        f"_EXCLUDED_PATHS entries {stale} name nothing in the tree — drop them "
        "so the exclusion list keeps meaning what it says."
    )


# --- Acceptance: each recognised shape, and each deliberate non-match -------
#
# The real-tree sweep above goes green the moment the repo is clean, and would
# stay green if a pattern were later broken. These pin the detector itself.

def test_keyword_form_is_flagged() -> None:
    for line in (
        "# Capability block v2 (issue " + "#89) adds three facts.",
        "Issue " + "#424 — canonical arrow_type parameters.",
        "# Review findings (PR " + "#131) — each shipped as a document.",
        "See pull request " + "#7 for the rationale.",
    ):
        assert scan_text(line), f"keyword ticket ref not detected: {line!r}"


def test_cross_repo_form_is_flagged() -> None:
    for line in (
        "the engine's SQL write path (analitiq-engine" + "#390, settled).",
        "Both artifacts self-declare their version (engine" + "#413).",
        "the publisher IAM role is specified by infrastructure" + "#1018",
        "Adopted verbatim from analitiq-ai/analitiq-engine" + "#392.",
    ):
        assert scan_text(line), f"cross-repo ticket ref not detected: {line!r}"


def test_bare_form_is_flagged() -> None:
    for line in (
        "Why a token array rather than the dotted string this replaced (" + "#108).",
        "the shape " + "#125 was filed to eliminate.",
        "ADV-STRM-008 retired in 1.0.0rc19 (" + "#108).",
    ):
        assert scan_text(line), f"bare ticket ref not detected: {line!r}"


def test_single_digit_bare_refs_are_left_wide() -> None:
    """The documented narrowing: bare ordinals stay legal, by design."""
    assert scan_text("this is the sanctioned copy (no-drift rule #3),") == []
    assert scan_text("the ADR's #5 clause") == []


def test_overlapping_forms_report_one_site() -> None:
    """`issue #81` is one ref, not a keyword match plus a bare match."""
    assert scan_text("the engine grammar (issue " + "#81) is vendored.") == [
        (1, "issue " + "#81")
    ]
    assert scan_text("mirrored from analitiq-engine" + "#406 today.") == [
        (1, "analitiq-engine" + "#406")
    ]
    # Two genuinely distinct refs on one line still report as two.
    assert len(scan_text("(engine ADR; issues " + "#87, " + "#89)")) == 2


def test_ordinary_prose_is_not_flagged() -> None:
    """False positives would make the gate unusable, so pin the near-misses."""
    for line in (
        "#!/usr/bin/env python3",
        "# One suite for the whole monorepo.",
        "## The contract, and the runtime pin",
        "    hash_prefix = digest[:12]  # a 12-char sha256 prefix",
        "run: pip install -r requirements-dev.txt",
        "python-version: ['3.12', '3.13']",
    ):
        assert scan_text(line) == [], f"false positive on: {line!r}"
