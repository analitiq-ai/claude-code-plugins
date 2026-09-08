#!/usr/bin/env python3
"""Guard: the published contracts-version fact must match the repo's pins.

`schemas/contracts-version.json` stamps the committed schema tree's
provenance (rendered by `scripts/render_schemas.py contracts-version`, whose
full `check` keeps it current): the `analitiq-contract-models` version the
contract source tree declared when the tree was rendered, plus a digest over
every other document in the tree — the half that changes with EVERY render,
where the version half changes only on a package bump. The publish workflow
uploads it like every other mutable pointer, so consumers can fetch a
machine-readable statement of which contract-models version rendered the
published tree, and check their own contract pin against it.

This guard is this repo answering the same question about itself, the half no
offline test can see:

  1. The committed stamp states the pyproject version (re-derived here so the
     guard never certifies a baseline the render check already rejects — a
     mismatch is a GuardError naming the render, not a verdict).
  2. The PUBLISHED `contracts-version.json` at schemas.analitiq.ai is
     byte-identical to the committed stamp. The stamp carries the tree
     digest and the publish uploads it dead last, so byte equality
     establishes: the last publish to COMPLETE was of a tree identical to
     the current committed one. A publish that silently failed, died
     mid-tree, or never ran (its `schemas` environment holds deployments
     for reviewer approval, so the stamp-changing push itself reaches this
     guard before the upload; a failed run retriggers only on the next
     schemas/ push) all leave a stale or absent stamp and land here. A
     completed publish that simply has not propagated through the CDN yet
     does NOT land here on a strict run: `fetch_published_with_retry`
     already retries across the publish's own pointer TTL
     (`.github/workflows/schemas-publish.yml` owns the cache-control) before
     this step ever sees the divergence. What this deliberately does NOT
     reach: an out-of-band write to some OTHER published object after a
     completed publish leaves the stamp intact — the stamp witnesses the last
     completed publish, not the bucket's current contents. The remediation
     once the retry budget is spent is the same flow the validator release
     already uses: land or re-run the publish, then re-run this job.
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
     invariants already distinguish. And the pin comparison runs only after
     step 2 holds, so it always grades the version the published tree was
     actually rendered with.

Strict-vs-warn windows (the STRICT env contract — typo-refusal and all — is
`_guard_lib.read_strict_env`, shared with `check_validator_pin_contract.py`,
and the CI trigger expression is pinned identical to that guard's; the
verdicts behind it are this guard's own):

  - CONTRACTS_VERSION_GUARD_STRICT=1 (CI sets it on pushes and on
    release-please branches): a published-fact or missing-stamp divergence
    (step 2) is retried — `fetch_published_with_retry` re-samples every
    RETRY_INTERVAL_SECONDS across a window one interval wider than
    RETRY_BUDGET_SECONDS (margin against landing exactly on the CDN's own
    expiry boundary), until the published bytes match the committed stamp
    or the window is spent, so a strict run does not red on the
    schemas-publish pointer TTL by itself
    (`.github/workflows/schemas-publish.yml` uploads that pointer with a
    cache-control max-age RETRY_BUDGET_SECONDS is pinned to at least —
    `test_retry_budget_covers_the_publish_ttl` holds the floor). A run that
    still diverges once the window is spent FAILS: the standing signal that
    the committed render has not been published, needing the `schemas`
    deployment approved or schemas-publish.yml re-run before this job is
    re-run. The pin catch-up divergence (step 3) is not a network race — it
    is graded only after step 2 already holds, and the loop returns as soon
    as step 2's bytes match — so it fails on the first sample; that one is
    deliberately TIGHTER than the offline "at or behind" tolerance (root
    CLAUDE.md, "The contract, and the runtime pin", which governs the merge
    gate): the red is the reminder that finishes the release.
  - unset (ordinary PRs): divergences WARN (checks-UI annotation) and the
    job passes — a stale published fact is main's problem, and a release
    PR's stamp legitimately runs ahead of the published tree until it
    merges.
  - A published stamp that is MISSING (HTTP 403/404 — CloudFront serves
    either status for an absent key, depending on bucket-policy shape) gets
    the same treatment: strict fails, non-strict warns. It is the expected
    state only while the change introducing the stamp has not reached main.
    Because that status is also what an access fault returns for objects that
    DO exist, a missing stamp is believed only after an object certain to be
    served (a superseded, pinned `X.Y.Z.json`) is confirmed served; a probe
    that is also missing is a GuardError, never a verdict.

Exit codes: 0 ok (including warn-mode divergences), 1 divergence, 2
GuardError. Every infrastructure failure — unreadable repo files, a fetch
error that is not a missing object, malformed JSON, anything unclassified —
is a GuardError: a guard that cannot run must never read as green, and never
mint the exit-1 verdict for a fault that is not a divergence.

Wiring: the `contracts-version-guard` job in `.github/workflows/tests.yml`;
`tests/schemas/test_contracts_version_guard.py` pins every verdict branch
offline with the fetch stubbed and the retry loop's clock (`_sleep`,
`_monotonic`) replaced so no test sleeps in real time, plus the CI wiring.
Shared plumbing (the host-pinned fetch, the exception split, the strict-env
and warning contracts, the pin reader) lives in `scripts/_guard_lib.py`.
"""
from __future__ import annotations

