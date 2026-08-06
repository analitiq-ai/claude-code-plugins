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

Every file git tracks, minus `schemas/` and minus `_EXCLUDED_PATHS` — plus the
one hand-authored, mutable file under `schemas/` that the tree's rationale below
does not cover (`_SCANNED_DESPITE_TREE`).

An earlier draft named the trees to walk — `packages/`, `plugins/`, `scripts/`,
… — and that shape is the wrong default in a way worth recording, because it
failed twice. It fails **open**: a tree nobody listed is a tree nobody lints,
which is how `CONTRIBUTING.md` and `.claude-plugin/` sat outside the gate. And
it is a hand-maintained parallel copy of the repo layout, and a second copy of
a fact drifts from the first — the same reason nothing in this repo restates a
value the schema owns — and worse, narrowing it is the cheapest way to turn a
red gate green. Enumerating
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
- A bare `#108` / `(#890)` / `pre-#125`. Counted over the tree this swept, the
  keyword and bare forms ran near-even (105 and 99 of the 230 sites), with 26
  cross-repo and no URLs at all.
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

**Anchors into numbered headings** — `#12-era`, `(#12-section)`, `README.md#12`,
`spec.yaml#3`, and the same hyphen tail on a slug with no document extension
(`guide#12-section`). No ticket in this repo has ever been cited that way, so
the ambiguity resolves toward the anchor. Two separate mechanisms do this and
they cover different cases, which is why both are pinned below: the trailing
`(?!-[^#])` lookahead handles the hyphenated tail on any slug, and `_DOC_ANCHOR`
handles the purely numeric fragment (`README.md#12`) the lookahead cannot see.
A hyphen *range* (`#150-#152`) is unaffected: both ends are caught.

**A bare `#N` of six or more digits** — that is an all-digit hex colour, and it
is far past any number this tracker will reach.

**But a three-digit `#N` is NOT excluded, and short-form hex therefore reports.**
`--accent: #123` is flagged. This one is a false positive rather than a
narrowing, and it is unfixable in this direction: `#123` is equally a plausible
issue number, so excluding it would blind the gate to a whole digit width of
real refs. Recorded here so it is a known cost, met with a `_EXCLUDED_PATHS`
entry or a long-form colour if it ever lands, not a mystery red build.

**A keyword with no `#`** — `settled in issue 89`, `tracked as GH-123` — is not
matched. Both read as unnatural enough that they are unlikely to be reached for,
and a bare `issues?` + digits pattern over prose that says "issue 3 of the four"
would cost more in false positives than it buys.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Generated, and its published pins are immutable — see the module docstring.
# Pinned literally alongside `_EXCLUDED_PATHS`: this is the other half of the
# scope predicate, and shortening it (`"sc"` still covers `schemas/`) silently
# drops `scripts/` from the scan. The extent test catches that too, because it
# spells `schemas/` out itself — but only until someone editing this constant
# updates that literal to match, which is one plausible edit. The pin is what
# makes the value itself the thing under review.
_UNSCANNED_TREE = "schemas/"

