"""Framework for the Analitiq artifact validator.

This module owns the parts that are independent of any particular artifact kind:

- `finding()` and the `VALIDATOR_IDS` registry every check emits through;
- `_model_findings()` — validate a document against a Pydantic contract model and
  map each error to a finding (the single source of single-document validity,
  reused by every kind);
- `contract_model_domain()` — the env guard every kind imports its contract models
  under, defined once so the DOMAIN dance is not reimplemented per kind;
- the KIND-VALIDATOR REGISTRY and `_dispatch()`/`validate_document()` driver — a
  per-kind module (e.g. `connectors`) contributes a `(detector, validator_fn)`
  pair plus its own validator ids; `_dispatch` consults the registry rather than
  hard-coding any kind's branches, so a new kind is *register, done*. A kind whose
  entire validity is its contract model registers via `register_model_kind()`;
- `_bounded()` — the one width every borrowed diagnostic is clipped to, so a
  finding is bounded the same way whichever route the text arrived by;
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

# The set of legal validator ids. The framework owns `contract-model` (emitted by
# `_model_findings`) and `document` (the unrecognized-artifact verdict); each
# per-kind module contributes its own ids via `register_validator_ids`.
#
# Transitional: `validator` still names a finding's category, the way it always
# has, while `finding()` also carries the `rule`/`message_id`/`kind` shape
# `rules/SCHEMA.md` documents. The category is not yet gone — a later change
# retires `VALIDATOR_IDS` and this field together, once nothing reads them.
VALIDATOR_IDS: set[str] = {"contract-model", "document"}

#: The finding shape's `kind` axis (`rules/SCHEMA.md`, "Findings — what a check
#: reports"): whether a check found a violation at all, not which check ran.
_KINDS = ("fail", "notApplicable", "informational")


def register_validator_ids(ids: set[str]) -> None:
    """A per-kind module declares the validator ids its findings may carry."""
    VALIDATOR_IDS.update(ids)


# The kind registry: ordered `(detector, validator_fn)` pairs. `_dispatch` runs
# each detector in registration order and hands the document to the first
# validator whose detector matches. A validator takes `(doc, doc_path, schema_url)`
# and returns a list of findings.
_Validator = Callable[[Any, "Path | None", "str | None"], list[dict]]
_KIND_REGISTRY: list[tuple[Callable[[Any], bool], _Validator]] = []


def register_kind(detector: Callable[[Any], bool], validator: _Validator) -> None:
    """Append a `(detector, validator_fn)` pair to the dispatch registry."""
    _KIND_REGISTRY.append((detector, validator))


def register_model_kind(detector: Callable[[Any], bool], adapter: TypeAdapter) -> None:
    """Register a single-document kind whose entire validity is its contract model.

    A kind with no cross-file or referential checks — the contract model IS the
    whole validity story — needs only `_model_findings(doc, adapter)` under the
    per-kind `(doc, doc_path, schema_url)` signature. Packaging that here lets such
    a module supply just its detector and adapter, so the trivial validator is
    defined once rather than reimplemented per kind.
    """
    register_kind(detector, lambda doc, doc_path=None, schema_url=None: _model_findings(doc, adapter))


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


def finding(
    validator: str,
    *,
    rule: str | None = None,
    message_id: str,
    kind: str,
    path: str,
    message: str,
) -> dict:
    """One thing a check said about one document (`rules/SCHEMA.md`, "Findings").

    `validator` is the transitional category (see `VALIDATOR_IDS` above);
    `rule`, when given, is the id of the record the finding concerns, resolved
    through the same `rule_by_id` a rejection raised via `rules.violation`
    resolves through, so the two never disagree about what an id names. A
    `fail` finding's `severity` is derived from that record — never accepted
    as a literal — and is `error` for the two framework cases that fail with
    no rule to name (`rule=None`). A `notApplicable` or `informational`
    finding carries no `severity` at all.
    """
    if validator not in VALIDATOR_IDS:
        raise ValueError(f"unknown validator id: {validator!r}")
    if kind not in _KINDS:
        raise ValueError(f"unknown kind: {kind!r}")
    record = None
    if rule is not None:
        from analitiq.contracts.shared.rules import rule_by_id
        try:
            record = rule_by_id(rule)
        except KeyError:
            raise ValueError(f"unknown rule id: {rule!r}") from None
    result: dict = {"validator": validator}
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

    A rejection raised through `rules.violation` carries its `rule_id` and
    `message_id` as attributes on a `RuleViolation`. Pydantic re-wraps that
    raised `ValueError` — prefixing the rendered message with `"Value error, "`
    and flattening its `err["type"]` to the generic `"value_error"` — but
    preserves the original exception at `err["ctx"]["error"]`, which is where
    this reads the two back rather than parsing the wrapped string pydantic
    itself defeats. A field constraint pydantic enforces on its own — no
    `violation` call behind it — carries no such context, and the finding's
    `rule` is `None` for it (`rules/SCHEMA.md`'s "a field constraint on a
    contract model rejected, and no record claims it"); its `message_id` is
    pydantic's own error-type string (`err["type"]`, e.g. `"missing"`,
    `"string_pattern_mismatch"`) — an existing, already-stable vocabulary
    reused rather than a second one invented beside it.
    """
    from analitiq.contracts.shared.rules import RuleViolation
    try:
        adapter.validate_python(doc)
        return []
    except ValidationError as exc:
        findings: list[dict] = []
        for err in exc.errors():
            path = "/" + "/".join(str(p) for p in err["loc"])
            original = err.get("ctx", {}).get("error")
            if isinstance(original, RuleViolation):
                rule, message_id = original.rule_id, original.message_id
            else:
                rule, message_id = None, err["type"]
            findings.append(finding(
                "contract-model",
                rule=rule,
                message_id=message_id,
                kind="fail",
                path=path,
                message=err["msg"],
            ))
        return findings


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def validate_document(doc: Any, doc_path: Path | None = None,
                      schema_url: str | None = None) -> list[dict]:
    """Detect the document kind, validate via its model, add cross-file checks.

    `schema_url` is a direction hint for a type-map array whose filename is
    ambiguous (a caller passing `--schema-url .../type-map-write/latest.json`
    from a temp file): it disambiguates read vs write when the filename can't.
    """
    return _run_guarded(_dispatch, doc, doc_path, schema_url, vid="contract-model")


