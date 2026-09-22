"""The pipeline manifest: the index of the pipelines a workspace holds."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.pipeline_manifest import PipelineManifest

ENTRY = {
    "pipeline_id": "0b1f0c9e-2c7a-4d1e-9b6a-3f5e8d2c1a47",
    "status": "active",
    "path": "orders/pipeline.json",
}


def test_a_manifest_the_engine_runs_validates():
    PipelineManifest.model_validate({"pipelines": [ENTRY]})


def test_a_manifest_holding_no_pipelines_validates():
    PipelineManifest.model_validate({"pipelines": []})


@pytest.mark.parametrize("dropped", ["pipeline_id", "status", "path"])
def test_an_entry_missing_a_required_field_is_rejected(dropped):
    entry = {k: v for k, v in ENTRY.items() if k != dropped}
    with pytest.raises(ValidationError):
        PipelineManifest.model_validate({"pipelines": [entry]})


def test_a_manifest_without_pipelines_is_rejected():
    with pytest.raises(ValidationError):
        PipelineManifest.model_validate({})


@pytest.mark.parametrize(
    "path",
    [
        "../x/pipeline.json",
        "/abs/pipeline.json",
        "a/b/pipeline.json",
        "./pipeline.json",
        "pipeline.json",
        "orders/streams.json",
        "orders/",
        "orders/pipeline.json\n",
    ],
)
def test_a_path_outside_one_pipeline_directory_is_rejected(path):
    with pytest.raises(ValidationError):
        PipelineManifest.model_validate({"pipelines": [{**ENTRY, "path": path}]})


@pytest.mark.parametrize("status", ["", "running"])
def test_status_outside_the_lifecycle_vocabulary_is_rejected(status):
    with pytest.raises(ValidationError):
        PipelineManifest.model_validate({"pipelines": [{**ENTRY, "status": status}]})


OTHER_ID = "5c2e7a10-9d3b-4f6e-8a1c-7b4d2e9f0a36"


@pytest.mark.parametrize(
    "second",
    [
        {**ENTRY, "path": "invoices/pipeline.json"},
        {**ENTRY, "pipeline_id": OTHER_ID},
    ],
    ids=["same-pipeline_id", "same-path"],
)
def test_two_entries_listing_one_pipeline_are_rejected(second):
    with pytest.raises(ValidationError, match="RULE-PIPE-020"):
        PipelineManifest.model_validate({"pipelines": [ENTRY, second]})


def test_entries_listing_distinct_pipelines_validate():
    PipelineManifest.model_validate(
        {"pipelines": [ENTRY, {**ENTRY, "pipeline_id": OTHER_ID, "path": "invoices/pipeline.json"}]}
    )
