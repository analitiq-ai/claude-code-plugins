"""analitiq.validator — the Analitiq artifact validator.

Validates Analitiq documents and packages against the **contract models**
(`analitiq-contract-models`) plus the cross-document checks a single-document
model cannot express, and validates an assembled **pipeline bundle** for
cross-document referential integrity. Output is a JSON report
(`{"passed": bool, "findings": [...]}`); the CLI exits non-zero exactly when
`passed` is `False` — `finding_costs_a_pass` owns the full predicate, which
also fails closed on an unchecked error-tier rule, not only a `fail` finding
at `severity: "error"`.

The caller names what it holds. `validate_document(doc, kind)` grades one
document as `kind`, one of the kinds the published packages locate.
`validate_package`, `validate_package_at` and `grade_package` grade a package
as the published package schema it names, which says where its root and each
kind of document sit.

Importing this package pulls in the per-kind modules (`connectors`, `pipelines`,
`connections`, `streams`), each of which registers its kinds and its package's
check — a new kind is a new module registering the same way, without touching
`_core`.
"""
from ._core import finding, finding_costs_a_pass, main, validate_document
from .document_set import (
    Finding,
    ValidationEnvelope,
    grade_package,
    keys_of,
    read_document,
    read_package,
    validate_package,
    validate_package_at,
    validate_single_document,
)
from . import connectors  # noqa: F401  — imported for its self-registration side effect
from . import pipelines  # noqa: F401  — imported for its self-registration side effect
from . import connections  # noqa: F401  — imported for its self-registration side effect
from . import streams  # noqa: F401  — imported for its self-registration side effect
# The underscore names below are test-facing internals: packages/validator/tests
# exercises them through the package root (see test_validation.py). Deliberately
# NOT in __all__ — that would widen the published star-import surface.
from .connectors import (  # skipcq: PY-W2000
    endpoint_filename_findings,
    _arrow_type_eq,
    _collect_native_arrow_pairs,
    _database_endpoint_locator_findings,
    _endpoint_locator_findings,
    _flatten_api_locator,
    _render_arrow_type,
)
from .pipelines import validate_pipeline_bundle

__all__ = [
    "finding",
    "finding_costs_a_pass",
    "main",
    "validate_document",
    "Finding",
    "ValidationEnvelope",
    "grade_package",
    "keys_of",
    "read_document",
    "read_package",
    "validate_package",
    "validate_package_at",
    "validate_single_document",
    "endpoint_filename_findings",
    "validate_pipeline_bundle",
]
