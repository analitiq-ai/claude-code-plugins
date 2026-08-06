"""Every citation a plugin's prose makes resolves to something that exists.

Agent prose routes an agent to its rules by filename, and to the part of that
file that carries the rule by section. A rename, a move, or a deleted file
leaves the citation dangling — and an agent that cannot read what it was sent
to read does not fail loudly, it authors without the rules. Nothing else in the
suite notices: these are strings in markdown.

Two claims, both checked, for **every** plugin under `plugins/`. The roots are
discovered rather than listed, so a plugin cannot land unnoticed: until it has
entries in the three per-plugin registries below, this suite is red.

1. **The file exists.** Six citation forms carry it:

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
     since that is what a link means. A link may leave the plugin only from
     the plugin's own README, a page a reader browses in the repo; from a
     skill or agent document, read out of an installed plugin cache where repo
     files do not exist, a link out of the tree dangles. A link's `#fragment`
     is checked as a section, below.

   - Paths with any other extension, wherever prose puts them — filling a code
     span (`` `examples/api-key/api-key.example.json` ``), sharing one with the
     flags a script runs with, bare in a frontmatter description, bare in a
     fenced command line — when the citation's leading segment names a
     directory this plugin has. That last clause is the whole rule: it tells a
     file of this plugin from `definition/connector.json`, which is what the
     connector *author* writes, and from `connection/latest.json` or
     `America/New_York`, which are not files at all. Delimiters are not part of
     it; requiring one hid the script a plugin's own contributor guidance tells
     you to run. An example an agent is told to copy starves it exactly as a
     missing spec does.

   What is **not** checked, each a decision rather than an oversight:

   - A path with no directory segment, and a path whose leading segment names
     no directory of this plugin. The first is indistinguishable from an
     ordinary prose word; the second is addressing something else — an
     authored artifact, a schema URL's tail, a timezone.
   - A same-document link, `](#a-heading)`. No plugin writes one; adding an
     extractor for a form with no sites would be a pattern nothing can floor,
     which is the shape of a guard that dies without anyone noticing.
   - A link whose target carries a URL scheme. `](https://…/docs/adr.md)`
     names a file in another repo, which this one cannot open — resolving it
     relative to the citing document would report every such link dangling.
   - `CHANGELOG.md`. release-please generates it from commit subjects, which
     in this repo carry both `§` and `.md` paths, so a release entry naming a
     since-renamed spec would fail the build on text no author can correct:
     hand-editing a generated file is undone by the next release. It is not
     prose any agent reads. A citation *of* the changelog still resolves — it
     is excluded as a source of citations, not from the path universe.

2. **The section exists.** A `path.md §Heading` citation, and a link's
   `#fragment`, make a second claim the file check never opens: that the
   heading is still there. A heading rename leaves the citation
   half-dangling — the file opens, the section the agent was sent to read is
   gone. `§` with no file in front of it cites a section of the citing
   document itself, and is resolved against it. The two forms differ in how
   exactly they name the heading: prose abbreviates and runs on, so `§` is
   matched by opening words; a fragment is generated from the whole heading,
   so it is matched by slug. Fences are not an exemption. Real *path*
   citations do sit inside fenced examples — a mission spec quoting the paths
   its researcher must read, an agent's command line naming the script it
   runs — so the file pass reads fenced lines. No `§` is fenced today; grading
   fenced ones too is the symmetric decision rather than an observed need, and
   the alternative was worse: an exemption that graded nothing while silently
   dropping the file half of a fenced `` `SKILL.md §Heading` ``. What a fence
   *does* suppress is a `#` line being read as a heading — that is a markdown
   comment in someone's code sample, not a section anyone can cite.
   The cost of grading every `§` is that one which is not a plugin-section
   citation at all — an RFC clause, say — has to be written another way; the
   failure message says so.

Pure text-vs-filesystem: no contract packages involved, so no `_pins` skip
guard — this always runs.
"""

from __future__ import annotations

import re
import sys
from bisect import bisect_right
from collections import Counter
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
_BARE_REF = re.compile(r"`((?:\.{1,2}/)*(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.md)`")

# An unbackticked `.md` path, matched on every line. At least one directory
# segment is required: a bare filename with no slash is indistinguishable
# from an ordinary prose word. That requirement is what keeps prose from
# matching — applied to every line of a plugin every match today is a genuine
# citation. The lookbehind rejects starts preceded by a backtick (that is
# `_BARE_REF`'s form) or by a path character, so the tail of a
# `${CLAUDE_PLUGIN_ROOT}/…` reference is not re-matched. The directory-segment
# charset mirrors `_BARE_REF`'s, and both spell the `./` and `../` prefixes a
# sibling-skill citation uses — a citation the reader resolves from the
# document it sits in, and so does `_candidates`. The last two lookbehinds hand
# a link target to the link pass alone: without them `](skills/x/y.md)` and its
# angle-bracket spelling `](<…>)` match here too, and one broken link fails two
# tests. It takes two because a lookbehind is fixed-width.
_BARE_PATH_REF = re.compile(
    r"(?<![\w`./-])(?<!\]\()(?<!\]\(<)"
    r"((?:\.{1,2}/)*(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+\.md)(?![\w-])"
)

_PATH_PATTERNS = (_PLUGIN_ROOT_REF, _BARE_REF, _BARE_PATH_REF)

# A path with any other extension, wherever it is written: filling a code span
# (`` `scripts/endpoint_id.py` ``), sharing one with its flags
# (`` `scripts/type_map_gaps.py --direction read` ``), bare in a frontmatter
# description, or bare in a fenced command line. Delimiters are not the rule
# here — `_addresses_this_plugin` is — because most such paths in prose are
# artifacts the *author* writes (`definition/connector.json`) or addresses
# elsewhere entirely (`connection/latest.json`, `America/New_York`). Requiring
# the path to fill a backtick span looked like a rule and was really a
# delimiter, and it left the script this plugin's own CLAUDE.md tells a
# contributor to run twice read by nobody.
# The lookbehind does two jobs. It admits a preceding backtick — unlike
# `_BARE_PATH_REF`, there is no separate backticked asset pattern to defer to,
# so excluding it would drop every `` `scripts/endpoint_id.py` `` in the tree —
# and it refuses a start preceded by a path character, which is what keeps the
# tail of a `${CLAUDE_PLUGIN_ROOT}/scripts/x.py` reference from being read a
# second time as a bare citation, and keeps every interior position of a URL
# out. The directory-segment charset carries `.`, so `.claude-plugin/
# plugin.json` is reachable and a `./`- or `../`-prefixed hop needs no separate
# alternative.
_BARE_ASSET_REF = re.compile(
    r"(?<![\w./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]+)(?![\w-])"
)

# A markdown link target and its optional fragment: `](spec-columns.md)`,
# `](../endpoint-spec/x.md)`, `](x.md#uniqueness)`. Kept apart from the
# patterns above because it resolves differently — relative to the citing file,
# and it may point outside the plugin, which the READMEs do. The fragment is
# captured, not discarded: it is the same claim a `§` citation makes, and
# leaving it unread would close the section-citation hole in one form while
# leaving it open in the other.
# The leading lookahead drops anything with a URL scheme: an engine ADR linked
# as `https://…/docs/sql-write-path-v2.md` is a file this repo cannot open, and
# resolving it relative to the citing document would report every such link
# dangling.
_LINK_REF = re.compile(
    r"\]\((?!\w+:)<?([^)\s#<>]+\.md)>?(#[^)\s]*)?(?:\s+[\"'(][^)]*)?\)"
)

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
# A blank line is not glue, however few characters it spends: the paragraph
# that named the file ended, so a `§` opening the next one is document-local.
# Dashes are the typographic ones only — an ASCII `-` is how a list item
# starts, and `- §Rules` under `- \`SKILL.md\`` is a new item, not a
# continuation of the last one.
# Emphasis markers are glue too — a citation is written `**`spec-x.md`**`
# — and a wrapped line inside a blockquote carries its `>` before the text
# resumes. Both already sit around `.md` citations in the tree, so leaving
# them out means re-wrapping a paragraph re-points its anchor at the citing
# document and fails the build naming a file the citation never mentioned.
_ANCHOR_BINDING = re.compile(
    r"([A-Za-z0-9_./-]+\.md)(?:[`\"'(,—–*_:]|[ \t]|\n[ \t]*>?(?![ \t]*\n)){0,8}$"
)

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
    "analitiq-pipeline-builder": {
        # A repo-root script, cited from this plugin's contributor guidance
        # because it is what renders the validator-behaviour claims. It lives
        # in the repo, not in the installed plugin.
        "scripts/render_validator_claims.py",
    },
}

# Every citation form this suite extracts, and the extractor that finds it.
# `anchor` has no single pattern — it is counted by what the anchor pass
# actually graded — so it is named in `_FORMS` and absent here. A form missing
# from the floors below would be unfloored and free to die silently, so
# `_REPO_FLOORS` must name them all; `test_every_plugin_is_covered` checks that.
_FORM_PATTERNS = {
    "plugin_root": _PLUGIN_ROOT_REF,
    "backticked": _BARE_REF,
    "bare_path": _BARE_PATH_REF,
    "link": _LINK_REF,
    "asset": _BARE_ASSET_REF,
}
_FORMS = (*_FORM_PATTERNS, "anchor")

