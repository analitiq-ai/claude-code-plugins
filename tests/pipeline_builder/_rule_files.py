"""Where the pipeline plugin's generated rule files live.

The per-scope files under the pipeline skill's `references/rules/` are rendered
whole by `scripts/render_rule_reference.py` — every row, including the contract
values it substitutes live — and `test_rule_reference_sync.py` fails on any
byte of drift in them. The gates that consume this helper police
*hand-authored* prose outside generated regions, so they skip these files the
same way they mask a `BEGIN GENERATED` block: the content is a rendering of
the contract, not a copy that can rot.

The path is read from the renderer's `OUTPUT_DIRS` rather than restated here,
so the renderer stays the one owner of its output layout.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def rule_reference_root() -> Path:
    path = _REPO_ROOT / "scripts" / "render_rule_reference.py"
    spec = importlib.util.spec_from_file_location("render_rule_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OUTPUT_DIRS["pipeline-plugin"]
