#!/usr/bin/env python3
"""Run a plugin scenario end to end and grade what it wrote to disk.

Every other check in this repo grades the *instructions*: that a rule id
resolves, that a fence names a live probe, that a prose enum still matches the
contract. None of them runs an agent, so none of them can tell you whether an
agent reading those instructions authors a correct document. That is the gap
this fills.

An eval is one realistic request, run in an empty directory, graded on the files
that appear. Grading has two halves:

- **The validator**, on every document produced. Objective, and already owned by
  this repo — the eval gets it for free.
- **Assertions**, for what the validator cannot see. That is the interesting
  half: the registry carries obligations no validator applies, and a document
  can satisfy the contract while violating them. Each assertion names the
  `RULE-*` it stands for, so `coverage` can answer which of those obligations no
  eval watches.

The model is not deterministic, so a single run decides nothing. Run each
scenario several times and read the pass rate: a scenario that holds every time
is prose the agent follows, and one that wavers is prose that reads two ways.
Which is the signal — a wavering scenario names the sentence to go fix.

    python3 evals/run_evals.py list
    python3 evals/run_evals.py coverage
    python3 evals/run_evals.py run --runs 5
    python3 evals/run_evals.py run --scenario pipeline-api-to-new-table --keep

This is not part of `pytest`. It takes minutes per run, spends tokens, and some
scenarios reach the network — three things the suite deliberately avoids. Run it
on a schedule or on demand and read it as a dashboard. `--fail-under` turns it
into a gate for a caller that wants one.

## Scenario format

A scenario is one JSON file in `scenarios/`:

    id       — the scenario's name; the filename stem must match.
    plugin   — directory name under `plugins/`, passed as `--plugin-dir`.
    network  — true if the run reaches anything outside the sandbox. `run`
               skips these unless `--network` is passed.
    why      — what this scenario exists to catch, in prose.
    seed     — files copied in before the run, each `{from, to, patch?}`.
               `from` is repo-relative and must be a document some existing gate
               already validates; `patch` sets dotted paths on it. Seeding
               derives fixtures from validated originals rather than growing a
               second copy of a connector nobody re-checks.
    prompt   — the request, written so nothing is left to ask about. A headless
               run cannot answer a clarifying question, so an underspecified
               prompt grades the wrong thing.
    expect   — `build` (documents must appear) or `refusal` (they must not).
    files    — for `refusal`: globs that must match nothing.
    validate — `{glob, entity|schema_url, bundle_root?}` per document family.
    docs     — name → `{glob, where?}`, resolving one document per name for
               assertions. `where` selects by field value where a glob matches
               more than one.
    assert   — `{doc, path, <op>, rule, note?, pluck?}`. Ops: `equals`, `absent`,
               `present`, `matches`, `not_matches`, `one_of`, `min_length`.
               `pluck` reduces a list of objects to one field of each first, so
               a column list can be asserted by name.
    artifacts — `{glob, rule, note?, absent?}` per non-JSON file the run owes.
               A connector's package files are the case: the validator grades
               JSON documents only, so nothing else looks at them at all.
    text_assert — `{file, matches|not_matches, rule, note?}` against the text of
               the one file a glob matches. For what lives in a `.toml`, a
               `requirements.txt` or a `.py`.
    also_covers — rule ids this scenario exercises without a direct assertion,
               counted by `coverage` but never graded. Kept honest by being a
               separate key: nothing here can turn a run green.

`path` is dotted, with `[n]` for list indexes: `destinations[0].write.mode`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
PIPELINE_VALIDATE = REPO_ROOT / "plugins" / "analitiq-pipeline-builder" / "scripts" / "validate.py"
RULE_RECORDS = REPO_ROOT / "rules" / "records"

# The agent under test installs the pinned published validator. The eval grades
# with the in-repo source instead, the same choice `conftest.py` makes for the
# suite: a repo-side check answers "does the code in this checkout accept it".
# Where the two disagree, the pin is behind, which the pin guards already cover.
SOURCE_ENV = {
    "ANALITIQ_VALIDATOR_FROM_SOURCE": "1",
    "DOMAIN": "analitiq.ai",
    "PYTHONPATH": os.pathsep.join([
        str(REPO_ROOT / "packages" / "contract-models" / "src"),
        str(REPO_ROOT / "packages" / "validator" / "src"),
    ]),
}

_INDEX_RE = re.compile(r"^(?P<key>[^\[]*)(?P<idx>(?:\[\d+\])*)$")

# An assertion's `rule` is a registry id, or this: the run did what the prompt
# asked. Choosing `upsert` because the user said upsert is not an obligation any
# record carries, and filing it under the rule that governs the resulting shape
# would report registry coverage the assertion does not provide.
INTENT = "intent"


class Missing:
    """Distinct from None, which is a value a document can legitimately hold."""

    def __repr__(self) -> str:
        return "<missing>"


MISSING = Missing()


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------

def load_scenarios() -> list[dict]:
    found = []
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        scenario = json.loads(path.read_text(encoding="utf-8"))
        if scenario.get("id") != path.stem:
            raise SystemExit(f"{path.name}: id {scenario.get('id')!r} does not match the filename")
        scenario["_path"] = path
        found.append(scenario)
    if not found:
        raise SystemExit(f"no scenarios in {SCENARIO_DIR.relative_to(REPO_ROOT)} — nothing to run")
    return found


# ---------------------------------------------------------------------------
# Paths into a loaded document
# ---------------------------------------------------------------------------

def dig(doc, path: str):
    """Resolve a dotted path with `[n]` indexes, or MISSING."""
    node = doc
    for part in path.split("."):
        match = _INDEX_RE.match(part)
        if not match:
            raise SystemExit(f"unparseable assertion path: {path!r}")
        key = match.group("key")
        if key:
            if not isinstance(node, dict) or key not in node:
                return MISSING
            node = node[key]
        for index in re.findall(r"\[(\d+)\]", match.group("idx")):
            if not isinstance(node, list) or int(index) >= len(node):
                return MISSING
            node = node[int(index)]
    return node


OPS = {
    "equals": lambda got, want: got == want,
    "absent": lambda got, want: (got is MISSING) is bool(want),
    "present": lambda got, want: (got is not MISSING) is bool(want),
    "matches": lambda got, want: isinstance(got, str) and re.search(want, got) is not None,
    "not_matches": lambda got, want: not (isinstance(got, str) and re.search(want, got)),
    "one_of": lambda got, want: got in want,
    "min_length": lambda got, want: hasattr(got, "__len__") and len(got) >= want,
}


# ---------------------------------------------------------------------------
# Running one scenario
# ---------------------------------------------------------------------------

def seed(scenario: dict, workdir: Path) -> None:
    for item in scenario.get("seed", []):
        src = REPO_ROOT / item["from"]
        if not src.is_file():
            raise SystemExit(f"{scenario['id']}: seed source {item['from']} does not exist")
        dst = workdir / item["to"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not item.get("patch"):
            shutil.copyfile(src, dst)
            continue
        doc = json.loads(src.read_text(encoding="utf-8"))
        for path, value in item["patch"].items():
            *parents, leaf = path.split(".")
            node = doc
            for key in parents:
                node = node[key]
            node[leaf] = value
        dst.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def invoke(scenario: dict, workdir: Path, timeout: int) -> tuple[bool, str]:
    plugin_dir = REPO_ROOT / "plugins" / scenario["plugin"]
    if not plugin_dir.is_dir():
        raise SystemExit(f"{scenario['id']}: no plugin at {plugin_dir}")
    proc = subprocess.run(
        ["claude", "-p", "--plugin-dir", str(plugin_dir),
         "--permission-mode", "bypassPermissions", scenario["prompt"]],
        cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=timeout, env={**os.environ, **SOURCE_ENV},
    )
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def run_validator(workdir: Path, spec: dict) -> list[str]:
    """Validate every document a spec's glob matches. Returns failure lines."""
    failures = []
    matches = sorted(workdir.glob(spec["glob"]))
    if not matches:
        return [f"{spec['glob']}: no document was written"]
    for doc in matches:
        rel = doc.relative_to(workdir).as_posix()
        if "entity" in spec:
            cmd = [sys.executable, str(PIPELINE_VALIDATE),
                   "--entity", spec["entity"], "--document", str(doc)]
            if spec.get("bundle_root"):
                cmd += ["--bundle-root", str(workdir / spec["bundle_root"])]
        else:
            cmd = [sys.executable, "-c",
                   "import sys;from analitiq.validator import main;"
                   "sys.argv=['analitiq-validate','--schema-url',sys.argv[1],"
                   "'--document',sys.argv[2]];sys.exit(main())",
                   spec["schema_url"], str(doc)]
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                              env={**os.environ, **SOURCE_ENV})
        if proc.returncode != 0:
            failures.append(f"{rel}: {(proc.stdout or proc.stderr).strip()[:400]}")
    return failures


