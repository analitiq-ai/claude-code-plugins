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
this codebase's prose is usually ordinal — `clause #3`, `point #2` — and
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

**A reference split across a line break** — `issue #` ending one line and `89`
opening the next. Scanning is line-at-a-time, so no match spans the break. The
consequence is smaller than it sounds and differs by digit count: at two or more
digits the orphaned `#89` is still caught by the bare arm on the following line,
so the site is reported, just as a bare ref. Only the single-digit case escapes
entirely, and it escapes already — see the two-digit floor above.

## Two sibling classes, same invariant

`_FOREIGN_PATH` and `_EPHEMERAL_REFERENT` gate the same defect in other
spellings, because a ticket number is only the most common way to point at
something the reader does not have:

- **A path under `.claude/`.** `.gitignore` excludes that tree, so a citation of
  `.claude/rules/…` resolves on the machine that wrote it and nowhere else —
  worse than a ticket number, which at least resolves for anyone in the org.
  Name the rule instead, or name a skill by its skill name, which is how Claude
  Code resolves one anyway. `.gitignore` is exempt in `_FOREIGN_ALLOWED`: it is
  where the exclusion is declared, so naming the tree there is the rule rather
  than a reference to it.
- **"this PR" and friends.** A file outlives the pull request that wrote it, so
  "the hole this PR closed" points at a moment the reader is not in. Two sites
  legitimately name the pull request being processed at runtime and are pinned
  in `_EPHEMERAL_ALLOWED` rather than matched by a cleverer pattern — a CI
  comment naming the source a job grades, and a message printed about the pull
  request under check. "this branch" is left wide: in this repo it always means
  a control-flow branch.
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

# `.claude/rules/no-drift-surfaces.md`, `.claude/skills/releasing/SKILL.md`.
# `.gitignore` excludes `.claude/`, so any path under it is absent from every
# clone. Matched anywhere in a line, backticked or not, since these appear both
# ways. The leading `.` is required: `claude/` alone is not the ignored tree,
# and `plugins/analitiq-connector-builder/.claude-plugin/` is a different
# directory that IS tracked — hence `/` immediately after `claude`.
_FOREIGN_PATH = re.compile(r"\.claude/[A-Za-z0-9_./-]+")

# `.gitignore` is the file that DECIDES the tree is absent, so it is the one
# place naming a path under it is not a dangling citation — it is the rule
# itself. It escapes today only by accident: its entry is a bare `.claude/`,
# and the `+` tail needs at least one character after the slash. Adopting the
# ordinary Claude Code split (track `.claude/settings.json`, ignore
# `.claude/settings.local.json`) would turn this file red with no correct
# remedy, since the citation cannot be reworded away. Exempt by PROVENANCE, so
# the liveness check below grades existence only — requiring it to contain a
# match would fail on today's bare entry.
_FOREIGN_ALLOWED = (".gitignore",)

# "this PR", "this pull request", "this commit" — a referent that resolves only
# while the change is in flight.
#
# "this branch" is deliberately NOT here. Every occurrence in this repo means a
# control-flow branch — "without this branch it fell through", "this branch
# disables the version guarantee" — and that is the dominant sense in code
# prose generally. Flagging it would cost more than it returns, the same trade
# the single-digit `#N` narrowing makes above.
_EPHEMERAL_REFERENT = re.compile(
    r"\bthis\s+(?:PR|pull\s+request|commit)\b", re.IGNORECASE
)

# The two sites where the PR IS the runtime subject rather than a dangling
# pointer: a CI comment about which source the job grades, and a message the
# pin-contract script prints about the pull request it is checking. Pinned as
# paths for the same reason `_EXCLUDED_PATHS` is — an exemption a reviewer reads
# beats a pattern that quietly decides which mentions are legitimate.
_EPHEMERAL_ALLOWED = (
    ".github/workflows/tests.yml",
    "scripts/check_validator_pin_contract.py",
)


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
    avoid, so name the file and re-raise. That choice is held by
    `test_an_unreadable_file_is_never_skipped`; without it, swapping the `raise`
    for a `return ""` passes the whole suite.
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


