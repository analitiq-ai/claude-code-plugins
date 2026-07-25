"""Every `${CLAUDE_PLUGIN_ROOT}/...` path an agent is told to read must exist.

Agent prose routes an agent to its specs by path. A rename, a move, or a
deleted file leaves the reference dangling — and an agent that cannot read its
spec does not fail loudly, it authors without the rules. Nothing else in the
suite notices: the paths are strings in markdown.

This is the same failure shape as issue #95 (prose describing something that no
longer exists, with no check), narrowed to the part this repo actually owns. It
cannot pin the CDK's hook surface — that lives in the engine — but it can
guarantee that a spec this plugin points at is a spec that exists.

Pure text-vs-filesystem: no contract packages involved, so no `_pins` skip
guard — this always runs.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "analitiq-connector-builder"

# `${CLAUDE_PLUGIN_ROOT}/skills/foo/bar.md` — the path segment only. Trailing
# punctuation (a sentence's full stop, a closing paren, a backtick) is excluded
# by the charset; a directory reference ending in `/` is kept and resolves as a
# directory.
_PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")


def _references() -> list[tuple[str, int, str]]:
    """Every (relpath, lineno, target) `${CLAUDE_PLUGIN_ROOT}` reference in the plugin."""
    return [
        (path.relative_to(PLUGIN_ROOT).as_posix(), lineno, match.group(1))
        for path in sorted(PLUGIN_ROOT.rglob("*.md"))
        for lineno, line in enumerate(path.read_text().splitlines(), 1)
        for match in _PLUGIN_ROOT_REF.finditer(line)
    ]


def test_plugin_root_references_resolve() -> None:
    """A dangling reference means an agent silently reads nothing."""
    dangling = [
        (rel, lineno, target)
        for rel, lineno, target in _references()
        if not (PLUGIN_ROOT / target.rstrip("/")).exists()
    ]
    assert not dangling, (
        "agent prose points at files that do not exist:\n"
        + "\n".join(
            f"  plugins/analitiq-connector-builder/{rel}:{lineno} -> {target}"
            for rel, lineno, target in dangling
        )
        + "\nFix the path, or restore the file the agent is told to read."
    )


def test_reference_detector_still_finds_references() -> None:
    """Guard the guard: a regex that matches nothing would pass vacuously.

    Also pins that the creator agents keep being routed to their spec skill —
    the wiring this PR extended when it added `spec-sql-write-path.md`.
    """
    refs = _references()
    assert len(refs) > 10, (
        f"only {len(refs)} ${{CLAUDE_PLUGIN_ROOT}} references found — the prose "
        "convention changed, so the resolution check above is near-vacuous."
    )
    targets = {target for _rel, _lineno, target in refs}
    assert any(t.startswith("skills/connector-spec-db/") for t in targets)
