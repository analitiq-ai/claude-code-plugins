#!/usr/bin/env python3
"""Compile the rule registry, and refuse to compile a broken one.

`rules/records/*.yaml` is the source of truth — one record per rule, schema in
`rules/SCHEMA.md`. This script is the only thing that reads it:

    render_rules.py write    # validate every record, compile rules.json
    render_rules.py check    # CI: same, and fail if the compiled copy is stale

**Why a compiled copy exists.** The registry ships inside
`analitiq-contract-models`, because the models enforce the advisory tier at
`model_validate` time and the engine installs that wheel. Reading YAML there
would add a parser dependency to a package deliberately kept to the minimum the
models need at run time, for data that never changes. So the records compile to
`analitiq/contracts/shared/rules.json`, read with the standard library. That
file is a *pinned* copy, not a second source: `check` recompiles and diffs, the
way `render_schemas.py` already guards `schemas/`.

**What validation covers**, beyond the record schema `RuleRecord` enforces:

* the filename matches the id, so a record is findable by the id in a finding;
* no id is reused, including by a record that was retired;
* every `validator` resolves. A code binding names the module that is imported
  — not a source path translated into one — so the string a record carries is
  the string this script resolves, and every part of it is checked: the module
  imports, the symbol is on it, a dotted `Symbol.attr` is a real method or a
  real model field. An agent-rule binding must name a file that exists. A rule
  claiming an enforcement it lost is worse than one that never claimed it,
  because the claim is what stops somebody re-adding the check.

The validator resolution is why this script imports the contract models and the
validator package. Nothing else here does. Both are on the path because a rule
is enforced from either: a rule one document can settle on its own is a model
validator, and a rule needing a second document in hand — a sibling type map, a
connector beside an endpoint, the streams an assembled run pins — is a check in
`analitiq.validator`, which is the whole reason that package exists.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = REPO_ROOT / "rules" / "records"

#: A code binding names an importable module, and only one this repo publishes.
#: Without the prefix, `os.path::join` resolves and reports a live enforcer.
_ENFORCER_NAMESPACE = "analitiq."

# Same bootstrap as render_schemas.py: this repo is the contract's SOURCE, and
# `requirements-dev.txt` deliberately installs no wheel of either package.
sys.path.insert(0, str(REPO_ROOT / "packages" / "contract-models" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "validator" / "src"))
os.environ.setdefault("DOMAIN", "analitiq.ai")

from analitiq.contracts.shared.rule_record import (  # noqa: E402
    OWNERS,
    RETIRED_BEFORE_THE_REGISTRY,
    RULES_PATH,
    SCOPES,
    RuleRecord,
)


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover - environment guard
        raise SystemExit(
            "PyYAML is needed to read the rule registry — "
            "pip install -r requirements-dev.txt"
        )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_registry() -> list[RuleRecord]:
    """Every record, validated, ordered by id."""
    problems: list[str] = []
    records: list[RuleRecord] = []
    for path in sorted(RULES_DIR.glob("*.yml")):
        # The glob below takes one spelling. A record saved under the other one
        # is not a rejected record, it is an absent one — the same silence a
        # moved directory produces, one file at a time.
        problems.append(
            f"{path.name}: the registry reads *.yaml — rename it, or this "
            "record is skipped rather than refused"
        )
    for path in sorted(RULES_DIR.glob("*.yaml")):
        payload = _load_yaml(path)
        if not isinstance(payload, dict):
            problems.append(f"{path.name}: not a mapping")
            continue
        try:
            record = RuleRecord(**payload)
        except TypeError as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if path.stem != record.id:
            problems.append(f"{path.name}: filename does not match id {record.id!r}")
        records.append(record)

    if not records and not problems:
        # A registry that globs nothing compiles to a valid, empty document and
        # every consumer reads it as "no rules exist" — a moved directory or a
        # renamed extension would land as a green build with the whole registry
        # switched off.
        problems.append(
            f"{RULES_DIR.relative_to(REPO_ROOT)} holds no *.yaml records — "
            "the registry is never empty, so this is a path or extension that "
            "stopped matching, not a registry with nothing in it"
        )
    seen: dict[str, str] = {}
    for record in records:
        if record.id in seen:
            problems.append(f"duplicate id {record.id}")
        seen[record.id] = record.id
        if record.id in RETIRED_BEFORE_THE_REGISTRY:
            problems.append(
                f"{record.id} was retired before the registry existed and must "
                "never be reissued — it still appears in archived findings"
            )
    problems += _unresolved_validators(records)
    if problems:
        raise SystemExit("rule registry is invalid:\n  " + "\n  ".join(sorted(problems)))
    return sorted(records, key=lambda r: r.id)


def _unresolved_validators(records: list[RuleRecord]) -> list[str]:
    """A `validator` that no longer resolves is a rule claiming a lost enforcement."""
    import importlib

    problems = []
    for record in records:
        if not record.validator:
            continue
        dotted, _, symbol = record.validator.partition("::")
        if not dotted.startswith(_ENFORCER_NAMESPACE):
            problems.append(
                f"{record.id}: validator module {dotted!r} is outside "
                f"{_ENFORCER_NAMESPACE}* — the record would resolve against "
                "whatever else happens to be importable, and report a live "
                "enforcer for a symbol this repo does not own"
            )
            continue
        try:
            module = importlib.import_module(dotted)
        except ImportError as exc:
            problems.append(f"{record.id}: cannot import {dotted} ({exc})")
            continue
        head, _, attr = symbol.partition(".")
        owner = getattr(module, head, None)
        if owner is None:
            problems.append(f"{record.id}: {dotted} has no {head!r}")
            continue
        if attr and not _has_member(owner, attr):
            problems.append(
                f"{record.id}: {head} has no method or model field {attr!r} — "
                "the enforcement this rule claims has moved or gone"
            )
    return problems


def _has_member(owner: object, attr: str) -> bool:
    """Whether `attr` is something this repo defines on `owner`.

    Deliberately narrower than `hasattr`: every contract model inherits
    pydantic's API and `object`'s, so a bare attribute lookup resolves a
    binding naming `dict`, `copy` or `json` and reports a live enforcer for a
    rule nothing applies. A member counts only if a class in this project's own
    namespace declares it, or it is a declared model field.
    """
    fields = getattr(owner, "model_fields", None)
    if fields and attr in fields:
        return True
    mro = getattr(owner, "__mro__", None)
    if mro is None:
        return hasattr(owner, attr)
    return any(
        attr in vars(cls)
        for cls in mro
        if getattr(cls, "__module__", "").startswith("analitiq")
    )


def compile_registry(records: list[RuleRecord]) -> str:
    payload = {
        "$comment": (
            "GENERATED from rules/records/*.yaml by scripts/render_rules.py — do not "
            "edit. The YAML records are the source of truth; this file is the "
            "copy the wheel ships so the package needs no YAML parser."
        ),
        "rules": [
            {
                "id": r.id,
                "statement": r.statement,
                "tier": r.tier,
                "severity": r.severity,
                # Canonical order, so the compiled copy does not churn on the
                # order somebody happened to type into the YAML.
                "scopes": [s for s in SCOPES if s in r.scopes],
                "validator": r.validator,
                "owners": [o for o in OWNERS if o in r.owners],
                "targets": list(r.targets),
                "fields": list(r.fields),
                "mechanism": r.mechanism,
                "pattern_symbol": r.pattern_symbol,
                "fixture_model": r.fixture_model,
                "rationale": r.rationale,
                "status": r.status,
                "superseded_by": r.superseded_by,
            }
            for r in records
        ],
    }
    return json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "check"
    if mode not in ("write", "check"):
        print(f"usage: {argv[0]} [write|check]", file=sys.stderr)
        return 2

    records = load_registry()
    rendered = compile_registry(records)

    if mode == "write":
        RULES_PATH.write_text(rendered, encoding="utf-8")
        print(f"{len(records)} records -> {RULES_PATH.relative_to(REPO_ROOT)}")
        return 0

    if not RULES_PATH.exists():
        print(f"{RULES_PATH} is missing — run `render_rules.py write`", file=sys.stderr)
        return 1
    if RULES_PATH.read_text(encoding="utf-8") != rendered:
        print(
            f"{RULES_PATH.relative_to(REPO_ROOT)} is stale — the registry changed. "
            "Run `python scripts/render_rules.py write`.",
            file=sys.stderr,
        )
        return 1
    print(f"{len(records)} records; compiled registry is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