def _dispatch(doc: Any, doc_path: Path | None, schema_url: str | None = None) -> list[dict]:
    for detector, validator in _KIND_REGISTRY:
        if detector(doc):
            return validator(doc, doc_path, schema_url)
    # Anything no registered kind claims is a document we were asked to validate
    # but cannot identify — that is a validation failure, not a pass.
    return [finding(
        "document", message_id="unrecognized-document", kind="fail", path="/",
        message=(
            "document does not match any known artifact (connector / api-endpoint / "
            "database-endpoint / type-map / connection / stream / pipeline); a connector "
            "must declare 'kind', an api-endpoint 'operations', a type-map is a JSON array "
            "of rules, a connection a 'connector_id', a stream 'source' + 'destinations', "
            "a pipeline 'connections'."))]


def _run_guarded(fn: Callable, *args, vid: str) -> list[dict]:
    """Run a check; a crash becomes one finding so other checks survive.

    `notApplicable`, not `fail`: the crash means nothing here decided whether
    any rule the check would have graded holds, which is exactly what that
    kind reports. Naming no `rule` (the crash is not attributable to one
    check's obligation) keeps it inside the framework's own no-rule case and
    off the "clears the bar" list `passed` reduces over, so it always costs —
    matching what an unconditional `severity: error` finding always did here.
    """
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        return [finding(
            vid, message_id="check-crashed", kind="notApplicable", path="",
            message=(
                f"check {vid!r} crashed unexpectedly ({type(exc).__name__}: {exc}); "
                "this is a validator bug — please report."))]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an Analitiq connector/endpoint/type-map document.")
    parser.add_argument("--document", required=True, help="Path to the JSON document to validate.")
    # Accepted for backward compatibility with existing invocations. Validation
    # is now always model-driven and offline, so these are no-ops.
    parser.add_argument("--schema-url", help="Used only as a read/write direction hint for an "
                        "ambiguously-named type-map array; otherwise not fetched (validation is model-driven).")
    parser.add_argument("--semantic-only", action="store_true", help="(ignored) always offline now.")
    parser.add_argument("--json-only", action="store_true", help="(ignored) always offline now.")
    parser.add_argument("--no-cache", action="store_true", help="(ignored) no schema cache.")
    args = parser.parse_args()

    document_path = Path(args.document)
    try:
        document = json.loads(document_path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        # OSError subsumes FileNotFoundError / IsADirectoryError / PermissionError,
        # so an unreadable document always yields the finding + exit 1 (never a
        # bare traceback), matching _load_type_map and the documented contract.
        print(json.dumps({"passed": False, "findings": [finding(
            "document", message_id="unreadable-document", kind="fail", path="",
            message=f"Cannot read document: {exc}")]}))
        return 1

    findings = validate_document(document, doc_path=document_path.resolve(), schema_url=args.schema_url)
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
