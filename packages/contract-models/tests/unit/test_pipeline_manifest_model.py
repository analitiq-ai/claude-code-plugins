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