# The forms `_scan_sites` reads line by line. `link` and `asset` have their own
# sweeps — one resolves through the citing document, the other filters by the
# plugin's directory vocabulary — so they are counted from those.
_SCAN_FORMS = ("plugin_root", "backticked", "bare_path")

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
    "asset": 15,
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
        "asset": 7,
        "anchor": 15,
    },
    "analitiq-pipeline-builder": {
        "plugin_root": 2,
        "backticked": 50,
        "link": 3,
        "asset": 7,
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
# not a silent pass. Written at full plugin-relative depth even where the prose
# cites the file more shortly: the check compares the *file* each side resolves
# to. Where the prose writes a path at some depth, the sentinel is written at
# another, so a comparison that rotted back into string equality would fail;
# the `backticked` sentinels are bare basenames because that is the only way
# that form is ever written.
#
# `fixture` is a real file plus the opening words of a heading it carries (the
# citation form the prose uses — `## Release version (`version`)` is cited as
# `§Release version`), so the acceptance tests dangle one citation against a
# document that genuinely exists. It must also carry one three-word,
# punctuation-free heading, which the quoted-anchor test renames past.
#
# `unsentinelled` waives a form this plugin writes but cannot pin — with the
# reason, which is itself checked: a waived form cited anywhere but the
# plugin's README fails, since that citation routes an agent and belongs in
# `sentinels` instead. The key is required, `{}` when nothing is waived, so a
# plugin cannot arrive with the question unanswered.
_PLUGIN_FIXTURES: dict[str, dict[str, object]] = {
    "analitiq-connector-builder": {
        "sentinels": {
            "plugin_root": "skills/connector-spec-db/spec-connector-package.md",
            "bare_path": "skills/connector-builder/references/metadata-and-versioning.md",
            "backticked": "spec-sql-write-path.md",
            # The archetype an API creator copies, cited by two specs.
            "asset": "skills/connector-spec-api/examples/api-key/api-key.example.json",
        },
        "fixture": (
            "skills/connector-builder/references/metadata-and-versioning.md",
            "Release version",
        ),
        # This plugin writes exactly one markdown link, in its README, pointing
        # at the repo README. It routes no agent, so there is no routing
        # citation to pin — the repo-wide link floor is what guards the form.
        "unsentinelled": {"link": "one link, in the README, routing nobody"},
    },
    "analitiq-pipeline-builder": {
        "sentinels": {
            "plugin_root": "scripts/validate.py",
            "bare_path": "skills/pipeline-builder/references/io-contracts.md",
            "backticked": "spec-database-object.md",
            # The one link in either plugin that routes an agent rather than a
            # reader: a stream spec sending its author to the column spec.
            "link": "skills/endpoint-spec/spec-columns.md",
            # The script six documents tell an agent to run for derived ids.
            "asset": "scripts/endpoint_id.py",
        },
        "fixture": (
            "skills/pipeline-builder/references/identity-and-versioning.md",
            "Metadata fields",
        ),
        "unsentinelled": {},
    },
}


def _sentinels(plugin: str) -> dict[str, str]:
    return _PLUGIN_FIXTURES[plugin]["sentinels"]  # type: ignore[return-value]


def _fixture(plugin: str) -> tuple[str, str]:
    return _PLUGIN_FIXTURES[plugin]["fixture"]  # type: ignore[return-value]


def _unsentinelled(plugin: str) -> dict[str, str]:
    """Forms this plugin declares it cannot pin with a routing citation, each
    with the reason. A declared, reviewable state rather than a form quietly
    missing from `sentinels`."""
    return _PLUGIN_FIXTURES[plugin]["unsentinelled"]  # type: ignore[return-value]


def _plugin_root(plugin: str) -> Path:
    return PLUGINS_DIR / plugin


# Generated, not authored: release-please writes `CHANGELOG.md` from commit
# subjects, and this repo's subjects carry both `§` and `.md` paths — including
# paths that were renamed after the commit landed. Sweeping it would fail the
# build on text no author can correct, since the next release regenerates
# whatever was hand-edited. Every other `.md` under a plugin is authored prose.
_GENERATED_PROSE = {"CHANGELOG.md"}


def _prose_files(plugin: str) -> list[Path]:
    """Every authored markdown document in the plugin — what an agent reads,
    and the only text this guard grades."""
    return [
        path
        for path in sorted(_plugin_root(plugin).rglob("*.md"))
        if path.name not in _GENERATED_PROSE
    ]


# Build artifacts a local checkout grows and an installed plugin never ships.
# They must not enter the path universe: a citation resolving against a
# `__pycache__` entry would pass here and dangle in CI, which is the one place
# a reference guard cannot afford to disagree with itself.
_NOT_SHIPPED = ("__pycache__",)


def _ships(relative: str) -> bool:
    """Would an installed plugin carry this path? A predicate rather than a
    comprehension inside the sweep, because the sweep can only be asked about
    a checkout that happens to contain the artifact — and whether it does
    depends on whether another suite imported a plugin script first, which is
    the local-vs-CI disagreement this exists to remove."""
    return not any(part in _NOT_SHIPPED for part in Path(relative).parts)


@cache
def _plugin_paths(plugin: str) -> tuple[str, ...]:
    """Every file and directory the plugin ships, as plugin-root-relative posix
    paths. Every file, not only `.md`: agent frontmatter cites the helper
    scripts it runs by the same `${CLAUDE_PLUGIN_ROOT}/…` form, and a citation
    of a deleted script starves an agent exactly as a citation of a deleted
    spec does."""
    return _paths_under(_plugin_root(plugin))


def _paths_under(root: Path) -> tuple[str, ...]:
    """The sweep, over a root the caller names. Split out so a test can build
    a tree containing a build artifact and prove the sweep drops it: asking the
    real checkout instead only works when some earlier suite happened to import
    a plugin script, which is test-execution order deciding whether a guard
    guards."""
    return tuple(
        rel
        for path in sorted(root.rglob("*"))
        for rel in [path.relative_to(root).as_posix()]
        if _ships(rel)
    )


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

    Three refinements on top:

    - A citation that spells out `plugins/<name>/…` is fully qualified, may
      name a sibling plugin, and is matched exactly against the repo tree.
    - A `./`- or `../`-prefixed citation is relative by construction: a reader
      resolves it from the document it sits in, so this does too, and refuses
      to leave the plugin the way the rest of the rule does.
    - Given the citing document, the nearest ancestor directory holding the
      path wins alone: `SKILL.md` cited from
      `skills/pipeline-builder/references/pipeline.md` is that skill's own
      `SKILL.md`, not another skill's. Without that, a basename four or five
      files answer to returns all of them — which the anchor pass wants (an
      anchor checked against every candidate is imprecise about *which* file it
      read; an anchor checked against none is unchecked).
    """
    cleaned = _clean(target)
    # Before any branch: a path an installed plugin does not carry resolves to
    # nothing, however it is spelled. Filtering only the path universe left the
    # three `.exists()` branches below answering from the checkout — and CI
    # grows `__pycache__` itself, so a citation of one passed there too.
    if not _ships(cleaned):
        return []
    if cleaned.startswith("plugins/"):
        candidate = REPO_ROOT / cleaned
        return [candidate] if candidate.exists() else []
    root = _plugin_root(plugin)
    if cleaned.startswith(("./", "../")):
        if citing is None:
            return []
        candidate = (root / citing).parent / cleaned
        inside = candidate.resolve().is_relative_to(root.resolve())
        return [candidate] if inside and candidate.exists() else []
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


def _scan_sites(text: str) -> list[tuple[int, str, str]]:
    """Every (lineno, form, target) path citation in one document's text — the
    three path patterns on every line, plus the path half of every `§`
    citation.

    De-duplicated per line and target: `` `spec-x.md` §Foo `` is seen by the
    backticked pattern *and* by the anchor binding, and one broken citation
    must read as one finding. `` `SKILL.md §Pipeline` `` is the opposite case —
    the anchor binding is the only extractor that sees it, which is why that
    half of the sweep exists.

    Form-tagged so the floors can count what this sweep found rather than
    re-running the patterns themselves: a floor that re-scans cannot notice
    the sweep going blind.
    """
    from_paths = [
        (lineno, form, match.group(1))
        for lineno, line in enumerate(text.splitlines(), 1)
        for form, pattern in _FORM_PATTERNS.items()
        if form in _SCAN_FORMS
        for match in pattern.finditer(line)
    ]
    from_anchors = [
        (site.lineno, "anchor_path", site.target)
        for site in _anchor_sites(text)
        if site.target
    ]
    seen, sites = set(), []
    for lineno, form, target in from_paths + from_anchors:
        if (lineno, target) in seen:
            continue
        seen.add((lineno, target))
        sites.append((lineno, form, target))
    return sites


def _scan_text(text: str) -> list[tuple[int, str]]:
    """`_scan_sites` without the form tag — what the file pass grades."""
    return [(lineno, target) for lineno, _form, target in _scan_sites(text)]


class Anchor(NamedTuple):
    """One `§` citation: where it sits, the file it binds to (`None` for a
    citation of the document it is written in), the heading it names, whether
    the author quoted that heading, and where in the document the bound path
    was written.

    `path_span` is what the census reads. Re-deriving it from
    `_ANCHOR_BINDING` per line does not work and is not a near miss: the
    pattern ends in `$`, which means *immediately before a `§`* when the pass
    searches `text[:marker.start()]` and *end of line* when anything else runs
    it per line. Those are unrelated positions, so the only honest source of
    what the anchor pass read is the pass.
    """

    lineno: int
    target: str | None
    text: str
    quoted: bool
    path_span: tuple[int, int] | None = None


def _anchor_sites(text: str) -> list[Anchor]:
    """Every `§` citation in one document.

    Scanned over the whole text, not line by line: a citation that wraps —
    ``§Dialect\\n  hooks)`` — is one citation, and a per-line scan would read
    half of it.
    """
    sites: list[Anchor] = []
    for marker in re.finditer("§", text):
        rest = text[marker.end() :]
        binding = _ANCHOR_BINDING.search(text[: marker.start()])
        sites.append(
            Anchor(
                lineno=text.count("\n", 0, marker.start()) + 1,
                target=binding.group(1) if binding else None,
                text=_anchor_text(rest),
                quoted=_QUOTED_ANCHOR.match(rest) is not None,
                path_span=binding.span(1) if binding else None,
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


def _fenced_lines(text: str) -> set[int]:
    """The 1-based line numbers inside a fenced block, the fence lines
    included. One contract, and only one: a `#` line in a fence is a comment in
    someone's code sample, not a section anyone can cite. Citations are *not*
    exempted — a fenced `§` is graded like any other, because fenced examples
    are where this repo's mission specs quote the paths a researcher reads."""
    fenced, inside = set(), False
    for lineno, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            inside = not inside
            fenced.add(lineno)
        elif inside:
            fenced.add(lineno)
    return fenced


def _headings(text: str) -> list[str]:
    """Every ATX heading in a document, fenced blocks excluded — a `# comment`
    inside a fenced example is not a section anyone can cite."""
    fenced = _fenced_lines(text)
    return [
        match.group(2)
        for lineno, line in enumerate(text.splitlines(), 1)
        if lineno not in fenced and (match := _HEADING.match(line))
    ]


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


@cache
def _plugin_dirs(plugin: str) -> frozenset[str]:
    """Every directory name the plugin contains, at any depth — the vocabulary
    that tells a citation of a plugin file from a path about something else."""
    root = _plugin_root(plugin)
    return frozenset(
        Path(path).name for path in _plugin_paths(plugin) if (root / path).is_dir()
    )


