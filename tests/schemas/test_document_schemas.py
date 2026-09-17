"""The single-document request's `entity` vocabulary is computed from the
schema registry, never listed.

`render_schemas.document_schema_names` selects every registered resource whose
root model declares a `$schema` field, and `document-schemas` writes the result
into the contract package for the request model to load. These tests register
throwaway resources to prove membership follows the models, and that the
selection refuses a root it cannot classify.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal, Union

import pytest
from pydantic import BaseModel, Field, TypeAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts", "render_schemas")

import render_schemas  # noqa: E402
from analitiq.contracts.shared.common import schema_url_for  # noqa: E402

PUBLIC = "analitiq.contracts.probe"


def _document_model(name: str, declared_resource: str) -> type[BaseModel]:
    cls = type(name, (BaseModel,), {
        "__annotations__": {"schema_url": Literal[schema_url_for(declared_resource)]},
        "schema_url": Field(..., alias="$schema"),
    })
    cls.__module__ = PUBLIC
    return cls


def _plain_model(name: str) -> type[BaseModel]:
    cls = type(name, (BaseModel,), {"__annotations__": {"a": int}})
    cls.__module__ = PUBLIC
    return cls


def _resource(name: str, root) -> render_schemas.Resource:
    return render_schemas.Resource(
        name=name, title="t", description="d", adapter=TypeAdapter(root))


def test_a_resource_whose_model_declares_schema_is_selected():
    base = list(render_schemas.RESOURCES)
    added = _resource("probe-document", _document_model("Probe", "probe-document"))
    names = render_schemas.document_schema_names([*base, added])
    assert names == [*render_schemas.document_schema_names(base), "probe-document"]


def test_a_resource_whose_model_declares_no_schema_is_not_selected():
    base = list(render_schemas.RESOURCES)
    added = _resource("probe-body", _plain_model("Body"))
    assert (render_schemas.document_schema_names([*base, added])
            == render_schemas.document_schema_names(base))


def test_every_member_of_a_union_root_declaring_schema_is_selected():
    root = Annotated[
        Union[_document_model("A", "probe-union"), _document_model("B", "probe-union")],
        "meta",
    ]
    assert render_schemas.document_schema_names([_resource("probe-union", root)]) == [
        "probe-union"]


def test_a_union_root_only_partly_declaring_schema_is_refused():
    root = Union[_document_model("A", "probe-mixed"), _plain_model("B")]
    with pytest.raises(ValueError, match="probe-mixed"):
        render_schemas.document_schema_names([_resource("probe-mixed", root)])


def test_a_schema_field_naming_another_resource_is_refused():
    wrong = _resource("probe-own", _document_model("Wrong", "probe-other"))
    with pytest.raises(ValueError, match="probe-own"):
        render_schemas.document_schema_names([wrong])


def test_a_schema_field_accepting_another_documents_url_is_refused():
    both = Literal[schema_url_for("probe-a"), schema_url_for("probe-b")]
    cls = type("Loose", (BaseModel,), {
        "__annotations__": {"schema_url": both},
        "schema_url": Field(..., alias="$schema"),
    })
    cls.__module__ = PUBLIC
    resources = [
        _resource("probe-a", cls),
        _resource("probe-b", _document_model("B", "probe-b")),
    ]
    with pytest.raises(ValueError, match="probe-a"):
        render_schemas.document_schema_names(resources)


def test_committed_label_file_matches_the_registry():
    ok, message = render_schemas.check_document_schemas()
    assert ok, message