# The one file under that tree the rationale does not cover: hand-authored, no
# version triple, served as a mutable pointer. Neither "generated" nor
# "immutable pin" applies to it, so it is scanned like any other authored file.
# Its sibling `data-sync-run-response/1.0.0.json` is hand-authored too but IS a
# pinned triple, so it stays out with the rest of the tree.
_SCANNED_DESPITE_TREE = ("schemas/data-sync-api/openapi.json",)

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
    # Release-please writes both of these, and every entry links into the
    # tracker: the changelog IS the GitHub-native surface, and hand-editing it
    # desyncs the release train from its own history. Exempt by PROVENANCE, not
    # by content — an entry only carries `#N` when the squash subject did, and a
    # merge-commit release (which the package release procedure requires) yields
    # entries with a commit link and no number at all.
    "plugins/analitiq-connector-builder/CHANGELOG.md",
    "plugins/analitiq-pipeline-builder/CHANGELOG.md",
    # Vendored from the engine and sha256-pinned by
    # `analitiq.contracts.arrow_grammar`. It carries no ticket reference today
    # and this exemption is doing no work — it is forward-looking cover, because
    # the file cannot be edited to remove one: any byte change fails that pin,
    # so a ref arriving from upstream would deadlock the gate against a file
    # this repo may not touch. That is why it is exempt by provenance rather
    # than by content, and why the liveness check below cannot grade it.
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
# `\d+\b` is load-bearing, not decorative. Without it the engine backtracks when
# the trailing lookahead fails: `guide#12-section` fails at `#12` and then
# happily matches `guide#1`, so an ordinary heading anchor reports as a ticket.
# Pick the example carefully — `README.md#12-layout` looks like it demonstrates
# this and does not, because `_DOC_ANCHOR` drops that one either way. The `\b`
# earns its place only on slugs with no document extension, which is exactly
# what `test_ordinary_prose_is_not_flagged` pins.
#
# The slug body is optional so a one-character name still matches: requiring two
# characters left `x#123` matched by nothing at all, since the bare pattern's
# lookbehind rejects a `#` preceded by a word character.
_CROSS_REPO_TICKET = re.compile(
    r"\b[A-Za-z](?:[A-Za-z0-9._/-]*[A-Za-z0-9])?#\d+\b(?!-[^#])"
)

# A slug ending in a document extension is a link with a fragment, never a repo:
# `README.md#12`, `spec.yaml#3`. The hyphenated form (`README.md#12-layout`) is
# already excluded by the trailing lookahead above; this covers the purely
# numeric heading the lookahead cannot see. Applied as a filter rather than a
# lookbehind because Python's `re` requires lookbehinds to be fixed-width, and
# these extensions are not.
_DOC_ANCHOR = re.compile(
    r"\.(?:md|markdown|html?|ya?ml|json|toml|txt|py|rst)#\d+$",
    re.IGNORECASE,
)

# A bare `#108`. Two-digit floor per the module docstring. The lookbehind keeps
# it from re-matching the tail of a cross-repo ref (including a markdown anchor
# like `README.md#12-layout`), from firing on `##123`, and — via `/` and `.` —
# from firing on a `#` butted onto a path or version token, `docs/#123` and
# `v1.#123`. It does NOT do the work for a JSON-Pointer `#/$defs/…`: there the
# `/` follows the `#`, so `#\d` cannot match and the lookbehind is irrelevant.
# `-` is deliberately NOT in the lookbehind, so
# `pre-#125` and the far end of a `#150-#152` range are both caught; the
# trailing lookahead is what separates a range from an anchor, and `\b` stops
# the backtracking that would otherwise slip under it.
#
# The 2-5 digit window has a ceiling as well as a floor: `#123456` is an
# all-digit hex colour, and six digits is well past any issue number this
# tracker will reach. (`#1a2b3c` never matched — it is not all digits.)
_BARE_TICKET = re.compile(r"(?<![\w#/.])#\d{2,5}\b(?!-[^#])")

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
    swallowed: a guard that cannot list the repo must not report it clean. The
    suite only ever runs from a checkout — `pytest.ini` and `conftest.py` live at
    the repo root and no wheel ships tests — so "git cannot list this tree" means
    the environment is wrong, not that the check is inapplicable.

    An EMPTY listing is that same failure wearing a success exit code, and it is
    the one shape `check=True` cannot catch: `git ls-files` exits 0 in a repo
    that tracks nothing, so every consumer downstream — the scan, the gate, and
    the extent check that grades them — reports clean over a tree it never
    looked at. Unpacking a source tarball and running `git init` for tooling
    reaches it. Refusing here covers all three at once; asserting non-vacuity in
    one test would leave the other consumers believing an empty repo.

    `cwd=REPO_ROOT` is correct inside a git worktree too, where `.git` is a file
    pointing at the parent repo: `git ls-files` resolves it and lists the
    worktree's own index.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        # git's own reason lives in stderr, and `capture_output` swallowed it.
        # Without re-emitting it, "not a git repository" and the `safe.directory`
        # dubious-ownership refusal — the realistic CI-container failure — are
        # indistinguishable, and the second one is nothing to do with checkouts.
        detail = (getattr(exc, "stderr", "") or "").strip()
        raise RuntimeError(
            f"could not enumerate tracked files under {REPO_ROOT} — this gate "
            "derives its scope from git, so it cannot run against a tree git "
            "does not know."
            + (f" git said: {detail}" if detail else "")
        ) from exc
    tracked = [line for line in result.stdout.split("\0") if line]
    if not tracked:
        raise RuntimeError(
            f"git tracks no files under {REPO_ROOT}. That is not a clean repo, "
            "it is a repo this gate cannot see: the scan, the gate, and the "
            "extent check would all pass over an empty listing. Run the suite "
            "from a checkout with an index."
        )
    return tracked