def _sites(pattern: re.Pattern[str], *, skip: tuple[str, ...] = ()) -> list[tuple[str, int, str]]:
    """Every (relpath, lineno, matched-text) for one pattern across the scan.

    The two sibling gates share this rather than each writing its own
    comprehension, so there is one traversal to guard instead of one per gate.
    A per-gate copy would be independently severable — `for relpath in []`
    silences a gate while every other test stays green — and each new gate would
    have to remember to bring its own guard.

    Guarded on BOTH axes, which is the whole point of routing gates through it:
    `test_the_gate_reads_every_file_the_scan_selected` pins WHICH files it
    reaches, and `test_the_gate_scans_the_whole_document_it_read` pins that the
    bytes reach the pattern and come back located. Selection alone is not
    enough — a traversal that reads every file and scans none of it satisfies
    the first and defeats the gate, which is exactly what happened to
    `_references` before that second test existed.
    """
    return [
        (relpath, lineno, match.group(0))
        for relpath in _scanned_files()
        if relpath not in skip
        for lineno, line in enumerate(_read(relpath).splitlines(), 1)
        for match in pattern.finditer(line)
    ]


def _foreign_path_sites() -> list[tuple[str, int, str]]:
    """The gitignored-path gate's findings.

    A named collector rather than a bare `_sites(...)` call inside the gate, so
    the exemption the gate passes is itself under test. Spelled only at the call
    site, `skip=_FOREIGN_ALLOWED` can be widened to every scanned file and
    nothing notices: the acceptance cases exercise the raw pattern, and a test
    calling `_sites` directly grades the traversal but never the argument the
    GATE hands it. Both tests below go through this function for that reason.
    """
    return _sites(_FOREIGN_PATH, skip=_FOREIGN_ALLOWED)


def _ephemeral_referent_sites() -> list[tuple[str, int, str]]:
    """The expiring-referent gate's findings. Named for the same reason."""
    return _sites(_EPHEMERAL_REFERENT, skip=_EPHEMERAL_ALLOWED)


def test_no_citation_of_a_path_the_reader_cannot_have() -> None:
    """A `.claude/` path is absent from every clone — `.gitignore` excludes it."""
    found = _foreign_path_sites()
    assert not found, (
        "citations of paths under `.claude/`, which `.gitignore` excludes:\n"
        + "\n".join(f"  {rel}:{lineno} -> {cited}" for rel, lineno, cited in found)
        + "\nThe file exists only on the machine that wrote the citation. State "
        "the rule itself, or name a skill by its skill name."
    )


def test_no_referent_that_expires_when_the_change_lands() -> None:
    """"this PR" points at a moment the reader of the file is not in."""
    found = _ephemeral_referent_sites()
    assert not found, (
        "referents that expire when the change lands:\n"
        + "\n".join(f"  {rel}:{lineno} -> {phrase}" for rel, lineno, phrase in found)
        + "\nA file outlives the pull request that wrote it. State the fact, the "
        "mechanism, or the decision. If the pull request genuinely IS the "
        "runtime subject, add the path to _EPHEMERAL_ALLOWED with its reason."
    )


