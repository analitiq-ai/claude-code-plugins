"""analitiq.validator — the Analitiq artifact validator.

Validates Analitiq connector / endpoint / type-map / connection / stream /
pipeline JSON documents against the **contract models**
(`analitiq-contract-models`) plus the cross-file coverage and advisory checks a
single-document model cannot express, and validates an assembled **pipeline
bundle** for cross-document referential integrity. Output is a JSON report
(`{"passed": bool, "findings": [...]}`); the CLI exits non-zero on any
error-severity finding.

Three entry points, one set of checks:

- `validate_document(doc, doc_path=None, schema_url=None, *, entity=None)` —
  one document, its kind detected from its shape or named by `entity`; with a
  `doc_path`, the cross-file checks read its siblings from disk.
- `validate_tree(documents)` — a whole connector or pipeline handed over as a
  mapping of relative keys to file text or parsed documents; returns the
  `diagnostics` envelope. What the path-based route reads from disk this route
  reads from the mapping, through the same checks.
- `validate_pipeline_bundle(bundle)` — the referential checks over an already
  assembled bundle.

Importing this package pulls in the per-kind modules (`connectors`, `pipelines`,
`connections`, `streams`, `trees`), each of which self-registers its
detector→validator pairs (and its validator ids) with the core dispatch registry
— a new kind is a new module registering the same way, without touching `_core`.
The public surface is re-exported here.
"""
from ._core import ENTITIES, diagnostics, finding, main, validate_document, VALIDATOR_IDS
from . import connectors  # noqa: F401  — imported for its self-registration side effect
from . import pipelines  # noqa: F401  — imported for its self-registration side effect
from . import connections  # noqa: F401  — imported for its self-registration side effect
from . import streams  # noqa: F401  — imported for its self-registration side effect
from . import trees  # noqa: F401  — imported for its self-registration side effect
# The underscore names below are test-facing internals: packages/validator/tests
# exercises them through the package root (see test_validation.py). Deliberately
# NOT in __all__ — that would widen the published star-import surface.
from .connectors import (  # skipcq: PY-W2000
    check_coverage,
    endpoint_filename_findings,
    is_api_endpoint_doc,
    is_connector_doc,
    is_database_endpoint_doc,
    is_stem_addressed_endpoint_path,
    resolve_type_map_gaps,
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
from .trees import validate_tree

__all__ = [
    "ENTITIES",
    "diagnostics",
    "finding",
    "main",
    "validate_document",
    "validate_tree",
    "resolve_type_map_gaps",
    "VALIDATOR_IDS",
    "check_coverage",
    "endpoint_filename_findings",
    "is_stem_addressed_endpoint_path",
    "is_api_endpoint_doc",
    "is_connector_doc",
    "is_database_endpoint_doc",
    "is_connection_doc",
    "is_stream_doc",
    "is_pipeline_doc",
    "is_pipeline_bundle",
    "validate_pipeline_bundle",
]
