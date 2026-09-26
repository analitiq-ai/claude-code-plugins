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


@pytest.fixture(scope="session")
def validator():
    """The validator package, imported from this repo's source (src layout).

    `analitiq` resolves as a PEP 420 namespace spanning both source trees;
    importing the package self-registers every kind's validators. The returned
    module is the public surface; tests reach internals through its submodules.
    """
    import analitiq.validator

    return analitiq.validator


#: A child process running the validator that has not returned is a defect, not
#: a slow machine: the checks driven this way are bounded, so exceeding this means
#: something stopped coming back. Generous by orders of magnitude, because what it
#: separates is a bounded run from an unbounded one.
SUBPROCESS_DEADLINE_SECONDS = 60.0


#: Text the JSON parser refuses with something other than `JSONDecodeError`:
#: nesting past the interpreter's recursion limit raises `RecursionError`, and an
#: integer longer than the default `sys.get_int_max_str_digits()` a plain
#: `ValueError`. Every route that parses a document has to catch both.
_TEXT_REFUSED_OUTSIDE_JSONDECODEERROR = {
    "too-deep": "[" * 20_000 + "]" * 20_000,
    "oversized-integer": "1" * 5_000,
}


@pytest.fixture(params=list(_TEXT_REFUSED_OUTSIDE_JSONDECODEERROR.values()),
                ids=list(_TEXT_REFUSED_OUTSIDE_JSONDECODEERROR))
def text_refused_outside_jsondecodeerror(request):
    return request.param
