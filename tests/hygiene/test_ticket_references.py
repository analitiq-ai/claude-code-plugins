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

Nothing enforced this before, so the refs accumulated to 231 sites across 53
files. Sweeping them without a guard would just restart the accumulation, which
is why this file exists: it is the half of the fix that keeps the class closed.

## What counts as a ticket reference

Four shapes:

- `issue #89`, `Issue #424`, `issues #87, #89`, `PR #131` — a keyword followed
  by a number.
- `analitiq-engine#454`, `engine#413`, `infrastructure#1018` — the
  cross-repo form GitHub itself renders as a link.
- A bare `#108` / `(#890)` — the dominant form in this repo's older prose.
- `https://github.com/analitiq-ai/analitiq-engine/issues/406` — the full URL.
  No swept site used it, and it is here precisely for that reason: it is the
  spelling closest to hand for an author whose `analitiq-engine#406` just went
  red, and it looks enough like a citation to feel legitimate. It rots
  identically.

## What it deliberately leaves wide

**A bare `#N` is only recognised at two or more digits.** Single-digit `#N` in
this codebase's prose is usually ordinal — `no-drift rule #3`, `point #2` — and
flagging those would make the gate cost more than it returns. This is a real
hole, not a costless narrowing: single-digit issues exist in this tracker and
`CONTRIBUTING.md` cites one. The keyword and cross-repo forms still catch
`issue #7` and `analitiq-engine#7` at any digit count, so what actually escapes
is the bare single-digit `(#7)`. Widen `_BARE_TICKET` if that starts happening.

**A keyword with no `#`** — `settled in issue 89`, `tracked as GH-123` — is not
matched. Both read as unnatural enough that they are unlikely to be reached for,
and a bare `issues?` + digits pattern over prose that says "issue 3 of the four"
would cost more in false positives than it buys.

## What is out of scope, and why

`_EXCLUDED_PATHS` carries every exclusion, each with its reason inline.
`test_the_guard_excludes_only_the_paths_it_records` pins the tuple exactly — so
an exemption cannot be added without landing in a diff a reviewer reads — and
`test_exclusions_are_all_live` fails if one stops matching anything, so the list
cannot outlive its reasons.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Trees holding text this repo authors, plus `""` for the repo root's own files
# (CLAUDE.md, README.md, conftest.py, …). The root is listed explicitly because
# a directory-only list silently exempts the files a new contributor reads
# first, and an exemption nobody can see is the thing this guard exists to
# prevent.
#
# `schemas/` is absent, and permanently so rather than by oversight: the tree is
# generated from the contract models, and its pinned `X.Y.Z.json` objects are
# immutable once published (the publish is first-write-wins and byte-compares on
# re-runs). Forty already-published pins still carry the old ticket text and
# always will. The only way that text leaves the served schemas is a re-render
# advancing the version, which is driven from the models this guard does scan.
_SCANNED_ROOTS = (
    "",
    "packages",
    "plugins",
    "scripts",
    "tests",
    ".github",
)

# Text formats. Anything else in these trees (fixtures, binaries) carries no
# prose a reader would mistake for an explanation.
_SCANNED_SUFFIXES = frozenset({".py", ".md", ".yml", ".yaml", ".json", ".toml", ".txt"})

# Repo-relative posix path prefixes (or exact paths) the sweep does not reach.
# Every entry names a surface where a ticket number is either GitHub-native,
# immutable, or not ours to edit. `test_the_guard_excludes_only_the_paths_it
# _records` pins this tuple exactly, so nothing joins it without review.
_EXCLUDED_PATHS = (
    # A contributor-facing process document about the issue tracker itself: it
    # teaches the consolidation rule by walking real issues as worked precedent
    # ("the model to copy is …"). The numbers are the referent a reader is being
    # sent to, not a pointer standing in for reasoning, and GitHub renders and
    # auto-links them. Removing them would delete the instruction.
    "CONTRIBUTING.md",
    # Release-please writes both of these, and every entry is a link into the
    # tracker: the changelog IS the GitHub-native surface, and hand-editing it
    # desyncs the release train from its own history.
    "plugins/analitiq-connector-builder/CHANGELOG.md",
    "plugins/analitiq-pipeline-builder/CHANGELOG.md",
    # Vendored from the engine and sha256-pinned by
    # `analitiq.contracts.arrow_grammar`. Editing a byte of it fails that pin.
    "packages/contract-models/src/analitiq/contracts/arrow_type_grammar.json",
    # This file quotes every shape it rejects, in the patterns and in the
    # synthetic acceptance fixtures below. Scanning itself is guaranteed
    # circular.
    "tests/hygiene/test_ticket_references.py",
)


