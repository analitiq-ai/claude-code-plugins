#!/usr/bin/env python3
"""Guard: the published contracts-version fact must match the repo's pins.

`schemas/contracts-version.json` stamps the `analitiq-contract-models` release
the committed schema tree renders from (rendered by
`scripts/render_schemas.py contracts-version`, whose full `check` pins the
stamp to `packages/contract-models/pyproject.toml`). The publish workflow
uploads it like every other mutable pointer, so consumers can fetch ONE
machine-readable fact answering "which models release produced this tree?" —
and check their own contract pin against it.

This guard is this repo answering the same question about itself, the half no
offline test can see:

  1. The committed stamp equals the pyproject version (re-derived here so the
     guard never certifies a baseline the render check already rejects — a
     mismatch is a GuardError naming the render, not a verdict).
  2. The PUBLISHED `contracts-version.json` at schemas.analitiq.ai equals the
     committed stamp. Divergence means the publish silently failed or never
     ran (the publish workflow retriggers only on the next schemas/ push), or
     something wrote to the bucket out-of-band — both are exactly the states
     this guard exists to surface.
  3. `VALIDATOR_PIN` (plugins/analitiq-pipeline-builder/scripts/_bootstrap.py
     — the validator end users actually install) agrees with the published
     fact. The guard asserts EQUALITY only and never orders versions: with
     step 2 green, the offline invariants (the pin is at or behind what this
     repo ships — `test_validator_pin_matches_the_package_this_repo_ships` —
     and the stamp equals what it ships, step 1) mean a mismatch here can
     only be the pin lagging: the release-window state whose remediation is
     the pin catch-up PR. Ordering PEP 440 pre-releases (`1.0.0rc21`) in
     stdlib would re-implement `packaging` badly to distinguish states the
     invariants already distinguish.

Windows, mirroring `check_validator_pin_contract.py`:

  - CONTRACTS_VERSION_GUARD_STRICT=1 (CI sets it on pushes and on
    release-please branches): every divergence FAILS. A red main during a
    package-release window (bump merged, pin catch-up pending) is by design —
    the same red, for the same window, as `pinned-validator-guard`.
  - unset (ordinary PRs): divergences WARN (checks-UI annotation) and the job
    passes — a stale published fact is main's problem, and a release PR's
    stamp legitimately runs ahead of the published tree until it merges.
  - A published object that is MISSING (HTTP 403/404 — CloudFront serves
    both for an absent key, depending on bucket-policy shape) is the same
    verdict pair: strict fails, non-strict warns. It is the expected state
    only while the change introducing the stamp has not reached main.

Strict runs poll before failing (`POLL_*` below): the push that changes the
stamp triggers this job and the schemas publish on the same commit, and the
pointer relies on a 5-minute TTL, not invalidation — so the deadline outlasts
upload + TTL, making a strict failure mean "did not converge", never "lost a
race with its own publish".

Exit codes: 0 ok (including warn-mode divergences), 1 divergence, 2
GuardError. Every infrastructure failure — unreadable repo files, a fetch
error that is not a missing object, malformed JSON, anything unclassified —
is a GuardError: a guard that cannot run must never read as green, and never
mint the exit-1 verdict for a fault that is not a divergence.

Wiring: the `contracts-version-guard` job in .github/workflows/tests.yml;
tests/schemas/test_contracts_version_guard.py pins every verdict branch
offline with the fetch stubbed, plus the CI wiring.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BASE_URL = "https://schemas.analitiq.ai"
PUBLISHED_URL = f"{BASE_URL}/contracts-version.json"

COMMITTED_PATH = REPO_ROOT / "schemas" / "contracts-version.json"
PYPROJECT_PATH = REPO_ROOT / "packages" / "contract-models" / "pyproject.toml"
PIN_SOURCE = (
    REPO_ROOT / "plugins" / "analitiq-pipeline-builder" / "scripts" / "_bootstrap.py"
)

# The stamp's fact key — the PyPI distribution name. render_schemas.py owns the
# document and states the same key (`CONTRACTS_VERSION_KEY`); this script
# cannot import it (render_schemas imports pydantic, and this job installs
# nothing), so test_contracts_version_render.py pins the two constants equal.
CONTRACTS_VERSION_KEY = "analitiq-contract-models"

# Strict-mode convergence window: schemas-publish uploads in seconds, the
# mutable pointer refreshes on a 5-minute TTL, so 8 minutes outlasts both.
POLL_DEADLINE_SECONDS = 480.0
POLL_INTERVAL_SECONDS = 30.0


class GuardError(RuntimeError):
    """Infrastructure failure — the guard could not run to a verdict."""


class NotPublished(Exception):
    """The published object does not exist (HTTP 403/404) — a verdict input,
    not an infrastructure failure."""


def _fetch(url: str) -> bytes:
    # The trailing slash matters: a bare prefix would admit a host that merely
    # STARTS with the pinned one (schemas.analitiq.ai.evil.example).
    if not url.startswith(f"{BASE_URL}/"):
        raise GuardError(f"refusing non-{BASE_URL} URL: {url}")
    try:
        # Scheme pinned by the BASE_URL check above.
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310  # skipcq: BAN-B310
            return resp.read()
    except urllib.error.HTTPError as exc:
        # CloudFront-over-S3 reports a missing key as 404, or as 403 when the
        # origin policy denies ListBucket. Both mean "no such object"; every
        # other status is the CDN misbehaving, which no verdict may rest on.
        if exc.code in (403, 404):
            raise NotPublished(f"{url} → HTTP {exc.code}") from exc
        raise GuardError(f"fetch failed for {url}: HTTP {exc.code}") from exc
    except (
        # URLError and TimeoutError are OSError subclasses; HTTPException
        # (IncompleteRead/BadStatusLine) is not.
        http.client.HTTPException,
        OSError,
    ) as exc:
        raise GuardError(f"fetch failed for {url}: {exc}") from exc


def _read_fact(raw: bytes, *, context: str) -> str:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GuardError(f"{context} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise GuardError(
            f"{context} parsed to {type(parsed).__name__}, expected an object"
        )
    value = parsed.get(CONTRACTS_VERSION_KEY)
    if not isinstance(value, str) or not value:
        raise GuardError(
            f"{context} has no string {CONTRACTS_VERSION_KEY!r} key — not a "
            "contracts-version document"
        )
    return value


def fetch_published() -> str | None:
    """The published fact, or None when the object is not published at all."""
    try:
        return _read_fact(_fetch(PUBLISHED_URL), context=PUBLISHED_URL)
    except NotPublished as exc:
        print(f"published object missing: {exc}")
        return None


def read_committed_stamp() -> str:
    if not COMMITTED_PATH.exists():
        raise GuardError(
            f"{COMMITTED_PATH.relative_to(REPO_ROOT)} is missing — run "
            "`scripts/render_schemas.py contracts-version`"
        )
    return _read_fact(
        COMMITTED_PATH.read_bytes(), context=str(COMMITTED_PATH.relative_to(REPO_ROOT))
    )


def read_shipped_version() -> str:
    try:
        data = tomllib.loads(PYPROJECT_PATH.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GuardError(f"cannot read {PYPROJECT_PATH}: {exc}") from exc
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise GuardError(f"{PYPROJECT_PATH} declares no [project] version")
    return version


def read_pin_version() -> str:
    """The version half of `VALIDATOR_PIN` in `_bootstrap.py` — the one place
    the runtime pin is stated (root CLAUDE.md, "The contract")."""
    try:
        source = PIN_SOURCE.read_text()
    except OSError as exc:
        raise GuardError(f"cannot read {PIN_SOURCE}: {exc}") from exc
    match = re.search(
        r'^VALIDATOR_PIN = "analitiq-validator==([^"]+)"$', source, re.MULTILINE
    )
    if not match:
        raise GuardError(f"VALIDATOR_PIN not found in {PIN_SOURCE}")
    return match.group(1)


def read_strict_env() -> bool:
    """The CONTRACTS_VERSION_GUARD_STRICT override. Only '1' or unset/'' parse.

    Anything else raises: a typo like `true` must not silently downgrade a
    strict run to warn-only.
    """
    value = os.environ.get("CONTRACTS_VERSION_GUARD_STRICT", "")
    if value not in ("", "1"):
        raise GuardError(
            f"CONTRACTS_VERSION_GUARD_STRICT={value!r} not recognized — "
            "set '1' for strict or leave unset"
        )
    return value == "1"


def _surface_warning(text: str) -> None:
    """Print the window warning where someone will actually see it.

    A plain print in a green job is read by no one; on Actions, also emit a
    workflow annotation (shows on the PR checks UI) and a step summary.
    """
    print(text)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=contracts version::{' '.join(text.split())}")
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(f"⚠️ {text}\n")


def poll_published(expected: str) -> str | None:
    """Re-fetch until the published fact equals `expected` or the deadline
    passes; returns the last observed value (None = still unpublished)."""
    deadline = time.monotonic() + POLL_DEADLINE_SECONDS
    published = fetch_published()
    while published != expected and time.monotonic() < deadline:
        print(
            f"published fact is {published!r}, waiting for {expected!r} "
            f"(publish + pointer TTL window; retry in {POLL_INTERVAL_SECONDS:g}s)"
        )
        time.sleep(POLL_INTERVAL_SECONDS)
        published = fetch_published()
    return published


def run() -> int:
    committed = read_committed_stamp()
    shipped = read_shipped_version()
    if committed != shipped:
        # The render check owns this half; a guard must not certify (or
        # blame the publish for) a baseline the repo itself cannot agree on.
        raise GuardError(
            f"committed stamp {committed!r} != pyproject version {shipped!r} — "
            "run `scripts/render_schemas.py contracts-version` (the render "
            "check gates this; this guard needs the two to agree before it "
            "can say anything about the published copy)"
        )
    pin_version = read_pin_version()
    strict = read_strict_env()
    print(f"committed: {committed}  validator pin: {pin_version}  strict: {strict}")

    published = poll_published(committed) if strict else fetch_published()

    divergences: list[str] = []
    if published is None:
        divergences.append(
            f"{PUBLISHED_URL} is not published. Expected only while the "
            "change introducing contracts-version.json has not reached main; "
            "on main, the schemas publish did not land — re-run "
            "schemas-publish.yml via workflow_dispatch."
        )
    elif published != committed:
        divergences.append(
            f"published fact {published!r} != committed stamp {committed!r} "
            "(held past the publish + pointer-TTL window on strict runs). "
            "Either the schemas publish failed — re-run schemas-publish.yml "
            "via workflow_dispatch — or the bucket was written out-of-band; "
            "the publish workflow's run history says which."
        )
    elif pin_version != published:
        # Equality-only by design: with the published fact equal to the
        # committed stamp, the offline pin invariants leave exactly one
        # mismatch state — the pin lagging a release (see module docstring).
        divergences.append(
            f"VALIDATOR_PIN ({pin_version}) disagrees with the published "
            f"contract ({published}) — the release-window state; land the pin "
            "catch-up so end users install the validator the published "
            "schemas were rendered from. A red main here during a package "
            "release window is by design, exactly as pinned-validator-guard."
        )

    if not divergences:
        print(f"OK: published == committed == pin == {committed}")
        return 0
    if strict:
        for d in divergences:
            print(f"DIVERGENCE: {d}", file=sys.stderr)
        return 1
    for d in divergences:
        _surface_warning(
            f"WINDOW: {d} Strict runs (pushes to main, release-please "
            "branches) enforce this."
        )
    return 0


def main() -> int:
    try:
        return run()
    except GuardError as exc:
        print(f"GUARD ERROR (not a verdict): {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — a guard that cannot run must
        # never read as green NOR mint the divergence verdict (exit 1) for a
        # crash; everything unclassified is infrastructure (exit 2).
        print(f"GUARD ERROR (unexpected): {exc!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
