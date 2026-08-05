"""Pin the verdict semantics of scripts/check_engine_grammar_pin.py.

The guard's network half runs only in CI (`engine-grammar-pin-guard` job), so
its direction logic — newer publish = notice, pin-ahead = failure, malformed
anything = GuardError — would otherwise only ever execute against live healthy
data, where an inverted comparison is a permanent false green. Same charter as
test_validator_pin_guard.py: every verdict branch offline, with the fetch
monkeypatched out. The offline hash check itself is pinned by
packages/contract-models/tests/unit/test_arrow_grammar.py.
"""
from __future__ import annotations

import hashlib
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts")

_SCRIPT = REPO_ROOT / "scripts" / "check_engine_grammar_pin.py"
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"


@pytest.fixture(scope="module")
def guard():
    spec = spec_from_file_location("check_engine_grammar_pin", _SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.arrow_grammar is not None, "vendored grammar failed to import"
    return module


def _grammar_bytes(guard):
    return guard.arrow_grammar._GRAMMAR_PATH.read_bytes()


def _grid(guard, families):
    """A realistic conversions grid: every cell is the published three-key
    object shape (`fn`, `mode`, `runtime_checked`). Diagonal cells carry the
    identity mode; off-diagonal modes are SYNTHETIC strings, because the guard
    asserts only non-empty-string there — the real mode vocabulary is
    engine-owned and never restated in this repo.
    """
    ag = guard.arrow_grammar
    return {
        row: {
            col: {
                "fn": f"{row}_to_{col}",
                ag.MATRIX_CELL_MODE_KEY: (
                    ag.MATRIX_IDENTITY_MODE if row == col else "synthetic-mode"
                ),
                "runtime_checked": False,
            }
            for col in families
        }
        for row in families
    }


def _matrix_bytes(guard, *, families=None, version=None):
    """A conversion-matrix object in the v2 envelope: its own `version` beside
    a full grid. Healthy by default (grid over exactly the grammar families,
    version equal to the pin); `families` and `version` override each half to
    build the divergent cases. The module's matrix pin is pointed at whatever
    bytes come out (monkeypatched per-test).
    """
    ag = guard.arrow_grammar
    families = list(ag.FAMILY_NAMES if families is None else families)
    return json.dumps(
        {
            ag.ARTIFACT_VERSION_KEY: (
                ag.CONVERSION_MATRIX_VERSION if version is None else version
            ),
            ag.MATRIX_CONVERSIONS_KEY: _grid(guard, families),
        }
    ).encode()


def _urls(guard):
    ag = guard.arrow_grammar
    base = guard.BASE_URL
    return {
        "grammar": f"{base}/{ag.ENGINE_GRAMMAR_RESOURCE}/v{ag.ENGINE_GRAMMAR_VERSION}/{ag.ENGINE_GRAMMAR_FILENAME}",
        "grammar_latest": f"{base}/{ag.ENGINE_GRAMMAR_RESOURCE}/latest.json",
        "matrix": f"{base}/{ag.CONVERSION_MATRIX_RESOURCE}/v{ag.CONVERSION_MATRIX_VERSION}/{ag.CONVERSION_MATRIX_FILENAME}",
        "matrix_latest": f"{base}/{ag.CONVERSION_MATRIX_RESOURCE}/latest.json",
    }


def _stub_fetch(
    guard,
    monkeypatch,
    *,
    matrix_bytes,
    grammar_latest,
    matrix_latest,
    grammar_bytes=None,
):
    urls = _urls(guard)
    responses = {
        # Defaults to the vendored bytes, i.e. a healthy publish. Override to
        # express a divergent republish — step 2 is the check the whole guard
        # exists for, so it must be expressible here.
        urls["grammar"]: _grammar_bytes(guard) if grammar_bytes is None else grammar_bytes,
        urls["matrix"]: matrix_bytes,
        urls["grammar_latest"]: json.dumps({"version": grammar_latest}).encode(),
        urls["matrix_latest"]: json.dumps({"version": matrix_latest}).encode(),
    }
    monkeypatch.setattr(guard, "_fetch", lambda url: responses[url])
    monkeypatch.setattr(
        guard.arrow_grammar,
        "CONVERSION_MATRIX_SHA256",
        hashlib.sha256(matrix_bytes).hexdigest(),
    )


def test_healthy_publication_passes(guard, monkeypatch, capsys):
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard),
        grammar_latest=guard.arrow_grammar.ENGINE_GRAMMAR_VERSION,
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 0
    assert "::notice::" not in capsys.readouterr().out


