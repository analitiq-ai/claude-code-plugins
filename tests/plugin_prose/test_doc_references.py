"""Every citation a plugin's prose makes resolves to something that exists.

Agent prose routes an agent to its rules by filename, and to the part of that
file that carries the rule by section. A rename, a move, or a deleted file
leaves the citation dangling — and an agent that cannot read what it was sent
to read does not fail loudly, it authors without the rules. Nothing else in the
suite notices: these are strings in markdown.

Two claims, both checked, for **every** plugin under `plugins/` (the roots are
discovered, not listed, so a third plugin is guarded the day it lands):

1. **The file exists.** Four citation forms carry it:

   - `${CLAUDE_PLUGIN_ROOT}/skills/…/spec-x.md` — the absolute form the agent
     frontmatter uses for required reading. It also names scripts an agent
     runs, so the path universe is every file in the plugin, not only `.md`.
   - `` `spec-x.md` `` / `` `references/io-contracts.md` `` — the bare
     backticked form used for cross-references between sibling specs. This is
     the dominant form by an order of magnitude.
   - Unbackticked bare paths with a directory segment, on every line — the
     `description:` citations the orchestrator reads to route work (frontmatter
     prose does not use backticks, which is exactly where a dangling citation
     to a since-deleted spec hid) and the same unbackticked form in body prose.
   - The path half of a `§` citation, which the three patterns above can miss:
     `` `SKILL.md §Pipeline` `` puts the anchor inside the backticks, so the
     backticked pattern never sees a closing backtick after `.md`.

2. **The section exists.** A `path.md §Heading` citation makes a second claim
   the file check never opens: that the heading is still there. A heading
   rename leaves the citation half-dangling — the file opens, the section the
   agent was sent to read is gone. `§` with no file in front of it cites a
   section of the citing document itself, and is resolved against it.

Pure text-vs-filesystem: no contract packages involved, so no `_pins` skip
guard — this always runs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = REPO_ROOT / "plugins"


def _plugin_names() -> list[str]:
    """Every plugin directory, discovered. A hand-listed set would leave the
    next plugin silently unguarded — the failure this file exists to prevent,
    one level up."""
    return sorted(p.name for p in PLUGINS_DIR.iterdir() if p.is_dir())


# `${CLAUDE_PLUGIN_ROOT}/skills/foo/bar.md` — the path segment only. `.` is in
# the charset (paths need it), so a reference ending a sentence captures the
# full stop; `_resolves` strips trailing `.` and `/` rather than trying to
# express "not at the end" in the charset.
_PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")

# A backticked markdown filename, optionally with a leading directory path:
# `spec-tls.md`, `references/io-contracts.md`. Bare `.md` only — a backticked
# `connector.json` or `connector.py` names an artifact the connector author
# writes, not a document in this plugin. A plugin file with another suffix is
# reachable through the `${CLAUDE_PLUGIN_ROOT}` form, which says outright that
# the path is inside the plugin, and every agent that runs a helper script
# cites it that way.
_BARE_REF = re.compile(r"`((?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.md)`")

# An unbackticked `.md` path, matched on every line. At least one directory
# segment is required: a bare filename with no slash is indistinguishable
# from an ordinary prose word. That requirement is what keeps prose from
# matching — applied to every line of a plugin it yields single-digit
# matches today, all genuine citations. The lookbehind rejects starts
# preceded by a backtick (that is `_BARE_REF`'s form) or by a path
# character, so the tail of a `${CLAUDE_PLUGIN_ROOT}/…` reference is not
# re-matched. The directory-segment charset mirrors `_BARE_REF`'s — no `.`,
# so a `./`- or `../`-prefixed relative link is out of scope rather than
# captured with a prefix the suffix-resolution rule cannot resolve.
_BARE_PATH_REF = re.compile(
    r"(?<![\w`./-])((?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+\.md)(?![\w-])"
)

_PATH_PATTERNS = (_PLUGIN_ROOT_REF, _BARE_REF, _BARE_PATH_REF)

# The file a `§` binds to: the last `.md` path before it, separated by nothing
# but glue — a closing backtick, whitespace (the citation often wraps a line),
# an opening paren or quote. More than that and the `§` is prose-separated from
# the path, which is how a bare `§Closed vocabularies` — a section of the
# citing document itself — is told apart from `` `SKILL.md` §Closed
# vocabularies``.
_ANCHOR_BINDING = re.compile(r"([A-Za-z0-9_./-]+\.md)[`\"'\s(]{0,8}$")

# The anchor text after `§`. Quoted form first: a heading with internal
# punctuation is quoted precisely so the stop set below cannot cut it short.
_QUOTED_ANCHOR = re.compile(r'\s*["“]([^"”]{1,200})["”]')

# Where an unquoted anchor ends: a closing bracket, a clause break, an
# em-dash, or a sentence-final period. Prose continuing past one of these is
# no longer the heading — `§Import rules is the list, and it is short.` cites
# `Import rules`, and the token-prefix rule in `_anchor_resolves` is what lets
# the surviving prose tail ride along.
_ANCHOR_STOP = re.compile(r"[)\]},;—]|\.(?=[\s`]|$)|\n[ \t]*\n")

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
        # The engine's own ADR, cited as the source of record for the write
        # path. It lives in analitiq-core; the citation says so.
        "docs/sql-write-path-v2.md",
    },
    "analitiq-pipeline-builder": set(),
}

# Per plugin, per citation form: the floor below which the extractor is not
# reading that plugin's prose any more. A pattern that stops matching passes
# vacuously, which is the one way a guard like this dies silently, so each form
# is floored separately — a floor on the total would let one dead pattern hide
# behind another's growth. Set well under today's counts: normal prose churn
# must not move them, a broken extractor must.
_FLOORS: dict[str, dict[str, int]] = {
    "analitiq-connector-builder": {
        "plugin_root": 10,  # 22 today
        "backticked": 70,  # 147 today
        "bare_path": 5,  # 9 today
        "anchor": 15,  # 32 today
    },
    "analitiq-pipeline-builder": {
        "plugin_root": 2,  # 4 today
        "backticked": 50,  # 97 today
        "bare_path": 2,  # 4 today
        "anchor": 4,  # 9 today
    },
}


def _plugin_root(plugin: str) -> Path:
    return PLUGINS_DIR / plugin


def _plugin_paths(plugin: str) -> list[str]:
    """Every file and directory in the plugin, as plugin-root-relative posix
    paths. Every file, not only `.md`: agent frontmatter cites the helper
    scripts it runs by the same `${CLAUDE_PLUGIN_ROOT}/…` form, and a citation
    of a deleted script starves an agent exactly as a citation of a deleted
    spec does."""
    root = _plugin_root(plugin)
    return [p.relative_to(root).as_posix() for p in sorted(root.rglob("*"))]


def _repo_plugin_paths() -> list[str]:
    """Every path under `plugins/`, repo-relative — the universe a fully
    qualified `plugins/<other-plugin>/…` citation resolves against."""
    return [
        p.relative_to(REPO_ROOT).as_posix() for p in sorted(PLUGINS_DIR.rglob("*"))
    ]


def _clean(target: str) -> str:
    """A citation as written, reduced to the path it names: no trailing slash
    on a directory reference, no sentence-final period swept up by the `.` in
    the path charset (only a trailing one, never one inside the name)."""
    cleaned = target.rstrip("/")
    return cleaned[:-1] if cleaned.endswith(".") else cleaned


def _resolves(target: str, plugin: str, paths: list[str]) -> bool:
    """Does `target` name something that exists?

    The prose writes citations at whatever depth reads well from where it sits
    — `spec-tls.md`, `connector-spec-db/spec-type-maps.md`,
    `skills/connector-builder/references/io-contracts.md` and directory
    citations like `skills/connector-spec-db/examples/` all appear, and every
    one is unambiguous to a reader. So resolve the way a reader does: a
    citation resolves if it is a path suffix of something in the plugin. That
    deliberately does not check the citation was written from the right
    directory — only that the thing it names exists, which is the failure that
    silently starves an agent of its rules.

    A citation that spells out `plugins/<name>/…` is the exception: it is fully
    qualified, may name a sibling plugin, and is matched exactly against the
    repo's `plugins/` tree.
    """
    cleaned = _clean(target)
    if cleaned.startswith("plugins/"):
        return cleaned in _repo_plugin_paths()
    return any(path == cleaned or path.endswith("/" + cleaned) for path in paths)


def _resolve_file(target: str, citing: str, plugin: str) -> Path | None:
    """The concrete file a citation names, resolved the way a reader resolves
    it — needed by the anchor pass, which must open the file.

    Nearest ancestor first: `SKILL.md` cited from
    `skills/pipeline-builder/references/pipeline.md` is that skill's own
    `SKILL.md`, not one of the four others in the plugin. Only when no ancestor
    directory holds the path does this fall back to the suffix rule `_resolves`
    uses, which is how a spec cited from `agents/` reaches
    `skills/<spec-skill>/`.
    """
    root = _plugin_root(plugin)
    cleaned = _clean(target)
    if cleaned.startswith("plugins/"):
        candidate = REPO_ROOT / cleaned
        return candidate if candidate.is_file() else None
    for ancestor in (root / citing).parents:
        if not ancestor.is_relative_to(root):
            break
        candidate = ancestor / cleaned
        if candidate.is_file():
            return candidate
    matches = [
        root / path
        for path in _plugin_paths(plugin)
        if path.endswith("/" + cleaned) and (root / path).is_file()
    ]
    return matches[0] if len(matches) == 1 else None


def _scan_text(text: str) -> list[tuple[int, str]]:
    """Every (lineno, target) path citation in one document's text — the three
    path patterns on every line, plus the path half of every `§` citation."""
    from_paths = [
        (lineno, match.group(1))
        for lineno, line in enumerate(text.splitlines(), 1)
        for pattern in _PATH_PATTERNS
        for match in pattern.finditer(line)
    ]
    from_anchors = [
        (lineno, target)
        for lineno, target, _anchor in _anchor_sites(text)
        if target is not None
    ]
    return from_paths + from_anchors


def _anchor_sites(text: str) -> list[tuple[int, str | None, str]]:
    """Every (lineno, target-or-None, anchor) `§` citation in one document.

    Scanned over the whole text, not line by line: a citation that wraps —
    ``§Dialect\\n  hooks)`` — is one citation, and a per-line scan would read
    half of it.
    """
    sites: list[tuple[int, str | None, str]] = []
    for marker in re.finditer("§", text):
        lineno = text.count("\n", 0, marker.start()) + 1
        binding = _ANCHOR_BINDING.search(text[: marker.start()])
        target = binding.group(1) if binding else None
        sites.append((lineno, target, _anchor_text(text[marker.end() :])))
    return sites


def _anchor_text(rest: str) -> str:
    """The heading an anchor names, cut out of the prose that follows it."""
    quoted = _QUOTED_ANCHOR.match(rest)
    if quoted:
        return quoted.group(1)
    window = rest[:200]
    stop = _ANCHOR_STOP.search(window)
    return (window[: stop.start()] if stop else window).strip()


def _headings(text: str) -> list[str]:
    """Every ATX heading in a document, fenced blocks excluded — a `# comment`
    inside a python fence is not a section anyone can cite."""
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


def _anchor_resolves(anchor: str, headings: list[str]) -> bool:
    """Does the cited section exist in the target file?

    Compared as word sequences, in both directions, because prose abbreviates
    in one direction and runs on in the other: `§Encoding values` cites
    `## Encoding values (closed enum)` (the citation is a prefix of the
    heading), and `§Import rules owns the list` cites `### Import rules` (the
    heading is a prefix of what the extractor cut out). Either way a renamed
    heading stops matching at the first word that changed, which is the failure
    this pass exists to catch.
    """
    cited = _tokens(anchor)
    if not cited:
        return False
    for heading in headings:
        actual = _tokens(heading)
        if not actual:
            continue
        shorter, longer = sorted((cited, actual), key=len)
        if longer[: len(shorter)] == shorter:
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


def _anchor_references(plugin: str) -> list[tuple[str, int, str | None, str]]:
    """Every (relpath, lineno, target-or-None, anchor) `§` citation."""
    root = _plugin_root(plugin)
    return [
        (path.relative_to(root).as_posix(), lineno, target, anchor)
        for path in sorted(root.rglob("*.md"))
        for lineno, target, anchor in _anchor_sites(
            path.read_text(encoding="utf-8")
        )
    ]


def _is_dangling(target: str, plugin: str, paths: list[str]) -> bool:
    """The one exemption-and-resolution predicate: a citation dangles unless it
    is allow-listed as deliberately external or names a file that exists. Both
    the real-tree sweep and the synthetic acceptance tests go through this, so
    the acceptance tests exercise the exemption logic that ships."""
    return target not in _EXTERNAL_REFS[plugin] and not _resolves(
        target, plugin, paths
    )


def _dangling_anchors(
    plugin: str, sites: list[tuple[str, int, str | None, str]]
) -> list[tuple[str, int, str, str]]:
    """Every `§` citation whose section is not in the file it points at.

    A citation whose *file* is missing is not reported here — that is the file
    pass's finding, and reporting it twice would make one break read as two.
    """
    dangling = []
    for rel, lineno, target, anchor in sites:
        if target in _EXTERNAL_REFS[plugin]:
            continue
        # No path in front of the `§`: the citation names a section of the
        # document it sits in.
        own = _plugin_root(plugin) / rel
        path = _resolve_file(target, rel, plugin) if target else own
        if path is None or not path.is_file():
            continue
        if not _anchor_resolves(anchor, _headings(path.read_text(encoding="utf-8"))):
            dangling.append((rel, lineno, target or rel, anchor))
    return dangling


@pytest.mark.parametrize("plugin", _plugin_names())
def test_doc_references_resolve(plugin: str) -> None:
    """A dangling citation means an agent silently reads nothing."""
    paths = _plugin_paths(plugin)
    dangling = [
        (rel, lineno, target)
        for rel, lineno, target in _references(plugin)
        if _is_dangling(target, plugin, paths)
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
def test_section_anchors_resolve(plugin: str) -> None:
    """A `§` citation whose heading is gone opens the file and starves the
    agent of the rule anyway — the half-dangling case the file pass cannot
    see."""
    dangling = _dangling_anchors(plugin, _anchor_references(plugin))
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
    cited = {target for _rel, _lineno, target in _references(plugin)}
    unused = sorted(_EXTERNAL_REFS[plugin] - cited)
    assert not unused, (
        f"_EXTERNAL_REFS[{plugin!r}] entries {unused} are no longer referenced "
        "by any prose — drop them so the allow-list keeps meaning what it says."
    )


def test_every_plugin_is_covered() -> None:
    """The guard's own reachability: a plugin that lands without an entry in
    the two per-plugin registries would be scanned with no floors and no
    allow-list, which is how the pipeline plugin went unguarded in the first
    place."""
    names = set(_plugin_names())
    assert set(_FLOORS) == names, (
        f"_FLOORS covers {sorted(_FLOORS)}, plugins are {sorted(names)} — give "
        "the new plugin its own non-vacuity floors."
    )
    assert set(_EXTERNAL_REFS) == names, (
        f"_EXTERNAL_REFS covers {sorted(_EXTERNAL_REFS)}, plugins are "
        f"{sorted(names)} — add the new plugin, with an empty set if it cites "
        "nothing outside itself."
    )


def _form_counts(plugin: str) -> dict[str, int]:
    """How many citations each extractor finds in a plugin, per form."""
    texts = [
        path.read_text(encoding="utf-8")
        for path in sorted(_plugin_root(plugin).rglob("*.md"))
    ]
    per_line = {
        name: sum(
            len(pattern.findall(line))
            for text in texts
            for line in text.splitlines()
        )
        for name, pattern in (
            ("plugin_root", _PLUGIN_ROOT_REF),
            ("backticked", _BARE_REF),
            ("bare_path", _BARE_PATH_REF),
        )
    }
    return per_line | {"anchor": sum(len(_anchor_sites(text)) for text in texts)}


@pytest.mark.parametrize("plugin", _plugin_names())
def test_citation_detector_finds_every_form(plugin: str) -> None:
    """Guard the guard: an extractor that matched nothing would pass
    vacuously."""
    counts = _form_counts(plugin)
    below = {
        form: (counts[form], floor)
        for form, floor in _FLOORS[plugin].items()
        if counts[form] < floor
    }
    assert not below, (
        f"citation forms below their floor in plugins/{plugin}: "
        + ", ".join(
            f"{form} found {found}, floor {floor}"
            for form, (found, floor) in sorted(below.items())
        )
        + " — either the citation convention changed (repoint the extractor "
        "and the floor together) or the extractor is broken and this check "
        "was about to pass vacuously."
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

# Per plugin: a real file, and a heading it really carries, so a synthetic
# document can dangle one citation while the other holds — and so the anchor
# acceptance tests below are checked against a heading that exists rather than
# one invented for the test.
_REAL_CITATION = {
    "analitiq-connector-builder": (
        "skills/connector-builder/references/metadata-and-versioning.md",
        "Release version",
    ),
    "analitiq-pipeline-builder": (
        "skills/pipeline-builder/references/identity-and-versioning.md",
        "Metadata fields",
    ),
}


def _dangling_in(text: str, plugin: str) -> list[str]:
    """The scan-and-resolve pipeline of `test_doc_references_resolve`, on one
    document's text."""
    paths = _plugin_paths(plugin)
    return [
        target
        for _lineno, target in _scan_text(text)
        if _is_dangling(target, plugin, paths)
    ]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_dangling_frontmatter_citation_is_flagged(plugin: str) -> None:
    """Acceptance: a frontmatter citation of a nonexistent path fails the
    guard. The motivating case — frontmatter is where the dangling citation
    this guard exists for was hiding."""
    existing, _heading = _REAL_CITATION[plugin]
    doc = _SYNTHETIC_AGENT.replace("skills/nowhere/references/also-gone.md", existing)
    assert _dangling_in(doc, plugin) == ["skills/nowhere/references/gone.md"]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_dangling_body_citation_is_flagged(plugin: str) -> None:
    """Acceptance: an unbackticked body citation of a nonexistent path fails
    the guard — body lines are swept exactly like frontmatter lines."""
    existing, _heading = _REAL_CITATION[plugin]
    doc = _SYNTHETIC_AGENT.replace("skills/nowhere/references/gone.md", existing)
    assert _dangling_in(doc, plugin) == ["skills/nowhere/references/also-gone.md"]


