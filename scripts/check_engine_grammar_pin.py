#!/usr/bin/env python3
"""Guard: the vendored engine grammar must equal the published pinned object.

The canonical Arrow type vocabulary is a capability surface the ENGINE owns:
analitiq-core publishes it as generated, versioned artifacts —
`arrow-type-grammar` (family + parameter grammar) and `conversion-matrix`
(family x family convertibility grid) — and this repo consumes a pinned,
vendored copy of the grammar (`analitiq.contracts.arrow_grammar`) to build
`ARROW_TYPE_PATTERN` and render `canonical-types.json`. Everything the
contract accepts therefore derives from the vendored file; this guard is what
ties the vendored file to the engine's published truth:

  1. sha256(vendored) == the pin stated in `arrow_grammar.py`, and the
     manifest's own `version` key == the pinned version (offline).
  2. The published immutable object at the pinned version is byte-identical
     to the vendored copy (a divergent republish or a tampered vendored file
     both fail — the publish side is first-write-wins, so bytes must agree).
     Byte-equality is what lets step 1 assert the published grammar's
     self-declared version by asserting the vendored copy's.
  3. The published conversion-matrix at ITS pinned version hashes to its pin,
     self-declares that same version, and its grid is certified three ways:
     KEY PARITY (family keys equal the grammar's family set exactly, row and
     column), CELL SHAPE (every cell is an object whose `mode` is a non-empty
     string), and DIAGONAL IDENTITY (every family-to-itself cell declares the
     identity mode). That list is exhaustive — the off-diagonal per-cell
     semantics (which conversions exist, in what mode) stay engine-owned and
     are NOT certified here. The matrix grid is read from the `conversions`
     key of the v2 envelope; the pre-v2 bare-grid shape is not accepted as a
     fallback. Note the grid half runs only when the sha256 matches AND the
     self-declared version equals the pin: a hash mismatch is already a
     definite divergence (the checks that would describe those unaccounted-for
     bytes are skipped), and a mislabeled object is rejected outright — one
     failure, one cause; no grid verdict is minted about a document already
     disclaimed. The hash-mismatch and mislabel branches still fall through
     to step 4; a grid GuardError (missing `conversions` key, a non-grid
     shape, malformed cells) exits before it.
  4. The published `latest.json` pointers are consulted: a newer engine
     version than the pin is a NOTICE, not a failure — contract ⊆ engine
     still holds; adopting the new version is a deliberate pin bump
     (re-render + doc regeneration), never an automatic one. A pointer BELOW
     the pin fails, as a POINTER problem, not an unpublished pin: this step
     runs only after steps 2-3 fetched the pinned immutable objects (a
     genuinely unpublished pin dies there as a GuardError, exit 2), so the
     mutable pointer is lagging a published object — a stale latest.json
     (the pointers rely on a 5-minute TTL, not invalidation) or a
     half-completed publish. Remediation: re-check after the TTL and repair
     the pointer if it persists — re-vendoring does not fix the pointer.

Exit codes: 0 ok (including the newer-version notice), 1 divergence, 2
GuardError. Every infrastructure failure — missing vendored file, fetch
failure, malformed JSON, anything unclassified — is a GuardError, and so is a
sha-matched artifact whose content the guard cannot certify (malformed grid
cells): the pin was minted against a malformed object, which neither a retry
nor a re-vendor fixes. A guard that cannot run must never read as green, and
must never mint the exit-1 verdict ("the contract diverged; re-vendor") for a
fault that is not a divergence.
Exit 2 still PRINTS any divergence already found: dropping them would report a
real divergent republish as an infrastructure flake, which a CI reader retries
forever. `--offline` runs only step 1 (local dev convenience; CI always runs
the full check).

Wiring: the `engine-grammar-pin-guard` job in .github/workflows/tests.yml —
which must NOT pass `--offline` (that would make the job permanently green
having verified nothing about the engine's published truth;
`test_ci_job_is_wired` pins this). The offline half is additionally pinned by
packages/contract-models/tests/unit/test_arrow_grammar.py so a plain pytest
run catches a hash or declared-version mismatch without network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packages" / "contract-models" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard_lib import BASE_URL, GuardError  # noqa: E402
# A 403/404 arrives as `_guard_lib.ObjectMissing`, a GuardError subclass:
# this guard has no state where a missing object is anything but
# infrastructure, so it needs no special case — exit 2 either way.
from _guard_lib import fetch as _fetch  # noqa: E402

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


def _declared_version(obj: dict, *, context: str) -> str:
    """The version an artifact stamps on ITSELF, under the key
    `arrow_grammar.ARTIFACT_VERSION_KEY` names.

    Reading this is strictly better than deriving the version from the URL
    path: a mislabeled publish — right path, wrong contents — is invisible to
    a path-derived version but fails here. An artifact that declares nothing
    is a GuardError: from grammar v1.1.0 / matrix v2.0.0 on, every published
    engine artifact carries the key, so its absence means the guard is looking
    at something it cannot verify, not that the version is fine.
    """
    declared = obj.get(arrow_grammar.ARTIFACT_VERSION_KEY)
    if not isinstance(declared, str):
        # Absent, null, or a non-string (e.g. `"version": 2`) all land here:
        # without a readable version there is no assertion to make.
        raise GuardError(
            f"{context} has no string `{arrow_grammar.ARTIFACT_VERSION_KEY}` "
            f"(got {declared!r}) — every published engine artifact "
            "self-declares its version. A pin naming a PRE-envelope version "
            "(grammar < 1.1.0, matrix < 2.0.0) lands here too, and is a "
            "deliberate one-way door: this guard cannot verify those."
        )
    return declared


def _version_mismatch(declared: str, pinned: str, *, remediation: str, context: str) -> list[str]:
    """Compare an artifact's self-declared version against its pin.

    `remediation` differs by call site and must not be generalised: offline,
    no published object has been fetched at all, so blaming the publish there
    would send the reader to the wrong place.
    """
    if declared == pinned:
        return []
    return [
        f"{context} declares version {declared!r} but the pin says "
        f"{pinned!r} — {remediation}"
    ]


def check_offline() -> list[str]:
    """Step 1 — the vendored bytes hash to the stated pin, and the manifest's
    own `version` agrees with it. (Existence is guaranteed here: a missing file
    already failed the module import above.)

    The self-declared check lives offline rather than in `check_published`
    because step 2 byte-compares the published grammar against this same file:
    once those bytes are equal, a statement about the vendored copy's declared
    version is a statement about the published object's. That inference needs
    step 2 to have actually run, so under `--offline` (and on any run where
    step 1 fails, since `main` then skips step 2) this asserts the vendored
    copy only — which is still the file every derivation is built from.
    """
    # Inspecting the vendored path IS the guard's purpose; the underscore marks
    # not-public-API, not not-touchable. (Same at the read in check_published.)
    vendored = arrow_grammar._GRAMMAR_PATH  # skipcq: PYL-W0212
    raw = vendored.read_bytes()
    digest = _sha256(raw)
    if digest != arrow_grammar.ENGINE_GRAMMAR_SHA256:
        # Return early: with the bytes unaccounted for, anything parsed out of
        # them describes a file we have already rejected.
        return [
            f"vendored {vendored.name} hashes to {digest}, but the pin in "
            f"arrow_grammar.py says {arrow_grammar.ENGINE_GRAMMAR_SHA256} — "
            "the vendored file and the pin must move together"
        ]
    context = f"vendored {vendored.name}"
    declared = _declared_version(_parse_object(raw, context=context), context=context)
    return _version_mismatch(
        declared,
        arrow_grammar.ENGINE_GRAMMAR_VERSION,
        remediation=(
            "the vendored file and the pin constants were not moved together; "
            "re-vendor the published object and bump both"
        ),
        context=context,
    )


def _check_matrix_grid(matrix: dict, matrix_url: str, failures: list[str]) -> None:
    """The conversion-matrix grid certification, in full and in the order the
    module docstring states it: key parity with the grammar family set (row
    and column), cell shape (every cell an object whose mode is a non-empty
    string), and diagonal identity. Runs only for a sha-matched object whose
    self-declared version equals the pin — the caller rejects a mislabeled
    object before any grid verdict.
    """
    # From v2.0.0 the grid sits under `conversions`, beside the artifact's
    # own `version`; v1.0.0 was the bare grid. Read the key, and treat its
    # absence as "cannot run" — falling back to the flat shape would make
    # the guard silently compare the ENVELOPE's keys against the family
    # set and report a nonsense diff.
    grid = matrix.get(arrow_grammar.MATRIX_CONVERSIONS_KEY)
    if not isinstance(grid, dict):
        raise GuardError(
            f"{matrix_url} has no `{arrow_grammar.MATRIX_CONVERSIONS_KEY}` "
            "object — the conversion matrix carries its grid there from "
            "v2.0.0 on"
        )
    bad_rows = sorted(
        row for row, cols in grid.items() if not isinstance(cols, dict)
    )
    if bad_rows:
        raise GuardError(
            f"{matrix_url} `{arrow_grammar.MATRIX_CONVERSIONS_KEY}` is not "
            f"a dict-of-dicts grid — {len(bad_rows)} row(s) hold a "
            f"non-object value: "
            f"{bad_rows[:5]}{' …' if len(bad_rows) > 5 else ''}"
        )
    # Existential floor. Every parity check below is an `all()` or a set
    # equality, both vacuously TRUE on empty input — an empty grid against
    # an empty family set would pass the whole block and print the guard's
    # strongest green while the shipped contract accepts nothing.
    #
    # Only the GRAMMAR side is guarded here. An empty published grid is a
    # definite divergence, not an un-runnable guard, so it belongs to the
    # `rows != grammar_families` comparison below, which reports it as
    # exit 1 and names every missing family. Guarding it here instead would
    # downgrade a real divergence to "could not run".
    #
    # `arrow_grammar` already refuses to import an empty `families`, so this
    # is unreachable today; it stays as the local statement of an invariant
    # the whole block silently depends on.
    grammar_families = set(arrow_grammar.FAMILY_NAMES)
    if not grammar_families:
        raise GuardError(
            "the vendored grammar has an empty family set — every parity "
            "check below would pass vacuously and certify nothing"
        )
    rows = set(grid)
    if rows != grammar_families:
        failures.append(
            "conversion-matrix family keys != grammar families: "
            f"matrix-only={sorted(rows - grammar_families)}, "
            f"grammar-only={sorted(grammar_families - rows)}"
        )
    else:
        bad_cols = {
            row for row, cols in grid.items() if set(cols) != grammar_families
        }
        if bad_cols:
            failures.append(
                "conversion-matrix rows with column keys != grammar "
                f"families: {sorted(bad_cols)}"
            )
    # The cells are READ, not just key-counted — the parity checks above
    # inspect keys only, so without this a grid whose every cell is `[]`
    # or null would pass the whole block and print the guard's strongest
    # green. Certified: each cell is an object whose mode is a non-empty
    # string. NOT certified: any off-diagonal mode value — the mode
    # vocabulary is engine-owned and deliberately not restated in this
    # repo (see MATRIX_IDENTITY_MODE in arrow_grammar.py).
    malformed = sorted(
        f"{row}->{col}"
        for row, cols in grid.items()
        for col, cell in cols.items()
        if not (
            isinstance(cell, dict)
            and isinstance(cell.get(arrow_grammar.MATRIX_CELL_MODE_KEY), str)
            and cell[arrow_grammar.MATRIX_CELL_MODE_KEY]
        )
    )
    if malformed:
        # GuardError, not a divergence: the sha256 already matched, so
        # these are the exact bytes the pin was minted against — malformed
        # cells mean the pin itself was minted against a malformed
        # artifact, a state neither re-vendoring (the exit-1 remediation)
        # nor a retry fixes. Raised AFTER the parity checks, which only
        # append: a publish that is both family-divergent and malformed
        # reports both findings — the exit-2 printing contract prints the
        # appended parity divergence beside this raise.
        raise GuardError(
            f"{matrix_url}: {len(malformed)} cell(s) are not objects with "
            f"a non-empty string `{arrow_grammar.MATRIX_CELL_MODE_KEY}`: "
            f"{malformed[:5]}{' …' if len(malformed) > 5 else ''}"
        )
    # The one universal per-cell invariant: the engine generates every
    # diagonal (family-to-itself) cell with the identity mode,
    # unconditionally, and pins that with its own tests — so a well-formed
    # published matrix contradicting it is a definite divergence. `row in
    # cols` narrows to cells that exist: a MISSING diagonal cell is a
    # column-parity divergence the parity checks above already report —
    # this check is about the mode value, not presence. It also stays
    # below the cell-shape raise: it reads each diagonal cell's mode, so
    # it needs the shape guaranteed.
    off_identity = sorted(
        row
        for row, cols in grid.items()
        if row in cols
        and cols[row][arrow_grammar.MATRIX_CELL_MODE_KEY]
        != arrow_grammar.MATRIX_IDENTITY_MODE
    )
    if off_identity:
        failures.append(
            "conversion-matrix diagonal cells do not declare the identity "
            f"mode {arrow_grammar.MATRIX_IDENTITY_MODE!r}: {off_identity} "
            "— the engine generates the diagonal identity unconditionally, "
            "or the engine renamed the identity mode, in which case "
            "MATRIX_IDENTITY_MODE moves with the pin bump"
        )


def check_published(failures: list[str]) -> list[str]:
    """Steps 2-4 — published objects vs the pins. Returns notices.

    Divergences are APPENDED to the caller's `failures` rather than returned,
    so a `GuardError` raised by a later check cannot discard the definite
    verdicts already reached. Losing them would be actively misleading: a
    published grammar that differs from the vendored copy is the divergence
    this whole guard exists to catch, and reporting only "could not run"
    invites a CI reader to retry it as a flake forever.
    """
    notices: list[str] = []

    grammar_url = (
        f"{BASE_URL}/{arrow_grammar.ENGINE_GRAMMAR_RESOURCE}/"
        f"v{arrow_grammar.ENGINE_GRAMMAR_VERSION}/"
        f"{arrow_grammar.ENGINE_GRAMMAR_FILENAME}"
    )
    published = _fetch(grammar_url)
    if published != arrow_grammar._GRAMMAR_PATH.read_bytes():  # skipcq: PYL-W0212
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
        mislabel = _version_mismatch(
            _declared_version(matrix, context=matrix_url),
            arrow_grammar.CONVERSION_MATRIX_VERSION,
            remediation=(
                "the published object is mislabeled, or the pin names a "
                "version whose object holds something else"
            ),
            context=matrix_url,
        )
        if mislabel:
            # The bytes DID hash to the pin, so this mislabel indicts the
            # pin-constants pair itself — version and sha256 minted against
            # different objects — and the object is rejected outright.
            # Mirror the reasoning check_offline states for its own early
            # return: one failure, one cause — a grid verdict about a
            # document already disclaimed would be a second, possibly
            # nonsense finding the reader must de-causate. The deliberate
            # exit-code consequence: a mislabeled matrix whose grid is also
            # malformed exits 1 on the mislabel alone where it previously
            # exited 2 — sound, because the mislabel's remediation fixes
            # the inconsistent pin pair. Step 4 below still runs: the
            # latest.json pointers are separate objects this rejection says
            # nothing about.
            failures.extend(mislabel)
        else:
            _check_matrix_grid(matrix, matrix_url, failures)

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
            # Reachable only AFTER steps 2-3 fetched the pinned immutable
            # objects successfully (a genuinely unpublished pin raises
            # GuardError at that fetch, exit 2, and never gets here) — so
            # "the engine has not published the pin" would be provably false.
            # It is the mutable pointer that lags. Still exit 1: a red state
            # a human must look at, but with the pointer's remediation, not
            # the divergence default of re-vendoring.
            failures.append(
                f"{resource}: latest.json says v{latest}, but the pinned "
                f"v{pinned} object is published — this same run just fetched "
                "it. The mutable pointer lags a published pinned object: a "
                "stale latest.json (the pointers rely on a 5-minute TTL, not "
                "invalidation) or a half-completed publish. Re-check after "
                "the TTL and repair the pointer if it persists — "
                "re-vendoring does not fix the pointer"
            )
    return notices


def _report(failures: list[str]) -> None:
    """Print divergences found so far. Called on every exit path, including the
    GuardError ones — a definite verdict already reached must never be dropped
    because a later check could not run."""
    for failure in failures:
        print(f"::error::{failure}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    # `__doc__` is None under `python -OO`; falling over here would exit 1 —
    # the "contract diverged, go re-vendor" verdict — for a flag that has
    # nothing to do with the contract.
    parser = argparse.ArgumentParser(
        description=(__doc__ or "engine-grammar pin guard").splitlines()[0]
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "run only the local checks — vendored hash + self-declared version "
            "(no network); CI always runs the full check"
        ),
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

    # Owned here, not inside the checks, so every exit path below can report
    # what was already found (see `_report`).
    failures: list[str] = []
    notices: list[str] = []
    try:
        failures.extend(check_offline())
        if not args.offline and not failures:
            notices.extend(check_published(failures))
    except GuardError as exc:
        _report(failures)
        print(f"::error::engine-grammar-pin guard could not run: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — see comment below
        # Anything not already classified is still "the guard could not run",
        # never a divergence. Without this the fallthrough is exit 1, which the
        # design defines as a DEFINITE verdict ("the contract diverged from
        # engine truth") whose remediation is re-vendoring — a confident and
        # wrong instruction. Reachable: the vendored file disappearing between
        # import and read, or `urlopen` raising ValueError/UnicodeError, which
        # `_fetch` does not catch.
        _report(failures)
        print(
            "::error::engine-grammar-pin guard could not run: unexpected "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    for notice in notices:
        print(f"::notice::{notice}")
    if failures:
        _report(failures)
        return 1
    scope = (
        "offline hash + declared version"
        if args.offline
        else (
            "published objects + hashes + declared versions + family parity "
            "+ cell shape + diagonal identity"
        )
    )
    print(
        f"engine-grammar pin OK ({scope}): "
        f"{arrow_grammar.ENGINE_GRAMMAR_RESOURCE} v{arrow_grammar.ENGINE_GRAMMAR_VERSION}, "
        f"{arrow_grammar.CONVERSION_MATRIX_RESOURCE} v{arrow_grammar.CONVERSION_MATRIX_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
