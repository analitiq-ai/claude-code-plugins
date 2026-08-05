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

Nothing enforced this before, so the refs accumulated to 230 sites across 53
files. Sweeping them without a guard would just restart the accumulation, which
is why this file exists: it is the half of the fix that keeps the class closed.

## What it scans, and why the scope is not a list of directories

Every file git tracks, minus `schemas/` and minus `_EXCLUDED_PATHS`.

An earlier draft named the trees to walk — `packages/`, `plugins/`, `scripts/`,
… — and that shape is the wrong default in a way worth recording, because it
failed twice. It fails **open**: a tree nobody listed is a tree nobody lints,
which is how `CONTRIBUTING.md` and `.claude-plugin/` sat outside the gate. And
it is a hand-maintained parallel copy of the repo layout, so it drifts exactly
like the values `.claude/rules/no-drift-surfaces.md` forbids duplicating — and
worse, narrowing it is the cheapest way to turn a red gate green. Enumerating
from git inverts both: a new authored tree is scanned the day it lands, nothing
restates the layout, and the only way to shrink the scan is to add a line to
`_EXCLUDED_PATHS`, which is pinned literally and lands in a diff a reviewer
reads.

`schemas/` is the one tree excluded structurally rather than by path, and
permanently so: it is generated from the contract models, and its pinned
`X.Y.Z.json` objects are immutable once published (the publish is
first-write-wins and byte-compares on re-runs). Roughly forty already-published
pins still carry the old ticket text and always will. The only way that text
leaves the served schemas is a re-render advancing the version — driven from the
models this guard does scan.

## What counts as a ticket reference

Four shapes:

- `issue #89`, `Issue #424`, `issues #87, #89`, `PR #131` — a keyword followed
  by a number.
- `analitiq-engine#454`, `engine#413`, `infrastructure#1018` — the
  cross-repo form GitHub itself renders as a link.
- A bare `#108` / `(#890)` / `pre-#125` — the dominant form in this repo's
  older prose.
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
is the bare single-digit `(#7)`.

**A bare `#N` followed by `-` and a word** — `#12-era`, `(#12-section)`. That
shape is how a markdown anchor into a numbered heading is written, and no ticket
in this repo has ever been cited that way, so the ambiguity resolves toward the
anchor. A hyphen *range* (`#150-#152`) is not affected: both ends are caught.

**A keyword with no `#`** — `settled in issue 89`, `tracked as GH-123` — is not
matched. Both read as unnatural enough that they are unlikely to be reached for,
and a bare `issues?` + digits pattern over prose that says "issue 3 of the four"
would cost more in false positives than it buys.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Generated, and its published pins are immutable — see the module docstring.
_UNSCANNED_TREE = "schemas/"

# Repo-relative posix paths the sweep does not reach. Every entry names a
# surface where a ticket number is either GitHub-native, immutable, or not ours
# to edit. `test_the_guard_excludes_only_the_paths_it_records` pins this tuple
# exactly, so nothing joins it without review.
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
# separates this from a bare ref is a slug butted against the `#` — `.`, `/` and
# `-` are all inside the slug, which is how the `org/repo#N` form matches, but
# the character immediately before `#` must be alphanumeric: a repo name never
# ends in punctuation, and requiring it keeps prose like `pre-#125` reporting as
# the bare ref it is rather than as a repo called `pre-`.
#
# `\d+\b` is load-bearing, not decorative. Without the `\b` the engine backtracks
# when the lookahead fails — `README.md#12-layout` would fail at `#12` and then
# happily match `README.md#1` — which silently defeats the anchor exclusion.
_CROSS_REPO_TICKET = re.compile(
    r"\b[A-Za-z][A-Za-z0-9._/-]*[A-Za-z0-9]#\d+\b(?!-[^#])"
)

# A bare `#108`. Two-digit floor per the module docstring. The lookbehind keeps
# it from re-matching the tail of a cross-repo ref (including a markdown anchor
# like `README.md#12-layout`), from firing on `##123`, and from firing on a
# JSON-Pointer `#/$defs/…`. `-` is deliberately NOT in the lookbehind, so
# `pre-#125` and the far end of a `#150-#152` range are both caught; the
# trailing lookahead is what separates a range from an anchor, and `\b` stops
# the backtracking that would otherwise slip under it.
_BARE_TICKET = re.compile(r"(?<![\w#/.])#\d{2,}\b(?!-[^#])")

# `https://github.com/analitiq-ai/analitiq-engine/issues/406`. Host-anchored, so
# it does not fire on an ordinary path that happens to end in a number.
_URL_TICKET = re.compile(
    r"github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+",
    re.IGNORECASE,
)

_PATTERNS = (_KEYWORD_TICKET, _CROSS_REPO_TICKET, _BARE_TICKET, _URL_TICKET)