def _scanned_files() -> list[str]:
    """Every tracked file the guard reads, sorted."""
    return sorted(
        relpath
        for relpath in _tracked_files()
        if (
            not relpath.startswith(_UNSCANNED_TREE)
            or relpath in _SCANNED_DESPITE_TREE
        )
        and not _is_excluded(relpath)
    )


def _read(relpath: str) -> str:
    """One scanned file's text, or a failure that says what went wrong.

    Every scanned path is read as UTF-8. That holds for all of them today, and
    nothing keeps it holding: the first tracked binary — an icon, a screenshot in
    plugin docs — would otherwise kill the gate with a bare `UnicodeDecodeError`
    naming neither the file nor this check. Skipping the unreadable would be
    worse, since fail-open is the failure mode this whole module exists to
    avoid, so name the file and re-raise. `test_an_unreadable_file_is_never_
    skipped` holds that choice; without it, swapping the `raise` for a
    `return ""` passes the whole suite.
    """
    try:
        return (REPO_ROOT / relpath).read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError) as exc:
        raise RuntimeError(
            f"{relpath} is tracked but could not be read as UTF-8 text. This "
            "gate reads every file it scans, so a binary or missing path has to "
            "be exempted deliberately — add it to _EXCLUDED_PATHS with its "
            "reason rather than leaving it unscanned."
        ) from exc


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
        if not _DOC_ANCHOR.search(match.group(0))
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
        for lineno, matched in scan_text(_read(relpath))
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

    `expected` asks git ITSELF here rather than calling `_tracked_files()`, and
    that independence is the whole point. Sharing the enumerator makes both
    sides shrink together: a filter added inside `_tracked_files` — drop every
    `.md`, return the first 210 entries — leaves `expected` and `_scanned_files`
    agreeing perfectly on a truncated tree, and both an extent check and the gate
    pass over a repo they can no longer see. Two independent listings cannot be
    narrowed by one edit, which also retires the arbitrary count floor an earlier
    draft leaned on: 200 sounds like a lot until you notice the repo tracks
    ~300, so a third of it could vanish under the floor.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tracked = {line for line in listing.split("\0") if line}
    assert tracked, (
        f"git listed no tracked files under {REPO_ROOT}, so this check and the "
        "gate both pass over an empty scan. `_tracked_files` refuses this too; "
        "the listing here is independent, so it needs its own refusal."
    )
    expected = {
        relpath
        for relpath in tracked
        if (not relpath.startswith("schemas/") or relpath in _SCANNED_DESPITE_TREE)
        and relpath not in _EXCLUDED_PATHS
    }
    missing = sorted(expected - set(_scanned_files()))
    assert not missing, (
        f"{len(missing)} tracked files are not scanned but carry no exemption: "
        f"{missing[:10]}. The gate is blind to them — either scan them or "
        "record the exemption in _EXCLUDED_PATHS with its reason."
    )


