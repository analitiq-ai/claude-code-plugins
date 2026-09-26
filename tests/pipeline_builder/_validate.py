"""Grade one document of a named kind the way the backend grades a
single-document request: through `validate_single_document`, from its text."""
from __future__ import annotations


def validate_as(kind: str, text: str) -> dict:
    from analitiq.contracts.validation_requests import ValidateSingleDocumentRequest
    from analitiq.validator import validate_single_document
    return validate_single_document(ValidateSingleDocumentRequest(document=text, document_kind=kind))
