"""Every citation a plugin's prose makes resolves to something that exists.

Agent prose routes an agent to its rules by filename, and to the part of that
file that carries the rule by section. A rename, a move, or a deleted file
leaves the citation dangling — and an agent that cannot read what it was sent
to read does not fail loudly, it authors without the rules. Nothing else in the
suite notices: these are strings in markdown.

Two claims, both checked, for **every** plugin under `plugins/`. The roots are
discovered rather than listed, so a plugin cannot land unnoticed: until it has
entries in the three per-plugin registries below, this suite is red.

1. **The file exists.** Five citation forms carry it:

   - `${CLAUDE_PLUGIN_ROOT}/skills/…/spec-x.md` — the absolute form the agent
     frontmatter uses for required reading. It also names scripts an agent
     runs, so the path universe is every file in the plugin, not only `.md`.
   - `` `spec-x.md` `` / `` `references/io-contracts.md` `` — the bare
     backticked form used for cross-references between sibling specs. This is
     the dominant form, by a wide margin.
   - Unbackticked bare paths with a directory segment, on every line — the
     `description:` citations the orchestrator reads to route work (frontmatter
     often cites a path with no backticks, which is exactly where a dangling
     citation to a since-deleted spec hid) and the same unbackticked form in
     body prose.
   - The path half of a `§` citation, which the three patterns above can miss:
     `` `SKILL.md §Pipeline` `` puts the anchor inside the backticks, so the
     backticked pattern never sees a closing backtick after `.md`.
   - Markdown links, `](spec-x.md)` — resolved relative to the citing file,
     since that is what a link means, and allowed to leave the plugin (the
     READMEs link the repo root's). A link's `#fragment` is checked as a
     section, below.

   What is **not** checked: a non-`.md` path in any form but the first. A
   backticked `connector.py` or `definition/connector.json` names an artifact
   the connector author writes, not a file of this plugin, and telling the two
   apart needs a rule this guard does not have. Agent-run scripts are covered
   because agents invoke them through `${CLAUDE_PLUGIN_ROOT}`.

2. **The section exists.** A `path.md §Heading` citation, and a link's
   `#fragment`, make a second claim the file check never opens: that the
   heading is still there. A heading rename leaves the citation
   half-dangling — the file opens, the section the agent was sent to read is
   gone. `§` with no file in front of it cites a section of the citing
   document itself, and is resolved against it. The two forms differ in how
   exactly they name the heading: prose abbreviates and runs on, so `§` is
   matched by opening words; a fragment is generated from the whole heading,
   so it is matched by slug.

Pure text-vs-filesystem: no contract packages involved, so no `_pins` skip
guard — this always runs.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = REPO_ROOT / "plugins"


def _plugin_names() -> list[str]:
    """Every plugin directory, discovered. Discovery does not by itself guard a
    new plugin — the per-plugin registries below still have to be filled in —
    but it makes the omission loud instead of silent, which is the half a
    hand-listed set of roots gets wrong."""
    return sorted(p.name for p in PLUGINS_DIR.iterdir() if p.is_dir())


# `${CLAUDE_PLUGIN_ROOT}/skills/foo/bar.md` — the path segment only. `.` is in
# the charset (paths need it), so a reference ending a sentence captures the
# full stop; `_clean` strips a trailing `.` and `/` rather than trying to
# express "not at the end" in the charset.
_PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")

# A backticked markdown filename, optionally with a leading directory path:
# `spec-tls.md`, `references/io-contracts.md`. Bare `.md` only — see the
# module docstring on why non-`.md` citations stay out of this form.
_BARE_REF = re.compile(r"`((?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.md)`")

# An unbackticked `.md` path, matched on every line. At least one directory
# segment is required: a bare filename with no slash is indistinguishable
# from an ordinary prose word. That requirement is what keeps prose from
# matching — applied to every line of a plugin every match today is a genuine
# citation. The lookbehind rejects starts preceded by a backtick (that is
# `_BARE_REF`'s form) or by a path character, so the tail of a
# `${CLAUDE_PLUGIN_ROOT}/…` reference is not re-matched. The directory-segment
# charset mirrors `_BARE_REF`'s — no `.`, so a `./`- or `../`-prefixed
# relative path is out of scope here; it arrives as a markdown link instead.
# The second lookbehind hands a link target to the link pass alone: without it
# `](skills/x/y.md)` matches here too, and one broken link fails two tests.
_BARE_PATH_REF = re.compile(
    r"(?<![\w`./-])(?<!\]\()((?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+\.md)(?![\w-])"
)

_PATH_PATTERNS = (_PLUGIN_ROOT_REF, _BARE_REF, _BARE_PATH_REF)

# A markdown link target and its optional fragment: `](spec-columns.md)`,
# `](../endpoint-spec/x.md)`, `](x.md#uniqueness)`. Kept apart from the
# patterns above because it resolves differently — relative to the citing file,
# and it may point outside the plugin, which the READMEs do. The fragment is
# captured, not discarded: it is the same claim a `§` citation makes, and
# leaving it unread would close the section-citation hole in one form while
# leaving it open in the other.
_LINK_REF = re.compile(r"\]\(([^)\s#]+\.md)(#[^)\s]*)?\)")

# What GitHub keeps when it slugs a heading into a fragment: case folded,
# spaces to hyphens, everything else that is not a word character or hyphen
# dropped. Enough to resolve `#derived-endpoint_id` against
# ``## Derived `endpoint_id` ``.
_SLUG_DROP = re.compile(r"[^\w\- ]")

# The file a `§` binds to: the last `.md` path before it, separated by nothing
# but glue — a closing backtick, whitespace (the citation often wraps a line),
# an opening paren or quote, a comma or a dash. More than that and the `§` is
# prose-separated from the path, which is how a bare `§Closed vocabularies` — a
# section of the citing document itself — is told apart from `` `SKILL.md`
# §Closed vocabularies``. A sentence-final `.` stays out of the glue: it ends
# the clause that named the file, so what follows is a fresh, document-local
# citation. Missing a comma or a dash here does not merely lose the binding —
# it silently re-points the anchor at the citing document and reports a section
# of the wrong file as missing.
_ANCHOR_BINDING = re.compile(r"([A-Za-z0-9_./-]+\.md)[`\"'\s(,—–-]{0,8}$")

# How far past a `§` an anchor may reach. One bound, used by both the quoted
# and the unquoted form — two would cut the two forms at different lengths.
_ANCHOR_WINDOW = 200

# The anchor text after `§`. Quoted form first: a heading whose own punctuation
# the stop set below would cut short is quoted for exactly that reason.
_QUOTED_ANCHOR = re.compile(r'\s*["“]([^"”]{1,%d})["”]' % _ANCHOR_WINDOW)

# Where an unquoted anchor ends: a closing bracket, a clause break, an
# em-dash, a table-cell divider, a sentence-final period, or a blank line.
# Prose continuing past one of these is no longer the heading — `§Import rules
# is the list, and it is short.` cites `Import rules`, and the token-prefix
# rule in `_anchor_resolves` is what lets the surviving prose tail ride along.
_ANCHOR_STOP = re.compile(r"[)\]},;—|]|\.(?=[\s`]|$)|\n[ \t]*\n")

_HEADING = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
_FENCE = re.compile(r"^\s*(?:```|~~~)")

# Word characters for heading/anchor comparison: `-` and `_` stay inside a
# token so `cross-field` and `endpoint_id` are single words; everything else
# (backticks, parens, emphasis) is separator, so `` Derived `endpoint_id` ``
# and `Derived endpoint_id` compare equal.
_TOKEN = re.compile(r"[a-z0-9_-]+")

# Citations that deliberately name something outside the plugin, each a
# recorded decision. A new entry here should be rare and deserves a reason.
_EXTERNAL_REFS: dict[str, set[str]] = {
    "analitiq-connector-builder": {
        # The storage skill is an explicit stub: it names the specs that will
        # exist "when engine support arrives" (see its own prose).
        "spec-file-transport.md",
        "spec-stdout-transport.md",
        "spec-s3-transport.md",
        # An ADR owned by the engine, cited as the source of record for the
        # write path. The citing prose attributes it to the engine.
        "docs/sql-write-path-v2.md",
    },
    "analitiq-pipeline-builder": set(),
}

# Every citation form this suite extracts. A form absent from the floors below
# would be unfloored and free to die silently, so `_REPO_FLOORS` must name them
# all — `test_every_plugin_is_covered` checks that.
_FORMS = ("plugin_root", "backticked", "bare_path", "link", "anchor")

# The floor below which an extractor is no longer reading prose at all. A
# pattern that stops matching passes vacuously, so each form is floored
# separately — a floor on the total would let one dead pattern hide behind
# another's growth. `anchor` counts anchors actually compared against a
# target's headings, not `§` characters found, so an anchor pass that finds
# citations and then quietly grades none of them trips it too.
#
# Repo-wide first, because that is where a form has enough sites for a floor to
# mean "the extractor works" rather than "this one sentence still exists".
# Floors sit near half of today's counts: prose churn must not move them, a
# broken extractor must. The counts are not restated here — the failure message
# reports found-vs-floor.
_REPO_FLOORS: dict[str, int] = {
    "plugin_root": 12,
    "backticked": 120,
    "bare_path": 6,
    "link": 3,
    "anchor": 20,
}

# Per plugin on top, so a form dying in one plugin cannot hide behind the
# other's volume. A form is listed only where that plugin writes it often
# enough for a floor to mean "the extractor works" rather than "this one
# sentence still exists" — the connector plugin has a single markdown link and
# the pipeline plugin a single unbackticked bare path, and flooring either
# would turn a reworded sentence into a "your extractor is broken" failure.
# Those two forms stay guarded repo-wide.
_FLOORS: dict[str, dict[str, int]] = {
    "analitiq-connector-builder": {
        "plugin_root": 10,
        "backticked": 70,
        "bare_path": 5,
        "anchor": 15,
    },
    "analitiq-pipeline-builder": {
        "plugin_root": 2,
        "backticked": 50,
        "link": 3,
        "anchor": 4,
    },
}

# What a plugin must state about itself for this suite to grade it: real
# citations the extractors have to keep finding, and one real file to write the
# acceptance tests against. One registry rather than two lists filled at the
# same moment — a new plugin is registered in one place or not at all.
#
# `sentinels` names one citation per form that carries a routing decision.
# Floors prove a form still matches *something*; sentinels prove the wiring an
# agent depends on is still written down — a creator routed to its spec skill,
# a classifier routed to the release table. A rename here is a review moment,
# not a silent pass.
#
# `fixture` is a real file plus the opening words of a heading it carries (the
# citation form the prose uses — `## Release version (`version`)` is cited as
# `§Release version`), so the acceptance tests dangle one citation against a
# document that genuinely exists.
_PLUGIN_FIXTURES: dict[str, dict[str, object]] = {
    "analitiq-connector-builder": {
        "sentinels": {
            "plugin_root": "skills/connector-spec-db/spec-connector-package.md",
            "bare_path": "connector-builder/references/metadata-and-versioning.md",
            "backticked": "spec-sql-write-path.md",
        },
        "fixture": (
            "skills/connector-builder/references/metadata-and-versioning.md",
            "Release version",
        ),
    },
    "analitiq-pipeline-builder": {
        "sentinels": {
            "plugin_root": "scripts/validate.py",
            "bare_path": "pipeline-builder/references/io-contracts.md",
            "backticked": "spec-database-object.md",
        },
        "fixture": (
            "skills/pipeline-builder/references/identity-and-versioning.md",
            "Metadata fields",
        ),
    },
}


def _sentinels(plugin: str) -> dict[str, str]:
    return _PLUGIN_FIXTURES[plugin]["sentinels"]  # type: ignore[return-value]


def _fixture(plugin: str) -> tuple[str, str]:
    return _PLUGIN_FIXTURES[plugin]["fixture"]  # type: ignore[return-value]


def _plugin_root(plugin: str) -> Path:
    return PLUGINS_DIR / plugin


@cache
def _plugin_paths(plugin: str) -> tuple[str, ...]:
    """Every file and directory in the plugin, as plugin-root-relative posix
    paths. Every file, not only `.md`: agent frontmatter cites the helper
    scripts it runs by the same `${CLAUDE_PLUGIN_ROOT}/…` form, and a citation
    of a deleted script starves an agent exactly as a citation of a deleted
    spec does."""
    root = _plugin_root(plugin)
    return tuple(p.relative_to(root).as_posix() for p in sorted(root.rglob("*")))


def _clean(target: str) -> str:
    """A citation as written, reduced to the path it names: no trailing slash
    on a directory reference, no sentence-final period swept up by the `.` in
    the path charset (only a trailing one, never one inside the name)."""
    cleaned = target.rstrip("/")
    return cleaned[:-1] if cleaned.endswith(".") else cleaned


def _candidates(target: str, plugin: str, citing: str | None = None) -> list[Path]:
    """Everything a citation could name. The one resolution rule in this file —
    both passes read it, so they cannot come to different answers about whether
    a citation resolves.

    The prose writes citations at whatever depth reads well from where it sits
    — `spec-tls.md`, `connector-spec-db/spec-type-maps.md`,
    `skills/connector-builder/references/io-contracts.md` and directory
    citations like `skills/connector-spec-db/examples/` all appear, and every
    one is unambiguous to a reader. So resolve the way a reader does: a
    citation names anything it is a path suffix of. That deliberately does not
    check the citation was written from the right directory — only that the
    thing it names exists, which is the failure that silently starves an agent
    of its rules.

    Two refinements on top:

    - A citation that spells out `plugins/<name>/…` is fully qualified, may
      name a sibling plugin, and is matched exactly against the repo tree.
    - Given the citing document, the nearest ancestor directory holding the
      path wins alone: `SKILL.md` cited from
      `skills/pipeline-builder/references/pipeline.md` is that skill's own
      `SKILL.md`, not another skill's. Without that, a basename four or five
      files answer to returns all of them — which the anchor pass wants (an
      anchor checked against every candidate is imprecise about *which* file it
      read; an anchor checked against none is unchecked).
    """
    cleaned = _clean(target)
    if cleaned.startswith("plugins/"):
        candidate = REPO_ROOT / cleaned
        return [candidate] if candidate.exists() else []
    root = _plugin_root(plugin)
    if citing is not None:
        for ancestor in (root / citing).parents:
            if not ancestor.is_relative_to(root):
                break
            candidate = ancestor / cleaned
            if candidate.exists():
                return [candidate]
    return [
        root / path
        for path in _plugin_paths(plugin)
        if path == cleaned or path.endswith("/" + cleaned)
    ]


def _resolve_files(target: str, citing: str, plugin: str) -> list[Path]:
    """The files a citation could name — the anchor pass's view of
    `_candidates`, since a section can only be read out of a file."""
    return [path for path in _candidates(target, plugin, citing) if path.is_file()]


def _scan_text(text: str) -> list[tuple[int, str]]:
    """Every (lineno, target) path citation in one document's text — the three
    path patterns on every line, plus the path half of every `§` citation.

    De-duplicated per line: a `` `SKILL.md §Pipeline` `` citation is seen by
    two extractors, and one broken citation must read as one finding.
    """
    from_paths = [
        (lineno, match.group(1))
        for lineno, line in enumerate(text.splitlines(), 1)
        for pattern in _PATH_PATTERNS
        for match in pattern.finditer(line)
    ]
    from_anchors = [
        (site.lineno, site.target) for site in _anchor_sites(text) if site.target
    ]
    return list(dict.fromkeys(from_paths + from_anchors))


class Anchor(NamedTuple):
    """One `§` citation: where it sits, the file it binds to (`None` for a
    citation of the document it is written in), the heading it names, and
    whether the author quoted that heading."""

    lineno: int
    target: str | None
    text: str
    quoted: bool


def _anchor_sites(text: str) -> list[Anchor]:
    """Every `§` citation in one document.

    Scanned over the whole text, not line by line: a citation that wraps —
    ``§Dialect\\n  hooks)`` — is one citation, and a per-line scan would read
    half of it.
    """
    sites: list[Anchor] = []
    for marker in re.finditer("§", text):
        rest = text[marker.end() :]
        sites.append(
            Anchor(
                lineno=text.count("\n", 0, marker.start()) + 1,
                target=(
                    binding.group(1)
                    if (binding := _ANCHOR_BINDING.search(text[: marker.start()]))
                    else None
                ),
                text=_anchor_text(rest),
                quoted=_QUOTED_ANCHOR.match(rest) is not None,
            )
        )
    return sites


def _anchor_text(rest: str) -> str:
    """The heading an anchor names, cut out of the prose that follows it.
    Quoting is a stronger claim than citing — it says *this is the heading,
    verbatim* — and `Anchor.quoted` carries that through to the comparison."""
    quoted = _QUOTED_ANCHOR.match(rest)
    if quoted:
        return quoted.group(1)
    window = rest[:_ANCHOR_WINDOW]
    stop = _ANCHOR_STOP.search(window)
    # A trailing backtick belongs to the citation's own markup, not the
    # heading: `` `SKILL.md §Pipeline` `` closes after the anchor.
    return (window[: stop.start()] if stop else window).strip().rstrip("`").strip()


def _headings(text: str) -> list[str]:
    """Every ATX heading in a document, fenced blocks excluded — a `# comment`
    inside a fenced example is not a section anyone can cite."""
    headings, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        match = None if fenced else _HEADING.match(line)
        if match:
            headings.append(match.group(2))
    return headings


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(text.lower()))


def _anchor_resolves(anchor: str, headings: list[str], exact: bool = False) -> bool:
    """Does the cited section exist in the target file?

    Compared as word sequences, because a citation both abbreviates the heading
    and runs on past it — often in the same sentence, which is why neither a
    prefix test nor an equality test alone works for prose.

    `exact` is for a **quoted** anchor, where the author claimed the heading
    verbatim: then only word-for-word equality passes. That is what catches a
    rename in the middle of a long heading, which the opening-words rule
    cannot — two shared opening words is all an unquoted citation ever
    promises.
    """
    cited = _tokens(anchor)
    if not cited:
        return False
    for heading in headings:
        actual = _tokens(heading)
        if not actual:
            continue
        if exact:
            if actual == cited:
                return True
            continue
        # The citation abbreviates the heading — `§Encoding values` for
        # `## Encoding values (closed enum)`, `§1` for `## 1. First-class ADBC
        # drivers`. Safe at any length: naming fewer words than the heading has
        # cannot name a heading that is gone.
        if actual[: len(cited)] == cited:
            return True
        # Or the citation names the heading's opening words and then runs on
        # into prose the stop set could not cut — `§Cross-field rules for the
        # exact tuple` for `## Cross-field rules the contract enforces`. Two
        # shared words is the floor: one is a coincidence a one-word heading
        # like `## Output` would hand to every anchor beginning with "Output",
        # including one whose section was renamed away.
        shared = 0
        for cited_word, actual_word in zip(cited, actual):
            if cited_word != actual_word:
                break
            shared += 1
        if shared >= 2:
            return True
    return False


def _references(plugin: str) -> list[tuple[str, int, str]]:
    """Every (relpath, lineno, target) path citation in the plugin."""
    root = _plugin_root(plugin)
    return [
        (path.relative_to(root).as_posix(), lineno, target)
        for path in sorted(root.rglob("*.md"))
        for lineno, target in _scan_text(path.read_text(encoding="utf-8"))
    ]


def _link_references(plugin: str) -> list[tuple[str, int, str, str]]:
    """Every (relpath, lineno, target, fragment) markdown-link citation. The
    fragment is `""` when the link names no section."""
    root = _plugin_root(plugin)
    return [
        (
            path.relative_to(root).as_posix(),
            lineno,
            match.group(1),
            (match.group(2) or "").lstrip("#"),
        )
        for path in sorted(root.rglob("*.md"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for match in _LINK_REF.finditer(line)
    ]


def _anchor_references(plugin: str) -> list[tuple[str, Anchor]]:
    """Every (relpath, anchor) `§` citation in the plugin."""
    root = _plugin_root(plugin)
    return [
        (path.relative_to(root).as_posix(), site)
        for path in sorted(root.rglob("*.md"))
        for site in _anchor_sites(path.read_text(encoding="utf-8"))
    ]


def _is_dangling(target: str, plugin: str) -> bool:
    """The one exemption-and-resolution predicate: a citation dangles unless it
    is allow-listed as deliberately external or names something that exists.
    Both the real-tree sweep and the synthetic acceptance tests go through this,
    so the acceptance tests exercise the exemption logic that ships."""
    return _clean(target) not in _EXTERNAL_REFS[plugin] and not _candidates(
        target, plugin
    )


def _slug(heading: str) -> str:
    """A heading as the fragment that links to it — case folded, punctuation
    dropped, spaces hyphenated."""
    return _SLUG_DROP.sub("", heading.lower()).strip().replace(" ", "-")


def _link_dangles(target: str, fragment: str, citing: str, plugin: str) -> bool:
    """Does a markdown link point at something that is not there?

    The file resolves relative to the document the link is written in — that is
    what a link means — and may leave the plugin, which is how a plugin README
    links the repo root's. A fragment is the same claim a `§` citation makes,
    so it is held to the same standard: the heading it slugs to must exist in
    the file the link opens.
    """
    path = (_plugin_root(plugin) / citing).parent / target
    if not path.is_file():
        return True
    if not fragment:
        return False
    return fragment.lower() not in {
        _slug(heading) for heading in _headings(path.read_text(encoding="utf-8"))
    }


def _anchor_checks(
    plugin: str, sites: list[tuple[str, Anchor]]
) -> tuple[list[tuple[str, int, str, str]], int]:
    """Every `§` citation whose section is in none of the files it could name,
    and how many citations were compared at all.

    A citation whose *file* does not exist is not reported here — that is the
    file pass's finding, and reporting it twice would make one break read as
    two. It is excluded from the compared count too, so the floor on that count
    stays a statement about anchors this pass actually graded.
    """
    dangling, checked = [], 0
    for rel, site in sites:
        if site.target is not None and _clean(site.target) in _EXTERNAL_REFS[plugin]:
            continue
        # No path in front of the `§`: the citation names a section of the
        # document it sits in.
        candidates = [
            path
            for path in (
                _resolve_files(site.target, rel, plugin)
                if site.target
                else [_plugin_root(plugin) / rel]
            )
            if path.is_file()
        ]
        if not candidates:
            continue
        checked += 1
        if not any(
            _anchor_resolves(
                site.text,
                _headings(path.read_text(encoding="utf-8")),
                exact=site.quoted,
            )
            for path in candidates
        ):
            dangling.append((rel, site.lineno, site.target or rel, site.text))
    return dangling, checked


@pytest.mark.parametrize("plugin", _plugin_names())
def test_doc_references_resolve(plugin: str) -> None:
    """A dangling citation means an agent silently reads nothing."""
    dangling = [
        (rel, lineno, target)
        for rel, lineno, target in _references(plugin)
        if _is_dangling(target, plugin)
    ]
    assert not dangling, (
        "agent prose points at files that do not exist:\n"
        + "\n".join(
            f"  plugins/{plugin}/{rel}:{lineno} -> {target}"
            for rel, lineno, target in dangling
        )
        + "\nFix the path, restore the file the agent is told to read, or — if "
        "the target deliberately lives outside this plugin — add it to "
        "_EXTERNAL_REFS with a reason."
    )


@pytest.mark.parametrize("plugin", _plugin_names())
def test_markdown_links_resolve(plugin: str) -> None:
    """A link is a citation a reader clicks — file and, when it names one,
    section. A link's `#fragment` is the same claim `§Heading` makes, and is
    held to the same standard."""
    dangling = [
        (rel, lineno, target, fragment)
        for rel, lineno, target, fragment in _link_references(plugin)
        if _link_dangles(target, fragment, rel, plugin)
    ]
    assert not dangling, (
        "markdown links point at something that does not exist:\n"
        + "\n".join(
            f"  plugins/{plugin}/{rel}:{lineno} -> {target}"
            + (f"#{fragment}" if fragment else "")
            for rel, lineno, target, fragment in dangling
        )
        + "\nLink targets are resolved relative to the file they are written "
        "in — check the number of `../` segments before assuming the target "
        "moved. A `#fragment` must slug to a heading the target still carries."
    )


@pytest.mark.parametrize("plugin", _plugin_names())
def test_section_anchors_resolve(plugin: str) -> None:
    """A `§` citation whose heading is gone opens the file and starves the
    agent of the rule anyway — the half-dangling case the file pass cannot
    see."""
    dangling, _checked = _anchor_checks(plugin, _anchor_references(plugin))
    assert not dangling, (
        "agent prose cites sections that do not exist:\n"
        + "\n".join(
            f"  plugins/{plugin}/{rel}:{lineno} -> {target} §{anchor}"
            for rel, lineno, target, anchor in dangling
        )
        + "\nRepoint the citation at the heading as it now reads, or restore "
        "the heading. A citation must name the heading's opening words: prose "
        "that continues past it is fine, but a paraphrase is not."
    )


@pytest.mark.parametrize("plugin", _plugin_names())
def test_external_ref_allowlist_is_not_stale(plugin: str) -> None:
    """An allow-listed name that no prose cites any more is dead config.

    Without this, `_EXTERNAL_REFS` only ever grows, and an entry could mask a
    genuine dangling citation introduced later under the same filename.
    """
    cited = {_clean(target) for _rel, _lineno, target in _references(plugin)}
    unused = sorted(_EXTERNAL_REFS[plugin] - cited)
    assert not unused, (
        f"_EXTERNAL_REFS[{plugin!r}] entries {unused} are no longer referenced "
        "by any prose — drop them so the allow-list keeps meaning what it says."
    )


def test_every_plugin_is_covered() -> None:
    """The guard's own reachability. Discovering the roots is what makes a new
    plugin loud; the per-plugin registries are what make it *guarded*, and a
    plugin missing from any of them raises `KeyError` rather than being scanned
    leniently. Iterated from one mapping, so a registry added later is covered
    by writing it down once."""
    registries = {
        "_FLOORS": set(_FLOORS),
        "_EXTERNAL_REFS": set(_EXTERNAL_REFS),
        "_PLUGIN_FIXTURES": set(_PLUGIN_FIXTURES),
    }
    names = set(_plugin_names())
    missing = {name: sorted(names - keys) for name, keys in registries.items()}
    stale = {name: sorted(keys - names) for name, keys in registries.items()}
    assert not any(missing.values()), (
        f"plugins missing from the per-plugin registries: "
        f"{ {k: v for k, v in missing.items() if v} } — give the new plugin "
        "its own floors, external-citation allow-list, sentinel citations and "
        "acceptance-test fixture."
    )
    assert not any(stale.values()), (
        f"registry entries naming plugins that no longer exist: "
        f"{ {k: v for k, v in stale.items() if v} } — drop them."
    )
    unfloored = sorted(set(_FORMS) - set(_REPO_FLOORS))
    assert not unfloored, (
        f"citation forms with no repo-wide floor: {unfloored} — an unfloored "
        "form is free to stop matching without failing anything."
    )
    unknown = sorted(
        {form for floors in _FLOORS.values() for form in floors} - set(_FORMS)
    )
    assert not unknown, (
        f"per-plugin floors name forms the extractor does not produce: "
        f"{unknown} — a floor on a form nobody counts never fails."
    )


def _form_counts(plugin: str) -> dict[str, int]:
    """How many citations each extractor finds in a plugin, per form. `anchor`
    is the number compared against a target's headings, not the number of `§`
    characters, so a pass that finds anchors and grades none of them is not
    counted as working."""
    texts = [
        path.read_text(encoding="utf-8")
        for path in sorted(_plugin_root(plugin).rglob("*.md"))
    ]
    per_line = {
        name: sum(
            len(pattern.findall(line)) for text in texts for line in text.splitlines()
        )
        for name, pattern in (
            ("plugin_root", _PLUGIN_ROOT_REF),
            ("backticked", _BARE_REF),
            ("bare_path", _BARE_PATH_REF),
            ("link", _LINK_REF),
        )
    }
    _dangling, checked = _anchor_checks(plugin, _anchor_references(plugin))
    return per_line | {"anchor": checked}


def _below_floor(counts: dict[str, int], floors: dict[str, int]) -> dict[str, tuple[int, int]]:
    return {
        form: (counts[form], floor)
        for form, floor in floors.items()
        if counts[form] < floor
    }


def _floor_failure(scope: str, below: dict[str, tuple[int, int]]) -> str:
    return (
        f"citation forms below their floor in {scope}: "
        + ", ".join(
            f"{form} found {found}, floor {floor}"
            for form, (found, floor) in sorted(below.items())
        )
        + " — either the citation convention changed (repoint the extractor "
        "and the floor together) or the extractor is broken and this check "
        "was about to pass vacuously."
    )


def test_every_citation_form_is_read_somewhere() -> None:
    """Guard the guard, repo-wide: every form must still be matching across
    the plugins taken together. This is the floor that can name every form,
    including the ones a single plugin writes too rarely to floor."""
    totals = {form: 0 for form in _FORMS}
    for plugin in _plugin_names():
        for form, count in _form_counts(plugin).items():
            totals[form] += count
    below = _below_floor(totals, _REPO_FLOORS)
    assert not below, _floor_failure("plugins/", below)


@pytest.mark.parametrize("plugin", _plugin_names())
def test_citation_detector_reads_this_plugin(plugin: str) -> None:
    """And per plugin, so a form dying in one plugin cannot hide behind the
    other's volume."""
    below = _below_floor(_form_counts(plugin), _FLOORS[plugin])
    assert not below, _floor_failure(f"plugins/{plugin}", below)


