"""Prove the renderer's audience guard FIRES — not merely that it passes.

`scripts/render_schemas.py` renders the public JSON Schemas from
`packages/contract-models`. Its one safety property is `Resource.__post_init__`:
every model a registered resource renders from must live in
`analitiq.contracts`. CI runs `render_schemas.py check`, but that only exercises
the clean registry — it would stay green if the guard were deleted.

This mirrors `packages/validator/tests/test_contract_models_build.py`, which
tests its sibling guard by injecting a violation and asserting the guard trips.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, RootModel, TypeAdapter, computed_field

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "connector_builder"))

# Skipping here would defeat the point: this module exists because
# `render_schemas.py check` alone would stay green if the guard were deleted, so
# these tests going quietly absent is the same failure one level up. CI sets
# DRIFT_REQUIRE_CONTRACT_MODELS=1, which turns the skip into a hard failure.
from _pins import require_contract_models  # noqa: E402

require_contract_models("analitiq.contracts", "render_schemas")

import render_schemas  # noqa: E402

Resource = render_schemas.Resource
_model_tree = render_schemas._model_tree
_module_allowed = render_schemas._module_allowed

PUBLIC = "analitiq.contracts.probe"
PRIVATE = "alq.models.probe"


def _model(name: str, module: str, **fields: Any) -> type[BaseModel]:
    cls = type(name, (BaseModel,), {"__annotations__": fields})
    cls.__module__ = module
    return cls


def _resource(adapter: TypeAdapter) -> Resource:
    return Resource(name="probe", title="t", description="d", adapter=adapter)


# --- the guard fires -------------------------------------------------------

def test_private_model_is_rejected():
    private = _model("Private", PRIVATE, a=int)
    public = _model("Public", PUBLIC, leaked=private)
    with pytest.raises(ValueError, match=r"reaches models outside"):
        _resource(TypeAdapter(public))


def test_rejection_names_the_offending_model():
    private = _model("Offender", PRIVATE, a=int)
    with pytest.raises(ValueError) as exc:
        _resource(TypeAdapter(_model("Public", PUBLIC, leaked=private)))
    assert f"{PRIVATE}.Offender" in str(exc.value), (
        "the message must name what leaked, or it is not actionable")


def test_clean_model_is_accepted():
    """Guard the guard: the rejection cases above are this plus one change."""
    _resource(TypeAdapter(_model("Public", PUBLIC, a=int)))


def test_empty_model_tree_is_rejected():
    """An unwalkable root would otherwise pass the leak check by having nothing
    to check — indistinguishable from clean."""
    with pytest.raises(ValueError, match=r"empty model tree"):
        _resource(TypeAdapter(dict[str, Any]))


def test_every_registered_resource_is_public():
    """The real registry, constructed at import — a regression here is a leak."""
    assert render_schemas.RESOURCES, "registry must not be empty"
    for resource in render_schemas.RESOURCES:
        for model in _model_tree(resource.adapter._type):
            assert _module_allowed(model.__module__), (
                f"{resource.name} renders {model.__module__}.{model.__qualname__}")


# --- the walker reaches what the guard depends on --------------------------

def test_module_allowlist_requires_a_dot_boundary():
    assert _module_allowed("analitiq.contracts")
    assert _module_allowed("analitiq.contracts.connector")
    assert _module_allowed("pydantic.root_model")
    # A bare `startswith` would admit both of these.
    assert not _module_allowed("analitiq.contracts_internal.x")
    assert not _module_allowed("pydantic_extra_types.color")


@pytest.mark.parametrize("wrap", [
    pytest.param(lambda m: list[m], id="list"),
    pytest.param(lambda m: m | None, id="optional"),
    pytest.param(lambda m: Annotated[m, "meta"], id="annotated"),
    pytest.param(lambda m: dict[str, m], id="dict-value"),
    pytest.param(lambda m: list[m | None], id="nested"),
])
def test_walker_unwraps_generics(wrap):
    """A container the walker cannot see through makes the guard a no-op."""
    private = _model("Private", PRIVATE, a=int)
    public = _model("Public", PUBLIC, leaked=wrap(private))
    assert private in _model_tree(public)


def test_walker_reaches_root_model():
    private = _model("Private", PRIVATE, a=int)
    root = RootModel[list[private]]
    root.__module__ = PUBLIC
    assert private in _model_tree(root)


def test_walker_reaches_computed_field_return_types():
    """Computed fields are absent from `model_fields` but ARE rendered under
    mode="serialization" — the surface the historical CDN leak came through."""
    private = _model("Private", PRIVATE, a=int)

    class Public(BaseModel):
        x: int

        @computed_field
        @property
        def leaked(self) -> private:  # type: ignore[valid-type]
            ...

    Public.__module__ = PUBLIC
    assert private in _model_tree(Public)


# ---------------------------------------------------------------------------
# Rendered mirrors of model rules
# ---------------------------------------------------------------------------


def test_write_mode_conflict_keys_mirror_covers_every_mode():
    """The `operations.write` conflict-keys mirror must be derived, not listed.

    `Operations._conflict_keys_by_mode` requires `conflict_keys` on `upsert` and
    forbids it on every other mode. `render_schemas` mirrors that into the
    published schema so external validators enforce the same rule — and that
    mirror used to hardcode `insert`/`upsert`. Widening `WriteMode` updated the
    derived `propertyNames.enum` but not the mirror, so the published schema
    ACCEPTED `truncate_insert.conflict_keys` while the model rejected it.

    Iterate the vocabulary, so a new mode fails here instead of shipping a hole
    into an immutable `X.Y.Z.json`.
    """
    from analitiq.contracts.endpoints import WRITE_MODES

    schema = render_schemas.render_latest(
        render_schemas.get_resource("api-endpoint"), "0.0.0"
    )
    write = schema["$defs"]["Operations"]["properties"]["write"]
    # `write` renders as anyOf[object-branch, null]; the mirror lives on the
    # object branch, alongside the derived `propertyNames.enum`.
    obj_branch = next(b for b in write["anyOf"] if b.get("type") == "object")
    branches = obj_branch["properties"]

    assert set(obj_branch["propertyNames"]["enum"]) == set(WRITE_MODES)
    assert set(branches) == set(WRITE_MODES), (
        "the rendered conflict-keys mirror does not cover every write mode; "
        f"missing: {sorted(set(WRITE_MODES) - set(branches))}"
    )
    for mode in WRITE_MODES:
        pinned = branches[mode]["allOf"][1]
        if mode == "upsert":
            assert pinned["required"] == ["conflict_keys"]
            assert pinned["properties"]["conflict_keys"]["minItems"] == 1
        else:
            assert pinned["properties"]["conflict_keys"] == {"type": "null"}, (
                f"write mode {mode!r} does not pin conflict_keys to null, so the "
                "published schema is laxer than the model"
            )
