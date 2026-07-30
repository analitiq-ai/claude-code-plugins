"""Make this suite's `importorskip` guards fatal in CI.

Every contract-backed module here opens with `pytest.importorskip(...)` on the
package it needs — `analitiq.validator` for the ones that validate documents,
`analitiq.contracts` for the ones that read the models directly (prose-only
modules need no import). That is right for offline local work but wrong for a
merge gate: if a source tree were missing or renamed, those modules would all
skip and the job would go green having validated nothing.

Both packages are checked, because they are separate `sys.path` entries: with
only `packages/validator/src` present, `analitiq.validator` resolves while the
contract models do not, and a validator-only check would let the modules that
import the models skip silently in CI.

The contracts probe names a SUBMODULE, not `analitiq.contracts` itself. The
in-repo contract tree is a namespace portion on purpose (no `__init__.py` — see
the root CLAUDE.md on why an installed wheel must not shadow it), so its spec
legitimately has `origin=None` and the bare-namespace test below would reject
a perfectly good checkout.

`tests/connector_builder/test_schema_drift.py` already solves this with
`DRIFT_REQUIRE_CONTRACT_MODELS`; that variable was the only consumer, so the
same contract is extended here rather than inventing a second one. CI sets it
(see .github/workflows/tests.yml).
"""
from __future__ import annotations

import importlib.util
import os

import pytest


def pytest_collectstart(collector):
    if os.environ.get("DRIFT_REQUIRE_CONTRACT_MODELS") != "1":
        return
    for module in ("analitiq.validator", "analitiq.contracts.stream"):
        try:
            spec = importlib.util.find_spec(module)
        except ModuleNotFoundError:
            # `find_spec` on a submodule imports its parent, which raises rather
            # than returning None when the whole tree is absent. Same verdict,
            # so fall through to the same message instead of a bare traceback.
            spec = None
        # `origin` None means a bare namespace directory, not an importable
        # module - the same false positive the plugin's own bootstrap guards
        # against.
        if spec is None or spec.origin in (None, "namespace"):
            raise pytest.UsageError(
                f"DRIFT_REQUIRE_CONTRACT_MODELS=1 but `{module}` has no "
                f"importable source (spec={spec!r}). This suite would have "
                "skipped silently. Check the repo-root conftest.py put "
                "packages/*/src on sys.path."
            )