@pytest.mark.parametrize("plugin", _plugin_names())
def test_sentinel_citations_are_still_found(plugin: str) -> None:
    """Floors prove a form still matches something; these prove the extractor
    still reaches the specific routing citations agents depend on. A count
    cannot do that — an over-matching pattern raises the count while losing
    the citation that mattered."""
    found = {target for _rel, _lineno, target in _references(plugin)}
    missing = {
        form: target
        for form, target in _sentinels(plugin).items()
        if target not in found
    }
    assert not missing, (
        f"sentinel citations no longer found in plugins/{plugin}: {missing} — "
        "if the prose deliberately moved the citation, repoint the sentinel; "
        "if not, the routing an agent depends on just disappeared."
    )


# A synthetic agent document shaped like the real ones: an unbackticked
# citation in the frontmatter description, and the same unbackticked form in
# the body — both within the bare-path pattern's reach.
_SYNTHETIC_AGENT = """\
---
name: synthetic-classifier
description: Classify per the release table in skills/nowhere/references/gone.md.
tools: Read
---

# synthetic-classifier

Body prose citing skills/nowhere/references/also-gone.md without backticks.
"""

def _dangling_in(text: str, plugin: str) -> list[str]:
    """The scan-and-resolve pipeline of `test_doc_references_resolve`, on one
    document's text."""
    return [
        target for _lineno, target in _scan_text(text) if _is_dangling(target, plugin)
    ]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_dangling_frontmatter_citation_is_flagged(plugin: str) -> None:
    """Acceptance: a frontmatter citation of a nonexistent path fails the
    guard. The motivating case — frontmatter is where the dangling citation
    this guard exists for was hiding."""
    existing, _heading = _fixture(plugin)
    doc = _SYNTHETIC_AGENT.replace("skills/nowhere/references/also-gone.md", existing)
    assert _dangling_in(doc, plugin) == ["skills/nowhere/references/gone.md"]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_dangling_body_citation_is_flagged(plugin: str) -> None:
    """Acceptance: an unbackticked body citation of a nonexistent path fails
    the guard — body lines are swept exactly like frontmatter lines."""
    existing, _heading = _fixture(plugin)
    doc = _SYNTHETIC_AGENT.replace("skills/nowhere/references/gone.md", existing)
    assert _dangling_in(doc, plugin) == ["skills/nowhere/references/also-gone.md"]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_dangling_plugin_root_citation_is_flagged(plugin: str) -> None:
    """Acceptance: the `${CLAUDE_PLUGIN_ROOT}` form, and with it the widening
    of the path universe past `.md` — an agent that is told to run a script
    that no longer exists fails at the shell, having authored nothing."""
    assert _dangling_in(
        'Run python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gone.py" now.\n', plugin
    ) == ["scripts/gone.py"]
    assert _dangling_in(
        'Run python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate.py" now.\n',
        "analitiq-pipeline-builder",
    ) == []