def test_newer_engine_publication_is_a_notice_not_a_failure(guard, monkeypatch, capsys):
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard),
        grammar_latest="9.0.0",
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 0
    assert "::notice::" in capsys.readouterr().out


def test_pin_ahead_of_published_latest_fails(guard, monkeypatch, capsys):
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard),
        grammar_latest="0.9.0",
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 1
    assert "AHEAD" in capsys.readouterr().err


def test_matrix_family_divergence_fails(guard, monkeypatch, capsys):
    families = list(guard.arrow_grammar.FAMILY_NAMES)[:-1] + ["Struct"]
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard, families=families),
        grammar_latest=guard.arrow_grammar.ENGINE_GRAMMAR_VERSION,
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 1
    assert "family keys != grammar families" in capsys.readouterr().err


def test_matrix_column_divergence_fails(guard, monkeypatch, capsys):
    """The row keys agree with the grammar but one row's COLUMNS do not.

    Pins the real pre-fix bug: the row check was taught the `conversions` key
    while the column check kept iterating the whole document, so this payload
    produced `['version']` — a nonsense diff naming an envelope key as a
    family — instead of the offending family. Hence the exact-list assertion
    below rather than a substring match on the message.
    """
    ag = guard.arrow_grammar
    families = list(ag.FAMILY_NAMES)
    grid = _grid(guard, families)
    grid[families[0]].pop(families[-1])
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(
            {
                ag.ARTIFACT_VERSION_KEY: ag.CONVERSION_MATRIX_VERSION,
                ag.MATRIX_CONVERSIONS_KEY: grid,
            }
        ).encode(),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 1
    err = capsys.readouterr().err
    # The exact reported list: the offending family alone, no envelope key.
    assert f"column keys != grammar families: ['{families[0]}']" in err
    assert f"'{ag.ARTIFACT_VERSION_KEY}'" not in err


def test_grid_of_empty_cells_is_a_guard_error_not_the_strongest_green(
    guard, monkeypatch, capsys
):
    """Cells must be READ, not key-counted. Every parity check inspects keys
    only, so a grid whose every cell is `[]` — no `fn`, no `mode`, nothing —
    used to pass the full network check with exit 0, the guard's strongest
    green. And the sha256 pin MATCHES these bytes, meaning the pin itself was
    minted against a malformed artifact: a state neither re-vendoring nor a
    retry fixes, so it must classify as "guard could not run" (exit 2), never
    as the exit-1 verdict whose remediation is re-vendoring.
    """
    ag = guard.arrow_grammar
    families = list(ag.FAMILY_NAMES)
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(
            {
                ag.ARTIFACT_VERSION_KEY: ag.CONVERSION_MATRIX_VERSION,
                ag.MATRIX_CONVERSIONS_KEY: {
                    row: {col: [] for col in families} for row in families
                },
            }
        ).encode(),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err
    assert f"`{ag.MATRIX_CELL_MODE_KEY}`" in err


@pytest.mark.parametrize(
    "bad_cell",
    [
        None,
        [],
        {"fn": "utf8_to_utf8", "runtime_checked": False},  # mode absent
        {"fn": "utf8_to_utf8", "mode": "", "runtime_checked": False},
        {"fn": "utf8_to_utf8", "mode": 3, "runtime_checked": False},
    ],
)
def test_one_malformed_cell_is_a_guard_error(guard, monkeypatch, capsys, bad_cell):
    """Every way a single cell can fail the shape contract (not an object /
    mode absent / empty / non-string) is exit 2 — see the empty-cells test for
    why malformed-under-a-matching-sha is "cannot run", not a divergence."""
    ag = guard.arrow_grammar
    families = list(ag.FAMILY_NAMES)
    grid = _grid(guard, families)
    grid[families[0]][families[1]] = bad_cell
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(
            {
                ag.ARTIFACT_VERSION_KEY: ag.CONVERSION_MATRIX_VERSION,
                ag.MATRIX_CONVERSIONS_KEY: grid,
            }
        ).encode(),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err
    # The offending coordinate is named — a reader must not have to re-derive
    # which of ~hundreds of cells failed.
    assert f"{families[0]}->{families[1]}" in err