def resolve_docs(scenario: dict, workdir: Path) -> tuple[dict, list[str]]:
    docs, problems = {}, []
    for name, spec in scenario.get("docs", {}).items():
        candidates = []
        for path in sorted(workdir.glob(spec["glob"])):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                problems.append(f"{name}: {path.name} is not JSON ({exc})")
                continue
            where = spec.get("where") or {}
            if all(loaded.get(k) == v for k, v in where.items()):
                candidates.append(loaded)
        if len(candidates) != 1:
            problems.append(
                f"{name}: {spec['glob']} resolved {len(candidates)} documents, wanted exactly one")
            continue
        docs[name] = candidates[0]
    return docs, problems


def check_assertions(scenario: dict, docs: dict) -> list[str]:
    failures = []
    for item in scenario.get("assert", []):
        if item["doc"] not in docs:
            failures.append(f"{item['rule']}: document {item['doc']!r} was not resolved")
            continue
        op = next((k for k in OPS if k in item), None)
        if op is None:
            raise SystemExit(f"{scenario['id']}: assertion on {item['path']} names no operator")
        got = dig(docs[item["doc"]], item["path"])
        if item.get("pluck") and isinstance(got, list):
            got = [entry.get(item["pluck"], MISSING) if isinstance(entry, dict) else MISSING
                   for entry in got]
        if not OPS[op](got, item[op]):
            failures.append(
                f"{item['rule']}: {item['doc']}.{item['path']} {op}={item[op]!r}, got {got!r}"
                + (f" — {item['note']}" if item.get("note") else ""))
    return failures


