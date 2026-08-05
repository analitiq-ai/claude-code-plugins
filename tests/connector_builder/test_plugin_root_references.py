"""Every spec file the plugin's prose points at must exist.

Agent prose routes an agent to its rules by filename. A rename, a move, or a
deleted file leaves the reference dangling — and an agent that cannot read its
spec does not fail loudly, it authors without the rules. Nothing else in the
suite notices: these are strings in markdown.

This is the same failure shape as issue #95 (prose describing something that no
longer exists, with no check), narrowed to the part this repo actually owns. It
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
- Unbackticked paths in the YAML frontmatter (`description:` citations the
  orchestrator reads to route work). Frontmatter prose does not use backticks,
  so the bare backticked sweep is blind there — which is exactly where a
  dangling citation to a since-deleted spec hid.

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

# An unbackticked `.md` path — applied ONLY inside the YAML frontmatter
# region (between the leading `---` fences), where descriptions cite specs
# without backticks. At least one directory segment is required: a bare
# filename with no slash is indistinguishable from an ordinary prose word,
# so requiring the segment avoids false positives at the cost of not seeing
# slash-less frontmatter citations (none exist; the non-vacuity test below
# keeps the form observed). The lookbehind rejects starts preceded by a
# backtick (that is `_BARE_REF`'s form) or by a path character, so the tail
# of a `${CLAUDE_PLUGIN_ROOT}/…` reference is not re-matched.
_FRONTMATTER_PATH_REF = re.compile(
    r"(?<![\w`./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.md)(?![\w-])"
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


def _frontmatter_end(lines: list[str]) -> int:
    """1-based line number of the closing `---` fence, or 0 when the document
    does not open with a frontmatter block."""
    if not lines or lines[0].strip() != "---":
        return 0
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return idx + 1
    return 0


def _frontmatter_refs(text: str) -> list[tuple[int, str]]:
    """(lineno, target) bare-path references in the frontmatter region only."""
    lines = text.splitlines()
    return [
        (lineno, match.group(1))
        # strictly between the fences: the fences themselves carry no prose
        for lineno in range(2, _frontmatter_end(lines))
        for match in _FRONTMATTER_PATH_REF.finditer(lines[lineno - 1])
    ]


def _scan_text(text: str) -> list[tuple[int, str]]:
    """Every (lineno, target) doc reference in one document's text.

    The two backtick-anchored forms are matched on every line; the
    unbackticked bare-path form only inside the frontmatter region.
    """
    return [
        (lineno, match.group(1))
        for lineno, line in enumerate(text.splitlines(), 1)
        for pattern in (_PLUGIN_ROOT_REF, _BARE_REF)
        for match in pattern.finditer(line)
    ] + _frontmatter_refs(text)


def _references() -> list[tuple[str, int, str]]:
    """Every (relpath, lineno, target) doc reference in the plugin."""
    return [
        (path.relative_to(PLUGIN_ROOT).as_posix(), lineno, target)
        for path in sorted(PLUGIN_ROOT.rglob("*.md"))
        for lineno, target in _scan_text(path.read_text(encoding="utf-8"))
    ]


def test_doc_references_resolve() -> None:
    """A dangling reference means an agent silently reads nothing."""
    paths = _plugin_paths()
    dangling = [
        (rel, lineno, target)
        for rel, lineno, target in _references()
        if target not in _EXTERNAL_REFS and not _resolves(target, paths)
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


def test_reference_detector_finds_both_forms() -> None:
    """Guard the guard: a regex that matched nothing would pass vacuously."""
    targets = [target for _rel, _lineno, target in _references()]
    plugin_root_form = [t for t in targets if "/" in t and t.startswith("skills/")]
    assert len(plugin_root_form) > 10, (
        f"only {len(plugin_root_form)} ${{CLAUDE_PLUGIN_ROOT}} references found "
        "— the prose convention changed, so the resolution check is near-vacuous."
    )
    assert len(targets) > 100, (
        f"only {len(targets)} doc references found across both forms — the "
        "bare-filename sweep is not reaching the sibling cross-references."
    )
    # The wiring this PR extended: creators are routed to their spec skill.
    assert any(t.startswith("skills/connector-spec-db/") for t in targets)
    assert "spec-sql-write-path.md" in targets


def test_frontmatter_scan_is_not_vacuous() -> None:
    """Guard the guard: zero frontmatter citations would let the bare-path
    form rot into a silent exemption instead of a red build."""
    found = [
        target
        for path in sorted(PLUGIN_ROOT.rglob("*.md"))
        for _lineno, target in _frontmatter_refs(path.read_text(encoding="utf-8"))
    ]
    assert found, (
        "no unbackticked frontmatter citations found anywhere in the plugin — "
        "the frontmatter convention changed, so the bare-path check is vacuous."
    )


# A synthetic agent document shaped like the real ones: an unbackticked
# citation in the frontmatter description, and — deliberately — the same
# unbackticked form in the body, which the bare-path pattern must NOT reach
# (body references are required to be backticked or ${CLAUDE_PLUGIN_ROOT}-
# absolute; matching bare body paths would flood the guard with prose noise).
_SYNTHETIC_AGENT = """\
---
name: synthetic-classifier
description: Classify per the release table in skills/nowhere/references/gone.md §Table.
tools: Read
---

# synthetic-classifier

Body prose mentioning skills/nowhere/references/also-gone.md without backticks.
"""


def _dangling_in(text: str) -> list[str]:
    """The scan-and-resolve pipeline of `test_doc_references_resolve`, on one
    document's text."""
    paths = _plugin_paths()
    return [
        target
        for _lineno, target in _scan_text(text)
        if target not in _EXTERNAL_REFS and not _resolves(target, paths)
    ]


def test_dangling_frontmatter_reference_is_flagged() -> None:
    """Acceptance: a frontmatter citation of a nonexistent path fails the
    guard — and the identical bare form in the body stays invisible."""
    assert _dangling_in(_SYNTHETIC_AGENT) == ["skills/nowhere/references/gone.md"]


def test_resolving_frontmatter_reference_passes() -> None:
    """The twin: the same document citing a spec that exists is clean."""
    existing = "connector-builder/references/metadata-and-versioning.md"
    twin = _SYNTHETIC_AGENT.replace("skills/nowhere/references/gone.md", existing)
    # The citation is seen (not skipped) …
    assert existing in [target for _lineno, target in _scan_text(twin)]
    # … and resolves, so nothing dangles.
    assert _dangling_in(twin) == []
