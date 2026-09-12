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

The universe is every literal `rule="RULE-…"` a `finding()` call carries — a
`finding()` naming no rule is one of the framework's own no-rule cases
(`rules/SCHEMA.md`'s case table) and carries no id to census. The mapping from
rule id to emitter is an AST walk for `finding(…, rule="<id>", …)`: a call
located by callee name and a literal `rule` keyword, never by reading what any
surrounding text means.
"""
from __future__ import annotations

import ast
from pathlib import Path

VALIDATOR_SRC = Path(__file__).resolve().parents[1] / "src" / "analitiq" / "validator"


def _rule_id_emitters() -> dict[str, set[str]]:
    """Map each literal `rule=` value on a `finding()` call to the
    `module::function` bindings emitting it.

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
            if owner and isinstance(child, ast.Call) and getattr(child.func, "id", None) == "finding":
                for kw in child.keywords:
                    if (
                        kw.arg == "rule"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ):
                        found.setdefault(kw.value.value, set()).add(f"{module}::{owner}")
            walk(child, module, owner)

    for path in sorted(VALIDATOR_SRC.glob("*.py")):
        stem = path.stem
        module = "analitiq.validator" if stem == "__init__" else f"analitiq.validator.{stem}"
        walk(ast.parse(path.read_text(encoding="utf-8")), module, None)
    return found


def test_every_finding_rule_id_resolves_to_a_record():
    """A `finding()` call naming a rule id the registry does not carry is a
    document rejected by a rule its author was never shown."""
    from analitiq.contracts.shared.rules import all_rules

    emitters = _rule_id_emitters()
    assert emitters, (
        f"no `finding(…, rule=\"<id>\", …)` call sites found under {VALIDATOR_SRC} — "
        "every cross-document check reports through `finding`, so this is a walk "
        "that stopped matching rather than a package with no rule-bound checks in it"
    )
    known = {rule.id for rule in all_rules()}
    unknown = sorted(set(emitters) - known)
    assert not unknown, (
        f"finding() call sites name rule ids the registry does not carry: {unknown}"
    )


def test_every_cross_document_rule_is_emitted_by_its_declared_enforcer():
    """The enforcer→registry direction, over the cross-document half.

    A record whose `validator` resolves into this package names the exact
    `module::function` a rule's finding must come from — the same statement
    the contract-models census makes of a `@model_validator`, applied to a
    function that emits through `finding()` instead of raising through
    `rules.violation`. A record naming a symbol nothing here emits from is
    a rule the reference advertises and the tool cannot actually apply.
    """
    from analitiq.contracts.shared.rules import all_rules

    emitters = _rule_id_emitters()
    unaccounted = []
    for rule in all_rules():
        if not rule.validator_module or not rule.validator_module.startswith("analitiq.validator"):
            continue
        binding = f"{rule.validator_module}::{rule.validator_symbol}"
        if binding not in emitters.get(rule.id, set()):
            unaccounted.append(f"{rule.id}: declares {binding!r}, which no finding() call emits it from")
    assert not unaccounted, (
        "records whose `validator` names a analitiq.validator function that "
        f"emits no finding for the rule's own id: {unaccounted}"
    )
