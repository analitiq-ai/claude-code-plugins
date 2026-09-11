#!/usr/bin/env python3
"""Validate an authored Analitiq document against the published contract.

This is a **disk reader** over the published `analitiq-validator` +
`analitiq-contract-models` packages (the same offline, model-driven contract
the Analitiq services validate against). It reads the document — and, for a
stitched pipeline, the documents around it — and hands them to the package,
which owns every check and returns the one Diagnostics envelope:

    {"passed": bool, "findings": [{"validator", "severity", "path", "message"}]}

Routing:

  * every ``--entity`` -> ``analitiq.validator.validate_document(doc, doc_path,
    entity=...)``. The entity names the published contract the document is
    authored against, so a document missing its discriminating key — the
    broken input a validation exists to diagnose — is graded by the model it
    was meant for rather than collapsing into one "unrecognised" finding. For a
    ``type_map_*`` entity the package also gates the filename (`RULE-TMAP-023`)
    and grades in that direction.
  * ``pipeline`` with ``--bundle-root`` -> ``analitiq.validator.validate_tree``
    over a tree read from disk, keyed the way the package's tree API expects:
    the ``--document`` itself under the key of a pipeline root whatever its own
    name, its ``streams/`` beside it, and each pattern ``_bundle_documents``
    globs under ``--bundle-root``. The ``analitiq.validator.trees`` module
    docstring carries what a pipeline tree holds; the globs below are how this
    reader fills one. The
    package assembles the bundle, checks referential integrity (a draft
    pipeline is not held to runnability; an ``active`` one is), grades each
    connection's type maps, and verifies ``scope='connector'`` refs against the
    connectors' endpoints on disk.

A file a pattern names but that cannot be read as text (a directory under a
document's name, a dangling symlink, an undecodable file) is handed over as
``Unreadable``, so the package sees the member was there and reports the read
failure at its key.

Validation is offline — no schema is fetched. Usage::

    python3 plugins/analitiq-pipeline-builder/scripts/validate.py --entity pipeline --document path/to/pipeline.json --bundle-root .

Exit status is ``0`` iff ``passed`` (no error-severity finding), ``1`` on any
error finding or an unreadable document, ``2`` on a CLI usage error.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _bootstrap import ensure_deps_or_reexec


def _crash_finding(path: str, exc: BaseException) -> dict:
    """The finding the outermost guard in `main()` prints when nothing else
    could: built without the package, because that guard also covers the
    bootstrap that installs it. `str(exc)` is empty for some exceptions (a bare
    `MemoryError()`), so the detail is only appended when there is one, never
    leaving a dangling `: `; an exception whose own `__str__` raises must not
    become a second, unguarded crash inside the guard, so that failure is
    swallowed too."""
    try:
        detail = str(exc)
    except Exception:  # noqa: BLE001 - the guard must not itself crash
        detail = ""
    message = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    return {"validator": "adapter-crash", "severity": "error", "path": path, "message": message}


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text())


def _bundle_documents(pipeline_doc: Any, document_path: Path, root: Path) -> dict[str, Any]:
    """The `--bundle-root` layout as the tree `validate_tree` reads."""
    from analitiq.validator import Unreadable

    documents: dict[str, Any] = {"pipeline.json": pipeline_doc}

    def take(key: str, path: Path) -> None:
        try:
            documents[key] = path.read_text()
        except (OSError, UnicodeDecodeError) as exc:
            documents[key] = Unreadable(str(exc))

    for path in (document_path.parent / "streams").glob("*.json"):
        take(f"streams/{path.name}", path)
    patterns = (
        "connections/*/connection.json",
        "connections/*/definition/endpoints/*.json",
        "connections/*/definition/type-map-read.json",
        "connections/*/definition/type-map-write.json",
        "connections/*/definition/type-map.json",
        "connectors/*/definition/connector.json",
        "connectors/*/definition/endpoints/*.json",
    )
    for pattern in patterns:
        for path in root.glob(pattern):
            take(path.relative_to(root).as_posix(), path)
    return documents


def diagnostics_for(entity: str, document_path: Path, bundle_root: Path | None = None) -> dict:
    """Validate one document and return the Diagnostics envelope. Raises nothing
    for validation failures — those become findings; only a genuinely unreadable
    document short-circuits."""
    from analitiq.validator import diagnostics, finding, validate_document, validate_tree

    try:
        doc = _read_json(document_path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return diagnostics([finding("document", "error", "", f"Cannot read document: {exc}")])

    if entity == "pipeline" and bundle_root is not None:
        return validate_tree(_bundle_documents(doc, Path(document_path), Path(bundle_root)))
    return diagnostics(validate_document(doc, doc_path=document_path, entity=entity))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entity", required=True,
                        help="Which published contract the document is authored against "
                             "(a member of analitiq.validator.ENTITIES).")
    parser.add_argument("--document", required=True, help="Path to the JSON document to validate.")
    parser.add_argument("--bundle-root",
                        help="Project root for cross-document validation of a stitched pipeline "
                             "(reads connections/, connectors/, and the pipeline's streams/). "
                             "Only meaningful with --entity pipeline.")
    args = parser.parse_args(argv)

    # The one guard that must contain everything Python exception handling can
    # contain, MemoryError included — it is reached however deep the failing
    # call is, so it is what keeps such a crash anywhere in the dispatch below
    # from ever reaching the interpreter's own uncaught-exception handling (a
    # traceback on stderr, nothing on stdout). A SystemExit raised below this
    # point (or anything else `except Exception` does not catch) still escapes
    # uncontained — the driving agent's stderr-excerpt fallback is for exactly
    # that case.
    try:
        ensure_deps_or_reexec(__file__)
        # The entity vocabulary is the package's; it is checked here, after the
        # bootstrap, because argparse runs before the package is importable.
        from analitiq.validator import ENTITIES
        if args.entity not in ENTITIES:
            parser.error(f"argument --entity: invalid choice: {args.entity!r} "
                         f"(choose from {', '.join(map(repr, ENTITIES))})")
        bundle_root = Path(args.bundle_root) if args.bundle_root else None
        diagnostics = diagnostics_for(args.entity, Path(args.document), bundle_root)
        # Serialized inside the guard: a backend finding carrying a
        # JSON-incompatible value (a malformed message from a validator
        # regression) must itself become an adapter-crash result, not a
        # TypeError escaping after the guard has already exited clean.
        output = json.dumps(diagnostics, indent=2)
        passed = diagnostics["passed"]
    except Exception as exc:  # noqa: BLE001 - the outermost guard
        print(json.dumps({"passed": False, "findings": [_crash_finding("", exc)]}, indent=2))
        return 1

    print(output)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
