"""`PublicRunError` message-to-code pairing (RULE-DSYNC-002).

`PUBLIC_ERROR_MESSAGES` owns the one customer-safe text per `PublicErrorCode`
member — the public surface must never echo a raw exception string or the
engine's internal-only detail. `_message_matches_code` makes any other
`message` unrepresentable; these tests pin that in both directions.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.pipelines.data_sync import (
    PUBLIC_ERROR_MESSAGES,
    PublicErrorCode,
    PublicRunError,
)


@pytest.mark.parametrize("code", list(PublicErrorCode))
def test_canonical_message_accepted_for_every_code(code):
    err = PublicRunError.model_validate(
        {"code": code.value, "message": PUBLIC_ERROR_MESSAGES[code]}
    )
    assert err.message == PUBLIC_ERROR_MESSAGES[code]


def test_non_canonical_message_rejected():
    with pytest.raises(ValidationError, match="canonical customer-safe text"):
        PublicRunError.model_validate(
            {
                "code": PublicErrorCode.INTERNAL.value,
                "message": "Traceback (most recent call last): boom",
            }
        )


def test_swapped_codes_message_rejected():
    """A message that IS canonical — for a different code — still fails."""
    with pytest.raises(ValidationError, match="canonical customer-safe text"):
        PublicRunError.model_validate(
            {
                "code": PublicErrorCode.RATE_LIMITED.value,
                "message": PUBLIC_ERROR_MESSAGES[PublicErrorCode.INTERNAL],
            }
        )
