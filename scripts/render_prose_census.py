#!/usr/bin/env python3
"""Check or restamp the prose census against the live contract prose.

The census (`analitiq.contracts.shared.prose_census`) catalogues EVERY prose
site in the contract tree — each field description and docstring of every
pydantic model, and the docstring of every Enum, under `analitiq.contracts`
(membership by category, mechanical and judgment-free: public enum docstrings
are published into schema descriptions, and private helper enums ride along
rather than requiring a per-class publishability judgment; plain classes and
enum member docstrings publish nothing and are out of scope) — binding each
site to a disposition and pinning
its exact wording with a content hash
(`analitiq.contracts.shared.introspect.prose_fingerprint`). The diff this
script prints is computed once, in `introspect.census_report`, the same
function `tests/unit/test_advisory_prose.py` asserts on — the lint and this
tool can never disagree.

Usage:
    render_prose_census.py check    # exit 1 on any missing / stale /
                                    # hash-mismatch finding (CI)
    render_prose_census.py write    # restamp changed hashes in the census
                                    # area files; print skeleton entries for
                                    # uncatalogued sites. Exits non-zero when
                                    # manual work remains after its edits
                                    # (an entry it could not restamp, a
                                    # stale entry, an uncatalogued site);
                                    # exits 0 only when the census is fully
                                    # current.

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


def _entry_spans(lines: list[str], source: str) -> list[tuple[int, int]]:
    """(start, end) line index of every ProseObligation(...) block.

    Entries are in canonical census format: a block opens with an indented
    ``ProseObligation(`` and closes either on the same line (the one-line
    form, recognizable by its inline ``prose_hash="``: a first line merely
    ending in ``),`` may be a multi-line entry whose opening line ends with a
    tuple kwarg) or on a later line that is exactly ``),`` at the same
    indent. A block that never closes is malformed and reported with its
    source file and opening line rather than walked off the end.
    """
    spans = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("    ProseObligation("):
            if lines[i].rstrip().endswith("),") and 'prose_hash="' in lines[i]:
                spans.append((i, i))
            else:
                j = i + 1
                while j < len(lines) and lines[j].rstrip() != "    ),":
                    j += 1
                if j >= len(lines):
                    raise ValueError(
                        f"{source}: unclosed ProseObligation block starting at "
                        f"line {i + 1}: {lines[i].strip()!r} — expected a "
                        'closing "    )," line'
                    )
                spans.append((i, j))
                i = j
        i += 1
    return spans


def _block_key(block: str):
    from analitiq.contracts.shared.introspect import SiteKey

    # Key kwargs (`model=`, `field=`) always precede the quote-delimited
    # structural/waiver texts, which may themselves contain `field="..."` —
    # so only the head of the block, up to the first of those kwargs, is
    # searched.
    head = re.split(r"\b(?:structural|waiver)\s*=", block)[0]
    model = re.search(r'model="([^"]+)"', head).group(1)
    field_match = re.search(r'field="([^"]+)"', head)
    return SiteKey(model=model, field=field_match.group(1) if field_match else None)


def _restamp(key, new_hash: str) -> Path | None:
    """Rewrite the entry's prose_hash in whichever area file holds it.

    Returns the rewritten file, or ``None`` when no entry was actually
    restamped — either no area file holds the entry, or the matched block
    carries no substitutable ``prose_hash="..."`` (both mean: restamp by
    hand).
    """
    for path in sorted(CENSUS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        stripped = [l.rstrip("\n") for l in lines]
        for start, end in _entry_spans(stripped, path.name):
            block = "".join(lines[start : end + 1])
            if _block_key(block) != key:
                continue
            new_block, count = re.subn(
                r'prose_hash="[0-9a-f]{12}"', f'prose_hash="{new_hash}"', block
            )
            if count == 0:
                return None
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
        for key in report.stale:
            print(f"    {key.label}")
    _print_group(
        "hash mismatches — prose re-worded since its disposition was "
        "affirmed (re-affirm, then `write` restamps)",
        report.hash_mismatches,
        lambda m: m.site.module,
        lambda m: f"{m.site.label}: census {m.recorded} -> live {m.site.fingerprint}",
    )
    if report.clean:
        print("prose census is complete and current")
        return 0
    return 1


def write(report) -> int:
    unrestamped = 0
    if report.hash_mismatches:
        print(f"restamped — RE-AFFIRM each disposition ({len(report.hash_mismatches)}):")
        for mismatch in report.hash_mismatches:
            path = _restamp(mismatch.site.key, mismatch.site.fingerprint)
            if path is None:
                unrestamped += 1
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
        for key in report.stale:
            print(f"    {key.label}")
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
    # Restamps are the only edit `write` makes itself; everything else above
    # is manual work still to do, so the exit code must say so.
    return 1 if (unrestamped or report.stale or report.missing) else 0


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