import json
import sys
import time
import tomllib
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _guard_lib  # noqa: E402
from _guard_lib import BASE_URL, PIN_SOURCE, GuardError  # noqa: E402
# ObjectMissing subclasses GuardError, so anywhere this guard fails to
# special-case a missing object it still exits 2; `fetch_published` catches
# it first, because HERE absence is a verdict input, not infrastructure.
from _guard_lib import ObjectMissing as NotPublished  # noqa: E402
from _guard_lib import fetch as _fetch  # noqa: E402
from _guard_lib import read_strict_env, surface_warning  # noqa: E402

COMMITTED_PATH = REPO_ROOT / "schemas" / "contracts-version.json"
PYPROJECT_PATH = REPO_ROOT / "packages" / "contract-models" / "pyproject.toml"
STRICT_ENV = "CONTRACTS_VERSION_GUARD_STRICT"

PUBLISHED_URL = f"{BASE_URL}/{COMMITTED_PATH.name}"
#: Probed only when the stamp comes back 403/404, to tell those two causes
#: apart: probe served = the stamp is genuinely absent; probe missing = reaching
#: the published tree is itself broken, which is a fault and never a verdict.
#:
#: The second fetch is needed because the serving side answers the same status
#: for an absent object and a refused one, so one fetch cannot distinguish them.
#: It stops being needed the day those differ.
#:
#: The requirement on the object named here is that it is CERTAIN to be served
#: whenever serving is healthy — so it must be one this repo can neither move
#: nor re-render. A pinned `X.Y.Z.json` is that by construction: publishes are
#: first-write-wins and never rewrite an existing version. It must also be a
#: SUPERSEDED version of a resource, never a current one: a version publishing
#: in the same release as the stamp would be absent exactly when the stamp is,
#: and answer nothing. `test_guard_probes_an_object_that_cannot_stop_being_served`
#: holds both halves.
PROBE_URL = f"{BASE_URL}/connector/1.0.0.json"

# The stamp's fact key — the PyPI distribution name. `render_schemas.py` owns
# the document and states the same key (`CONTRACTS_VERSION_KEY`);
# `tests/schemas/test_contracts_version_render.py` pins the copies equal.
CONTRACTS_VERSION_KEY = "analitiq-contract-models"  # skipcq: SCT-A000 — a PyPI distribution name, not a credential


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


def fetch_published() -> bytes | None:
    """The published stamp's bytes, or None when it is genuinely unpublished.

    "Genuinely": a 403/404 on the stamp alone cannot distinguish an absent
    key from a site-wide access fault (a broken origin policy returns 403
    for EVERY key, existing or not), and misreading the latter as "not
    published" would mint the divergence verdict — with a re-run-the-publish
    remediation that cannot fix it. So the missing-stamp reading must be
    corroborated by the probe being served.
    """
    try:
        return _fetch(PUBLISHED_URL)
    except NotPublished as exc:
        print(f"stamp not served: {exc}")
        try:
            _fetch(PROBE_URL)
        except NotPublished as probe_exc:
            raise GuardError(
                f"the probe is not served either ({probe_exc}) — a "
                "CDN/bucket access fault, not a missing stamp; fix the "
                "serving side before believing any verdict"
            ) from probe_exc
        return None


#: The floor for how long a strict run waits out schemas-publish.yml's own
#: pointer TTL before minting a divergence verdict, and how often it
#: re-samples while waiting. Bound to (at least) the mutable-pointer
#: cache-control max-age that workflow uploads with —
#: `test_retry_budget_covers_the_publish_ttl` reads that value back and
#: holds the floor, so retrying for less than the CDN's own TTL (and
#: reddening every schemas-touching push on the propagation window alone)
#: cannot silently come back. `fetch_published_with_retry` waits one
#: RETRY_INTERVAL_SECONDS beyond this floor (see its docstring) rather than
#: exactly up to it.
RETRY_BUDGET_SECONDS = 300.0
RETRY_INTERVAL_SECONDS = 20.0

#: Overridable by tests so the loop's *behavior* (call count, termination on
#: window exhaustion, termination on a match) is exercised without
#: sleeping in real time. Production always binds the real ones; tests
#: replace only these two local names — never the global `time` module —
#: with a simulated clock that `_sleep` itself advances.
_sleep = time.sleep
_monotonic = time.monotonic


