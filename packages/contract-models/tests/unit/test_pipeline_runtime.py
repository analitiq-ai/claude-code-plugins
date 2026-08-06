"""Pipeline `runtime` block guards.

Narrow by design: the runtime block is mostly bounded scalars that the rendered
JSON Schema already states. What is worth a test is the shape the contract
deliberately *stopped* offering — `TestBatchingHasNoConcurrencyKnob`.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.pipelines.config import Batching


class TestBatchingHasNoConcurrencyKnob:
    """`max_concurrent_batches` was declared here and on `stream.Execution`, and
    bounded nothing. Dead config, not ignored config: the pipeline-level key WAS
    parsed downstream, it just never limited concurrency. A knob that looks
    load-bearing and is inert is worse than an absent one — it invites tuning
    advice that cannot have an effect — so it was retired from both models
    rather than documented as a no-op. The stream half is pinned in
    test_stream_mapping_shapes.py.

    This is a BREAKING removal, not a deprecation: the field had a non-null
    default, so any stored pipeline document that spells it out now fails
    validation and needs migrating. That is why pipeline schema 9.0.0 -> 10.0.0
    is a major bump.
    """

    def test_batch_size_still_accepted(self):
        assert Batching.model_validate({"batch_size": 5000}).batch_size == 5000

    def test_defaults(self):
        assert Batching().batch_size == 100

    def test_max_concurrent_batches_rejected(self):
        # The block is closed, so an old document carrying the field fails
        # loudly instead of having it silently dropped.
        with pytest.raises(ValidationError):
            Batching.model_validate({"batch_size": 100, "max_concurrent_batches": 3})
