#!/usr/bin/env python3
"""Run a plugin scenario end to end and grade what it wrote to disk.

Every other check in this repo grades the *instructions*: that a rule id
resolves, that a fence names a live probe, that a prose enum still matches the
contract. None of them runs an agent, so none of them can tell you whether an
agent reading those instructions authors a correct document. That is the gap
this fills.

An eval is one realistic request, run in a directory holding only what the
scenario seeds, graded on the files that appear. Grading has two halves:

- **The validator**, on every document produced. Objective, and already owned by
  this repo — the eval gets it for free.
- **Assertions**, for what the validator cannot see. That is the interesting
  half: the registry carries obligations no validator applies, and a document
  can satisfy the contract while violating them. Each assertion names the
  `RULE-*` it stands for, or `intent` where it grades that the run did what the
  prompt asked rather than that it satisfied an obligation, so `coverage` can
  answer which registry obligations no eval watches.

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

A scenario is one JSON file in `scenarios/`. `load_scenarios` rejects a file
that breaks any of this, before a single agent runs.

    id       — the scenario's name; the filename stem must match.
    plugin   — directory name under `plugins/`, passed as `--plugin-dir`.
    network  — true if the run reaches anything outside the sandbox. `run`
               skips these unless `--network` is passed.
    why      — what this scenario exists to catch, in prose.
    seed     — files copied in before the run, each `{from, to, patch?}`.
               `from` is repo-relative and must be a document some existing gate
               already validates; `patch` replaces dotted paths on it, and a
               path the source does not already carry is an error rather than an
               insertion. Seeding derives fixtures from validated originals
               rather than growing a second copy of a connector nobody
               re-checks.
    prompt   — the request, written so nothing is left to ask about. A headless
               run cannot answer a clarifying question, so an underspecified
               prompt grades the wrong thing.
    expect   — `build` (documents must appear) or `refusal` (they must not).
    files    — for `refusal`: globs that must match nothing.
    cites    — for `refusal`: rule ids the session's own output must name.
               Absence of a file is only negative evidence; a session that
               crashed, hit a turn limit or never loaded the plugin also writes
               nothing. This is the positive half, and it stays a *locating*
               check in the sense `.claude/rules/guards.md` requires: the id is
               resolved against the registry, and no verdict here depends on
               what the surrounding sentence means.
    validate — `{glob, entity|schema_url, bundle_root?}` per document family.
               `entity` selects the contract to grade against. `schema_url` does
               NOT: the validator detects a document's kind from its own shape
               and takes the URL only as a read/write hint for an ambiguously
               named type-map, so a scenario wanting the family pinned asserts a
               discriminating field itself.
    docs     — name → `{glob, where?}`, resolving one document per name for
               assertions. `where` selects by top-level field value where a glob
               matches more than one.
    assert   — `{doc, path, <op>, rule, note?, pluck?}`. Ops: `equals`, `absent`,
               `present`, `matches`, `not_matches`, `one_of`, `min_length`.
               `pluck` reduces a list of objects to one field of each first, so
               a column list can be asserted by name. `path` is dotted with
               `[n]` for list indexes: `destinations[0].write.mode`.
    artifacts — `{glob, rule, note?, absent?}` per non-JSON file the run owes, or
               must not produce. A connector's package files are the case: the
               validator grades JSON documents only. Presence is all this
               grades, so a rule whose obligation is about a file's *content*
               belongs in `text_assert` or in `also_covers`, not here.
    text_assert — `{file, matches|not_matches, rule, note?}` against the text of
               the one file a glob matches. For what lives in a `.toml`, a
               `requirements.txt` or a `.py`.
    also_covers — rule ids this scenario exercises without grading, counted by
               `coverage` as exercised and never able to turn a run green. An id
               here may not also be asserted: the two buckets mean different
               things and an id in both would report the weaker one as the
               stronger.
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
import time
from collections import Counter
from pathlib import Path

# Stdout is block-buffered when it is not a terminal, so a run redirected to a
# file or a pipe shows nothing until the process exits — and a job killed part
# way through shows nothing at all, however far it got. Line buffering here
# rather than `flush=True` per call or `-u` at the call site: one place, and it
# holds however the script is invoked.
sys.stdout.reconfigure(line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
PIPELINE_VALIDATE = REPO_ROOT / "plugins" / "analitiq-pipeline-builder" / "scripts" / "validate.py"
RULE_RECORDS = REPO_ROOT / "rules" / "records"

# The grader's environment, and only the grader's. It puts the in-repo contract
# models and validator on the path, the same choice `conftest.py` makes for the
# suite: a repo-side check answers "does the code in this checkout accept it".
#
# It must never reach the agent. `_bootstrap.py` short-circuits on
# `ANALITIQ_VALIDATOR_FROM_SOURCE`, and the connector plugin's self-install is
# guarded by an import probe that `PYTHONPATH` would satisfy — so an agent
# handed this environment validates its own work against in-repo source instead
# of the release its users run. That is the one thing a plugin eval must not do:
# the gap between what this repo ships and what `VALIDATOR_PIN` names is a real
# failure mode, and leaking these variables hides it.
GRADER_ENV = {
    "ANALITIQ_VALIDATOR_FROM_SOURCE": "1",
    "DOMAIN": os.environ.get("DOMAIN", "analitiq.ai"),
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

SCENARIO_KEYS = {
    "id", "plugin", "network", "why", "seed", "prompt", "expect", "files", "cites",
    "validate", "docs", "assert", "artifacts", "text_assert", "also_covers",
}


class Sentinel:
    def __init__(self, label: str) -> None:
        self.label = label

    def __repr__(self) -> str:
        return self.label


# The leaf is not there, but everything above it was. This is the only outcome
# an `absent` assertion may be satisfied by.
MISSING = Sentinel("<missing>")

# The path could not be walked: an intermediate key was absent, or a segment hit
# a value of the wrong shape (an index into a mapping, a key into a list).
#
# Kept apart from MISSING because collapsing them is how a negative assertion
# stops measuring. `absent: true` on `destinations[0].execution` is meant to say
# "that destination declares no execution override". If `destinations` is
# renamed, or authored as a mapping, the walk fails at the first segment — and a
# single sentinel would report the same MISSING, so the assertion passes forever
# while the thing it names no longer exists to be absent from.
UNREACHABLE = Sentinel("<unreachable>")


# ---------------------------------------------------------------------------
# Paths into a loaded document
# ---------------------------------------------------------------------------

def dig(doc, path: str):
    """Resolve a dotted path with `[n]` indexes.

    Returns the value, MISSING when the final segment alone is absent, or
    UNREACHABLE when the path could not be walked that far. Only the last
    segment may come back MISSING; anything failing earlier is a path that no
    longer describes this document, which is a different fact and a different
    verdict.
    """
    parts = path.split(".")
    node = doc
    for position, part in enumerate(parts):
        last = position == len(parts) - 1
        match = _INDEX_RE.match(part)
        if not match:
            raise ValueError(f"unparseable assertion path: {path!r}")
        key = match.group("key")
        indexes = re.findall(r"\[(\d+)\]", match.group("idx"))
        if key:
            if not isinstance(node, dict):
                return UNREACHABLE
            if key not in node:
                return MISSING if last and not indexes else UNREACHABLE
            node = node[key]
        for offset, index in enumerate(indexes):
            if not isinstance(node, list):
                return UNREACHABLE
            if int(index) >= len(node):
                return MISSING if last and offset == len(indexes) - 1 else UNREACHABLE
            node = node[int(index)]
    return node


def _matches(got, want) -> bool:
    return isinstance(got, str) and re.search(want, got) is not None


def _compiles(want) -> bool:
    try:
        re.compile(want)
    except (re.error, TypeError):
        return False
    return True


OPS = {
    "equals": lambda got, want: got == want and type(got) is type(want),
    "absent": lambda got, want: (got is MISSING) is bool(want),
    "present": lambda got, want: (got is not MISSING and got is not UNREACHABLE) is bool(want),
    "matches": _matches,
    # Requires a string to negate. `not _matches(...)` would be satisfied by an
    # absent field, an integer or a list — a negative assertion passing because
    # there was nothing to read is the failure this file exists to catch, so the
    # wrong type is a failure rather than a pass.
    "not_matches": lambda got, want: isinstance(got, str) and not _matches(got, want),
    # Membership, never substring: `one_of` given the string "insert" would
    # otherwise accept "in".
    "one_of": lambda got, want: isinstance(want, list) and got in want,
    # The length of a sequence the contract declares as one. A mapping and a
    # string both have a length, and neither is the array being asked about.
    "min_length": lambda got, want: isinstance(got, (list, tuple)) and len(got) >= want,
}

# What a scenario may write for each operator, checked before any run starts so
# a JSON slip fails in a second rather than hours in — or, worse, grades wrongly.
OP_ARGUMENT = {
    "equals": lambda want: True,
    "absent": lambda want: isinstance(want, bool),
    "present": lambda want: isinstance(want, bool),
    "matches": _compiles,
    "not_matches": _compiles,
    "one_of": lambda want: isinstance(want, list) and bool(want),
    "min_length": lambda want: isinstance(want, int) and not isinstance(want, bool),
}


# ---------------------------------------------------------------------------
# The rule registry
# ---------------------------------------------------------------------------

def unenforced_rules() -> set[str]:
    """Rule ids whose record names no validator — nothing rejects a violation.

    Loaded through the contract package's own registry rather than read out of
    the YAML with a regex. `validator` is optional, so a record omitting the key
    and a record writing `null` mean the same thing and a text scan sees two
    different shapes; a trailing comment or a folded value makes a third. The
    registry already types the field, and a coverage denominator that quietly
    loses rules reports coverage nobody has.
    """
    sys.path[:0] = [p for p in GRADER_ENV["PYTHONPATH"].split(os.pathsep) if p not in sys.path]
    os.environ.setdefault("DOMAIN", GRADER_ENV["DOMAIN"])
    from analitiq.contracts.shared.rules import all_rules

    ids = {rule.id for rule in all_rules() if not rule.validator}
    if not ids:
        raise SystemExit(
            "no unenforced rule in the registry — the scan has stopped measuring rather than "
            "found full coverage")
    return ids


def known_rule_ids() -> set[str]:
    return {p.stem for p in RULE_RECORDS.glob("*.yaml")}


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------

def _scenario_problems(scenario: dict, path: Path) -> list[str]:
    """Everything wrong with a scenario, as a list so one pass reports them all.

    A build scenario that grades nothing is the headline case. Every grading key
    is optional in the code that reads it, so a mistyped `artifacts` or a
    dropped `validate` silently deletes a whole half of the grading and the run
    reports pass. That must be impossible to write, not merely discouraged.
    """
    problems = []
    if scenario.get("id") != path.stem:
        problems.append(f"id {scenario.get('id')!r} does not match the filename")
    unknown = sorted(set(scenario) - SCENARIO_KEYS)
    if unknown:
        problems.append(f"unknown key(s) {unknown} — the runner reads none of these, so anything "
                        f"written under them grades nothing")
    expect = scenario.get("expect")
    if expect not in ("build", "refusal"):
        problems.append(f"expect must be 'build' or 'refusal', not {expect!r}")

    if expect == "refusal":
        if not scenario.get("files"):
            problems.append("a refusal names the globs that must stay empty")
        if not scenario.get("cites"):
            problems.append("a refusal names the rule ids its output must cite — without them a "
                            "session that crashed is indistinguishable from one that declined")
        for key in ("assert", "artifacts", "text_assert", "validate", "docs"):
            if scenario.get(key):
                problems.append(f"a refusal writes nothing, so {key!r} cannot grade it")
    elif expect == "build":
        if not scenario.get("validate"):
            problems.append("a build scenario carries a validate block — the validator half of "
                            "the grading is not optional")
        if not scenario.get("assert"):
            problems.append("a build scenario that asserts nothing grades nothing")
        if scenario.get("cites"):
            problems.append("'cites' grades a refusal's output; a build scenario grades files")

    graded = {item["rule"] for key in ("assert", "artifacts", "text_assert")
              for item in scenario.get(key, []) if "rule" in item}
    overlap = sorted(graded & set(scenario.get("also_covers", [])))
    if overlap:
        problems.append(f"{overlap} are both graded and listed in also_covers; an id belongs to "
                        f"exactly one bucket or coverage reports the weaker as the stronger")
    if INTENT in scenario.get("also_covers", []):
        problems.append("also_covers holds registry ids; 'intent' is not one")

    known = known_rule_ids()
    for key in ("assert", "artifacts", "text_assert"):
        for item in scenario.get(key, []):
            rule = item.get("rule")
            if rule != INTENT and rule not in known:
                problems.append(f"{key} entry cites unknown rule {rule!r}")
    for rule in scenario.get("also_covers", []):
        if rule not in known:
            problems.append(f"also_covers cites unknown rule {rule!r}")
    for rule in scenario.get("cites", []):
        if rule not in known:
            problems.append(f"cites names unknown rule {rule!r}")

    for item in scenario.get("assert", []):
        named = [op for op in OPS if op in item]
        if len(named) != 1:
            problems.append(f"assertion on {item.get('path')!r} names operators {named}")
            continue
        op = named[0]
        if not OP_ARGUMENT[op](item[op]):
            problems.append(f"assertion on {item.get('path')!r}: {op}={item[op]!r} is not a valid "
                            f"argument for {op}")
        if item.get("doc") not in scenario.get("docs", {}):
            problems.append(f"assertion on {item.get('path')!r} reads document "
                            f"{item.get('doc')!r}, which `docs` does not resolve")
    for item in scenario.get("text_assert", []):
        named = [op for op in ("matches", "not_matches") if op in item]
        if len(named) != 1:
            problems.append(f"text_assert on {item.get('file')!r} names operators {named}")
        elif not _compiles(item[named[0]]):
            problems.append(f"text_assert on {item.get('file')!r}: {item[named[0]]!r} does not "
                            f"compile as a regex")
    for spec in scenario.get("validate", []):
        selectors = [k for k in ("entity", "schema_url") if k in spec]
        if len(selectors) != 1:
            problems.append(f"validate spec {spec.get('glob')!r} names {selectors}; exactly one of "
                            f"entity / schema_url selects how a document is graded")
    for item in scenario.get("seed", []):
        if not (REPO_ROOT / item["from"]).is_file():
            problems.append(f"seed source {item['from']} does not exist")
        target = Path(item["to"])
        if target.is_absolute() or ".." in target.parts:
            problems.append(f"seed target escapes the working directory: {item['to']}")
    return problems


def load_scenarios() -> list[dict]:
    found, broken = [], []
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        scenario = json.loads(path.read_text(encoding="utf-8"))
        problems = _scenario_problems(scenario, path)
        if problems:
            broken += [f"{path.name}: {p}" for p in problems]
            continue
        found.append(scenario)
    if broken:
        raise SystemExit("\n".join(["unusable scenario(s):", *(f"  {b}" for b in broken)]))
    if not found:
        raise SystemExit(f"no scenarios in {SCENARIO_DIR.relative_to(REPO_ROOT)} — nothing to run")
    return found


# ---------------------------------------------------------------------------
# Running one scenario
# ---------------------------------------------------------------------------

def seed(scenario: dict, workdir: Path) -> None:
    for item in scenario.get("seed", []):
        src = REPO_ROOT / item["from"]
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
                if not isinstance(node, dict) or key not in node:
                    raise SystemExit(f"{scenario['id']}: patch path {path!r} does not resolve in "
                                     f"{item['from']}")
                node = node[key]
            # Replacement only. A patch that inserts is a patch whose field was
            # renamed under it: the seeded document then carries the stale value
            # too, and the scenario grades a fixture the contract no longer
            # recognises.
            if not isinstance(node, dict) or leaf not in node:
                raise SystemExit(f"{scenario['id']}: patch path {path!r} is not a field of "
                                 f"{item['from']} — a patch replaces, it does not add")
            node[leaf] = value
        dst.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def invoke(scenario: dict, workdir: Path, timeout: int) -> tuple[bool, str]:
    """Run the agent. Returns (the process exited cleanly, its output).

    The environment is the caller's, deliberately untouched: the agent must
    resolve the validator its users would resolve. See GRADER_ENV.
    """
    plugin_dir = REPO_ROOT / "plugins" / scenario["plugin"]
    if not plugin_dir.is_dir():
        raise SystemExit(f"{scenario['id']}: no plugin at {plugin_dir}")
    try:
        proc = subprocess.run(
            ["claude", "-p", "--plugin-dir", str(plugin_dir),
             "--permission-mode", "bypassPermissions", scenario["prompt"]],
            cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = "".join(part.decode(errors="replace") if isinstance(part, bytes) else (part or "")
                          for part in (exc.stdout, exc.stderr))
        return False, f"the session did not finish within {timeout}s\n{partial[-2000:]}"
    except OSError as exc:
        return False, f"could not start the session: {exc}"
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def run_validator(workdir: Path, spec: dict, timeout: int) -> list[str]:
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
        try:
            proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                                  timeout=timeout, env={**os.environ, **GRADER_ENV})
        except subprocess.TimeoutExpired:
            failures.append(f"{rel}: the validator did not finish within {timeout}s")
            continue
        if proc.returncode != 0:
            # Both stdout and stderr: the diagnostics land on stdout and a crash
            # lands on stderr, and reading only the first hides the second.
            # Generous truncation — this line is what `record` writes to the
            # file the results are read from, so a dropped finding is dropped
            # for good.
            detail = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
            failures.append(f"{rel}: {detail[:4000]}")
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
        op = next(op for op in OPS if op in item)
        got = dig(docs[item["doc"]], item["path"])
        if item.get("pluck"):
            if not isinstance(got, list):
                failures.append(f"{item['rule']}: {item['doc']}.{item['path']} is {got!r}, which "
                                f"has no {item['pluck']!r} to pluck")
                continue
            got = [entry.get(item["pluck"], MISSING) if isinstance(entry, dict) else MISSING
                   for entry in got]
        if not OPS[op](got, item[op]):
            failures.append(
                f"{item['rule']}: {item['doc']}.{item['path']} {op}={item[op]!r}, got {got!r}"
                + (f" — {item['note']}" if item.get("note") else ""))
    return failures


def check_artifacts(scenario: dict, workdir: Path) -> list[str]:
    """Files the run owes, or must not produce, that no validator reads."""
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


def check_refusal(scenario: dict, workdir: Path, output: str) -> list[str]:
    """A refusal is graded on both halves: nothing written, and a reason given.

    Absence of files is negative evidence only — a session that hit a turn
    limit, failed to load the plugin, or asked a question and stopped writes
    nothing either, and would otherwise be recorded as a correct decline. The
    positive half is that the session's own output names the rule it declined
    under. That is a token lookup, not a reading of what the sentence means.
    """
    failures = []
    wrote = [p.relative_to(workdir).as_posix()
             for glob in scenario.get("files", []) for p in workdir.glob(glob)]
    if wrote:
        failures.append(f"expected a refusal; it authored {wrote}")
    for rule in scenario["cites"]:
        if rule not in output:
            failures.append(f"{rule}: the session never named this rule, so nothing shows it "
                            f"declined rather than failed to run")
    return failures


def discard(workdir: Path) -> str | None:
    """Remove a run's working directory, reporting what stopped it.

    Not `ignore_errors=True`. A run leaves a whole seeded pipeline tree behind,
    and a job that fails to clean one up per run fills the disk while reporting
    nothing. A cleanup that cannot happen is a result, so it travels with the
    run's other failures rather than to a stream a nightly may not keep.
    """
    try:
        shutil.rmtree(workdir)
    except OSError as exc:
        return f"could not remove {workdir}: {exc}"
    return None


def one_run(scenario: dict, keep: bool, timeout: int) -> list[str]:
    """Every way this run fell short. Empty means it passed."""
    workdir = Path(tempfile.mkdtemp(prefix=f"eval-{scenario['id']}-"))
    failures: list[str] = []
    try:
        seed(scenario, workdir)
        ok, output = invoke(scenario, workdir, timeout)
        if not ok:
            failures.append(f"the session did not complete: {output.strip()[-1000:]}")
        elif scenario["expect"] == "refusal":
            failures += check_refusal(scenario, workdir, output)
        else:
            for spec in scenario.get("validate", []):
                failures += run_validator(workdir, spec, timeout)
            docs, problems = resolve_docs(scenario, workdir)
            failures += problems
            failures += check_assertions(scenario, docs)
            failures += check_artifacts(scenario, workdir)
            failures += check_text(scenario, workdir)
    except Exception as exc:  # noqa: BLE001 - a broken run is a result, not a crash
        # Anything raised here — a malformed path, an unreadable seed, a bug in
        # this file — belongs in the record. Letting it propagate loses this run
        # AND every scenario queued behind it, which is the opposite of what the
        # results log exists for.
        failures.append(f"the harness raised while grading: {type(exc).__name__}: {exc}")
    finally:
        if keep:
            print(f"    kept {workdir}")
        else:
            left = discard(workdir)
            if left:
                failures.append(left)
    return failures


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def asserted_rules(scenarios: list[dict]) -> Counter:
    """Registry ids a scenario grades, and (at zero) ids it merely exercises."""
    counted: Counter = Counter()
    for scenario in scenarios:
        for key in ("assert", "artifacts", "text_assert"):
            for item in scenario.get(key, []):
                if item["rule"] != INTENT:
                    counted[item["rule"]] += 1
        # A refusal grades its `cites` ids: a run that authored the connector,
        # or never explained itself, fails.
        for rule in scenario.get("cites", []):
            counted[rule] += 1
        for rule in scenario.get("also_covers", []):
            counted[rule] += 0
    return counted


def cmd_coverage(scenarios: list[dict]) -> int:
    unenforced = unenforced_rules()
    asserted = asserted_rules(scenarios)

    # Four buckets, kept apart on purpose. A rule a scenario merely exercises is
    # not a rule a scenario grades: the run touches it and would stay green
    # while it was violated. Folding those together would report coverage this
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


def record(path: Path, entry: dict) -> None:
    """Append one finished run to the results log.

    A kill costs the in-flight run only: every run that finished is already on
    disk, partial results are readable while the job is still going, and rates
    can be counted across invocations rather than only within one. Flushing
    stdout keeps a watcher informed; it does not keep a result.
    """
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, sort_keys=True) + "\n")
        log.flush()
        os.fsync(log.fileno())


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

    results = args.results.resolve()
    results.parent.mkdir(parents=True, exist_ok=True)
    print(f"appending each finished run to {results}")

    worst = 1.0
    for scenario in selected:
        passed = 0
        print(f"\n{scenario['id']} ({args.runs} run(s))")
        for attempt in range(1, args.runs + 1):
            started = time.time()
            failures = one_run(scenario, args.keep, args.timeout)
            record(results, {
                "scenario": scenario["id"],
                "plugin": scenario["plugin"],
                "run": attempt,
                "of": args.runs,
                "passed": not failures,
                "failures": failures,
                "seconds": round(time.time() - started, 1),
            })
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
        print(f"\nworst pass rate {worst:.2f} is under --fail-under {args.fail_under}",
              file=sys.stderr)
        return 1
    return 0


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show every scenario and what it is for")
    sub.add_parser("coverage", help="which unenforced rules no eval watches")
    run = sub.add_parser("run", help="run scenarios and report pass rates")
    run.add_argument("--scenario", help="run just this one")
    run.add_argument("--runs", type=_positive, default=3, help="repetitions per scenario (default 3)")
    run.add_argument("--network", action="store_true",
                     help="include scenarios that reach the network")
    run.add_argument("--keep", action="store_true", help="leave each working directory on disk")
    run.add_argument("--timeout", type=_positive, default=1800,
                     help="seconds allowed per agent session and per validator call (default 1800)")
    run.add_argument("--fail-under", type=float, help="exit 1 if any pass rate falls below this")
    run.add_argument("--results", type=Path, default=Path("eval-results.jsonl"),
                     help="file each finished run is appended to as JSON, one line per run "
                          "(default: ./eval-results.jsonl). Appended, never truncated, so rates "
                          "can be counted across invocations.")
    args = parser.parse_args(argv[1:])

    scenarios = load_scenarios()
    if args.command == "list":
        return cmd_list(scenarios)
    if args.command == "coverage":
        return cmd_coverage(scenarios)
    return cmd_run(scenarios, args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