def test_non_identity_diagonal_cell_is_a_divergence(guard, monkeypatch, capsys):
    """Well-formed cells, but one DIAGONAL cell declares a non-identity mode.
    The engine generates the diagonal identity unconditionally and pins it
    with its own tests, so a published matrix contradicting that is a definite
    divergence (exit 1) naming the family — not an un-runnable guard."""
    ag = guard.arrow_grammar
    families = list(ag.FAMILY_NAMES)
    grid = _grid(guard, families)
    grid[families[0]][families[0]][ag.MATRIX_CELL_MODE_KEY] = "synthetic-mode"
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(
            {
                ag.ARTIFACT_VERSION_KEY: ag.CONVERSION_MATRIX_VERSION,
                ag.MATRIX_CONVERSIONS_KEY: grid,
            }
        ).encode(),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 1
    err = capsys.readouterr().err
    assert ag.MATRIX_IDENTITY_MODE in err
    assert families[0] in err


def test_matrix_self_declared_version_mismatch_fails(guard, monkeypatch, capsys):
    """The object at the pinned path declares a different version — a
    mislabeled publish, invisible to a version derived from the URL.

    Uses a PATCH-level difference, not a wildly different version: the realistic
    mislabel is `2.0.1` served at the `2.0.0` path, and only a near-miss kills a
    major-only or prefix comparison.
    """
    ag = guard.arrow_grammar
    near_miss = ag.CONVERSION_MATRIX_VERSION[:-1] + str(
        int(ag.CONVERSION_MATRIX_VERSION[-1]) + 1
    )
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard, version=near_miss),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 1
    err = capsys.readouterr().err
    # Both operands, in the right order — a swapped message reads plausibly.
    assert f"declares version {near_miss!r}" in err
    assert f"pin says {ag.CONVERSION_MATRIX_VERSION!r}" in err


def test_matrix_without_conversions_key_is_a_guard_error(guard, monkeypatch, capsys):
    """A well-formed envelope that self-declares the PINNED version but holds
    its grid somewhere other than `conversions`.

    This is the branch the no-fallback contract actually rests on, and the only
    payload that reaches it: every other malformed-matrix test trips the
    `version` check first. Without this, replacing the read with
    `matrix.get(KEY, matrix)` — the exact silent fallback the code refuses —
    leaves the whole suite green. It is also the realistic future shape: a
    later engine major renaming the grid key, which is precisely what the
    matrix itself did in v2.0.0.
    """
    ag = guard.arrow_grammar
    families = list(ag.FAMILY_NAMES)
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(
            {
                ag.ARTIFACT_VERSION_KEY: ag.CONVERSION_MATRIX_VERSION,
                "grid": _grid(guard, families),
            }
        ).encode(),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err
    assert f"has no `{ag.MATRIX_CONVERSIONS_KEY}` object" in err


def test_empty_grid_is_a_divergence_not_a_guard_error(guard, monkeypatch, capsys):
    """An empty published grid must not pass vacuously — and must be reported
    as the DIVERGENCE it is (exit 1, naming the missing families), not as
    "the guard could not run". Exit 2 here would tell a CI reader to retry an
    infrastructure flake that will never clear."""
    ag = guard.arrow_grammar
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(
            {
                ag.ARTIFACT_VERSION_KEY: ag.CONVERSION_MATRIX_VERSION,
                ag.MATRIX_CONVERSIONS_KEY: {},
            }
        ).encode(),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 1
    err = capsys.readouterr().err
    assert "family keys != grammar families" in err
    assert "Utf8" in err  # the diff names what is missing


