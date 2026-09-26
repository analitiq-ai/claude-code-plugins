"""analitiq.validator — the Analitiq artifact validator.

Validates Analitiq connector / endpoint / type-map / connection / stream /
pipeline JSON documents against the **contract models**
(`analitiq-contract-models`) plus the cross-document coverage and advisory
checks a single-document model cannot express. Every answer is
one `{"passed": bool, "findings": [...]}` verdict; `finding_costs_a_pass` owns
when a finding costs `passed`, which also fails closed on an unchecked
error-tier rule, not only a `fail` finding at `severity: "error"`.

Importing this package pulls in the per-kind modules (`connectors`, `pipelines`,
`connections`, `streams`), each of which registers its kinds with `_core`'s
registry at import. The public surface is re-exported here.

`document_set` grades a document, a package or a workspace handed over in
memory, graded as the kinds the request names. Its entry points take the
request models in `analitiq.contracts.validation_requests`, which own the
request shape and are where a malformed argument is refused. It is imported
after the per-kind modules, whose checks it routes. Its entry points read no
file.
"""
from ._core import finding, finding_costs_a_pass
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

__all__ = [
    "finding",
    "finding_costs_a_pass",
    "Finding",
    "ValidationEnvelope",
    "validate_package",
    "validate_single_document",
    "validate_workspace",
]