def _is_excluded(relpath: str) -> bool:
    """Exact-match only.

    Prefix and suffix matching have both been tried and both leak: a `startswith`
    arm exempts `CONTRIBUTING.md.bak`, an `endswith` arm exempts
    `docs/CONTRIBUTING.md`. Every entry is one file, so equality is the whole
    rule — and `test_exclusion_matching_is_exact` pins the near-misses.
    """
    return relpath in _EXCLUDED_PATHS


def _tracked_files() -> list[str]:
    """Every path git tracks, as repo-relative posix strings.

    Enumerating from git rather than from the filesystem is what makes the scope
    fail closed (see the module docstring). A failure here is raised, never
    swallowed: a guard that cannot list the repo must not report it clean.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.split("\0") if line]


def _scanned_files() -> list[str]:
    """Every tracked file the guard reads, sorted."""
    return sorted(
        relpath
        for relpath in _tracked_files()
        if not relpath.startswith(_UNSCANNED_TREE) and not _is_excluded(relpath)
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
            other_start <= start
            and end <= other_end
            and (other_start, other_end) != (start, end)
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
        (relpath, lineno, matched)
        for relpath in _scanned_files()
        for lineno, matched in scan_text(
            (REPO_ROOT / relpath).read_text(encoding="utf-8", errors="strict")
        )
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


def test_the_guard_reaches_every_tracked_file_it_does_not_exempt() -> None:
    """Guard the guard: extent, not a sample.

    The gate asserts a list is empty, so a scan reaching fewer files is
    indistinguishable from a clean repo — and narrowing the scan is the cheapest
    way to turn it green. The earlier version of this test sampled: it named a
    few roots, a few suffixes, one file per root. Sampling only ever proves the
    scan touches *something* in a tree, never that it touches *everything*, so
    swapping a root for one of its own subdirectories (dropping the whole
    `analitiq-pipeline-builder` plugin, say) passed clean.

    So assert extent directly, against git rather than against a second copy of
    the layout: every tracked file is scanned unless `schemas/` or
    `_EXCLUDED_PATHS` says otherwise, and nothing else may be skipped for any
    reason.
    """
    expected = {
        relpath
        for relpath in _tracked_files()
        if not relpath.startswith(_UNSCANNED_TREE) and relpath not in _EXCLUDED_PATHS
    }
    missing = sorted(expected - set(_scanned_files()))
    assert not missing, (
        f"{len(missing)} tracked files are not scanned but carry no exemption: "
        f"{missing[:10]}. The gate is blind to them — either scan them or "
        "record the exemption in _EXCLUDED_PATHS with its reason."
    )
    # Sanity floor: `git ls-files` returning nothing (wrong cwd, no checkout)
    # would make `expected` empty and the assertion above vacuous.
    assert len(expected) > 200, (
        f"only {len(expected)} tracked files enumerated — git listed far less "
        "than this repo holds, so the extent check above proved nothing."
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


def test_exclusion_matching_is_exact() -> None:
    """A look-alike path must not inherit an exemption.

    Both loosenings are one character away and neither would fail any other
    test: a prefix match exempts a `.bak`, a suffix match exempts a same-named
    file in another directory.
    """
    assert _is_excluded("CONTRIBUTING.md")
    assert not _is_excluded("CONTRIBUTING.md.bak")
    assert not _is_excluded("CONTRIBUTING.mdx")
    assert not _is_excluded("docs/CONTRIBUTING.md")
    assert not _is_excluded("plugins/analitiq-connector-builder/CONTRIBUTING.md")
    assert not _is_excluded("plugins/analitiq-connector-builder/spec-CHANGELOG.md")


def test_exclusions_are_all_live() -> None:
    """An exemption must still be doing work, not just naming a file that exists.

    Existence alone is too weak for `CONTRIBUTING.md`: its stated reason is that
    the file *cites real issues as worked precedent*. If those citations were
    ever rewritten out, a whole-file exemption would persist over a file nobody
    watches — which is exactly the state this guard was written to end. So for
    the excluded files that are exempt because of what they contain, assert they
    still contain it.
    """
    stale = [
        excluded for excluded in _EXCLUDED_PATHS if not (REPO_ROOT / excluded).exists()
    ]
    assert not stale, (
        f"_EXCLUDED_PATHS entries {stale} name nothing in the tree — drop them "
        "so the exclusion list keeps meaning what it says."
    )
    citation_exemptions = (
        "CONTRIBUTING.md",
        "plugins/analitiq-connector-builder/CHANGELOG.md",
        "plugins/analitiq-pipeline-builder/CHANGELOG.md",
    )
    inert = [
        excluded
        for excluded in citation_exemptions
        if not scan_text((REPO_ROOT / excluded).read_text(encoding="utf-8"))
    ]
    assert not inert, (
        f"{inert} are exempt because they cite tickets, but no longer cite any. "
        "Drop the exemption so the file rejoins the gate."
    )


# --- Acceptance: each recognised shape, and each deliberate non-match -------
#
# The real-tree sweep above goes green the moment the repo is clean, and would
# stay green if a pattern were later broken. These pin the detector itself —
# asserting the matched TEXT, not merely that something matched, so an arm
# cannot be deleted and quietly covered by a broader pattern's tail.


def test_keyword_form_is_flagged() -> None:
    """Every arm at a single digit, where no other pattern can cover for it.

    At two or more digits `_BARE_TICKET` matches the tail regardless, so a
    two-digit case proves nothing about the keyword arm that produced it.
    """
    cases = {
        "the block (issue " + "#7) adds three facts": "issue " + "#7",
        "Issue " + "#4 — canonical arrow_type parameters": "Issue " + "#4",
        "Review findings (PR " + "#3) shipped as documents": "PR " + "#3",
        "See pull request " + "#7 for the rationale": "pull request " + "#7",
        "See pull requests " + "#7 and elsewhere": "pull requests " + "#7",
        "the two issues " + "#8 and later": "issues " + "#8",
        # Whitespace runs: a reflowed comment collapses to two spaces or a tab
        # far more often than anyone reaches for a second space on purpose.
        "see pull  request  " + "#9 for this": "pull  request  " + "#9",
        "settled in issue\t" + "#6 upstream": "issue\t" + "#6",
    }
    for line, expected in cases.items():
        assert scan_text(line) == [(1, expected)], f"keyword arm missed: {line!r}"


def test_cross_repo_form_is_flagged() -> None:
    cases = {
        "the SQL write path (analitiq-engine" + "#390, settled)":
            "analitiq-engine" + "#390",
        "artifacts self-declare their version (engine" + "#413)":
            "engine" + "#413",
        "the IAM role is specified by infrastructure" + "#1018":
            "infrastructure" + "#1018",
        "Adopted verbatim from analitiq-ai/analitiq-engine" + "#392.":
            "analitiq-ai/analitiq-engine" + "#392",
        # Single digit: only this arm can catch it.
        "settled in analitiq-engine" + "#7 last year": "analitiq-engine" + "#7",
    }
    for line, expected in cases.items():
        assert scan_text(line) == [(1, expected)], f"cross-repo arm missed: {line!r}"


def test_bare_form_is_flagged() -> None:
    cases = {
        "a token array rather than the dotted string (" + "#108).": "#108",
        "the shape " + "#125 was filed to eliminate.": "#125",
        "ADV-STRM-008 retired in 1.0.0rc19 (" + "#108).": "#108",
        # A hyphen prefix is a ref, not an anchor: the lookbehind must allow it.
        "the pre-" + "#125 spelling stayed valid": "#125",
    }
    for line, expected in cases.items():
        assert scan_text(line) == [(1, expected)], f"bare arm missed: {line!r}"
    # A range: both ends, not just the first.
    assert scan_text("closes " + "#150-" + "#152 in one PR") == [
        (1, "#150"),
        (1, "#152"),
    ]


def test_url_form_is_flagged() -> None:
    """The escape hatch from the other three: same pointer, spelled long.

    An author whose `analitiq-engine#406` just went red reaches for this next,
    and it reads like a citation rather than a shorthand. It rots identically —
    a private repo refuses the reader either way.
    """
    cases = {
        "See https://github.com/analitiq-ai/analitiq-engine/issues/406 for why":
            "github.com/analitiq-ai/analitiq-engine/issues/406",
        "Adopted from https://github.com/analitiq-ai/analitiq-engine/pull/392":
            "github.com/analitiq-ai/analitiq-engine/pull/392",
        "tracked at github.com/analitiq-ai/claude-code-plugins/issues/150":
            "github.com/analitiq-ai/claude-code-plugins/issues/150",
        # Case-insensitive: a pasted link is not always lowercased.
        "see GitHub.com/analitiq-ai/analitiq-engine/Issues/406":
            "GitHub.com/analitiq-ai/analitiq-engine/Issues/406",
    }
    for line, expected in cases.items():
        assert scan_text(line) == [(1, expected)], f"URL arm missed: {line!r}"
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

    The last three are the boundaries the patterns sit closest to.
    `_CROSS_REPO_TICKET` is the loosest (`slug#digits`), and a JSON-Pointer
    `$ref` or a markdown anchor into a numbered heading is exactly `slug` + `#` +
    something; the scanned trees carry hundreds of the former. `##123` is what a
    doubled comment marker in front of a number looks like.
    """
    for line in (
        "#!/usr/bin/env python3",
        "# One suite for the whole monorepo.",
        "## The contract, and the runtime pin",
        "    hash_prefix = digest[:12]  # a 12-char sha256 prefix",
        "run: pip install -r requirements-dev.txt",
        "python-version: ['3.12', '3.13']",
        '"$ref": "connector.json#/$defs/Transport"',
        "see [the layout](../README.md#12-layout) for the tree",
        "##" + "123 — a doubled marker, not a ref",
        "jump to [the section](" + "#12-section) below",
    ):
        assert scan_text(line) == [], f"false positive on: {line!r}"
