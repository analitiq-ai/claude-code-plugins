"""analitiq.validator — the Analitiq artifact validator.

Validates Analitiq connector / endpoint / type-map / connection / stream /
pipeline JSON documents against the **contract models**
(`analitiq-contract-models`) plus the cross-file coverage and advisory checks a
single-document model cannot express, and validates an assembled **pipeline
bundle** for cross-document referential integrity. Output is a JSON report
(`{"passed": bool, "findings": [...]}`); the CLI exits non-zero exactly when
`passed` is `False` — `finding_costs_a_pass` owns the full predicate, which
also fails closed on an unchecked error-tier rule, not only a `fail` finding
at `severity: "error"`.

`validate_document(doc, kind)` grades one document as the kind its caller names,
which `document_kinds()` enumerates; nothing reads a document to work out what
it is. A caller holding a type map grades it with
`type_map_findings(doc, scope)`, where `scope` says whether it is a connector's
map or a connection's. A caller holding a `definition/` *directory* calls
`load_type_map(parent, rule=...)`, which reads the directory's
`TYPE_MAP_FILENAME` and refuses every other type-map name beside it; where each caller roots those
findings is its own.

Importing this package pulls in the per-kind modules (`connectors`, `pipelines`,
`connections`, `streams`), each of which self-registers its validators under the
kind names it owns — a new kind is a new module registering the same way,
without touching `_core`. The public surface is re-exported here.

`document_set` declares the path-free document-set API;
`validate_pipeline_package` raises `NotImplementedError`. See that module's docstring and
`__all__` below for what it contributes to this package's surface. Its entry
points take the request models in `analitiq.contracts.validation_requests`,
which own the document-set shape and are where a malformed argument is
refused.
"""
from ._core import (
    document_kinds,
    finding,
    finding_costs_a_pass,
    main,
    validate_document,
)
from .document_set import (
    Finding,
    ValidationEnvelope,
    validate_connector_package,
    validate_pipeline_package,
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
    TYPE_MAP_FILENAME,
    TypeMapLoad,
    check_coverage,
    endpoint_filename_findings,
    load_type_map,
    type_map_findings,
    is_addressed_endpoint_path,
    is_stem_addressed_endpoint_path,
    _arrow_type_eq,
    _collect_native_arrow_pairs,
    _database_endpoint_locator_findings,
    _endpoint_locator_findings,
    _flatten_api_locator,
    _render_arrow_type,
)
from .pipelines import PIPELINE_BUNDLE_KIND, validate_pipeline_bundle

__all__ = [
    "finding",
    "finding_costs_a_pass",
    "main",
    "validate_document",
    "document_kinds",
    "Finding",
    "ValidationEnvelope",
    "validate_connector_package",
    "validate_pipeline_package",
    "validate_single_document",
    "check_coverage",
    "endpoint_filename_findings",
    "type_map_findings",
    "load_type_map",
    "TypeMapLoad",
    "TYPE_MAP_FILENAME",
    "is_stem_addressed_endpoint_path",
    "is_addressed_endpoint_path",
    "PIPELINE_BUNDLE_KIND",
    "validate_pipeline_bundle",
]