@pytest.mark.parametrize("plugin", _plugin_names())
def test_dangling_anchor_path_half_is_flagged(plugin: str) -> None:
    """Acceptance: a citation that puts the anchor inside the backticks —
    `` `SKILL.md §Pipeline` `` — is seen by no path pattern, only by the
    anchor binding. Without that half of `_scan_text` the file it names is
    never checked."""
    assert _dangling_in("Author per `skills/nowhere/gone.md §Foo`.\n", plugin) == [
        "skills/nowhere/gone.md"
    ]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_fully_qualified_citations_resolve_across_plugins(plugin: str) -> None:
    """Acceptance: a `plugins/<name>/…` citation is matched exactly against the
    repo's plugins tree, so it can name a sibling plugin — and a wrong one
    still fails, rather than falling back to a lenient suffix match."""
    assert _dangling_in(f"See `plugins/{plugin}/CLAUDE.md` for rules.\n", plugin) == []
    assert _dangling_in("See `plugins/analitiq-nonexistent/CLAUDE.md`.\n", plugin) == [
        "plugins/analitiq-nonexistent/CLAUDE.md"
    ]
    assert _dangling_in(f"See `plugins/{plugin}/skills/nowhere.md`.\n", plugin) == [
        f"plugins/{plugin}/skills/nowhere.md"
    ]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_resolving_citations_pass(plugin: str) -> None:
    """The twin: the same document citing specs that exist is clean."""
    existing, _heading = _fixture(plugin)
    twin = _SYNTHETIC_AGENT.replace(
        "skills/nowhere/references/gone.md", existing
    ).replace("skills/nowhere/references/also-gone.md", existing)
    # Both citations are seen (not skipped) …
    assert [target for _lineno, target in _scan_text(twin)].count(existing) == 2
    # … and resolve, so nothing dangles.
    assert _dangling_in(twin, plugin) == []