def test_unexpected_exception_is_a_guard_error_not_a_divergence(
    guard, monkeypatch, capsys
):
    """Anything unclassified must be exit 2. Exit 1 is the definite verdict
    "the contract diverged; re-vendor" — a confident, wrong instruction for a
    fault that is not a divergence."""
    def _boom(_failures):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(guard, "check_published", _boom)
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "RuntimeError" in err


def test_a_guard_error_still_reports_divergences_already_found(
    guard, monkeypatch, capsys
):
    """The published grammar differs from the vendored copy (a definite
    divergence, step 2) AND the matrix lacks `conversions` (a GuardError,
    step 3). Exit 2 dominates, but the divergence must still be printed —
    otherwise the one check this guard exists for reports as an infra flake
    that a retry will never clear.
    """
    ag = guard.arrow_grammar
    _stub_fetch(
        guard,
        monkeypatch,
        grammar_bytes=_grammar_bytes(guard) + b"\n",
        matrix_bytes=json.dumps(
            {ag.ARTIFACT_VERSION_KEY: ag.CONVERSION_MATRIX_VERSION, "grid": {}}
        ).encode(),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err
    assert "differs from the vendored copy" in err


def test_published_grammar_differing_from_the_vendored_copy_fails(
    guard, monkeypatch, capsys
):
    """Step 2 — the check the whole guard exists for. A divergent republish (or
    a tampered vendored file) must fail; nothing else in the suite covers it."""
    _stub_fetch(
        guard,
        monkeypatch,
        grammar_bytes=_grammar_bytes(guard) + b"\n",
        matrix_bytes=_matrix_bytes(guard),
        grammar_latest=guard.arrow_grammar.ENGINE_GRAMMAR_VERSION,
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 1
    assert "differs from the vendored copy" in capsys.readouterr().err


def test_matrix_without_self_declared_version_is_a_guard_error(
    guard, monkeypatch, capsys
):
    ag = guard.arrow_grammar
    families = list(ag.FAMILY_NAMES)
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(
            {ag.MATRIX_CONVERSIONS_KEY: _grid(guard, families)}
        ).encode(),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "self-declares its version" in err


def test_pre_v2_bare_grid_is_a_guard_error_not_a_silent_fallback(
    guard, monkeypatch, capsys
):
    """The v1.0.0 shape — a bare dict-of-dicts with no envelope. Accepting it
    as a fallback would let the pin name v2 while the guard verified a v1
    payload; it must refuse to run instead.

    Asserts the observable verdict, not which check produces it (today the
    missing `version` key trips first, before the grid is even read) — the
    contract is that a pre-v2 payload can never reach a green verdict.
    """
    families = list(guard.arrow_grammar.FAMILY_NAMES)
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(_grid(guard, families)).encode(),
        grammar_latest=guard.arrow_grammar.ENGINE_GRAMMAR_VERSION,
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    assert "could not run" in capsys.readouterr().err


def test_malformed_matrix_is_a_guard_error(guard, monkeypatch, capsys):
    # sha256 pin matches the bytes, but the payload is not a dict-of-dicts —
    # must classify as "guard could not run" (2), never a divergence verdict.
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=b'"not a grid"',
        grammar_latest=guard.arrow_grammar.ENGINE_GRAMMAR_VERSION,
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    assert "could not run" in capsys.readouterr().err


def test_matrix_conversions_is_not_a_grid_is_a_guard_error(guard, monkeypatch, capsys):
    ag = guard.arrow_grammar
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=json.dumps(
            {
                ag.ARTIFACT_VERSION_KEY: ag.CONVERSION_MATRIX_VERSION,
                ag.MATRIX_CONVERSIONS_KEY: {"Utf8": "not a column map"},
            }
        ).encode(),
        grammar_latest=ag.ENGINE_GRAMMAR_VERSION,
        matrix_latest=ag.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    assert "not a dict-of-dicts grid" in capsys.readouterr().err


def test_malformed_latest_version_is_a_guard_error(guard, monkeypatch, capsys):
    # "1.0" would compare (1,0) < (1,0,0) and fabricate a pin-AHEAD verdict if
    # it were parsed leniently.
    _stub_fetch(
        guard,
        monkeypatch,
        matrix_bytes=_matrix_bytes(guard),
        grammar_latest="1.0",
        matrix_latest=guard.arrow_grammar.CONVERSION_MATRIX_VERSION,
    )
    assert guard.main([]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err and "unparseable version" in err


def test_offline_hash_mismatch_fails(guard, monkeypatch, capsys):
    monkeypatch.setattr(guard.arrow_grammar, "ENGINE_GRAMMAR_SHA256", "0" * 64)
    assert guard.main(["--offline"]) == 1
    assert "must move together" in capsys.readouterr().err


def test_offline_grammar_self_declared_version_mismatch_fails(
    guard, monkeypatch, capsys
):
    """The vendored bytes hash correctly but the pin names a different version
    than the manifest declares — which would publish `pinned at vX` prose that
    misdescribes the vocabulary the contract actually derives from.

    Near-miss version, and both operands asserted, for the reasons given in
    test_matrix_self_declared_version_mismatch_fails.
    """
    ag = guard.arrow_grammar
    real = ag.ENGINE_GRAMMAR_VERSION
    near_miss = real[:-1] + str(int(real[-1]) + 1)
    monkeypatch.setattr(ag, "ENGINE_GRAMMAR_VERSION", near_miss)
    assert guard.main(["--offline"]) == 1
    err = capsys.readouterr().err
    assert f"declares version {real!r}" in err
    assert f"pin says {near_miss!r}" in err
    # Offline fetches nothing, so it must not blame the publish.
    assert "published object is mislabeled" not in err


def test_offline_vendored_grammar_without_version_is_a_guard_error(
    guard, monkeypatch, capsys
):
    """A vendored manifest whose declared version cannot be read at all. Must
    be exit 2, not a defaulted-and-green pass."""
    monkeypatch.setattr(guard.arrow_grammar, "ARTIFACT_VERSION_KEY", "absent_key")
    assert guard.main(["--offline"]) == 2
    assert "self-declares its version" in capsys.readouterr().err


def test_offline_healthy_passes_and_never_touches_the_network(
    guard, monkeypatch, capsys
):
    """The offline success path: exit 0, and `--offline` genuinely suppresses
    the fetch rather than merely being documented to."""
    # Recorded rather than raised: `main` now catches every exception and turns
    # it into exit 2, which would swallow a sentinel AssertionError and report
    # this as an opaque `assert 2 == 0`.
    attempted: list[str] = []
    monkeypatch.setattr(guard, "_fetch", lambda url: attempted.append(url) or b"")
    assert guard.main(["--offline"]) == 0
    assert not attempted, f"--offline must not fetch, but tried {attempted}"
    out = capsys.readouterr().out
    # The banner must describe what actually ran — it is the only signal a CI
    # reader gets about which half of the guard executed.
    assert "offline hash + declared version" in out


def test_ci_job_is_wired():
    workflow = _WORKFLOW.read_text()
    assert "engine-grammar-pin-guard:" in workflow
    assert "check_engine_grammar_pin.py" in workflow
    # The one flag that makes the guard not run is the one the charter test has
    # to forbid: `--offline` is the obvious "fix" when the CDN flakes, and it
    # would leave the job permanently green having verified nothing about the
    # engine's published truth. Checked over every non-comment line rather than
    # the guard's own `run:` line, so a `run: |` block form cannot slip past.
    invocations = [
        line
        for line in workflow.splitlines()
        if "--offline" in line and not line.lstrip().startswith("#")
    ]
    assert not invocations, f"--offline must never run in CI: {invocations}"


def test_fetch_refuses_a_url_outside_the_pinned_base(guard):
    """The refusal is what the urlopen suppression rests on — pin it.

    Every other test stubs `_fetch` wholesale, so without this the branch that
    justifies the audit suppression could be deleted and nothing would notice.
    The lookalike host covers the sharp edge: a bare startswith(BASE_URL)
    would admit a host that merely begins with the pinned one.
    """
    for url in (
        "https://evil.example/arrow-type-grammar/v1.1.0/grammar.json",
        "https://schemas.analitiq.ai.evil.example/arrow-type-grammar/latest.json",
        "http://schemas.analitiq.ai/arrow-type-grammar/latest.json",
    ):
        with pytest.raises(guard.GuardError, match="refusing non-"):
            guard._fetch(url)
