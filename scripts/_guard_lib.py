"""Shared plumbing for the CI guard scripts (`scripts/check_*.py`).

Each guard runs as a standalone stdlib-only script — its CI job installs
nothing — and each used to carry its own copy of the pieces below, which is
exactly the parallel-copy state `.claude/rules/no-drift-surfaces.md` forbids:
a behavioral fix to the host pinning, the strict-env contract, or the
annotation surface would land in one script and silently not the others.
This module is the single guard-side statement of those pieces; what stays
per-guard is semantics — what a divergence means, its exit codes, and its
remediation text.

Import shape: every guard inserts its own directory on `sys.path` before
`import _guard_lib`, so the import works both run directly
(`python3 scripts/check_*.py`) and spec-loaded by the test suites.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The serving host — a guard-side copy of the contract-owned
#: `analitiq.contracts.shared.common.SCHEMA_BASE_URL` (the guards cannot
#: import the contract package: it needs pydantic, and guard jobs install
#: nothing). The one copy, pinned to its owner by
#: `tests/schemas/test_contracts_version_render.py`.
BASE_URL = "https://schemas.analitiq.ai"

#: Where `VALIDATOR_PIN` is stated — the one place (root CLAUDE.md, "The
#: contract, and the runtime pin").
PIN_SOURCE = (
    REPO_ROOT / "plugins" / "analitiq-pipeline-builder" / "scripts" / "_bootstrap.py"
)


class GuardError(RuntimeError):
    """Infrastructure failure — the guard could not run to a verdict.

    Every guard maps this to exit 2: a guard that cannot run must never read
    as green, and never mint a divergence verdict for a fault that is not a
    divergence.
    """


class ObjectMissing(GuardError):
    """The fetched object does not exist (HTTP 403/404).

    A GuardError subclass so a guard that does not special-case a missing
    object still lands on exit 2; a guard for which absence is a legitimate
    verdict input catches this class before it propagates.
    """


def fetch(url: str) -> bytes:
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
            raise ObjectMissing(f"{url} → HTTP {exc.code}") from exc
        raise GuardError(f"fetch failed for {url}: HTTP {exc.code}") from exc
    except (
        # URLError and TimeoutError are OSError subclasses; HTTPException
        # (IncompleteRead/BadStatusLine) is not.
        http.client.HTTPException,
        OSError,
    ) as exc:
        raise GuardError(f"fetch failed for {url}: {exc}") from exc


def read_pin(pin_source: Path = PIN_SOURCE) -> str:
    """The full `analitiq-validator==X` requirement from `_bootstrap.py`."""
    try:
        source = pin_source.read_text(encoding="utf-8")
    except OSError as exc:
        raise GuardError(f"cannot read {pin_source}: {exc}") from exc
    match = re.search(
        r'^VALIDATOR_PIN = "(analitiq-validator==[^"]+)"$', source, re.MULTILINE
    )
    if not match:
        raise GuardError(f"VALIDATOR_PIN not found in {pin_source}")
    return match.group(1)


def read_pin_version(pin_source: Path = PIN_SOURCE) -> str:
    return read_pin(pin_source).split("==", 1)[1]


def read_strict_env(env_var: str) -> bool:
    """The strict-mode override contract. Only '1' or unset/'' parse.

    Anything else raises: a typo like `true` must not silently downgrade a
    strict run to warn-only.
    """
    value = os.environ.get(env_var, "")
    if value not in ("", "1"):
        raise GuardError(
            f"{env_var}={value!r} not recognized — set '1' for strict or leave unset"
        )
    return value == "1"


def surface_warning(text: str, *, title: str) -> None:
    """Print a window warning where someone will actually see it.

    A plain print in a green job is read by no one; on Actions, also emit a
    workflow annotation (shows on the PR checks UI) and a step summary.
    """
    print(text)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title={title}::{' '.join(text.split())}")
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(f"⚠️ {text}\n")


#: The version shape the pinned artifacts and their pointers declare — the
#: grammar, the matrix and the consumption manifest, at the versions this
#: repo pins. Anything else stops the guard (exit 2) rather than minting a
#: verdict: that is the direction a published format change fails.
VERSION_TRIPLE_RE = re.compile(r"^\d+\.\d+\.\d+$")


def parse_object(raw: bytes, *, context: str) -> dict:
    """A fetched or vendored document as a JSON object, or a GuardError.

    Malformed JSON and a non-object top level are both "the guard cannot
    read this", never a divergence verdict about bytes it could not parse.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuardError(f"{context} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise GuardError(f"{context} parsed to {type(parsed).__name__}, expected object")
    return parsed


def fetch_json(url: str, *, fetcher=fetch) -> dict:
    """`parse_object` over `fetcher(url)`. Each guard binds its own module-level
    fetch name here so its test suite can monkeypatch that one name."""
    return parse_object(fetcher(url), context=url)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_version(value: str, *, context: str) -> tuple[int, ...]:
    """A version triple as a comparable tuple, or a GuardError.

    Exactly three non-negative components: a malformed pointer must be a
    GuardError, not a crafted (and wrong) "pin AHEAD of latest" verdict from
    comparing tuples of different lengths.
    """
    if not VERSION_TRIPLE_RE.match(value):
        raise GuardError(f"{context}: unparseable version {value!r}")
    return tuple(int(part) for part in value.split("."))


def report_failures(failures: list[str]) -> None:
    """Print divergences found so far as workflow error annotations. Called on
    every exit path, including the GuardError ones — a definite verdict
    already reached must never be dropped because a later check could not
    run."""
    for failure in failures:
        print(f"::error::{failure}", file=sys.stderr)
