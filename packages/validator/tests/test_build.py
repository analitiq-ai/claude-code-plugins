"""Guards on the publishable artifact: dependency-light, versioned, and authored
in the public `analitiq.validator` namespace with no private codename.

The source IS the artifact — it is copied, not rendered — so the interesting
assertions are on the SOURCE. `test_staged_artifact_matches_source` pins that
property: if staging ever starts transforming again, it fails.
"""
import importlib.util
import re
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1] / "scripts" / "build.py"

# PEP 440: plain X.Y.Z, optionally with a pre-release (aN/bN/rcN) or .devN suffix
# — the validator-v* tag is cut from it, and pre-releases are used while iterating.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.dev\d+)?$")


def _build_module():
    spec = importlib.util.spec_from_file_location("validator_build", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_is_public_safe():
    leaks = _build_module().check_public_safe()
    assert leaks == [], (
        f"validator source imports disallowed packages {leaks}; the published "
        "package may use only stdlib + pydantic + the contract-models subset."
    )


def test_version_is_release_or_prerelease():
    version = _build_module().read_version()
    assert _VERSION_RE.match(version), (
        f"pyproject version {version!r} must be X.Y.Z with an optional pre-release "
        "suffix (rcN/.devN); the validator-v* tag is cut from it."
    )


def test_source_imports_the_public_contract():
    """The validator binds to the contract via its PUBLIC import path — the same
    one an installed consumer uses, which is why nothing needs rewriting."""
    build = _build_module()
    text = "\n".join(p.read_text() for p in build._source_files())
    assert "from analitiq.contracts." in text, (
        "the validator must import the contract models from the public namespace"
    )
    assert "alq" not in text, "the private codename must not appear in the source"


def test_staged_artifact_matches_source(tmp_path):
    """Staging COPIES; it does not transform.

    This is the property the whole split buys: what a maintainer reads in
    `validator/src/` is byte-for-byte what a consumer installs, at the same
    relative path — the rule case corpus is nested directories, so a copy that
    flattened names would lose it. If a render step ever creeps back in, this
    fails.
    """
    build = _build_module()
    dist = tmp_path / "dist"
    build.stage(dist)

    pkg = dist / "src" / "analitiq" / "validator"
    assert (pkg / "__init__.py").is_file(), "package must expose analitiq/validator/__init__.py"
    assert (pkg / "_core.py").is_file(), "the _core module must be staged"
    assert (pkg / "connectors.py").is_file(), "the connectors module must be staged"
    assert any((pkg / "cases").rglob("*.json")), "the rule case corpus must be staged"
    assert not (dist / "src" / "analitiq" / "__init__.py").exists(), (
        "analitiq/ must stay a PEP 420 namespace (no __init__.py) so it can be "
        "shared with analitiq-contract-models"
    )
    for src_path in build.tracked_files(build.SRC_DIR):
        relative = src_path.relative_to(build.SRC_DIR)
        assert (pkg / relative).read_bytes() == src_path.read_bytes(), (
            f"{relative} was transformed during staging — the published "
            "package must be the source verbatim"
        )


def test_every_data_file_under_src_is_tracked():
    """A data file the wheel must ship has to be tracked, because tracking is
    what stages it.

    `scripts/build.py` stages from `git ls-files`, so the one way a case file
    goes missing from a wheel is by never being committed. The suite reads the
    source tree, where the file is right there, so only a consumer grading the
    installed corpus would notice.
    """
    build = _build_module()
    tracked = set(build.tracked_files(build.SRC_DIR))
    on_disk = {
        path
        for path in build.SRC_DIR.rglob("*")
        if path.is_file()
        and path.suffix not in (".py", ".pyc")
        and "__pycache__" not in path.parts
    }
    assert on_disk, (
        f"{build.SRC_DIR} carries no data files at all — the rule case corpus "
        "lives here, so this is a path that stopped matching rather than a tree "
        "with nothing in it"
    )
    untracked = sorted(str(p.relative_to(build.SRC_DIR)) for p in on_disk - tracked)
    assert not untracked, (
        "data files under src/ that the wheel would silently drop — the build "
        f"stages tracked files only, so commit each of these: {untracked}"
    )


def test_staging_refuses_a_tracked_symlink(tmp_path):
    """A link would publish the bytes it points AT under the package's name.

    `shutil.copy2` resolves one, so without the refusal the staged tree — and
    the wheel built from it — carries content from outside the package, chosen
    by the link target rather than by the file a reviewer read. An immutable
    PyPI release makes that unrecallable.
    """
    import subprocess

    import pytest

    _build_module()  # puts `packages/` on sys.path, where build_shared lives
    import build_shared

    src = tmp_path / "pkg"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "real.json").write_text("{}")
    outside = tmp_path / "outside.txt"
    outside.write_text("content from outside the package")
    (src / "sub" / "link.json").symlink_to(outside)

    git = build_shared.git_executable()
    subprocess.run([git, "init", "-q", str(src)], check=True)
    subprocess.run([git, "-C", str(src), "add", "-A"], check=True)

    with pytest.raises(SystemExit, match="symbolic link"):
        build_shared.stage_tree(src, tmp_path / "staged")
