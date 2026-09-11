# The rule registry — record schema

One YAML file per rule under `rules/records/`, named for its id. The record is the
source of truth: the published references, the plugin prose, the validator
bindings and this repo's runtime enforcement are all generated from it or
validated against it, never the reverse.

A rule is added by writing a file here. Nothing else is edited by hand.

```
rules/
  SCHEMA.md            this file — the record schema
  records/
    RULE-CTOR-004.yaml  one file per rule, named for its id
    …
```

One file per rule rather than one registry document: an id is immutable and
outlives every wording it ever had, so its history should be readable on its
own, and two authors adding rules should never touch the same bytes.

## Fields

Every field here has a consumer or a guard. That is the admission test — a
field nothing reads is a second place to keep something true, which is what
this registry exists to remove (`.claude/rules/no-drift-surfaces.md`).

| Field | Required | What it is |
|---|---|---|
| `id` | yes | `RULE-<AREA>-NNN`. Immutable, never reused — it appears in validator findings and archived diagnostics, so reissuing one silently re-points every stored occurrence. The filename must match it. |
| `statement` | yes | The normative sentence, RFC 2119 keywords in caps. Self-contained: someone reading only this understands the obligation. Never restates a value the contract owns — `targets` and `fields` point at what does. |
| `tier` | yes | What *kind* of rule it is. See below. |
| `severity` | yes | `error` \| `warning` \| `info`. What a violation costs — independent of what enforces it, except that a rule a pydantic validator applies is always `error`: that enforcer can only reject the document, so a lower cost on the record is one no document ever pays. `test_a_rule_bound_to_a_pydantic_validator_costs_an_error` in `packages/contract-models/tests/unit/test_rule_registry.py` pins it. Whether a change to this field moves the packages' major version is decided by `.claude/rules/validator-verdict-stability.md`, which is also where a finding's own severity is confined to `error` \| `warning` regardless of what a record declares. Once a check derives a `fail` finding's severity from the violated record rather than a literal at the call site (see Findings, below), a call site naming its own severity becomes a second copy of a fact this field already owns. |
| `scopes` | yes | The artifact kinds it binds, as a list from the vocabulary `SCOPES` declares in `analitiq.contracts.shared.rule_record` — each member names the kind of document whose author the rule binds, plus `any` for the rules that bind every authored document. A list because a rule can grade more than one kind, and the generated reference is split by scope: a scalar scope makes it decide, silently, which of two authors never meets the rule. `any` may not appear beside a named kind — it already covers every document — and an entry may not repeat. It is not derived from the published resources and does not track them: `connector-package` is a repository an author lays out and no resource renders, `type-map` covers both a read map and a write map, and several published resources have no member because no rule has needed one. A member is added when a rule does. Scopes decide which FILE of a plugin's generated reference set the rule lands in; who the rule is rendered *to* is `owners`. |
| `validator` | no | What applies it: `dotted.module::Symbol.attr`. It names the module that is **imported**, under `analitiq.`, never a path to a file standing in for one — the record ships in `rules.json`, where a repo path resolves for nobody, and a path is checked by slicing rather than by importing, so one that never existed passes. The module half must be a dotted identifier chain, which is how a path is refused whatever it ends in. It lands in one of two packages, decided by how much the check must see: a rule one document settles alone is a `@model_validator` in `contract-models`, and a rule needing a second document in hand is a check in `validator`, bound to the function that emits the finding. An enforcer that reports `warning` is a check in `validator` whatever it needs to see: a `@model_validator` can only reject, so it cannot carry a severity below `error` (`.claude/rules/validator-verdict-stability.md`). Lint-resolved by import, so a renamed validator fails the build instead of leaving a record claiming an enforcement it lost. `null` when nothing does. |
| `owners` | yes | Who applies the rule and decides a change to it, as a list of `engine`, `connector-plugin`, `pipeline-plugin`. More than one is normal: a type map is authored by both plugins and executed by the engine. |
| `targets` | no | Every model class the rule binds, matched against the whole MRO. Wider than `validator`, which names one representative symbol: a rule over a discriminated union lists every branch, and a rule with no validator still names the models it governs. Read by the enforcer census and the reachability tests, which require every one to carry the member `validator` names. |
| `fields` | no | The model fields a structural rule's `mechanism` rides on, so the rendered reference can print the members off the live model instead of restating them. Resolved against the target, so a renamed field fails the build. |
| `symbol` | no | The dotted `module::NAME` holding the constant a `mechanism: pattern` or `mechanism: reserved_names` rule is about — a regex, or a frozenset of forbidden literal strings — so the rendered reference can print the form the statement points at. Read from the record rather than off the field, because a field carries one device per rule that grades it and nothing in the shape says which is whose — printing them all under each rule states what the field must satisfy and never what the rule requires. Resolved by import like `validator`, so a renamed constant fails the build. Refused on a record whose `mechanism` is anything else. |
| `mechanism` | no | Which shape device a structural rule is **about**, from the vocabulary `MECHANISMS` declares in `analitiq.contracts.shared.rule_record` — not merely which one the target carries, since a model usually carries several. `literal_enum` says the members *are* the rule, and is what makes the rendered reference print them off the live model. Not derivable, which is why it is written down: `Schedule.type` is a `Literal` with a default, so a rule about omitting fields that default and a rule about an enum's legal values read the same annotation and want opposite answers. |
| `fixture_model` | no | The concrete model the shared fixture corpus validates against. Naming one is how a rule joins the corpus; absent means it ships no fixtures, and the tests assert both directions. |
| `rationale` | yes | Why the rule exists, and — when nothing mechanizes it — what would have to be read to catch a violation, and how far away that is. |
| `status` | yes | `draft` \| `active` \| `deprecated` \| `retired`. The lifecycle, and the reason no record carries a boolean — `active` is not the opposite of any one thing. A `draft` is written down but not yet in force; a `deprecated` rule still binds while authors are moved off it, so prose citing it still resolves; a `retired` record stays on disk because the id must never be reused, and the record is the only thing that proves it was taken. |
| `superseded_by` | no | The id that replaced this one. Required when `status: retired`. |

