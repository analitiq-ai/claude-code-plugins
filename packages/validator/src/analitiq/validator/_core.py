"""Framework for the Analitiq artifact validator.

This module owns the parts that are independent of any particular artifact kind:

- `finding()` and the `VALIDATOR_IDS` registry every check emits through, and
  `diagnostics()`, the one envelope every entry point returns;
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
- the ENTITY REGISTRY — the explicit routes `validate_document(..., entity=...)`
  takes when the caller already knows what a document is meant to be, so a
  document missing its discriminating key is graded by the model it was authored
  against instead of collapsing into "unrecognised";
- `_bounded()` — the one width every borrowed diagnostic is clipped to, so a
  finding is bounded the same way whichever route the text arrived by;
- `_run_guarded()` — a crash in one check becomes a single error finding so the
  others survive — and `_contained()`, the same containment for one stage of a
  multi-document run, reported under `adapter-crash`;
- the `main()` CLI: read the document, validate, print the envelope, exit 0 iff
  no error-severity finding (1 on error findings / unreadable document; 2 on CLI
  usage errors).

A per-kind validator takes `(doc, where, schema_url)`: `where` is the document's
`Location` in a tree (`_tree`), or None for a bare document. The tree is what a
cross-file check reads its siblings from — an in-memory one for `validate_tree`,
a disk-backed one for `validate_document(..., doc_path=...)`.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from pydantic import TypeAdapter, ValidationError

from ._tree import Location, disk_location

# The set of legal validator ids. The framework owns `contract-model` (emitted by
# `_model_findings`), `document` (the unrecognized-artifact verdict) and
# `adapter-crash` (a stage that was not evaluated); each per-kind module
# contributes its own ids via `register_validator_ids`.
VALIDATOR_IDS: set[str] = {"contract-model", "document", "adapter-crash"}


def register_validator_ids(ids: set[str]) -> None:
    """A per-kind module declares the validator ids its findings may carry."""
    VALIDATOR_IDS.update(ids)


# The kind registry: ordered `(detector, validator_fn)` pairs. `_dispatch` runs
# each detector in registration order and hands the document to the first
# validator whose detector matches. A validator takes `(doc, where, schema_url)`
# and returns a list of findings.
_Validator = Callable[[Any, "Location | None", "str | None"], list[dict]]
_KIND_REGISTRY: list[tuple[Callable[[Any], bool], _Validator]] = []

#: The explicit routes `validate_document(..., entity=...)` accepts, in the order
#: a caller's CLI lists them. Each is a kind an author names when validating one
#: file; the per-kind modules bind them through `register_entity`.
ENTITIES = ("pipeline", "stream", "connection", "database_endpoint",
            "type_map_read", "type_map_write")
_ENTITY_REGISTRY: dict[str, _Validator] = {}


def register_kind(detector: Callable[[Any], bool], validator: _Validator) -> None:
    """Append a `(detector, validator_fn)` pair to the dispatch registry."""
    _KIND_REGISTRY.append((detector, validator))


def register_entity(entity: str, validator: _Validator) -> None:
    """Bind one of `ENTITIES` to the validator its explicit route runs."""
    if entity not in ENTITIES:
        raise ValueError(f"unknown entity {entity!r}; expected one of {ENTITIES}")
    _ENTITY_REGISTRY[entity] = validator


def register_model_kind(detector: Callable[[Any], bool], adapter: TypeAdapter,
                        *, entity: str | None = None) -> None:
    """Register a single-document kind whose entire validity is its contract model.

    A kind with no cross-file or referential checks — the contract model IS the
    whole validity story — needs only `_model_findings(doc, adapter)` under the
    per-kind `(doc, where, schema_url)` signature. Packaging that here lets such
    a module supply just its detector and adapter, so the trivial validator is
    defined once rather than reimplemented per kind. Naming an `entity` binds
    the same validator to that explicit route.
    """
    def validator(doc: Any, where: Location | None = None, schema_url: str | None = None) -> list[dict]:  # skipcq: PYL-W0613 — uniform registered-validator signature
        return _model_findings(doc, adapter)

    register_kind(detector, validator)
    if entity is not None:
        register_entity(entity, validator)


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


def finding(validator: str, severity: str, path: str, message: str) -> dict:
    if validator not in VALIDATOR_IDS:
        raise ValueError(f"unknown validator id: {validator!r}")
    if severity not in ("error", "warning"):
        raise ValueError(f"unknown severity: {severity!r}")
    return {"validator": validator, "severity": severity, "path": path, "message": message}


def diagnostics(findings: Iterable[dict]) -> dict:
    """The envelope every entry point returns: `passed` is true only when no
    finding has severity `error` — a warning never fails a run."""
    findings = list(findings)
    return {"passed": all(f["severity"] != "error" for f in findings), "findings": findings}


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
                      schema_url: str | None = None, *,
                      entity: str | None = None) -> list[dict]:
    """Validate one document: via its model, plus the cross-file checks its
    siblings on disk allow.

    With no `entity` the kind is detected from the document's shape. An `entity`
    from `ENTITIES` routes explicitly instead — the document is graded by the
    model it was authored against even when it lacks the key shape detection
    would key off, and a type map is graded in the entity's direction.

    `doc_path` anchors the document in a disk-backed tree, which is what the
    cross-file checks read siblings from. `schema_url` is a direction hint for a
    type-map array whose filename is ambiguous (a caller passing
    `--schema-url .../type-map-write/latest.json` from a temp file): it
    disambiguates read vs write when the filename can't.
    """
    if entity is not None and entity not in _ENTITY_REGISTRY:
        raise ValueError(f"unknown entity {entity!r}; expected one of {ENTITIES}")
    where = None if doc_path is None else disk_location(Path(doc_path))
    return _validate_at(doc, where, schema_url, entity=entity)


def _validate_at(doc: Any, where: Location | None, schema_url: str | None = None,
                 *, entity: str | None = None) -> list[dict]:
    """`validate_document` over a location in any tree."""
    validator = _dispatch if entity is None else _ENTITY_REGISTRY[entity]
    return _run_guarded(validator, doc, where, schema_url, vid="contract-model")


def _dispatch(doc: Any, where: Location | None, schema_url: str | None = None) -> list[dict]:
    for detector, validator in _KIND_REGISTRY:
        if detector(doc):
            return validator(doc, where, schema_url)
    # Anything no registered kind claims is a document we were asked to validate
    # but cannot identify — that is a validation failure, not a pass.
    return [finding("document", "error", "/",
                    "document does not match any known artifact (connector / api-endpoint / "
                    "database-endpoint / type-map / connection / stream / pipeline); a connector "
                    "must declare 'kind', an api-endpoint 'operations', a type-map is a JSON array "
                    "of rules, a connection a 'connector_id', a stream 'source' + 'destinations', "
                    "a pipeline 'connections'.")]


def _run_guarded(fn: Callable, *args, vid: str) -> list[dict]:
    """Run a check; a crash becomes one error finding so other checks survive."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        return [finding(vid, "error", "",
                        f"check {vid!r} crashed unexpectedly ({type(exc).__name__}: {exc}); "
                        "this is a validator bug — please report.")]


