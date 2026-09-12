# I/O contracts

Pin every I/O between phases and sub-agents as a JSON Schema fragment.

## Contents

- ProviderFacts (discriminated union by kind)
- EndpointFacts (per-resource field schema — API fan-out only)
- Diagnostics
- DriftVerdict
- CreatorOutput
- EndpointCreatorOutput

## ProviderFacts (discriminated union by kind)

`ProviderFacts` is the researcher's **coverage of the published contract** —
the facts the live schemas (`connector`, `api-endpoint`,
`type-map-read`/`-write`) require in order to author a connector for the
target system. It is shaped *like* the contract, not maintained as a curated
parallel list.

Read the schema below as a **floor, not a ceiling**: it pins the facts the
pipeline depends on by name, but the researcher's mission is "ground every
fact the contract asks about." When current docs expose a contract-relevant
fact this fragment does not name, the researcher records it (alongside a
`notes` line) rather than dropping it — the contract, not this fragment, is
the source of truth for *what to know*. Per-resource response **field
schemas** (the field-level facts that decide things like datetime
zone-awareness) are not carried here; they are researched per endpoint in the
fan-out and returned as `EndpointFacts` (below).

<!-- illustrative -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["provider", "kind"],
  "properties": {
    "provider": { "type": "string" },
    "kind": { "type": "string", "enum": ["api", "database"] },
    "notes": { "type": "string" }
  },
  "oneOf": [
    {
      "properties": {
        "kind": { "const": "api" },
        "auth_model": {
          "type": "object",
          "required": ["family"],
          "properties": {
            "family": {
              "type": "string",
              "enum": [
                "api_key", "basic_auth", "oauth2_authorization_code",
                "oauth2_client_credentials", "jwt",
                "credentials", "aws_iam", "none"
              ]
            },
            "scopes": { "type": "array", "items": { "type": "string" } },
            "redirect_required": { "type": "boolean" },
            "refresh_supported": { "type": "boolean" }
          }
        },
        "base_urls": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["name", "url_or_template"],
            "properties": {
              "name": { "type": "string" },
              "url_or_template": { "type": "string" },
              "depends_on": { "type": "array", "items": { "type": "string" } }
            }
          }
        },
        "post_auth_selections": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "key": { "type": "string" },
              "label": { "type": "string" },
              "discovery_endpoint": { "type": "string" }
            }
          }
        },
        "discovery_endpoints": {
          "type": "array",
          "description": "Dynamic POST-AUTH discovery probes only (e.g. a call that resolves a per-tenant api_domain). NOT the list of data resources to author endpoints for — that is `resources`.",
          "items": {
            "type": "object",
            "properties": {
              "purpose": { "type": "string" },
              "method": { "type": "string" },
              "path": { "type": "string" }
            }
          }
        },
        "resources": {
          "type": "array",
          "description": "The data resources the connector should expose — the domain branch's resource list. The orchestrator enumerates these into the endpoint fan-out worklist; each becomes one `endpoint-creator` branch fed by its own `EndpointFacts`. Carries only what the domain pass can know without deep-diving each resource's fields.",
          "items": {
            "type": "object",
            "required": ["key"],
            "properties": {
              "key": { "type": "string", "description": "Stable resource id — becomes the endpoint_id and the on-disk filename `endpoints/{key}.json` (pattern ^[a-z0-9][a-z0-9_-]*$). DERIVE it, do not free-pick it (`RULE-ENDP-046`): flatten the resource's full locator, lowercased, with `__` between path levels so the full path — not just the leaf — distinguishes the id. API: every path segment in order, with path-params dropped (they are operation-level) — `/v1/blah/something/customer` → `v1__blah__something__customer`, `/v2/blah/something/customer` → `v2__blah__something__customer`. Deterministic: the same locator yields the same key on every re-author. (Database resources are not authored here — `RULE-DBEP-006`; the ids runtime discovery derives are opaque handles over a verbatim `database_object` — `RULE-DBEP-007`.)" },
              "label": { "type": "string" },
              "method": { "type": "string" },
              "path": { "type": "string" },
              "paginated": { "type": "boolean", "description": "Whether this resource's list operation paginates (style is the connector-level `pagination`)." },
              "writable": { "type": "boolean", "description": "Whether the provider documents a write (insert/upsert) for this resource." },
              "replication_cursor": { "type": "string", "description": "Field usable as an incremental/replication cursor, when the resource supports one; else absent." }
            }
          }
        },
        "native_type_vocabulary": {
          "type": "array",
          "description": "Connector-wide set of native wire-type tokens observed across the provider's resources (e.g. `string`, `integer`, `date-time`, `number`, `boolean`, provider-specific scalar names). Researched at the domain level so the creator can author a COMPLETE `type-map-read` before fan-out — every endpoint field must resolve through that map (`RULE-PKG-033`). A genuinely new native surfaced by an endpoint is a domain-level type-map addition, never an endpoint-local one.",
          "items": { "type": "string" }
        },
        "pagination": {
          "type": "object",
          "properties": {
            "style": { "type": "string", "enum": ["offset", "page", "cursor", "link", "keyset"] },
            "params": { "type": "array", "items": { "type": "string" } }
          }
        },
        "rate_limit": {
          "type": "object",
          "properties": {
            "max_requests": { "type": "integer" },
            "time_window_seconds": { "type": "integer" }
          }
        },
        "documented_http_errors": {
          "type": "object",
          "description": "Per-status documented meaning, verbatim, from the provider's own API docs (an errors/status-codes reference page, or per-endpoint error notes) — the creator's grounding for `error_map.http` (`references/error-classification.md`). One entry per status the docs describe; never a failure category — that judgment is the creator's, per `references/error-classification.md`'s procedure. No status may be classified in `error_map.http` without a corresponding entry here. `error_map.http` carries no per-endpoint scoping, so when the docs establish DIFFERENT meanings for the same status depending on the call (an operation, an endpoint), the entry must say so rather than picking one — record every documented meaning and which call(s) each applies to, verbatim, so the creator can see the conflict and refuse to classify that status generically instead of silently applying whichever meaning happened to be recorded.",
          "additionalProperties": { "type": "string" }
        }
      },
      "required": ["auth_model"]
    },
    {
      "properties": {
        "kind": { "const": "database" },
        "driver": { "type": "string" },
        "transport_family": {
          "type": "string",
          "enum": ["sqlalchemy", "adbc", "flight_sql", "jdbc", "odbc", "mongodb"]
        },
        "adbc_driver_package": {
          "type": "string",
          "description": "First-class ADBC driver wheel when one exists (e.g. 'adbc-driver-postgresql'); absent when the system has no production ADBC driver. Drives step 1 of the driver-selection decision order."
        },
        "flight_sql_endpoint": {
          "type": "boolean",
          "description": "True when the server exposes an Arrow Flight SQL endpoint (step 2 of the decision order — generic adbc-driver-flightsql)."
        },
        "bulk_load_protocol": {
          "type": "string",
          "description": "The system's native bulk-load protocol when no ADBC driver exists, as the vendor documents it (e.g. 'LOAD DATA LOCAL INFILE', 'COPY FROM stdin BINARY'). A driver-side executemany tuning knob (SQL Server's fast_executemany, oracledb's arraysize) is NOT one — record it in notes instead, since it makes the ordinary executemany landing fast rather than adding a protocol. Informs step 3 of the driver-selection order, but only a protocol in the closed sql_capabilities.bulk_load vocabulary can actually be declared; anything else lands via executemany at tier 4."
        },
        "sql_write_path": {
          "type": ["object", "null"],
          "description": "Documented facts the creator needs to declare sql_capabilities (connector-spec-db/spec-sql-write-path.md). The engine refuses, it does not guess, so an ungrounded fact must be left unset and reported as a gap rather than assumed. Two signals, deliberately distinct: OMIT a field the docs do not establish (the creator reports a research gap), and use NULL only where the field admits it to record a documented ABSENCE — `upsert_grammar: null` means the system documents no native upsert, which is a fact the creator maps to merge_form 'none'.",
          "properties": {
            "upsert_grammar": {
              "type": ["string", "null"],
              "description": "The system's documented native upsert statement, verbatim (e.g. 'INSERT ... ON CONFLICT DO UPDATE', 'INSERT ... ON DUPLICATE KEY UPDATE', 'MERGE'); null when the system documents none."
            },
            "catalog_model": {
              "type": "string",
              "description": "How the system addresses catalogs/databases: whether ONE CONNECTION can reference across them (the deciding fact — not merely whether a database level exists above the schema, which it does on Postgres and MySQL without being cross-addressable), and whether the docs permit creating/dropping them."
            },
            "qualified_statement_targeting": {
              "type": "boolean",
              "description": "True when statements may fully qualify the target (schema.table); false when the write target must be established as session state (e.g. USE / search_path)."
            },
            "temp_table_support": {
              "type": "string",
              "description": "The documented session/transaction-scoped temporary relation syntax, or a note that the system has none (which forces a real staging table)."
            },
            "transactional_ddl": {
              "type": "boolean",
              "description": "Whether CREATE/DROP TABLE participate in a transaction. False for engines documenting an implicit commit on DDL (MySQL). Omit when the docs do not establish it — a boolean has no 'documented absence' state, so omission is the only unknown signal, and this is a correctness fact the creator must not assume."
            },
            "identifier_limits": {
              "type": "object",
              "description": "Documented driver/engine caps: max identifier length in bytes, max bind parameters per statement. Omit a cap the docs do not state.",
              "properties": {
                "max_identifier_len": { "type": "integer" },
                "max_bind_params": { "type": "integer" }
              }
            }
          }
        },
        "error_signals": {
          "type": ["object", "null"],
          "description": "Documented facts about how the driver's exception(s) expose a native error signal, and what each signal value means — the creator's grounding for `error_map.key_attrs`/`codes` (`references/error-classification.md`). The researcher reports only what the docs establish exists, verbatim, never a failure category — that judgment is the creator's; a signal's meaning is grounded from whichever source actually defines it (the driver's own docs, or the database/server's own published catalog when the server owns that meaning, per `documented_codes` below). Same omit-vs-null discipline as `sql_write_path`: OMIT a fact the docs do not establish (a research gap), and use NULL only where a field admits it to record a documented ABSENCE.",
          "properties": {
            "native_code_attrs": {
              "type": ["array", "null"],
              "items": { "type": "string" },
              "description": "Attribute name(s) the driver's exception class documents as carrying its native error code (e.g. `sqlstate`, `pgcode`, `errno`), most-specific first when the docs establish more than one. NULL when the docs establish the exception carries no such attribute at all — the fact that routes the creator to the `__exception_class__` sentinel instead."
            },
            "documented_codes": {
              "type": "object",
              "description": "The documented meaning, verbatim, from whichever source actually defines it — the driver's own docs for how it exposes a signal, or, for what a given code VALUE means, the database/server's own published documentation when the server (not the driver) owns that catalog (e.g. a SQLSTATE class). One entry per signal value grounded above: a native-code value (e.g. `\"23505\": \"unique_violation\"`) for anything `native_code_attrs` names, and/or a documented exception class name (e.g. `\"IntegrityError\": \"constraint violation\"`) for anything worth classifying by the exception's type instead. Both kinds of key may appear in the same map: a driver whose docs ground an attribute AND a meaningful class hierarchy reports both, grounding the creator's mixed `key_attrs` (an attribute plus the `__exception_class__` fallback, `references/error-classification.md`). `error_map` carries no per-driver scoping, so when a connector ships more than one driver (`skills/connector-spec-db/spec-driver-selection.md`) and their docs establish DIFFERENT meanings for what would resolve to the same key — most often a shared exception class name under `__exception_class__` — the entry must say so rather than picking one: record every documented meaning and which driver each applies to, verbatim, so the creator can see the conflict and refuse that entry instead of silently applying whichever meaning happened to be recorded. Never a failure category either way; the creator applies `references/error-classification.md`'s procedure to produce `error_map.codes`.",
              "additionalProperties": { "type": "string" }
            }
          }
        },
        "sqlalchemy_driver": {
          "type": "string",
          "description": "The SQLAlchemy 'dialect+driver' for the SQLAlchemy transport, sync or async (e.g. 'postgresql+asyncpg', 'mysql+aiomysql', 'redshift+redshift_connector'); connector-spec-db/spec-driver-selection.md owns which forms are authorable and the sync/async dispatch constraints."
        },
        "dsn": {
          "type": "object",
          "properties": {
            "url_template_example": { "type": "string" },
            "logical_fields": { "type": "array", "items": { "type": "string" } }
          }
        },
        "tls": {
          "type": ["object", "null"],
          "properties": {
            "supported_modes": { "type": "array", "items": { "type": "string" } }
          }
        },
        "native_types": {
          "type": "array",
          "items": { "type": "string" }
        },
        "default_port": { "type": "integer" }
      },
      "required": ["driver", "transport_family"]
    }
  ]
}
```

## EndpointFacts (per-resource field schema — API fan-out only)

One `EndpointFacts` object per data resource: the researcher's
**per-endpoint** pass in the fan-out grounds the researched fields, the
orchestrator injects the connector-level `pagination` (echoed from
`ProviderFacts.pagination`), and `endpoint-creator` consumes it. It carries
the field-level truths about one resource's fields — those a read returns and
those a write accepts — including whether each
datetime field is zone-aware, which decides `Timestamp(MICROSECOND, UTC)`
versus the naive `Timestamp(MICROSECOND)`. Every field fact is grounded on
the resource's own documentation / a real sample; an
`endpoint-creator` dispatched without `EndpointFacts` refuses (it has no web
access and may not guess field types).

<!-- illustrative -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["resource", "fields"],
  "properties": {
    "resource": { "type": "string", "description": "Resource key — the exact `ProviderFacts.resources[].key` for this resource (already the derived full-locator id; see its rule) and becomes the endpoint_id verbatim. Do not re-derive, shorten, or alter it here." },
    "method": { "type": "string" },
    "path": { "type": "string" },
    "paginated": { "type": "boolean", "description": "Whether this resource's list operation paginates." },
    "pagination": {
      "type": "object",
      "description": "The connector-wide pagination style + params (echoed from ProviderFacts.pagination into the branch), so endpoint-creator — which sees only EndpointFacts + the connector body — can author the per-endpoint pagination block. Present whenever `paginated` is true.",
      "properties": {
        "style": { "type": "string", "description": "The style `ProviderFacts.pagination.style` enumerates." },
        "params": { "type": "array", "items": { "type": "string" } }
      }
    },
    "replication_cursor": { "type": "string", "description": "Field usable as an incremental cursor, when the resource supports one." },
    "record_path": { "type": "string", "description": "Path to the iterable record collection in the response body (informs response.records, e.g. `response.body.data`)." },
    "writable": { "type": "boolean" },
    "conflict_keys": { "type": "array", "items": { "type": "string" }, "description": "Provider-documented natural key for upsert, when the resource is upsertable." },
    "idempotency": {
      "type": "object",
      "required": ["in", "name"],
      "description": "Provider-documented idempotency-key placement for this resource's write operation, when the provider exposes one. Placement only — the key value is engine-owned. endpoint-creator carries `in`/`name` into the write mode's `idempotency` block; `required` informs whether to declare it on `upsert`.",
      "properties": {
        "in": { "type": "string", "description": "Where the provider accepts the key, from the placement vocabulary `RULE-ENDP-039` renders — a body placement means a top-level JSON body field, never a nested one." },
        "name": { "type": "string", "minLength": 1, "description": "The documented header name (e.g. `Idempotency-Key`) or top-level body field name (e.g. `idempotency_key`), verbatim." },
        "required": { "type": "boolean", "description": "true when the provider mandates the key on this operation (e.g. Square UpsertCatalogObject)." }
      }
    },
    "fields": {
      "type": "array",
      "minItems": 1,
      "description": "One entry per field the connector exposes for this resource — the fields a read returns and the fields a write accepts, each entry naming which of the two it belongs to. `native_type` must be a token covered by ProviderFacts.native_type_vocabulary; `arrow_type` is the canonical Arrow type the field resolves to. A field the provider types differently by direction is recorded as an entry per direction, so an entry always carries one pair.",
      "items": {
        "type": "object",
        "required": ["name", "directions"],
        "dependentRequired": { "native_type": ["arrow_type"], "arrow_type": ["native_type"] },
        "properties": {
          "name": { "type": "string" },
          "directions": { "type": "array", "minItems": 1, "items": { "type": "string", "enum": ["read", "write"] }, "description": "Which sides of the resource carry this field. A generated id the provider returns and refuses on a write is `[\"read\"]`; a credential it accepts and never echoes is `[\"write\"]`. The creator takes the read subset into `response.schema` and the write subset into the write mode's `input.schema`, so a field is never advertised on a side the provider does not have it on. An entry carrying a `sample_value` names exactly one direction — the one whose payload the value came out of — so a field sampled on both sides is an entry per side (`RULE-SHRD-002`)." },
          "required_in_modes": { "type": "array", "items": { "type": "string" }, "description": "The write modes that REQUIRE this field, from the same vocabulary as `write_modes`. The creator puts a field in a mode's `input.schema.required` exactly when that mode is named here, so a provider-mandatory field is never advertised as optional and a record missing it is refused at authoring rather than by the provider. An empty array — or the key omitted — means no mode requires it." },
          "write_modes": { "type": "array", "minItems": 1, "items": { "type": "string" }, "description": "Which write modes accept this field, when the provider does not accept the same set for all of them (an `insert` that takes fields an `upsert` refuses). Each name is a mode key from the vocabulary `RULE-ENDP-053` prints, never a provider's own word for the operation. Omitted means every mode the resource declares. Only meaningful on an entry whose `directions` include `write`." },
          "native_type": { "type": "string", "description": "Provider's documented/observed wire-type token (e.g. `string`, `integer`, `date-time`). Omitted, with `arrow_type`, for a field whose wire type the provider documents nowhere — the creator then leaves that node untyped rather than guessing (`RULE-ENDP-006`, `RULE-ENDP-062`), and `notes` says which fields those were." },
          "arrow_type": { "type": "string", "description": "Canonical Arrow type (PascalCase). For temporals, chosen from the SAMPLE value's zone-awareness (`RULE-SHRD-002`): a zoneless wire value → bare `Timestamp(<unit>)`; a value carrying an offset/Z → `Timestamp(<unit>, UTC)`." },
          "nullable": { "type": "boolean" },
          "enum": { "type": "array", "items": { "type": "string" }, "description": "Closed value domain, when the field is enumerated in the docs." },
          "format": { "type": "string", "description": "Documented string format (e.g. `email`, `uri`, `uuid`, `date`)." },
          "sample_value": { "description": "One value for this field copied verbatim out of a real payload, recorded in the JSON kind the provider sends it as — the string `\"0\"` and the boolean `false` are different evidence. Never composed to match the declared type, and a placeholder a documentation renderer synthesized is not a wire value. Carry it for any field a payload shows one for; REQUIRED for a temporal, whose zone-awareness is decided on it rather than guessed (`RULE-SHRD-002`). The read operation's record payload is evidence for `read`; a write mode's input payload is evidence for `write`; a body a read sends to filter its search is evidence for neither." },
          "tz_aware": { "type": "boolean", "description": "For date-time fields: true iff the wire value carries a zone/offset." }
        }
      }
    },
    "notes": { "type": "string" }
  }
}
```

## Diagnostics

<!-- illustrative -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["passed", "findings"],
  "properties": {
    "passed": { "type": "boolean" },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["message_id", "kind", "path", "message"],
        "properties": {
          "rule": {
            "type": "string",
            "description": "The rule id this finding concerns; absent when none does."
          },
          "message_id": {
            "type": "string",
            "description": "Which of a rule's distinct complaints this is."
          },
          "kind": { "type": "string", "enum": ["fail", "notApplicable", "informational"] },
          "severity": {
            "type": "string",
            "enum": ["error", "warning"]
          },
          "path": { "type": "string", "description": "JSON pointer into the document" },
          "message": { "type": "string" }
        },
        "allOf": [
          {
            "if": { "properties": { "kind": { "const": "fail" } }, "required": ["kind"] },
            "then": { "required": ["severity"] },
            "else": { "not": { "required": ["severity"] } }
          }
        ]
      }
    }
  }
}
```

