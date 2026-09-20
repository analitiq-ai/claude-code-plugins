"""Framework for the Analitiq artifact validator.

This module owns the parts that are independent of any particular artifact kind:

- `finding()`, the one construction point for every finding this package emits;
- `_model_findings()` — validate a document against a Pydantic contract model and
  map each error to a finding (the single source of single-document validity,
  reused by every kind);
- `document_pointer()` — the document location a pydantic error names, which
  its `loc` does not spell directly; shared with the pipeline plugin's adapter;
- `contract_model_domain()` — the env guard every kind imports its contract models
  under, defined once so the DOMAIN dance is not reimplemented per kind;
- the KIND-VALIDATOR REGISTRY and `validate_document()` driver — a per-kind
  module (e.g. `connectors`) contributes a validator under the kind name it owns
  via `register_kind()`, and `validate_document` looks the caller's kind up in it
  rather than hard-coding any kind's branches, so a new kind is *register, done*. A kind whose validity is its contract model plus the
  `$schema`-omission check registers via `register_model_and_schema_kind()`
  instead of hand-writing that combination;
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
  `main()` and `analitiq.validator.document_set` answer "did this document
  pass" identically;
- the `main()` CLI: read the document, validate it as the kind its caller named,
  print `{"passed", "findings"}`, exit 0 iff `_passed()` says so (1 on a failing
  document / unreadable document; 2 on CLI usage errors, which is what an
  unnamed kind is).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterator

from pydantic import TypeAdapter, ValidationError

from ._location import Location, located

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


# The kind registry: one validator per kind a caller can submit a document as.
# A validator takes `(doc, location)` and returns a list of findings. The names
# are supplied by the per-kind modules rather than listed here, so the
# vocabulary has one owner and this module never names a kind of its own;
# `packages/validator/tests/test_document_kind.py` holds the registered names to
# `analitiq.contracts.validation_requests.DOCUMENT_SCHEMA_NAMES` plus the
# assembled-run kind no published document schema names.
_Validator = Callable[[Any, "Location | None"], list[dict]]
_KIND_VALIDATORS: dict[str, _Validator] = {}


def register_kind(kind: str, validator: _Validator) -> None:
    """Bind `kind` to the validator that grades a document submitted under it.

    A second registration of one name is a packaging defect — two modules each
    believing they own the kind — so it raises here rather than letting import
    order decide which validator a caller gets.
    """
    if kind in _KIND_VALIDATORS:
        raise ValueError(f"document kind {kind!r} is already registered")
    _KIND_VALIDATORS[kind] = validator


def document_kinds() -> set[str]:
    """The document kinds `validate_document` grades, for a caller that offers
    them as a choice — the CLI's `--kind`, a hosted validator's request
    schema — rather than restating the vocabulary beside this one."""
    return set(_KIND_VALIDATORS)


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
    as a literal — and is `error` for a framework case that fails with no
    rule to name (`rule=None`). A `notApplicable` or `informational`
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


def is_bare_pointer(path: str) -> bool:
    """Whether `path` points into the validated document itself — a JSON
    Pointer, empty or starting with `/` — rather than naming another document
    (`rules/SCHEMA.md`, "Findings", `path`)."""
    return path == "" or path.startswith("/")


def qualified(f: dict, reference: str) -> dict:
    """`f`, reported about the document at `reference` rather than the one
    validated: its pointer follows the reference, after the one `#` the
    reference cannot carry.

    Raises `ValueError` for a finding already naming a document: its pointer
    is into that document, and naming a second one in front of it would point
    nowhere.
    """
    if not is_bare_pointer(f["path"]):
        raise ValueError(f"finding already names a document: {f['path']!r}")
    return {**f, "path": f"{reference}#{f['path']}"}


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

#: Core schema kinds that validate their one inner `schema` without adding a
#: `loc` token of their own.
_TRANSPARENT = frozenset({
    "definitions", "model", "default", "nullable",
    "function-after", "function-before", "function-wrap",
})
#: Core schema kinds with no members for a `loc` token to name.
_LEAVES = frozenset({"str", "int", "float", "bool", "literal", "any", "enum"})


def document_pointer(loc: tuple, schema: dict) -> str:
    """The JSON Pointer to the member a pydantic error at `loc` concerns, in
    the document validated against the core `schema`.

    `loc` is the path the validator took, not a path through the document: a
    tagged union adds the tag it dispatched on, a smart union the label of
    the choice it tried, and a rejected mapping key a trailing `[key]`, and
    none of them is a member. Only the schema tells them apart — a union
    discriminated by the key it carries has a tag equal to a member name — so
    the walk follows `loc` through it rather than through the document.

    Raises `ValueError` for a `loc` the schema cannot account for, and
    `TypeError` when the schema uses a core schema kind the walk does not
    know, wherever it sits and not only along `loc`.
    """
    from analitiq.contracts.shared.json_schema import pointer_position
    refs = {node["ref"]: node for node in _schema_nodes(schema) if "ref" in node}
    members = _members(schema, tuple(loc), refs)
    if members is None:
        raise ValueError(f"error location {loc!r} does not follow the model's schema")
    return pointer_position(members)


def _schema_nodes(node: dict) -> Iterator[dict]:
    yield node
    for child in _schema_children(node):
        yield from _schema_nodes(child)


def _schema_children(node: dict) -> Iterator[dict]:
    kind = node["type"]
    if kind == "definitions":
        yield from node["definitions"]
    if kind in _TRANSPARENT:
        yield node["schema"]
    elif kind == "model-fields":
        yield from (field["schema"] for field in node["fields"].values())
    elif kind == "list":
        yield node["items_schema"]
    elif kind == "tuple":
        yield from node["items_schema"]
    elif kind == "dict":
        yield from (node[k] for k in ("keys_schema", "values_schema") if k in node)
    elif kind == "tagged-union":
        yield from node["choices"].values()
    elif kind == "union":
        yield from (_choice_schema(choice) for choice in node["choices"])
    elif kind not in _LEAVES and kind != "definition-ref":
        raise TypeError(f"core schema kind {kind!r} is not walked for error locations")


def _choice_schema(choice: dict | tuple) -> dict:
    # A union choice is a schema, or a `(schema, label)` pair when labelled.
    return choice[0] if isinstance(choice, tuple) else choice


def _members(node: dict, loc: tuple, refs: dict[str, dict]) -> list | None:
    """The member tokens of `loc` below `node`, or `None` when `loc` does not
    follow it."""
    if not loc:
        return []
    kind = node["type"]
    token, rest = loc[0], loc[1:]
    if kind == "definition-ref":
        return _members(refs[node["schema_ref"]], loc, refs)
    if kind in _TRANSPARENT:
        return _members(node["schema"], loc, refs)
    if kind == "model-fields":
        field = _field_at(node, token)
        if field is None:
            # A key the model declares no field for: `extra_forbidden`.
            return None if rest else [token]
        return _below(token, _members(field["schema"], rest, refs))
    if kind == "list" and isinstance(token, int):
        return _below(token, _members(node["items_schema"], rest, refs))
    if kind == "tuple" and isinstance(token, int):
        items = node["items_schema"]
        variadic = node.get("variadic_item_index")
        item = items[min(token, variadic)] if variadic is not None else (
            items[token] if token < len(items) else None)
        return None if item is None else _below(token, _members(item, rest, refs))
    if kind == "dict":
        if rest == ("[key]",):
            return [token]
        return _below(token, _members(node["values_schema"], rest, refs))
    if kind == "tagged-union":
        choice = node["choices"].get(token)
        return None if choice is None else _members(choice, rest, refs)
    if kind == "union":
        # pydantic renders a choice's label itself and does not expose it, so
        # the choice is the one the rest of `loc` follows.
        for choice in node["choices"]:
            members = _members(_choice_schema(choice), rest, refs)
            if members is not None:
                return members
        return None
    if kind in _LEAVES or kind in ("list", "tuple"):
        return None
    raise TypeError(f"core schema kind {kind!r} is not walked for error locations")


def _field_at(node: dict, token: Any) -> dict | None:
    """The field `token` names: a field is addressed by its alias when it has one."""
    for name, field in node["fields"].items():
        if field.get("validation_alias", name) == token:
            return field
    return None


def _below(token: Any, members: list | None) -> list | None:
    return None if members is None else [token, *members]

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
    its `path` extending the pointer `document_pointer` builds from
    `err["loc"]` when it set one.

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
            base_path = document_pointer(err["loc"], adapter.core_schema)
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


def model_and_schema_findings(doc: Any, adapter: TypeAdapter) -> list[dict]:
    """A document graded against its contract model and the RULE-SHRD-003
    `$schema`-omission check — what every kind whose contract leaves `$schema`
    optional owes, whatever else it also checks."""
    return _model_findings(doc, adapter) + _missing_schema_url_findings(doc)


def register_model_and_schema_kind(kind: str, adapter: TypeAdapter) -> None:
    """Register a single-document kind whose entire validity is
    `model_and_schema_findings`.

    A kind with no further cross-file or referential checks needs only that pair
    under the per-kind `(doc, location)` signature. Packaging it here lets such a
    module supply just its name and adapter.
    """
    def _validate(doc: Any, location: Location | None = None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
        return model_and_schema_findings(doc, adapter)
    register_kind(kind, _validate)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def validate_document(doc: Any, kind: str,
                      doc_path: Path | Location | None = None) -> list[dict]:
    """Validate `doc` as `kind`: its contract model plus that kind's cross-file
    checks.

    `kind` is the name a caller submits a document under, and it is the only
    thing that decides how the document is graded — nothing here reads the
    document to work out what it is. A submitted document's content is the least reliable way to
    identify it, because the defects worth reporting are the ones that stop it
    resembling its own kind: a connector that omits the field naming its family
    needs to be told about that field, not told it is a document of some other
    kind with a defect it does not have.

    A `kind` no module registered raises: the caller named something outside the
    vocabulary, which is an error in the caller and not a verdict on a document
    that may be perfectly valid. A `doc_path` that `located` refuses raises its
    refusal for the same reason — a finding would come from the crash guard,
    which reports a validator bug.
    """
    try:
        validator = _KIND_VALIDATORS[kind]
    except KeyError:
        raise ValueError(
            f"unknown document kind {kind!r}; the kinds this validator grades are "
            f"{sorted(_KIND_VALIDATORS)}") from None
    location = None if doc_path is None else located(doc_path)
    return _run_guarded(validator, doc, location, crash_label="document validation")


def _run_guarded(fn: Callable, *args, crash_label: str, rule: str | None = None) -> list[dict]:
    """Run a check; a crash becomes one finding so other checks survive.

    `crash_label` is keyword-only and named apart from any parameter a
    wrapped `fn` might itself take — a same-named keyword here would be
    consumed by this function instead of reaching `fn`, silently dropping the
    caller's intent.

    `notApplicable`, not `fail`: the crash means nothing here decided whether
    any rule the check would have graded holds, which is exactly what that
    kind reports. `rule`, when the caller names one, is the obligation a
    crash mid-check leaves unevaluated — a caller wrapping a check bound to
    exactly one rule (`_embedded_schema_example_findings` and RULE-ENDP-063,
    say) passes it so the crash stays routable to it; a caller wrapping a
    whole kind's grading (`validate_document`, which could
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
# Document text
# ---------------------------------------------------------------------------

