"""Every check this package runs must be some rule's enforcer.

The registry's enforcer→registry census lives in the contract-models suite and
walks contract classes, so it sees `@model_validator` methods and nothing else.
That covers one of the two enforcement homes. The other is here: a rule needing
a second document in hand — a sibling type map, the connector an endpoint ships
beside, the streams an assembled run pins — is a function in
`analitiq.validator`, and until this file existed such a check could be added
with no record and nothing would notice. The registry would then describe less
than the tool enforces, which is the failure the census exists to prevent: an
author reads the rendered reference, sees no rule, and writes a document the
validator rejects.

The universe is `VALIDATOR_IDS`, which the package builds at import — each
per-kind module declares its ids through `register_validator_ids` — so a new
check is swept the moment it can emit a finding, without this file being
edited. The mapping from id to enforcer is an AST walk for `finding("<id>",
…)`: a call located by callee name and a literal first argument, never by
reading what any surrounding text means.
"""
from __future__ import annotations

import ast
from pathlib import Path

VALIDATOR_SRC = Path(__file__).resolve().parents[1] / "src" / "analitiq" / "validator"

#: Ids the framework emits, which are not checks and have no rule to bind.
#: `finding()` accepts them so every failure path can report through one
#: shape, including the paths that report the absence of a verdict.
EXEMPT_VALIDATOR_IDS = {
    "contract-model": (
        "the contract models' own rejection, relabelled — each pydantic error "
        "becomes a finding, and the rule it violates is registered against the "
        "model validator or field that raised it"
    ),
    "document": (
        "not a rule about a document but the verdict that there is no document "
        "to judge: unrecognized kind, unreadable file, unparseable JSON"
    ),
}


def _emitters() -> dict[str, set[str]]:
    """Map each literal validator id to the `module::function` bindings emitting it.

    The enclosing function is the outermost one, because that is the symbol a
    record can name: a helper defined inside a check is not importable, so a
    `finding()` raised there is attributed to the check that owns it.
    """
    found: dict[str, set[str]] = {}

    def walk(node: ast.AST, module: str, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, module, owner or child.name)
                continue
            if (
                owner
                and isinstance(child, ast.Call)
                and getattr(child.func, "id", None) == "finding"
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
            ):
                found.setdefault(child.args[0].value, set()).add(f"{module}::{owner}")
            walk(child, module, owner)

    for path in sorted(VALIDATOR_SRC.glob("*.py")):
        stem = path.stem
        module = "analitiq.validator" if stem == "__init__" else f"analitiq.validator.{stem}"
        walk(ast.parse(path.read_text(encoding="utf-8")), module, None)
    return found


def test_every_registered_check_is_some_rules_enforcer(validator):
    """The enforcer→registry direction, over the cross-document half.

    A check with no record enforces a rule the registry does not carry, so
    nothing renders it into the plugin references an agent authors against —
    the document is rejected by a rule its author was never shown.
    """
    from analitiq.contracts.shared.rules import all_rules

    emitters = _emitters()
    assert emitters, (
        f"no `finding(\"<id>\", …)` call sites found under {VALIDATOR_SRC} — "
        "every check reports through `finding`, so this is a walk that stopped "
        "matching rather than a package with no checks in it"
    )
    bound = {rule.validator for rule in all_rules() if rule.validator}
    unaccounted = []
    for check_id in sorted(validator.VALIDATOR_IDS):
        if check_id in EXEMPT_VALIDATOR_IDS:
            continue
        if not emitters.get(check_id, set()) & bound:
            unaccounted.append(check_id)
    assert not unaccounted, (
        "registered validator checks that are no rule's enforcer — give each a "
        "record in rules/records/ whose `validator` names the function emitting "
        "it (then run `python3 scripts/render_rules.py write`), or add an "
        f"explicit exemption with its reason: {unaccounted}"
    )


def test_every_registered_check_has_a_call_site(validator):
    """An id nobody emits is a check that was deleted, leaving its declaration.

    Without this the test above passes for it forever: an id with no emitter
    can never be bound, so an exemption is the only way to make it green, and
    the honest fix is to drop the id. It also keeps the census from grading a
    typo — a registered id spelled one way and emitted another silently makes
    two entries where the author meant one.
    """
    emitters = _emitters()
    orphans = sorted(set(validator.VALIDATOR_IDS) - set(emitters))
    assert not orphans, (
        "validator ids declared through `register_validator_ids` that no "
        f"`finding()` call emits — drop each from its module: {orphans}"
    )


def test_exemptions_name_live_validator_ids(validator):
    """The rot direction of the exemption table: an exemption for an id that no
    longer exists stays green forever, and silently exempts the next check to
    reuse the name."""
    stale = sorted(set(EXEMPT_VALIDATOR_IDS) - set(validator.VALIDATOR_IDS))
    assert not stale, f"exemptions naming no registered validator id: {stale}"
