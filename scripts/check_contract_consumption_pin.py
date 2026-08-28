#!/usr/bin/env python3
"""Guard: the vendored consumption manifest must equal the published pinned object.

Which contract fields the engine reads at run time is a fact the ENGINE owns:
it publishes `contract-consumption` as a generated, versioned artifact, and
this repo consumes a pinned, vendored copy (`census.consumption.pin`) as the
coverage claim the reachability census grades every contract field against.
Every "this field is read" verdict the census reaches therefore derives from
the vendored file; this guard is what ties that file to the engine's
published truth:

  1. sha256(vendored) == the pin stated in `census/consumption/pin.py`, the
     file has the shape the census walks, and the manifest's own `version`
     key == the pinned version (offline).
  2. The published immutable object at the pinned version is byte-identical
     to the vendored copy (a divergent republish or a tampered vendored file
     both fail — the publish side is first-write-wins, so bytes must agree).
     Byte-equality is what lets step 1 assert the published manifest's
     self-declared version by asserting the vendored copy's.
  3. The published `latest.json` pointer is consulted. A pointer AT the pin
     must also carry a string `sha256` equal to the pinned one: the pointer
     naming the pinned version but different bytes is a divergence, and a
     pointer at the pinned version with no string `sha256` is a GuardError
     (the pointer cannot be held to the pin). A newer engine version than
     the pin is a NOTICE, not a failure — the census still grades against a
     manifest the engine did publish; adopting it is a deliberate pin bump,
     never an automatic one (the procedure is the pin-bump section of
     `.claude/rules/reachability-dispositions.md`), and its sha is not
     compared, since the pin says nothing about it. A pointer BELOW the
     pin fails, as a POINTER problem, not an unpublished pin: this step runs only after step 2
     fetched the pinned immutable object (a genuinely unpublished pin dies
     there as a GuardError, exit 2), so the mutable pointer is lagging a
     published object — a stale latest.json (the pointers rely on a short
     TTL, not invalidation; `.github/workflows/schemas-publish.yml` owns the
     cache-control) or a half-completed publish.
     Remediation: re-check after the TTL and repair the pointer if it
     persists — re-vendoring does not fix the pointer.

Exit codes: 0 ok (including the newer-version notice), 1 divergence, 2
GuardError. Every infrastructure failure — missing vendored file, fetch
failure, malformed JSON, a malformed pointer, anything unclassified — is a
GuardError, and so is a sha-matched vendored file whose shape the census
cannot walk: the pin was minted against a malformed object, which neither a
retry nor a re-vendor fixes. A guard that cannot run must never read as
green, and must never mint the exit-1 verdict ("the manifest diverged;
re-vendor") for a fault that is not a divergence.
Exit 2 still PRINTS any divergence already found: dropping them would report a
real divergent republish as an infrastructure flake, which a CI reader retries
forever. `--offline` runs only step 1 (local dev convenience; CI always runs
the full check).

Wiring: the `contract-consumption-pin-guard` job in
`.github/workflows/tests.yml` — which must NOT pass `--offline` (that would
make the job permanently green having verified nothing about the engine's
published truth; `test_ci_job_is_wired` in
`tests/schemas/test_contract_consumption_guard.py` pins this).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# A 403/404 arrives as `ObjectMissing`, a GuardError subclass: this guard has
# no state where a missing object is anything but infrastructure, so it needs
# no special case — exit 2 either way. Re-exported so the suite can raise the
# exact class the fetch raises.
from _guard_lib import BASE_URL, GuardError, ObjectMissing, surface_warning  # noqa: E402
from _guard_lib import fetch as _fetch  # noqa: E402

__all__ = ["GuardError", "ObjectMissing", "check_offline", "check_published", "main"]

# The import itself is part of the guard: a pin module that cannot be
# imported leaves no verdict possible — it must classify as "guard could not
# run" (exit 2), never as a divergence verdict or a raw traceback. The broad
# except is deliberate at this boundary.
try:
    from census.consumption import pin
except Exception as exc:  # noqa: BLE001 — see comment above
    pin = None  # type: ignore[assignment]
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


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


def _pinned_url() -> str:
    return (
        f"{BASE_URL}/{pin.CONSUMPTION_RESOURCE}/v{pin.CONSUMPTION_VERSION}/"
        f"{pin.CONSUMPTION_FILENAME}"
    )


def _read_vendored() -> bytes:
    try:
        return pin.MANIFEST_PATH.read_bytes()
    except OSError as exc:
        raise GuardError(
            f"cannot read the vendored manifest {pin.MANIFEST_PATH}: {exc} — "
            "re-vendor the published object — the procedure is the pin-bump "
            "section of `.claude/rules/reachability-dispositions.md`"
        ) from exc


def check_offline() -> list[str]:
    """Step 1 — the vendored bytes hash to the stated pin, the file has the
    shape the census walks, and the manifest's own `version` agrees with the
    pin.

    The self-declared check lives offline rather than in `check_published`
    because step 2 byte-compares the published manifest against this same
    file: once those bytes are equal, a statement about the vendored copy's
    declared version is a statement about the published object's. That
    inference needs step 2 to have actually run, so under `--offline` (and on
    any run where step 1 fails, since `main` then skips step 2) this asserts
    the vendored copy only — which is still the file the census grades from.
    """
    raw = _read_vendored()
    digest = _sha256(raw)
    if digest != pin.CONSUMPTION_SHA256:
        # Return early: with the bytes unaccounted for, anything parsed out of
        # them describes a file we have already rejected.
        return [
            f"vendored {pin.MANIFEST_PATH.name} hashes to {digest}, but the pin "
            f"in pin.py says {pin.CONSUMPTION_SHA256} — the vendored file and "
            "the pin must move together"
        ]
    context = f"vendored {pin.MANIFEST_PATH.name}"
    try:
        # The census's own loader: it refuses a document it could not walk.
        # A sha-matched file it refuses means the pin was minted against a
        # malformed object — "cannot run", not a divergence.
        manifest = pin.load_manifest(pin.MANIFEST_PATH)
    except ValueError as exc:
        raise GuardError(f"{context}: {exc}") from exc
    declared = manifest[pin.ARTIFACT_VERSION_KEY]
    if declared == pin.CONSUMPTION_VERSION:
        return []
    return [
        f"{context} declares version {declared!r} but the pin says "
        f"{pin.CONSUMPTION_VERSION!r} — the vendored file and the pin "
        "constants were not moved together; re-vendor the published object "
        "and bump both"
    ]


def check_published(failures: list[str]) -> tuple[list[str], bool]:
    """Steps 2-3 — the published object and pointer vs the pin. Returns
    notices and whether the pointer's sha was held to the pin — it is only
    when the pointer names the pinned version, and the success banner must
    not claim a comparison that did not run.

    Divergences are APPENDED to the caller's `failures` rather than returned,
    so a `GuardError` raised by a later check cannot discard the definite
    verdicts already reached. Losing them would be actively misleading: a
    published manifest that differs from the vendored copy is the divergence
    this whole guard exists to catch, and reporting only "could not run"
    invites a CI reader to retry it as a flake forever.
    """
    notices: list[str] = []
    pointer_sha_compared = False

    url = _pinned_url()
    published = _fetch(url)
    if published != _read_vendored():
        failures.append(
            f"published {url} differs from the vendored copy — re-vendor the "
            "published object (and re-run the census)"
        )

    pointer_name = f"{pin.CONSUMPTION_RESOURCE}/latest.json"
    pointer = _fetch_json(f"{BASE_URL}/{pointer_name}")
    latest = pointer.get(pin.ARTIFACT_VERSION_KEY)
    if not isinstance(latest, str):
        raise GuardError(
            f"{pointer_name} has no string `{pin.ARTIFACT_VERSION_KEY}`"
        )
    latest_v = _parse_version(latest, context=pointer_name)
    pinned_v = _parse_version(
        pin.CONSUMPTION_VERSION, context=f"{pin.CONSUMPTION_RESOURCE} pin"
    )
    if latest_v > pinned_v:
        notices.append(
            f"{pin.CONSUMPTION_RESOURCE}: engine has published v{latest}, pin "
            f"is v{pin.CONSUMPTION_VERSION} — the census still grades against "
            "a manifest the engine published; adopt deliberately via a pin "
            "bump — the procedure is the pin-bump section of "
            "`.claude/rules/reachability-dispositions.md`"
        )
    elif latest_v == pinned_v:
        # The pointer names the pinned version, so it must name the pinned
        # bytes too. A newer pointer is not held to anything: the pin says
        # nothing about bytes it does not vendor.
        pointer_sha_compared = True
        pointer_sha = pointer.get(pin.POINTER_SHA256_KEY)
        if not isinstance(pointer_sha, str):
            raise GuardError(
                f"{pointer_name} names the pinned v{latest} but has no string "
                f"`{pin.POINTER_SHA256_KEY}` to hold to the pin"
            )
        if pointer_sha != pin.CONSUMPTION_SHA256:
            failures.append(
                f"{pin.CONSUMPTION_RESOURCE}: latest.json names v{latest} with "
                f"sha256 {pointer_sha}, but the pin says "
                f"{pin.CONSUMPTION_SHA256} — the pointer names different bytes "
                "than the pin"
            )
    else:
        # Reachable only AFTER step 2 fetched the pinned immutable object
        # successfully (a genuinely unpublished pin raises GuardError at that
        # fetch, exit 2, and never gets here) — so "the engine has not
        # published the pin" would be provably false. It is the mutable
        # pointer that lags. Still exit 1: a red state a human must look at,
        # but with the pointer's remediation, not the divergence default of
        # re-vendoring.
        failures.append(
            f"{pin.CONSUMPTION_RESOURCE}: latest.json says v{latest}, but the "
            f"pinned v{pin.CONSUMPTION_VERSION} object is published — this "
            "same run just fetched it. The mutable pointer lags a published "
            "pinned object: a stale latest.json (the pointers rely on a short "
            "TTL, not invalidation; .github/workflows/schemas-publish.yml "
            "owns the cache-control) or a half-completed publish. "
            "Re-check after the TTL and repair the pointer if it persists — "
            "re-vendoring does not fix the pointer"
        )
    return notices, pointer_sha_compared


def _report(failures: list[str]) -> None:
    """Print divergences found so far. Called on every exit path, including the
    GuardError ones — a definite verdict already reached must never be dropped
    because a later check could not run."""
    for failure in failures:
        print(f"::error::{failure}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    # `__doc__` is None under `python -OO`; falling over here would exit 1 —
    # the "manifest diverged, go re-vendor" verdict — for a flag that has
    # nothing to do with the manifest.
    parser = argparse.ArgumentParser(
        description=(__doc__ or "contract-consumption pin guard").splitlines()[0]
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "run only the local checks — vendored hash + shape + self-declared "
            "version (no network); CI always runs the full check"
        ),
    )
    args = parser.parse_args(argv)

    if pin is None:
        print(
            "::error::contract-consumption-pin guard could not run: importing "
            f"the pin module failed ({_IMPORT_ERROR}) — see "
            "census/consumption/pin.py",
            file=sys.stderr,
        )
        return 2

    # Owned here, not inside the checks, so every exit path below can report
    # what was already found (see `_report`).
    failures: list[str] = []
    notices: list[str] = []
    pointer_sha_compared = False
    try:
        failures.extend(check_offline())
        if not args.offline and not failures:
            published_notices, pointer_sha_compared = check_published(failures)
            notices.extend(published_notices)
    except GuardError as exc:
        _report(failures)
        print(
            f"::error::contract-consumption-pin guard could not run: {exc}",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 — see comment below
        # Anything not already classified is still "the guard could not run",
        # never a divergence. Without this the fallthrough is exit 1, which the
        # design defines as a DEFINITE verdict ("the manifest diverged from
        # engine truth") whose remediation is re-vendoring — a confident and
        # wrong instruction. Reachable: `urlopen` raising ValueError or
        # UnicodeError, which `_fetch` does not catch.
        _report(failures)
        print(
            "::error::contract-consumption-pin guard could not run: unexpected "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    for notice in notices:
        # A notice in a green job is read by no one on a plain print; on
        # Actions this also annotates the checks UI and the step summary.
        surface_warning(notice, title="contract-consumption pin")
    if failures:
        _report(failures)
        return 1
    if args.offline:
        scope = "offline hash + shape + declared version"
    else:
        scope = "published object + hash + shape + declared version + latest pointer"
        if pointer_sha_compared:
            scope += " + pointer sha"
    print(
        f"contract-consumption pin OK ({scope}): "
        f"{pin.CONSUMPTION_RESOURCE} v{pin.CONSUMPTION_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
