"""A pipeline document is refused when its connections or runtime block is
misshapen.

Each case bends one field of an otherwise valid pipeline document and
validates the whole document.
"""
from __future__ import annotations

import pytest

from analitiq.contracts.pipelines.config import PipelineInput

from _contract_documents import PIPELINE, bent
from _contract_refusals import refusal_at

ERROR_HANDLING = ("runtime", "error_handling")


def _error_handling(**fields):
    return bent(PIPELINE, lambda d: d.update(runtime={"error_handling": fields}))


@pytest.mark.parametrize(
    "document",
    [
        PIPELINE,
        _error_handling(max_retries=5),
        *(_error_handling(strategy=strategy) for strategy in ("fail", "dlq", "skip")),
    ],
    ids=["pipeline", "max-retries-at-bound", "strategy-fail", "strategy-dlq", "strategy-skip"],
)
def test_a_well_shaped_pipeline_is_accepted(document):
    PipelineInput.model_validate(document)


@pytest.mark.parametrize(
    "document, path, error_type",
    [
        (bent(PIPELINE, lambda d: d.pop("connections")), ("connections",), "missing"),
        (bent(PIPELINE, lambda d: d["connections"].pop("source")), ("connections", "source"), "missing"),
        (
            bent(PIPELINE, lambda d: d["connections"].update(destinations=[])),
            ("connections", "destinations"),
            "too_short",
        ),
        (_error_handling(max_retries=9), (*ERROR_HANDLING, "max_retries"), "less_than_equal"),
        (_error_handling(strategy="nope"), (*ERROR_HANDLING, "strategy"), "literal_error"),
    ],
    ids=["no-connections", "no-source", "no-destinations", "max-retries-over-bound", "unknown-strategy"],
)
def test_a_misshapen_pipeline_is_refused(document, path, error_type):
    refusal_at(PipelineInput.model_validate, document, path, error_type)