@pytest.mark.parametrize("plugin", _plugin_names())
def test_dangling_markdown_link_is_flagged(plugin: str) -> None:
    """Acceptance: a link target is resolved from the citing file's directory,
    so a `../` hop that lands nowhere fails while the same hop that lands on a
    real file passes. Written from a real document, since a relative path is
    only resolvable from a directory that exists."""
    citing, heading = _fixture(plugin)  # skills/<skill>/references/x.md
    assert _link_dangles("../nowhere/spec-z.md", "", citing, plugin)
    assert not _link_dangles("../../../CLAUDE.md", "", citing, plugin)
    # The fragment is held to the same standard as a `§` citation: the file
    # opens either way, the section is what the link claims. A fragment names
    # the whole heading, slugged — unlike `§`, which may abbreviate — so this
    # reads the heading off the file rather than using the fixture's short
    # citation form.
    own = Path(citing).name
    full = next(h for h in _headings((_plugin_root(plugin) / citing).read_text()))
    assert heading  # the fixture's own citation form, exercised by the `§` tests
    assert not _link_dangles(own, _slug(full), citing, plugin)
    assert _link_dangles(own, "section-that-was-renamed", citing, plugin)


@pytest.mark.parametrize("plugin", _plugin_names())
def test_dangling_anchor_in_a_resolving_file_is_flagged(plugin: str) -> None:
    """Acceptance: the half-dangling case. The file opens; the section named
    does not exist in it. The twin below pins that the same citation with the
    real heading passes, so this is the anchor doing the work, not the file
    check failing for its own reasons."""
    existing, _heading = _fixture(plugin)
    citing = "agents/synthetic-classifier.md"
    doc = f"Author per `{existing}` §Heading that was renamed away.\n"
    sites = [(citing, site) for site in _anchor_sites(doc)]
    dangling, checked = _anchor_checks(plugin, sites)
    assert dangling == [(citing, 1, existing, "Heading that was renamed away")]
    assert checked == 1
    # The file half is clean — this failure is the anchor's alone.
    assert _dangling_in(doc, plugin) == []