def _asset_citations(plugin: str) -> list[tuple[str, int, str]]:
    """Non-`.md` citations that name a file of *this plugin*, however written.

    Prose writes two very different things in this shape. `examples/api-key/
    api-key.example.json` and `scripts/endpoint_id.py` are files an agent is
    sent to read, and renaming one starves it exactly as a missing spec does.
    `definition/connector.json`, `.secrets/credentials.json`,
    `connection/latest.json`, `America/New_York` are not files of this plugin
    at all — they are what the *author* writes, a schema URL's tail, a
    timezone.

    The discriminator is the plugin's own directory vocabulary: a citation
    whose leading segment names a directory this plugin has is addressing this
    plugin. Measured over every non-`.md` path the extractor matches in both
    plugins, that separates them with one exception — a repo-root script cited
    from plugin prose, which is what `_EXTERNAL_REFS` is for. Delimiters play
    no part: a path is as much a citation in a fenced command line as in a
    code span, and treating the backticks as the rule left a real citation
    unread.
    """
    root = _plugin_root(plugin)
    return [
        (rel, lineno, target)
        for path in _prose_files(plugin)
        for rel in [path.relative_to(root).as_posix()]
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for match in _BARE_ASSET_REF.finditer(line)
        for target in [match.group(1)]
        if _addresses_this_plugin(target, plugin)
    ]


def _addresses_this_plugin(target: str, plugin: str) -> bool:
    """Is this path a file of this plugin, or something else the prose merely
    names? The one filter — the sweep and its tests read it, so they cannot
    disagree about what an asset citation is."""
    if target.endswith(".md"):
        return False  # the `.md` forms already read it
    cleaned = _clean(target)
    if cleaned.startswith("plugins/"):
        return True  # fully qualified: `_candidates` matches it exactly
    # The first real segment, past any `./` or `../` hops — and `.` alone is a
    # hop while `.claude-plugin` is a directory, so this cannot be a strip.
    segments = [part for part in cleaned.split("/") if part not in ("", ".", "..")]
    return bool(segments) and segments[0] in _plugin_dirs(plugin)


def _tagged_references(plugin: str) -> list[tuple[str, int, str, str]]:
    """Every (relpath, lineno, form, target) path citation the line sweep
    finds — the sweep the file pass grades, with the form each citation came
    from, so the floors can count it."""
    root = _plugin_root(plugin)
    return [
        (path.relative_to(root).as_posix(), lineno, form, target)
        for path in _prose_files(plugin)
        for lineno, form, target in _scan_sites(path.read_text(encoding="utf-8"))
    ]


def _references(plugin: str) -> list[tuple[str, int, str]]:
    """Every (relpath, lineno, target) path citation in the plugin."""
    return [
        (rel, lineno, target)
        for rel, lineno, _form, target in _tagged_references(plugin)
    ] + _asset_citations(plugin)


def _links_in(text: str) -> list[tuple[int, str, str]]:
    """Every (lineno, target, fragment) markdown link in one document's text.
    The fragment is `""` when the link names no section — a text-level helper,
    like `_scan_text`, so a synthetic document can drive the extraction end to
    end. No link in either plugin carries a fragment today, so this is the only
    place the capture is exercised at all."""
    return [
        (lineno, match.group(1), (match.group(2) or "").lstrip("#"))
        for lineno, line in enumerate(text.splitlines(), 1)
        for match in _LINK_REF.finditer(line)
    ]


def _link_references(plugin: str) -> list[tuple[str, int, str, str]]:
    """Every (relpath, lineno, target, fragment) markdown-link citation."""
    root = _plugin_root(plugin)
    return [
        (path.relative_to(root).as_posix(), lineno, target, fragment)
        for path in _prose_files(plugin)
        for lineno, target, fragment in _links_in(path.read_text(encoding="utf-8"))
    ]


def _anchor_references(plugin: str) -> list[tuple[str, Anchor]]:
    """Every (relpath, anchor) `§` citation in the plugin."""
    root = _plugin_root(plugin)
    return [
        (path.relative_to(root).as_posix(), site)
        for path in _prose_files(plugin)
        for site in _anchor_sites(path.read_text(encoding="utf-8"))
    ]


def _is_dangling(target: str, plugin: str, citing: str | None = None) -> bool:
    """The one exemption-and-resolution predicate: a citation dangles unless it
    is allow-listed as deliberately external or names something that exists.
    Both the real-tree sweep and the synthetic acceptance tests go through this,
    so the acceptance tests exercise the exemption logic that ships.

    `citing` is what a `../`-prefixed citation is relative to; without it such a
    citation resolves to nothing, which is why the sweep always passes it."""
    return _clean(target) not in _EXTERNAL_REFS[plugin] and not _candidates(
        target, plugin, citing
    )


def _slug(heading: str) -> str:
    """A heading as the fragment that links to it — case folded, punctuation
    dropped, spaces hyphenated."""
    return _SLUG_DROP.sub("", heading.lower()).strip().replace(" ", "-")


def _link_dangles(target: str, fragment: str, citing: str, plugin: str) -> bool:
    """Does a markdown link point at something that is not there?

    The file resolves relative to the document the link is written in — that is
    what a link means. It may leave the plugin only from a plugin-root README,
    which is a page a reader browses in the repo; a skill or agent document is
    read out of an installed plugin cache, where a repo file does not exist, so
    a link out of the tree from there dangles for the same reason
    `_candidates` refuses to walk past the plugin root.

    A fragment is the same claim a `§` citation makes, so it is held to the
    same standard: the heading it slugs to must exist in the file the link
    opens.
    """
    root = _plugin_root(plugin)
    path = (root / citing).parent / target
    if not path.is_file():
        return True
    if not path.resolve().is_relative_to(root.resolve()) and citing != "README.md":
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
        # An allow-listed external target needs no exemption here: it names no
        # file in this plugin, so it resolves to nothing and falls out below
        # with every other unreadable target.
        #
        # No path in front of the `§`: the citation names a section of the
        # document it sits in.
        candidates = (
            _resolve_files(site.target, rel, plugin)
            if site.target
            else [_plugin_root(plugin) / rel]
        )
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
        if _is_dangling(target, plugin, rel)
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
        "the heading. A citation must name the heading's opening words — at "
        "least two of them, so prose may run on past a multi-word heading but "
        "a one-word heading has to end the citation (`§Process, and then …`, "
        "or quote it). A paraphrase never resolves. And if the `§` is not a "
        "citation of a section in this plugin at all — an RFC clause, a "
        "statute — spell the word 'section' instead: every `§` in plugin prose "
        "is read as a citation."
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


# The registry checks, as predicates rather than expressions inlined in the
# assertion. Every one of them only ever runs against a tree that must not trip
# it, so each would be satisfied by a constant — the vacuity `_below_floor` had
# until it got a failing-direction test, in eight more places. Extracted, they
# are testable, and `test_the_registry_checks_fail_when_they_should` drives
# each one until it fires.
_REQUIRED_FIXTURE_KEYS = ("sentinels", "fixture", "unsentinelled")


def _incomplete_fixtures(fixtures: dict[str, dict]) -> dict[str, list[str]]:
    """Fixture entries that leave a required question unanswered."""
    return {
        plugin: sorted(set(_REQUIRED_FIXTURE_KEYS) - set(entry))
        for plugin, entry in fixtures.items()
        if set(_REQUIRED_FIXTURE_KEYS) - set(entry)
    }


def _unfloored_forms(forms: tuple[str, ...], repo_floors: dict[str, int]) -> list[str]:
    """Forms no repo-wide floor names — free to stop matching silently."""
    return sorted(set(forms) - set(repo_floors))


def _unknown_floor_forms(
    floors: dict[str, dict[str, int]], forms: tuple[str, ...]
) -> list[str]:
    """Floors on forms nobody counts, which can therefore never fail."""
    return sorted({form for plugin in floors.values() for form in plugin} - set(forms))


def _unpinned_forms(sentinels: dict[str, str], waived: dict[str, str]) -> list[str]:
    """Forms a plugin neither pins with a sentinel nor waives with a reason."""
    return sorted(set(_FORM_PATTERNS) - set(sentinels) - set(waived))


def _stale_waivers(sentinels: dict[str, str], waived: dict[str, str]) -> list[str]:
    """Waivers naming a form the plugin does pin after all."""
    return sorted(set(waived) & set(sentinels))


def _unreachable_preemptive(
    preemptive: dict[str, str], reachable: set[str]
) -> list[str]:
    """Pre-emptive entries naming a disposition no branch returns — config
    that exempts nothing, in the registry whose point is that an exemption is
    declared and reviewable."""
    return sorted(set(preemptive) - reachable)


def _falsified_waivers(plugin: str) -> dict[str, list[str]]:
    """Waivers the prose contradicts: the waiver says the form routes nobody,
    and the form is cited outside the plugin's reader-facing README."""
    return {
        form: outside
        for form in _unsentinelled(plugin)
        if (
            outside := sorted(
                {rel for rel, _target in _form_sites(plugin, form)} - {"README.md"}
            )
        )
    }


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
    incomplete = _incomplete_fixtures(_PLUGIN_FIXTURES)
    assert not incomplete, (
        f"_PLUGIN_FIXTURES entries missing required keys: {incomplete} — every "
        "plugin answers all three, `unsentinelled` with `{}` when it waives "
        "nothing, so the question is never left open."
    )
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
    unfloored = _unfloored_forms(_FORMS, _REPO_FLOORS)
    assert not unfloored, (
        f"citation forms with no repo-wide floor: {unfloored} — an unfloored "
        "form is free to stop matching without failing anything."
    )
    unknown = _unknown_floor_forms(_FLOORS, _FORMS)
    assert not unknown, (
        f"per-plugin floors name forms the extractor does not produce: "
        f"{unknown} — a floor on a form nobody counts never fails."
    )
    # Sentinels are per form too: a form with no sentinel is pinned by its
    # count alone, which an over-matching pattern satisfies while losing the
    # citation that mattered.
    unpinned = {
        plugin: forms
        for plugin in _plugin_names()
        if (forms := _unpinned_forms(_sentinels(plugin), _unsentinelled(plugin)))
    }
    assert not unpinned, (
        f"citation forms neither pinned nor waived: {unpinned} — name one real "
        "routing citation per form, so the extractor stays pinned to prose an "
        "agent actually follows, or record in `unsentinelled` why this plugin "
        "has no such citation. (`anchor` never reaches this check, having no "
        "pattern of its own; its floor counts what the pass graded.)"
    )
    waived_but_pinned = {
        plugin: forms
        for plugin in _plugin_names()
        if (forms := _stale_waivers(_sentinels(plugin), _unsentinelled(plugin)))
    }
    assert not waived_but_pinned, (
        f"forms both waived and pinned: {waived_but_pinned} — a waiver that "
        "names a form the plugin does pin is stale; drop it."
    )
    # A waiver says the form routes nobody. That claim is checkable: a citation
    # of the form outside the plugin's reader-facing README routes an agent,
    # and the waiver has stopped being true. Without this the reason is prose
    # nobody grades — the failure the census pattern in `.claude/CLAUDE.md`
    # exists to prevent.
    falsified = {
        plugin: forms
        for plugin in _plugin_names()
        if (forms := _falsified_waivers(plugin))
    }
    assert not falsified, (
        f"waivers contradicted by the prose: {falsified} — the waiver says the "
        "form routes nobody, but it is cited outside the README. Pin one of "
        "those citations as a sentinel and drop the waiver."
    )


