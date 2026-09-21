"""Grading one document, or one package, as what its caller says it is.

`validate_single_document` grades a document's text as the published schema
its request names. `grade_package` grades a package's texts as the published
package schema it is handed the name of; `validate_package` hands it a
request's documents, and `validate_package_at` hands it what `read_package`
reads from a directory. Nothing reads
content to decide what it was handed: `PACKAGE_MODELS[package]` says which key is the package's root and
which kind each located key holds, and a key no location matches is not part
of the package and is not graded.

A package is graded in one order whatever order its documents arrive in: the
root's presence, then each located document in key order as its kind, then the
package's own cross-document check (`register_package_check`). Every finding
about a document names it by its percent-encoded key; a finding about the
package as a whole has an empty `path`.

**The request model is the argument gate.** A key outside the document-key
grammar, a value that is not text, a key that is also a directory of another,
a package past the document ceiling, an `entity` or `package` outside the
published names — each is a `pydantic.ValidationError` raised at construction,
so a malformed argument never becomes a finding. `bytes` and `bytearray`
holding UTF-8 pass that gate: pydantic decodes them to `str` in lax mode,
except that a byte-order mark survives the decoding, and a document starting
with one is reported as unreadable *content*. Document content is what this
module judges: unparseable text, a wrong shape, a contract-model failure or a
cross-document inconsistency is a finding, never a raised error.

An exception raised by this module's own assembly is a defect in this package:
it propagates. `_run_guarded` in `analitiq.validator._core` contains a crash
inside grading one document, or inside a package check, as a `check-crashed`
`notApplicable` finding.
"""
from __future__ import annotations

import errno
import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, TypedDict
from urllib.parse import quote

if TYPE_CHECKING:
    # Annotation-only, so importing this module never imports the contract
    # package: `connectors` guards its own contract-model imports to turn a
    # missing `analitiq-contract-models` into a structured diagnostic, and an
    # unconditional import here would pre-empt that guard with a traceback.
    from analitiq.contracts.shared.common import DocumentPackage
    from analitiq.contracts.validation_requests import (
        ValidatePackageRequest,
        ValidateSingleDocumentRequest,
    )


class _FindingRequired(TypedDict):
    """`finding()` sets every key below unconditionally on every result it
    builds — see `Finding`."""

    message_id: str
    kind: Literal["fail", "notApplicable", "informational"]
    path: str
    message: str


class Finding(_FindingRequired, total=False):
    """One entry of a `ValidationEnvelope`'s `findings` list — the shape
    `rules/SCHEMA.md`'s "Findings" section defines and
    `analitiq.validator.finding` constructs. Restated here only so this
    module's signatures are checkable; `test_packages.py::
    test_finding_matches_the_keys_finding_builder_produces` pins the restated
    keys, and which of them are required, to what `finding()` produces."""

    rule: str
    severity: Literal["error", "warning"]


class ValidationEnvelope(TypedDict):
    """The result of every entry point below. `passed` is `False` exactly
    when `findings` holds one that `finding_costs_a_pass` accepts, which is not
    the same as "a finding at `severity: error`": an unchecked error-tier rule
    costs a pass too."""

    passed: bool
    findings: list[Finding]


def _envelope(findings: list[Finding]) -> ValidationEnvelope:
    from analitiq.validator._core import _passed
    return {"passed": _passed(findings), "findings": findings}


# ---------------------------------------------------------------------------
# Package checks
# ---------------------------------------------------------------------------

#: A package's cross-document check: handed every located document that parsed,
#: by key, and the located keys that did not, it returns `(key, finding)` pairs
#: whose pointer is into the document at `key`.
PackageCheck = Callable[[dict[str, Any], frozenset[str]], list[tuple[str, dict]]]
_PACKAGE_CHECKS: dict[str, PackageCheck] = {}


def register_package_check(package: str, check: PackageCheck) -> None:
    """Bind `check` to `package`. Raises `ValueError` for a package already bound."""
    if package in _PACKAGE_CHECKS:
        raise ValueError(f"package {package!r} already has a check")
    _PACKAGE_CHECKS[package] = check


def keys_of(model: type[DocumentPackage], documents: dict[str, Any], kind: str) -> list[str]:
    """The keys of `documents` that `model` locates as `kind`, in key order."""
    return sorted(key for key in documents if model.kind_at(key) == kind)


