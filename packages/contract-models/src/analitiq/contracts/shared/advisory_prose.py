"""The prose census datum — every piece of prose on the contract's own surface
(the field descriptions and docstring of every contract model, and the
docstring of every contract enum, in ``analitiq.contracts`` — both docstring
kinds render into the published JSON Schema; non-model, non-enum classes such
as the exception types publish no prose and are out of scope) carries a
:class:`ProseObligation` entry binding it to what enforces it, and a content
hash pinning its exact wording.

:mod:`advisory_rules` is the census of relational rules; this module is its
missing other half. The registry's tests verify the integrity of rules that
EXIST — every check starts from a registered rule, so an obligation stated in a
field description or model docstring and never registered was invisible (that
is exactly how the pagination `response.body.*` rule shipped: it lived in
``ResponseExtraction.schema``'s description, enforced by nothing).

The census is EXHAUSTIVE: every prose site — not just sites matching a
vocabulary of modal words — must carry exactly one entry in
:mod:`analitiq.contracts.shared.prose_census`, declaring one of:

- ``rule_ids`` — the ``ADV-*`` advisory rule(s) enforcing the obligation;
- ``structural`` — the model's own structure carries it (a Field pattern /
  bound / default / ``Literal``, a discriminated union, a closed
  ``extra='forbid'`` shape, a single-field validator) — the tier that renders
  into the published JSON Schema, below the advisory registry;
- ``waiver`` — why the obligation is NOT mechanisable (engine-owned at
  configure/run time, cross-document, authoring judgment);
- ``descriptive=True`` — the prose states no obligation an instance could
  violate at all: nothing to enforce, nothing to waive.

Each entry pins its prose with ``prose_hash``
(:func:`analitiq.contracts.shared.introspect.prose_fingerprint`): any wording
change breaks the pin and forces the author to re-affirm the disposition. That
hash ratchet — not modal-word detection — is what catches a new obligation
slipping into existing prose.

``descriptive=True`` versus the :data:`DESCRIPTIVE` waiver: plain
``descriptive=True`` is only for prose carrying no :data:`NORMATIVE_PATTERN`
marker. Prose that DOES carry a modal marker yet states no obligation takes
``waiver=DESCRIPTIVE`` instead — the tripwire in ``census_report`` flags
``descriptive=True`` there (its consumers, the lint suite and the CI check,
are what fail on the finding), deliberately, so marking modal text harmless
always costs an explicit, reviewable waiver.

``tests/unit/test_advisory_prose.py`` enforces the census bidirectionally
through :func:`analitiq.contracts.shared.introspect.census_report` (the same
diff ``scripts/render_prose_census.py`` prints): an uncatalogued prose site
fails the build, and so do an entry whose site disappeared, a broken hash pin,
and a tripwire hit. A waiver is therefore *data* — a declared, reviewable
state — never a comment or an absence nobody can review.

This module imports no contract models (the :mod:`advisory` convention):
entries bind to their prose sites by class name, so tooling can read the
census without pulling in pydantic. The census entries themselves live in the
:mod:`prose_census` area modules, which import this module and nothing else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# introspect's top-level imports are stdlib-only (it lazy-imports pydantic),
# so this import keeps the census readable without pulling in pydantic.
from analitiq.contracts.shared.introspect import SiteKey

#: Modal-language tripwire — FROZEN forever. The census no longer depends on
#: this pattern to find obligations (every prose site is catalogued, and the
#: per-entry ``prose_hash`` ratchet catches any rewording); its only remaining
#: job is rejecting ``descriptive=True`` on prose that carries one of these
#: markers. Its false negatives are covered by the hash ratchet — a reworded
#: site breaks its pin and forces re-affirmation — so a review proposing to
#: widen the modal set should rely on that mechanism instead of amending the
#: pattern. Two-word phrases tolerate any whitespace (docstrings wrap);
#: ``\bmust\b`` covers "must not".
NORMATIVE_PATTERN = re.compile(
    r"\bmust\b|\bevery\b|\brequires\b|\bmay\s+not\b|\bdefaults\s+to\b"
    r"|\bis\s+required\s+to\b|\bonly\b",
    re.IGNORECASE,
)

#: The exact shape of a ``prose_hash``: the first 12 hex chars of the
#: whitespace-normalized prose's sha256 (see ``introspect.prose_fingerprint``).
_PROSE_HASH_PATTERN = re.compile(r"[0-9a-f]{12}")


# --- Shared waiver reasons ---------------------------------------------------
#
# Named so the census is countable by category, and so one edit re-words a
# category everywhere. A bespoke reason is still the right choice when the
# site's situation is not one of these.

#: The contract's unknowable→skip convention, as registry data — previously it
#: lived only in the docstrings of ``resolve_read_record_schema`` and
#: ``_walk_input_schema_path``, a convention nobody could review. Where an
#: enforced rule resolves an authored path against a declared schema, a path
#: the document provably contradicts is an error, and a path the document
#: simply does not decide is SKIPPED — the engine owns the resolved shape at
#: configure time, so refusing to guess is the correct static behavior.
UNKNOWABLE_SKIP = (
    "bounded by the unknowable-skip convention: the enforcing rule checks what "
    "the document declares and skips what is statically unknowable — the "
    "engine owns the resolved shape at configure time (see "
    "resolve_read_record_schema / _walk_input_schema_path in endpoints.py)"
)

#: The prose states a default the ENGINE substitutes at configure/dispatch
#: time; the document records only absence, so there is no shape to check.
ENGINE_OWNED_DEFAULTING = (
    "engine-owned defaulting: the stated default is substituted by the engine "
    "at configure/dispatch time; the document records only the field's absence"
)

#: The sentence binds the engine's (or the producing service's) runtime
#: behavior, not a checkable shape of this document.
ENGINE_CONDUCT = (
    "engine-conduct obligation: the sentence binds the engine's or the "
    "producing service's runtime behavior, not a checkable shape of this "
    "document"
)

#: The modal word is descriptive prose (a role, a permission, a scope note);
#: the sentence states no obligation an instance could violate. This waiver —
#: not plain ``descriptive=True`` — is the required form for MODAL prose that
#: is harmless: the tripwire rejects ``descriptive=True`` wherever the live
#: prose matches :data:`NORMATIVE_PATTERN`.
DESCRIPTIVE = (
    "no obligation stated: the modal word is descriptive prose, not a "
    "requirement an instance could violate"
)


# --- Census datum ------------------------------------------------------------


@dataclass(frozen=True)
class ProseObligation:
    """One prose site, bound to what enforces it — or declared unenforceable.

    ``model`` names the class that DEFINES the prose (never an inheriting
    subclass; a name shared by two distinct prose-carrying classes is
    module-qualified, e.g. ``endpoints.RefExpression``); ``field`` is the
    model field name whose description carries it, or ``None`` for the class
    docstring. ``prose_hash`` pins the exact wording
    (``introspect.prose_fingerprint``).

    Disposition: either ``descriptive=True`` alone (the prose states no
    obligation an instance could violate — allowed only on non-modal prose;
    the tripwire in ``census_report`` surfaces a violation for the lint and
    CI to fail on), or at least one of ``rule_ids`` /
    ``structural`` / ``waiver``. A mixed description (several obligations,
    differently carried) may combine those three — the waiver then names the
    unenforced remainder.
    """

    model: str
    prose_hash: str
    field: str | None = None
    rule_ids: tuple[str, ...] = ()
    structural: str | None = None
    waiver: str | None = None
    descriptive: bool = False

    def __post_init__(self) -> None:
        if not _PROSE_HASH_PATTERN.fullmatch(self.prose_hash):
            raise ValueError(
                f"{self.site}: prose_hash must be the 12-lowercase-hex-char "
                "prose fingerprint — stamp a format-valid placeholder "
                '("0" * 12) first, then run scripts/render_prose_census.py '
                "write to restamp it with the real fingerprint (the census "
                "cannot import, so the script cannot run, while a malformed "
                "hash is in place)"
            )
        if self.descriptive:
            if self.rule_ids or self.structural or self.waiver:
                raise ValueError(
                    f"{self.site}: descriptive=True asserts the prose states "
                    "no obligation — combining it with a rule, a structural "
                    "mechanism, or a waiver contradicts that assertion"
                )
        elif not (self.rule_ids or self.structural or self.waiver):
            raise ValueError(
                f"{self.site}: an obligation must be bound to a rule, a "
                "structural mechanism, or an explicit waiver — an unbound "
                "entry declares nothing"
            )
        for value, label in ((self.structural, "structural"), (self.waiver, "waiver")):
            if value is not None and not value.strip():
                raise ValueError(f"{self.site}: empty {label} is not a declaration")

    @property
    def key(self) -> SiteKey:
        return SiteKey(model=self.model, field=self.field)

    @property
    def site(self) -> str:
        return self.key.label