### Deliberately absent

Recorded so nobody re-adds them thinking they were forgotten:

- **`title`** — a second wording of `statement`, to be kept in sync by hand,
  read by nothing.
- **`since`**, **`references`** — both derivable. Git owns when a rule
  appeared; a citation *is* the id appearing in prose, and
  `tests/registry/test_rule_reachability.py` derives that mapping in
  the direction that matters (every cited id is readable where it is cited).
- **`examples`** — an example nothing validates is exactly the rot
  `.claude/rules/plugin-prose.md` warns about: a "don't do this" sample that
  quietly becomes valid. Examples live in `examples/` trees CI runs through the
  validator, or in annotated fences; the prose that cites a rule is where its
  example belongs.
- **`applies_when`** — no consumer. A rule that binds conditionally says so in
  its statement.
- **`data`** — a nested bag the four binding keys above once sat in. A free
  mapping is a key nothing checks: a misspelled `fixture_models` read as "ships
  no fixtures" and a scalar where a list belongs bound the rule to one-letter
  model names, both silently. Flat, every key is a declared field, so the
  dataclass refuses an unknown one the way it already refuses a misspelled
  `severity`.
- **`kind`** — a name for which generic check to run, back when a closed
  vocabulary of relational checks was dispatched from the record. Enforcement is
  ordinary Python now, so a rule is applied by the symbol `validator` names or
  by nothing; there is no third state for a key to select. A key naming a
  dispatch target is a key a typo can silently disable. A *finding's* `kind`
  (below) is a different axis on a different object — whether a check found a
  violation at all, not which check to run — and does not revive this one.

## `tier` — what kind of rule

Tier states first whether the rule constrains the *artifact* being authored
or the *author* authoring it, then which one of those it is.

| Axis | Tier | The rule says | Typically |
|---|---|---|---|
| Constrains an artifact | `shape` | one artifact has this form | a `Literal`, a pattern, a bound, a required field, a closed object, a discriminated union — usually rendered into the published JSON Schema, but a rule about what a connector's `connector.py` may contain is shape too |
| | `coherence` | fields *within* one document must agree | set-equality, disjointness, membership, cross-key uniqueness — what stock JSON Schema cannot express |
| | `reference` | this artifact must agree with another one | a `connector_id` against its directory name, a stream field against the endpoint declaring it, a declared capability against the hook implementing it |
| Constrains the author | `process` | the author must do something in a particular way or order | what is regenerated after what, what is never hand-edited, what the engine owns and so is never authored |
| | `choice` | several authorings all validate and one is right | driver selection, sync versus async, whether a system's catalog level is addressable |

