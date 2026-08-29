"""The crash screen: what is screened, and the helpers that screen it.

A module of its own rather than `conftest`, because `conftest` is a name every
test root defines. `from conftest import …` resolves to whichever one reached
`sys.modules` first, so these imports bound `tests/pipeline_builder/conftest`
under some collection orders and the modules failed to import at all — a
failure that depends on which roots pytest is given, not on anything here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGES_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_SRC_ROOT = PACKAGES_ROOT / "contract-models" / "src"
VALIDATOR_SRC_ROOT = PACKAGES_ROOT / "validator" / "src"


#: The entry points that return findings and are therefore screened. Stated
#: as data because two things read it: the class below, which wraps exactly
#: these, and the coverage test, which fails a test module importing one of
#: them straight from the package and so out from under the wrap.
SCREENED_ENTRY_POINTS = (
    "validate_document", "check_coverage", "validate_pipeline_bundle",
)

#: The name shapes of every OTHER function that returns findings — a per-kind
#: validator, and anything ending `_findings`. Reaching one of those directly
#: gets an unscreened list just as surely as reaching an entry point does, and
#: there are more of them than a hand-kept list would stay level with. Matched
#: on the name, which is a question about what a module imported; whether the
#: findings it returns are then asserted over is nobody's mechanism to decide.
SCREENED_NAME_SHAPES = ("_validate_", "_findings")


class _ScreenedValidator:
    """The validator package, with every finding-returning entry point screened.

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
        attr = getattr(self._module, name)
        if callable(attr) and any(
                shape in name for shape in SCREENED_NAME_SHAPES):
            # Reaching a finding-returning helper through the fixture is the
            # channel the import check cannot see — an attribute access is not
            # an import. Screened here so both channels answer the same, and
            # `SCREENED_NAME_SHAPES` is the one statement of which names those
            # are rather than two lists to keep level.
            def _screened(*args, expect_crash: bool = False, **kwargs):
                return self._screen(attr(*args, **kwargs), expect_crash)
            return _screened
        return attr

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

    def validate_pipeline_bundle(self, *args, expect_crash: bool = False, **kwargs):
        """Screened too. Its name matches neither shape in
        `SCREENED_NAME_SHAPES`, so `__getattr__` would hand it back raw — and
        it is public, which is the spelling every bundle test uses."""
        return self._screen(
            self._module.validate_pipeline_bundle(*args, **kwargs), expect_crash)

    def check_coverage(self, *args, expect_crash: bool = False, **kwargs):
        """Screened too. Every entry point returning findings is wrapped —
        leaving any of them to `__getattr__` covers some of the ways a crash
        reaches a test and not the rest, which is the shape that reads as
        covered."""
        return self._screen(
            self._module.check_coverage(*args, **kwargs), expect_crash)


#: What the CLI is driven with, and where it imports from. Only the two public
#: source trees, matching an installed consumer exactly. Stated once: every CLI
#: test in this suite runs through the helpers below, so a second copy would be
#: a spelling of the invocation nothing keeps in step with this one.
CLI_CODE = "from analitiq.validator import main; import sys; sys.exit(main())"
CLI_PYTHONPATH = os.pathsep.join(
    [str(VALIDATOR_SRC_ROOT), str(CONTRACTS_SRC_ROOT)])


def cli_env(**overrides):
    """The environment the CLI subprocess runs in."""
    return {**os.environ, "PYTHONPATH": CLI_PYTHONPATH,
            "DOMAIN": "analitiq.ai", **overrides}


def run_cli_argv(*args, env=None):
    """Run the CLI with these arguments, screening any verdict it emits.

    The CLI is the path no fixture can wrap — it runs out of process — and the
    one where a crash is hardest to see: `main()` exits 1 on any
    error-severity finding, so a guard finding produces exactly the
    `returncode == 1` / `passed is False` a caller asserts, and the test passes
    on a document nothing judged.

    A run that emits no verdict at all is not screened, because there is
    nothing to screen: an argument error exits before any document is read and
    reports through argparse on stderr. Whether stdout carries a verdict is
    read off the output rather than declared by the caller, so a run that stops
    emitting one is not quietly excused by a flag someone passed.
    """
    import json
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", CLI_CODE, *args],
        capture_output=True, text=True, env=env or cli_env(), check=False)
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return result
    if not (isinstance(payload, dict) and isinstance(payload.get("findings"), list)):
        return result

    import analitiq.validator

    crashed = [f for f in payload["findings"]
               if analitiq.validator.is_guard_finding(f)]
    assert not crashed, (
        "a check crashed inside the CLI, so this test's exit code says "
        "nothing about what the validator decided:\n"
        + "\n".join(f["message"] for f in crashed))
    return result


def run_cli(tmp_path, doc, filename="doc.json"):
    """Drive `analitiq-validate --document` on one authored document, screened.

    Asserts a verdict came back, which :func:`run_cli_argv` cannot: this
    helper is handed a document, so "the CLI emitted nothing" is a failure
    rather than one of the outcomes under test.
    """
    import json

    path = tmp_path / filename
    path.write_text(json.dumps(doc))
    result = run_cli_argv("--document", str(path))
    assert result.stdout.strip(), (
        "the CLI produced no stdout for a document it was handed, so there is "
        f"no verdict to assert against. stderr:\n{result.stderr}")
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict) and "findings" in payload, (
        f"the CLI emitted no `findings` key: {result.stdout[:400]}")
    return result
