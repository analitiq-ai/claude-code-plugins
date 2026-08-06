"""Pipeline `runtime` block guards.

Narrow by design: the runtime block is mostly bounded scalars that the rendered
JSON Schema already states. What is worth a test is the shape the contract
deliberately *stopped* offering — `TestBatchingHasNoConcurrencyKnob` — and the
one bound that has to agree with a field in another module,
`TestStreamOverrideTakesTheSameBatchSizes`.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.pipelines.config import Batching
from analitiq.contracts.stream import Execution


class TestBatchingHasNoConcurrencyKnob:
    """`max_concurrent_batches` was declared here and on `stream.Execution`, and
    acted on by nothing (#108: "declared in two models, consumed by nothing").
    Note the precise claim — the pipeline-level key was parsed downstream, it
    just never bounded anything, which is dead config rather than ignored
    config. A knob that looks load-bearing and is inert is worse than an absent
    one — it invites tuning advice that cannot have an effect — so it was
    retired from both models rather than documented as a no-op. The stream half
    is pinned in test_stream_mapping_shapes.py.

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


class TestStreamOverrideTakesTheSameBatchSizes:
    """`stream.Execution.batch_size` overrides `pipeline.runtime.batching.batch_size`.

    An override that admits a value the field it overrides could never hold
    describes a batch size no pipeline default can express — so each carries the
    same shared annotation rather than a bound spelled at its own site. Asserted
    through the bound itself, not by comparing annotations, so the claim stays
    the behaviour a document actually meets.
    """

    @pytest.mark.parametrize("value", [1, 100_000])
    def test_the_edges_of_the_range_hold_on_both(self, value):
        assert Batching.model_validate({"batch_size": value}).batch_size == value
        assert Execution.model_validate({"batch_size": value}).batch_size == value

    @pytest.mark.parametrize("value", [0, -1, 100_001])
    def test_outside_the_range_is_refused_by_both(self, value):
        with pytest.raises(ValidationError):
            Batching.model_validate({"batch_size": value})
        with pytest.raises(ValidationError):
            Execution.model_validate({"batch_size": value})