@pytest.mark.parametrize("plugin", _plugin_names())
def test_resolving_anchor_passes(plugin: str) -> None:
    """The twin: the same citation naming a heading that exists is clean."""
    existing, heading = _fixture(plugin)
    citing = "agents/synthetic-classifier.md"
    doc = f"Author per `{existing}` §{heading}, then stop.\n"
    sites = [(citing, site) for site in _anchor_sites(doc)]
    assert sites == [(citing, Anchor(1, existing, heading, quoted=False))]
    assert _anchor_checks(plugin, sites) == ([], 1)


@pytest.mark.parametrize("plugin", _plugin_names())
def test_ambiguous_citation_is_still_checked(plugin: str) -> None:
    """A basename several files answer to — `SKILL.md`, cited from `agents/`
    where no ancestor carries one — must not fall through unchecked. The file
    pass passes it (suffix match), so a skip here would leave nobody checking
    the section at all."""
    citing = "agents/synthetic-classifier.md"
    assert len(_resolve_files("SKILL.md", citing, plugin)) > 1
    doc = "Author per `SKILL.md §Heading no skill carries`.\n"
    sites = [(citing, site) for site in _anchor_sites(doc)]
    dangling, checked = _anchor_checks(plugin, sites)
    assert checked == 1
    assert [site[3] for site in dangling] == ["Heading no skill carries"]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_bare_anchor_binds_to_the_citing_document(plugin: str) -> None:
    """A `§` with no path in front of it cites a section of the document it
    sits in — the form `§Closed vocabularies` uses. Resolved against the citing
    file, a nonexistent section still fails."""
    rel, _heading = _fixture(plugin)
    own_heading = _headings((_plugin_root(plugin) / rel).read_text(encoding="utf-8"))[-1]
    good = [(rel, site) for site in _anchor_sites(f"See §{own_heading}.\n")]
    assert good and good[0][1].target is None
    assert _anchor_checks(plugin, good) == ([], 1)
    bad = [(rel, site) for site in _anchor_sites("See §Nowhere at all.\n")]
    assert [site[3] for site in _anchor_checks(plugin, bad)[0]] == ["Nowhere at all"]


