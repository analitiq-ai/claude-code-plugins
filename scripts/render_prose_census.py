#!/usr/bin/env python3
"""Check or restamp the prose census against the live contract prose.

The census (`analitiq.contracts.shared.prose_census`) catalogues EVERY prose
site in the contract models — each field description and each class docstring —
binding it to a disposition and pinning its exact wording with a content hash
(`analitiq.contracts.shared.introspect.prose_fingerprint`). The diff this
script prints is computed once, in `introspect.census_report`, the same
function `tests/unit/test_advisory_prose.py` asserts on — the lint and this
tool can never disagree.

Usage:
    render_prose_census.py check    # exit 1 on any missing / stale /
                                    # hash-mismatch / tripwire finding (CI)
    render_prose_census.py write    # restamp changed hashes in the census
                                    # area files; print skeleton entries for
                                    # uncatalogued sites

`write` never invents dispositions: it rewrites only the `prose_hash` of
entries whose prose was re-worded (re-affirm each disposition when committing
the new hash), and PRINTS ready-to-paste skeletons — `descriptive=True` by
default — for sites with no entry; the author judges the real disposition and
pastes the entry into the right area file by hand.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# This repo is the contract's SOURCE, so bootstrap the same way render_schemas.py
# does rather than relying on an installed wheel — requirements-dev.txt
# deliberately does not install one. Keeps the script runnable standalone (CI
# calls it directly, outside pytest and its conftest).
sys.path.insert(0, str(REPO_ROOT / "packages" / "contract-models" / "src"))
# `analitiq.contracts.shared.common` reads os.environ["DOMAIN"] at import and
# raises KeyError without it.
os.environ.setdefault("DOMAIN", "analitiq.ai")

CENSUS_DIR = (
    REPO_ROOT
    / "packages"
    / "contract-models"
    / "src"
    / "analitiq"
    / "contracts"
    / "shared"
    / "prose_census"
)


def _grouped_by_module(records, module_of):
    groups: dict[str, list] = {}
    for record in records:
        groups.setdefault(module_of(record), []).append(record)
    return sorted(groups.items())


def _print_group(title: str, records, module_of, line_of) -> None:
    if not records:
        return
    print(f"\n{title} ({len(records)}):")
    for module, group in _grouped_by_module(records, module_of):
        print(f"  {module}:")
        for record in group:
            print(f"    {line_of(record)}")


def _entry_spans(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line index of every ProseObligation(...) block.

    Entries are in canonical census format: a block opens with an indented
    ``ProseObligation(`` and closes either on the same line (the one-line
    descriptive form) or on a line that is exactly ``),`` at the same indent.
    """
    spans = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("    ProseObligation("):
            if lines[i].rstrip().endswith("),"):
                spans.append((i, i))
            else:
                j = i + 1
                while lines[j].rstrip() != "    ),":
                    j += 1
                spans.append((i, j))
                i = j
        i += 1
    return spans


def _block_key(block: str) -> tuple[str, str | None]:
    model = re.search(r'model="([^"]+)"', block).group(1)
    field_match = re.search(r'field="([^"]+)"', block)
    return (model, field_match.group(1) if field_match else None)


def _restamp(key: tuple[str, str | None], new_hash: str) -> Path | None:
    """Rewrite the entry's prose_hash in whichever area file holds it."""
    for path in sorted(CENSUS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        for start, end in _entry_spans([l.rstrip("\n") for l in lines]):
            block = "".join(lines[start : end + 1])
            if _block_key(block) != key:
                continue
            new_block = re.sub(
                r'prose_hash="[0-9a-f]{12}"', f'prose_hash="{new_hash}"', block
            )
            lines[start : end + 1] = [new_block]
            path.write_text("".join(lines), encoding="utf-8")
            return path
    return None


def _skeleton(site) -> str:
    field_part = f' field="{site.field}",' if site.field else ""
    return (
        f'    ProseObligation(model="{site.model}",{field_part} '
        f'prose_hash="{site.fingerprint}", descriptive=True),'
    )


def check(report) -> int:
    _print_group(
        "uncatalogued prose sites — add census entries "
        "(`write` prints skeletons)",
        report.missing,
        lambda s: s.module,
        lambda s: f"{s.label}: hash {s.fingerprint}",
    )
    if report.stale:
        print(f"\nstale census entries — no matching prose site ({len(report.stale)}):")
        for model, field in report.stale:
            print(f"    {model}.{field or '(docstring)'}")
    _print_group(
        "hash mismatches — prose re-worded since its disposition was "
        "affirmed (re-affirm, then `write` restamps)",
        report.hash_mismatches,
        lambda m: m.site.module,
        lambda m: f"{m.site.label}: census {m.recorded} -> live {m.site.fingerprint}",
    )
    _print_group(
        "tripwire — descriptive=True on modal prose (use waiver=DESCRIPTIVE "
        "or a real disposition)",
        report.tripwires,
        lambda s: s.module,
        lambda s: s.label,
    )
    if report.clean:
        print("prose census is complete and current")
        return 0
    return 1


def write(report) -> int:
    if report.hash_mismatches:
        print(f"restamped — RE-AFFIRM each disposition ({len(report.hash_mismatches)}):")
        for mismatch in report.hash_mismatches:
            path = _restamp(mismatch.site.key, mismatch.site.fingerprint)
            if path is None:
                print(
                    f"    {mismatch.site.label}: entry not found in any census "
                    "area file (non-canonical format?) — restamp by hand"
                )
            else:
                print(
                    f"    {mismatch.site.label}: {mismatch.recorded} -> "
                    f"{mismatch.site.fingerprint} in {path.relative_to(REPO_ROOT)}"
                )
    if report.stale:
        print(f"\nstale entries — remove or re-key by hand ({len(report.stale)}):")
        for model, field in report.stale:
            print(f"    {model}.{field or '(docstring)'}")
    if report.missing:
        print(
            f"\nnew sites needing entries ({len(report.missing)}) — skeletons "
            "below default to descriptive=True; judge the real disposition "
            "before pasting into the area file:"
        )
        for module, group in _grouped_by_module(report.missing, lambda s: s.module):
            print(f"\n  # {module}")
            for site in group:
                print(_skeleton(site))
    if report.clean:
        print("prose census is complete and current — nothing to do")
    return 0


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "check"
    if mode not in ("write", "check"):
        print(f"usage: {argv[0]} [write|check]", file=sys.stderr)
        return 2

    from analitiq.contracts.shared.introspect import census_report

    report = census_report()
    return write(report) if mode == "write" else check(report)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