def check_artifacts(scenario: dict, workdir: Path) -> list[str]:
    """Files the run owes that no validator would ever look at."""
    failures = []
    for item in scenario.get("artifacts", []):
        matched = sorted(workdir.glob(item["glob"]))
        if item.get("absent"):
            if matched:
                failures.append(
                    f"{item['rule']}: {item['glob']} should not exist, found "
                    f"{[p.relative_to(workdir).as_posix() for p in matched]}"
                    + (f" — {item['note']}" if item.get("note") else ""))
        elif not matched:
            failures.append(f"{item['rule']}: nothing matched {item['glob']}"
                            + (f" — {item['note']}" if item.get("note") else ""))
    return failures


def check_text(scenario: dict, workdir: Path) -> list[str]:
    failures = []
    for item in scenario.get("text_assert", []):
        matched = sorted(workdir.glob(item["file"]))
        if len(matched) != 1:
            failures.append(
                f"{item['rule']}: {item['file']} matched {len(matched)} files, wanted exactly one")
            continue
        text = matched[0].read_text(encoding="utf-8", errors="replace")
        op = "matches" if "matches" in item else "not_matches"
        if not OPS[op](text, item[op]):
            failures.append(f"{item['rule']}: {item['file']} {op}={item[op]!r} failed"
                            + (f" — {item['note']}" if item.get("note") else ""))
    return failures


