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
- `_run_guarded()` — a crash in one check becomes a single error finding so the
  others survive;
- the `main()` CLI: read the document, validate, print `{"passed", "findings"}`,
  exit 0 iff no error-severity finding (1 on error findings / unreadable document;
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

# The set of legal validator ids. The framework owns `contract-model` (emitted by
# `_model_findings`) and `document` (the unrecognized-artifact verdict); each
# per-kind module contributes its own ids via `register_validator_ids`.
VALIDATOR_IDS: set[str] = {"contract-model", "document"}


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


def finding(validator: str, severity: str, path: str, message: str) -> dict:
    if validator not in VALIDATOR_IDS:
        raise ValueError(f"unknown validator id: {validator!r}")
    if severity not in ("error", "warning"):
        raise ValueError(f"unknown severity: {severity!r}")
    return {"validator": validator, "severity": severity, "path": path, "message": message}


# ---------------------------------------------------------------------------
# Model validation — the single source of single-document validity
# ---------------------------------------------------------------------------

def _model_findings(doc: Any, adapter: TypeAdapter) -> list[dict]:
    """Validate `doc` against a contract model; map each error to a finding."""
    try:
        adapter.validate_python(doc)
        return []
    except ValidationError as exc:
        findings: list[dict] = []
        for err in exc.errors():
            path = "/" + "/".join(str(p) for p in err["loc"])
            findings.append(finding("contract-model", "error", path, err["msg"]))
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
    # `document`, not `contract-model`: a crash anywhere under the dispatch is
    # not a pydantic rejection, and reporting one under the id that means
    # "this document failed its contract model" hands an agent filtering for
    # model errors a crash to render as one. `document` is the id for "there
    # is no verdict about this document", which is what a crash leaves.
    return _run_guarded(_dispatch, doc, doc_path, schema_url,
                        vid="document", path="/")


def _dispatch(doc: Any, doc_path: Path | None, schema_url: str | None = None) -> list[dict]:
    for detector, validator in _KIND_REGISTRY:
        if detector(doc):
            return validator(doc, doc_path, schema_url)
    # Anything no registered kind claims is a document we were asked to validate
    # but cannot identify — that is a validation failure, not a pass.
    return [finding("document", "error", "/",
                    "document does not match any known artifact (connector / api-endpoint / "
                    "database-endpoint / type-map / connection / stream / pipeline); a connector "
                    "must declare 'kind', an api-endpoint 'operations', a type-map is a JSON array "
                    "of rules, a connection a 'connector_id', a stream 'source' + 'destinations', "
                    "a pipeline 'connections'.")]


def _run_guarded(
    fn: Callable, *args, vid: str, path: str = "", blame: str | None = None,
    scope: str | None = None,
) -> list[dict]:
    """Run a check; a crash becomes one error finding so other checks survive.

    The message has two halves, and they are answered by different things.

    The CAUSE — what went wrong and who can fix it — is decided by the
    exception TYPE, never by reading a message. `TypeError`, `AttributeError`
    and their kin are how code goes wrong, not how a document does, so they
    are always this tool's to answer for whatever the caller believes.
    Running out of stack or memory is always the document's size: every walk
    here recurses on the document's own nesting, so a deep enough one
    exhausts the stack in whichever walk reaches it first — including walks no
    guard wraps, which land on the dispatch guard where the default would send
    the author to file a bug about their own document. What is left is a
    check brought down by the content it was reading, and there the caller
    knows more than the type does — a value a keyword cannot compute against
    is the document's doing, and only the code that ran it can say what the
    author should look at. That is what `blame` is for. It has to be true of
    every exception that reaches this clause: running out of room is
    intercepted above, so a `blame` naming a `$ref` that leads back to itself
    describes an outcome it can never carry.

    The SCOPE — which slot was being checked and what survived — is the
    caller's alone, and is appended whatever the cause turns out to be. It was
    folded into `blame` once, which made every caller's scope sentence
    conditional on their guess about the cause being right: a coding defect in
    a check reported as "this document nests too deep" AND lost the only line
    naming which endpoint it happened on.

    `path` locates the crash. A check that runs per-slot has more than one
    place to crash, and without it every one of them reports the same finding.
    """
    try:
        return fn(*args)
    except _TOOL_DEFECT_TYPES as exc:
        return [guard_finding(vid, path, _detail(exc), GUARD_DEFAULT_BLAME, scope)]
    except (RecursionError, MemoryError) as exc:
        # No detail beyond the type: a `RecursionError` raised while unwinding
        # names whichever frame was innermost, which is not the walk that
        # filled the stack and reads to an author as though it were.
        return [guard_finding(vid, path, type(exc).__name__,
                               GUARD_RESOURCE_BLAME, scope)]
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        return [guard_finding(vid, path, _detail(exc),
                               blame or GUARD_DEFAULT_BLAME, scope)]


#: How code goes wrong, as opposed to how a document does. A check meeting one
#: of these has a defect in it whatever it was reading, so no caller's reading
#: of its own failure modes applies — the author is told to report it.
_TOOL_DEFECT_TYPES = (
    TypeError, AttributeError, KeyError, IndexError, NameError, ImportError,
)


def _detail(exc: BaseException) -> str:
    """The exception's own text, bounded.

    Truncated because `jsonschema`'s referencing errors embed the whole schema
    root in their repr, which on a real endpoint is kilobytes of JSON inside
    one finding an agent has to read.
    """
    detail = f"{type(exc).__name__}: {exc}"
    if len(detail) > _GUARD_DETAIL_MAX:
        detail = detail[:_GUARD_DETAIL_MAX] + "…"
    return detail


def guard_finding(
    vid: str, path: str, detail: str, cause: str, scope: str | None = None,
) -> dict:
    """A finding that says a check could not finish, in the one shape
    :func:`is_guard_finding` recognises.

    Public because this package is not the only thing that runs a check and
    meets a crash — the plugins drive contract models directly for the kinds
    the validator has no entry point for. A second emitter writing the wording
    by hand produces something that reads like a guard finding to a person and
    like a REJECTION to `is_guard_finding`, which is how an undecided document
    gets counted as a refused one.
    """
    tail = f"{cause} {scope}" if scope else cause
    return finding(vid, "error", path, f"{_guard_opening(vid)} ({detail}); {tail}")


#: The halves of the guard's message. Private, and read only by
#: :func:`_guard_opening` below, which is the single statement of the wording
#: that both writes a guard finding and recognises one. Nothing outside this
#: module matches on the text: a consumer asks :func:`is_guard_finding`.
#: That is not a style preference — the wording was matched by hand once, and
#: renaming it from "crashed unexpectedly" left a tripwire matching nothing
#: and firing on no document, silently, until something else found it.
_GUARD_PREFIX = "check"
_GUARD_VERB = "could not finish"


def _guard_opening(vid: str) -> str:
    """The opening a guard finding is written with, and recognised by.

    One statement of it, used by the writer and by :func:`is_guard_finding`,
    so recognition cannot drift from emission: a message assembled here and
    matched by two loose substrings elsewhere would also match a real finding
    whose text happens to begin "checked" and contain the verb. That is why
    the two halves below it are private — matching them is not a thing a
    consumer should be doing.
    """
    return f"{_GUARD_PREFIX} {vid!r} {_GUARD_VERB}"


#: What a crash tells the reader to do when the caller names nothing better.
#: Stated here because the tests over it assert its ABSENCE — that a
#: document-caused crash does not send the author to file a bug — and an
#: absence assertion against a hand-typed string goes quiet, not red, when the
#: string moves. Tests also require it where it does belong, so emptying it is
#: not silent either.
GUARD_DEFAULT_BLAME = "this is a validator bug — please report."
#: What a crash tells the reader when the interpreter ran out of stack or
#: memory and the caller named nothing better. No check here allocates or
#: recurses on anything but the document, so the document is what was too big.
#: Claims no scope: this is the default a caller did not override, so it does
#: not know what else in the run survived.
#: Its first clause names the cause on its own, because the plugin README
#: renders that clause as the literal text a reader matches a finding against.
#: Stated as its own constant rather than cut out of the sentence: a `.` added
#: anywhere earlier — an abbreviation, a reordering — would silently render a
#: fragment, and the generated-block check compares the file to the renderer,
#: so both would move together and nothing would go red.
GUARD_RESOURCE_CAUSE = (
    "the document nests deeper, or runs larger, than this tool can walk"
)
GUARD_RESOURCE_BLAME = (
    f"{GUARD_RESOURCE_CAUSE}. Nothing was decided about it either way; "
    "flattening the nesting is what gets it checked."
)
#: How much of an exception's own text a finding carries.
_GUARD_DETAIL_MAX = 300


def is_guard_finding(item: dict) -> bool:
    """Whether `item` is a check that could not finish, rather than a verdict.

    A crash proves nothing in either direction — not that the document is
    good, and not that the rejection a caller was expecting happened.
    """
    message = item.get("message", "")
    vid = item.get("validator")
    if not isinstance(vid, str):
        return False
    return message.startswith(_guard_opening(vid))


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
        print(json.dumps({"passed": False, "findings": [
            finding("document", "error", "", f"Cannot read document: {exc}")]}))
        return 1

    findings = validate_document(document, doc_path=document_path.resolve(), schema_url=args.schema_url)
    passed = all(f["severity"] != "error" for f in findings)
    print(json.dumps({"passed": passed, "findings": findings}, indent=2))
    return 0 if passed else 1