def test_wrapped_anchor_is_read_whole() -> None:
    """An anchor that wraps a line is one citation, not a truncated one — the
    per-line scan the file pass uses would read `Dialect` and miss `hooks`."""
    text = "see `spec-connector-package.md` §Dialect\n  hooks). The engine\n"
    assert _anchor_sites(text) == [
        Anchor(1, "spec-connector-package.md", "Dialect\n  hooks", quoted=False)
    ]
    assert _anchor_resolves("Dialect\n  hooks", ["Dialect hooks"])


def test_quoted_anchor_keeps_its_own_punctuation() -> None:
    """Quoting is how a heading whose own punctuation the stop set would cut
    survives — the form `§ "Fenced JSON examples — the annotation convention"`
    uses. Unquoted, the em-dash would end the anchor early."""
    quoted = _anchor_text(' "Rules, exceptions — and limits" follow.\n')
    assert quoted == "Rules, exceptions — and limits"
    assert _anchor_text(" Rules, exceptions — and limits follow.\n") == "Rules"


def test_a_heading_inside_a_fence_is_not_a_section() -> None:
    """`# Encoding values` in a shell or python example is a comment. Treating
    it as a heading would resolve citations of a section that does not
    exist."""
    doc = "# Real\n\n```python\n# Encoding values\n```\n"
    assert _headings(doc) == ["Real"]
    assert not _anchor_resolves("Encoding values", _headings(doc))


