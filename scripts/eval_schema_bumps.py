#!/usr/bin/env python3
"""Measure the schema bump cascade against labelled corpora. Paid; run by hand.

Runs `schema_bump_cascade.decide` over every pair in the historical corpus
(each consecutive pair of pinned versions under `schemas/`, labelled by its
committed bump unless `evals/schema_bumps/labels.json` says otherwise) and the
synthetic corpus (`evals/schema_bumps/synthetic.py`), `--runs` times, and
reports accuracy, under-bumps, escalation rate, cost and the confidence of
every miss.

Changing a model pin, the confidence floor or any prompt text in
`schema_bump_cascade` is a change this evaluation must be re-run for. Exit 1
when the acceptance bars fail: any under-bump, or any stage-1 miss at or above
the floor.

    OPENROUTER_API_KEY=... python3 scripts/eval_schema_bumps.py --runs 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_ROOT = REPO_ROOT / "evals" / "schema_bumps"
sys.path.insert(0, str(EVAL_ROOT))

import render_schemas  # noqa: E402
import schema_bump_cascade as cascade  # noqa: E402
from schema_diff import diff  # noqa: E402
from synthetic import PAIRS as SYNTHETIC_PAIRS  # noqa: E402

_RANK = {"patch": 1, "minor": 2, "major": 3}


@dataclass(frozen=True)
class Case:
    corpus: str
    name: str
    old: dict
    new: dict
    label: str


def historical_cases() -> list[Case]:
    """Every consecutive pinned pair, labelled; pairs labelled null are left out."""
    labels = {
        (entry["resource"], entry["from"], entry["to"]): entry["label"]
        for entry in json.loads((EVAL_ROOT / "labels.json").read_text())["pairs"]
    }
    cases: list[Case] = []
    seen: set[tuple[str, str, str]] = set()
    for resource in render_schemas.RESOURCES:
        versions = render_schemas.list_published_versions(resource)
        for old_version, new_version in zip(versions, versions[1:]):
            key = (resource.name, old_version, new_version)
            seen.add(key)
            label = labels.get(key, _committed_bump(old_version, new_version))
            if label is None:
                continue
            old = json.loads((resource.dir() / f"{old_version}.json").read_text())
            new = json.loads((resource.dir() / f"{new_version}.json").read_text())
            cases.append(Case("historical", f"{resource.name} {old_version}→{new_version}", old, new, label))
    stale = sorted(set(labels) - seen)
    if stale:
        raise SystemExit(f"labels.json names pairs that are not consecutive pinned versions: {stale}")
    return cases


def _committed_bump(old: str, new: str) -> str:
    o, n = render_schemas.parse_semver(old), render_schemas.parse_semver(new)
    return "major" if n[0] != o[0] else "minor" if n[1] != o[1] else "patch"


def synthetic_cases() -> list[Case]:
    return [Case("synthetic", pair.name, pair.old, pair.new, pair.label) for pair in SYNTHETIC_PAIRS]


def _score(case: Case, post: cascade.Post) -> dict:
    changes = diff(case.old, case.new)
    if not changes:
        raise SystemExit(f"{case.corpus}: {case.name} has an empty diff; label it null or remove it")
    decision = cascade.decide(case.name, case.old, case.new, changes, post)
    stage1 = decision.stage1
    return {
        "corpus": case.corpus,
        "name": case.name,
        "label": case.label,
        "final": decision.final,
        "stage1": stage1,
        "escalated": decision.stage2 is not None,
        "cost": decision.cost,
        "stage1_confident_miss": cascade.stage1_is_final(stage1) and stage1["choice"] != case.label,
    }


def _report(run: int, results: list[dict]) -> bool:
    """Print one run's summary; True when it meets the acceptance bars."""
    ok = True
    for corpus in sorted({r["corpus"] for r in results}):
        rows = [r for r in results if r["corpus"] == corpus]
        misses = [r for r in rows if r["final"] != r["label"]]
        under = [r for r in misses if _RANK[r["final"]] < _RANK[r["label"]]]
        confident = [r for r in rows if r["stage1_confident_miss"]]
        escalated = sum(r["escalated"] for r in rows)
        print(
            f"run {run} {corpus}: {len(rows) - len(misses)}/{len(rows)} correct, "
            f"{len(under)} under-bumps, {escalated}/{len(rows)} escalated, "
            f"${sum(r['cost'] for r in rows):.4f}"
        )
        for r in misses:
            kind = "UNDER" if r in under else "over"
            confidence = r["stage1"].get("confidence", r["stage1"].get("skipped"))
            print(f"    {kind}: {r['name']}: {r['final']} (label {r['label']}; stage 1 {confidence})")
        for r in confident:
            print(f"    STAGE-1 MISS AT/ABOVE FLOOR: {r['name']}: {r['stage1']}")
        ok = ok and not under and not confident
    return ok


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--runs", type=_positive_int, default=3)
    parser.add_argument("--corpus", choices=("all", "historical", "synthetic"), default="all")
    parser.add_argument("--workers", type=_positive_int, default=8)
    parser.add_argument("--out", type=Path, help="write every scored result as JSON here")
    args = parser.parse_args(argv)

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY is not set.", file=sys.stderr)
        return 2
    cases = []
    if args.corpus in ("all", "historical"):
        cases += historical_cases()
    if args.corpus in ("all", "synthetic"):
        cases += synthetic_cases()

    post = cascade.openrouter_post(api_key)
    all_results: list[list[dict]] = []
    ok = True
    for run in range(1, args.runs + 1):
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(lambda case: _score(case, post), cases))
        all_results.append(results)
        ok = _report(run, results) and ok
    if args.out:
        args.out.write_text(json.dumps(all_results, indent=2) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