def _package_model(package: str) -> type[DocumentPackage]:
    from analitiq.contracts.validation_requests import PACKAGE_MODELS
    model = PACKAGE_MODELS.get(package)
    if model is None:
        raise ValueError(f"unknown package {package!r}; expected one of {sorted(PACKAGE_MODELS)}")
    return model


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def validate_single_document(
        request: ValidateSingleDocumentRequest) -> ValidationEnvelope:
    """Grade one document's text as the published schema `request.entity`
    names. Text the JSON parser cannot read is a finding, not a raised error."""
    from analitiq.validator._core import (
        _JSON_TEXT_REFUSALS, _unreadable_document_finding, validate_document)

    try:
        document = json.loads(request.document)
    except _JSON_TEXT_REFUSALS as exc:
        return _envelope([_unreadable_document_finding(exc)])
    return _envelope(validate_document(document, request.entity))


def validate_package(request: ValidatePackageRequest) -> ValidationEnvelope:
    """Grade `request.documents` as the published package `request.package`."""
    return grade_package(request.package, dict(request.documents.root))


def validate_package_at(directory: Path, package: str) -> ValidationEnvelope:
    """Grade the files under `directory` as the published package `package`,
    as `read_package` reads them."""
    return grade_package(package, read_package(directory, package))


def read_package(directory: Path, package: str) -> dict[str, str | Exception]:
    """The text of every file under `directory` whose path from it is a
    location of the published package `package`, by that path.

    A file that cannot be read, or looked up, maps to the exception that
    raised. A directory the walk cannot list or look up raises its `OSError`,
    and so does an unlocated entry it cannot look up: what either holds is
    unknown, and no finding about a document can say so. A symlinked directory
    is walked under the path that reaches it, and a directory reached a second
    time is not walked again, which is what ends a symlink cycle. A dangling or
    looping link leads to nothing and is skipped. Raises `ValueError` for a
    package name outside the published ones — the caller's error, not the
    package's.
    """
    model = _package_model(package)
    texts: dict[str, str | Exception] = {}
    walked: set[tuple[int, int]] = set()
    pending = [Path(directory)]
    while pending:
        parent = pending.pop()
        identity = os.stat(parent)
        if (identity.st_dev, identity.st_ino) in walked:
            continue
        walked.add((identity.st_dev, identity.st_ino))
        # Not `os.walk` or `Path.is_file`: each answers a refused lookup as
        # "no directory" or "no file", or raises, depending on the interpreter.
        with os.scandir(parent) as entries:
            for entry in entries:
                path = Path(entry.path)
                key = path.relative_to(directory).as_posix()
                located = model.kind_at(key) is not None
                try:
                    mode = _mode(path)
                except OSError as exc:
                    if not located:
                        raise
                    texts[key] = exc
                    continue
                if mode is None:
                    continue
                if stat.S_ISDIR(mode):
                    pending.append(path)
                elif located and stat.S_ISREG(mode):
                    try:
                        texts[key] = path.read_text(encoding="utf-8")
                    except (OSError, ValueError) as exc:
                        texts[key] = exc
    return texts


def _mode(path: Path) -> int | None:
    """The mode of what `path` leads to, `None` where it leads to nothing."""
    try:
        return os.stat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None
        raise


def grade_package(package: str, texts: dict[str, str | Exception]) -> ValidationEnvelope:
    """Grade `texts` as the published package `package`, where a value that is
    an exception is a document that could not be read — the shape
    `read_package` returns. Raises `ValueError` for a package name outside the
    published ones."""
    from pydantic import TypeAdapter

    from analitiq.validator._core import (
        _JSON_TEXT_REFUSALS, _model_findings, _run_guarded, _unreadable_document_finding,
        qualified, validate_document)

    model = _package_model(package)
    located = sorted(key for key in texts if model.kind_at(key) is not None)
    # The package model refuses a set without its root; every key handed to it
    # is located, so the root is all it can refuse here.
    findings = _model_findings(dict.fromkeys(located), TypeAdapter(model))
    documents: dict[str, Any] = {}
    unread: set[str] = set()
    for key in located:
        text = texts[key]
        error = text if isinstance(text, Exception) else None
        if error is None:
            try:
                documents[key] = json.loads(text)
            except _JSON_TEXT_REFUSALS as exc:
                error = exc
        if error is not None:
            unread.add(key)
            findings.append(qualified(_unreadable_document_finding(error), quote(key)))
            continue
        findings += [qualified(f, quote(key))
                     for f in validate_document(documents[key], model.kind_at(key))]
    check = _PACKAGE_CHECKS[package]
    findings += _run_guarded(
        lambda: [qualified(f, quote(key)) for key, f in check(documents, frozenset(unread))],
        crash_label=f"{package} check")
    return _envelope(findings)
