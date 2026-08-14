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
     schemas/ push) all leave a stale or absent stamp and land here. What
     this deliberately does NOT reach: an out-of-band write to some OTHER
     published object after a completed publish leaves the stamp intact —
     the stamp witnesses the last completed publish, not the bucket's
     current contents. The remediation is the same flow the validator
     release already uses: land or re-run the publish, wait out the pointer
     TTL (`.github/workflows/schemas-publish.yml` owns the cache-control),
     then re-run this job.
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
    release-please branches): every divergence FAILS. A red strict run is
    the standing signal that the committed render has not been published —
    every schemas-touching push reds until the `schemas` deployment is
    approved, the pointer TTL passes, and this job is re-run. The pin
    catch-up window is one case of it, and that one is deliberately TIGHTER
    than the offline "at or behind" tolerance (root CLAUDE.md, "The
    contract, and the runtime pin", which governs the merge gate): the red
    is the reminder that finishes the release.
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
offline with the fetch stubbed, plus the CI wiring. Shared plumbing (the
host-pinned fetch, the exception split, the strict-env and warning
contracts, the pin reader) lives in `scripts/_guard_lib.py`.
"""
from __future__ import annotations

import json
import sys
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
#: A versionless object published since before the stamp existed. Fetched only
#: when the stamp comes back 403/404: served sentinel = the stamp is genuinely
#: absent; missing sentinel = the CDN/bucket access itself is broken. The
#: basename is a copy of the renderer's `CANONICAL_TYPES_PATH` (this script
#: cannot import the renderer — it needs pydantic, and guard jobs install
#: nothing); `tests/schemas/test_contracts_version_render.py` pins it.
SENTINEL_URL = f"{BASE_URL}/canonical-types.json"

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
    corroborated by the sentinel being served.
    """
    try:
        return _fetch(PUBLISHED_URL)
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
    """The version half of `VALIDATOR_PIN`, via the shared reader.

    Wrapped rather than re-exported so the module-level `PIN_SOURCE` is what
    the reader consults — the tests point it at a synthetic pin to keep the
    suite green through a real release window.
    """
    return _guard_lib.read_pin_version(PIN_SOURCE)


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
    strict = read_strict_env(STRICT_ENV)
    print(f"committed: {committed}  validator pin: {pin_version}  strict: {strict}")

    committed_bytes = COMMITTED_PATH.read_bytes()
    published_bytes = fetch_published()

    # Per divergent state: (what strict runs are told, what warn runs are
    # told). The strict text carries the on-main remediation; the warn text
    # names the state an ordinary PR is most likely looking at.
    divergence: tuple[str, str] | None = None
    if published_bytes is None:
        divergence = (
            f"{PUBLISHED_URL} is not published (the sentinel is served, so "
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