#: What `json.loads` raises for text it will not parse. `JSONDecodeError`, an
#: integer past the interpreter's digit limit and a `UnicodeDecodeError` on the
#: read are all `ValueError`; nesting past the recursion limit is a
#: `RecursionError`, which is a `RuntimeError` and escapes a `ValueError` arm.
_JSON_TEXT_REFUSALS = (ValueError, RecursionError)

#: What reading a JSON document off disk raises for what the file holds rather
#: than for this package: the path's own failure, a refusal of its text, or
#: `located`'s refusal of the path.
_JSON_READ_ERRORS = (OSError, *_JSON_TEXT_REFUSALS)


def _unreadable_document_finding(exc: Exception) -> dict:
    """The finding for a document whose text could not be read or parsed at
    all, so no check naming a rule ever ran on it."""
    return finding(
        message_id="unreadable-document", kind="fail", path="",
        message=f"Cannot read document: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one Analitiq artifact document.")
    parser.add_argument("--document", required=True, help="Path to the JSON document to validate.")
    # Required, and offered as `choices` read off the registry: the caller says
    # what it submitted, so a mistyped kind is refused as the usage error it is
    # rather than silently grading the document as something else.
    parser.add_argument("--kind", required=True, choices=sorted(document_kinds()),
                        help="The kind to grade the document as.")
    args = parser.parse_args()

    try:
        # Read as given, so a path the kernel refuses is refused for the
        # kernel's own reason, then located, which refuses a path it would
        # grade somewhere other than where that read opened it.
        document = json.loads(Path(args.document).read_text())
        location = located(Path(args.document))
    except _JSON_READ_ERRORS as exc:
        print(json.dumps({"passed": False, "findings": [_unreadable_document_finding(exc)]}))
        return 1

    findings = validate_document(document, args.kind, doc_path=location)
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