# `issue #89` / `Issue #424` / `PR #131` / `pull request #7`. The keyword makes
# the intent unambiguous, so no digit floor applies.
_KEYWORD_TICKET = re.compile(
    r"\b(?:issues?|PRs?|pull\s+requests?)\s+#\d+",
    re.IGNORECASE,
)

# `analitiq-engine#406`, `engine#413`, `analitiq-ai/analitiq-engine#392`. What
# separates this from a bare ref is a slug character butted against the `#` —
# not just a word character: `.`, `/` and `-` are all in the charset, which is
# how the `org/repo#N` form matches. The trailing `(?![\w-])` keeps a markdown
# anchor into a numbered heading (`README.md#2-layout`) from reading as a ref.
_CROSS_REPO_TICKET = re.compile(r"\b[A-Za-z][A-Za-z0-9._/-]*#\d+(?![\w-])")

# A bare `#108`. Two-digit floor per the module docstring. The lookbehind keeps
# it from re-matching the tail of a cross-repo ref, from firing on `##123`, and
# from firing on a JSON-Pointer `#/$defs/…`; the lookahead mirrors the anchor
# exclusion above.
_BARE_TICKET = re.compile(r"(?<![\w#/.-])#\d{2,}(?![\w-])")

# `https://github.com/analitiq-ai/analitiq-engine/issues/406`. Host-anchored, so
# it does not fire on an ordinary path that happens to end in a number.
_URL_TICKET = re.compile(
    r"github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+",
    re.IGNORECASE,
)

_PATTERNS = (_KEYWORD_TICKET, _CROSS_REPO_TICKET, _BARE_TICKET, _URL_TICKET)


def _is_excluded(relpath: str) -> bool:
    return any(
        relpath == excluded or relpath.startswith(excluded + "/")
        for excluded in _EXCLUDED_PATHS
    )


def _files_under(root: str) -> list[Path]:
    """Candidate files for one scanned root.

    The `""` root means the repo's own top-level files and is deliberately NOT
    recursive: recursing from the root would pull in `schemas/`, `.git/`, and
    every other tree the roots list exists to choose between.
    """
    base = REPO_ROOT / root
    return list(base.glob("*") if root == "" else base.rglob("*"))


def _scanned_files() -> list[Path]:
    """Every authored text file the guard reaches, sorted."""
    return sorted(
        path
        for root in _SCANNED_ROOTS
        for path in _files_under(root)
        if path.is_file()
        and path.suffix in _SCANNED_SUFFIXES
        and not _is_excluded(path.relative_to(REPO_ROOT).as_posix())
    )