def test_the_gate_reads_every_file_the_scan_selected(monkeypatch) -> None:
    """Guard the guard, one level down: what the gate READS, not what it selects.

    `test_the_guard_reaches_every_tracked_file_it_does_not_exempt` pins
    `_scanned_files()`. The gate does not call `_scanned_files()` — it calls
    `_references()`, which is one function further on and was watched by nothing.
    Everything the extent test defends against re-enters there unseen: slicing
    the loop, filtering it to `.py`, or the whole thing collapsing to
    `return []`, at which point the gate asserts an empty list is empty and every
    ticket reference in the repo is invisible to a fully green suite.

    So record the argument of every `_read` the traversal performs, and require
    that set to be exactly the scan. This is the assertion that makes "narrowing
    the scan is the cheapest way to turn it green" true of the gate rather than
    only of one helper.
    """
    seen: list[str] = []

    def recording_read(relpath: str) -> str:
        seen.append(relpath)
        return ""

    monkeypatch.setitem(globals(), "_read", recording_read)
    _references()

    scanned = set(_scanned_files())
    assert set(seen) == scanned, (
        f"the gate read {len(set(seen))} files but the scan selects "
        f"{len(scanned)}: {sorted(scanned - set(seen))[:10]} were selected and "
        "never read. _references() must traverse the whole scan — a filter or a "
        "slice there turns the gate off without touching anything the other "
        "guard-the-guard tests watch."
    )


def test_an_empty_listing_is_a_failure_not_a_clean_repo(tmp_path, monkeypatch) -> None:
    """The one enumeration failure that arrives with a success exit code.

    `check=True` catches "not a git repository" and the `safe.directory`
    refusal. It cannot catch a repo that simply tracks nothing: `git ls-files`
    exits 0 with empty stdout, and an empty scan satisfies every assertion in
    this module — the gate finds no refs, the extent check finds nothing
    missing, and the read-set check compares two empty sets. A source tarball
    unpacked and `git init`-ed for tooling lands exactly here.

    An earlier draft caught this incidentally, with a `len(expected) > 200`
    floor in the extent test. That floor was a magnitude claim standing in for a
    non-vacuity claim, and it was removed as arbitrary — correctly, but the
    behaviour it was covering had to move somewhere, not evaporate. It lives in
    `_tracked_files` now, where it covers every consumer rather than one test.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "CLAUDE.md").write_text("settled in issue #89\n", encoding="utf-8")
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="tracks no files"):
        _tracked_files()


def test_an_unreadable_file_is_never_skipped(tmp_path, monkeypatch) -> None:
    """`_read`'s fail-closed choice, held.

    Its docstring argues at length that skipping an unreadable file would be
    fail-open, "the failure mode this whole module exists to avoid". Nothing
    held that: swapping the `raise` for `return ""`, or `errors="strict"` for
    `errors="ignore"`, leaves the suite green while the file drops silently out
    of the scan. Both are one word.

    A repo-tracked binary cannot be planted from a test, so point `REPO_ROOT` at
    a temp tree and exercise the two ways a path becomes unreadable.
    """
    (tmp_path / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)

    with pytest.raises(RuntimeError) as undecodable:
        _read("icon.png")
    assert "icon.png" in str(undecodable.value), (
        "the failure must name the file — a bare UnicodeDecodeError names "
        "neither the path nor this gate."
    )

    with pytest.raises(RuntimeError):
        _read("never-existed.md")


def test_the_guard_excludes_only_the_paths_it_records() -> None:
    """The exclusion list is pinned exactly, so nothing joins it unreviewed.

    A predicate over the tuple (`endswith("CHANGELOG.md")`, say) is not a
    ratchet: a future `spec-CHANGELOG.md` would satisfy it and be exempted
    silently. Pinning the literal tuple makes every addition land in a diff, and
    an exemption a reviewer has to read is the whole point — a file nobody may
    lint is where the refs come back.

    `_UNSCANNED_TREE` is pinned here for the same reason and it is not
    decorative: it is the OTHER half of the scope predicate, and shortening it
    is a one-character way to shrink the scan. `"sc"` still covers `schemas/` —
    and silently drops every file in `scripts/`. The extent test independently
    reddens on that, since it spells `schemas/` out rather than reading this
    constant; what this pin adds is that the value cannot be changed together
    with that literal in one consistent-looking edit.
    """
    assert _UNSCANNED_TREE == "schemas/", (
        "_UNSCANNED_TREE is a scope control, not a convenience prefix: any value "
        "that still covers `schemas/` keeps the gate green while dropping "
        "whatever else shares the prefix."
    )
    assert _SCANNED_DESPITE_TREE == ("schemas/data-sync-api/openapi.json",), (
        "_SCANNED_DESPITE_TREE re-admits hand-authored files from the generated "
        "tree; each addition needs the reason stated inline."
    )
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

    Grade each by the reason recorded for it, because the reasons differ in kind
    and the wrong remedy is worse than no check:

    - `CONTRIBUTING.md` is exempt for its CONTENT — it cites real issues as
      worked precedent. If those citations were ever rewritten out, a whole-file
      exemption would persist over a file nobody watches, which is the state
      this guard exists to end. So assert it still cites something.
    - The changelogs and the vendored manifest are exempt by PROVENANCE: a bot
      writes the first, an upstream repo the second, and this repo may hand-edit
      neither. Asserting they *contain* a ref would fail on a perfectly ordinary
      release — release-please only carries `#N` when the squash subject did,
      and the merge-commit release the package procedure requires yields entries
      with a commit link and no number. Worse, the remedy that assertion implies
      (drop the exemption) would put the gate back in front of a file nobody is
      allowed to fix. Existence is the whole check for those.
    """
    stale = [
        excluded for excluded in _EXCLUDED_PATHS if not (REPO_ROOT / excluded).exists()
    ]
    assert not stale, (
        f"_EXCLUDED_PATHS entries {stale} name nothing in the tree — drop them "
        "so the exclusion list keeps meaning what it says."
    )
    assert scan_text(_read("CONTRIBUTING.md")), (
        "CONTRIBUTING.md is exempt because it cites real issues as worked "
        "precedent, and it no longer cites any. Drop the exemption so the file "
        "rejoins the gate."
    )