@pytest.mark.parametrize("plugin", _plugin_names())
def test_resolving_citations_pass(plugin: str) -> None:
    """The twin: the same document citing specs that exist is clean."""
    existing, _heading = _REAL_CITATION[plugin]
    twin = _SYNTHETIC_AGENT.replace(
        "skills/nowhere/references/gone.md", existing
    ).replace("skills/nowhere/references/also-gone.md", existing)
    # Both citations are seen (not skipped) …
    assert [target for _lineno, target in _scan_text(twin)].count(existing) == 2
    # … and resolve, so nothing dangles.
    assert _dangling_in(twin, plugin) == []


@pytest.mark.parametrize("plugin", _plugin_names())
def test_dangling_anchor_in_a_resolving_file_is_flagged(plugin: str) -> None:
    """Acceptance: the half-dangling case. The file opens; the section named
    does not exist in it. The twin below pins that the same citation with the
    real heading passes, so this is the anchor doing the work, not the file
    check failing for its own reasons."""
    existing, _heading = _REAL_CITATION[plugin]
    citing = "agents/synthetic-classifier.md"
    doc = f"Author per `{existing}` §Heading that was renamed away.\n"
    sites = [(citing, lineno, t, a) for lineno, t, a in _anchor_sites(doc)]
    assert _dangling_anchors(plugin, sites) == [
        (citing, 1, existing, "Heading that was renamed away")
    ]
    # The file half is clean — this failure is the anchor's alone.
    assert _dangling_in(doc, plugin) == []


