"""A boolean field in a document this contract publishes takes JSON `true` or
`false` and nothing a lax parse would read as one.

Pydantic's lax mode reads `"yes"`, `"false"`, `1` and `0` as booleans, while
the published schema says `type: boolean` and refuses every one of them — and
the engine refuses them too, so a lax model passes a document that fails the
moment it is read. `"false"` is the dangerous spelling: it parses to `False`
here and is a non-empty string everywhere else.
"""
from __future__ import annotations

from types import UnionType
from typing import Annotated, Union, get_args, get_origin

import pytest
from pydantic import Strict, ValidationError

from analitiq.contracts.connector import ConnectionContractInput, SqlStageCapabilities
from analitiq.contracts.endpoints import Column, ColumnFieldSpec, Param
from analitiq.contracts.pipelines.config import Logging
from analitiq.contracts.pipelines.data_sync import PipelineRunRequest
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
    pytest.param(
        Param, {"in": "query", "type": "string", "required": True}, "required",
        id="param.required",
    ),
    pytest.param(
        Param,
        {"in": "query", "type": "array", "style": "form", "required": False,
         "explode": True},
        "explode",
        id="param.explode",
    ),
    pytest.param(
        ColumnFieldSpec, {"arrow_type": "Utf8", "nullable": True}, "nullable",
        id="column_field_spec.nullable",
    ),
    pytest.param(
        Column,
        {"name": "id", "native_type": "text", "arrow_type": "Utf8", "nullable": True},
        "nullable",
        id="column.nullable",
    ),
    pytest.param(
        Logging, {"metrics_enabled": True}, "metrics_enabled",
        id="logging.metrics_enabled",
    ),
    pytest.param(
        PipelineRunRequest, {"terminate_existing_sync": True},
        "terminate_existing_sync", id="run_request.terminate_existing_sync",
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


def test_the_sites_are_every_boolean_field_the_contract_declares():
    """The table is the class, not a sample of it: a boolean-only field added
    without strictness is one more document a lax parse takes and the
    published schema refuses. A field whose value union merely admits `bool`
    beside other types is a value slot, not a boolean field, and is not this
    rule's — `true` is one of the values it carries."""
    from analitiq.contracts.shared.introspect import contract_classes

    lax = sorted(
        f"{cls.__module__}.{cls.__name__}.{name}"
        for cls in contract_classes()
        for name, info in cls.model_fields.items()
        if _is_a_boolean_field(info.annotation) and not _is_strict(info)
    )
    assert not lax, f"boolean fields taking a lax parse: {lax}"


def _members(annotation) -> list:
    """An annotation's union members, `None` dropped, `Annotated` unwrapped."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return _members(args[0])
    if origin in (Union, UnionType):
        return [m for arg in args if arg is not type(None) for m in _members(arg)]
    return [annotation]


def _is_a_boolean_field(annotation) -> bool:
    return _members(annotation) == [bool]


def _is_strict(info) -> bool:
    """Whether pydantic parses this field strictly, wherever `Strict` sits.

    On a bare `StrictBool` it lands in the field's own metadata; under
    `StrictBool | None` it stays inside the `Annotated` member, which is why
    reading one place reported half the fields as lax.
    """
    if any(isinstance(m, Strict) for m in info.metadata):
        return True
    return _annotated_strict(info.annotation)


def _annotated_strict(annotation) -> bool:
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return any(isinstance(m, Strict) for m in args[1:]) or _annotated_strict(args[0])
    if origin in (Union, UnionType):
        return all(
            arg is type(None) or _annotated_strict(arg) for arg in args
        )
    return False
