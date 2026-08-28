"""Fixtures for the validator tests.

These need `pydantic` plus the two public source packages, both of which
contribute to the `analitiq` PEP 420 namespace from their own source trees:
`contract-models/src` (`analitiq.contracts.*`) and `validator/src`
(`analitiq.validator`). Nothing private is on the path — the validator depends
only on the public contract, so the tests exercise exactly what an installed
consumer gets. Run explicitly: `python -m pytest validator/tests`.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_SRC_ROOT = REPO_ROOT / "contract-models" / "src"
VALIDATOR_SRC_ROOT = REPO_ROOT / "validator" / "src"

# The contract models bind DOMAIN at import for the `$schema` host Literal. The
# published package pins it; in-repo the ambient value wins, so set the public
# host before the first import.
os.environ.setdefault("DOMAIN", "analitiq.ai")

for _root in (CONTRACTS_SRC_ROOT, VALIDATOR_SRC_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))


class _ScreenedValidator:
    """The validator package, with `validate_document` screened.

    A check that crashes becomes one guard finding — by design, so the rest of
    a document still reports. That makes a crash indistinguishable from a
    verdict to a test that only looks at how many findings came back or what
    is in them. A document the model layer aborts on returns one guard finding,
    and a test comparing what came back then compares one crash against itself
    — satisfying a count, an ordering, or a liveness assert while measuring
    nothing, so a mutation of the thing under test leaves the suite green.
    That is how the ordering guard here came to pass for a while.

    So the screen is here rather than in each test: every `validate_document`
    call refuses a guard finding, and a test that means to provoke one says so
    with `expect_crash=True`. Forgetting is not an available move.
    """

    def __init__(self, module):
        self._module = module

    def __getattr__(self, name):
        return getattr(self._module, name)

    def _screen(self, findings: list, expect_crash: bool) -> list:
        crashed = [f for f in findings if self._module.is_guard_finding(f)]
        if expect_crash:
            # An assertion, not a bypass: a test saying the crash IS its
            # subject has to keep provoking one. Otherwise the day the crash
            # stops happening — which is the day something got better — the
            # test keeps passing and nobody is told it now proves nothing.
            assert crashed, (
                "expect_crash=True, but nothing crashed. Either this document "
                "no longer provokes a crash — in which case drop the flag and "
                "assert what the validator now decides — or the test is "
                "pointed at the wrong document."
            )
            return findings
        assert not crashed, (
            "a check crashed on this document, so every assertion below is "
            "about the crash rather than about what the validator decided:\n"
            + "\n".join(f["message"] for f in crashed)
            + "\n\nFix the document, or pass expect_crash=True if the crash "
            "IS what this test is about."
        )
        return findings

    def validate_document(self, *args, expect_crash: bool = False, **kwargs):
        return self._screen(
            self._module.validate_document(*args, **kwargs), expect_crash)

    def check_coverage(self, *args, expect_crash: bool = False, **kwargs):
        """Screened too. It is the other entry point that returns findings, so
        leaving it to `__getattr__` covered one of the two ways a crash reaches
        a test."""
        return self._screen(
            self._module.check_coverage(*args, **kwargs), expect_crash)


@pytest.fixture(scope="session")
def validator():
    """The validator package, imported from this repo's source (src layout).

    `analitiq` resolves as a PEP 420 namespace spanning both source trees;
    importing the package self-registers every kind's validators. The returned
    object re-exports every symbol the tests use, and screens
    `validate_document` — see :class:`_ScreenedValidator`.
    """
    import analitiq.validator

    return _ScreenedValidator(analitiq.validator)