# --- Acceptance: each recognised shape, and each deliberate non-match -------
#
# The real-tree sweep above goes green the moment the repo is clean, and would
# stay green if a pattern were later broken. These pin the detector itself —
# asserting the matched TEXT, not merely that something matched, so an arm
# cannot be deleted and quietly covered by a broader pattern's tail.
#
# The fixtures below spell every ref out in full. An earlier draft split them
# ("engine" + "#390") to keep the file from matching itself; that bought nothing
# twice over — this file is exempt in `_EXCLUDED_PATHS`, and the splitting did
# not even work, since `_BARE_TICKET` happily matches a `#390` preceded by a
# quote. The exemption is what makes scanning safe, and it is recorded there.


def test_keyword_form_is_flagged() -> None:
    """Every arm at a single digit, where no other pattern can cover for it.

    At two or more digits `_BARE_TICKET` matches the tail regardless, so a
    two-digit case proves nothing about the keyword arm that produced it.
    """
    cases = {
        "the block (issue #7) adds three facts": "issue #7",
        "Issue #4 — canonical arrow_type parameters": "Issue #4",
        "Review findings (PR #3) shipped as documents": "PR #3",
        "See pull request #7 for the rationale": "pull request #7",
        "See pull requests #7 and elsewhere": "pull requests #7",
        "the two issues #8 and later": "issues #8",
        # Whitespace runs: a reflowed comment collapses to two spaces or a tab
        # far more often than anyone reaches for a second space on purpose.
        "see pull  request  #9 for this": "pull  request  #9",
        "settled in issue\t#6 upstream": "issue\t#6",
    }
    for line, expected in cases.items():
        assert scan_text(line) == [(1, expected)], f"keyword arm missed: {line!r}"


