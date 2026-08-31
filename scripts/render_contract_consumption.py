#!/usr/bin/env python3
"""Check the reachability census against the vendored consumption manifest.

The engine publishes ``contract-consumption`` — the fields its run-time path
reads — and ``census/consumption/pin.py`` vendors one pinned version. The
census walks the live contract models from the manifest's roots and holds,
for every reachable field the manifest does not claim, a
``FieldDisposition`` in ``census/consumption/dispositions.py`` saying what
consumes it instead, or declaring the gap. The census also grades the rule
records over that ground: every record whose ``targets``/``fields`` govern
an unread field carries a reader-affirmed ``RecordAffirmation`` in
``census/consumption/record_affirmations.py``, pinned to the refs and the
rationale wording judged. Each report this script prints is computed once —
``census.consumption.reachability.census_report`` and
``census.consumption.records.record_report`` — the same functions the
census test suite asserts on, so the lint and this tool can never disagree.

Usage:
    render_contract_consumption.py check    # exit 1 on any finding (CI)

Exit codes: 0 when the census is complete and current, 1 on any finding, 2
when the check could not run — a usage error, a vendored manifest the
envelope check refuses, a manifest ROOT the live tree does not hold (a
``claims`` or ``opaque`` key naming an unknown model is an exit-1 finding),
or a contract tree that does not import. A check that cannot run
prints a "could not run" line and never reads as a finding: the exit-1
remediation is "write or retire a disposition" or "re-affirm a record",
which fixes none of those.

There is no ``write`` mode. A disposition is a judgment — which consumer
reads the field off the run-time path, or what an author loses when the
engine ignores it — and nothing here can make it; the report names each
field that needs one and the author writes the entry by hand. An
affirmation is the same shape of judgment over a record's rationale.
Whether an entry's kind and reason — or an affirmed rationale — are the
right ones is the reader's half —
``.claude/rules/reachability-dispositions.md``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# This repo is the contract's SOURCE, so bootstrap the same way
# render_prose_census.py does rather than relying on an installed wheel —
# requirements-dev.txt deliberately does not install one.
sys.path.insert(0, str(REPO_ROOT / "packages" / "contract-models" / "src"))
# And the repo root, for `census/` — outside the package because it is this
# repo's machinery, not the contract.
sys.path.insert(0, str(REPO_ROOT))
# `analitiq.contracts.shared.common` reads os.environ["DOMAIN"] at import.
os.environ.setdefault("DOMAIN", "analitiq.ai")


def check(*reports) -> int:
    print("\n\n".join(report.render() for report in reports))
    return 0 if all(report.ok for report in reports) else 1


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "check"
    if mode != "check":
        print(f"usage: {argv[0]} [check]", file=sys.stderr)
        return 2

    try:
        from census.consumption.dispositions import DISPOSITIONS
        from census.consumption.pin import load_manifest
        from census.consumption.reachability import census_report
        from census.consumption.record_affirmations import AFFIRMATIONS
        from census.consumption.records import load_rules, record_report

        manifest = load_manifest()
        fields = census_report(manifest, DISPOSITIONS)
        records = record_report(manifest, load_rules(), AFFIRMATIONS)
    # Anything at all: a fault the check did not anticipate must still read
    # as "could not run" (the pin guards carry the same arm), never as a
    # finding whose remediation is to write a disposition.
    except Exception as exc:  # noqa: BLE001 — see comment above
        print(
            f"reachability census could not run: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    return check(fields, records)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