# ---------------------------------------------------------------------------
# Stage containment — a multi-document run reports every stage it could decide
# ---------------------------------------------------------------------------

def _crash_finding(path: str, exc: BaseException) -> dict:
    """The finding every containment site emits: a guard fired and the document
    was not evaluated for that stage. `str(exc)` is empty for some exceptions (a
    bare `MemoryError()`), so the detail is only appended when there is one,
    never leaving a dangling `: `. An exception whose own `__str__` raises must
    not become a second, unguarded crash inside a guard, so that failure is
    swallowed too."""
    try:
        detail = str(exc)
    except Exception:  # noqa: BLE001 - the guard must not itself crash
        detail = ""
    message = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    return finding("adapter-crash", "error", path, message)


class _Outcome:
    """Mutable result of one `_contained` block, readable by the caller after
    the `with` exits — the only way to tell a block that ran clean from one a
    crash cut short, since the exception itself never escapes."""

    def __init__(self) -> None:
        self.crashed = False


@contextlib.contextmanager
def _contained(findings: list[dict], path: str) -> Iterator[_Outcome]:
    """Run one independently-decidable stage. Any exception besides
    `MemoryError` becomes one `adapter-crash` finding and the walk continues
    past it. `MemoryError` re-raises: only the outermost guard of the process
    turns it into a finding, so a resource-exhaustion event yields exactly one
    finding rather than one per in-progress unit. Yields a `_Outcome` so a
    caller that must know whether this stage actually completed — e.g. before
    trusting a collection it fed into a downstream referential check — can
    check `.crashed` once the block exits."""
    outcome = _Outcome()
    try:
        yield outcome
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001 - containment is the point
        outcome.crashed = True
        findings.append(_crash_finding(path, exc))


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
        print(json.dumps(diagnostics([
            finding("document", "error", "", f"Cannot read document: {exc}")])))
        return 1

    envelope = diagnostics(validate_document(
        document, doc_path=document_path.resolve(), schema_url=args.schema_url))
    print(json.dumps(envelope, indent=2))
    return 0 if envelope["passed"] else 1
