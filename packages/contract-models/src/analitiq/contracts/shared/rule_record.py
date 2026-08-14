"""The rule record — one datum, loaded from the registry, never hand-written here.

The registry is ``rules/records/*.yaml`` at the repo root: one YAML file per rule,
schema in ``rules/SCHEMA.md``. That record is the source of truth. Everything
else — the published references, the plugin prose, the checker bindings, this
package's runtime enforcement — is generated from it or validated against it.

The wheel cannot read the YAML: parsing it would put a YAML dependency in a
package the engine installs, for data that never changes at run time. So
``scripts/render_rules.py`` compiles the registry into :data:`RULES_PATH`, a
JSON document shipped beside this module and read with the standard library.
That projection is a *pinned copy*, not a second source: ``render_rules.py
check`` re-compiles and fails on any difference, the way ``render_schemas.py``
already guards ``schemas/``.

Three axes, deliberately independent — collapsing them is what made the old
single-tier registry unable to describe most of its own rules:

``tier``
    What kind of rule it is: a shape, an agreement between fields, an agreement
    between artifacts, a procedure, a judgment.
``validator``
    What applies the rule without a human deciding to: the importable symbol
    that rejects a violation. Absent means nothing here applies it, which is
    what :attr:`RuleRecord.mechanized` reads. A rule is not a lesser rule for
    having no validator — it is one whose enforcement lives where this repo
    cannot reach.
``severity``
    What a violation costs.

This module imports no contract models: records bind to their target classes by
*name*, so tooling can read the whole registry without pulling in pydantic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: The compiled registry this package ships. Written by `scripts/render_rules.py`.
RULES_PATH = Path(__file__).with_name("rules.json")

# --- Closed vocabularies ----------------------------------------------------

STRUCTURAL_TIER = "structural"
ADVISORY_TIER = "advisory"
REFERENTIAL_TIER = "referential"
PROCEDURAL_TIER = "procedural"
JUDGMENT_TIER = "judgment"

#: The tiers a record may take. ``descriptive`` is in the vocabulary and is
#: deliberately absent here: prose that states no obligation
#: has no rule to register, so a record claiming it is refused rather than
#: stored (see :meth:`RuleRecord._validate`). The name exists in `SCHEMA.md` so
#: "this states nothing" stays a verdict someone writes down instead of a
#: silence nobody can review.
TIERS = (
    STRUCTURAL_TIER,
    ADVISORY_TIER,
    REFERENTIAL_TIER,
    PROCEDURAL_TIER,
    JUDGMENT_TIER,
)
DESCRIPTIVE_TIER = "descriptive"

SEVERITIES = ("error", "warning", "info")

#: A rule's lifecycle, and the reason no record carries a boolean: `active` is
#: not the opposite of one thing. `draft` is written down but not yet in force;
#: `deprecated` is still in force and on its way out, so prose citing it still
#: resolves while authors are moved off it; `retired` no longer binds and its
#: record survives only to keep the id from being reused.
STATUSES = ("draft", "active", "deprecated", "retired")

#: Who applies the rule and decides a change to it. A list — plenty of rules
#: bind more than one component, and a type map is authored by both plugins.
#: These are this repo's actual parts, not teams: `engine` covers the engine
#: and the CDK it publishes, including registry CI and the conformance kit.
OWNERS = ("engine", "connector-plugin", "pipeline-plugin")

#: Artifact kinds a rule can bind. `any` is for the rules that bind every
#: authored document — a `$schema` convention, a secret never appearing
#: literally — which would otherwise be filed under whichever document happened
#: to state them first.
#:
#: A record names one or more, because binding two artifacts is a thing a rule
#: does and a scalar could not say: a rule over a request slot that a stream
#: also fills used to have to over-claim `any` or pick one kind and silently
#: miss the other author. Plural is also what lets the rendered reference fan a
#: rule out to every agent it binds without any record naming an agent.
SCOPES = (
    "connector",
    "connector-package",
    "api-endpoint",
    "database-endpoint",
    "type-map",
    "stream",
    "pipeline",
    "connection",
    # A server-side response shape, in the registry because it is part of the
    # published contract surface even though neither plugin authors one.
    "data-sync-run-status",
    "any",
)

#: Which shape device a structural rule is ABOUT — not merely which one the
#: target happens to carry, because a model usually carries several. It decides
#: whether the rendered reference prints that rule's vocabulary off the live
#: model: `literal_enum` says the members ARE the rule, so print them.
#:
#: The distinction is not derivable, which is why an author writes it down.
#: `Schedule.type` is a `Literal` with a default, so a rule about the legal
#: values it declares and a rule about omitting fields that default read the
#: same annotation and want opposite answers — `literal_enum` for the first,
#: `default` for the second. Deriving from the annotation would print a
#: vocabulary that is true and beside the point.
MECHANISMS = (
    "literal_enum",
    "discriminated_union",
    "pattern",
    "closed_object",
    "default",
)

#: Ids retired before the registry had files, so no record on disk remembers
#: them. A live record normally carries `status: retired` and guards its own id;
#: these have nothing to carry it, and an id is never reissued — it appears in
#: findings and archived diagnostics, where reuse silently re-points every
#: stored occurrence at a different rule.
RETIRED_BEFORE_THE_REGISTRY = (
    # `exactly one of expression or constant`, retired in 1.0.0rc19 when
    # `AssignmentValue` became a `kind`-discriminated union, so the union states
    # the rule and no validator enforces it.
    "RULE-STRM-008",
    # `conflict_keys required for a connection-scope upsert, forbidden
    # otherwise` and `a database destination's write.mode belongs to the closed
    # database vocabulary`: the destination became an `endpoint_ref.scope`-tagged
    # union whose database branch is itself `mode`-discriminated, so both are now
    # the shape rather than a check over it.
    "RULE-STRM-011",
    "RULE-STRM-013",
)

#: RFC 2119 keywords, which `statement` must use in caps. Checked rather than
#: assumed: a statement with no keyword is usually a description that drifted
#: into the registry, and the tier vocabulary has a name for that.
RFC2119 = ("MUST NOT", "MUST", "SHOULD NOT", "SHOULD", "MAY NOT", "MAY")


# --- The record -------------------------------------------------------------


@dataclass(frozen=True)
class RuleRecord:
    """One rule, as authored in `rules/records/<id>.yaml`.

    Field meanings are `rules/SCHEMA.md`; this class is where the ones a record
    settles ALONE are enforced — shapes, closed vocabularies, and the internal
    agreements between its own fields. Construction is the gate for those, so
    no consumer re-checks them.

    What a record cannot settle alone is checked by `scripts/render_rules.py`
    when it compiles the registry: that the filename matches the id, that no id
    repeats or reissues a retired one, that the binding names a symbol inside
    `analitiq.`, and that the symbol resolves against the live models and
    validator. Those run at build time and not when the wheel loads
    `rules.json`, so a record read back from the compiled copy has been through
    this class only.
    """

    id: str
    statement: str
    tier: str
    severity: str
    #: Every artifact kind the rule binds, from :data:`SCOPES`. A list because a
    #: rule can grade more than one kind of document, and the rendered reference
    #: is split by scope: a scalar here would decide, silently, which of two
    #: authors never meets the rule.
    scopes: tuple[str, ...]
    rationale: str
    #: What applies it: `dotted.module::Symbol.attr`, naming the module that is
    #: imported so the value means the same thing here as it does wherever this
    #: record is read. Lint-resolved, so a renamed validator fails the build
    #: instead of leaving a record claiming an enforcement it lost.
    validator: str | None = None
    owners: tuple[str, ...] = ()
    status: str = "active"
    #: Every model class the rule binds, matched against the whole MRO. Wider
    #: than `validator`, which names one representative symbol: a rule over a
    #: discriminated union lists every branch, and a rule naming no validator
    #: still names the models it governs.
    targets: tuple[str, ...] = ()
    #: Fields on those classes that carry the rule, for a structural rule whose
    #: `mechanism` the rendered reference reads members off.
    fields: tuple[str, ...] = ()
    #: Which shape device a structural rule is about, from :data:`MECHANISMS`.
    mechanism: str | None = None
    #: The concrete model the shared fixture corpus validates against. Naming
    #: one is how a rule joins the corpus, so membership is a thing the record
    #: says rather than a thing derived from how the rule happens to be
    #: written. Absent means the rule ships no fixtures — the tests assert both
    #: directions, so a corpus directory without this key is an orphan and this
    #: key without a corpus directory is a gap.
    fixture_model: str | None = None
    superseded_by: str | None = None

    #: The fields a record authors as a YAML list and this class holds as a
    #: tuple. Normalized here rather than by each loader, so a caller that
    #: hands over a list gets a record that is still frozen and comparable.
    _SEQUENCES = ("scopes", "owners", "targets", "fields")

    def __post_init__(self) -> None:
        for name in self._SEQUENCES:
            value = getattr(self, name)
            if isinstance(value, str):
                # `tuple("Foo")` is `('F', 'o', 'o')` — a YAML scalar where a
                # list belongs would bind the rule to three one-letter model
                # names and match nothing, silently.
                self._fail(f"{name} is a list of names, not the string {value!r}")
            if isinstance(value, dict):
                # `tuple(dict)` is the dict's KEYS — a YAML mapping where a
                # list belongs would validate on the key names alone, with
                # the author's value structure silently discarded.
                self._fail(f"{name} is a list of names, not the mapping {value!r}")
            object.__setattr__(self, name, tuple(value) if value else ())
        self._validate()

    def _fail(self, message: str) -> None:
        raise ValueError(f"{self.id}: {message}")

    def _validate(self) -> None:
        if self.tier == DESCRIPTIVE_TIER:
            self._fail(
                f"'{DESCRIPTIVE_TIER}' is the disposition of prose that states "
                "no obligation an instance could violate, so there is no rule "
                "to register. Leave the sentence where it is."
            )
        for value, allowed, label in (
            (self.tier, TIERS, "tier"),
            (self.severity, SEVERITIES, "severity"),
            (self.status, STATUSES, "status"),
        ):
            if value not in allowed:
                self._fail(f"unknown {label} {value!r}; expected one of {allowed}")
        for text, label in ((self.statement, "statement"), (self.rationale, "rationale")):
            if not (text or "").strip():
                self._fail(f"{label} is empty")
        if not any(k in self.statement for k in RFC2119):
            self._fail(
                "statement carries no RFC 2119 keyword in caps. A rule states an "
                f"obligation; if this one does not, it is not a rule. ({RFC2119})"
            )
        for name in self._SEQUENCES:
            bad = [v for v in getattr(self, name) if not isinstance(v, str)]
            if bad:
                self._fail(f"{name} carries non-name entries {bad!r}")
        if self.fixture_model is not None and not isinstance(self.fixture_model, str):
            self._fail(f"fixture_model is one name or absent, not {self.fixture_model!r}")
        if self.mechanism is not None and self.mechanism not in MECHANISMS:
            # A closed set, checked like every other one the record carries.
            # Untyped it was a live hole rather than a tidiness point: one
            # member is load-bearing, and a record whose `mechanism` misses it
            # by a character still compiles, still renders, and stops being
            # graded — the vocabulary guard and the no-restatement guard both
            # select on that spelling, and the reference prints a dash where
            # the members belong.
            self._fail(
                f"unknown mechanism {self.mechanism!r}; expected one of {MECHANISMS}"
            )
        if not self.scopes:
            self._fail(f"name the artifact kind(s) this rule binds — from {SCOPES}")
        unknown = [s for s in self.scopes if s not in SCOPES]
        if unknown:
            self._fail(f"unknown scope(s) {unknown}; expected from {SCOPES}")
        if len(set(self.scopes)) != len(self.scopes):
            # A repeat renders the rule into the same file twice. Cheap to
            # refuse here, invisible in a diff of the rendered reference.
            self._fail(f"scopes repeats an entry: {list(self.scopes)}")
        if "any" in self.scopes and len(self.scopes) > 1:
            # `any` already means every authored document, so naming it beside
            # a specific kind states a narrower claim the renderer will ignore.
            self._fail(
                f"scopes names 'any' beside {[s for s in self.scopes if s != 'any']} — "
                "'any' already covers every authored document; drop one or the other"
            )
        if not self.owners:
            self._fail(f"name who applies this rule — one or more of {OWNERS}")
        unknown = [o for o in self.owners if o not in OWNERS]
        if unknown:
            self._fail(f"unknown owner(s) {unknown}; expected from {OWNERS}")
        if self.validator:
            # The binding names what is IMPORTED, so the left half is a dotted
            # identifier chain and nothing else. A path was the earlier form:
            # it shipped in this package's rules.json pointing into a tree no
            # consumer has, and the lint derived a module from it by slicing
            # rather than by resolving — so a path that never existed resolved
            # cleanly. Refusing every non-identifier module half refuses that
            # whole shape, including a file extension, a separator, and a
            # markdown document standing in for a mechanism.
            dotted, separator, _ = self.validator.partition("::")
            if not separator or not all(
                part.isidentifier() for part in dotted.split(".")
            ):
                self._fail(
                    f"validator {self.validator!r} is not "
                    "dotted.module::Symbol — bind the module that is imported "
                    "(analitiq.contracts.x::Symbol), never a path to a file"
                )
        if self.status == "retired" and not self.superseded_by:
            self._fail(
                "a retired rule names the id that replaced it — a retirement "
                "with no successor leaves every prose site citing it with "
                "nowhere to go"
            )
        if self.superseded_by == self.id:
            self._fail("a rule cannot supersede itself")

    # --- Derived ------------------------------------------------------------

    @property
    def mechanized(self) -> bool:
        """Whether anything applies this rule without a human deciding to.

        Derived, because it was never a second fact: `validator` names the
        symbol that applies the rule, so "is it applied" is "is that named".
        Authored, the two could only ever agree, which made the field one
        chance per record to state the same thing twice and a lint whose whole
        job was catching one of them typed wrong.
        """
        return bool(self.validator)

    @property
    def validator_symbol(self) -> str | None:
        return self.validator.split("::", 1)[1] if self.validator else None

    @property
    def validator_module(self) -> str | None:
        """The importable module half of the binding.

        A symbol alone does not identify an enforcer: a bare class name can be
        declared by more than one contract module, and `TemplateExpression` is.
        Callers matching a record to a live method compare this too, or two
        classes sharing a name share each other's records.
        """
        return self.validator.split("::", 1)[0] if self.validator else None


# --- Loading ----------------------------------------------------------------


def load_records(path: Path | None = None) -> list[RuleRecord]:
    """Read the compiled registry. Every record is validated on construction."""
    source = path or RULES_PATH
    payload = json.loads(source.read_text(encoding="utf-8"))
    records = [RuleRecord(**r) for r in payload["rules"]]
    seen: dict[str, RuleRecord] = {}
    for record in records:
        if record.id in seen:
            raise ValueError(f"duplicate rule id {record.id}")
        seen[record.id] = record
    return records