def test_a_one_word_heading_does_not_swallow_a_renamed_section() -> None:
    """A file carrying `## Output` must not answer for `§Output contract`,
    whose section was renamed — one-word headings are everywhere (`Rules`,
    `Shape`, `Modes`), and each would otherwise absorb every anchor starting
    with its word."""
    assert not _anchor_resolves("Output contract", ["Output", "Inputs to collect"])
    assert _anchor_resolves("Output", ["Output", "Inputs to collect"])
    # Two words is enough to be a citation rather than a coincidence.
    assert _anchor_resolves("Import rules owns the list", ["Import rules"])


def test_a_citation_may_abbreviate_and_run_on_at_once() -> None:
    """The two things prose does to a heading happen in one sentence: name its
    opening words (not all of them) and keep going into the sentence. A rule
    that allowed only one at a time failed `§Cross-field rules for the exact
    tuple` against `## Cross-field rules the contract enforces` — a citation
    that is exactly right."""
    assert _anchor_resolves(
        "Cross-field rules for the exact tuple",
        ["Cross-field rules the contract enforces"],
    )
    # What still fails is the rename: a word changed *inside* the opening.
    assert not _anchor_resolves(
        "Cross-field rules for the exact tuple",
        ["Cross-document rules the contract enforces"],
    )