@pytest.mark.parametrize("plugin", _plugin_names())
def test_resolving_anchor_passes(plugin: str) -> None:
    """The twin: the same citation naming a heading that exists is clean."""
    existing, heading = _REAL_CITATION[plugin]
    citing = "agents/synthetic-classifier.md"
    doc = f"Author per `{existing}` §{heading}, then stop.\n"
    sites = [(citing, lineno, t, a) for lineno, t, a in _anchor_sites(doc)]
    assert sites == [(citing, 1, existing, heading)]
    assert _dangling_anchors(plugin, sites) == []


@pytest.mark.parametrize("plugin", _plugin_names())
def test_bare_anchor_binds_to_the_citing_document(plugin: str) -> None:
    """A `§` with no path in front of it cites a section of the document it
    sits in — the form `§Closed vocabularies` uses. Resolved against the citing
    file, a nonexistent section still fails."""
    rel, _heading = _REAL_CITATION[plugin]
    own_heading = _headings(
        (_plugin_root(plugin) / rel).read_text(encoding="utf-8")
    )[-1]
    good = [(rel, 1, t, a) for _l, t, a in _anchor_sites(f"See §{own_heading}.\n")]
    assert good and good[0][2] is None
    assert _dangling_anchors(plugin, good) == []
    bad = [(rel, 1, t, a) for _l, t, a in _anchor_sites("See §Nowhere at all.\n")]
    assert [site[3] for site in _dangling_anchors(plugin, bad)] == ["Nowhere at all"]


def test_wrapped_anchor_is_read_whole() -> None:
    """An anchor that wraps a line is one citation, not a truncated one — the
    per-line scan the file pass uses would read `Dialect` and miss `hooks`."""
    text = "see `spec-connector-package.md` §Dialect\n  hooks). The engine\n"
    assert _anchor_sites(text) == [(1, "spec-connector-package.md", "Dialect\n  hooks")]
    assert _anchor_resolves("Dialect\n  hooks", ["Dialect hooks"])


def test_anchored_forms_are_not_double_counted() -> None:
    """A line carrying a backticked citation and a ${CLAUDE_PLUGIN_ROOT}
    citation yields exactly one match per target: the bare-path pattern's
    lookbehind must not re-match the path tail inside either anchored form."""
    line = (
        "Read ${CLAUDE_PLUGIN_ROOT}/skills/connector-spec-db/spec-tls.md "
        "and `references/io-contracts.md`."
    )
    targets = [target for _lineno, target in _scan_text(line)]
    assert targets.count("skills/connector-spec-db/spec-tls.md") == 1
    assert targets.count("references/io-contracts.md") == 1
    assert len(targets) == 2