def _form_counts(plugin: str) -> dict[str, int]:
    """How many citations each extractor finds in a plugin, per form. `anchor`
    is the number compared against a target's headings, not the number of `§`
    characters, so a pass that finds anchors and grades none of them is not
    counted as working."""
    texts = [
        path.read_text(encoding="utf-8")
        for path in _prose_files(plugin)
    ]
    assert texts  # a plugin with no prose is not a plugin
    # Counted from the sweeps that grade, never by re-running the patterns: a
    # floor that re-scans the tree cannot notice the sweep going blind, which
    # is the one failure a floor exists to make loud.
    scanned = Counter(form for _rel, _lineno, form, _t in _tagged_references(plugin))
    _dangling, checked = _anchor_checks(plugin, _anchor_references(plugin))
    return (
        {form: scanned.get(form, 0) for form in _SCAN_FORMS}
        | {"link": len(_link_references(plugin))}
        # `asset` counts what the filter kept, not what the pattern matched:
        # the pattern also sees paths this plugin does not own, and a floor on
        # those would be met by prose about `definition/connector.json`.
        | {"asset": len(_asset_citations(plugin))}
        | {"anchor": checked}
    )


def _unreached_sentinels(plugin: str, sentinels: dict[str, str]) -> dict[str, str]:
    """Which sentinels the extractor named by their form no longer reaches,
    compared by resolved file rather than by the string as written."""
    return {
        form: sentinel
        for form, sentinel in sentinels.items()
        if not {path.resolve() for path in _candidates(sentinel, plugin)}
        & _files_reached_by(plugin, form)
    }


def _files_reached_by(plugin: str, form: str) -> set[Path]:
    """Every file one extractor's citations resolve to. Links resolve from the
    document they are written in, so they are read back with their citing file
    rather than through the one-argument `_candidates` call the other forms
    use — it resolves `../` only when told which document to resolve from."""
    if form == "link":
        return {
            ((_plugin_root(plugin) / rel).parent / target).resolve()
            for rel, _lineno, target, _fragment in _link_references(plugin)
        }
    return {
        path.resolve()
        for target in _form_targets(plugin, form)
        for path in _candidates(target, plugin)
    }


# Every `.md` written anywhere in plugin prose. Not an extractor — the census
# below uses it to ask the question the extractors cannot ask of themselves:
# *is there a citation none of us read?* Floors, sentinels and waivers all sit
# downstream of extraction, so none of them can see a form no regex spells.
_MD_MENTION = re.compile(r"[A-Za-z0-9_.\-/*]*\.md\b")

# A URL scheme immediately before the mention, no whitespace between: the
# mention is part of somebody else's address. Keyed on `//`, not on the colon
# — prose writes `contract: skills/x/y.md`, and a bare colon before a citation
# must not read as a scheme.
_SCHEME_BEFORE = re.compile(r"\w+://\S*$")

# A disposition the tree does not need yet, and why it is written anyway. The
# liveness check honours these instead of demanding a citation exist first —
# the alternative is adding prose to satisfy a guard, which is backwards.
_PREEMPTIVE_DISPOSITIONS = {
    "external url": (
        "the link pass drops scheme targets by design, and the engine ADR "
        "already allow-listed for the file pass is the obvious first one "
        "somebody links — the census must not fail on it the day they do"
    ),
}


def _mention_disposition(rel: str, line: str, start: int, end: int) -> str | None:
    """Why a `.md` written in prose is not a citation any extractor should
    read. One name per reason, and the census below fails on a mention that
    fits none of them — that is what makes a citation form nobody spelled
    impossible to add silently."""
    mention = line[start:end]
    before, after = line[:start], line[end:]
    if mention.startswith("//") or _SCHEME_BEFORE.search(before):
        # `https://…/docs/sql-write-path-v2.md`: a file in another repo, which
        # this one cannot open. `_LINK_REF` drops scheme targets for that
        # reason, so without this the census would fail on the very link the
        # link pass deliberately declines to grade.
        return "external url"
    if _HEADING.match(line) and mention == Path(rel).name:
        # `# CLAUDE.md — analitiq-connector-builder`: the document naming
        # itself, not pointing anywhere. Tied to the document's own filename,
        # because "a `.md` in a heading" would disposition away a real citation
        # written in one — and a heading is a normal place to cite from.
        return "document title"
    if before.rstrip().endswith(("──", "─")):
        # A tree diagram's leaf. The directory listing is illustrative; the
        # files in it are cited properly elsewhere or do not exist yet.
        return "tree diagram"
    if before.endswith("[") or (before.rstrip("`").endswith("[") and after.startswith(("`]", "]"))):
        # The visible half of a markdown link. The target half is checked.
        return "link text"
    if "*" in mention:
        # `spec-*.md` names a set of files, not a file.
        return "glob"
    if "/" not in mention:
        # The documented decision: a bare filename with no directory segment is
        # indistinguishable from an ordinary prose word, so only its backticked
        # form (`_BARE_REF`) is read. Unbackticked, it stays prose.
        return "bare filename"
    return None


def _mention_spans(text: str) -> list[tuple[int, int]]:
    """Where every extractor reads in one document, as offsets into the whole
    text. Document-level, not per-line, because that is what the anchor pass
    is: it scans the whole text and binds across a line break, and the span it
    bound is carried on the `Anchor` rather than guessed at afterwards."""
    spans = []
    offset = 0
    for line in text.splitlines(keepends=True):
        for pattern in (*_PATH_PATTERNS, _LINK_REF):
            for match in pattern.finditer(line):
                start, end = match.span(1)
                spans.append((offset + start, offset + end))
        offset += len(line)
    return spans + [site.path_span for site in _anchor_sites(text) if site.path_span]


class UnreadMention(NamedTuple):
    """One `.md` written in prose that no extractor read, and the reason it is
    not a citation — `None` when there is none, which is the failure."""

    rel: str
    lineno: int
    mention: str
    line: str
    disposition: str | None


def _unread_mentions(plugin: str) -> list[UnreadMention]:
    """Every `.md` in the plugin's prose that no extractor reads, each with its
    disposition. The census asserts on the ones that have none; the
    disposition-liveness test reads the rest, so a disposition is only 'used'
    if the census actually needed it."""
    root = _plugin_root(plugin)
    unread = []
    for path in _prose_files(plugin):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        spans = _mention_spans(text)
        lines = text.splitlines()
        starts, offset = [], 0
        for line in text.splitlines(keepends=True):
            starts.append(offset)
            offset += len(line)
        for match in _MD_MENTION.finditer(text):
            start, end = match.span()
            if any(start < span_end and span_start < end for span_start, span_end in spans):
                continue
            index = bisect_right(starts, start) - 1
            line, line_start = lines[index], starts[index]
            unread.append(
                UnreadMention(
                    rel=rel,
                    lineno=index + 1,
                    mention=match.group(0),
                    line=line.strip(),
                    disposition=_mention_disposition(
                        rel, line, start - line_start, end - line_start
                    ),
                )
            )
    return unread


def _uncovered(mentions: list[UnreadMention]) -> list[UnreadMention]:
    """The census's finding: read by nobody, explained by nothing. Split from
    the sweep so it can be driven in its failing direction — it is the step
    that turns an unread mention into a build failure, and it only ever runs
    on a tree that must not trip it."""
    return [m for m in mentions if m.disposition is None]


def _uncovered_mentions(plugin: str) -> list[UnreadMention]:
    return _uncovered(_unread_mentions(plugin))


def _form_sites(plugin: str, form: str) -> list[tuple[str, str]]:
    """Every (citing relpath, target) one extractor finds — the form's own
    view, with the document each citation is written in, which is what tells a
    reader-facing README link from one that routes an agent."""
    if form == "link":
        return [(rel, target) for rel, _lineno, target, _frag in _link_references(plugin)]
    if form == "asset":
        # Filtered by the plugin's own directory vocabulary. The raw pattern
        # matches several times what this keeps; the surplus is paths the
        # plugin does not own, and the count test pins kept < raw.
        return [(rel, target) for rel, _lineno, target in _asset_citations(plugin)]
    return [
        (rel, target)
        for rel, _lineno, tagged, target in _tagged_references(plugin)
        if tagged == form
    ]


def _form_targets(plugin: str, form: str) -> set[str]:
    """Every citation one extractor finds in a plugin — the form's own view,
    not the union `_references` returns."""
    return {
        match.group(1)
        for path in _prose_files(plugin)
        for line in path.read_text(encoding="utf-8").splitlines()
        for match in _FORM_PATTERNS[form].finditer(line)
    }


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


def test_the_registry_checks_fail_when_they_should() -> None:
    """Every guard-the-guard predicate, driven until it fires. Each of these
    only ever runs against a tree that must not trip it, so without this each
    could be replaced by a constant and the suite would stay green — the
    registries would keep their shape and stop meaning anything."""
    assert _incomplete_fixtures({"p": {"sentinels": {}, "fixture": ()}}) == {
        "p": ["unsentinelled"]
    }
    assert _incomplete_fixtures({"p": dict.fromkeys(_REQUIRED_FIXTURE_KEYS)}) == {}
    assert _unfloored_forms(("a", "b"), {"a": 1}) == ["b"]
    assert _unfloored_forms(("a",), {"a": 1}) == []
    assert _unknown_floor_forms({"p": {"ghost": 1}}, ("a",)) == ["ghost"]
    assert _unknown_floor_forms({"p": {"a": 1}}, ("a",)) == []
    every_form = dict.fromkeys(_FORM_PATTERNS, "x")
    assert _unpinned_forms({}, {}) == sorted(_FORM_PATTERNS)
    assert _unpinned_forms(every_form, {}) == []
    assert _stale_waivers({"link": "x"}, {"link": "why"}) == ["link"]
    assert _stale_waivers({"link": "x"}, {}) == []
    # The census's own filter — the step that turns an unread mention into a
    # build failure, and the newest predicate here to only ever run on a tree
    # that must not trip it.
    unexplained = UnreadMention("a.md", 1, "x/y.md", "see x/y.md", None)
    explained = unexplained._replace(disposition="glob")
    assert _uncovered([unexplained, explained]) == [unexplained]
    assert _uncovered([explained]) == []


