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


#: The entry points that return findings and are therefore screened. Stated
#: as data because two things read it: the class below, which wraps exactly
#: these, and the coverage test, which fails a test module importing one of
#: them straight from the package and so out from under the wrap.
SCREENED_ENTRY_POINTS = ("validate_document", "check_coverage")


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
    call through this fixture refuses a guard finding, and a test that means to
    provoke one says so with `expect_crash=True`.

    What the screen cannot reach is a test that imports the entry point from
    the package instead of taking the fixture. That is not left to memory:
    :data:`SCREENED_ENTRY_POINTS` names what is wrapped, and a test in
    `test_screen_coverage.py` fails on a module importing one of those names
    directly. The out-of-process path — the CLI — is screened at its own helper
    for the same reason, since no fixture can wrap a subprocess.
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
        """Screened too. Every entry point returning findings is wrapped —
        leaving any of them to `__getattr__` covers some of the ways a crash
        reaches a test and not the rest, which is the shape that reads as
        covered."""
        return self._screen(
            self._module.check_coverage(*args, **kwargs), expect_crash)


#: What the CLI is driven with, and where it imports from. Only the two public
#: source trees, matching an installed consumer exactly.
_CLI_CODE = "from analitiq.validator import main; import sys; sys.exit(main())"


def run_cli(tmp_path, doc, filename="doc.json"):
    """Drive `analitiq-validate --document` on one authored document, screened.

    The CLI is the path no fixture can wrap — it runs out of process — and it
    is the one where a crash is hardest to see: `main()` exits 1 on any
    error-severity finding, so a guard finding produces exactly the
    `returncode == 1` / `passed is False` a caller asserts, and the test passes
    on a document nothing judged. So the screen is applied here, once, and
    every CLI test in this suite goes through it.

    Returns the `CompletedProcess`; callers assert the exit code and parse
    stdout themselves.
    """
    import json
    import subprocess

    path = tmp_path / filename
    path.write_text(json.dumps(doc))
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(VALIDATOR_SRC_ROOT), str(CONTRACTS_SRC_ROOT)]),
        "DOMAIN": "analitiq.ai",
    }
    result = subprocess.run(
        [sys.executable, "-c", _CLI_CODE, "--document", str(path)],
        capture_output=True, text=True, env=env, check=False)
    # Asserted, not tolerated: the CLI's contract is that it always emits
    # `{"passed": …, "findings": […]}`. Falling back to "no findings" on
    # unparseable output would screen nothing and say nothing, which is the
    # shape this helper exists to remove.
    assert result.stdout.strip(), (
        "the CLI produced no stdout, so there is no verdict to screen or to "
        f"assert against. stderr:\n{result.stderr}")
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict) and "findings" in payload, (
        f"the CLI emitted no `findings` key: {result.stdout[:400]}")

    import analitiq.validator

    crashed = [f for f in payload["findings"]
               if analitiq.validator.is_guard_finding(f)]
    assert not crashed, (
        "a check crashed inside the CLI, so this test's exit code says "
        "nothing about what the validator decided:\n"
        + "\n".join(f["message"] for f in crashed))
    return result


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
