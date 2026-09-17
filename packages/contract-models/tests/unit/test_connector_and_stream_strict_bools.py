"""A boolean field in the connector and stream documents takes JSON `true` or
`false` and nothing a lax parse would read as one.

Pydantic's lax mode reads `"yes"`, `"false"`, `1` and `0` as booleans, while
the published schema says `type: boolean` and refuses every one of them — and
the engine refuses them too, so a lax model passes a document that fails the
moment it is read. `"false"` is the dangerous spelling: it parses to `False`
here and is a non-empty string everywhere else.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from analitiq.contracts.connector import ConnectionContractInput, SqlStageCapabilities
from analitiq.contracts.stream import ArrowFieldSpec, AssignmentTarget

_STAGE = {"scope": "temp", "schema": "target", "transactional_ddl": True}
_INPUT = {
    "source": "user",
    "phase": "pre_auth",
    "storage": "connection.parameters",
    "type": "string",
    "required": True,
}

SITES = [
    pytest.param(SqlStageCapabilities, _STAGE, "transactional_ddl", id="stage.transactional_ddl"),
    pytest.param(ConnectionContractInput, _INPUT, "required", id="input.required"),
    pytest.param(
        ConnectionContractInput,
        {**_INPUT, "storage": "secrets", "secret": True},
        "secret",
        id="input.secret",
    ),
    pytest.param(ArrowFieldSpec, {"arrow_type": "Utf8", "nullable": True}, "nullable", id="arrow_field.nullable"),
    pytest.param(
        AssignmentTarget,
        {"path": "id", "arrow_type": "Utf8", "nullable": True},
        "nullable",
        id="assignment_target.nullable",
    ),
]


@pytest.mark.parametrize(("model", "document", "field"), SITES)
@pytest.mark.parametrize("spelling", ["yes", "false", 1, 0])
def test_a_non_boolean_spelling_is_refused(model, document, field, spelling):
    with pytest.raises(ValidationError) as exc:
        model.model_validate({**document, field: spelling})
    assert (field,) in [err["loc"] for err in exc.value.errors()]


@pytest.mark.parametrize(("model", "document", "field"), SITES)
def test_a_json_boolean_is_accepted(model, document, field):
    assert getattr(model.model_validate(document), field) is document[field]