@pytest.mark.parametrize("plugin", _plugin_names())
def test_the_waiver_check_reads_real_prose(plugin: str) -> None:
    """`_falsified_waivers` grades a waiver against `_form_sites`, so both have
    to be non-empty on the real tree — a `_form_sites` that returned nothing
    would make every waiver unfalsifiable while the suite stayed green."""
    assert _form_sites(plugin, "backticked"), "the form's own view is empty"
    assert {rel for rel, _target in _form_sites(plugin, "link")} - {"README.md"} or (
        "link" in _unsentinelled(plugin)
    ), "a plugin routing agents by link must pin one, not waive the form"
    # Waiving a form this plugin does cite outside its README is caught.
    outside_the_readme = [
        form
        for form in _FORM_PATTERNS
        if {rel for rel, _target in _form_sites(plugin, form)} - {"README.md"}
    ]
    assert outside_the_readme, (
        f"plugins/{plugin} cites nothing outside its README, so no waiver here "
        "can be contradicted — the check below would pass vacuously."
    )
    cited_outside = outside_the_readme[0]
    fixtures = _PLUGIN_FIXTURES[plugin]
    original = fixtures["unsentinelled"]
    try:
        fixtures["unsentinelled"] = {cited_outside: "claims to route nobody"}
        assert cited_outside in _falsified_waivers(plugin)
    finally:
        fixtures["unsentinelled"] = original
    assert _falsified_waivers(plugin) == {}


