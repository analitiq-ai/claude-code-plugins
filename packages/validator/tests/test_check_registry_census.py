"""Every check this package runs must be some rule's enforcer.

The registry's enforcer→registry census lives in the contract-models suite and
walks contract classes, so it sees `@model_validator` methods and nothing else.
That covers one of the two enforcement homes. The other is here: a rule needing
a second document in hand — a package's type map, the connector an endpoint
ships in, the streams an assembled run pins — is a function in
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

The no-rule half is censused too, in the other direction: `rules/SCHEMA.md`
names the specific cases `rule` may be absent for, so a ruleless call site
with no entry in `RULELESS_SITES` is a check reaching for the shortcut rather
than attributing a real rule — the failure this file's other half cannot see,
since it only ever looks at calls that DO carry one.
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


def _ruleless_emitters() -> dict[tuple[str, str], int]:
    """Map each `(module::owner, message_id)` a ruleless `finding()` call
    carries to the line it sits on.

    A call is ruleless when its `rule` keyword is absent or the literal
    `None`. `_run_guarded` threads `rule` through from its caller, so a call
    to it is tracked the way a direct `finding()` call would be, attributed to
    whichever function made it. Its own internal `finding()` call is not
    counted: it passes `rule` as a variable — its callers decide, and are what
    this counts.
    """
    found: dict[tuple[str, str], int] = {}

    def walk(node: ast.AST, module: str, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, module, owner or child.name)
                continue
            if isinstance(child, ast.Call) and getattr(child.func, "id", None) in (
                "finding", "_run_guarded",
            ):
                callee = child.func.id
                kwargs = {kw.arg: kw.value for kw in child.keywords}
                rule_kw = kwargs.get("rule")
                is_ruleless = rule_kw is None or (
                    isinstance(rule_kw, ast.Constant) and rule_kw.value is None
                )
                # `_run_guarded`'s own `message_id="check-crashed"` is fixed inside
                # its body, never passed by a caller — the same literal every call
                # site shares, unlike `finding`'s caller-given one.
                mid = ast.Constant("check-crashed") if callee == "_run_guarded" else kwargs.get("message_id")
                if is_ruleless and isinstance(mid, ast.Constant) and isinstance(mid.value, str):
                    found[(f"{module}::{owner or '<module>'}", mid.value)] = child.lineno
            walk(child, module, owner)

    for path in sorted(VALIDATOR_SRC.glob("*.py")):
        stem = path.stem
        module = "analitiq.validator" if stem == "__init__" else f"analitiq.validator.{stem}"
        walk(ast.parse(path.read_text(encoding="utf-8")), module, None)
    return found


#: Every call site this package emits a ruleless finding from, mapped to the
#: `rules/SCHEMA.md` framework case it is. A site this file's walk finds and
#: this table does not name fails the build — the only way a check earns the
#: right to omit `rule` is stating which documented case it is.
RULELESS_SITES: dict[tuple[str, str], str] = {
    ("analitiq.validator._core::_unreadable_document_finding", "unreadable-document"): (
        "the document could not be parsed at all, so no rule of the kind it "
        "was named as could be evaluated"),
    ("analitiq.validator.document_set::_graded_request", "package-root-missing"): (
        "a structural precondition — the package carries no document at its "
        "root location — rejected before any package check could run"),
    ("analitiq.validator.document_set::validate_workspace", "workspace-empty"): (
        "a structural precondition — the workspace locates no package — "
        "rejected before any document or check could run"),
    ("analitiq.validator._core::validate_document_as", "check-crashed"): (
        "grading one document as the kind a request named crashed, leaving "
        "its model errors and every rule of that kind unevaluated together, "
        "so the crash is attributable to no one of them"),
    ("analitiq.validator.document_set::_check_findings", "check-crashed"): (
        "a cross-document check crashed; a check may grade several rules, so "
        "the crash is attributable to no one of them"),
    ("analitiq.validator.connectors::_unknown_kind_finding", "coverage-check-skipped-bad-kind"): (
        "the connector's kind is outside the closed enum the model already "
        "rejects, so coverage was never asked"),
}


def test_every_ruleless_finding_is_a_named_framework_case():
    """`rules/SCHEMA.md`'s Findings section says `rule` is absent only in
    named framework cases — never as a shortcut a new cross-document check
    reaches for. `RULELESS_SITES` is that enumeration; a call site this walk
    finds and that table does not name is the failure this test exists to
    catch."""
    found = _ruleless_emitters()
    unaccounted = sorted(set(found) - set(RULELESS_SITES))
    assert not unaccounted, (
        "finding() (or _run_guarded, which threads rule through from its caller) "
        "emits a ruleless finding "
        "RULELESS_SITES does not name — add it with the framework case it is, "
        "or attribute an actual rule instead: "
        + ", ".join(f"{site} (line {found[site]})" for site in unaccounted)
    )


def test_ruleless_sites_are_still_live():
    """The rot direction: a table entry naming a call site this walk no
    longer finds stays green forever and silently exempts the next ruleless
    emission to reuse the name."""
    found = _ruleless_emitters()
    stale = sorted(set(RULELESS_SITES) - set(found))
    assert not stale, f"RULELESS_SITES names call sites no longer emitting: {stale}"


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
            unaccounted.append(f"{rule.id}: declares {binding!r}, but no finding() call emits it")
    assert not unaccounted, (
        "records whose `validator` names a analitiq.validator function that "
        f"emits no finding for the rule's own id: {unaccounted}"
    )
