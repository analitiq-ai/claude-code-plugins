"""The interpreter floor the packages declare is one the suite actually runs.

A `requires-python` floor is a support statement, and an untested one is a
claim instead: `pip install` admits the interpreter, so a user gets the
package, and any stdlib behaviour that arrived after the floor reads as working
code here while refusing their documents there. That is not hypothetical — a
calendar reader resting on `datetime.fromisoformat` was correct on the versions
CI ran and called every zone-bearing wire sample impossible on the declared
floor, because arbitrary-ISO-8601 parsing landed one version later.

The floor is owned by each package's `requires-python`; the matrix is owned by
the workflow. Neither restates the other, and this fails when they part.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"
_PYPROJECTS = (
    REPO_ROOT / "packages" / "contract-models" / "pyproject.toml",
    REPO_ROOT / "packages" / "validator" / "pyproject.toml",
)


def _floor(pyproject: Path) -> tuple[int, int]:
    spec = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    declared = spec["requires-python"]
    match = re.fullmatch(r">=\s*(\d+)\.(\d+)", declared.strip())
    assert match, (
        f"{pyproject.name} declares requires-python {declared!r}; this guard "
        "reads a simple `>=X.Y` floor. Widen it deliberately, or state the "
        "floor in the form it reads"
    )
    return int(match.group(1)), int(match.group(2))


def _matrix() -> list[tuple[int, int]]:
    """The pytest job's matrix, reached by name rather than by position.

    Parsed, not matched: a regex for the first bracketed `python-version:` in
    the file grades whichever job happens to come first, so a second matrixed
    job landing above this one would silently move the guard onto it and it
    would go on reporting agreement.
    """
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    try:
        declared = workflow["jobs"]["pytest"]["strategy"]["matrix"]["python-version"]
    except (KeyError, TypeError) as exc:
        raise AssertionError(
            f"{_WORKFLOW.name} has no jobs.pytest.strategy.matrix."
            f"python-version ({exc}) — the job may have been renamed or "
            "restructured, in which case this guard is reading nothing and "
            "reporting agreement"
        ) from exc
    assert isinstance(declared, list) and declared, (
        f"the pytest matrix is not a non-empty list: {declared!r}")
    versions = []
    for entry in declared:
        match = re.fullmatch(r"(\d+)\.(\d+)", str(entry))
        assert match, f"matrix entry {entry!r} is not an `X.Y` version"
        versions.append((int(match.group(1)), int(match.group(2))))
    return versions


def test_the_packages_agree_on_one_floor():
    floors = {path.name: _floor(path) for path in _PYPROJECTS}
    assert len(set(floors.values())) == 1, (
        f"the packages declare different interpreter floors: {floors}. They "
        "release in lockstep and one installs the other, so the lower floor "
        "is the one users actually get"
    )


def test_the_declared_floor_is_tested():
    floor = _floor(_PYPROJECTS[0])
    matrix = _matrix()
    assert floor in matrix, (
        f"the packages declare requires-python >={floor[0]}.{floor[1]}, and "
        f"the pytest matrix runs {['.'.join(map(str, v)) for v in matrix]} — "
        "so nothing here ever runs the oldest interpreter a user may install "
        "on. Add the floor to the matrix, or raise the floor to one the "
        "matrix covers"
    )


def test_this_interpreter_is_at_or_above_the_floor():
    """The floor is a floor for the suite too — a contributor running it on an
    older interpreter is measuring a configuration the packages do not
    support, and would read its failures as defects."""
    floor = _floor(_PYPROJECTS[0])
    assert sys.version_info[:2] >= floor, (
        f"this interpreter is {sys.version_info.major}.{sys.version_info.minor}"
        f", below the declared floor {floor[0]}.{floor[1]}"
    )
