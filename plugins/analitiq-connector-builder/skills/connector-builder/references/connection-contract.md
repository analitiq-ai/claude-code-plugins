# Connection contract — outer shape

Creator agents emit the same outer `connection_contract` shape; the concrete
inputs differ. Its field set, their types and which are mandatory are
`ConnectionContract` in the published connector schema — read them there rather
than from a copy here.

## Contents

- Per-input fields (`ConnectionContractInput`)
- API vs DB inputs
- What belongs in the contract at all
- Post-auth outputs
- Cross-input validation (`validation`)
- Drift detection

## Per-input fields (`ConnectionContractInput`)

`source`, `phase`, `storage` and `type` each draw from a closed vocabulary the
model declares (`RULE-CTOR-021`). Read the members off that rule's Values column
in `rules/connector.md`, which prints them from the live model; what each choice *decides*
is below.

| Field | What the choice decides |
|---|---|
| `source` | Who supplies the value — the end user filling in the connection form, or the platform/admin provisioning it. |
| `phase` | When the value has to be available. An input a transport references must declare a phase no later than that transport's first use (`RULE-CTOR-050`); `lifecycle-phases.md` walks the ordering. |
| `storage` | Which durable store the resolved value lands in, and so the prefix of the reference path every other document targets. The post-auth stores are produced by `post_auth_outputs` (below), never collected as an input. |
| `type` | Not an Arrow type — that vocabulary describes data coming back from a resource, not configuration going in. |

`secret` tracks `storage` (`RULE-CONN-003`). `enum` is the authoritative
allowed-value list, and both the input's `default` and any `ui.options` picker
are graded against it (`RULE-CONN-002`, `RULE-CONN-001`).

## API vs DB inputs

- **API connectors** — typically declare `api_key`, OAuth `client_id`/`client_secret`, and any tenant/account identifiers the provider requires.
- **DB connectors** — typically declare `host`, `port`, `database`, `username`, `password`, `ssl_mode`, `ssl_ca_certificate`.

## What belongs in the contract at all

**A connector must not own customer-specific values** (`RULE-CTOR-028`). A host,
tenant id, account id, database name, profile, credential, or token belongs to
a *connection*, not to the connector — the connector declares the *shape* of
the input, and the connection supplies the value. A connector carrying a real
customer's host is not reusable, which is the whole point of the split. (This
is broader than "no secrets": a tenant slug isn't secret and still doesn't
belong.)

**Place a value by who owns it, not by who first references it.** The question
is never "where is this interpolated from?" but "whose value is this?":

| Value | Where |
|---|---|
| Secret supplied by the end user or platform | input, `storage: "secrets"`, `secret: true` |
| Non-secret value the user supplies before auth | input, `storage: "connection.parameters"` |
| A choice the user makes from a post-auth list | `post_auth_outputs`, `mode: "user_selection"` |
| A value read from a post-auth probe | `post_auth_outputs`, `mode: "auto_discovery"` |
| Operational tunable (API version, timeout, page size, warehouse) | a `default` in the contract, overridable in `connection.parameters` — not a hardcoded literal buried in a transport |

## Post-auth outputs

`post_auth_outputs` are the single source of truth for durable post-auth
context. `RULE-CTOR-022` carries the vocabulary for `mode`, `storage` and `type`
alike, printed from the live model in `rules/connector.md`. Which pairings of `mode` and
`storage` are legal, and each mode's required and forbidden request fields, are
`RULE-CTOR-002`.

`value_path`, `label_path` and `options_path` are **response-extraction
paths** — fields read out of the `options_request` / `discovery_request`
response (e.g. `"id"` for a selection option, `"company_domain"` for a discovery
field). None of them is the materialized reference path.

That path is **derived** as `storage` + `"."` + the output key — e.g. an output
keyed `api_domain` with `storage: "connection.discovered"` materializes at
`connection.discovered.api_domain`, which is what refs and
`required_for_activation` target (`RULE-CTOR-007`).

**Don't hide a non-secret value in `secrets`.** An output's storage must
reflect what the value *is*. Routing a tenant domain or account id through
`secrets` because it "feels safer" makes it unreadable to the refs that need it
and misreports the connector's secret surface. (`RULE-CTOR-002` enforces the
mode↔storage pairing; it cannot tell whether a value is truly secret.)

**Don't rely on output ordering** (`RULE-CTOR-038`). Author each output so it
stands on its own: never write one that quietly depends on another having
already run, and don't build a chain of outputs referencing each other's
values.

## Cross-input validation (`validation`)

`validation.rules[]` expresses conditional requirements *between inputs* — "if
the user picked X, then Y is required and Z is meaningless". The rule shape and
the predicate operator set are contract-owned (`RULE-CTOR-012`, plus
`RULE-CTOR-008`/`RULE-CTOR-009` requiring every referenced field to be a
declared input); what the contract can't tell you is where the boundary sits.

`present` is the operator whose name misleads: it fires on a **non-empty**
value, not on a key the form merely submitted. Write the `message` for the
person filling in the form, naming the field they must fix.

**Scope boundary.** These predicates are for *cross-input* validation only —
relationships among values already on the form. They are not a place to express:

- provider reachability or credential correctness (that's `auth.test`),
- anything requiring a network call (OAuth callbacks, post-auth probes —
  those are `post_auth_outputs`),
- runtime connection health.

If a rule can't be decided from the submitted inputs alone, it doesn't belong
here.

## Drift detection

Contract drift is versioned by the connector's top-level `version` semver —
bump rules: `metadata-and-versioning.md` §Release version (`version`).
