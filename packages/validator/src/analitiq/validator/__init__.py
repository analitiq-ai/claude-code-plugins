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
holding an unidentified document. A caller holding a type map grades it with
`type_map_findings(doc)`, the same for a connector's map and a connection's. A
caller holding a `definition/` *directory* calls
`load_type_map(parent, rule=...)`, which reads the directory's
`TYPE_MAP_FILENAME`; where each caller roots the
findings is its own.

Importing this package pulls in the per-kind modules (`connectors`, `pipelines`,
`connections`, `streams`), each of which registers its kinds with `_core`'s
registries at import. The public surface is re-exported here.

`document_set` grades a document, a package or a workspace handed over in
memory, graded as the kinds the request names. Its entry points take the
request models in `analitiq.contracts.validation_requests`, which own the
request shape and are where a malformed argument is refused. It is imported
after the per-kind modules, whose checks it routes.
"""
from ._core import finding, finding_costs_a_pass, main, validate_document
from . import connectors  # noqa: F401  — imported for its self-registration side effect
from . import pipelines  # noqa: F401  — imported for its self-registration side effect
from . import connections  # noqa: F401  — imported for its self-registration side effect
from . import streams  # noqa: F401  — imported for its self-registration side effect
from .document_set import (
    Finding,
    ValidationEnvelope,
    validate_package,
    validate_single_document,
    validate_workspace,
)
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
    is_api_endpoint_doc,
    is_connector_doc,
    is_addressed_endpoint_path,
    is_database_endpoint_doc,
    is_stem_addressed_endpoint_path,
    is_type_map_doc,
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
    "validate_package",
    "validate_single_document",
    "validate_workspace",
    "check_coverage",
    "endpoint_filename_findings",
    "type_map_findings",
    "load_type_map",
    "TypeMapLoad",
    "TYPE_MAP_FILENAME",
    "is_stem_addressed_endpoint_path",
    "is_addressed_endpoint_path",
    "is_api_endpoint_doc",
    "is_connector_doc",
    "is_database_endpoint_doc",
    "is_type_map_doc",
    "is_connection_doc",
    "is_stream_doc",
    "is_pipeline_doc",
    "is_pipeline_bundle",
    "validate_pipeline_bundle",
]
