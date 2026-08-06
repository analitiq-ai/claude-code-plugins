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
hand-authored files under `schemas/` that the tree's rationale below does not
cover (`_SCANNED_DESPITE_TREE`).

An earlier draft named the trees to walk — `packages/`, `plugins/`, `scripts/`,
… — and that shape is the wrong default for two reasons. It fails **open**: a
tree nobody listed is a tree nobody lints, which is how `CONTRIBUTING.md` and
`.claude-plugin/` sat outside that draft's scope. And it is a hand-maintained
parallel copy of the repo layout, so it drifts from the layout the way any
second copy of a fact drifts from the first — the same reason nothing in this
repo restates a value the schema owns — with narrowing it the cheapest way to
turn a red gate green. Enumerating from git inverts both: a new authored tree is
scanned the day it lands, nothing restates the layout, and the only way to
shrink the scan is to add a line to `_EXCLUDED_PATHS`, which is pinned literally
and lands in a diff a reviewer reads.

`schemas/` is the one tree excluded structurally rather than by path, and
permanently so: it is generated from the contract models, and its pinned
`X.Y.Z.json` objects are immutable once published (the publish is
first-write-wins and byte-compares on re-runs). Dozens of already-published pins
still carry the old ticket text and always will. The only way that text leaves
the served schemas is a re-render advancing the version — driven from the models
this guard does scan.

That rationale covers only what the renderer writes, so it cannot be applied to
the tree by prefix and left there. A hand-authored file under `schemas/` is
neither generated nor immutable-before-publication, and dropping it into the
tree would exempt it from the gate with nothing said.
`test_every_file_under_the_generated_tree_is_classified` closes that by asking
the renderer's own `RESOURCES` registry which folders it owns: anything under
`schemas/` outside a registered folder must be named in `_SCANNED_DESPITE_TREE`,
which is to say scanned, or the build fails.

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
  "the hole this PR closed" points at a moment the reader is not in. Where the
  pull request is the thing being processed at runtime the phrase is literal,
  and those files are pinned in `_EPHEMERAL_ALLOWED` rather than matched by a
  cleverer pattern — a CI workflow naming the source a job grades, and the
  pin-contract script's messages about the pull request under check. "this
  branch" is left wide: in this repo it always means a control-flow branch.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
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

# The files under that tree the rationale does not cover: hand-authored, so
# "generated from the contract models" is false of them, and authored HERE, so
# the gate reaches them in the pull request that writes them — which is the only
# moment a ref in one can still be removed. `openapi.json` carries no version
# triple and is served as a mutable pointer; `data-sync-run-response/1.0.0.json`
# is a pinned triple and immutable once published, which makes scanning it more
# useful rather than less: after publication its text cannot be corrected at
# all, so the gate has to see it before that.
#
# Graded, not just listed. `test_the_guard_excludes_only_the_paths_it_records`
# requires every entry to be tracked and to sit under `_UNSCANNED_TREE`, because
# a stale entry here fails OPEN and silently: it is textually indistinguishable
# from a correct one, and the file it meant to re-admit drops back under the
# blanket exclusion with nothing red.
_SCANNED_DESPITE_TREE = (
    "schemas/data-sync-api/openapi.json",
    "schemas/data-sync-run-response/1.0.0.json",
)

