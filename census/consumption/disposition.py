"""The reachability census datum — what an UNREAD contract field means.

The engine publishes ``contract-consumption``, the fields its run-time path
reads (``census.consumption.pin`` vendors one pinned version). A field a root
reaches that the manifest does not claim is *unread*: the contract declares
it, the published schema renders it, plugin prose teaches it, authors write
it, and no engine path looks at it. That is a fact the engine states and
this repo decides the meaning of, field by field, in a
:class:`FieldDisposition` entry. An unread field with no entry fails the
build; an entry for a field the manifest now claims — or that no longer
exists — fails the build too, so the census can only ever say what is
currently true.

One disposition per kind of consumer an unread field may have:

- ``authoring_only`` — the field is consumed off the run-time path by design:
  by a person reading the document, by plugin prose, by the validator, or by
  the schema renderer. The ``reason`` names that consumer.
- ``structural`` — the model's own parsing consumes it: a discriminator the
  union dispatches on, a literal the schema pins. No attribute read exists
  because pydantic settled the value before the engine held the object.
- ``engine_gap`` — the contract permits something the engine ignores and
  should honour. The request goes out, just not the one the author wrote.
  The ``reason`` states what an author writing the field expects and what
  happens instead; it is the declared, reviewable state that replaces "found
  sixteen schema versions later", and the entry is what gets filed against
  the engine.
- ``contract_surplus`` — the contract declares something neither side
  needs: a knob nothing honours and nothing should, a value the document
  already states elsewhere, a setting meaningless in the shape it sits on.
  The fix is on this side — the field is removed, a breaking change to the
  resource that carries it — and the ``reason`` states why removal rather
  than adoption is the answer. An entry of this kind is a decision recorded,
  not a gap waiting on the engine.

Like the prose census, this module imports no contract models: entries name
a model by its ``analitiq.contracts``-relative dotted path and a field by
name, so the guard can be read without pydantic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DispositionKind = Literal[
    "authoring_only", "structural", "engine_gap", "contract_surplus"
]

#: Named reasons, so the census is countable by category and one edit
#: re-words a category everywhere. A bespoke ``reason`` is still right when a
#: field's situation is not one of these.

#: Human-facing metadata: labels and descriptions a person reads in a catalog
#: or a diff; nothing at run time branches on them.
HUMAN_METADATA = (
    "human-facing metadata: read by a person in the catalog, the registry "
    "diff and the plugin's own summaries, never by the run-time path"
)

#: A discriminator: the value selects which union member pydantic builds, so
#: the engine holds the member and never reads the tag.
UNION_DISCRIMINATOR = (
    "union discriminator: the value selects the member pydantic builds, so "
    "the engine holds the member type and never reads the tag"
)


@dataclass(frozen=True)
class FieldDisposition:
    """One unread field, bound to what consumes it — or declared a gap.

    ``model`` is the ``analitiq.contracts``-relative dotted path of the class
    that DECLARES the field (``endpoints.Param``, ``pipelines.config.Logging``,
    ``stream.StreamSource``); ``field`` is the field name on that model.
    ``kind`` is the disposition and ``reason`` the sentence a reviewer reads
    to judge it. A ``reason`` is required for every kind: an ``engine_gap``
    with no stated consequence is an alarm with no text, and an
    ``authoring_only`` with no named consumer is a waiver of nothing.
    """

    model: str
    field: str
    kind: DispositionKind
    reason: str

    def __post_init__(self) -> None:
        if self.kind not in (
            "authoring_only", "structural", "engine_gap", "contract_surplus"
        ):
            raise ValueError(f"{self.model}.{self.field}: unknown kind {self.kind!r}")
        if not self.reason.strip():
            raise ValueError(f"{self.model}.{self.field}: a reason is required")
        if not self.model or "." not in self.model:
            raise ValueError(
                f"{self.model!r}: model must be an analitiq.contracts-relative "
                "dotted path such as 'endpoints.Param'"
            )

    @property
    def qualified_model(self) -> str:
        return f"analitiq.contracts.{self.model}"