def one_run(scenario: dict, keep: bool, timeout: int) -> list[str]:
    """Every way this run fell short. Empty means it passed."""
    workdir = Path(tempfile.mkdtemp(prefix=f"eval-{scenario['id']}-"))
    try:
        seed(scenario, workdir)
        ok, output = invoke(scenario, workdir, timeout)
        if not ok:
            return [f"the session exited non-zero: {output.strip()[-400:]}"]

        if scenario["expect"] == "refusal":
            wrote = [p.relative_to(workdir).as_posix()
                     for glob in scenario.get("files", []) for p in workdir.glob(glob)]
            return [f"expected a refusal; it authored {wrote}"] if wrote else []

        failures = []
        for spec in scenario.get("validate", []):
            failures += run_validator(workdir, spec)
        docs, problems = resolve_docs(scenario, workdir)
        failures += problems
        failures += check_assertions(scenario, docs)
        failures += check_artifacts(scenario, workdir)
        failures += check_text(scenario, workdir)
        return failures
    finally:
        if keep:
            print(f"    kept {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def unenforced_rules() -> set[str]:
    """Rule ids whose record names no validator — nothing rejects a violation."""
    ids = set()
    for path in sorted(RULE_RECORDS.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^validator:(.*)$", text, re.M)
        if match and match.group(1).strip() in ("null", "~", ""):
            ids.add(path.stem)
    if not ids:
        raise SystemExit(
            f"no unenforced rule found in {RULE_RECORDS.relative_to(REPO_ROOT)} — the scan has "
            "stopped measuring rather than found full coverage")
    return ids


def asserted_rules(scenarios: list[dict]) -> Counter:
    counted: Counter = Counter()
    for scenario in scenarios:
        for key in ("assert", "artifacts", "text_assert"):
            for item in scenario.get(key, []):
                if item["rule"] != INTENT:
                    counted[item["rule"]] += 1
        for rule in scenario.get("also_covers", []):
            counted[rule] += 0
    return counted


def cmd_coverage(scenarios: list[dict]) -> int:
    unenforced = unenforced_rules()
    asserted = asserted_rules(scenarios)

    # Three states, kept apart on purpose. A rule a scenario merely exercises is
    # not a rule a scenario grades: the run touches it and would stay green
    # while it was violated. Folding the two together would report coverage this
    # harness does not have.
    graded = sorted(r for r in unenforced if asserted.get(r, 0) > 0)
    exercised = sorted(r for r in unenforced if asserted.get(r) == 0)
    untouched = sorted(unenforced - set(asserted))
    redundant = sorted(set(asserted) - unenforced)

    print(f"unenforced rules: {len(unenforced)}")
    print(f"\ngraded — a violation fails a run ({len(graded)}):")
    for rule in graded:
        print(f"  graded    {rule}  ({asserted[rule]} assertion(s))")
    print(f"\nexercised but not graded — the run touches it and stays green anyway "
          f"({len(exercised)}):")
    for rule in exercised:
        print(f"  exercised {rule}")
    print(f"\nuntouched — no scenario reaches it ({len(untouched)}):")
    for rule in untouched:
        print(f"  untouched {rule}")
    if redundant:
        print(f"\nasserted, but the registry names a validator for it — the eval duplicates a "
              f"check that already fails loudly ({len(redundant)}):")
        for rule in redundant:
            print(f"  redundant {rule}")
    return 0


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def cmd_list(scenarios: list[dict]) -> int:
    for scenario in scenarios:
        reach = "network" if scenario.get("network") else "offline"
        print(f"{scenario['id']}  [{scenario['plugin']}, {reach}, expect {scenario['expect']}]")
        print(f"    {scenario['why']}")
    return 0


def cmd_run(scenarios: list[dict], args) -> int:
    selected = [s for s in scenarios if args.scenario in (None, s["id"])]
    if args.scenario and not selected:
        raise SystemExit(f"no scenario named {args.scenario!r}")
    if not args.network:
        skipped = [s["id"] for s in selected if s.get("network")]
        selected = [s for s in selected if not s.get("network")]
        for name in skipped:
            print(f"{name}: skipped, reaches the network (pass --network to include it)")
    if not selected:
        raise SystemExit("every selected scenario was skipped — nothing ran")

    worst = 1.0
    for scenario in selected:
        passed = 0
        print(f"\n{scenario['id']} ({args.runs} run(s))")
        for attempt in range(1, args.runs + 1):
            failures = one_run(scenario, args.keep, args.timeout)
            if failures:
                print(f"  run {attempt}: FAIL")
                for line in failures:
                    print(f"    - {line}")
            else:
                passed += 1
                print(f"  run {attempt}: pass")
        rate = passed / args.runs
        worst = min(worst, rate)
        print(f"  {passed}/{args.runs} passed")

    if args.fail_under is not None and worst < args.fail_under:
        print(f"\nworst pass rate {worst:.2f} is under --fail-under {args.fail_under}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show every scenario and what it is for")
    sub.add_parser("coverage", help="which unenforced rules no eval watches")
    run = sub.add_parser("run", help="run scenarios and report pass rates")
    run.add_argument("--scenario", help="run just this one")
    run.add_argument("--runs", type=int, default=3, help="repetitions per scenario (default 3)")
    run.add_argument("--network", action="store_true", help="include scenarios that reach the network")
    run.add_argument("--keep", action="store_true", help="leave each working directory on disk")
    run.add_argument("--timeout", type=int, default=1800, help="seconds per run (default 1800)")
    run.add_argument("--fail-under", type=float, help="exit 1 if any pass rate falls below this")
    args = parser.parse_args(argv[1:])

    scenarios = load_scenarios()
    if args.command == "list":
        return cmd_list(scenarios)
    if args.command == "coverage":
        return cmd_coverage(scenarios)
    return cmd_run(scenarios, args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