# Repo-relative posix paths the sweep does not reach. Every entry names a
# surface where a ticket number is either GitHub-native, immutable, or not ours
# to edit. `test_the_guard_excludes_only_the_paths_it_records` pins this tuple
# exactly, so nothing joins it without review.
_EXCLUDED_PATHS = (
    # A contributor-facing process document about the issue tracker itself: it
    # teaches the consolidation rule by walking real issues as worked precedent,
    # one of which it names as "the model to copy". The numbers are the referent
    # a reader is being sent to, not a pointer standing in for reasoning, and
    # GitHub renders and auto-links them. Removing them deletes the instruction.
    "CONTRIBUTING.md",
    # The form a pull request is written in, which makes it the GitHub-native
    # surface by definition — its own header tells the author that ticket
    # numbers belong in it rather than in code, and "this PR" is the runtime
    # subject there rather than a referent that expires. It passes the gate
    # today only because the placeholder is a literal `Closes #N` carrying no
    # digits, so the day anyone makes that example concrete the template teaching
    # the rule is the file the rule reddens.
    ".github/pull_request_template.md",
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
    # The prose half of the same rule, and circular for the same reason: it
    # teaches the shapes by showing them, so it names `issue #89`,
    # `analitiq-engine#406` and "this PR" in the course of forbidding them.
    # Its subject is the judgment this gate cannot mechanise, so the gate has
    # nothing to say about it either way.
    ".claude/rules/resolvable-referents.md",
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

# `.claude/skills/releasing/SKILL.md`, `.claude/settings.local.json`. Matched
# anywhere in a line, backticked or not, since citations appear both ways. The
# leading `.` is required: `claude/` alone is not that tree, and
# `plugins/analitiq-connector-builder/.claude-plugin/` is a different directory
# that IS tracked — hence `/` immediately after `claude`.
#
# A match is only a DEFECT if the path it names is untracked, which is checked
# against git rather than assumed. `.claude/` used to be ignored wholesale; now
# `rules/` is tracked and `skills/` is not, so "under `.claude/`" no longer
# answers the question the gate is actually asking. Resolving against the index
# keeps the two in step by construction — re-admitting another subtree changes
# what the gate accepts without anyone editing this file, and re-ignoring one
# turns its citations red the same day.
_FOREIGN_PATH = re.compile(r"\.claude/[A-Za-z0-9_./-]+")

# `.gitignore` is where the exclusion is DECLARED, so naming an unignored path
# there is the rule itself rather than a citation of it — the one place the
# resolvability check above gets the answer backwards. Exempt by PROVENANCE:
# the liveness check grades existence only, since a `.gitignore` that happens to
# name no untracked `.claude/` path is perfectly ordinary.
_FOREIGN_ALLOWED = (".gitignore",)

# The same defect one level up. `.claude/` is the tree this repo cites most, but
# nothing makes it special: ANY path `.gitignore` excludes is a path that exists
# on the machine that wrote the citation and in no clone. Asking git which
# tokens it ignores covers every such tree at once — `docs/`, `htmlcov/`,
# `dist/` — and keeps covering them when the ignore file changes, which a
# hardcoded prefix cannot.
#
# Any slash-joined token. `_looks_like_a_path` then narrows it, in Python
# rather than in the pattern, because the two conditions are easier to read
# apart than as one alternation — and an alternation gets this wrong quietly:
# `(?:/|/\w+\.\w+)` prefers its first branch, so `docs/sql-write-path-v2.md`
# matches as the bare `docs/` and the citation the gate is looking for never
# reaches the ignore check.
_PATHLIKE = re.compile(
    r"(?<![A-Za-z0-9_./-])[A-Za-z0-9_.-]+/(?:[A-Za-z0-9_.-]+/?)*"
)

# Every `.gitignore` DECLARES exclusions, so naming an ignored path in one is
# the rule rather than a citation of it — the same inversion `_FOREIGN_ALLOWED`
# records for the `.claude/` gate. Listed by path rather than matched by
# filename: a `.gitignore` added to a new package has to land in a diff here,
# which is the same reason every other exemption tuple is pinned literally.
_IGNORED_ALLOWED = (
    ".gitignore",
    "packages/contract-models/.gitignore",
    "packages/validator/.gitignore",
)

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

# The files where the PR IS the runtime subject rather than a dangling pointer:
# a CI comment about which source the job grades, and the messages the
# pin-contract script prints about the pull request it is checking. Pinned as
# paths for the same reason `_EXCLUDED_PATHS` is — an exemption a reviewer reads
# beats a pattern that quietly decides which mentions are legitimate.
_EPHEMERAL_ALLOWED = (
    ".github/workflows/tests.yml",
    "scripts/check_validator_pin_contract.py",
)


def _is_excluded(relpath: str) -> bool:
    """Exact-match only.

    Prefix and suffix matching both leak, which is why neither is used here: a
    `startswith` arm exempts `CONTRIBUTING.md.bak`, an `endswith` arm exempts
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
    # Bytes, decoded explicitly, rather than `text=True`. `text=True` decodes
    # with `locale.getpreferredencoding(False)` in strict mode, so a tracked path
    # carrying a non-ASCII byte raises `UnicodeDecodeError` under the `LC_ALL=C`
    # of a minimal CI container — and `UnicodeDecodeError` is a `ValueError`,
    # which this handler does not catch. The diagnostic below would be skipped
    # for a traceback naming neither this gate nor the path. Git stores paths as
    # bytes and this repo's are UTF-8, so decode them as UTF-8 whatever the
    # locale says, inside the `try` that explains the failure.
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        )
        listing = result.stdout.decode("utf-8")
    except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as exc:
        # git's own reason lives in stderr, and `capture_output` swallowed it.
        # Without re-emitting it, "not a git repository" and the `safe.directory`
        # dubious-ownership refusal — the realistic CI-container failure — are
        # indistinguishable, and the second one is nothing to do with checkouts.
        stderr = getattr(exc, "stderr", b"") or b""
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"could not enumerate tracked files under {REPO_ROOT} — this gate "
            "derives its scope from git, so it cannot run against a tree git "
            "does not know."
            + (f" git said: {detail}" if detail else "")
        ) from exc
    tracked = [line for line in listing.split("\0") if line]
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

    The causes are separated because they take different remedies and only one
    of them is `_EXCLUDED_PATHS`. A tracked path can also be unreadable because
    it is a submodule gitlink (`git ls-files` lists the directory), or because
    the worktree is missing a file the index still holds — an unstaged `rm`, a
    sparse checkout, `skip-worktree`. Those are structural or transient, and an
    author who follows a message saying "add it to _EXCLUDED_PATHS" exempts a
    real, readable, authored file permanently. For the submodule case
    `test_exclusions_are_all_live` then blesses the exemption forever, since
    `.exists()` is true of the directory: a whole-file exemption standing over a
    file nobody watches, which is the state that test exists to end.
    """
    try:
        return (REPO_ROOT / relpath).read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"{relpath} is tracked but is not UTF-8 text. This gate reads every "
            "file it scans, so if it is genuinely binary, add it to "
            "_EXCLUDED_PATHS with its reason rather than leaving it unscanned."
        ) from exc
    except IsADirectoryError as exc:
        raise RuntimeError(
            f"{relpath} is tracked as a directory, which means a submodule "
            "gitlink. This gate scans files; what is inside the submodule is "
            "its own repo's gate to run. Do NOT exempt it as a path here."
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{relpath} is in the index but absent from the worktree — a staged "
            "deletion, a sparse checkout, or `skip-worktree`. Fix the checkout; "
            "exempting it would hide a real authored file from the gate."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"{relpath} is tracked but could not be read: {exc}. This gate reads "
            "every file it scans and will not skip one silently."
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


def _resolves_in_tree(cited: str, tracked: set[str]) -> bool:
    """Does a cited path name something git tracks — a file OR a directory?

    Prose cites both shapes, in different files: `CLAUDE.md` names the tree
    `.claude/rules/` where it says the rules are tracked, and
    `.github/pull_request_template.md` names files inside it
    (`.claude/rules/no-drift-surfaces.md`). Git tracks files, so a
    set-membership test alone calls the directory citation unresolvable and
    reddens an ordinary reference to a tree that is right there in the clone.

    Trailing punctuation is stripped first because a citation usually ends a
    sentence or sits inside parentheses, and `_FOREIGN_PATH`'s charset carries
    `.` and `/` so it swallows them.
    """
    cited = cited.rstrip("/.,;:)`\"'")
    return cited in tracked or any(path.startswith(cited + "/") for path in tracked)


def _foreign_path_sites() -> list[tuple[str, int, str]]:
    """Citations of a `.claude/` path git does not track.

    A named collector rather than a bare `_sites(...)` call inside the gate, so
    the exemption the gate passes is itself under test. Spelled only at the call
    site, `skip=_FOREIGN_ALLOWED` can be widened to every scanned file and
    nothing notices: the acceptance cases exercise the raw pattern, and a test
    calling `_sites` directly grades the traversal but never the argument the
    GATE hands it. Both tests below go through this function for that reason.

    The tracked-path filter is what makes this a resolvability check instead of
    a directory ban. `.claude/rules/` is tracked and `.claude/skills/` is not,
    so the same prefix now covers citations that resolve and citations that do
    not, and only git can tell them apart.
    """
    tracked = set(_tracked_files())
    return [
        site for site in _sites(_FOREIGN_PATH, skip=_FOREIGN_ALLOWED)
        if not _resolves_in_tree(site[2], tracked)
    ]


def _ephemeral_referent_sites() -> list[tuple[str, int, str]]:
    """The expiring-referent gate's findings. Named for the same reason."""
    return _sites(_EPHEMERAL_REFERENT, skip=_EPHEMERAL_ALLOWED)


def _trim(cited: str) -> str:
    """A cited path without the prose punctuation that ends the sentence."""
    return cited.rstrip("/.,;:)`\"'")


def _looks_like_a_path(cited: str) -> bool:
    """Is this token a path, or just prose that happens to contain a slash?

    A directory citation ends in `/`; a file citation's last segment carries an
    extension. Everything else is prose — "Build/check", "read/write", "and/or"
    — and prose gets matched by the ignore check for reasons that have nothing
    to do with citations: on a case-insensitive filesystem "Build/check" is
    ignored by a `build/` rule.
    """
    if cited.endswith("/"):
        return True
    return "." in cited.rsplit("/", 1)[-1]


def _git_ignores(paths: set[str]) -> set[str]:
    """Which of these repo-relative paths `.gitignore` excludes.

    One batched `git check-ignore` rather than one call per candidate. Paths
    that escape the repo are dropped first: a single `../../LICENSE` makes git
    abort the whole batch with "is outside repository", which would silently
    return no ignored paths at all and turn the gate off.
    """
    # `p` must be non-empty as well: stripping trailing punctuation can consume
    # a whole token (`./`), and git rejects an empty pathspec by aborting the
    # batch, which would look exactly like "nothing is ignored".
    inside = sorted(
        p for p in paths if p and ".." not in p and not p.startswith("/")
    )
    if not inside:
        return set()
    # `check=False` deliberately: `git check-ignore` uses its exit code to
    # ANSWER, not only to report failure — 1 means "none of these are ignored",
    # which is the ordinary green case. `check=True` would raise on it. The
    # codes are discriminated below instead, which is the part that matters.
    result = subprocess.run(
        ["git", "check-ignore", "--stdin", "-z"],
        cwd=REPO_ROOT,
        input=("\0".join(inside) + "\0").encode("utf-8"),
        capture_output=True,
        check=False,
    )
    # Any code other than 0 or 1 is git failing, and this gate does not report
    # clean on a question it could not ask.
    if result.returncode not in (0, 1):
        raise RuntimeError(
            "git check-ignore failed, so citations of ignored paths cannot be "
            "identified and this gate would pass vacuously. git said: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return {p for p in result.stdout.decode("utf-8").split("\0") if p}


def _ignored_path_sites() -> list[tuple[str, int, str]]:
    """Citations of any path this repo's `.gitignore` excludes.

    `_foreign_path_sites` asks whether a `.claude/` citation is tracked; this
    asks the general form of the same question, and the two are not redundant.
    A mistyped rule filename (`.claude/rules/no-such-rule.md`) is untracked but
    NOT ignored, since the tree is re-admitted — only the first gate sees it.
    An ignored path outside `.claude/` is not matched by the first pattern at
    all — only this one sees it.
    """
    sites = [
        site for site in _sites(_PATHLIKE, skip=_IGNORED_ALLOWED)
        if _looks_like_a_path(site[2])
    ]
    ignored = _git_ignores({_trim(cited) for _, _, cited in sites})
    return [site for site in sites if _trim(site[2]) in ignored]


def test_no_citation_of_a_path_the_ignore_file_excludes() -> None:
    """An ignored path is present for its author and absent from every clone."""
    found = _ignored_path_sites()
    assert not found, (
        "citations of paths `.gitignore` excludes:\n"
        + "\n".join(f"  {rel}:{lineno} -> {cited}" for rel, lineno, cited in found)
        + "\nThe reader's clone does not contain them. Name the artifact and the "
        "repo that owns it, or state the fact the path was standing in for."
    )


def test_no_citation_of_a_path_the_reader_cannot_have() -> None:
    """An untracked `.claude/` path is absent from every clone."""
    found = _foreign_path_sites()
    assert not found, (
        "citations of `.claude/` paths git does not track:\n"
        + "\n".join(f"  {rel}:{lineno} -> {cited}" for rel, lineno, cited in found)
        + "\nThe file exists only on the machine that wrote the citation. State "
        "the rule itself, or name a skill by its skill name. (`.claude/rules/` "
        "IS tracked — citing a rule there resolves and is not flagged.)"
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

    assert _IGNORED_ALLOWED == (
        ".gitignore",
        "packages/contract-models/.gitignore",
        "packages/validator/.gitignore",
    ), (
        "_IGNORED_ALLOWED exempts whole files from the ignored-path gate; state "
        "the reason inline and update this pin."
    )
    # Exempt by PROVENANCE like `_FOREIGN_ALLOWED`, and for the same inversion:
    # an ignore file names ignored paths because that is what an ignore file is.
    # Existence is the whole check — but every entry must actually BE an ignore
    # file, or the exemption is just a file removed from the gate under a name
    # that reads like a rule.
    for relpath in _IGNORED_ALLOWED:
        assert (REPO_ROOT / relpath).exists(), (
            f"{relpath} is exempt but names nothing in the tree — drop it."
        )
        assert Path(relpath).name == ".gitignore", (
            f"{relpath} is exempt from the ignored-path gate as a declaration "
            "of the exclusions, and it is not a `.gitignore`. That exemption "
            "removes a file from the gate for a reason that is not true of it."
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
    draft leaned on. A floor is a magnitude claim standing in for an extent
    claim: any number small enough to be safe as the repo shrinks is small enough
    to let a large part of the scan disappear under it, and any number large
    enough to bite reddens on its own as the repo grows.
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
    # and everything but the `_SCANNED_DESPITE_TREE` re-admissions falls out.
    # `_tracked_files`'s refusal cannot help — this test asks git itself, by
    # design — so require the graded set to be non-empty here.
    assert expected, (
        f"the scope predicate kept nothing out of {len(tracked)} tracked files, "
        "so the comparison below holds vacuously. Either the `schemas/` literal "
        "in this test is wrong or every file has been exempted."
    )
    # Equality, not containment. `missing` alone (`expected - scanned`) is
    # one-directional: it catches a scan that shrank and says nothing about one
    # that reached files the predicate excludes, so `schemas/` re-entering the
    # scan wholesale would pass. Equality also makes the two independent
    # listings do their own work — no separate partition arithmetic, computed by
    # this test out of this test's own literal, can be satisfied by construction.
    scanned = set(_scanned_files())
    assert scanned == expected, (
        f"the scan and the scope disagree. Selected and never scanned: "
        f"{sorted(expected - scanned)[:10]}; scanned without being in scope: "
        f"{sorted(scanned - expected)[:10]}. The first blinds the gate to those "
        "files — scan them, or record the exemption in _EXCLUDED_PATHS with its "
        "reason. The second means the generated tree is being scanned after all."
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
    # which is not the one the gate hands over: appending `"README.md"` and
    # `"CLAUDE.md"` to the `skip` inside the collector drops two real files from
    # a gate with every constant untouched and the whole suite green. The
    # end-to-end fixture cannot see it either — its tmp repo holds no file by
    # those names. Comparing the collector's real read set against the pinned
    # tuple is what makes a narrowing land in a diff, the same way the tuples
    # themselves are pinned.
    for collector, allowed, name in (
        (_foreign_path_sites, _FOREIGN_ALLOWED, "gitignored-path"),
        (_ephemeral_referent_sites, _EPHEMERAL_ALLOWED, "expiring-referent"),
        (_ignored_path_sites, _IGNORED_ALLOWED, "ignored-path"),
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
        ... if relpath.endswith(".md")              # scan one file type
        ... if not line.lstrip().startswith("#")    # skip comments and headings
        ... if not line.startswith("    ")          # skip indented lines

    All seven leave the gate asserting an empty list is empty over the whole
    repo. `test_the_gate_reads_every_file_the_scan_selected` cannot catch any of
    them and never could: its recording `_read` returns `""`, so it proves the
    call happened with the right argument and can say nothing about what came
    back. Two tests, two different questions.

    The fixture is shaped to make each one fail, and it needs TWO axes to do it.
    Magnitude is the obvious one: 299 filler lines put the refs past any
    plausible head-slice, the 400-character line defeats a length filter, and
    asserting the LINE NUMBERS pins the enumerator, which every single-line
    acceptance fixture leaves free to be a constant `1` — and when this gate is
    red, `rel:lineno -> matched` is its entire product, so a constant sends
    every author to line 1 of a 400-line file.

    Identity is the axis that was missing, and its absence is why the last three
    severances above survived a fully green suite. Two `.md` documents of the
    same size, both at the repo root, both carrying every ref on a flush-left
    prose line, vary only in magnitude: any predicate over the PATH, the
    extension, the file's size, or the shape of the LINE reads the whole scan,
    passes the read-set check, matches both fixture files, and drops the rest of
    the repo. So `pkg/mod.py` is deliberately unlike them in every one of those
    dimensions — nested rather than root, `.py` rather than `.md`, a few hundred
    bytes rather than 14 KB, and its refs sit on an indented line and on a
    comment line, which is where refs actually live in this repo. The two
    filters that blind the gate to every Python comment and every contract-model
    docstring are the ones that matter most, because those descriptions render
    into the published schemas.

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
    # The identity axis: a third document unlike the other two in every
    # dimension a post-read filter can test — nested path, `.py` suffix, small,
    # and carrying each class of referent once on an INDENTED line and once on a
    # COMMENT line. `pkg/` also gives `relpath` a directory component, which the
    # two root-level documents leave untestable.
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(
        "def helper():\n"
        + "    return 1\n" * 40
        + "    settled in issue #89 upstream\n"
        + "# see analitiq-engine#406 for the grammar\n"
        + "    per .claude/skills/releasing/SKILL.md this holds\n"
        + "# the wiring this PR extended is routed\n"
        + "    stated in build/notes.md instead\n",
        encoding="utf-8",
    )
    # The ignored-path gate needs an ignore file to have an opinion at all, and
    # `build/` is ignored here so `build/notes.md` above is a citation of a path
    # no clone of this fixture repo contains.
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "notes.md", "guide.md", "pkg/mod.py", ".gitignore"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)

    # Through the GATES' own collectors, not `_sites` directly: that is what
    # puts the exemption each one passes under test. Neither `_FOREIGN_ALLOWED`
    # nor `_EPHEMERAL_ALLOWED` names either document, so both must report both.
    assert _foreign_path_sites() == [
        ("guide.md", 302, ".claude/rules/no-drift-surfaces.md"),
        ("pkg/mod.py", 44, ".claude/skills/releasing/SKILL.md"),
    ]
    assert _ephemeral_referent_sites() == [
        ("guide.md", 303, "this PR"),
        ("pkg/mod.py", 45, "this PR"),
    ]
    # And `skip` removes exactly the named file, not the pattern's ability to
    # match: the exempt document goes quiet and the other one still reports.
    assert _sites(_EPHEMERAL_REFERENT, skip=("guide.md",)) == [
        ("pkg/mod.py", 45, "this PR"),
    ]
    # The ignored-path gate through its own collector: the ignore file itself is
    # exempt, the citation on the indented line is not.
    assert _ignored_path_sites() == [("pkg/mod.py", 46, "build/notes.md")]

    assert _references() == [
        ("notes.md", 302, "analitiq-engine#406"),
        ("notes.md", 303, "issue #89"),
        ("pkg/mod.py", 42, "issue #89"),
        ("pkg/mod.py", 43, "analitiq-engine#406"),
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
    assert _SCANNED_DESPITE_TREE == (
        "schemas/data-sync-api/openapi.json",
        "schemas/data-sync-run-response/1.0.0.json",
    ), (
        "_SCANNED_DESPITE_TREE re-admits hand-authored files from the generated "
        "tree; each addition needs the reason stated inline."
    )
    # The pin proves the STRING is unchanged. It cannot prove the string still
    # RESOLVES, and this is the one tuple where staleness fails open: an
    # `_EXCLUDED_PATHS` entry naming nothing is an exemption over nothing, while
    # a `_SCANNED_DESPITE_TREE` entry naming nothing silently returns a real
    # authored file to the blanket `schemas/` exclusion. A typo here is
    # textually indistinguishable from the correct value, and the extent test
    # cannot help because it reads this same tuple — the one input it does not
    # keep an independent copy of, so both sides shrink together.
    tracked = set(_tracked_files())
    for readmitted in _SCANNED_DESPITE_TREE:
        assert readmitted.startswith(_UNSCANNED_TREE), (
            f"{readmitted} is not under `{_UNSCANNED_TREE}`, so re-admitting it "
            "is a no-op — it was never excluded in the first place."
        )
        assert readmitted in tracked, (
            f"{readmitted} re-admits a file from the generated tree and names "
            "nothing git tracks. Whatever file it meant is silently unscanned."
        )
    assert _EXCLUDED_PATHS == (
        "CONTRIBUTING.md",
        ".github/pull_request_template.md",
        "plugins/analitiq-connector-builder/CHANGELOG.md",
        "plugins/analitiq-pipeline-builder/CHANGELOG.md",
        "packages/contract-models/src/analitiq/contracts/arrow_type_grammar.json",
        "tests/hygiene/test_ticket_references.py",
        ".claude/rules/resolvable-referents.md",
    ), (
        "_EXCLUDED_PATHS changed. Each entry exempts a whole file from the gate, "
        "so state the reason inline and update this pin in the same diff."
    )


def test_every_file_under_the_generated_tree_is_classified() -> None:
    """The blanket `schemas/` exclusion must only cover what the renderer writes.

    The tree is excluded for a reason that is a claim about provenance —
    generated from the contract models, published immutably — and the exclusion
    is applied by prefix, which does not check that claim. A hand-authored file
    dropped anywhere under `schemas/` is exempt from the gate the moment it
    lands, with nothing said and nothing red. Two such files already exist, so
    this is not hypothetical; a third arriving is the ordinary case.

    `render_schemas.py check` does not cover it either. It re-renders the
    registered resources and diffs them, so an extra file it never renders is
    not a difference it can see.

    Ask the renderer which folders it owns, rather than restating the answer:
    `RESOURCES` is the registry the rendering is driven from, so a resource
    added there is classified here the same day. Everything else under the tree
    must be named in `_SCANNED_DESPITE_TREE` — that is, must be scanned. The
    only sanctioned way to have a hand-authored file under `schemas/` is to let
    the gate read it.
    """
    renderer = REPO_ROOT / "scripts" / "render_schemas.py"
    spec = importlib.util.spec_from_file_location("_render_schemas", renderer)
    assert spec and spec.loader, f"could not load the renderer at {renderer}"
    render_schemas = importlib.util.module_from_spec(spec)
    # Registered before execution, not after: the module defines dataclasses,
    # and `dataclasses` resolves a field's annotations through
    # `sys.modules[cls.__module__]`. Executing it unregistered raises inside
    # `dataclass()` on a module that imports perfectly well by name.
    sys.modules[spec.name] = render_schemas
    try:
        spec.loader.exec_module(render_schemas)
    finally:
        del sys.modules[spec.name]
    generated_folders = tuple(
        f"{_UNSCANNED_TREE}{resource.name}/" for resource in render_schemas.RESOURCES
    )
    assert generated_folders, (
        "the renderer's RESOURCES registry is empty, so every file under "
        f"`{_UNSCANNED_TREE}` would read as hand-authored and this check would "
        "grade the wrong thing."
    )
    # `canonical-types.json` is generated too, from the vendored engine grammar
    # rather than from a registered resource, so it has no folder of its own.
    generated_files = (f"{_UNSCANNED_TREE}canonical-types.json",)

    unclassified = sorted(
        relpath
        for relpath in _tracked_files()
        if relpath.startswith(_UNSCANNED_TREE)
        and not relpath.startswith(generated_folders)
        and relpath not in generated_files
        and relpath not in _SCANNED_DESPITE_TREE
    )
    assert not unclassified, (
        f"{unclassified} sit under `{_UNSCANNED_TREE}` but no registered "
        "resource renders them, so the tree's exclusion — generated, immutable "
        "once published — is not true of them. They are hand-authored and "
        "currently unscanned. Add each to _SCANNED_DESPITE_TREE with its reason "
        "so the gate reads it, or register the resource that renders it."
    )


def test_a_cited_path_resolves_as_file_or_directory() -> None:
    """Both citation shapes, and the untracked case that must still report.

    The directory form is not hypothetical: `CLAUDE.md` cites the tree
    `.claude/rules/`, while `.github/pull_request_template.md` cites files
    inside it. A file-only membership test passes the second and reddens the
    first, on a tree the reader has in their clone.
    """
    tracked = {".claude/rules/no-drift-surfaces.md", "CLAUDE.md"}
    assert _resolves_in_tree(".claude/rules/no-drift-surfaces.md", tracked)
    assert _resolves_in_tree(".claude/rules/", tracked)
    assert _resolves_in_tree(".claude/rules", tracked)
    # Trailing punctuation from surrounding prose must not defeat resolution.
    assert _resolves_in_tree(".claude/rules/no-drift-surfaces.md.", tracked)
    assert _resolves_in_tree(".claude/rules/no-drift-surfaces.md`", tracked)
    # Untracked: the whole point of the gate.
    assert not _resolves_in_tree(".claude/skills/releasing/SKILL.md", tracked)
    assert not _resolves_in_tree(".claude/settings.local.json", tracked)
    # A prefix that is not a path BOUNDARY does not resolve — `.claude/rul` is
    # not a directory just because `.claude/rules/…` starts with it.
    assert not _resolves_in_tree(".claude/rul", tracked)


def test_a_slashed_phrase_is_not_a_path() -> None:
    """The ignored-path gate must not fire on prose that contains a slash.

    This is not a hypothetical tidy-up. On a case-insensitive filesystem git
    reports `Build/check` — an ordinary phrase in a build script's comment — as
    ignored by a `build/` rule, so without this narrowing the gate reports a
    citation that was never a path and the fix is to reword English prose.
    """
    assert _looks_like_a_path("docs/sql-write-path-v2.md")
    assert _looks_like_a_path(".claude/rules/")
    assert _looks_like_a_path("htmlcov/")
    assert not _looks_like_a_path("Build/check")
    assert not _looks_like_a_path("read/write")
    assert not _looks_like_a_path("and/or")
    # A directory whose name carries a dot is still a directory, and a file with
    # no extension in a cited directory is still prose as far as this can tell.
    assert _looks_like_a_path("packages/contract-models/")
    assert not _looks_like_a_path("packages/Makefile")


def test_an_unanswerable_ignore_question_is_never_a_clean_answer(
    tmp_path, monkeypatch
) -> None:
    """`_git_ignores` must raise rather than report nothing ignored.

    Its return value is a filter: an empty set means "no citation is of an
    ignored path", which is exactly what a green gate looks like. So every way
    the question fails to get asked has to be loud. Pointing it at a directory
    git does not manage is the reachable one — and a single unanswerable path
    aborts the whole batch, which is why `..` paths are dropped before the call
    rather than left to git.
    """
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="check-ignore failed"):
        _git_ignores({"docs/whatever.md"})

    # The dropped-path branch: no answerable candidate means no call at all, and
    # an empty answer is correct there rather than a swallowed failure.
    assert _git_ignores({"../../LICENSE", "/etc/hosts"}) == set()


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
    - This file and `.claude/rules/resolvable-referents.md` are exempt for their
      CONTENT too, and for the same reason as each other: both quote the shapes
      they forbid, so scanning them is circular. Move the acceptance fixtures to
      a sibling module, or rewrite the rule to describe the shapes instead of
      showing them, and each exemption outlives its reason silently. Graded the
      same way `CONTRIBUTING.md` is.
    - The changelogs, the vendored manifest and the pull-request template are
      exempt by PROVENANCE: a bot writes the first, an upstream repo the second,
      and the third is a form whose whole purpose is to hold what the gate bans.
      Asserting the changelogs *contain* a ref would fail on a perfectly
      ordinary release — release-please only carries `#N` when the squash
      subject did, and the merge-commit release the package procedure requires
      yields entries with a commit link and no number. The template is worse
      still: it passes the gate today precisely because its placeholder has no
      digits. Worse, the remedy that assertion implies (drop the exemption)
      would put the gate back in front of a file nobody is allowed to fix.
      Existence is the whole check for those.
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
    for circular in (
        "tests/hygiene/test_ticket_references.py",
        ".claude/rules/resolvable-referents.md",
    ):
        assert scan_text(_read(circular)), (
            f"{circular} is exempt because it quotes the shapes it rejects, and "
            "it no longer quotes any. The exemption is now a whole-file blind "
            "spot with no reason behind it — drop it."
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
