#!/usr/bin/env python3
"""Claude Code PostToolUse hook: regenerate derived artifacts after an edit.

Wired in `.claude/settings.json` on Edit|Write. Reads the hook payload from
stdin and, when the edited file can feed a generator, runs
`render_all.py write`, so the diff a session reviews already carries the
rendered output. Any other edit exits immediately.

Exit 0 on success or a non-matching path; the generators' output passes
through so a consequential success — a regenerated file, a deleted orphan —
is visible in the transcript. Exit 2 on a generator failure so the harness
feeds the error back to the session that made the edit: an invalid record is
that session's finding to fix, and so is a census check failing after a
contract-prose edit — re-affirming the census entry is the deliberate act
`render_all.py`'s docstring describes.

The pre-commit hook in `.githooks/` covers edits made outside a session, and
CI's `render_all.py check` covers both being bypassed.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Kept identical to the staged-path gate in `.githooks/pre-commit` — the two
#: hooks guard the same generator inputs at different moments, so the lists
#: move together.
SOURCE_PREFIXES = (
    "rules/", "packages/", "plugins/", "schemas/", "scripts/", "census/",
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    file_path = (tool_input or {}).get("file_path") if isinstance(tool_input, dict) else ""
    if not file_path:
        return 0
    try:
        rel = pathlib.Path(file_path).resolve().relative_to(REPO_ROOT)
    except ValueError:
        return 0
    if not str(rel).startswith(SOURCE_PREFIXES):
        return 0
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "render_all.py"), "write"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        sys.stderr.write(
            f"\nrender_all.py write failed after editing {rel} — "
            "fix what its output names before continuing.\n")
        return 2
    sys.stdout.write(result.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
