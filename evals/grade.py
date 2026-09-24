"""Grade what an eval run wrote the way the plugin submits it: the plugin's own
request builder selects the files, reading the location tables this checkout
renders, and the validator's request entry point grades them in-process.

    grade.py package <directory> <package_kind>
    grade.py workspace <directory>

Prints the validation envelope and exits 1 when it did not pass or a file was
left out of the request.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from analitiq.contracts.validation_requests import ValidatePackageRequest, ValidateWorkspaceRequest
from analitiq.validator import validate_package, validate_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]
_BUILDER = REPO_ROOT / "plugins" / "analitiq-pipeline-builder" / "scripts" / "validation_request.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("validation_request", _BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def rendered_schema(url: str) -> dict:
    """The schema this checkout renders at a published URL."""
    path = url.removeprefix(builder.SCHEMA_HOST + "/")
    return json.loads((REPO_ROOT / "schemas" / path).read_text())


def package_request(directory: Path, package_kind: str) -> tuple[ValidatePackageRequest, list[dict]]:
    documents, left_out = builder.package_documents(directory, package_kind, rendered_schema)
    return ValidatePackageRequest(package_kind=package_kind, documents=documents), left_out


def workspace_request(directory: Path) -> tuple[ValidateWorkspaceRequest, list[dict]]:
    documents, left_out = builder.workspace_documents(directory, None, rendered_schema)
    return ValidateWorkspaceRequest(documents=documents), left_out


def main(argv: list[str]) -> int:
    if argv[0] == "package":
        request, left_out = package_request(Path(argv[1]), argv[2])
        envelope = validate_package(request)
    else:
        request, left_out = workspace_request(Path(argv[1]))
        envelope = validate_workspace(request)
    print(json.dumps({**envelope, "left_out": left_out}, indent=2))
    return 0 if envelope["passed"] and not left_out else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
