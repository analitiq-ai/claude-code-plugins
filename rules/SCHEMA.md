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
| `severity` | yes | `error` \| `warning` \| `info`. What a violation costs — independent of what enforces it. |
| `scope` | yes | The artifact kind it binds, from the vocabulary `SCOPES` declares in `analitiq.contracts.shared.rule_record` — one member per published resource, plus `any` for the rules that bind every authored document. A scope decides which block of a plugin's generated reference the rule lands in; who the rule is rendered *to* is `owners`. |
| `validator` | no | What applies it: `dotted.module::Symbol.attr` for code, or a `.claude/rules/*.md` path for an agent rule. A code binding names the module that is **imported**, under `analitiq.`, never a source path standing in for one — the record ships in `rules.json`, where a repo path resolves for nobody, and a path is checked by slicing rather than by importing, so one that never existed passes. It lands in one of two packages, decided by how much the check must see: a rule one document settles alone is a `@model_validator` in `contract-models`, and a rule needing a second document in hand is a check in `validator`, bound to the function that emits the finding. Lint-resolved either way — a renamed validator or a deleted rules file fails the build instead of leaving a record claiming an enforcement it lost. `null` when nothing does. |
| `owners` | yes | Who applies the rule and decides a change to it, as a list of `engine`, `connector-plugin`, `pipeline-plugin`. More than one is normal: a type map is authored by both plugins and executed by the engine. |
| `targets` | no | Every model class the rule binds, matched against the whole MRO. Wider than `validator`, which names one representative symbol: a rule over a discriminated union lists every branch, and a rule with no validator still names the models it governs. Read by the enforcer census and the reachability tests, which require every one to carry the member `validator` names. |
| `fields` | no | The model fields a structural rule's `mechanism` rides on, so the rendered reference can print the members off the live model instead of restating them. |
| `mechanism` | no | Which shape device carries a structural rule — `literal_enum`, `discriminated_union`, `pattern`, `closed_object`, `default`. |
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
  `tests/connector_builder/test_rule_reachability.py` derives that mapping in
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
  dispatch target is a key a typo can silently disable.

## `tier` — what kind of rule

| Tier | The rule says | Typically |
|---|---|---|
| `structural` | one artifact has this shape | a `Literal`, a pattern, a bound, a required field, a closed object, a discriminated union — usually rendered into the published JSON Schema, but a rule about what a connector's `connector.py` may contain is structural too |
| `advisory` | fields *within* one document must agree | set-equality, disjointness, membership, cross-key uniqueness — what stock JSON Schema cannot express |
| `referential` | this artifact must agree with another one | a `connector_id` against its directory name, a stream field against the endpoint declaring it, a declared capability against the hook implementing it |
| `procedural` | the author must do something in a particular way or order | what is regenerated after what, what is never hand-edited, what the engine owns and so is never authored |
| `judgment` | several authorings all validate and one is right | driver selection, sync versus async, whether a system's catalog level is addressable |

Tier is the rule's *nature*, `validator` its enforcement,
`severity` its cost. Keeping the three apart is the point: a `referential` rule
may name a validator or not, and one that names none is not a lesser rule — it is
one whose enforcement lives somewhere this repo cannot reach.

## `validator` — what applies the rule

The mechanism is not always code, so the binding takes either form, and the
lint resolves both: a symbol must exist, a file must exist.

| `validator` | Means |
|---|---|
| a `module::Symbol` | code rejects a violation — a model validator, a field annotation, a class whose shape *is* the rule, or a cross-document check in `analitiq.validator` |
| a `.claude/rules/*.md` | an agent rule applies it on every edit to the paths that file governs |
| `null` | nothing here applies it; `rationale` says what would have to be read, and how far away that is |

`RuleRecord.mechanized` reads this and nothing else. It was an authored field
once, and could only ever restate what `validator` already said — a record
naming a mechanism is a record with one, in both forms — so it was one copy of
that fact per record and a lint whose whole job was catching a copy typed wrong.

One further word, **`descriptive`**, names prose that states no obligation an
instance could violate. It is not a tier a record may take — such a sentence
has no rule to register and stays prose. The name exists so "this states
nothing" is a verdict someone writes down rather than a silence nobody reviews;
`RuleRecord` refuses a record claiming it, and says so.

## Severity

| | Meaning |
|---|---|
| `error` | the artifact is wrong; a violation produces a broken connector, pipeline or run |
| `warning` | legal but very likely a mistake — a coverage gap, a shape that works today and will not survive the next case |
| `info` | a convention worth stating and citing, whose violation costs only consistency |

## Guards

- `scripts/render_rules.py check` — validates every record against this schema,
  resolves every `validator` binding (a code symbol against the live models, an
  agent rule against the filesystem), refuses a duplicate or reissued id, and
  fails when the compiled projection the wheel ships is stale.
- `packages/contract-models/tests/unit/test_rule_registry.py` — what
  `render_rules.py` cannot see from a record alone: that every target carries
  the member `validator` names, that every model validator on a contract model
  is some rule's enforcer or carries a written exemption, that a retired id is
  never reissued, and that each rule naming a `fixture_model` is rejected by its
  own invalid fixtures and by no other constraint.
- `packages/validator/tests/test_check_registry_census.py` — the same
  enforcer→registry direction over the other enforcement home: every check id
  `analitiq.validator` registers is emitted by a function some record binds, or
  carries a written exemption. The census above walks contract classes, so it
  cannot see a cross-document check.
- `tests/connector_builder/test_rule_reachability.py` — every id a plugin's
  prose cites is readable inside that plugin.
