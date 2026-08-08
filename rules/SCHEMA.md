# The rule registry — record schema

One YAML file per rule under `rules/adv/`, named for its id. The record is the
source of truth: the published references, the plugin prose, the validator
bindings and this repo's runtime enforcement are all generated from it or
validated against it, never the reverse.

A rule is added by writing a file here. Nothing else is edited by hand.

```
rules/
  SCHEMA.md            this file — the record schema
  adv/
    ADV-CTOR-004.yaml  one file per rule, named for its id
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
| `id` | yes | `ADV-<AREA>-NNN`. Immutable, never reused — it appears in validator findings and archived diagnostics, so reissuing one silently re-points every stored occurrence. The filename must match it. |
| `statement` | yes | The normative sentence, RFC 2119 keywords in caps. Self-contained: someone reading only this understands the obligation. Never restates a value the contract owns — `targets` and `fields` point at what does. |
| `tier` | yes | What *kind* of rule it is. See below. |
| `severity` | yes | `error` \| `warning` \| `info`. What a violation costs — independent of what enforces it. |
| `scope` | yes | The artifact kind it binds, from the vocabulary `SCOPES` declares in `analitiq.contracts.shared.rule_record` — one member per published resource, plus `any` for the rules that bind every authored document. A scope decides which block of a plugin's generated reference the rule lands in; who the rule is rendered *to* is `owners`. |
| `mechanized` | yes | Whether anything applies the rule without a human deciding to. Not the same question as whether `validator` is set — see below. |
| `validator` | when mechanized | What applies it: `path/to/module.py::Symbol.attr` for code, or a `.claude/rules/*.md` path for an agent rule. Lint-resolved either way — a renamed validator or a deleted rules file fails the build instead of leaving a record claiming an enforcement it lost. `null` when nothing does. |
| `owners` | yes | Who applies the rule and decides a change to it, as a list of `engine`, `connector-plugin`, `pipeline-plugin`. More than one is normal: a type map is authored by both plugins and executed by the engine. |
| `targets` | no | Every model class the rule binds, matched against the whole MRO. Wider than `validator`, which names one representative symbol: a rule over a discriminated union lists every branch, and an unmechanized rule still names the models it governs. Read by the enforcer census and the reachability tests, which require every one to carry the member `validator` names. |
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

Tier is the rule's *nature*, `mechanized`/`validator` its enforcement,
`severity` its cost. Keeping the three apart is the point: a `referential` rule
may be mechanized or not, and an unmechanized rule is not a lesser rule — it is
one whose enforcement lives somewhere this repo cannot reach.

## `mechanized` and `validator` are two questions

`mechanized` asks whether anything applies the rule without a human deciding
to. `validator` names what. They are separate because the mechanism is not
always code: an agent rule under `.claude/rules/` binds every edit an agent
makes to the paths it declares, and has no symbol to point at — so a record can
be `mechanized: true` with a `.md` path, and the lint resolves that by checking
the file exists.

| `mechanized` | `validator` | Means |
|---|---|---|
| `true` | a `.py::Symbol` | code rejects a violation — a model validator, a field annotation, a class whose shape *is* the rule |
| `true` | a `.claude/rules/*.md` | an agent rule applies it on every edit to the paths that file governs |
| `false` | `null` | nothing here applies it; `rationale` says what would have to be read, and how far away that is |

`validator` set with `mechanized: false` is refused: it claims an enforcement
and denies it in the same record.

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
- `packages/contract-models/tests/unit/test_advisory_registry.py` — what
  `render_rules.py` cannot see from a record alone: that every target carries
  the member `validator` names, that every model validator on a contract model
  is some rule's enforcer or carries a written exemption, that a retired id is
  never reissued, and that each rule naming a `fixture_model` is rejected by its
  own invalid fixtures and by no other constraint.
- `tests/connector_builder/test_rule_reachability.py` — every id a plugin's
  prose cites is readable inside that plugin.
