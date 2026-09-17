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

`validate_document` detects a document's kind from its own body, for a caller
holding an unidentified document. A caller that knows what a document is meant
to be says so instead: `type_map_findings(doc, direction, scope)` grades a type
map as the direction named, reporting a disagreeing `direction` alongside every
other defect the model finds rather than grading the document as what it claims
to be.

A caller holding a *directory* of maps asks which files are maps and which one
is the map for each direction: `type_map_sibling_paths(parent)` answers the
first, `TypeMapDirections` the second. They are published because the answer has
to be the same at every scope — a connector's siblings here, a connection's
beside the pipeline plugin's adapter — while what each caller reports about a
document, and where it roots that report, is its own.

Importing this package pulls in the per-kind modules (`connectors`, `pipelines`,
`connections`, `streams`), each of which self-registers its detector→validator
pairs with the core dispatch registry — a new kind is a new module registering
the same way, without touching `_core`. The public surface is re-exported here.

`document_set` declares the path-free document-set API's types and
signatures ahead of their implementation — every function it exports
currently raises `NotImplementedError`; see that module's docstring and
`__all__` below for what it contributes to this package's surface. Its entry
points take the request models in `analitiq.contracts.validation_requests`,
which own the document-set shape and are where a malformed argument is
refused.
"""
from ._core import finding, finding_costs_a_pass, main, validate_document
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
    LEGACY_TYPE_MAP_FILENAME,
    TypeMapDirections,
    check_coverage,
    endpoint_filename_findings,
    type_map_discriminator_findings,
    type_map_findings,
    type_map_sibling_paths,
    legacy_type_map_present,
    is_api_endpoint_doc,
    is_connector_doc,
    is_addressed_endpoint_path,
    is_database_endpoint_doc,
    is_stem_addressed_endpoint_path,
    _arrow_type_eq,
    _collect_native_arrow_pairs,
    _database_endpoint_locator_findings,
    _endpoint_locator_findings,
    _flatten_api_locator,
    _render_arrow_type,
)
from .pipelines import is_pipeline_bundle, is_pipeline_doc, validate_pipeline_bundle
from .connections import is_connection_doc
from .streams import is_stream_doc

__all__ = [
    "finding",
    "finding_costs_a_pass",
    "main",
    "validate_document",
    "Finding",
    "ValidationEnvelope",
    "validate_connector_package",
    "validate_pipeline_package",
    "validate_single_document",
    "check_coverage",
    "endpoint_filename_findings",
    "type_map_findings",
    "type_map_discriminator_findings",
    "type_map_sibling_paths",
    "legacy_type_map_present",
    "LEGACY_TYPE_MAP_FILENAME",
    "TypeMapDirections",
    "is_stem_addressed_endpoint_path",
    "is_addressed_endpoint_path",
    "is_api_endpoint_doc",
    "is_connector_doc",
    "is_database_endpoint_doc",
    "is_connection_doc",
    "is_stream_doc",
    "is_pipeline_doc",
    "is_pipeline_bundle",
    "validate_pipeline_bundle",
]