def test_cross_repo_form_is_flagged() -> None:
    cases = {
        "the SQL write path (analitiq-engine#390, settled)": "analitiq-engine#390",
        "artifacts self-declare their version (engine#413)": "engine#413",
        "the IAM role is specified by infrastructure#1018": "infrastructure#1018",
        "Adopted verbatim from analitiq-ai/analitiq-engine#392.":
            "analitiq-ai/analitiq-engine#392",
        # Single digit: only this arm can catch it.
        "settled in analitiq-engine#7 last year": "analitiq-engine#7",
        # A one-character slug. No other arm can reach it — the bare pattern's
        # lookbehind rejects a `#` preceded by a word character — so requiring
        # two characters here left this shape matched by nothing.
        "tracked on x#123 upstream": "x#123",
        # An extension `_DOC_ANCHOR` does NOT list still reports. `_DOC_ANCHOR`
        # is a suppression list, so widening it hides refs just as effectively
        # as narrowing the scan does, and nothing watched that direction:
        # adding `png`, or simplifying the whole thing to `\.\w+#\d+$`, went
        # green. This is the case that goes red when it does.
        "the crop in diagram.png#12 is wrong": "diagram.png#12",
        # A repo whose name merely ENDS in an extension word, with no dot. The
        # `\.` that opens `_DOC_ANCHOR` is what separates `spec.py#3` (a file
        # fragment) from this (a repo ref); dropping it suppresses both, and
        # slugs like this one are ordinary repo names.
        "vendored from analitiq-py#123 upstream": "analitiq-py#123",
    }
    for line, expected in cases.items():
        assert scan_text(line) == [(1, expected)], f"cross-repo arm missed: {line!r}"


def test_bare_form_is_flagged() -> None:
    cases = {
        "a token array rather than the dotted string (#108).": "#108",
        "the shape #125 was filed to eliminate.": "#125",
        "ADV-STRM-008 retired in 1.0.0rc19 (#108).": "#108",
        # A hyphen prefix is a ref, not an anchor: the lookbehind must allow it.
        "the pre-#125 spelling stayed valid": "#125",
        # Five digits is inside the window. The ceiling exists to skip six-digit
        # hex colours, so it must not creep down onto real issue numbers.
        "closes #12345 upstream": "#12345",
    }
    for line, expected in cases.items():
        assert scan_text(line) == [(1, expected)], f"bare arm missed: {line!r}"
    # A range: both ends, not just the first.
    assert scan_text("closes #150-#152 in one PR") == [
        (1, "#150"),
        (1, "#152"),
    ]
    # Short-form hex reports. Pinned as a KNOWN FALSE POSITIVE, not as desired
    # behaviour: `#123` is indistinguishable from a three-digit issue number, so
    # the only way to spare the colour is to blind the gate to that whole digit
    # width. Documented in "What it deliberately leaves wide"; here so the cost
    # is on record executably and nobody "fixes" it without meeting the trade.
    assert scan_text("  --accent: #123;") == [(1, "#123")]


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
    assert scan_text("the over-strict scope check (#7) fixed this") == []


def test_overlapping_forms_report_one_site() -> None:
    """`issue #81` is one ref, not a keyword match plus a bare match."""
    assert scan_text("the engine grammar (issue #81) is vendored.") == [
        (1, "issue #81")
    ]
    assert scan_text("mirrored from analitiq-engine#406 today.") == [
        (1, "analitiq-engine#406")
    ]
    # Two genuinely distinct refs on one line still report as two.
    assert len(scan_text("(engine ADR; issues #87, #89)")) == 2


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
        "##123 — a doubled marker, not a ref",
        "jump to [the section](#12-section) below",
        # A purely numeric heading anchor: no hyphen for the lookahead to catch,
        # so the doc-extension filter is the only thing standing between this
        # and a red build on an ordinary cross-reference.
        "see [the layout](../README.md#12) for the tree",
        "the fragment spec.yaml#3 names the third document",
        # A hyphenated anchor on a slug with NO document extension. These are
        # what `\d+\b` and the trailing `(?!-[^#])` actually defend — the
        # `README.md#12-layout` case the pattern comment used to cite is
        # dropped by `_DOC_ANCHOR` either way, so it proved nothing. Without
        # the `\b` the first reports as `guide#1`; without the lookahead the
        # second reports as `type#12`. Both are false positives on ordinary
        # prose, the cost this gate is explicitly trying not to impose.
        "see the guide#12-section for it",
        "the field type#12-variant is odd",
        # An all-digit hex colour. Six digits is past the ceiling.
        "  --accent: #123456;",
    ):
        assert scan_text(line) == [], f"false positive on: {line!r}"