@pytest.mark.parametrize("plugin", _plugin_names())
def test_every_anchor_in_the_tree_is_graded(
    plugin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `anchor` floor counts anchors compared against real headings, and
    on this tree that is every `§` there is. Stated as equality because the
    gap is the interesting quantity: any citation the pass skips — an
    unresolvable file, a target it declines to open — shows up here as a
    number smaller than the `§` count, which is the silent-skip failure round
    one was filed for, measured directly rather than inferred."""
    # Both sides must not come from the same place: if `_form_counts` counted
    # `§` characters instead of asking the pass, this equality would be `x ==
    # x` and the anchor floor would be satisfiable by citations nobody graded.
    # Rebinding the pass proves the count follows it.
    with monkeypatch.context() as patched:
        patched.setattr(
            sys.modules[__name__], "_anchor_checks", lambda *_a, **_k: ([], 0)
        )
        assert _form_counts(plugin)["anchor"] == 0, (
            "the `anchor` count does not come from the anchor pass, so the "
            "floor it feeds is a count of `§` characters"
        )
    graded = _form_counts(plugin)["anchor"]
    written = len(_anchor_references(plugin))
    assert graded == written, (
        f"{written - graded} of {written} `§` citations in plugins/{plugin} "
        "were never graded — the anchor pass skipped them. A skipped citation "
        "is checked by nobody while the floor still counts as met."
    )


@pytest.mark.parametrize("plugin", _plugin_names())
def test_every_md_written_in_prose_is_read_or_dispositioned(plugin: str) -> None:
    """The census, and the only check here that can see a citation form nobody
    spelled. Everything else in this file sits downstream of extraction: a
    floor counts what a regex matched, a sentinel names a citation a regex
    finds, a waiver explains a form a regex reads. None of them can notice a
    `.md` no pattern reaches — which is how two `../`-prefixed citations sat
    unchecked while the suite was green and a comment said otherwise.

    So: having a `.md` in prose is the trigger, and every one is either inside
    some extractor's match or carries a named reason it is not a citation. A
    new citation form arrives as a failure here, not as silence."""
    uncovered = _uncovered_mentions(plugin)
    assert not uncovered, (
        "`.md` written in prose that no extractor reads:\n"
        + "\n".join(
            f"  plugins/{plugin}/{m.rel}:{m.lineno} -> {m.mention}\n      {m.line}"
            for m in uncovered
        )
        + "\nEither it is a citation — teach the extractor its form, and pin "
        "the form with a sentinel — or it is not, and `_mention_disposition` "
        "needs the reason why, named."
    )


def test_the_census_reads_coverage_from_the_passes_themselves() -> None:
    """Coverage is what an extractor actually read, not what a pattern would
    match if run somewhere else. Both directions of getting that wrong are
    live: a citation ending a line looks covered by the anchor binding (whose
    `$` means *before a `§`*, not *end of line*), and a `§` citation written at
    full depth inside the backticks looks uncovered though both passes grade
    it."""
    # Nothing reads this, and nothing may claim to. It ends the line, which is
    # where a per-line `_ANCHOR_BINDING` would wrongly answer for it.
    line_final = "The rule lives in /skills/nowhere/gone.md"
    assert _scan_text(line_final) == []
    assert not any(
        start < end2 and start2 < end
        for start, end in [(18, len(line_final))]
        for start2, end2 in _mention_spans(line_final)
    )
    # This one *is* read — by the anchor pass, whose binding is the only thing
    # that sees a path with the anchor inside the backticks.
    anchored = "Follow `skills/endpoint-spec/spec-columns.md §Timestamp` when mapping."
    assert [target for _lineno, target in _scan_text(anchored)] == [
        "skills/endpoint-spec/spec-columns.md"
    ]
    covered = _mention_spans(anchored)
    mention = _MD_MENTION.search(anchored)
    assert any(
        mention.start() < end and start < mention.end() for start, end in covered
    ), "the anchor pass read this path; the census must say so"


def test_every_mention_disposition_is_load_bearing() -> None:
    """A disposition nobody's prose fits is dead config that can only mask a
    real citation later. Each is exercised on the shape it was written for,
    and the real tree is what proves they are all still needed."""
    cases = {
        "document title": ("CLAUDE.md", "# CLAUDE.md — analitiq-connector-builder", 2, 11),
        "tree diagram": ("README.md", "└── README.md", 4, 13),
        "link text": (
            "README.md",
            "[spec-envelope.md](skills/connection-spec/spec-envelope.md)",
            1,
            17,
        ),
        "glob": ("agents/x.md", "- every `spec-*.md` under it.", 9, 18),
        "bare filename": ("agents/x.md", "the rule in io-contracts.md, never a slug", 12, 27),
        # `_MD_MENTION` starts after the `//`, so the span is the URL's tail.
        "external url": (
            "CLAUDE.md",
            "See [the ADR](https://github.com/analitiq-ai/x/blob/main/docs/adr.md).",
            20,
            68,
        ),
    }
    for expected, (rel, line, start, end) in cases.items():
        assert line[start:end].endswith(".md"), (expected, line[start:end])
        assert _mention_disposition(rel, line, start, end) == expected
    # A real citation is not dispositioned away by any of them — including one
    # written in a heading, which is a normal place to cite from.
    line = "see skills/nowhere/gone.md for details"
    assert _mention_disposition("agents/x.md", line, 4, 26) is None
    heading = "## Pipeline (full contract: skills/nowhere/gone.md)"
    found = _MD_MENTION.search(heading)
    assert found.group(0) == "skills/nowhere/gone.md"
    assert _mention_disposition("SKILL.md", heading, *found.span()) is None
    # A prose colon before a citation is not a URL scheme — including with no
    # space after it, which is what tells `https://` from `contract:`.
    prose = "per contract: skills/nowhere/gone.md"
    assert _mention_disposition("agents/x.md", prose, *_MD_MENTION.search(prose).span()) is None
    assert _SCHEME_BEFORE.search("See [the ADR](https://host/docs/")
    assert not _SCHEME_BEFORE.search("per contract:")
    assert not _SCHEME_BEFORE.search("see ADV-CTOR-004:")
    # And every disposition still answers for a mention the census *needed* it
    # for. Counting mentions an extractor already read would let a disposition
    # look alive on prose the census never asks about.
    used = {
        mention.disposition
        for plugin in _plugin_names()
        for mention in _unread_mentions(plugin)
    }
    unused = sorted(set(cases) - used - set(_PREEMPTIVE_DISPOSITIONS))
    assert not unused, (
        f"dispositions no prose needs any more: {unused} — drop them, so the "
        "list keeps meaning what it says, or record why one is written ahead "
        "of the prose in _PREEMPTIVE_DISPOSITIONS."
    )
    # A pre-emptive entry is a claim that nothing needs it *yet*. Once prose
    # does, it stops being pre-emptive and the reason is stale config.
    arrived = sorted(set(_PREEMPTIVE_DISPOSITIONS) & used)
    assert not arrived, (
        f"dispositions recorded as pre-emptive that real prose now needs: "
        f"{arrived} — drop the _PREEMPTIVE_DISPOSITIONS entry; the tree proves "
        "the disposition itself."
    )
    # And the other direction, which nothing else asks: a pre-emptive entry
    # naming a reason `_mention_disposition` can never return is config that
    # exempts nothing, sitting in the one registry whose whole point is that
    # an exemption is declared and reviewable.
    unreachable = _unreachable_preemptive(_PREEMPTIVE_DISPOSITIONS, set(cases))
    # Driven, not merely evaluated: on today's registry the check has nothing
    # to find, so without this it would pass as a constant.
    assert _unreachable_preemptive({"typo url": "why"}, set(cases)) == ["typo url"]
    assert _unreachable_preemptive({"glob": "why"}, set(cases)) == []
    assert not unreachable, (
        f"_PREEMPTIVE_DISPOSITIONS names dispositions no branch returns: "
        f"{unreachable} — either the branch went away, or the name is a typo "
        "nobody would ever see fail."
    )


@pytest.mark.parametrize("plugin", _plugin_names())
def test_a_build_artifact_is_never_a_citation_target(plugin: str) -> None:
    """Filtering the path universe is not enough: `_candidates` answers three
    of its branches straight off the filesystem, so a citation of a
    `__pycache__` entry resolved from the checkout — and CI grows that
    directory itself, so it resolved there too. The refusal belongs at the top
    of resolution, where every branch passes through it."""
    root = _plugin_root(plugin)
    cache = root / "__pycache__"
    artifact = cache / "guard_probe.cpython-313.pyc"
    created = not cache.exists()
    try:
        cache.mkdir(exist_ok=True)
        artifact.write_text("", encoding="utf-8")
        # The artifact is on disk, which is what the three `.exists()` branches
        # of `_candidates` consult. Every one must still refuse it.
        assert artifact.is_file()
        rel = f"__pycache__/{artifact.name}"
        assert _candidates(rel, plugin) == []
        assert _candidates(rel, plugin, "agents/x.md") == []
        assert _candidates(f"plugins/{plugin}/{rel}", plugin) == []
        assert _is_dangling(rel, plugin, "agents/x.md")
    finally:
        artifact.unlink(missing_ok=True)
        if created and cache.exists():
            cache.rmdir()
    # The twin: a file the plugin does ship resolves by the same route.
    shipped = _fixture(plugin)[0]
    assert _candidates(shipped, plugin)


@pytest.mark.parametrize("plugin", _plugin_names())
def test_the_floors_count_what_the_sweep_found(plugin: str) -> None:
    """A floor that re-runs the patterns cannot notice the sweep going blind —
    it would keep counting citations nothing grades. Every scanned form's count
    comes from the same tagged sweep the file pass reads, so narrowing the
    sweep drops the count with it."""
    counts = _form_counts(plugin)
    tagged = Counter(form for _rel, _lineno, form, _t in _tagged_references(plugin))
    for form in _SCAN_FORMS:
        assert counts[form] == tagged[form] > 0, form
    assert counts["link"] == len(_link_references(plugin))
    assert counts["asset"] == len(_asset_citations(plugin))
    # And the sweep really is the plugin's whole prose, not one document of it.
    assert len({rel for rel, _l, _f, _t in _tagged_references(plugin)}) > 5


def test_the_shipped_universe_drops_build_artifacts(tmp_path: Path) -> None:
    """The sweep has to *call* the filter — asserting that everything it
    returned passes the filter is true by construction, and asking the real
    checkout only bites when an earlier suite happened to import a plugin
    script and leave a `__pycache__` behind. So: a tree this test builds, with
    the artifact in it."""
    (tmp_path / "scripts" / "__pycache__").mkdir(parents=True)
    (tmp_path / "scripts" / "validate.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "__pycache__" / "validate.cpython-313.pyc").write_text(
        "", encoding="utf-8"
    )
    paths = _paths_under(tmp_path)
    assert "scripts/validate.py" in paths
    assert not [path for path in paths if "__pycache__" in path]


def test_a_starved_form_trips_its_floor() -> None:
    """The floors are this file's anti-vacuity device, and their comparison is
    only ever run on a tree that must not trip it — so the failing direction
    needs its own test, or `_below_floor` could return nothing at all and
    every floor in both registries would go decorative."""
    assert _below_floor({"backticked": 3}, {"backticked": 4}) == {"backticked": (3, 4)}
    assert _below_floor({"backticked": 4}, {"backticked": 4}) == {}  # equal is not below
    assert _below_floor({"backticked": 0}, {}) == {}  # an unfloored form is not below
    message = _floor_failure("plugins/x", {"backticked": (3, 4)})
    assert "backticked found 3, floor 4" in message
    assert "vacuously" in message


def test_a_link_fragment_is_read_out_of_the_prose() -> None:
    """The join from prose to `_link_dangles`' fragment check. No link in
    either plugin carries a fragment today, so nothing on the real tree
    exercises the capture — and a fragment silently lost reads as "the section
    is fine", while a `#` silently kept reads as a broken link that is not."""
    assert _links_in("See [t](spec-envelope.md#type-fidelity).") == [
        (1, "spec-envelope.md", "type-fidelity")
    ]
    assert _links_in("See [t](spec-envelope.md).") == [(1, "spec-envelope.md", "")]
    assert _links_in("Line one\n\nSee [t](../x/y.md#a-b).") == [
        (3, "../x/y.md", "a-b")
    ]
    # CommonMark's other spellings of the same link. Missing them is worse than
    # missing an exotic form: `_BARE_PATH_REF` defers everything after `](` to
    # this pattern, so a target this does not match is read by nobody.
    assert _links_in('See [t](spec-envelope.md "Envelope").') == [
        (1, "spec-envelope.md", "")
    ]
    assert _links_in('See [t](x/y.md#frag "Title").') == [(1, "x/y.md", "frag")]
    assert _links_in("See [t](<spec-envelope.md>).") == [(1, "spec-envelope.md", "")]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_sentinel_citations_are_still_found(plugin: str) -> None:
    """Floors prove a form still matches something; these prove the extractor
    still reaches the specific routing citations agents depend on. A count
    cannot do that — an over-matching pattern raises the count while losing
    the citation that mattered.

    Matched by the *file* each sentinel resolves to, and only through the form
    the sentinel is filed under. Depth is prose's business: rewriting
    `connector-builder/references/x.md` as `references/x.md` routes to the same
    document and must not fail. Losing the citation, or writing it in another
    form while this one dies, must.
    """
    missing = _unreached_sentinels(plugin, _sentinels(plugin))
    assert not missing, (
        f"sentinel citations no longer reached in plugins/{plugin}: {missing} "
        "— if the prose deliberately moved the citation, repoint the sentinel; "
        "if not, the routing an agent depends on just disappeared. Note the "
        "form: a citation rewritten in another form fails here too, because "
        "this is what keeps each extractor pinned to real prose."
    )
    # The form is load-bearing, not decorative: the same file filed under a
    # form that does not cite it must fail. Without this, one extractor could
    # die while another's citations kept every sentinel green.
    misfiled = {"link": _sentinels(plugin)["backticked"]}
    assert _unreached_sentinels(plugin, misfiled) == misfiled
    # And the comparison is by resolved file, not by the string as written —
    # the `bare_path` sentinel is deliberately spelled at a depth no prose
    # uses, so a rewrite of this check into string equality fails here.
    bare = _sentinels(plugin)["bare_path"]
    assert bare not in _form_targets(plugin, "bare_path")


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

def _dangling_in(text: str, plugin: str, citing: str | None = None) -> list[str]:
    """The scan-and-resolve pipeline of `test_doc_references_resolve`, on one
    document's text. `citing` is the document the text stands in for, which a
    `../`-prefixed citation resolves against."""
    return [
        target
        for _lineno, target in _scan_text(text)
        if _is_dangling(target, plugin, citing)
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
    # The twin, in this plugin: its own sentinel path resolves — and does so
    # with the sentence-final period `_clean` has to strip, which is how the
    # prose ends such a line.
    real = _sentinels(plugin)["plugin_root"]
    assert _dangling_in(f"Read ${{CLAUDE_PLUGIN_ROOT}}/{real}.\n", plugin) == []


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
def test_a_cited_example_file_is_guarded(plugin: str) -> None:
    """An example an agent is told to copy is a citation like any other —
    renaming it starves the agent the same way a missing spec does. The `.md`
    passes cannot see these, and the census cannot either: `.md` is its
    trigger, so this form needs its own extractor and its own floor."""
    citations = _asset_citations(plugin)
    assert citations, "no plugin-owned non-`.md` citation found at all"
    # Every shape prose writes these in, not just the tidy one. A backtick
    # span the path fills, a span it shares with the flags it is run with, a
    # frontmatter line with no backticks at all, and a fenced command — the
    # last is how this plugin's own contributor guidance names the script it
    # tells you to run, and it was read by nobody while the extractor
    # required the path to fill a code span.
    shapes = {
        "Wraps scripts/x.py.": ["scripts/x.py"],
        "`scripts/x.py`": ["scripts/x.py"],
        "`scripts/x.py --direction read`": ["scripts/x.py"],
        "python3 plugins/{plugin}/scripts/x.py --check": [
            "plugins/{plugin}/scripts/x.py"
        ],
        # A dot-leading directory is a directory. This is the shape the `.` in
        # the segment charset exists for, and asserting the captured path — not
        # merely that something matched — is what pins it.
        "See `.claude-plugin/plugin.json` for the version.": [
            ".claude-plugin/plugin.json"
        ],
        "See `./examples/x.json` and `../spec-db/examples/y.json`.": [
            "./examples/x.json",
            "../spec-db/examples/y.json",
        ],
        # And the negative that pins the lookbehind: the tail of a
        # `${CLAUDE_PLUGIN_ROOT}` reference is that form's citation, already
        # graded, and reading it again would double-count it into this form's
        # floor.
        'Run `${CLAUDE_PLUGIN_ROOT}/scripts/x.py --direction read`.': [],
        "See https://schemas.analitiq.ai/connector/latest.json for the shape.": [],
    }
    for shape, expected in shapes.items():
        line = shape.replace("{plugin}", plugin)
        found = [match.group(1) for match in _BARE_ASSET_REF.finditer(line)]
        assert found == [want.replace("{plugin}", plugin) for want in expected], shape
    # Each resolves from the document it is written in — the same predicate
    # the file pass runs, so this is the sweep, not a second opinion.
    unresolved = [
        (rel, lineno, target)
        for rel, lineno, target in citations
        if _is_dangling(target, plugin, rel)
    ]
    assert not unresolved, unresolved


@pytest.mark.parametrize("plugin", _plugin_names())
def test_only_this_plugins_files_are_read_as_asset_citations(plugin: str) -> None:
    """The discriminator, both ways. A leading segment this plugin has means
    the citation addresses this plugin; anything else is the author's artifact,
    a schema URL's tail, or a timezone — and grading those would fail the build
    on prose that is correct."""
    assert {"examples", "skills"} <= _plugin_dirs(plugin)
    assert _addresses_this_plugin("examples/nowhere/gone.example.json", plugin)
    for elsewhere in (
        "definition/connector.json",  # what the connector author writes
        "connection/latest.json",  # a published schema URL's tail
        "America/New_York",  # not a file
        ".secrets/credentials.json",  # the user's, not the plugin's
        "skills/x/spec-y.md",  # a `.md`, read by the other passes
    ):
        assert not _addresses_this_plugin(elsewhere, plugin), elsewhere
    # A fully qualified citation names a plugin file however it is spelled.
    assert _addresses_this_plugin(f"plugins/{plugin}/scripts/x.py", plugin)
    # A dot-leading directory is a directory, not a relative hop.
    assert ".claude-plugin" in _plugin_dirs(plugin)
    assert _addresses_this_plugin(".claude-plugin/plugin.json", plugin)
    assert _addresses_this_plugin("./examples/x.json", plugin)
    # And nothing a local checkout grows is in the universe a citation
    # resolves against — it would pass here and dangle in CI. Asserted on the
    # predicate, not on the checkout: whether `__pycache__` exists right now
    # depends on whether another suite imported a plugin script first, so a
    # sweep-based assertion would pin the filter only sometimes.
    assert not _ships("scripts/__pycache__/validate.cpython-313.pyc")
    assert not _ships("__pycache__")
    assert _ships("scripts/validate.py")
    assert _ships("skills/x/examples/y.json")
    # And the floor counts what the filter kept. Counting raw matches would
    # let prose about `definition/connector.json` satisfy a floor meant to
    # prove this plugin's own examples are still cited.
    raw = sum(
        len(_BARE_ASSET_REF.findall(line))
        for path in _prose_files(plugin)
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    kept = len(_asset_citations(plugin))
    assert _form_counts(plugin)["asset"] == kept < raw


@pytest.mark.parametrize("plugin", _plugin_names())
def test_a_generated_changelog_is_not_graded_as_prose(plugin: str) -> None:
    """release-please writes `CHANGELOG.md` from commit subjects, and this
    repo's subjects carry `§` and `.md` paths — this PR's own does. Grading it
    would fail the build on text the author cannot fix: hand-editing a
    generated file is undone by the next release. It is also not prose any
    agent reads."""
    # Not "the changelog exists" — release-please writes it at a plugin's
    # first release, so a plugin can legitimately be here without one, and a
    # guard about citations must not demand a release train.
    assert "CHANGELOG.md" in _GENERATED_PROSE
    assert not [p for p in _prose_files(plugin) if p.name in _GENERATED_PROSE]
    # The shape that would break the build if it were swept: a release entry
    # naming a since-renamed spec, and one quoting a `§` from a commit subject.
    entry = (
        "* guard every plugin's citations — the section a § names "
        "([#151](https://github.com/analitiq-ai/x/issues/151))\n"
        "* fix drift in skills/stream-spec/spec-renamed-away.md\n"
    )
    assert _dangling_in(entry, plugin) == ["skills/stream-spec/spec-renamed-away.md"]
    assert _anchor_sites(entry)


@pytest.mark.parametrize("plugin", _plugin_names())
def test_a_link_to_another_repo_is_not_a_broken_link(plugin: str) -> None:
    """An engine ADR linked by URL is a file this repo cannot open. Resolving
    it relative to the citing document would report every such link dangling —
    and the one ADR already allow-listed for the file pass is exactly the link
    someone would write."""
    doc = (
        "See [the ADR](https://github.com/analitiq-ai/analitiq-engine/blob/"
        "main/docs/sql-write-path-v2.md).\n"
    )
    assert [m.group(1) for m in _LINK_REF.finditer(doc)] == []
    # A repo-relative link on the same line is still read.
    assert [
        m.group(1) for m in _LINK_REF.finditer("[a](x.md) and [b](http://y/z.md)")
    ] == ["x.md"]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_a_link_out_of_the_plugin_is_a_readme_privilege(plugin: str) -> None:
    """The file pass refuses to resolve past the plugin root, because an agent
    reads an installed plugin cache where repo files do not exist. The link
    pass has to agree — except from the plugin's own README, a page a reader
    browses in the repo, which is the only place either plugin links out
    today."""
    assert (REPO_ROOT / "README.md").is_file()
    assert not _link_dangles("../../README.md", "", "README.md", plugin)
    # The same target, from a document an agent reads out of the plugin cache.
    deep, _heading = _fixture(plugin)  # skills/<skill>/references/<file>.md
    hops = "../" * 5  # references -> skill -> skills -> plugin -> plugins -> repo
    assert (_plugin_root(plugin) / deep).parent.joinpath(
        f"{hops}README.md"
    ).is_file(), "the link resolves — it is the plugin boundary that rejects it"
    assert _link_dangles(f"{hops}README.md", "", deep, plugin)


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
    full = _headings((_plugin_root(plugin) / citing).read_text(encoding="utf-8"))[0]
    assert heading  # the fixture's own citation form, exercised by the `§` tests
    assert not _link_dangles(own, _slug(full), citing, plugin)
    assert _link_dangles(own, "section-that-was-renamed", citing, plugin)


def test_a_heading_slugs_the_way_a_link_writes_it() -> None:
    """Stated as literals, not round-tripped through `_slug` on both sides —
    comparing the function to itself would accept any slug rule at all, and
    the fragment check is only as good as this mapping. These are the shapes
    plugin headings actually take: backticked identifiers, parenthesised
    qualifiers, an em-dash."""
    assert _slug("Derived `endpoint_id`") == "derived-endpoint_id"
    assert _slug("Release version (`version`)") == "release-version-version"
    assert _slug("Cross-field rules the contract enforces") == (
        "cross-field-rules-the-contract-enforces"
    )
    assert _slug("Fenced JSON examples — the annotation convention") == (
        "fenced-json-examples--the-annotation-convention"
    )
    # A fragment is compared case-insensitively: a link may spell it either
    # way and lands on the same anchor in a browser.
    citing, _heading = _fixture("analitiq-connector-builder")
    own = Path(citing).name
    assert not _link_dangles(
        own, "RELEASE-VERSION-VERSION", citing, "analitiq-connector-builder"
    )


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
    assert [(rel, s.lineno, s.target, s.text, s.quoted) for rel, s in sites] == [
        (citing, 1, existing, heading, False)
    ]
    assert _anchor_checks(plugin, sites) == ([], 1)


@pytest.mark.parametrize("plugin", _plugin_names())
def test_ambiguous_citation_is_still_checked(plugin: str) -> None:
    """A basename several files answer to — `SKILL.md`, cited from `agents/`
    where no ancestor carries one — must not fall through unchecked. The file
    pass passes it (suffix match), so a skip here would leave nobody checking
    the section at all."""
    citing = "agents/synthetic-classifier.md"
    candidates = _resolve_files("SKILL.md", citing, plugin)
    assert len(candidates) > 1
    doc = "Author per `SKILL.md §Heading no skill carries`.\n"
    sites = [(citing, site) for site in _anchor_sites(doc)]
    dangling, checked = _anchor_checks(plugin, sites)
    assert checked == 1
    assert [site[3] for site in dangling] == ["Heading no skill carries"]
    # The other half of the ambiguity policy: a heading carried by *one* of the
    # candidates resolves. Requiring all of them would fail every ambiguous
    # citation in the tree — a guard that cries wolf gets switched off.
    solo = [
        heading
        for heading, seen in Counter(
            heading
            for path in candidates
            for heading in _headings(path.read_text(encoding="utf-8"))
            if re.fullmatch(r"[\w ]+", heading)
        ).items()
        if seen == 1
    ][0]
    solo_doc = f"Author per `SKILL.md §{solo}`.\n"
    solo_sites = [(citing, site) for site in _anchor_sites(solo_doc)]
    assert _anchor_checks(plugin, solo_sites) == ([], 1)


@pytest.mark.parametrize("plugin", _plugin_names())
def test_a_sibling_citation_resolves_to_its_own_skill(plugin: str) -> None:
    """The other half of the ambiguity policy: when the citing document *has*
    an ancestor carrying the path, that one file is the answer and the four or
    five namesakes elsewhere are not consulted. Without this narrowing, a
    heading renamed in one skill is covered by the same heading surviving in
    another — two of the pipeline plugin's `SKILL.md` files carry
    `## Cross-field rules …` today, so the citation would resolve against the
    wrong document and never fail."""
    citing, _heading = _fixture(plugin)  # skills/<skill>/references/<file>.md
    skill_dir = Path(citing).parent.parent
    assert _resolve_files("SKILL.md", citing, plugin) == [
        _plugin_root(plugin) / skill_dir / "SKILL.md"
    ]
    # And the lenient branch stays lenient where there is no ancestor to use.
    assert len(_resolve_files("SKILL.md", "agents/x.md", plugin)) > 1


@pytest.mark.parametrize("plugin", _plugin_names())
def test_an_unreadable_target_is_not_counted_as_checked(plugin: str) -> None:
    """`checked` is what the floor is a statement about, so it must count
    anchors this pass actually graded. A citation whose file does not exist is
    the file pass's finding and is graded by nobody — counting it would let a
    dead `_anchor_resolves` clear the floor on citations it never read."""
    citing = "agents/synthetic-classifier.md"
    doc = "Author per `skills/nowhere/gone.md §Some heading`.\n"
    sites = [(citing, site) for site in _anchor_sites(doc)]
    assert _anchor_checks(plugin, sites) == ([], 0)


@pytest.mark.parametrize("plugin", _plugin_names())
def test_a_citation_must_name_a_whole_path_segment(plugin: str) -> None:
    """Suffix resolution matches path *segments*, not characters. Without the
    `/`, `versioning.md` would resolve against `metadata-and-versioning.md` —
    a dangling citation passing silently, the one failure this file exists to
    catch."""
    assert _dangling_in("See `versioning.md` for rules.\n", plugin) == [
        "versioning.md"
    ]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_resolution_stops_at_the_plugin_boundary(plugin: str) -> None:
    """The ancestor walk stops at the plugin root. Walking past it resolves a
    citation against repo files an installed plugin does not ship — the agent
    reading it has only the plugin directory."""
    assert (REPO_ROOT / "CONTRIBUTING.md").is_file()
    assert _resolve_files("CONTRIBUTING.md", "agents/x.md", plugin) == []
    assert _dangling_in("See `CONTRIBUTING.md` for the rules.\n", plugin) == [
        "CONTRIBUTING.md"
    ]
    # And by the relative route, which resolves through the citing document
    # rather than by suffix — the same boundary, a different code path.
    citing, _heading = _fixture(plugin)  # skills/<skill>/references/<file>.md
    assert _candidates("../../../../../CONTRIBUTING.md", plugin, citing) == []
    assert _dangling_in(
        "See `../../../../../CONTRIBUTING.md`.\n", plugin, citing
    ) == ["../../../../../CONTRIBUTING.md"]
    # The same route, staying inside: a sibling skill's reference resolves.
    sibling = _sentinels(plugin)["bare_path"]
    hops = "../" * (len(Path(citing).parts) - 1)
    assert _candidates(f"{hops}{sibling}", plugin, citing)


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
    site = _anchor_sites(text)[0]
    assert (site.lineno, site.target, site.text, site.quoted) == (
        1,
        "spec-connector-package.md",
        "Dialect\n  hooks",
        False,
    )
    # The span is where the path was written, which is what the census reads.
    assert text[slice(*site.path_span)] == "spec-connector-package.md"
    assert _anchor_resolves("Dialect\n  hooks", ["Dialect hooks"])


def test_quoted_anchor_keeps_its_own_punctuation() -> None:
    """Quoting is how a heading whose own punctuation the stop set would cut
    survives — the form `§ "Fenced JSON examples — the annotation convention"`
    uses. Unquoted, each of those marks ends the anchor early."""
    quoted = ' "Rules, exceptions — and limits" follow.\n'
    assert _anchor_text(quoted) == "Rules, exceptions — and limits"
    assert _anchor_text(" Rules — and limits follow.\n") == "Rules"
    assert _anchor_text(" Rules, exceptions follow.\n") == "Rules"
    # Curly quotes are a form prose editors produce; the anchor survives them.
    assert _anchor_text(" “Rules, exceptions — and limits” follow.\n") == (
        "Rules, exceptions — and limits"
    )


def test_the_anchor_stop_set_cuts_where_the_heading_ends() -> None:
    """Each stop is a place prose resumes after naming a section. A missing one
    swallows the rest of the sentence into the heading, and the citation then
    matches only by its opening words — quietly weaker than it reads."""
    assert _anchor_text("Shape) and then some") == "Shape"
    assert _anchor_text("Shape — and then some") == "Shape"
    assert _anchor_text("Shape, and then some") == "Shape"
    assert _anchor_text("Shape] and then some") == "Shape"
    assert _anchor_text("Shape} and then some") == "Shape"
    assert _anchor_text("Shape | next cell") == "Shape"
    assert _anchor_text("Shape\n\nA new paragraph.") == "Shape"
    assert _anchor_text("Shape. Then a sentence.") == "Shape"
    # A period inside the heading is not a sentence end: `1.0` survives.
    assert _anchor_text("Release version 1.0 and up") == "Release version 1.0 and up"


def test_tokens_keep_hyphens_and_underscores_whole() -> None:
    """`cross-field` and `endpoint_id` are one word each. Splitting them turns
    a one-word heading into two, which is exactly the length the swallow rule
    keys on — `## Cross-field` would start answering for any anchor beginning
    with "cross"."""
    assert _tokens("Derived `endpoint_id`") == ("derived", "endpoint_id")
    assert _tokens("Cross-field rules") == ("cross-field", "rules")
    assert not _anchor_resolves("Cross-field rules that moved", ["Cross-field"])
    assert not _anchor_resolves("endpoint_id derivation", ["endpoint_id"])
    # Case is not part of the claim: prose cites a heading as it reads.
    assert _anchor_resolves("METADATA fields", ["Metadata Fields"])
    # An anchor with no words at all names nothing.
    assert not _anchor_resolves("", ["Anything"])


def test_a_heading_inside_a_fence_is_not_a_section() -> None:
    """`# Encoding values` in a shell or python example is a comment. Treating
    it as a heading would resolve citations of a section that does not
    exist — in a backtick fence or a tilde fence. An indented `#` is not a
    heading either, but for a different reason: `_HEADING` anchors at column
    zero, so indentation alone already disqualifies it."""
    doc = "# Real\n\n```python\n# Encoding values\n```\n"
    assert _headings(doc) == ["Real"]
    assert not _anchor_resolves("Encoding values", _headings(doc))
    assert _headings("# Real\n\n~~~\n# Encoding values\n~~~\n") == ["Real"]
    assert _headings("# Real\n\n    # Encoding values\n") == ["Real"]
    # And a deep heading is still a section: specs cite `###` and below.
    assert _headings("#### Dialect hooks\n") == ["Dialect hooks"]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_a_citation_ending_a_sentence_still_resolves(plugin: str) -> None:
    """The path charset has to contain `.`, so a citation that ends a sentence
    captures the full stop. Left on, every such citation reads as dangling —
    and prose ends sentences with citations constantly."""
    existing, _heading = _fixture(plugin)
    assert _dangling_in(f"Author per {existing}.\n", plugin) == []
    assert _clean("skills/x/y.md.") == "skills/x/y.md"
    assert _clean("skills/x/examples/") == "skills/x/examples"
    # Only a *trailing* dot, never one inside the name.
    assert _clean("skills/x/spec.v2.md") == "skills/x/spec.v2.md"


def test_the_bare_path_form_needs_a_directory_and_a_clean_ending() -> None:
    """Without the directory requirement this pattern reads ordinary prose as
    citations; without the trailing guard it reads `.mdx` and friends as `.md`.
    Both would fill the guard with findings nobody can act on, which is how a
    guard gets muted."""
    assert _scan_text("The author writes their own notes.md by hand.") == []
    assert _scan_text("see skills/foo/bar.mdx here") == []
    assert _scan_text("see skills/foo/bar.md here") == [(1, "skills/foo/bar.md")]


def test_a_one_word_heading_does_not_swallow_a_renamed_section() -> None:
    """A file carrying `## Output` must not answer for `§Output contract`,
    whose section was renamed — one-word headings are everywhere (`Rules`,
    `Shape`, `Modes`), and each would otherwise absorb every anchor starting
    with its word."""
    assert not _anchor_resolves("Output contract", ["Output", "Inputs to collect"])
    assert _anchor_resolves("Output", ["Output", "Inputs to collect"])
    # Two words is enough to be a citation rather than a coincidence.
    assert _anchor_resolves("Import rules owns the list", ["Import rules"])
    # The cost, stated so it is a decision and not a surprise: prose cannot run
    # on past a one-word heading — it has to end the citation with punctuation
    # the stop set knows. The failure message says so.
    assert not _anchor_resolves("Process and run in order", ["Process"])
    assert _anchor_resolves(_anchor_text("Process, and run in order"), ["Process"])


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
    sites = _anchor_sites('§ "Shape" and §Shape of it')
    assert len(sites) == 2
    assert (sites[0].quoted, sites[0].text) == (True, "Shape")
    assert (sites[1].quoted, sites[1].text) == (False, "Shape of it")
    # A long quoted heading stays quoted: the anchor window has to reach past
    # it, or the citation is silently declassified to the looser rule.
    long_heading = "Fenced JSON examples — the annotation convention"
    assert len(long_heading) < _ANCHOR_WINDOW
    assert _anchor_sites(f'§ "{long_heading}"')[0].quoted


@pytest.mark.parametrize("plugin", _plugin_names())
def test_a_quoted_anchor_is_graded_exactly_by_the_pass(plugin: str) -> None:
    """The join, not the pieces: `_anchor_checks` must hand `Anchor.quoted`
    through to the comparison. Grading a quoted citation as prose is invisible
    on the real tree — both quoted citations there resolve by opening words
    too — so only a mid-heading rename shows the loss."""
    citing, _heading = _fixture(plugin)
    # A heading long enough to rename *past* its opening words — the only
    # rename an unquoted citation cannot see.
    candidates = [
        h
        for h in _headings((_plugin_root(plugin) / citing).read_text(encoding="utf-8"))
        if re.fullmatch(r"[\w ]+", h) and len(h.split()) >= 3
    ]
    if not candidates:
        pytest.fail(
            f"the fixture file for {plugin} — {citing}, named in "
            "_PLUGIN_FIXTURES — no longer carries a three-word heading free of "
            "punctuation, which this test needs to rename past a citation's "
            "opening words. Point the fixture at a file that does."
        )
    words = candidates[0].split()
    renamed = " ".join([*words[:2], "renamed", *words[3:]])
    doc = f'Author per `{citing}` § "{renamed}".\n'
    sites = [(citing, site) for site in _anchor_sites(doc)]
    assert sites[0][1].quoted
    dangling, checked = _anchor_checks(plugin, sites)
    assert checked == 1
    assert [site[3] for site in dangling] == [renamed]
    # Unquoted, the same words resolve — two shared opening words are all an
    # unquoted citation promises, which is exactly why quoting exists.
    unquoted = [(citing, site) for site in _anchor_sites(f"Author per `{citing}` §{renamed}.\n")]
    assert not unquoted[0][1].quoted
    assert _anchor_checks(plugin, unquoted) == ([], 1)


def test_a_comma_or_dash_still_binds_the_anchor_to_its_file() -> None:
    """Glue between the path and the `§` is punctuation prose uses freely. A
    binding that broke on a comma would not merely lose the check — it would
    resolve the anchor against the citing document and report a section of the
    wrong file as missing."""
    for glue in ("` ", "`, ", "` — ", "`\n  ", "`** ", "`\n> ", "`**\n> ", "`: "):
        text = f"see `SKILL.md{glue}§Cross-field rules for the tuple."
        assert _anchor_sites(text)[0].target == "SKILL.md", glue
    # A sentence-final period is not glue: the clause naming the file ended, so
    # what follows is a citation of the document being read.
    assert _anchor_sites("see `SKILL.md`. §Closed vocabularies.")[0].target is None
    # Nor is a blank line, however few characters it spends — the paragraph
    # that named the file is over. Same for a list that moves to a new item.
    assert _anchor_sites("see `SKILL.md`\n\n§Closed vocabularies.")[0].target is None
    assert _anchor_sites("- `SKILL.md`\n- §Closed vocabularies.")[0].target is None


def test_a_citation_inside_a_fence_is_still_a_citation() -> None:
    """Fenced examples carry real *path* citations — a mission spec quoting
    the paths its researcher must read, an agent's command line naming the
    script it runs — so the file pass reads fenced lines, and a fenced `§` is
    graded on the same footing rather than exempted. What a fence suppresses
    is the opposite direction: a `#` line inside it is a comment in someone's
    code sample, not a section anyone can cite. The count of fenced citations
    is not restated here — the sweep below asserts there is more than one, and
    the rest is the failure message's business."""
    fenced_paths = [
        (plugin, path.relative_to(_plugin_root(plugin)).as_posix(), lineno)
        for plugin in _plugin_names()
        for path in _prose_files(plugin)
        for text in [path.read_text(encoding="utf-8")]
        for lineno, line in enumerate(text.splitlines(), 1)
        if lineno in _fenced_lines(text)
        and any(pattern.search(line) for pattern in _PATH_PATTERNS)
    ]
    assert len(fenced_paths) > 1, (
        "no prose cites a path inside a fence any more, so grading fenced "
        f"lines pins nothing: {fenced_paths}"
    )
    doc = "# Real\n\n```markdown\nsee `spec-tls.md` §Shape of it\n```\n"
    assert [(site.target, site.text) for site in _anchor_sites(doc)] == [
        ("spec-tls.md", "Shape of it")
    ]
    assert [target for _lineno, target in _scan_text(doc)] == ["spec-tls.md"]
    # Fences bind headings, not citations.
    assert _fenced_lines(doc) == {3, 4, 5}
    assert _headings(doc) == ["Real"]


def test_fences_are_recognised_when_indented() -> None:
    """Fenced blocks nested in a list item are indented — 32 lines of real
    prose are — and an unindented fence pattern would read their contents as
    document structure."""
    assert _fenced_lines("   ```jsonc\n   {}\n   ```\n") == {1, 2, 3}
    assert _headings("# Real\n\n  ```md\n# Not a heading\n  ```\n") == ["Real"]


def test_anchored_forms_are_not_double_counted() -> None:
    """One citation, one finding. Three ways that can break: the bare-path
    pattern re-matching the tail of an anchored form, a `§` citation reported
    once per extractor that sees it, and a link target claimed by both the link
    pass and the bare-path pattern. The dedup below covers the first two even
    if a pattern over-matches; the link case it cannot, because the two passes
    report separately — that one is the lookbehind's job."""
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
    # broken link would fail two tests and read as two breaks. Both spellings
    # CommonMark allows, since a lookbehind is fixed-width and each needs its
    # own.
    assert _scan_text("See [envelope](skills/gone/spec-envelope.md).") == []
    assert _scan_text("See [envelope](<skills/gone/spec-envelope.md>).") == []
