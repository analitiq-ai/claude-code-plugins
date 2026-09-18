"""Framework for the Analitiq artifact validator.

This module owns the parts that are independent of any particular artifact kind:

- `finding()`, the one construction point for every finding this package emits;
- `_model_findings()` — validate a document against a Pydantic contract model and
  map each error to a finding (the single source of single-document validity,
  reused by every kind);
- `contract_model_domain()` — the env guard every kind imports its contract models
  under, defined once so the DOMAIN dance is not reimplemented per kind;
- the KIND-VALIDATOR REGISTRY and `_dispatch()`/`validate_document()` driver — a
  per-kind module (e.g. `connectors`) contributes a `(detector, validator_fn)`
  pair via `register_kind()`; `_dispatch` consults the registry rather than
  hard-coding any kind's branches, so a new kind is *register, done*. A kind
  whose validity is its contract model plus the `$schema`-omission check
  registers via `register_model_and_schema_kind()` instead of hand-writing
  that combination;
- `_bounded()` — the one width a diagnostic borrowed from another library, or
  a document value it echoes, is clipped to; messages the contract models write
  are not clipped;
- `_run_guarded()` — a crash in one check becomes a single `notApplicable`
  finding so the others survive;
- `finding_costs_a_pass()` — whether one finding, on its own, keeps a document
  from passing; exported so a caller aggregating published findings into its
  own verdict (the pipeline plugin's adapter, say) reduces over them the same
  way `_passed()` does, rather than a second predicate that can drift from it;
- `_passed()` — `not any(finding_costs_a_pass(f) for f in findings)`, so
  `main()` and a future caller answer "did this document pass" identically;
- the `main()` CLI: read the document, validate, print `{"passed", "findings"}`,
  exit 0 iff `_passed()` says so (1 on a failing document / unreadable document;
  2 on CLI usage errors).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterator

from pydantic import TypeAdapter, ValidationError

# `analitiq.contracts.shared.rules` (`rule_by_id`, `RuleViolation`) is
# imported lazily, inside the functions below that need it, never at this
# module's own top level: `connectors`/`pipelines` import THIS module before
# their own guarded `try/except ImportError` around the contract models runs,
# so an unconditional import here would raise before that guard ever sees it,
# turning a missing `analitiq-contract-models` into a raw traceback instead of
# the structured "Missing dependency" diagnostic the guard exists to produce.

#: The finding shape's `kind` axis (`rules/SCHEMA.md`, "Findings — what a check
#: reports"): whether a check found a violation at all, not which check ran.
_KINDS = ("fail", "notApplicable", "informational")


# The kind registry: ordered `(detector, validator_fn)` pairs. `_dispatch` runs
# each detector in registration order and hands the document to the first
# validator whose detector matches. A validator takes `(doc, doc_path)` and
# returns a list of findings.
_Validator = Callable[[Any, "Path | None"], list[dict]]
_KIND_REGISTRY: list[tuple[Callable[[Any], bool], _Validator]] = []


def register_kind(detector: Callable[[Any], bool], validator: _Validator) -> None:
    """Append a `(detector, validator_fn)` pair to the dispatch registry."""
    _KIND_REGISTRY.append((detector, validator))


# ---------------------------------------------------------------------------
# Contract-model import guard
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def contract_model_domain() -> Iterator[None]:
    """Bind `DOMAIN=analitiq.ai` for the duration of a contract-model import, then
    restore the caller's ambient value.

    The contract models bind `DOMAIN` at *their* import for the `$schema` host
    `Literal`. This tool validates PUBLIC documents, which always declare the
    canonical `schemas.analitiq.ai` host, so the models must import under
    `DOMAIN=analitiq.ai` — an ambient `DOMAIN=analitiq.dev` (common in dev shells)
    would otherwise make them reject every `schemas.analitiq.ai` document. Scope
    the override to exactly the import window and restore the caller's ambient
    `DOMAIN` afterwards: importing this package must not leak a process-wide env
    mutation onto a host that reads `DOMAIN` at runtime (e.g. an in-process
    consumer of a bundle), which would silently repoint it. The models keep the
    `analitiq.ai` host they captured at import.
    """
    ambient = os.environ.get("DOMAIN")
    os.environ["DOMAIN"] = "analitiq.ai"
    try:
        yield
    finally:
        if ambient is None:
            os.environ.pop("DOMAIN", None)
        else:
            os.environ["DOMAIN"] = ambient


def _bounded(text: str, limit: int = 200) -> str:
    """A diagnostic sentence borrowed from another library, bounded.

    `jsonschema` renders the failing instance AND the failing keyword's own value
    into `ValidationError.message`, so an oversized sample or a long `enum` comes
    back whole — and it is emitted once per recorded entry. Bounding the sample
    alone leaves the same unbounded text arriving by the other route.

    Plain slicing rather than `textwrap.shorten`, which drops the text entirely
    when it holds no whitespace to break on — a provider record is exactly that
    shape."""
    return text if len(text) <= limit else f"{text[:limit]}…"


# Pydantic error types whose sentence renders a value taken from the failing
# document, each keyed to the `ctx` entry holding that value. Only the value is
# clipped: the rest of the sentence is the constraint that rejected it — for a
# discriminated union, every tag it accepts — which is the half an author needs
# entire. Pydantic's other built-in sentences render the constraint and not the
# input; a `value_error` carries whatever the contract model's validator wrote.
_INPUT_ECHOING_CTX = {"union_tag_invalid": "tag"}


def _model_error_message(err: Any) -> str:
    key = _INPUT_ECHOING_CTX.get(err["type"])
    if key is None:
        return err["msg"]
    echoed = str(err["ctx"][key])
    return err["msg"].replace(echoed, _bounded(echoed), 1)


def finding(
    *,
    rule: str | None = None,
    message_id: str,
    kind: str,
    path: str,
    message: str,
) -> dict:
    """One thing a check said about one document (`rules/SCHEMA.md`, "Findings").

    `rule`, when given, is the id of the record the finding concerns, resolved
    through the same `rule_by_id` a rejection raised via `rules.violation`
    resolves through, so the two never disagree about what an id names. A
    `fail` finding's `severity` is derived from that record — never accepted
    as a literal — and is `error` for the two framework cases that fail with
    no rule to name (`rule=None`). A `notApplicable` or `informational`
    finding carries no `severity` at all.
    """
    if kind not in _KINDS:
        raise ValueError(f"unknown kind: {kind!r}")
    record = None
    if rule is not None:
        from analitiq.contracts.shared.rules import rule_by_id
        try:
            record = rule_by_id(rule)
        except KeyError:
            raise ValueError(f"unknown rule id: {rule!r}") from None
    result: dict = {}
    if rule is not None:
        result["rule"] = rule
    result["message_id"] = message_id
    result["kind"] = kind
    if kind == "fail":
        severity = record.severity if record is not None else "error"
        if severity not in ("error", "warning"):
            # A record's `info` never reaches a finding as a severity value
            # (rules/SCHEMA.md, validator-verdict-stability.md) — a detected
            # info-tier violation is `kind: informational`, not `kind: fail`.
            # A rule binding a validator at info severity and a call site
            # still reporting it as `fail` is a caller bug, not a runtime case
            # to tolerate silently.
            raise ValueError(
                f"rule {rule!r} is severity {severity!r}; a fail finding may "
                "only report error or warning — an info-tier violation is "
                "kind: informational instead")
        result["severity"] = severity
    result["path"] = path
    result["message"] = message
    return result


def finding_costs_a_pass(f: dict) -> bool:
    """Whether one finding, on its own, keeps `passed` from being `True`
    (`rules/SCHEMA.md`, "Findings"): a `fail` at `severity: error`, or a
    `notApplicable` for a rule that is (or, naming none, might as well be)
    `error`-tier. Exported so a consumer aggregating `analitiq.validator`
    findings into its own verdict — the pipeline plugin's adapter, say —
    reduces over them the same way `_passed` does, rather than a second
    predicate that can drift from this one.

    A finding with no `kind` at all is a pre-`rules/SCHEMA.md` shape (a
    consumer's own locally-minted finding, never built through `finding()`)
    and is graded the way every finding once was: `severity: error` costs,
    anything else does not.
    """
    kind = f.get("kind", "fail")
    if kind == "fail":
        return f.get("severity") == "error"
    if kind == "notApplicable":
        rule = f.get("rule")
        if rule is None:
            return True
        from analitiq.contracts.shared.rules import rule_by_id
        return rule_by_id(rule).severity == "error"
    return False


# ---------------------------------------------------------------------------
# Model validation — the single source of single-document validity
# ---------------------------------------------------------------------------

def _model_findings(doc: Any, adapter: TypeAdapter) -> list[dict]:
    """Validate `doc` against a contract model; map each error to a finding.

    A rejection raised through `rules.violation` carries its `rule_id`,
    `message_id`, `message` and optional `path` as attributes on a
    `RuleViolation`, and an enforcer that walks a whole document and finds
    several unrelated complaints raises one `MultiRuleViolation` carrying
    every one — a `@model_validator` returns the model or raises, nothing
    between. `rules.rule_violations` unpacks either shape from the pydantic
    error, and each violation becomes its own finding: its own attributes
    rather than the joined `err["msg"]` pydantic rendered for the raise, and
    its `path` extending `err["loc"]` when it set one.

    A field constraint pydantic enforces on its own — no `violation` call
    behind it — carries no violation, and the finding's `rule` is `None` for
    it (`rules/SCHEMA.md`'s "a field constraint on a contract model rejected,
    and no record claims it"); its `message_id` is pydantic's own error-type
    string (`err["type"]`, e.g. `"missing"`, `"string_pattern_mismatch"`) — an
    existing, already-stable vocabulary reused rather than a second one
    invented beside it.
    """
    from analitiq.contracts.shared.rules import rule_violations
    try:
        adapter.validate_python(doc)
        return []
    except ValidationError as exc:
        findings: list[dict] = []
        for err in exc.errors():
            base_path = "/" + "/".join(str(p) for p in err["loc"])
            violations = rule_violations(err)
            if not violations:
                findings.append(finding(
                    message_id=err["type"],
                    kind="fail",
                    path=base_path,
                    message=_model_error_message(err),
                ))
            for v in violations:
                findings.append(finding(
                    rule=v.rule_id,
                    message_id=v.message_id,
                    kind="fail",
                    path=base_path + (v.path or ""),
                    message=v.message,
                ))
        return findings


def _missing_schema_url_findings(doc: Any) -> list[dict]:
    """RULE-SHRD-003 gate, shared by every kind whose contract leaves `$schema`
    optional (connection, stream, pipeline, connector). The rule asks for the
    published canonical URL, and each of those models types the field as a
    `Literal`/pattern that rejects any other STRING as a structural `error`.
    What the annotation admits is the absence of a value — the key left out, or
    the key present holding `null`, both of which those models type as optional.
    Neither names a contract, so both are reported here as the same defect: a
    document a reader, an editor or a migration cannot place.
    """
    if not isinstance(doc, dict) or doc.get("$schema") is not None:
        return []
    return [finding(
        rule="RULE-SHRD-003",
        message_id="schema-url-missing",
        kind="fail",
        path="/$schema",
        message="document declares no `$schema`; declare it with the published canonical URL for this family.",
    )]


def register_model_and_schema_kind(detector: Callable[[Any], bool], adapter: TypeAdapter) -> None:
    """Register a single-document kind whose entire validity is its contract model
    plus the RULE-SHRD-003 `$schema`-omission check.

    A kind with no further cross-file or referential checks needs only
    `_model_findings(doc, adapter) + _missing_schema_url_findings(doc)` under the
    per-kind `(doc, doc_path)` signature. Packaging that here lets such a
    module supply just its detector and adapter, so the combination is
    defined once rather than reimplemented per kind.
    """
    def _validate(doc: Any, doc_path: Path | None = None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
        return _model_findings(doc, adapter) + _missing_schema_url_findings(doc)
    register_kind(detector, _validate)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def validate_document(doc: Any, doc_path: Path | None = None) -> list[dict]:
    """Detect the document kind, validate via its model, add cross-file checks."""
    return _run_guarded(_dispatch, doc, doc_path, crash_label="document validation")


def _dispatch(doc: Any, doc_path: Path | None) -> list[dict]:
    for detector, validator in _KIND_REGISTRY:
        if detector(doc):
            return validator(doc, doc_path)
    # Anything no registered kind claims is a document we were asked to validate
    # but cannot identify — that is a validation failure, not a pass.
    return [finding(
        message_id="unrecognized-document", kind="fail", path="/",
        message=(
            "document does not match any known artifact (connector / api-endpoint / "
            "database-endpoint / type-map / connection / stream / pipeline); a connector "
            "must declare 'kind', an api-endpoint 'operations', a type-map a "
            "'rules' array (inside the {$schema, direction, rules} object), a "
            "connection a 'connector_id', a stream 'source' + 'destinations', "
            "a pipeline 'connections'."))]


def _run_guarded(fn: Callable, *args, crash_label: str, rule: str | None = None) -> list[dict]:
    """Run a check; a crash becomes one finding so other checks survive.

    `crash_label` is keyword-only and named apart from any parameter a
    wrapped `fn` might itself take (`_embedded_schema_example_findings`'s own
    `label`, say) — a same-named keyword here would be consumed by this
    function instead of reaching `fn`, silently dropping the caller's intent.

    `notApplicable`, not `fail`: the crash means nothing here decided whether
    any rule the check would have graded holds, which is exactly what that
    kind reports. `rule`, when the caller names one, is the obligation a
    crash mid-check leaves unevaluated — a caller wrapping a check bound to
    exactly one rule (`_embedded_schema_example_findings` and RULE-ENDP-063,
    say) passes it so the crash stays routable to it; a caller wrapping
    dispatch over an unidentified document (`validate_document`, which could
    crash on behalf of any rule or none) leaves it `None`, which keeps the
    finding inside the framework's own no-rule case and off the "clears the
    bar" list `passed` reduces over, so it always costs — matching what an
    unconditional `severity: error` finding always did here.
    """
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        return [finding(
            rule=rule, message_id="check-crashed", kind="notApplicable", path="",
            message=(
                f"{crash_label} crashed unexpectedly ({type(exc).__name__}: {exc}); "
                "this is a validator bug — please report."))]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an Analitiq connector/endpoint/type-map document.")
    parser.add_argument("--document", required=True, help="Path to the JSON document to validate.")
    args = parser.parse_args()

    document_path = Path(args.document)
    try:
        document = json.loads(document_path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        # OSError subsumes FileNotFoundError / IsADirectoryError / PermissionError,
        # so an unreadable document always yields the finding + exit 1 (never a
        # bare traceback), matching _load_type_map and the documented contract.
        print(json.dumps({"passed": False, "findings": [finding(
            message_id="unreadable-document", kind="fail", path="",
            message=f"Cannot read document: {exc}")]}))
        return 1

    findings = validate_document(document, doc_path=document_path.resolve())
    passed = _passed(findings)
    print(json.dumps({"passed": passed, "findings": findings}, indent=2))
    return 0 if passed else 1


def _passed(findings: list[dict]) -> bool:
    """Whether `findings` clears the bar `rules/SCHEMA.md`'s Findings section
    sets: no `fail` at `severity: error`, and no `notApplicable` for a rule
    that is (or, naming none, might as well be) `error`-tier.

    An unchecked `error`-tier rule is not a rule that held — a `notApplicable`
    naming no `rule` at all cannot even ask the question, so it always costs,
    same as one naming an `error`-tier rule explicitly. `informational`
    findings never reach this reduction: nothing about them costs anything.
    """
    return not any(finding_costs_a_pass(f) for f in findings)
