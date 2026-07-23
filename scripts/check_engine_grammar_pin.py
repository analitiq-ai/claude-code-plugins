#!/usr/bin/env python3
"""Guard: the vendored engine grammar must equal the published pinned object.

The canonical Arrow type vocabulary is a capability surface the ENGINE owns
(issue #81): analitiq-core publishes it as generated, versioned artifacts —
`arrow-type-grammar` (family + parameter grammar) and `conversion-matrix`
(family x family convertibility grid) — and this repo consumes a pinned,
vendored copy of the grammar (`analitiq.contracts.arrow_grammar`) to build
`ARROW_TYPE_PATTERN` and render `canonical-types.json`. Everything the
contract accepts therefore derives from the vendored file; this guard is what
ties the vendored file to the engine's published truth:

  1. sha256(vendored) == the pin stated in `arrow_grammar.py` (offline).
  2. The published immutable object at the pinned version is byte-identical
     to the vendored copy (a divergent republish or a tampered vendored file
     both fail — the publish side is first-write-wins, so bytes must agree).
  3. The published conversion-matrix at ITS pinned version hashes to its pin,
     and its family keys equal the grammar's family set exactly, row and
     column — the two engine artifacts must describe one capability set.
  4. The published `latest.json` pointers are consulted: a newer engine
     version than the pin is a NOTICE, not a failure — contract ⊆ engine
     still holds; adopting the new version is a deliberate pin bump
     (re-render + doc regeneration), never an automatic one. A pin AHEAD of
     the published latest fails: the contract would be promising a manifest
     the engine has not published.

Exit codes: 0 ok (including the newer-version notice), 1 divergence, 2
GuardError. Every infrastructure failure — missing vendored file, fetch
failure, malformed JSON — is a GuardError: a guard that cannot run must never
read as green. `--offline` runs only step 1 (local dev convenience; CI always
runs the full check).

Wiring: the `engine-grammar-pin-guard` job in .github/workflows/tests.yml.
The offline half is additionally pinned by
packages/contract-models/tests/unit/test_arrow_grammar.py so a plain pytest
run catches a hash mismatch without network.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packages" / "contract-models" / "src"))


class GuardError(RuntimeError):
    """Infrastructure failure — the guard could not run to a verdict."""


# The import itself is part of the guard: `arrow_grammar` loads and derives
# from the vendored manifest at import time, so a missing/corrupt vendored
# file or an underivable manifest shape surfaces HERE — it must classify as
# "guard could not run" (exit 2), never as a divergence verdict or a raw
# traceback. The broad except is deliberate at this boundary: any import
# failure whatsoever means no verdict is possible.
try:
    from analitiq.contracts import arrow_grammar
except Exception as exc:  # noqa: BLE001 — see comment above
    arrow_grammar = None  # type: ignore[assignment]
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None

BASE_URL = "https://schemas.analitiq.ai"

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            return resp.read()
    except (
        urllib.error.URLError,
        http.client.HTTPException,  # IncompleteRead/BadStatusLine are not OSError
        OSError,
        TimeoutError,
    ) as exc:
        raise GuardError(f"fetch failed for {url}: {exc}") from exc


def _parse_object(raw: bytes, *, context: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuardError(f"{context} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise GuardError(f"{context} parsed to {type(parsed).__name__}, expected object")
    return parsed


def _fetch_json(url: str) -> dict:
    return _parse_object(_fetch(url), context=url)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_version(value: str, *, context: str) -> tuple[int, ...]:
    # Exactly three non-negative components: a malformed pointer must be a
    # GuardError, not a crafted (and wrong) "pin AHEAD of latest" verdict from
    # comparing tuples of different lengths.
    if not _VERSION_RE.match(value):
        raise GuardError(f"{context}: unparseable version {value!r}")
    return tuple(int(part) for part in value.split("."))


def check_offline() -> list[str]:
    """Step 1 — the vendored bytes hash to the stated pin. (Existence is
    guaranteed here: a missing file already failed the module import above.)"""
    vendored = arrow_grammar._GRAMMAR_PATH
    digest = _sha256(vendored.read_bytes())
    if digest != arrow_grammar.ENGINE_GRAMMAR_SHA256:
        return [
            f"vendored {vendored.name} hashes to {digest}, but the pin in "
            f"arrow_grammar.py says {arrow_grammar.ENGINE_GRAMMAR_SHA256} — "
            "the vendored file and the pin must move together"
        ]
    return []


def check_published() -> tuple[list[str], list[str]]:
    """Steps 2-4 — published objects vs the pins. Returns (failures, notices)."""
    failures: list[str] = []
    notices: list[str] = []

    grammar_url = (
        f"{BASE_URL}/{arrow_grammar.ENGINE_GRAMMAR_RESOURCE}/"
        f"v{arrow_grammar.ENGINE_GRAMMAR_VERSION}/"
        f"{arrow_grammar.ENGINE_GRAMMAR_FILENAME}"
    )
    published = _fetch(grammar_url)
    if published != arrow_grammar._GRAMMAR_PATH.read_bytes():
        failures.append(
            f"published {grammar_url} differs from the vendored copy — "
            "re-vendor the published object (and re-render schemas + docs)"
        )

    matrix_url = (
        f"{BASE_URL}/{arrow_grammar.CONVERSION_MATRIX_RESOURCE}/"
        f"v{arrow_grammar.CONVERSION_MATRIX_VERSION}/"
        f"{arrow_grammar.CONVERSION_MATRIX_FILENAME}"
    )
    matrix_raw = _fetch(matrix_url)
    if _sha256(matrix_raw) != arrow_grammar.CONVERSION_MATRIX_SHA256:
        failures.append(
            f"published {matrix_url} hashes to {_sha256(matrix_raw)}, pin says "
            f"{arrow_grammar.CONVERSION_MATRIX_SHA256}"
        )
    else:
        # Guarded parse + shape check even though the sha matched — a pin
        # minted against corrupt bytes must be a GuardError, not a traceback
        # or a confidently wrong family-diff verdict.
        matrix = _parse_object(matrix_raw, context=matrix_url)
        if not all(isinstance(cols, dict) for cols in matrix.values()):
            raise GuardError(f"{matrix_url} is not a dict-of-dicts grid")
        grammar_families = set(arrow_grammar.FAMILY_NAMES)
        rows = set(matrix)
        if rows != grammar_families:
            failures.append(
                "conversion-matrix family keys != grammar families: "
                f"matrix-only={sorted(rows - grammar_families)}, "
                f"grammar-only={sorted(grammar_families - rows)}"
            )
        else:
            bad_cols = {
                row for row, cols in matrix.items() if set(cols) != grammar_families
            }
            if bad_cols:
                failures.append(
                    "conversion-matrix rows with column keys != grammar "
                    f"families: {sorted(bad_cols)}"
                )

    for resource, pinned in (
        (arrow_grammar.ENGINE_GRAMMAR_RESOURCE, arrow_grammar.ENGINE_GRAMMAR_VERSION),
        (
            arrow_grammar.CONVERSION_MATRIX_RESOURCE,
            arrow_grammar.CONVERSION_MATRIX_VERSION,
        ),
    ):
        pointer = _fetch_json(f"{BASE_URL}/{resource}/latest.json")
        latest = pointer.get("version")
        if not isinstance(latest, str):
            raise GuardError(f"{resource}/latest.json has no string `version`")
        latest_v = _parse_version(latest, context=f"{resource}/latest.json")
        pinned_v = _parse_version(pinned, context=f"{resource} pin")
        if latest_v > pinned_v:
            notices.append(
                f"{resource}: engine has published v{latest}, pin is v{pinned} "
                "— contract ⊆ engine still holds; adopt deliberately via a pin "
                "bump (re-vendor, re-render schemas, regenerate docs)"
            )
        elif latest_v < pinned_v:
            failures.append(
                f"{resource}: pin v{pinned} is AHEAD of the published latest "
                f"v{latest} — the contract promises a manifest the engine has "
                "not published"
            )
    return failures, notices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--offline",
        action="store_true",
        help="run only the local hash check (no network); CI runs the full check",
    )
    args = parser.parse_args(argv)

    if arrow_grammar is None:
        print(
            "::error::engine-grammar-pin guard could not run: importing the "
            f"vendored grammar failed ({_IMPORT_ERROR}) — re-vendor the "
            "published object (see analitiq/contracts/arrow_grammar.py)",
            file=sys.stderr,
        )
        return 2

    try:
        failures = check_offline()
        notices: list[str] = []
        if not args.offline and not failures:
            net_failures, notices = check_published()
            failures.extend(net_failures)
    except GuardError as exc:
        print(f"::error::engine-grammar-pin guard could not run: {exc}", file=sys.stderr)
        return 2

    for notice in notices:
        print(f"::notice::{notice}")
    if failures:
        for failure in failures:
            print(f"::error::{failure}", file=sys.stderr)
        return 1
    scope = "offline hash" if args.offline else "published objects + hashes + family parity"
    print(
        f"engine-grammar pin OK ({scope}): "
        f"{arrow_grammar.ENGINE_GRAMMAR_RESOURCE} v{arrow_grammar.ENGINE_GRAMMAR_VERSION}, "
        f"{arrow_grammar.CONVERSION_MATRIX_RESOURCE} v{arrow_grammar.CONVERSION_MATRIX_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
