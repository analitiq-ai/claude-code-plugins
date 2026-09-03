"""The wire-name policy: no contract model accepts a field spelling its own
published JSON Schema rejects.

The sibling half of ``test_strict_numeric_policy.py``, which states the shared
invariant in general terms and then tests only numbers:

    a document this package ACCEPTS is always one the schema accepts.

A field whose Python attribute cannot be spelled in JSON — ``$schema``, ``in``,
``not``, ``minLength``, ``_corrupted`` — carries an ``alias``. Pydantic's
``populate_by_name`` makes such a field accept BOTH spellings, while the
rendered schema publishes only the alias under ``additionalProperties: false``
and therefore rejects the other. Six models set that flag, so every aliased
field beneath them had two accepted spellings locally and one on the wire.

The failure it produced is the quiet kind. ``connector-schema-validator`` runs
these models, so a connector authored as ``{"location": "query"}`` came back
clean; the platform applies the published schema and refuses the same file. The
agent's only feedback loop was the lenient one, and the strict one runs after
the connector has shipped.

The fix is the flag's absence, not a check that tolerates it: an aliased field
has exactly one authored spelling, and it is the wire one. This file is what
keeps it absent, because ``populate_by_name=True`` reads like a convenience and
its cost is invisible at the site that sets it.

**How the property is decided.** Not by reading the config — the flag is
today's cause, and the invariant has to survive whatever tomorrow's is. Each
aliased field is probed with a one-key document under the ATTRIBUTE spelling,
and the assertion is that pydantic reports that key as ``extra_forbidden``:
an unexpected key, which is how the published schema refuses it.

The error TYPE is the discriminator, not its location. A first draft asserted
only that some error mentioned the key, which a model with ``populate_by_name``
satisfies just as well — it consumes the key and then reports a *type* error at
the same place. Measured: that version passed on 34 of 35 fields with the flag
restored. Only ``extra_forbidden`` distinguishes "this key is not a field here"
from "this key is the field and its value is wrong".

**Why this enumerates instead of listing.** Same reason as the numeric policy: a
table of ``(model, field)`` pairs is correct the day it is written and rots on
the next alias. The model set comes from :func:`contract_classes` and the
aliased fields from each model's own ``model_fields``, so a model that starts
aliasing joins the sweep on its own.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import analitiq.contracts
from analitiq.contracts import endpoints, stream
from analitiq.contracts.shared.introspect import contract_classes


def _aliased_fields() -> list[tuple[type, str, str]]:
    """Every ``(model, attribute, alias)`` where the two spellings differ."""
    return sorted(
        (
            (cls, name, info.alias)
            for cls in contract_classes()
            for name, info in cls.model_fields.items()
            if info.alias and info.alias != name
        ),
        key=lambda row: (row[0].__module__, row[0].__name__, row[1]),
    )


ALIASED = _aliased_fields()

#: The probe value is deliberately uninteresting: what is asserted is the error
#: TYPE at this key, so the value never has to be plausible for the field.
_PROBE_VALUE = object()


def test_the_sweep_reaches_something() -> None:
    """A sweep over nothing passes silently and proves nothing."""
    assert ALIASED, (
        "no aliased field found anywhere under analitiq.contracts — either the "
        "contract stopped aliasing entirely (delete this file with the last "
        "alias) or the contract walk broke."
    )


def test_every_aliased_model_forbids_unknown_keys() -> None:
    """The probe below reads a rejection off `extra='forbid'`.

    A model that allowed unknown keys would absorb the attribute spelling
    silently, and the probe could not tell that from acceptance. No contract
    model does — the published schemas are `additionalProperties: false`
    throughout — so this states the premise rather than leaving the sweep
    resting on it.
    """
    lax = sorted(
        {
            cls.__name__
            for cls, _name, _alias in ALIASED
            if cls.model_config.get("extra") != "forbid"
        }
    )
    assert not lax, (
        f"models with an aliased field that do not forbid unknown keys: {lax}. "
        "The probe below cannot distinguish acceptance from absorption there."
    )


@pytest.mark.parametrize(
    "cls, attribute, alias",
    [pytest.param(c, n, a, id=f"{c.__name__}.{n}") for c, n, a in ALIASED],
)
def test_no_model_accepts_the_python_attribute_as_a_field_name(
    cls: type, attribute: str, alias: str
) -> None:
    """The attribute spelling must be refused the way the published schema
    refuses it: as a key the document may not carry."""
    try:
        cls.model_validate({attribute: _PROBE_VALUE})
    except ValidationError as exc:
        errors = exc.errors()
        refused = [
            err for err in errors
            if tuple(err["loc"]) == (attribute,) and err["type"] == "extra_forbidden"
        ]
        assert refused, (
            f"{cls.__name__} does not refuse {attribute!r} as an unexpected key, "
            f"so it consumed it as {alias!r}. The published schema names this "
            f"field {alias!r} and forbids unknown keys, so a document written "
            "that way validates here and is refused by every consumer of the "
            "published schema. Remove `populate_by_name` from this model's "
            f"config.\nerrors: {errors}"
        )
        return
    pytest.fail(
        f"{cls.__name__}.model_validate({{{attribute!r}: ...}}) did not raise at "
        f"all — the attribute spelling is accepted where the published schema "
        f"admits only {alias!r}."
    )


def test_the_wire_spelling_still_works() -> None:
    """The policy must refuse the attribute name WITHOUT refusing the alias.

    A model that rejected both spellings would satisfy every assertion above and
    be unusable, so one worked case separates "the attribute is refused" from
    "nothing validates".
    """
    from analitiq.contracts.endpoints import Param

    accepted = Param.model_validate({"in": "query", "type": "string", "required": True})
    assert accepted.location == "query"
    with pytest.raises(ValidationError):
        Param.model_validate({"location": "query", "type": "string", "required": True})


# ---------------------------------------------------------------------------
# Anchored patterns are applied with fullmatch
# ---------------------------------------------------------------------------


def test_anchored_patterns_are_applied_with_fullmatch():
    """A `$`-anchored pattern applied with `re.match` accepts a trailing newline.

    `$` matches before a final `\\n`, so `match` on `^[a-z_]+$` admits
    `"total\\n"` — a key no provider has, that every later comparison treats as
    the key without it. This contract has closed the hole three times, by
    fullmatch and a comment each time, and left other call sites open: a record
    field path and a `response.metadata` key both accepted a trailing newline.

    So the rule is applied here rather than remembered: any compiled `*_RE`
    whose pattern ends in `$` may only be called with `fullmatch`. Source-level
    and lexical — it reads this package's own code for a call shape, never
    prose for a meaning.
    """
    src_root = Path(analitiq.contracts.__path__[0])
    anchored = {
        name.removesuffix("_PATTERN")
        for module in (endpoints, stream)
        for name, value in vars(module).items()
        if name.endswith("_PATTERN") and isinstance(value, str) and value.endswith("$")
    }
    assert anchored, "found no anchored patterns — the guard has stopped measuring"

    offenders = []
    for path in sorted(src_root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for stem in anchored:
                if f"{stem}_RE.match(" in line:
                    offenders.append(f"{path.relative_to(src_root)}:{lineno} {stem}_RE.match(")
    assert not offenders, (
        "an anchored pattern is applied with `match`, which accepts a trailing "
        "newline; use `fullmatch`:\n  " + "\n  ".join(offenders))