This is `finding()`'s current shape; the `analitiq-validator` release this
skill's self-install line pins predates it and still returns the original
shape — `validator` (a coarse per-check category), `severity`, `path`,
`message`, with no `rule`, `message_id` or `kind` at all. A finding carrying
`validator` instead of `rule`/`message_id`/`kind` is that older shape, not a
malformed one — treat both until the pin catches up to a release carrying this
schema.

## DriftVerdict

<!-- BEGIN GENERATED: drift-verdict-envelope -->
<!-- illustrative -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["bump", "previous_version", "next_version", "rationale"],
  "properties": {
    "bump": { "type": "string", "enum": ["patch", "minor", "major", "none"] },
    "previous_version": { "type": "string" },
    "next_version": { "type": "string" },
    "rationale": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["change_path", "category"],
        "properties": {
          "change_path": { "type": "string" },
          "category": {
            "type": "string",
            "enum": [
              "input-removed", "input-renamed", "input-type-changed",
              "input-enum-narrowed", "storage-changed",
              "non-optional-input-added", "auth-shape-changed",
              "discovery-shape-changed", "sql-capabilities-changed",
              "endpoint-removed", "write-mode-removed",
              "record-field-removed", "record-field-type-changed",
              "filter-operators-narrowed", "conflict-keys-changed",
              "endpoint-capability-narrowed", "type-map-rule-removed",
              "type-map-render-changed", "optional-input-added",
              "optional-output-added", "optional-endpoint-added",
              "write-mode-added", "record-field-added",
              "filter-operators-widened", "endpoint-obligation-added",
              "endpoint-capability-added", "type-map-rule-added", "bug-fix",
              "doc-fix", "tuning", "capability-block-added",
              "type-map-rule-reordered"
            ]
          },
          "note": { "type": "string" }
        }
      }
    }
  }
}
```
<!-- END GENERATED: drift-verdict-envelope -->

## CreatorOutput

Returned by `api-connector-creator` and `db-connector-creator`.

<!-- illustrative -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["connector", "type_map_read"],
  "properties": {
    "connector": {
      "anyOf": [
        { "type": "object", "description": "Assembled connector body, ready for validation against https://schemas.analitiq.ai/connector/latest.json." },
        { "type": "null", "description": "Returned by stub agents (e.g. storage-connector-creator) that decline to author." }
      ]
    },
    "type_map_read": {
      "anyOf": [
        {
          "$ref": "https://schemas.analitiq.ai/type-map-read/latest.json",
          "description": "On-disk shape of the standalone type-map-read.json (native → Arrow): `native_type` is the matcher (regex patterns authored UPPERCASE) and `arrow_type` is the rendered Arrow type (may carry ${name} substitutions backed by named captures in `native_type`). Written by the orchestrator to {connector_id}/definition/type-map-read.json."
        },
        { "type": "null", "description": "Returned by stub agents that decline to author." }
      ]
    },
    "type_map_write": {
      "anyOf": [
        {
          "$ref": "https://schemas.analitiq.ai/type-map-write/latest.json",
          "description": "On-disk shape of the standalone type-map-write.json (Arrow → native DDL render rules). Which kinds must ship it, and which must not: `RULE-PKG-030`. Same rule shape as the read map but the direction inverts: `arrow_type` is the matcher (regex with ECMA named captures for parameterized types) and `native_type` is the rendered DDL (may carry ${name} substitutions backed by captures in `arrow_type`). Arrow-vocabulary coverage, and when a family may be left unrendered: `RULE-TMAP-019`. Written to {connector_id}/definition/type-map-write.json."
        },
        { "type": "null", "description": "kind=api connectors and stub agents return null — the write direction is a database-package concept." }
      ]
    },
    "package_files": {
      "anyOf": [
        {
          "type": "object",
          "required": ["connector_py", "init_py", "requirements_txt", "pyproject_toml"],
          "additionalProperties": false,
          "description": "Python package files for kind=database connectors — the connector root IS the package (`RULE-PKG-002`). MUST be null for kind=api. Written by the orchestrator to {connector_id}/connector.py, __init__.py, requirements.txt, pyproject.toml. Contents follow the connector-package contract in connector-spec-db/spec-connector-package.md, whose section Enforcement owns how they are checked.",
          "properties": {
            "connector_py":     { "type": "string", "minLength": 1, "description": "{Name}Dialect(SqlDialect) + {Name}Connector(GenericSQLConnector) — `RULE-PKG-010`; imports: `RULE-PKG-011`." },
            "init_py":          { "type": "string", "minLength": 1, "description": "See `RULE-PKG-009`." },
            "requirements_txt": { "type": "string", "minLength": 1, "description": "See `RULE-PKG-027`." },
            "pyproject_toml":   { "type": "string", "minLength": 1, "description": "Names derived from connector_id: `RULE-PKG-007`. Dependencies: `RULE-PKG-006`. Entry-point groups: `RULE-PKG-008`. Copy the template in connector-spec-db/spec-connector-package.md." }
          }
        },
        { "type": "null", "description": "kind=api connectors and stub agents return null — API connectors carry only the definition." }
      ]
    },
    "notes": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Human-readable notes the orchestrator should surface (e.g. fields the creator could not populate from ProviderFacts)."
    }
  }
}
```

## EndpointCreatorOutput

<!-- illustrative -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["endpoint_files"],
  "properties": {
    "endpoint_files": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["endpoint_id", "document"],
        "properties": {
          "endpoint_id": {
            "type": "string",
            "pattern": "^[a-z0-9][a-z0-9_-]*$",
            "description": "Stable endpoint identifier; mirrors document.endpoint_id and is used by the orchestrator to derive the on-disk filename."
          },
          "document": {
            "type": "object",
            "description": "One endpoint document body. Must validate against https://schemas.analitiq.ai/api-endpoint/latest.json and carry the same endpoint_id at its top level."
          }
        }
      }
    }
  }
}
```
