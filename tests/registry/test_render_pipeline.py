"""Guards over the render pipeline's own machinery.

Three surfaces, each with a regression nothing downstream would catch loudly:

* `buckets()` placement — the multi-scope fan-out, the `any` append, and the
  sub-floor fold are exercised here on synthetic rules, because the committed
  registry cannot be relied on to exhibit every placement shape and the
  byte-sync test can only pin behavior a committed file shows.
* `render_all.py` write mode must never invoke `render_schemas.py write` or
  `render_prose_census.py write` — the one regression every other stage stays
  green through: a hook fire would auto-cut an immutable schema version or
  silently press a census re-affirmation, CI's check would then agree with
  the freshly moved source, and nothing would ever go red.
* `render_hook.py`'s two contracts: the path gate in both directions, and
  exit 2 (not 1) on a generator failure — exit 2 is what makes the harness
  feed the failure back to the session that caused it.
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rule(rid: str, *scopes: str):
    return SimpleNamespace(id=rid, scopes=tuple(scopes))


def _buckets(monkeypatch, rules):
    renderer = _load("render_rule_reference")
    monkeypatch.setattr(renderer, "_load_rules", lambda owner: list(rules))
    monkeypatch.setattr(renderer, "BUCKET_FLOOR", 2)
    return renderer.buckets("connector-plugin")


def test_a_two_scope_rule_lands_in_both_files(monkeypatch):
    filler = [_rule(f"RULE-A-{i:03}", "connector") for i in range(2)]
    filler += [_rule(f"RULE-B-{i:03}", "api-endpoint") for i in range(2)]
    both = _rule("RULE-X-001", "connector", "api-endpoint")
    out = _buckets(monkeypatch, filler + [both])
    assert "RULE-X-001" in {r.id for r in out["connector"]}
    assert "RULE-X-001" in {r.id for r in out["api-endpoint"]}


def test_an_any_rule_lands_in_every_file(monkeypatch):
    rules = [_rule(f"RULE-A-{i:03}", "connector") for i in range(2)]
    rules += [_rule(f"RULE-B-{i:03}", "api-endpoint") for i in range(2)]
    rules.append(_rule("RULE-ANY-001", "any"))
    out = _buckets(monkeypatch, rules)
    for bucket, placed in out.items():
        assert "RULE-ANY-001" in {r.id for r in placed}, bucket


def test_a_sub_floor_scope_folds_into_shared(monkeypatch):
    rules = [_rule(f"RULE-A-{i:03}", "connector") for i in range(2)]
    rules.append(_rule("RULE-S-001", "stream"))
    out = _buckets(monkeypatch, rules)
    assert "stream" not in out
    assert "RULE-S-001" in {r.id for r in out["shared"]}


def test_a_file_never_carries_a_rule_twice(monkeypatch):
    rules = [_rule(f"RULE-A-{i:03}", "connector") for i in range(2)]
    rules += [_rule(f"RULE-B-{i:03}", "api-endpoint") for i in range(2)]
    rules.append(_rule("RULE-X-001", "connector", "api-endpoint"))
    rules.append(_rule("RULE-ANY-001", "any"))
    for bucket, placed in _buckets(monkeypatch, rules).items():
        ids = [r.id for r in placed]
        assert len(ids) == len(set(ids)), f"{bucket}: {ids}"


def test_every_pipeline_entry_names_a_real_script():
    render_all = _load("render_all")
    for script, _write, _check in render_all.PIPELINE:
        assert (REPO_ROOT / "scripts" / script).is_file(), script


def test_write_mode_never_writes_the_judgment_generators(monkeypatch):
    """`render_schemas.py write` cuts an immutable version; census `write`
    presses a re-affirmation. Neither may ever run from the pipeline."""
    render_all = _load("render_all")
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(render_all, "_run",
                        lambda script, args: calls.append((script, tuple(args))) or 0)
    assert render_all.main(["write"]) == 0
    for script in ("render_schemas.py", "render_prose_census.py"):
        modes = [args for s, args in calls if s == script]
        assert modes and all(args == ("check",) for args in modes), (script, modes)


def test_check_mode_runs_every_entry_despite_a_failure(monkeypatch):
    render_all = _load("render_all")
    ran: list[str] = []
    monkeypatch.setattr(render_all, "_run",
                        lambda script, args: ran.append(script) or (1 if script == "render_rules.py" else 0))
    assert render_all.main(["check"]) == 1
    assert ran == [script for script, _w, _c in render_all.PIPELINE]


def _run_hook(monkeypatch, payload, returncode=0):
    hook = _load("render_hook")
    invoked: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        invoked.append(cmd)
        return SimpleNamespace(returncode=returncode, stdout="out", stderr="err")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(payload))
    return hook.main(), invoked


def test_hook_regenerates_on_a_source_edit(monkeypatch):
    payload = json.dumps(
        {"tool_input": {"file_path": str(REPO_ROOT / "rules/records/RULE-CONN-001.yaml")}})
    code, invoked = _run_hook(monkeypatch, payload)
    assert code == 0 and invoked


def test_hook_ignores_a_path_outside_the_repo(monkeypatch):
    payload = json.dumps({"tool_input": {"file_path": "/tmp/elsewhere.md"}})
    code, invoked = _run_hook(monkeypatch, payload)
    assert code == 0 and not invoked


def test_hook_ignores_an_in_repo_path_no_generator_reads(monkeypatch):
    """The prefix branch, distinctly from the outside-the-repo branch: an
    in-repo file outside SOURCE_PREFIXES must not trigger — widening the
    tuple to match everything would otherwise leave this module green."""
    payload = json.dumps({"tool_input": {"file_path": str(REPO_ROOT / "README.md")}})
    code, invoked = _run_hook(monkeypatch, payload)
    assert code == 0 and not invoked


def test_hook_and_pre_commit_gate_on_the_same_prefixes():
    """Both hooks claim their prefix lists are kept identical; this is the
    pin that claim needs. The hook's tuple is the owner; the shell hook's
    `grep -qE` alternation is read back lexically and compared."""
    import re

    hook = _load("render_hook")
    script = (REPO_ROOT / ".githooks" / "pre-commit").read_text()
    match = re.search(r"grep -qE '\^\(([^']+)\)'", script)
    assert match, "pre-commit no longer gates staged paths with grep -qE '^(...)'"
    assert set(match.group(1).split("|")) == set(hook.SOURCE_PREFIXES)


def test_hook_swallows_a_malformed_payload(monkeypatch):
    for payload in ("not json", "null", "[]", '"x"', "{}"):
        code, invoked = _run_hook(monkeypatch, payload)
        assert code == 0 and not invoked, payload


def test_hook_exits_two_on_a_generator_failure(monkeypatch):
    """Exit 2 is the contract: it is what makes the harness feed the failure
    back to the session whose edit caused it. A regression to exit 1 turns the
    finding into a surprise at commit time."""
    payload = json.dumps(
        {"tool_input": {"file_path": str(REPO_ROOT / "packages/contract-models/pyproject.toml")}})
    code, invoked = _run_hook(monkeypatch, payload, returncode=1)
    assert code == 2 and invoked