def test_a_quoted_anchor_is_held_to_the_whole_heading() -> None:
    """Quoting claims the heading verbatim, so it is graded verbatim. That is
    what catches a rename in the *middle* of a long heading — two shared
    opening words is all an unquoted citation ever promises, so the run-on rule
    would let `Fenced JSON snippets …` answer for `Fenced JSON examples …`."""
    cited = "Fenced JSON examples — the annotation convention"
    renamed = ["Fenced JSON snippets — the annotation convention"]
    assert _anchor_resolves(cited, renamed)  # unquoted: two opening words match
    assert not _anchor_resolves(cited, renamed, exact=True)
    assert _anchor_resolves(
        cited, ["Fenced JSON examples — the annotation convention"], exact=True
    )
    # And the flag comes from the prose, not from the caller.
    quoted, unquoted = _anchor_sites('§ "Shape" and §Shape of it')
    assert (quoted.quoted, quoted.text) == (True, "Shape")
    assert (unquoted.quoted, unquoted.text) == (False, "Shape of it")


def test_a_comma_or_dash_still_binds_the_anchor_to_its_file() -> None:
    """Glue between the path and the `§` is punctuation prose uses freely. A
    binding that broke on a comma would not merely lose the check — it would
    resolve the anchor against the citing document and report a section of the
    wrong file as missing."""
    for glue in ("` ", "`, ", "` — ", "`\n  "):
        text = f"see `SKILL.md{glue}§Cross-field rules for the tuple."
        assert _anchor_sites(text)[0][1] == "SKILL.md", glue
    # A sentence-final period is not glue: the clause naming the file ended, so
    # what follows is a citation of the document being read.
    assert _anchor_sites("see `SKILL.md`. §Closed vocabularies.")[0][1] is None


def test_anchored_forms_are_not_double_counted() -> None:
    """A line carrying a backticked citation and a ${CLAUDE_PLUGIN_ROOT}
    citation yields exactly one match per target: the bare-path pattern's
    lookbehind must not re-match the path tail inside either anchored form,
    and a `§` citation must not be reported once per extractor that sees it."""
    line = (
        "Read ${CLAUDE_PLUGIN_ROOT}/skills/connector-spec-db/spec-tls.md "
        "and `references/io-contracts.md`."
    )
    targets = [target for _lineno, target in _scan_text(line)]
    assert targets.count("skills/connector-spec-db/spec-tls.md") == 1
    assert targets.count("references/io-contracts.md") == 1
    assert len(targets) == 2
    anchored = [target for _lineno, target in _scan_text("See `spec-tls.md` §Shape.")]
    assert anchored == ["spec-tls.md"]
    # A link target belongs to the link pass alone: matched here too, one
    # broken link would fail two tests and read as two breaks.
    assert _scan_text("See [envelope](skills/gone/spec-envelope.md).") == []