def test_the_sibling_class_exemptions_are_pinned_and_live() -> None:
    """Both allow-tuples are pinned exactly and must still be doing work.

    Pinned, because a whole-file exemption from a prose gate is how the phrase
    comes back. Live for different reasons, and graded differently for the same
    reason `test_exclusions_are_all_live` splits its two cases:

    - `_EPHEMERAL_ALLOWED` is exempt for CONTENT — each entry genuinely names
      the pull request being processed — so an entry that no longer contains the
      phrase is an exemption standing over a file nobody watches.
    - `_FOREIGN_ALLOWED` is exempt by PROVENANCE: `.gitignore` is where the
      exclusion is declared, and it may say `.claude/` whether or not that
      spelling happens to match. Asserting content there would fail today, on
      the bare entry that is the whole reason the file is safe by accident.
    """
    assert _EPHEMERAL_ALLOWED == (
        ".github/workflows/tests.yml",
        "scripts/check_validator_pin_contract.py",
    ), "each entry exempts a whole file — state the reason inline and update this pin."
    for relpath in _EPHEMERAL_ALLOWED:
        assert _EPHEMERAL_REFERENT.search(_read(relpath)), (
            f"{relpath} is exempt because the pull request is its runtime "
            "subject, and it no longer says so. Drop the exemption."
        )

    assert _FOREIGN_ALLOWED == (".gitignore",), (
        "_FOREIGN_ALLOWED exempts a whole file from the gitignored-path gate; "
        "state the reason inline and update this pin."
    )
    for relpath in _FOREIGN_ALLOWED:
        assert (REPO_ROOT / relpath).exists(), (
            f"{relpath} is exempt but names nothing in the tree — drop it."
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
    narrowed by one edit, which also retires the arbitrary count floor an
    earlier draft leaned on: `> 200` sounds like a lot until you notice 467
    files are tracked and 298 scanned, so a third of the scan could vanish
    under it.
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
    # `expected` is what this test actually grades, and it can be emptied
    # without emptying `tracked`: loosen the `"schemas/"` literal above to `""`
    # and everything but the one `_SCANNED_DESPITE_TREE` re-admission falls out,
    # leaving `missing` empty and the assertion below vacuous. `_tracked_files`'s
    # refusal cannot help — this test asks git itself, by design.
    #
    # State the invariant exactly rather than as a ratio. A proportional floor
    # (`> len(tracked) // 2`) was tried and is a slow time bomb: `schemas/` is
    # the LARGEST tree here, 165 of 467 tracked files, and it grows
    # monotonically because published pins are immutable and never pruned —
    # this change alone added three. `expected` stays flat while the ratio's
    # denominator climbs, so the check reddens on its own after ~129 more pins,
    # blaming a predicate that never changed. Counting what must survive has no
    # such drift: everything outside the generated tree, minus the exemptions.
    #
    # Partition rather than filter, and require both halves. A floor is only as
    # good as its operand: computing `outside_generated` with the same literal
    # the predicate uses means loosening that literal to `""` empties it, the
    # floor becomes `>= -5`, and the guard against a vacuous check is itself
    # vacuous. This repo has both a generated tree and files outside it, so an
    # empty half means the split is wrong, whatever the counts then say.
    generated = {relpath for relpath in tracked if relpath.startswith("schemas/")}
    outside_generated = tracked - generated
    assert generated and outside_generated, (
        f"the generated/authored split put {len(generated)} files under "
        f"`schemas/` and {len(outside_generated)} outside it. Neither side is "
        "empty in this repo, so an empty one means the prefix is wrong and "
        "every count derived from it is meaningless."
    )
    # Which half is which, stated as a set relation rather than a count. Swap
    # the two and the floor still passes — it just drops from 297 to 160 — so
    # non-emptiness alone does not pin the orientation. The scope admits exactly
    # authored files plus the recorded re-admissions, and nothing from the
    # generated tree by accident.
    assert expected <= outside_generated | set(_SCANNED_DESPITE_TREE), (
        "the scope admitted files from the generated tree that "
        "_SCANNED_DESPITE_TREE does not name: "
        f"{sorted(expected - outside_generated - set(_SCANNED_DESPITE_TREE))[:10]}. "
        "Either the partition is inverted or `schemas/` is being scanned."
    )
    assert len(expected) >= len(outside_generated) - len(_EXCLUDED_PATHS), (
        f"the scope predicate kept {len(expected)} files, but {len(tracked)} "
        f"are tracked and {len(outside_generated)} of them sit outside "
        "`schemas/`. Every one of those is scanned unless _EXCLUDED_PATHS says "
        "otherwise, so a smaller number means the predicate is inverted or "
        "over-broad and the extent check below would pass over whatever it "
        "dropped."
    )
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
    scanned = set(_scanned_files())

    _references()
    assert set(seen) == scanned, (
        f"the gate read {len(set(seen))} files but the scan selects "
        f"{len(scanned)}: {sorted(scanned - set(seen))[:10]} were selected and "
        "never read. _references() must traverse the whole scan — a filter or a "
        "slice there turns the gate off without touching anything the other "
        "guard-the-guard tests watch."
    )

    # `_sites` is the same traversal for the two sibling gates, and it needs the
    # same guard for the same reason: `for relpath in []` there silences both
    # while every other test stays green.
    seen.clear()
    _sites(_FOREIGN_PATH)
    assert set(seen) == scanned, (
        "_sites() skipped "
        f"{sorted(scanned - set(seen))[:10]} — the sibling gates read whatever "
        "it traverses, so a narrowing here turns both off at once."
    )

    # Then each gate through ITS OWN collector, against the PINNED constant.
    # Spelling the exemption here instead grades an argument this test supplies,
    # which is not the one the gate hands over: `skip=_FOREIGN_ALLOWED + ("
    # "README.md", "CLAUDE.md")` inside the collector drops two real files from
    # a gate with every constant untouched and the whole suite green. The
    # end-to-end fixture cannot see it either — its tmp repo holds no file by
    # those names. Comparing the collector's real read set against the pinned
    # tuple is what makes a narrowing land in a diff, the same way the tuples
    # themselves are pinned.
    for collector, allowed, name in (
        (_foreign_path_sites, _FOREIGN_ALLOWED, "gitignored-path"),
        (_ephemeral_referent_sites, _EPHEMERAL_ALLOWED, "expiring-referent"),
    ):
        seen.clear()
        collector()
        assert set(seen) == scanned - set(allowed), (
            f"the {name} gate read the wrong set: "
            f"{sorted((scanned - set(allowed)) - set(seen))[:10]} were selected "
            "and never read. Its `skip` must be exactly the pinned exemption "
            "tuple — no more, or files leave the gate without leaving a diff."
        )


def test_the_gate_scans_the_whole_document_it_read(tmp_path, monkeypatch) -> None:
    """The data path, end to end: bytes in, located matches out.

    Every other guard-the-guard test here verifies SELECTION — which files are
    listed, which are scanned, which are read. The path from the bytes `_read`
    returned to the matches the gate reports was verified nowhere, and it is
    just as cheap to sever:

        scan_text(_read(relpath) and "")            # read everything, scan nothing
        scan_text(_read(relpath).replace("#", ""))  # read everything, defang it
        text.splitlines()[:100]                     # scan the top of each file
        ... if len(line) < 200                      # skip long lines, i.e. most JSON

    All four leave the gate asserting an empty list is empty over the whole
    repo. `test_the_gate_reads_every_file_the_scan_selected` cannot catch any of
    them and never could: its recording `_read` returns `""`, so it proves the
    call happened with the right argument and can say nothing about what came
    back. Two tests, two different questions.

    The fixture is shaped to make each one fail. The 299 filler lines put both
    refs past any plausible head-slice; the 400-character line defeats a length
    filter; asserting the LINE NUMBERS pins the enumerator, which every
    single-line acceptance fixture leaves free to be a constant `1` — and when
    this gate is red, `rel:lineno -> matched` is its entire product, so a
    constant sends every author to line 1 of a 400-line file.

    The FORM FEED, alone, pins `splitlines()` against a plain `split("\n")`:
    `splitlines()` treats `\x0c` as a break and `split("\n")` does not, so that
    one character is worth a line — the refs sit at 302 and 303 under the real
    implementation and at 301 and 302 under the mutant. The `\r\n` line endings
    carry none of that (measured: identical counts with `\n`); they are here
    because a repo holds CRLF files and this fixture should look like one.

    `_sites` is driven through the same fixture, because it is a SECOND
    traversal with the same four severances available and none of them shared
    with `_references`. Routing the sibling gates through one helper bought one
    place to guard, not automatic coverage: the first version of this file
    guarded `_references` here and left `_sites` watched only for selection, at
    which point reading every file and defanging the text turned both sibling
    gates off with the suite green.
    """
    filler = "prose carrying no reference at all, padded out\r\n" * 299
    tail = (
        "a line broken by a form feed\x0c\n"
        + "x" * 400 + " see analitiq-engine#406 for it\n"
        + "settled in issue #89 upstream\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "notes.md").write_text(filler + tail, encoding="utf-8")
    # A second document for the sibling gates, same shape: both referents land
    # past line 300 and one rides a line no length filter would read.
    (tmp_path / "guide.md").write_text(
        filler
        + "a line broken by a form feed\x0c\n"
        + "x" * 400 + " per .claude/rules/no-drift-surfaces.md this holds\n"
        + "the wiring this PR extended is routed\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "notes.md", "guide.md"], cwd=tmp_path, check=True)
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)

    # Through the GATES' own collectors, not `_sites` directly: that is what
    # puts the exemption each one passes under test. Neither `_FOREIGN_ALLOWED`
    # nor `_EPHEMERAL_ALLOWED` names `guide.md`, so both must report it.
    assert _foreign_path_sites() == [
        ("guide.md", 302, ".claude/rules/no-drift-surfaces.md"),
    ]
    assert _ephemeral_referent_sites() == [("guide.md", 303, "this PR")]
    # And `skip` removes exactly the named file, not the pattern's ability to
    # match: the same document reports nothing once it is exempt.
    assert _sites(_EPHEMERAL_REFERENT, skip=("guide.md",)) == []

    assert _references() == [
        ("notes.md", 302, "analitiq-engine#406"),
        ("notes.md", 303, "issue #89"),
    ]


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
    assert scan_text("this is the sanctioned copy (clause #3),") == []
    assert scan_text("the ADR's #5 clause") == []
    # The hole itself, pinned so it is a decision on record rather than a
    # surprise the day someone hits it.
    assert scan_text("the over-strict scope check (#7) fixed this") == []


def test_a_keyword_without_a_hash_is_left_wide() -> None:
    """The fifth documented narrowing, which had no case of its own.

    `_KEYWORD_TICKET` requires the `#`. Widening it to `#?` — or bolting on a
    `GH-\\d+` arm — passed every other test here, so the narrowing the module
    docstring argues for could be reversed silently. The argument is that prose
    counts things: "issue 3 of the four" and "PR 2 of a stack" are ordinary
    sentences, and a digits-after-keyword pattern reddens both.
    """
    assert scan_text("settled in issue 89 of the old tracker") == []
    assert scan_text("tracked as GH-123 elsewhere") == []
    assert scan_text("issue 3 of the four is the hard one") == []
    assert scan_text("PR 2 of a three-part stack") == []


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


def test_foreign_path_form_is_flagged() -> None:
    """Both spellings the swept sites used, backticked and bare."""
    cases = {
        "Per `.claude/rules/no-drift-surfaces.md` a restatement must be pinned":
            ".claude/rules/no-drift-surfaces.md",
        "see the `releasing` skill (.claude/skills/releasing/SKILL.md).":
            ".claude/skills/releasing/SKILL.md",
        "settings live in .claude/settings.json today": ".claude/settings.json",
    }
    for line, expected in cases.items():
        found = _FOREIGN_PATH.findall(line)
        assert found == [expected], f"foreign-path arm missed: {line!r}"

    # The tracked look-alikes. `.claude-plugin/` is a real directory in this
    # repo — the marketplace manifest lives there — so requiring `/` straight
    # after `claude` is what keeps the gate off it.
    for line in (
        "`.claude-plugin/marketplace.json` declares the marketplace",
        "plugins/analitiq-connector-builder/.claude-plugin/plugin.json",
        "the claude/rules directory is not the ignored one",
    ):
        assert not _FOREIGN_PATH.findall(line), f"false positive on: {line!r}"


def test_ephemeral_referent_form_is_flagged() -> None:
    cases = {
        "the wiring this PR extended is routed": "this PR",
        "the defect class This Pull Request closed": "This Pull Request",
        "tried earlier in this  pull\trequest and abandoned": "this  pull\trequest",
        "push the release tag at this commit": "this commit",
    }
    for line, expected in cases.items():
        # No capture groups, so `findall` yields whole matches: this asserts the
        # matched TEXT and the count at once, as the ticket arms above do.
        assert _EPHEMERAL_REFERENT.findall(line) == [expected], (
            f"ephemeral arm missed: {line!r}"
        )

    # The documented narrowing, and the words the pattern sits closest to.
    for line in (
        "without this branch it fell through to the dict case",
        "this branch disables the version-exactness guarantee",
        "this PRs list is not a thing anyone writes",
        "the PR body carries the numbers instead",
    ):
        assert not _EPHEMERAL_REFERENT.findall(line), f"false positive on: {line!r}"


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
        # `.py` is in the extension list and was the one entry nothing held:
        # deleting it turns an ordinary source cross-reference red.
        "the fragment spec.py#3 names the third document",
        # A hyphenated anchor on a slug with NO document extension. These are
        # what `\d+\b` and the trailing `(?!-[^#])` actually defend — the
        # `README.md#12-layout` case the pattern comment used to cite is
        # dropped by `_DOC_ANCHOR` either way, so it proved nothing. Without
        # the `\b` the first reports as `guide#1`; without the lookahead the
        # second reports as `type#12`. Both are false positives on ordinary
        # prose, the cost this gate is explicitly trying not to impose.
        "see the guide#12-section for it",
        "the field type#12-variant is odd",
        # `_BARE_TICKET`'s lookbehind names these two in its comment and nothing
        # else held them: drop `/` or `.` from the class and both report.
        "the note at docs/#123 covers it",
        "pinned at v1.#123 for now",
        # `_KEYWORD_TICKET`'s leading `\b`. Without it the keyword matches
        # inside a longer word, and both of these are ordinary English.
        "a reissue #7 of the doc went out",
        "the tissues #4 metaphor never landed",
        # `_URL_TICKET` requires at least one digit. With `\d*` a link to the
        # issue LIST reports as a reference to an issue.
        "browse github.com/analitiq-ai/analitiq-engine/issues/ for context",
        # An all-digit hex colour. Six digits is past the ceiling.
        "  --accent: #123456;",
    ):
        assert scan_text(line) == [], f"false positive on: {line!r}"
