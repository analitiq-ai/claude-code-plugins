"""Fixtures for the validator tests.

These need `pydantic` plus the two public source packages, both of which
contribute to the `analitiq` PEP 420 namespace from their own source trees:
`contract-models/src` (`analitiq.contracts.*`) and `validator/src`
(`analitiq.validator`). Nothing private is on the path — the validator depends
only on the public contract, so the tests exercise exactly what an installed
consumer gets. Run explicitly: `python -m pytest validator/tests`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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


@pytest.fixture(scope="session")
def validator():
    """The validator package, imported from this repo's source (src layout).

    `analitiq` resolves as a PEP 420 namespace spanning both source trees;
    importing the package self-registers every kind's validators. The returned
    module re-exports every symbol the tests use.
    """
    import analitiq.validator

    return analitiq.validator


#: A CLI run that has not returned is a defect, not a slow machine: the checks
#: driven this way are bounded, so exceeding this means something stopped coming
#: back. Generous by orders of magnitude, because what it separates is a bounded
#: run from an unbounded one.
CLI_DEADLINE_SECONDS = 60.0

_CLI_PROGRAM = "import sys; from analitiq.validator import main; sys.exit(main())"


@pytest.fixture
def validator_cli(tmp_path):
    """Drive `analitiq-validate` in a child process.

    The CLI is the integration surface consumers depend on, and a child process
    is the only place some of its behaviour is observable at all: an exit code, a
    stream that has to stay clean, and a check whose regression is a run that does
    not come back rather than one that answers wrongly. A direct call would hang
    the suite where this fails it.

    Only the two public source trees reach the child — the validator and the
    contract models it depends on — which is exactly what an installed consumer
    has, so nothing private can hold a test up.
    """
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(VALIDATOR_SRC_ROOT), str(CONTRACTS_SRC_ROOT)]),
        "DOMAIN": "analitiq.ai",
    }

    def run(*argv, timeout=CLI_DEADLINE_SECONDS):
        try:
            return subprocess.run(
                [sys.executable, "-c", _CLI_PROGRAM, *argv],
                capture_output=True, text=True, env=env, check=False, timeout=timeout)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the validator did not return within {timeout}s: {argv}")

    def on_document(doc, filename="doc.json", timeout=CLI_DEADLINE_SECONDS):
        """The same, over a document staged into this test's `tmp_path`.

        Staged rather than passed in memory because the CLI resolves a bundle's
        siblings from the document's own path, which is what the connector-walk
        entry point needs.
        """
        path = tmp_path / filename
        path.write_text(json.dumps(doc))
        return run("--document", str(path), timeout=timeout)

    return SimpleNamespace(run=run, on_document=on_document)