Tier is the rule's *nature*, `validator` its enforcement, `severity` its cost.
Keeping the three apart is the point: a `reference` rule may name a validator
or not, and one that names none is not a lesser rule — it is one whose
enforcement lives somewhere this repo cannot reach.

## `validator` — what applies the rule

The binding is an importable symbol, and the lint imports it.

| `validator` | Means |
|---|---|
| a `module::Symbol` | code rejects a violation — a model validator, a field annotation, a class whose shape *is* the rule, or a cross-document check in `analitiq.validator` |
| `null` | nothing here applies it; `enforcement_location` (below, once it exists) will name where the obligation is actually checked instead, and `rationale` stays the prose that justifies that answer |

A prose document is not a second form of this. A record ships to PyPI inside
`rules.json`, so a repo path in it resolves for nobody who reads it there, and
`mechanized` would report an applied rule to a consumer who cannot see, load or
run the thing applying it. The rules an agent applies reach that agent by being
rendered into the plugin prose it loads, which is a mechanism the plugin
references and `test_rule_reachability.py` already keep honest.

`RuleRecord.mechanized` reads this and nothing else. It was an authored field
once, and could only ever restate what `validator` already said — a record
naming a mechanism is a record with one — so it was one copy of that fact per
record and a lint whose whole job was catching a copy typed wrong.

One further word, **`descriptive`**, names prose that states no obligation an
instance could violate. It is not a tier a record may take — such a sentence
has no rule to register and stays prose. The name exists so "this states
nothing" is a verdict someone writes down rather than a silence nobody reviews;
`RuleRecord` refuses a record claiming it, and says so.

## `enforcement_location` — where a `null` validator leaves off

Not yet a field `RuleRecord` accepts, the way `status: draft` names a record
written down but not yet in force. Once it is, it is required whenever
`validator` is `null` **and** `status` is `active` or `deprecated` — a record
currently binding an author. A `draft` names no obligation yet and a `retired`
names one no longer live, so neither has anywhere to check, and this field
does not force one on them. It closes the question `validator` leaves open for
every record that does bind: not *what* rejects a violation, but *where* the
obligation is checked at all.

| `enforcement_location` | Means |
|---|---|
| `engine` | the obligation is enforced outside this repo, by the Analitiq engine at run time; the record's own `rationale` states the reading that rests on (`.claude/rules/engine-behaviour-claims.md`) |
| `external-ci` | a mechanical check exists but runs outside both this repo and the engine's runtime path — the CDK conformance kit DIP CI runs against a published connector, say |
| `platform-save` | a mechanical check exists but runs outside this repo, the engine, and CI — the managed platform's own backend, when an author saves the artifact (a connection's authored value against its connector's declared type, a stream's field reference against the endpoint document it names) |
| `authoring-practice` | no mechanical check exists anywhere; an author or a reviewing agent satisfies it by judgment, the rule's citation in plugin prose being the only guard |
| `unenforced` | a known gap — nothing anywhere catches a violation yet |

A non-null `validator` already answers where the rule is enforced, so the
field carries nothing further once one is present. `enforcement_location` is
the machine-readable answer for the records `validator` cannot answer for;
`rationale` stays the prose that justifies it, not a second place the answer
itself lives.

## Severity

| | Meaning |
|---|---|
| `error` | the artifact is wrong; a violation produces a broken connector, pipeline or run |
| `warning` | legal but very likely a mistake — a coverage gap, a shape that works today and will not survive the next case |
| `info` | a convention worth stating and citing, whose violation costs only consistency |

## Findings — what a check reports

A finding is not a record; it is one thing a check said about one document,
raised by a field constraint or `@model_validator` in `analitiq.contracts`, or
produced by a check in `analitiq.validator`. This is the shape the registry is
moving to: today,
`finding()` in `analitiq.validator._core` returns `validator`, `severity`,
`path` and `message`, with `validator` naming the check's own category rather
than the rule id. Once it does, every finding carries:

| Field | What it is |
|---|---|
| `rule` | The id of the record it concerns. |
| `message_id` | Which of a rule's distinct complaints this is — a rule can fail in more than one way, and a consumer branches on this rather than parsing `message`. Immutable and never reused within its namespace once assigned, for the reason `id` is: a consumer that branches on it is a stored or routed decision a rename or reuse silently repoints. A finding's `rule` is that namespace; a finding with no `rule` (below) draws instead from one shared framework namespace, under the same guarantee. |
| `kind` | `fail` \| `notApplicable` \| `informational`. See below. |
| `severity` | `error` \| `warning`, the violated record's own `severity`, present only when `kind` is `fail`. A record's `info` never reaches a finding as this field (`.claude/rules/validator-verdict-stability.md`): a detected `info`-tier violation surfaces as `kind: informational` naming that rule instead, so nothing that carries `severity` costs less than `warning`. |
| `path` | Where in the document the finding applies. |
| `message` | The human-readable complaint. |

The pipeline plugin's own adapter (`plugins/analitiq-pipeline-builder/scripts/validate.py`)
mints ids of its own — outside `analitiq.validator`, documented in
`plugins/analitiq-pipeline-builder/skills/pipeline-builder/references/io-contracts.md`
— and is not this shape either before or after; this registry does not govern
it.

### `kind`

| `kind` | Means |
|---|---|
| `fail` | the document violates the rule; `severity` names the cost |
| `notApplicable` | the check knew which rule it was evaluating but could not evaluate the document against it — a sibling file was unreachable, a path could not be resolved — and reports nothing about whether that rule holds. It still carries `rule`: a consumer that cannot tell which obligation went unchecked cannot route or count it. |
| `informational` | something worth surfacing whose cost is not `error`/`warning` — either nothing here violates a rule at all (a default was silently applied, say), or the violated rule is `info`-tier, whose citation carries what a `severity` field would |

A `notApplicable` or `informational` finding never carries a `severity` of
its own, but a `notApplicable` finding is not costless: an unchecked
`error`-tier rule is not a rule that held, and the overall verdict says so.
**`passed` is `true` only when no finding is `kind: fail` with `severity:
error`, and every finding that is `kind: notApplicable` names a `rule` whose
own `severity` is not `error`.** A `notApplicable` naming no `rule` at all — a check that failed before it
even identified what it was attempting, one of the cases named below —
cannot clear that bar and always costs: the less a finding says about what it
missed, the less room there is to call the miss safe. A
`notApplicable` against a named `warning`- or `info`-tier rule costs nothing,
the same as a `fail` against one would.

`rule` is absent only in the cases named below, and no others — a check that
cannot identify which rule it was attempting, never one that knows and simply
could not run it this time:

| Case | `kind` | `severity` |
|---|---|---|
| A structural precondition rejected the document before any rule-specific check could run, and no record claims it — a contract model's own field constraint, or a hand-written shape guard ahead of a check with no model behind it (a pipeline bundle malformed enough that no referential check could run over it, say) | `fail` | `error` |
| No detector recognised the document | `fail` | `error` |
| A check failed before it identified which rule applies | `notApplicable` | none |
| Something worth surfacing about how a check proceeded, with no rule to bind it — a direction guessed from an ambiguous filename, say | `informational` | none |

The rows above are the framework reporting something no rule describes, not a
new kind of rule — and not license to leave `rule` off anywhere else a check
emits one.

## Guards

- `scripts/render_rules.py check` — validates every record against this schema,
  imports every `validator` binding and resolves the symbol against the live
  models and the live validator, refuses a duplicate or reissued id, and fails
  when the compiled projection the wheel ships is stale.
- `packages/contract-models/tests/unit/test_rule_registry.py` — what
  `render_rules.py` cannot see from a record alone: that every target carries
  the member `validator` names, that every model validator on a contract model
  is some rule's enforcer or carries a written exemption, that a record bound
  to a pydantic validator — on the class it names, or on a mixin its targets
  inherit — declares `error`, that a retired id is never reissued, and that
  each rule naming a `fixture_model` is rejected by its own invalid fixtures
  and by no other constraint.
- `packages/validator/tests/test_check_registry_census.py` — the same
  enforcer→registry direction over the other enforcement home: every check id
  `analitiq.validator` registers is emitted by a function some record binds, or
  carries a written exemption. The census above walks contract classes, so it
  cannot see a cross-document check.
- `tests/registry/test_rule_reachability.py` — every id a plugin's
  prose cites is readable inside that plugin.
