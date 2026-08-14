"""Every file and section a plugin's prose points at must exist.

Agent prose routes an agent to its rules by filename — and often deeper, by
section: `` `spec-driver-selection.md` §Constraints ``. A rename, a move, or a
deleted file leaves the reference dangling — and an agent that cannot read its
spec does not fail loudly, it authors without the rules. A renamed heading rots
the same way with less noise: the file still opens, and the section the
citation promises is simply not in it. Nothing else in the suite notices:
these are strings in markdown.

It is one instance of a recurring shape — prose describing something that no
longer exists, with nothing checking — narrowed to the part this repo owns. It
cannot pin the CDK's hook surface — that lives in the engine — but it can
guarantee that a spec a plugin points at is a spec that exists, and that a
section it names is a section that file carries.

Every plugin root under `plugins/` is scanned. The reference forms are one
prose convention shared across the plugin trees, so the guard discovers roots
rather than naming them, and a new plugin is graded from its first citation.

The file-reference forms, each checked in every plugin:

- `${CLAUDE_PLUGIN_ROOT}/skills/…/spec-x.md` — the absolute form agents'
  required-reading lists use. Reaches every shipped file, scripts
  included.
- `` `spec-x.md` `` / `` `references/io-contracts.md` `` /
  `` `../stream-spec/spec-destinations.md` `` / `` `scripts/validate.py` `` —
  the backticked form used for cross-references between sibling specs and for
  the helper scripts an agent is told to run, with or without leading `../`
  segments. This is the dominant form by an
  order of magnitude, and it is the form a stale reference was actually found
  in, so leaving it unchecked would miss the very case that motivated this
  file.
- Unbackticked bare paths with a directory segment, on every line — the
  `description:` citations an orchestrator reads to route work (frontmatter
  prose does not use backticks, which is exactly where a dangling citation to
  a since-deleted spec hid) and the same unbackticked form in body prose,
  where several specs cite their siblings without backticks.

The `.md` and `.py` extensions bound the reach: those are the file kinds
prose routes an agent to (a spec to read, a helper to run). The `_BARE_REF`
comment states the `.py` directory-segment boundary, and `_cleaned` owns
the `../` handling.
Only prose is scanned — `_prose_text` blanks fenced code blocks and HTML
comments — and the release-please CHANGELOG is not (`_docs`).

The section-anchor pass: a `§` directly after a file reference carries a
second claim — that the named heading exists in the cited file. Each such
anchor is resolved against the target's markdown headings (`#` through
`######`, outside code fences). Only citation-adjacent anchors are in scope;
`_anchor_claims` states the boundary.

Pure text-vs-filesystem: no contract packages involved, so no `_pins` skip
guard — this always runs.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS_ROOT = REPO_ROOT / "plugins"
PLUGIN_ROOTS = sorted(p for p in PLUGINS_ROOT.iterdir() if p.is_dir())

# The synthetic acceptance tests below need one real tree to heal citations
# against; the connector plugin carries the paths they use.
_CONNECTOR_ROOT = PLUGINS_ROOT / "analitiq-connector-builder"

# `${CLAUDE_PLUGIN_ROOT}/skills/foo/bar.md` — the path segment only. `.` is in
# the charset (paths need it), so a reference ending a sentence captures the
# full stop; `_cleaned` strips trailing `.` and `/` rather than trying to
# express "not at the end" in the charset.
_PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")

# A backticked markdown or script filename: `spec-tls.md`,
# `references/io-contracts.md`, `../stream-spec/spec-x.md`,
# `scripts/validate.py`. Leading `../` segments are allowed and stripped by
# `_cleaned`, so a relative cross-skill reference resolves by the same suffix
# rule. A `.md` name may stand bare; a `.py` name needs a directory segment —
# a bare Python filename (`connector.py`, `__init__.py`) names a file the
# *authored connector* ships, not a document in a plugin, and a backticked
# JSON filename (`connector.json`) is the same artifact-layout class, so
# `.json` stays out entirely.
_BARE_REF = re.compile(
    r"`((?:\.\./)*"
    r"(?:(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.md"
    r"|(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+\.py))`"
)

# An unbackticked `.md` or `.py` path, matched on every prose line. At least
# one directory segment is required: a bare filename with no slash is
# indistinguishable from an ordinary prose word. That requirement is what
# keeps prose from matching — every match it yields is a genuine citation —
# and for `.py` it is the same boundary `_BARE_REF`'s comment states. The
# first lookbehind rejects starts
# preceded by a backtick (that is `_BARE_REF`'s form) or by a path
# character, so the tail of a `${CLAUDE_PLUGIN_ROOT}/…` reference is not
# re-matched and a `./`- or `../`-prefixed relative link stays out of this
# form's scope. The trailing `(?!\]\()` skips markdown link text: the link's
# URL half beside it is the resolvable form, and the text half is display
# only — a README labels a link with a repo-root path the plugin does not
# ship.
_BARE_PATH_REF = re.compile(
    r"(?<![\w`./-])((?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+\.(?:md|py))(?![\w-])(?!\]\()"
)

# Every reference a plugin's prose writes names a file that plugin ships, so
# resolution is the whole predicate — there is no allow-list of deliberately
# external targets. There was one, holding the specs the storage stub said would
# exist "when engine support arrives"; naming files no clone contains is the
# defect `.claude/rules/resolvable-referents.md` forbids, and the prose that did
# it is gone. Should a citation ever have to point outside its plugin, this
# guard fails and the exemption is reintroduced with a member and a reason —
# which is strictly better than keeping an empty one waiting.


def _plugin_paths(root: Path) -> list[str]:
    """Every file and directory in the plugin, as plugin-root-relative posix
    paths. Every file, not just `.md`: the `${CLAUDE_PLUGIN_ROOT}` form also
    names the helper scripts an agent is told to run, and a deleted script
    dangles exactly like a deleted spec."""
    return [p.relative_to(root).as_posix() for p in sorted(root.rglob("*"))]


def _cleaned(target: str, root: Path) -> str:
    """The plugin-relative suffix a written reference names."""
    cleaned = target.rstrip("/")
    while cleaned.startswith("../"):
        cleaned = cleaned[3:]
    cleaned = cleaned.removeprefix(root.relative_to(REPO_ROOT).as_posix() + "/")
    # `.md.` — a reference that ended a sentence. Strip only a trailing dot,
    # never one inside the name.
    return cleaned[:-1] if cleaned.endswith(".") else cleaned


def _names(path: str, cleaned: str) -> bool:
    """Does a plugin-relative `path` answer to a written suffix `cleaned`?
    The `/` boundary is load-bearing: `driver-selection.md` names a
    different file than `spec-driver-selection.md`, and a bare `endswith`
    would let the shorter spelling resolve through the longer file."""
    return path == cleaned or path.endswith("/" + cleaned)


def _resolves(target: str, paths: list[str], root: Path) -> bool:
    """Does `target` name something that exists?

    The prose writes references at whatever depth reads well from where it
    sits — `spec-tls.md`, `connector-spec-db/spec-type-maps.md`,
    `skills/connector-builder/references/io-contracts.md`, and directory
    references like `skills/connector-spec-db/examples/` all appear, and
    every one is unambiguous to a reader; a repo-root-relative
    `plugins/<name>/…` spelling, where prose uses one, resolves by the same
    rule. So resolve the way a reader does: a reference
    resolves if it is a path suffix of something in the plugin. That
    deliberately does not check the reference was written from the right
    directory — only that the thing it names exists, which is the failure that
    silently starves an agent of its rules.
    """
    cleaned = _cleaned(target, root)
    return any(_names(path, cleaned) for path in paths)


_FENCE = re.compile(r"^[ \t]*(```|~~~)")


def _prose_text(text: str) -> str:
    """The document with its non-prose spans blanked, line count preserved.

    Two spans are not prose, so nothing in them is a citation. A fenced code
    block is sample input or output — a path inside one is a command to copy
    (a README shows a repo-root `python3 plugins/…` invocation no plugin
    cache contains), not a claim that the path ships in the plugin. An HTML
    comment is the tooling-metadata channel — GENERATED headers name the
    repo-root renderer that wrote the file, PROBE fences name probe ids,
    maintainer notes name the repo-side pin — and no agent is routed by one.
    Blanking rather than deleting keeps line numbers stable for the reports.
    """
    out: list[str] = []
    fence: str | None = None
    in_comment = False
    for line in text.splitlines():
        if not in_comment:
            marker = _FENCE.match(line)
            if marker:
                if fence is None:
                    fence = marker.group(1)
                elif line.lstrip().startswith(fence):
                    fence = None
                out.append("")
                continue
            if fence is not None:
                out.append("")
                continue
        kept: list[str] = []
        rest = line
        while rest:
            if in_comment:
                _, closer, rest = rest.partition("-->")
                in_comment = not closer
            else:
                before, opener, rest = rest.partition("<!--")
                kept.append(before)
                in_comment = bool(opener)
        out.append("".join(kept))
    return "\n".join(out)


def _scan_text(text: str) -> list[tuple[int, str]]:
    """Every (lineno, target) doc reference in one document's prose — every
    citation pattern, on every line outside fences and comments."""
    return [
        (lineno, match.group(1))
        for lineno, line in enumerate(_prose_text(text).splitlines(), 1)
        for pattern in (_PLUGIN_ROOT_REF, _BARE_REF, _BARE_PATH_REF)
        for match in pattern.finditer(line)
    ]


def _docs(root: Path) -> list[Path]:
    """The markdown one plugin's sweep reads: every tracked doc except the
    release-please CHANGELOG. Its entries describe the tree as it was at each
    release, so a path that has since moved is correct history there while
    reading as rot — and its surface is the tracker's, not a route an agent
    follows."""
    return [p for p in sorted(root.rglob("*.md")) if p.name != "CHANGELOG.md"]


def _references(root: Path) -> list[tuple[str, int, str]]:
    """Every (relpath, lineno, target) doc reference in one plugin."""
    return [
        (path.relative_to(root).as_posix(), lineno, target)
        for path in _docs(root)
        for lineno, target in _scan_text(path.read_text(encoding="utf-8"))
    ]


def _is_dangling(target: str, paths: list[str], root: Path) -> bool:
    """The one resolution predicate: a reference dangles unless it names a file
    that exists. Both the real-tree sweep and the synthetic acceptance tests go
    through this, so the acceptance tests exercise the predicate that ships."""
    return not _resolves(target, paths, root)


@pytest.mark.parametrize("root", PLUGIN_ROOTS, ids=lambda r: r.name)
def test_doc_references_resolve(root: Path) -> None:
    """A dangling reference means an agent silently reads nothing."""
    paths = _plugin_paths(root)
    dangling = [
        (rel, lineno, target)
        for rel, lineno, target in _references(root)
        if _is_dangling(target, paths, root)
    ]
    prefix = root.relative_to(REPO_ROOT).as_posix()
    assert not dangling, (
        "agent prose points at files that do not exist:\n"
        + "\n".join(
            f"  {prefix}/{rel}:{lineno} -> {target}"
            for rel, lineno, target in dangling
        )
        + "\nFix the path, restore the file the agent is told to read, or state "
        "the fact the citation was carrying instead of pointing at it."
    )


# ---------------------------------------------------------------------------
# Section anchors: `path.md` §Heading claims the heading exists in that file.

# The gap between a citation and its anchor: horizontal whitespace, at most
# one newline (citations wrap mid-sentence), and optionally one opening
# parenthesis when the parenthetical starts at the anchor itself.
_GAP = r"`?[ \t]*\n?[ \t]*\(?§"

# One anchored variant per citation form, keyed for the per-variant floor.
# The optional backtick in `_GAP` exists for the `${CLAUDE_PLUGIN_ROOT}`
# form, which usually sits inside backticks its own pattern does not
# consume; after the other forms it matches nothing.
_ANCHORED = {
    "${CLAUDE_PLUGIN_ROOT}": re.compile(_PLUGIN_ROOT_REF.pattern + _GAP),
    "backticked": re.compile(_BARE_REF.pattern + _GAP),
    "unbackticked bare-path": re.compile(_BARE_PATH_REF.pattern + _GAP),
}

_HEADING = re.compile(r"^#{1,6}\s+(?P<title>\S.*?)\s*$")

# Where an undelimited anchor's own text stops being the heading and starts
# being the sentence around it: sentence punctuation, a parenthesis, an em
# dash, a following `§`. A hyphen is deliberately absent — it appears inside
# heading tokens ("Fix-and-revalidate") — and so is `|`, because a table
# cell's closer is absorbed by the prefix rule below.
_TERMINATOR = re.compile(r"[.,;:()§—]")


def _normalize(s: str) -> str:
    """One text shape for anchors and headings, so markup never decides a
    match: backticks and double quotes stripped (headings quote identifiers —
    `` ## `native_type` `` — while citations freely re-quote or drop that
    markup), whitespace runs collapsed (quoted anchors wrap across lines)."""
    return " ".join(s.replace("`", "").replace('"', "").split())


def _headings(text: str) -> list[str]:
    """Normalized heading titles, `#` through `######`, read over the same
    prose mask as the citation scan — a `#` inside a fence or an HTML
    comment is sample output or tooling metadata, not a section a reader
    sees. One mask, so the two passes can never disagree about what is
    prose."""
    return [
        _normalize(match.group("title"))
        for line in _prose_text(text).splitlines()
        if (match := _HEADING.match(line))
    ]


def _boundary_prefix(short: str, long: str) -> bool:
    """`short` is `long` cut at a token boundary (or all of it)."""
    if not long.startswith(short):
        return False
    rest = long[len(short):]
    return not rest or not (rest[0].isalnum() or rest[0] == "_")


def _anchor_matches(anchor: str, heading: str) -> bool:
    """The matching convention, in one place. After `_normalize`, the anchor
    and the heading must be token-boundary prefixes of one another, in either
    direction: prose truncates long headings (`§Constraints` cites
    "Constraints from the engine contract") and runs on past fully cited ones
    ("§Modes. Each phase …" leaves ". Each phase …" in an undelimited
    anchor's tail). The boundary requirement is what keeps `§Mode` from
    matching a heading "Modes". A citation of a numbered section (`§7`)
    matches a heading whose leading number is the same. This is deliberately
    a prefix claim, not an identity claim: where an undelimited anchor ends
    is a fact about what the English means, which a guard may not decide
    (`.claude/rules/guards.md`), so the guard proves "a heading this citation
    is a truncation of exists" and leaves "it is the heading the author
    meant" to the reader."""
    if _boundary_prefix(anchor, heading) or _boundary_prefix(heading, anchor):
        return True
    anchor_num = re.match(r"\d+", anchor)
    heading_num = re.match(r"\d+", heading)
    return (
        anchor_num is not None
        and heading_num is not None
        and anchor_num.group() == heading_num.group()
    )


def _anchor_claims(text: str) -> list[tuple[int, str, str, str]]:
    """Every citation-adjacent (lineno, target, anchor, form) § claim in one
    document's text.

    Citation-adjacent only: the § must follow a file reference across nothing
    but `_GAP`. A § bound anaphorically — "see §Failing closed" (the
    containing document), "that file's §…", or the trailing half of a chain
    like "§File shape, and its §API coverage" — names its target only through
    what the sentence means, which a guard may not decide
    (`.claude/rules/guards.md`), so those stay out of scope: uncovered, not
    unresolvable. So does an anchor after a citation form the file pass does
    not match (an unbackticked `../` link): an anchor binds only to a
    citation the guard resolves.

    The anchor is read from the tail after `§`, bounded to the rest of the
    line plus one more (quoted anchors wrap once):

    - opening `"`: the anchor is everything to the closing quote — delimited,
      compared whole;
    - opening backtick: likewise, to the closing backtick;
    - otherwise the anchor is undelimited — normalized, then cut at the first
      `_TERMINATOR`. The cut can shorten the claim (an *unbackticked*
      `§target.path` would check only up to the first dot); the prefix
      convention in `_anchor_matches` absorbs that.

    `form` is the lexical shape found — "quoted", "backticked", "numeric"
    (undelimited, digit-led), "phrase" (undelimited, containing a space) or
    "word" — and exists for the non-vacuity floors.
    """
    claims: list[tuple[int, str, str, str]] = []
    text = _prose_text(text)
    for pattern in _ANCHORED.values():
        for match in pattern.finditer(text):
            target = match.group(1)
            lineno = text.count("\n", 0, match.start()) + 1
            tail = text[match.end():]
            first_break = tail.find("\n")
            if first_break != -1:
                second_break = tail.find("\n", first_break + 1)
                if second_break != -1:
                    tail = tail[:second_break]
            tail = tail.lstrip()
            if tail.startswith('"'):
                anchor = _normalize(tail[1:].partition('"')[0])
                form = "quoted"
            elif tail.startswith("`"):
                anchor = _normalize(tail[1:].partition("`")[0])
                form = "backticked"
            else:
                normalized = _normalize(tail)
                cut = _TERMINATOR.search(normalized)
                anchor = (normalized[: cut.start()] if cut else normalized).strip()
                form = (
                    "numeric"
                    if anchor[:1].isdigit()
                    else "phrase" if " " in anchor else "word"
                )
            claims.append((lineno, target, anchor, form))
    return claims


def _candidates(target: str, paths: list[str], root: Path) -> list[Path]:
    """Every plugin file the reference resolves to, by the same suffix rule as
    `_resolves`. A short reference (`SKILL.md`) resolves to every skill's
    file; the claim graded is that some resolved candidate carries the
    heading — choosing which one the sentence means needs the sentence's
    meaning, so it is the reader's (`.claude/rules/guards.md`)."""
    cleaned = _cleaned(target, root)
    return [
        root / path
        for path in paths
        if path.endswith(".md") and _names(path, cleaned)
    ]


def _token_prefixes(anchor: str) -> list[str]:
    """Every space-token prefix of an undelimited anchor, longest first.

    Where an undelimited anchor ends is a fact about what the English means,
    which the guard may not decide (`.claude/rules/guards.md`) — a truncated
    citation can run straight into the sentence with no punctuation to cut
    at ("§Release version for the bump rules"). So the claim graded is that
    SOME prefix of the tail names a real heading under `_anchor_matches`'s
    boundary convention; which prefix the author meant is the reader's. A
    delimited (quoted or backticked) anchor declared its own extent and is
    compared whole.

    The floor this buys is deliberately weak: the walk reaches a single
    token, so an undelimited anchor holds as long as its first token
    boundary-matches some heading — a heading rename that keeps its leading
    word is not caught. The exact comparison is the delimited forms';
    under-detection is the side `.claude/rules/guards.md` picks over
    failing on correct prose."""
    tokens = anchor.split(" ")
    return [" ".join(tokens[:i]) for i in range(len(tokens), 0, -1)]


def _dangling_anchors_in(
    text: str, paths: list[str], root: Path
) -> list[tuple[int, str, str]]:
    """The scan-and-resolve pipeline of `test_section_anchors_resolve`, on one
    document's text. Candidates are `.md` files only: a § claims a *markdown
    heading*, which no other file kind carries, so an anchor after a citation
    resolving only to a script is out of scope — uncovered, not unresolvable
    (the boundary-naming obligation of `.claude/rules/guards.md`).
    A target resolving to nothing at all is also skipped here: that is the
    file pass's finding, so each dangling file is reported exactly once."""
    dangling = []
    for lineno, target, anchor, form in _anchor_claims(text):
        candidates = _candidates(target, paths, root)
        if not candidates:
            continue
        headings = [
            h
            for candidate in candidates
            for h in _headings(candidate.read_text(encoding="utf-8"))
        ]
        anchors = (
            [anchor] if form in ("quoted", "backticked")
            else _token_prefixes(anchor)
        )
        if not anchor or not any(
            _anchor_matches(a, h) for a in anchors for h in headings
        ):
            dangling.append((lineno, target, anchor))
    return dangling


@pytest.mark.parametrize("root", PLUGIN_ROOTS, ids=lambda r: r.name)
def test_section_anchors_resolve(root: Path) -> None:
    """A dangling § anchor means the file opens and the promised section is
    not in it — the quieter half of the same rot."""
    paths = _plugin_paths(root)
    dangling = [
        (path.relative_to(root).as_posix(), lineno, target, anchor)
        for path in _docs(root)
        for lineno, target, anchor in _dangling_anchors_in(
            path.read_text(encoding="utf-8"), paths, root
        )
    ]
    prefix = root.relative_to(REPO_ROOT).as_posix()
    assert not dangling, (
        "agent prose cites sections its target files do not carry:\n"
        + "\n".join(
            f"  {prefix}/{rel}:{lineno} -> {target} §{anchor}"
            for rel, lineno, target, anchor in dangling
        )
        + "\nRename the citation to the heading the file carries, or restore "
        "the heading the citation promises."
    )


# ---------------------------------------------------------------------------
# Non-vacuity floors. Every floor is "at least one", never an exact count: a
# count pins today's prose and rots with it, while an empty form means the
# convention changed or a pattern died — the only fact a floor exists to
# catch (`.claude/rules/guards.md`: an extractor that finds nothing has
# stopped measuring).


@pytest.mark.parametrize("root", PLUGIN_ROOTS, ids=lambda r: r.name)
def test_reference_detector_finds_every_citation_form(root: Path) -> None:
    """Guard the guard, per plugin: each citation form matches somewhere in
    each tree, and the section-anchor pass binds at least one claim."""
    texts = [p.read_text(encoding="utf-8") for p in _docs(root)]
    for name, pattern in {
        "${CLAUDE_PLUGIN_ROOT}": _PLUGIN_ROOT_REF,
        "backticked": _BARE_REF,
        "unbackticked bare-path": _BARE_PATH_REF,
    }.items():
        assert any(pattern.search(text) for text in texts), (
            f"no {name} citation found in {root.name} — the prose convention "
            "changed, so that form's resolution check is vacuous there."
        )
    assert any(_anchor_claims(text) for text in texts), (
        f"no citation-adjacent § anchor found in {root.name} — the "
        "section-anchor pass is vacuous there."
    )


def test_anchor_detector_finds_every_quoting_form() -> None:
    """Guard the guard, per anchor form: each quoting shape the extractor
    claims to read has a live site. Form floors span the plugin set rather
    than binding per plugin: the extractor branch is shared, so one live site
    anywhere proves the branch still matches, while a per-plugin floor would
    oblige every tree to keep using every quoting style."""
    forms = Counter(
        form
        for root in PLUGIN_ROOTS
        for path in _docs(root)
        for _lineno, _target, _anchor, form in _anchor_claims(
            path.read_text(encoding="utf-8")
        )
    )
    for form in ("quoted", "backticked", "numeric", "phrase", "word"):
        assert forms[form], (
            f"no {form} § anchor found in any plugin — the quoting "
            "convention changed, so that branch of the anchor extractor is "
            "vacuous."
        )


def test_every_anchored_variant_binds_somewhere() -> None:
    """Guard the guard, per anchored variant: each citation form the anchor
    pass extends with `_GAP` has a live § site. Spans the plugin set for the
    same reason the quoting-form floors do — the variants share one claim
    extractor, and one live site anywhere proves a variant still matches."""
    texts = [
        _prose_text(path.read_text(encoding="utf-8"))
        for root in PLUGIN_ROOTS
        for path in _docs(root)
    ]
    for name, pattern in _ANCHORED.items():
        assert any(pattern.search(text) for text in texts), (
            f"no §-anchored {name} citation found in any plugin — that "
            "variant of the anchor pass is vacuous. Either some prose still "
            f"carries a §-anchored {name} citation, or the convention is "
            "gone and the variant leaves _ANCHORED with this floor."
        )


def test_script_citation_branch_is_live() -> None:
    """Guard the guard: the `.py` half of the citation patterns has a live
    site. Spans the plugin set rather than binding per plugin — a tree that
    ships no helper scripts has nothing to cite — while zero sites anywhere
    means the convention changed and the branch is vacuous."""
    script_targets = [
        target
        for root in PLUGIN_ROOTS
        for _rel, _lineno, target in _references(root)
        if target.endswith(".py")
    ]
    assert script_targets, (
        "no .py citation found in any plugin — the script half of the "
        "citation patterns is vacuous."
    )


def test_connector_routing_citations_still_present() -> None:
    """The routing that has to survive: creators reach their spec skill, and
    the drift-classifier description routes bump decisions through the
    release table via an unbackticked frontmatter citation — the reach that
    motivated the bare-path form."""
    targets = [target for _rel, _lineno, target in _references(_CONNECTOR_ROOT)]
    assert any(t.startswith("skills/connector-spec-db/") for t in targets), (
        "no citation routes into skills/connector-spec-db/ — the DB creator "
        "has lost the routing to its spec skill."
    )
    assert "spec-sql-write-path.md" in targets, (
        "spec-sql-write-path.md is cited nowhere in the connector plugin — "
        "the write-path spec has come unrouted."
    )
    bare_path_form = [
        match.group(1)
        for path in _docs(_CONNECTOR_ROOT)
        for line in _prose_text(path.read_text(encoding="utf-8")).splitlines()
        for match in _BARE_PATH_REF.finditer(line)
    ]
    assert "connector-builder/references/metadata-and-versioning.md" in bare_path_form, (
        "the drift-classifier's unbackticked frontmatter citation of the "
        "release table is gone — the bare-path form's motivating site."
    )


# ---------------------------------------------------------------------------
# Synthetic acceptance: documents shaped like the real ones, run through the
# same predicates the real-tree sweeps use.

# A synthetic agent document shaped like the real ones: an unbackticked
# citation in the frontmatter description, and the same unbackticked form in
# the body — both within the bare-path pattern's reach.
_SYNTHETIC_AGENT = """\
---
name: synthetic-classifier
description: Classify per the release table in skills/nowhere/references/gone.md §Table.
tools: Read
---

# synthetic-classifier

Body prose citing skills/nowhere/references/also-gone.md without backticks.
"""

# A path that resolves in the real tree, for healing one synthetic citation
# while testing the other.
_EXISTING = "connector-builder/references/metadata-and-versioning.md"


def _dangling_in(text: str) -> list[str]:
    """The scan-and-resolve pipeline of `test_doc_references_resolve`, on one
    document's text."""
    paths = _plugin_paths(_CONNECTOR_ROOT)
    return [
        target
        for _lineno, target in _scan_text(text)
        if _is_dangling(target, paths, _CONNECTOR_ROOT)
    ]


def test_dangling_frontmatter_citation_is_flagged() -> None:
    """Acceptance: a frontmatter citation of a nonexistent path fails the
    guard. The motivating case — frontmatter is where the dangling citation
    this guard exists for was hiding."""
    doc = _SYNTHETIC_AGENT.replace("skills/nowhere/references/also-gone.md", _EXISTING)
    assert _dangling_in(doc) == ["skills/nowhere/references/gone.md"]


def test_dangling_body_citation_is_flagged() -> None:
    """Acceptance: an unbackticked body citation of a nonexistent path fails
    the guard — body lines are swept exactly like frontmatter lines."""
    doc = _SYNTHETIC_AGENT.replace("skills/nowhere/references/gone.md", _EXISTING)
    assert _dangling_in(doc) == ["skills/nowhere/references/also-gone.md"]


def test_resolving_citations_pass() -> None:
    """The twin: the same document citing specs that exist is clean."""
    twin = _SYNTHETIC_AGENT.replace(
        "skills/nowhere/references/gone.md", _EXISTING
    ).replace("skills/nowhere/references/also-gone.md", _EXISTING)
    # Both citations are seen (not skipped) …
    assert [target for _lineno, target in _scan_text(twin)].count(_EXISTING) == 2
    # … and resolve, so nothing dangles.
    assert _dangling_in(twin) == []


def test_dangling_section_anchor_is_flagged() -> None:
    """Acceptance: a § citation of a heading the resolved target does not
    carry fails the guard, in each quoting form the extractor reads."""
    paths = _plugin_paths(_CONNECTOR_ROOT)
    for citation, anchor in (
        (f"Bump rules: `{_EXISTING}` §No Such Heading, then release.",
         "No Such Heading"),
        (f'Bump rules: `{_EXISTING}` §"No Such Heading", then release.',
         "No Such Heading"),
        (f"Bump rules: `{_EXISTING}` §`no_such_heading`, then release.",
         "no_such_heading"),
    ):
        assert _dangling_anchors_in(citation, paths, _CONNECTOR_ROOT) == [
            (1, _EXISTING, anchor)
        ], citation


def test_resolving_section_anchors_pass() -> None:
    """The twin: the same citation naming headings the target carries — full,
    truncated, and running past the heading into the sentence — is clean."""
    paths = _plugin_paths(_CONNECTOR_ROOT)
    for citation in (
        # the full heading, quoted, with its backticked identifier re-quoted
        f'Bump rules: `{_EXISTING}` §"Release version (`version`)".',
        # truncated: the citation stops before the heading's parenthetical
        f"Bump rules: `{_EXISTING}` §Release version, then release.",
        # fully cited, with the sentence running on past the heading
        f"See `{_EXISTING}` §First release covers the initial tag.",
        # truncated AND running on, with no punctuation at the cut
        f"See `{_EXISTING}` §Release version for the bump rules.",
    ):
        assert _dangling_anchors_in(citation, paths, _CONNECTOR_ROOT) == [], citation


def test_pattern_boundaries_stay_drawn() -> None:
    """Acceptance for the boundaries the patterns and resolver draw. Each
    line pins a convention a real-tree sweep only covers while some live
    prose site happens to discriminate it."""
    paths = _plugin_paths(_CONNECTOR_ROOT)
    # A bare `.py` name is an authored-artifact name, never a citation.
    assert _scan_text("Ship `connector.py` beside the maps.") == []
    # A `.py` path with a directory segment is a citation, and dangles.
    assert _dangling_in("Run `skills/nowhere/gone.py` first.") == [
        "skills/nowhere/gone.py"
    ]
    # A suffix match binds only at a `/` boundary: chopping characters off
    # the front of a real filename must not resolve through it.
    tail = _EXISTING.rsplit("/", 1)[1]
    assert _resolves(tail, paths, _CONNECTOR_ROOT)
    assert not _resolves(tail[3:], paths, _CONNECTOR_ROOT)
    # A `../`-prefixed reference resolves by the same suffix rule.
    assert _dangling_in(f"See `../{_EXISTING}` for the table.") == []
    # Markdown link text is display only; the URL half is the citation.
    assert _scan_text("[skills/nowhere/gone.md](https://example.com/x)") == []


def test_anchor_matching_convention_holds_at_its_boundaries() -> None:
    """Acceptance for `_anchor_matches` at the edges its docstring promises:
    prefixes bind only at token boundaries, in either direction, and a
    numeric anchor matches on the whole leading number, never a digit
    prefix of it."""
    assert _anchor_matches("Release version", "Release version (version)")
    assert not _anchor_matches("Mode", "Modes")
    assert not _anchor_matches("First releases", "First release")
    assert _anchor_matches("4", "4. Write the map")
    assert not _anchor_matches("12", "1. Overview")


def test_headings_ignore_fenced_hash_lines() -> None:
    """A `#` inside a fence is sample output or a shell comment, not a
    section an anchor may bind to."""
    text = "## Real section\n```bash\n# not a heading\n```\n"
    assert _headings(text) == ["Real section"]


def test_masked_spans_are_not_scanned() -> None:
    """A citation inside a fence (column-0 or indented) or an HTML comment
    is a sample or tooling metadata, not a citation; the first prose line
    after the span closes is scanned again."""
    doc = (
        "```bash\npython3 plugins/nowhere/scripts/gone.py\n"
        "see skills/nowhere/references/gone.md\n```\n"
        "<!-- see skills/nowhere/references/also-gone.md -->\n"
        f"Then read {_EXISTING} for the table.\n"
    )
    indented = (
        "- step one:\n\n  ```bash\n  python3 plugins/nowhere/scripts/gone.py\n"
        f"  ```\n\nThen read {_EXISTING} for the table.\n"
    )
    for text in (doc, indented):
        assert [t for _lineno, t in _scan_text(text)] == [_EXISTING], text


def test_masking_survives_fence_and_comment_interleaving() -> None:
    """Acceptance for the interleavings with no live site yet: a fence
    marker inside a comment must not open a fence, and a comment opener
    inside a fence must not open a comment — either error blanks the rest
    of the document and silently skips every later citation."""
    fence_in_comment = (
        "<!-- maintainer note\n```\n-->\n"
        "Body citing skills/nowhere/references/gone.md here.\n"
    )
    comment_in_fence = (
        "```\n<!--\n```\n"
        "Body citing skills/nowhere/references/gone.md here.\n"
    )
    for text in (fence_in_comment, comment_in_fence):
        assert _dangling_in(text) == ["skills/nowhere/references/gone.md"], text


def test_anchored_forms_are_not_double_counted() -> None:
    """A line carrying a backticked reference and a ${CLAUDE_PLUGIN_ROOT}
    reference yields exactly one match per target: the bare-path pattern's
    lookbehind must not re-match the path tail inside either anchored form."""
    line = (
        "Read ${CLAUDE_PLUGIN_ROOT}/skills/connector-spec-db/spec-tls.md "
        "and `references/io-contracts.md`."
    )
    targets = [target for _lineno, target in _scan_text(line)]
    assert targets.count("skills/connector-spec-db/spec-tls.md") == 1
    assert targets.count("references/io-contracts.md") == 1
    assert len(targets) == 2
