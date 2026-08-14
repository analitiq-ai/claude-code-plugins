#!/usr/bin/env python3
"""Guard: the published contracts-version fact must match the repo's pins.

`schemas/contracts-version.json` stamps the `analitiq-contract-models` release
the committed schema tree renders from (rendered by
`scripts/render_schemas.py contracts-version`, whose full `check` pins the
stamp to `packages/contract-models/pyproject.toml`). The publish workflow
uploads it like every other mutable pointer, so consumers can fetch a
machine-readable fact answering "which models release produced this tree?" —
and check their own contract pin against it.

This guard is this repo answering the same question about itself, the half no
offline test can see:

  1. The committed stamp equals the pyproject version (re-derived here so the
     guard never certifies a baseline the render check already rejects — a
     mismatch is a GuardError naming the render, not a verdict).
  2. The PUBLISHED `contracts-version.json` at schemas.analitiq.ai equals the
     committed stamp. Divergence means the schemas publish has not landed
     (its `schemas` environment holds deployments for reviewer approval, so
     the stamp-bumping push itself reaches this guard before the upload), or
     it failed outright (the publish workflow retriggers only on the next
     schemas/ push), or something wrote to the bucket out-of-band. The
     remediation is the same flow the validator release already uses: land
     or re-run the publish, wait out the pointer TTL
     (`.github/workflows/schemas-publish.yml` owns the cache-control), then
     re-run this job.
  3. `VALIDATOR_PIN` (`plugins/analitiq-pipeline-builder/scripts/_bootstrap.py`
     — the validator end users actually install) agrees with the published
     fact. The guard asserts EQUALITY only and never orders versions: the
     offline invariants close every other direction. The pin is at or behind
     the validator this repo ships
     (`test_validator_pin_matches_the_package_this_repo_ships`), the shipped
     validator and contract-models versions are held equal
     (`test_validator_version_matches_contract_models_version` in
     `packages/validator/tests/test_contract_models_pin.py` — the bridge that
     makes an `analitiq-validator` version comparable to the stamp's
     `analitiq-contract-models` version at all), and the stamp equals the
     contract-models version (step 1). With step 2 green, a mismatch here can
     only be the pin lagging: the release-window state whose remediation is
     the pin catch-up PR. Ordering PEP 440 pre-releases (`1.0.0rc21`) in
     stdlib would re-implement `packaging` badly to distinguish states those
     invariants already distinguish.

Strict-vs-warn windows (the STRICT env contract — name shape, the CI trigger
expression, and typo-refusal — matches `check_validator_pin_contract.py`;
the verdicts behind it are this guard's own):

  - CONTRACTS_VERSION_GUARD_STRICT=1 (CI sets it on pushes and on
    release-please branches): every divergence FAILS. A red main while a
    package-release window is open — publish awaiting approval, or the pin
    catch-up pending — is deliberate, and deliberately TIGHTER than the
    offline "at or behind" tolerance (root CLAUDE.md, "The contract, and the
    runtime pin", which governs the merge gate): the red is the reminder
    that finishes the release, cleared by re-running this job once the
    publish and the catch-up land.
  - unset (ordinary PRs): divergences WARN (checks-UI annotation) and the
    job passes — a stale published fact is main's problem, and a release
    PR's stamp legitimately runs ahead of the published tree until it
    merges.
  - A published stamp that is MISSING (HTTP 403/404 — CloudFront serves
    either status for an absent key, depending on bucket-policy shape) gets
    the same treatment: strict fails, non-strict warns. It is the expected
    state only while the change introducing the stamp has not reached main.
    Because a 403 is also what a site-wide access fault returns for objects
    that DO exist, a missing stamp is believed only after a sentinel object
    that predates it (`canonical-types.json`) is confirmed served; a sentinel
    that is also missing is a GuardError, never a verdict.

Exit codes: 0 ok (including warn-mode divergences), 1 divergence, 2
GuardError. Every infrastructure failure — unreadable repo files, a fetch
error that is not a missing object, malformed JSON, anything unclassified —
is a GuardError: a guard that cannot run must never read as green, and never
mint the exit-1 verdict for a fault that is not a divergence.

Wiring: the `contracts-version-guard` job in `.github/workflows/tests.yml`;
`tests/schemas/test_contracts_version_guard.py` pins every verdict branch
offline with the fetch stubbed, plus the CI wiring.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import tomllib
import traceback
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

COMMITTED_PATH = REPO_ROOT / "schemas" / "contracts-version.json"
PYPROJECT_PATH = REPO_ROOT / "packages" / "contract-models" / "pyproject.toml"
PIN_SOURCE = (
    REPO_ROOT / "plugins" / "analitiq-pipeline-builder" / "scripts" / "_bootstrap.py"
)

# The serving host is owned by `analitiq.contracts.shared.common.SCHEMA_BASE_URL`;
# this script cannot import it (the guard job installs nothing, and the
# contract package needs pydantic), so the copy here — and the sentinel's
# basename, owned by the renderer as `CANONICAL_TYPES_PATH` — are pinned to
# their owners by `tests/schemas/test_contracts_version_render.py`.
BASE_URL = "https://schemas.analitiq.ai"
PUBLISHED_URL = f"{BASE_URL}/{COMMITTED_PATH.name}"
#: A versionless object published since before the stamp existed. Fetched only
#: when the stamp comes back 403/404: served sentinel = the stamp is genuinely
#: absent; missing sentinel = the CDN/bucket access itself is broken.
SENTINEL_URL = f"{BASE_URL}/canonical-types.json"

# The stamp's fact key — the PyPI distribution name. `render_schemas.py` owns
# the document and states the same key (`CONTRACTS_VERSION_KEY`);
# `tests/schemas/test_contracts_version_render.py` pins the copies equal.
CONTRACTS_VERSION_KEY = "analitiq-contract-models"


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
        # origin policy denies ListBucket. Each means "no such object"; every
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
    """The published fact, or None when the stamp is genuinely not published.

    "Genuinely": a 403/404 on the stamp alone cannot distinguish an absent
    key from a site-wide access fault (a broken origin policy returns 403
    for EVERY key, existing or not), and misreading the latter as "not
    published" would mint the divergence verdict — with a re-run-the-publish
    remediation that cannot fix it. So the missing-stamp reading must be
    corroborated by the sentinel being served.
    """
    try:
        return _read_fact(_fetch(PUBLISHED_URL), context=PUBLISHED_URL)
    except NotPublished as exc:
        print(f"stamp not served: {exc}")
        try:
            _fetch(SENTINEL_URL)
        except NotPublished as sentinel_exc:
            raise GuardError(
                f"the sentinel is not served either ({sentinel_exc}) — a "
                "CDN/bucket access fault, not a missing stamp; fix the "
                "serving side before believing any verdict"
            ) from sentinel_exc
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


_REPUBLISH = (
    "approve or re-run schemas-publish.yml (workflow_dispatch), wait out the "
    "pointer TTL it documents, then re-run this job"
)


def run() -> int:
    committed = read_committed_stamp()
    shipped = read_shipped_version()
    if committed != shipped:
        # The render check owns this half; a guard must not certify (or
        # blame the publish for) a baseline the repo itself cannot agree on.
        raise GuardError(
            f"committed stamp {committed!r} != pyproject version {shipped!r} — "
            "run `scripts/render_schemas.py contracts-version` (the render "
            "check gates this; this guard needs the stamp and the pyproject "
            "to agree before it can say anything about the published copy)"
        )
    pin_version = read_pin_version()
    strict = read_strict_env()
    print(f"committed: {committed}  validator pin: {pin_version}  strict: {strict}")

    published = fetch_published()

    # Per divergent state: (what strict runs are told, what warn runs are
    # told). The strict text carries the on-main remediation; the warn text
    # names the state an ordinary PR is most likely looking at.
    divergence: tuple[str, str] | None = None
    if published is None:
        divergence = (
            f"{PUBLISHED_URL} is not published (the sentinel is served, so "
            f"this is a missing stamp, not an access fault) — the schemas "
            f"publish never landed the stamp; {_REPUBLISH}.",
            f"{PUBLISHED_URL} is not published — expected while the change "
            "introducing the stamp has not reached main.",
        )
    elif published != committed:
        divergence = (
            f"published fact {published!r} != committed stamp {committed!r} — "
            f"the schemas-publish deployment is awaiting its environment "
            f"approval, failed, or the bucket was written out-of-band (the "
            f"publish run history says which); {_REPUBLISH}.",
            f"published fact {published!r} != committed stamp {committed!r} — "
            "on a release PR the committed stamp legitimately runs ahead of "
            "the published tree until it merges.",
        )
    elif pin_version != published:
        divergence = (
            f"VALIDATOR_PIN ({pin_version}) disagrees with the published "
            f"contract ({published}) — land the pin catch-up so end users "
            "install the validator the published schemas were rendered from. "
            "Deliberately tighter than the offline at-or-behind tolerance "
            "(root CLAUDE.md, \"The contract, and the runtime pin\"): this "
            "red is the reminder that finishes the release.",
            f"VALIDATOR_PIN ({pin_version}) lags the published contract "
            f"({published}) — a package-release window; the pin catch-up "
            "clears it.",
        )

    if divergence is None:
        print(f"OK: published == committed == pin == {committed}")
        return 0
    strict_text, warn_text = divergence
    if strict:
        print(f"DIVERGENCE: {strict_text}", file=sys.stderr)
        return 1
    _surface_warning(
        f"WINDOW: {warn_text} Strict runs (pushes to main, release-please "
        "branches) enforce this."
    )
    return 0


def main() -> int:
    try:
        return run()
    except GuardError as exc:
        print(f"GUARD ERROR (not a verdict): {exc}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 — a guard that cannot run must never
        # read as green NOR mint the divergence verdict (exit 1) for a crash;
        # everything unclassified is infrastructure (exit 2). The traceback
        # is the debugging surface: this script runs only in CI, where a bare
        # repr would leave no file/line to start from.
        print("GUARD ERROR (unexpected):", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
