"""Grade what an eval run wrote the way the plugin submits it: the plugin's own
request builder assembles the request, and the validator's request entry point
grades it in-process.

    grade.py package <directory> <package_kind>
    grade.py workspace <directory>

(or any other argument list the builder takes).

Prints the validation envelope and exits 1 when it did not pass.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from analitiq.contracts.validation_requests import (
    ValidatePackageRequest,
    ValidateSingleDocumentRequest,
    ValidateWorkspaceRequest,
)
from analitiq.validator import validate_package, validate_single_document, validate_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]
_BUILDER = REPO_ROOT / "plugins" / "analitiq-pipeline-builder" / "scripts" / "validation_request.py"

# The request model and entry point behind each MCP tool the builder names.
_TOOLS = {
    "validate_single_document": (ValidateSingleDocumentRequest, validate_single_document),
    "validate_package": (ValidatePackageRequest, validate_package),
    "validate_workspace": (ValidateWorkspaceRequest, validate_workspace),
}


def _load_builder():
    spec = importlib.util.spec_from_file_location("validation_request", _BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def request(built: dict):
    """The built arguments as the request model their tool takes."""
    model, _ = _TOOLS[built["tool"]]
    return model.model_validate(built["arguments"])


def main(argv: list[str]) -> int:
    built = builder.build(argv)
    envelope = _TOOLS[built["tool"]][1](request(built))
    print(json.dumps(envelope, indent=2))
    return 0 if envelope["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
