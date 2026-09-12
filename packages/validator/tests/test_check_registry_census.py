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
    `None` — `_load_json_sibling` and `_run_guarded` each thread `rule`
    through from THEIR caller, so a call to either passing a literal
    `rule=None` (or omitting it) is tracked the same way a direct `finding()`
    call would be, attributed to whichever function made that call. Neither
    helper's own internal `finding(rule=rule, …)` is itself ruleless by this
    walk (`rule` there is a variable, not a literal `None`) — their callers
    are what decide, and are what this counts.
    """
    found: dict[tuple[str, str], int] = {}

    def walk(node: ast.AST, module: str, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, module, owner or child.name)
                continue
            if isinstance(child, ast.Call) and getattr(child.func, "id", None) in (
                "finding", "_load_json_sibling", "_run_guarded",
            ):
                callee = child.func.id
                kwargs = {kw.arg: kw.value for kw in child.keywords}
                rule_kw = kwargs.get("rule")
                is_ruleless = rule_kw is None or (
                    isinstance(rule_kw, ast.Constant) and rule_kw.value is None
                )
                # `_run_guarded`'s own `message_id="check-crashed"` is fixed inside
                # its body, never passed by a caller — the same literal every call
                # site shares, unlike `finding`/`_load_json_sibling`'s caller-given one.
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
    ("analitiq.validator._core::_dispatch", "unrecognized-document"): (
        "no registered kind's detector claimed the document"),
    ("analitiq.validator._core::main", "unreadable-document"): (
        "the document could not be read or parsed at all, before any kind "
        "was even identified"),
    ("analitiq.validator._core::validate_document", "check-crashed"): (
        "top-level dispatch crashed before any kind was even identified, so "
        "the crash is not attributable to any one rule; a guarded check "
        "bound to exactly one rule instead passes it through _run_guarded's "
        "own rule= parameter, which keeps that crash off this table"),
    ("analitiq.validator.connectors::<module>", "missing-contract-models-dependency"): (
        "the contract-models dependency is missing; no rule was even "
        "reachable to ask about"),
    ("analitiq.validator.connectors::check_coverage", "coverage-check-skipped-no-path"): (
        "coverage needs a filesystem-anchored document path this call did "
        "not have"),
    ("analitiq.validator.connectors::check_coverage", "coverage-check-skipped-bad-kind"): (
        "the connector's kind is outside the closed enum the model already "
        "rejects, so coverage was never asked"),
    ("analitiq.validator.connectors::check_coverage", "endpoint-file-unreadable"): (
        "a sibling endpoint file's read/parse failure precedes any rule "
        "evaluation of its content"),
    ("analitiq.validator.connectors::_validate_api_endpoint", "sibling-connector-unreadable"): (
        "a read/parse failure has not evaluated RULE-ENDP-047 one way or "
        "the other; which rule went unchecked is the sibling notApplicable "
        "branch's to name, not this one's"),
    ("analitiq.validator.connectors::_validate_type_map", "type-map-direction-defaulted"): (
        "informational: a direction default is a fact about how this run "
        "proceeded, not a violation of anything"),
    ("analitiq.validator.pipelines::validate_pipeline_bundle", "bundle-not-a-mapping"): (
        "rejects before any referential check the registry binds could even "
        "begin"),
    ("analitiq.validator.pipelines::validate_pipeline_bundle", "bundle-missing-pipeline-document"): (
        "rejects before any referential check the registry binds could even "
        "begin"),
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
        "finding() (or _load_json_sibling/_run_guarded, which thread rule "
        "through from their caller) emits a ruleless finding RULELESS_SITES "
        "does not name — add it with the framework case it is, or attribute "
        "an actual rule instead: "
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
            unaccounted.append(f"{rule.id}: declares {binding!r}, which no finding() call emits it from")
    assert not unaccounted, (
        "records whose `validator` names a analitiq.validator function that "
        f"emits no finding for the rule's own id: {unaccounted}"
    )
