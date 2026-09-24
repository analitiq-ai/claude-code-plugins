"""Grade what an eval run wrote, as a file holder submits it: select the files
the contract locates, leave its secret locations behind, and hand the texts to
the validator's request entry point.

    grade.py package <directory> <package_kind>
    grade.py workspace <directory>

Prints the validation envelope and exits 1 when it did not pass.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from analitiq.contracts.validation_requests import (
    PACKAGE_KINDS,
    ValidatePackageRequest,
    ValidateWorkspaceRequest,
)
from analitiq.contracts.workspace import Workspace
from analitiq.validator import validate_package, validate_workspace


def _located_texts(root: Path, kind_at: Callable[[str], str | None],
                   secret_at: Callable[[str], bool]) -> dict[str, str]:
    # A link could carry a file from outside the directory into the request,
    # so only regular files are read.
    texts = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        key = path.relative_to(root).as_posix()
        if kind_at(key) and not secret_at(key):
            texts[key] = path.read_text(encoding="utf-8")
    return texts


def package_request(directory: Path, package_kind: str) -> ValidatePackageRequest:
    model = PACKAGE_KINDS[package_kind]
    return ValidatePackageRequest(
        package_kind=package_kind,
        documents=_located_texts(directory, model.kind_at, model.secret_at))


def workspace_request(directory: Path) -> ValidateWorkspaceRequest:
    return ValidateWorkspaceRequest(
        documents=_located_texts(directory, Workspace.kind_at, Workspace.secret_at))


def main(argv: list[str]) -> int:
    if argv[0] == "package":
        envelope = validate_package(package_request(Path(argv[1]), argv[2]))
    else:
        envelope = validate_workspace(workspace_request(Path(argv[1])))
    print(json.dumps(envelope, indent=2))
    return 0 if envelope["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