def fetch_published_with_retry(committed_bytes: bytes, *, strict: bool) -> bytes | None:
    """`fetch_published`, retried in strict mode until the bytes it returns
    match `committed_bytes` or the retry window elapses.

    The window is RETRY_BUDGET_SECONDS plus one extra RETRY_INTERVAL_SECONDS:
    the deadline sits past the floor `test_retry_budget_covers_the_publish_ttl`
    pins to the CDN's own TTL, so the sample deciding the verdict is not the
    one racing that exact boundary against this job's own request latency.

    Warn-mode runs (ordinary PRs) fetch once, unretried: a stale published
    fact there is not a race to wait out — a release PR's committed stamp
    legitimately runs ahead of the published tree until it merges, and no
    amount of waiting resolves that.
    """
    if not strict:
        return fetch_published()
    deadline = _monotonic() + RETRY_BUDGET_SECONDS + RETRY_INTERVAL_SECONDS
    while True:
        published_bytes = fetch_published()
        if published_bytes == committed_bytes:
            return published_bytes
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return published_bytes
        wait = min(RETRY_INTERVAL_SECONDS, remaining)
        print(
            f"published stamp still diverges from the committed one — "
            f"retrying in {wait:.0f}s ({remaining:.0f}s left of the "
            f"retry window)"
        )
        _sleep(wait)


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
    """The version half of `VALIDATOR_PIN`, via the shared reader.

    Wrapped rather than re-exported so the module-level `PIN_SOURCE` is what
    the reader consults — the tests point it at a synthetic pin to keep the
    suite green through a real release window.
    """
    return _guard_lib.read_pin_version(PIN_SOURCE)


_REPUBLISH = (
    "this job already retried across schemas-publish.yml's own pointer TTL "
    "without seeing the stamp catch up — approve or re-run "
    "schemas-publish.yml (workflow_dispatch), then re-run this job"
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
    strict = read_strict_env(STRICT_ENV)
    print(f"committed: {committed}  validator pin: {pin_version}  strict: {strict}")

    committed_bytes = COMMITTED_PATH.read_bytes()
    published_bytes = fetch_published_with_retry(committed_bytes, strict=strict)

    # Per divergent state: (what strict runs are told, what warn runs are
    # told). The strict text carries the on-main remediation; the warn text
    # names the state an ordinary PR is most likely looking at.
    divergence: tuple[str, str] | None = None
    if published_bytes is None:
        divergence = (
            f"{PUBLISHED_URL} is not published (the probe is served, so "
            f"this is a missing stamp, not an access fault) — the schemas "
            f"publish never landed the stamp; {_REPUBLISH}.",
            f"{PUBLISHED_URL} is not published — expected while the change "
            "introducing the stamp has not reached main.",
        )
    elif published_bytes != committed_bytes:
        published = _read_fact(published_bytes, context=PUBLISHED_URL)
        if published != committed:
            divergence = (
                f"published fact {published!r} != committed stamp "
                f"{committed!r} — the schemas-publish deployment is awaiting "
                f"its environment approval, failed, or the bucket was "
                f"written out-of-band (the publish run history says which); "
                f"{_REPUBLISH}.",
                f"published fact {published!r} != committed stamp "
                f"{committed!r} — on a release PR the committed stamp "
                "legitimately runs ahead of the published tree until it "
                "merges.",
            )
        else:
            divergence = (
                f"the published stamp names the same release ({published}) "
                f"but not the same tree digest — the publish has not landed "
                f"the latest render; {_REPUBLISH}.",
                f"the published stamp names the same release ({published}) "
                "but not the same tree digest — expected on a PR that "
                "re-renders schemas, until it merges and publishes.",
            )
    elif pin_version != committed:
        divergence = (
            f"VALIDATOR_PIN ({pin_version}) disagrees with the published "
            f"contract ({committed}) — land the pin catch-up so end users "
            "install the validator the published schemas were rendered from. "
            "Deliberately tighter than the offline at-or-behind tolerance "
            "(root CLAUDE.md, \"The contract, and the runtime pin\"): this "
            "red is the reminder that finishes the release.",
            f"VALIDATOR_PIN ({pin_version}) lags the published contract "
            f"({committed}) — a package-release window; the pin catch-up "
            "clears it.",
        )

    if divergence is None:
        print(f"OK: published stamp == committed stamp, pin == {committed}")
        return 0
    strict_text, warn_text = divergence
    if strict:
        print(f"DIVERGENCE: {strict_text}", file=sys.stderr)
        return 1
    surface_warning(
        f"WINDOW: {warn_text} Strict runs (pushes to main, release-please "
        "branches) enforce this.",
        title="contracts version",
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
