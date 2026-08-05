"""Every spec file the plugin's prose points at must exist.

Agent prose routes an agent to its rules by filename. A rename, a move, or a
deleted file leaves the reference dangling — and an agent that cannot read its
spec does not fail loudly, it authors without the rules. Nothing else in the
suite notices: these are strings in markdown.

This is the same failure shape as prose describing a thing that no longer
exists with no check catching it, narrowed to the part this repo owns. It
cannot pin the CDK's hook surface — that lives in the engine — but it can
guarantee that a spec this plugin points at is a spec that exists.

Three reference forms, all checked:

- `${CLAUDE_PLUGIN_ROOT}/skills/…/spec-x.md` — the absolute form the agent
  frontmatter uses for required reading.
- `` `spec-x.md` `` / `` `references/io-contracts.md` `` — the bare backticked
  form used for cross-references between sibling specs. This is the dominant
  form by an order of magnitude, and it is the form the round-1 review found a
  stale reference in, so leaving it unchecked would miss the very case that
  motivated this file.
- Unbackticked bare paths with a directory segment, on every line — the
  `description:` citations the orchestrator reads to route work (frontmatter
  prose does not use backticks, which is exactly where a dangling citation to
  a since-deleted spec hid) and the same unbackticked form in body prose,
  where several specs cite their siblings without backticks.

Pure text-vs-filesystem: no contract packages involved, so no `_pins` skip
guard — this always runs.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "analitiq-connector-builder"

# `${CLAUDE_PLUGIN_ROOT}/skills/foo/bar.md` — the path segment only. `.` is in
# the charset (paths need it), so a reference ending a sentence captures the
# full stop; `_resolves` strips trailing `.` and `/` rather than trying to
# express "not at the end" in the charset.
_PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")

# A backticked markdown filename, optionally with a leading directory path:
# `spec-tls.md`, `references/io-contracts.md`. Bare `.md` only — a backticked
# JSON filename (`connector.json`) names an artifact the connector author
# writes, not a document in this plugin.
_BARE_REF = re.compile(r"`((?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.md)`")

# An unbackticked `.md` path, matched on every line. At least one directory
# segment is required: a bare filename with no slash is indistinguishable
# from an ordinary prose word. That requirement is what keeps prose from
# matching — applied to every line of the plugin it yields single-digit
# matches today, all genuine citations. The lookbehind rejects starts
# preceded by a backtick (that is `_BARE_REF`'s form) or by a path
# character, so the tail of a `${CLAUDE_PLUGIN_ROOT}/…` reference is not
# re-matched. The directory-segment charset mirrors `_BARE_REF`'s — no `.`,
# so a `./`- or `../`-prefixed relative link is out of scope rather than
# captured with a prefix the suffix-resolution rule cannot resolve.
_BARE_PATH_REF = re.compile(
    r"(?<![\w`./-])((?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+\.md)(?![\w-])"
)

# References that deliberately name something outside this plugin, each a
# recorded decision. A new entry here should be rare and deserves a reason.
_EXTERNAL_REFS = {
    # The storage skill is an explicit stub: it names the specs that will exist
    # "when engine support arrives" (see its own prose).
    "spec-file-transport.md",
    "spec-stdout-transport.md",
    "spec-s3-transport.md",
    # The engine's own ADR, cited as the source of record for the write path.
    # It lives in analitiq-core; the citation says so.
    "docs/sql-write-path-v2.md",
}


def _plugin_paths() -> list[str]:
    """Every file and directory in the plugin, as plugin-root-relative posix paths."""
    return [
        p.relative_to(PLUGIN_ROOT).as_posix()
        for p in sorted(PLUGIN_ROOT.rglob("*"))
        if p.is_dir() or p.suffix == ".md"
    ]


def _resolves(target: str, paths: list[str]) -> bool:
    """Does `target` name something that exists?

    The prose writes references at whatever depth reads well from where it
    sits — `spec-tls.md`, `connector-spec-db/spec-type-maps.md`,
    `skills/connector-builder/references/io-contracts.md`, the repo-root-
    relative `plugins/analitiq-connector-builder/agents/…`, and directory
    references like `skills/connector-spec-db/examples/` all appear, and every
    one is unambiguous to a reader. So resolve the way a reader does: a
    reference resolves if it is a path suffix of something in the plugin. That
    deliberately does not check the reference was written from the right
    directory — only that the thing it names exists, which is the failure that
    silently starves an agent of its rules.
    """
    cleaned = target.rstrip("/").removeprefix(
        PLUGIN_ROOT.relative_to(REPO_ROOT).as_posix() + "/"
    )
    # `.md.` — a reference that ended a sentence. Strip only a trailing dot,
    # never one inside the name.
    cleaned = cleaned[:-1] if cleaned.endswith(".") else cleaned
    return any(path == cleaned or path.endswith("/" + cleaned) for path in paths)


def _scan_text(text: str) -> list[tuple[int, str]]:
    """Every (lineno, target) doc reference in one document's text — all
    three patterns, on every line."""
    return [
        (lineno, match.group(1))
        for lineno, line in enumerate(text.splitlines(), 1)
        for pattern in (_PLUGIN_ROOT_REF, _BARE_REF, _BARE_PATH_REF)
        for match in pattern.finditer(line)
    ]


def _references() -> list[tuple[str, int, str]]:
    """Every (relpath, lineno, target) doc reference in the plugin."""
    return [
        (path.relative_to(PLUGIN_ROOT).as_posix(), lineno, target)
        for path in sorted(PLUGIN_ROOT.rglob("*.md"))
        for lineno, target in _scan_text(path.read_text(encoding="utf-8"))
    ]


def _is_dangling(target: str, paths: list[str]) -> bool:
    """The one exemption-and-resolution predicate: a reference dangles unless
    it is allow-listed as deliberately external or names a file that exists.
    Both the real-tree sweep and the synthetic acceptance tests go through
    this, so the acceptance tests exercise the exemption logic that ships."""
    return target not in _EXTERNAL_REFS and not _resolves(target, paths)


def test_doc_references_resolve() -> None:
    """A dangling reference means an agent silently reads nothing."""
    paths = _plugin_paths()
    dangling = [
        (rel, lineno, target)
        for rel, lineno, target in _references()
        if _is_dangling(target, paths)
    ]
    assert not dangling, (
        "agent prose points at files that do not exist:\n"
        + "\n".join(
            f"  plugins/analitiq-connector-builder/{rel}:{lineno} -> {target}"
            for rel, lineno, target in dangling
        )
        + "\nFix the path, restore the file the agent is told to read, or — if "
        "the target deliberately lives outside this plugin — add it to "
        "_EXTERNAL_REFS with a reason."
    )


def test_external_ref_allowlist_is_not_stale() -> None:
    """An allow-listed name that no prose cites any more is dead config.

    Without this, `_EXTERNAL_REFS` only ever grows, and an entry could mask a
    genuine dangling reference introduced later under the same filename.
    """
    cited = {target for _rel, _lineno, target in _references()}
    unused = sorted(_EXTERNAL_REFS - cited)
    assert not unused, (
        f"_EXTERNAL_REFS entries {unused} are no longer referenced by any "
        "prose — drop them so the allow-list keeps meaning what it says."
    )


def test_reference_detector_finds_all_forms() -> None:
    """Guard the guard: a regex that matched nothing would pass vacuously."""
    targets = [target for _rel, _lineno, target in _references()]
    plugin_root_form = [t for t in targets if "/" in t and t.startswith("skills/")]
    assert len(plugin_root_form) > 10, (
        f"only {len(plugin_root_form)} ${{CLAUDE_PLUGIN_ROOT}} references found "
        "— the prose convention changed, so the resolution check is near-vacuous."
    )
    assert len(targets) > 100, (
        f"only {len(targets)} doc references found across the three forms — "
        "the bare-filename sweep is not reaching the sibling cross-references."
    )
    # The wiring this PR extended: creators are routed to their spec skill.
    assert any(t.startswith("skills/connector-spec-db/") for t in targets)
    assert "spec-sql-write-path.md" in targets
    # The unbackticked bucket on its own: 9 citations exist today (3 in
    # frontmatter descriptions, 6 in body prose). A dead pattern finds 0;
    # normal prose churn stays comfortably above the floor.
    bare_path_form = [
        match.group(1)
        for path in sorted(PLUGIN_ROOT.rglob("*.md"))
        for line in path.read_text(encoding="utf-8").splitlines()
        for match in _BARE_PATH_REF.finditer(line)
    ]
    assert len(bare_path_form) >= 5, (
        f"only {len(bare_path_form)} unbackticked bare-path citations found — "
        "the citation convention changed, so the bare-path check is near-vacuous."
    )
    # The citation that motivated the form: the drift-classifier description
    # routes bump decisions through the release table.
    assert "connector-builder/references/metadata-and-versioning.md" in bare_path_form


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
    paths = _plugin_paths()
    return [
        target
        for _lineno, target in _scan_text(text)
        if _is_dangling(target, paths)
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