def _matches_in_line(line: str) -> list[str]:
    """The ticket refs on one line, longest form wins.

    The patterns overlap by construction: `issue #81` is matched whole by
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


# The trees and formats the gate must reach. Asserted as literals rather than by
# iterating `_SCANNED_ROOTS` / `_SCANNED_SUFFIXES`, because a loop over the
# config deletes its own assertion when someone trims the config: dropping
# `plugins` or `.md` narrows the scan by a third and a self-referential loop
# stays green through it. Every entry here is a surface whose prose ships —
# to the published schemas, to users' plugin caches, or to contributors.
_REQUIRED_ROOTS = ("", "packages", "plugins", "scripts", "tests", ".github")
_REQUIRED_SUFFIXES = (".py", ".md", ".yml")

# One named file per required root: proof the root resolves to the tree meant,
# not merely to something. The two contract modules are the highest-stakes
# surface in scope — their descriptions render into the published JSON Schemas.
_REQUIRED_FILES = (
    "CLAUDE.md",
    "packages/contract-models/src/analitiq/contracts/connector.py",
    "packages/contract-models/src/analitiq/contracts/endpoints.py",
    "plugins/analitiq-connector-builder/CLAUDE.md",
    "scripts/render_schemas.py",
    "tests/connector_builder/test_schema_drift.py",
    ".github/workflows/tests.yml",
)


def test_the_guard_reaches_the_trees_it_claims_to() -> None:
    """Guard the guard: a scan that walks nothing passes vacuously.

    The whole gate is an assertion that a list is empty, so a scan reaching zero
    files is indistinguishable from a clean repo — and narrowing the scan is the
    cheapest way to make a red gate green. Pin the required roots, suffixes, and
    a representative file per root, all as literals independent of the config
    they check.
    """
    scanned = {path.relative_to(REPO_ROOT).as_posix() for path in _scanned_files()}
    for root in _REQUIRED_ROOTS:
        prefix = root + "/" if root else ""
        matched = [
            rel
            for rel in scanned
            if rel.startswith(prefix) and (root or "/" not in rel)
        ]
        assert matched, (
            f"required root {root!r} matched no files — it was renamed, moved, "
            "or dropped from _SCANNED_ROOTS, and the gate is now blind to "
            "everything under it."
        )
    for suffix in _REQUIRED_SUFFIXES:
        assert any(rel.endswith(suffix) for rel in scanned), (
            f"required suffix {suffix!r} matched no files — it was dropped from "
            "_SCANNED_SUFFIXES, and every document in that format is unlinted."
        )
    missing = [required for required in _REQUIRED_FILES if required not in scanned]
    assert not missing, (
        f"files the gate must reach are outside the scan: {missing}. Either the "
        "layout moved and the roots need repointing, or the scan was narrowed."
    )


def test_the_guard_excludes_only_the_paths_it_records() -> None:
    """The exclusion list is pinned exactly, so nothing joins it unreviewed.

    A predicate over the tuple (`endswith("CHANGELOG.md")`, say) is not a
    ratchet: a future `spec-CHANGELOG.md` would satisfy it and be exempted
    silently. Pinning the literal tuple makes every addition land in a diff, and
    an exemption a reviewer has to read is the whole point — a file nobody may
    lint is where the refs come back.
    """
    assert _EXCLUDED_PATHS == (
        "CONTRIBUTING.md",
        "plugins/analitiq-connector-builder/CHANGELOG.md",
        "plugins/analitiq-pipeline-builder/CHANGELOG.md",
        "packages/contract-models/src/analitiq/contracts/arrow_type_grammar.json",
        "tests/hygiene/test_ticket_references.py",
    ), (
        "_EXCLUDED_PATHS changed. Each entry exempts a whole file from the gate, "
        "so state the reason inline and update this pin in the same diff."
    )


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


def test_url_form_is_flagged() -> None:
    """The escape hatch from the other three: same pointer, spelled long.

    An author whose `analitiq-engine#406` just went red reaches for this next,
    and it reads like a citation rather than a shorthand. It rots identically —
    a private repo refuses the reader either way.
    """
    for line in (
        "See https://github.com/analitiq-ai/analitiq-engine/issues/406 for why.",
        "Adopted from https://github.com/analitiq-ai/analitiq-engine/pull/392.",
        "* tracked at github.com/analitiq-ai/claude-code-plugins/issues/150",
    ):
        assert scan_text(line), f"URL ticket ref not detected: {line!r}"
    # Not host-anchored prose: an ordinary path ending in a number is untouched.
    assert scan_text("the fixture lives at corpus/connectors/postgres/17") == []


def test_single_digit_bare_refs_are_left_wide() -> None:
    """The documented narrowing: bare ordinals stay legal, by design.

    This is the gate's one acknowledged hole, not a costless win — a genuine
    bare `(#7)` escapes with them. The keyword and cross-repo forms still catch
    `issue #7` and `analitiq-engine#7`.
    """
    assert scan_text("this is the sanctioned copy (no-drift rule #3),") == []
    assert scan_text("the ADR's #5 clause") == []
    # The hole itself, pinned so it is a decision on record rather than a
    # surprise the day someone hits it.
    assert scan_text("the over-strict scope check (" + "#7) fixed this") == []


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
    """False positives would make the gate unusable, so pin the near-misses.

    The last two are the boundary `_CROSS_REPO_TICKET` sits closest to: it is
    the loosest pattern (`slug#digits`), and a JSON-Pointer `$ref` or a markdown
    anchor into a numbered heading is exactly `slug` + `#` + something. The
    scanned trees carry hundreds of the former. Both pass today — these pin that
    a future widening of the charset cannot quietly break them.
    """
    for line in (
        "#!/usr/bin/env python3",
        "# One suite for the whole monorepo.",
        "## The contract, and the runtime pin",
        "    hash_prefix = digest[:12]  # a 12-char sha256 prefix",
        "run: pip install -r requirements-dev.txt",
        "python-version: ['3.12', '3.13']",
        '"$ref": "connector.json#/$defs/Transport"',
        "see [the layout](../README.md#2-layout) for the tree",
    ):
        assert scan_text(line) == [], f"false positive on: {line!r}"
