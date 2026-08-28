#!/usr/bin/env python3
"""Check the reachability census against the vendored consumption manifest.

The engine publishes ``contract-consumption`` — the fields its run-time path
reads — and ``census/consumption/pin.py`` vendors one pinned version. The
census walks the live contract models from the manifest's roots and holds,
for every reachable field the manifest does not claim, a
``FieldDisposition`` in ``census/consumption/dispositions.py`` saying what
consumes it instead, or declaring the gap. The report this script prints is
computed once, in ``census.consumption.reachability.census_report``, the
same function ``tests/census/test_contract_consumption.py`` asserts on — the
lint and this tool can never disagree.

Usage:
    render_contract_consumption.py check    # exit 1 on any finding (CI)

Exit codes: 0 when the census is complete and current, 1 on any finding, 2
when the check could not run — a usage error, a vendored manifest the
envelope check refuses, a manifest naming a model the live tree does not
hold, or a contract tree that does not import. A check that cannot run
prints a "could not run" line and never reads as a finding: the exit-1
remediation is "write or retire a disposition", which fixes none of those.

There is no ``write`` mode. A disposition is a judgment — which consumer
reads the field off the run-time path, or what an author loses when the
engine ignores it — and nothing here can make it; the report names each
field that needs one and the author writes the entry by hand.
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


def check(report) -> int:
    print(report.render())
    return 0 if report.ok else 1


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "check"
    if mode != "check":
        print(f"usage: {argv[0]} [check]", file=sys.stderr)
        return 2

    try:
        from census.consumption.dispositions import DISPOSITIONS
        from census.consumption.pin import load_manifest
        from census.consumption.reachability import census_report

        report = census_report(load_manifest(), DISPOSITIONS)
    except (ImportError, LookupError, OSError, ValueError) as exc:
        print(
            f"reachability census could not run: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    return check(report)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
