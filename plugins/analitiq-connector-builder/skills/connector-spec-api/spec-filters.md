# Filters: which operator reaches the wire, and how

A read operation's `filters` map is what makes a stream able to narrow the read.
It says which record fields are filterable, and — per operator — which declared
param carries the comparison. A filter's value arrives per run, and a per-run
value reaches the request only through a declared param (`RULE-ENDP-032`), so
every operator offered has to bind one (`RULE-ENDP-065`); an operator with no
entry is one no stream is told it may use.

Read `spec-request-binding.md` first: a filter binding names a param, and the
param still reaches the wire the ordinary way.

## Contents

- The three places, again
- Keys are record fields, not param names
- What a filter entry is not
- What you cannot author, and why
- Which operators exist

## The three places, again

Same shape as pagination and replication. A filter binding does not create a
request binding on its own — the param it names must be:

1. **declared** in `params`,
2. **named** by an operator under `filters.<record field>`, and
3. **bound** into the request with `{"from_param": …}`.

The entry itself is just the param's name.

The param must not carry `controlled_by`: pagination and replication set their
params on every request, so a filter routed to one is overwritten
(`RULE-ENDP-002`).

<!-- validate: api-endpoint#/operations/read -->
```json
{
  "params": {
    "min_amount": { "in": "query", "type": "number", "required": false },
    "max_amount": { "in": "query", "type": "number", "required": false },
    "status": { "in": "query", "type": "string", "required": false }
  },
  "request": {
    "method": "GET",
    "path": "/v1/invoices",
    "query": {
      "amount_from": { "from_param": "min_amount" },
      "amount_to": { "from_param": "max_amount" },
      "status": { "from_param": "status" }
    }
  },
  "filters": {
    "amount": { "gt": "min_amount", "lt": "max_amount" },
    "status": { "eq": "status" }
  },
  "response": {
    "records": { "ref": "response.body.data" },
    "schema": {
      "type": "object",
      "properties": {
        "data": {
          "type": "array",
          "native_type": "array",
          "arrow_type": "List",
          "items": {
            "type": "object",
            "native_type": "object",
            "arrow_type": "Object",
            "properties": {
              "amount": { "type": "number", "native_type": "number", "arrow_type": "Float64" },
              "status": { "type": "string", "native_type": "string", "arrow_type": "Utf8" }
            }
          }
        }
      }
    }
  }
}
```

The map is what tells a stream it may narrow `amount` with `gt` and with `lt`,
and it gives each its own param, so each comparison has somewhere of its own to
go. That is the whole point of it.

## Keys are record fields, not param names

A `filters` key is a dotted path into the record shape `response.records`
selects, and it must resolve there (`RULE-ENDP-068`) — the same resolution a
replication `cursor_field` gets. A stream's `field` names the record field it
narrows on, and the map is what turns that into a request. The param name is an
implementation detail of this endpoint and never leaves it.

## What a filter entry is not

An entry is a param name. It does not render anything: `request.query` /
`headers` / `path_params` already own how a param's value reaches the wire, and
a second shaping slot here would be a second binding grammar for the same job.

That has a consequence worth knowing before you pick a provider shape. A
provider that spells the comparison **inside** the value — `amount=<>0`,
`q=created>2020-01-01`, `$filter=amount gt 100` — cannot be expressed: nothing
in the document can put a fixed prefix around a per-run value, and no resolution
scope carries a stream's filter value for a template to read. Offer the
operators the provider takes as its own parameters, and raise the gap for the
rest rather than smuggling the comparison through as part of the value — a
filter authored as `eq` with `">100"` as its value bypasses the operator
vocabulary entirely and reads as an exact match to every check that looks.

## What you cannot author, and why

Two entries binding one param are refused (`RULE-ENDP-066`). A param is one slot
holding one value, so nothing in the request would say which of the two
comparisons was meant, and one of them reads the wrong rows. That is the failure
this map exists to make unrepresentable, and it is why each operator needs its
own param:

<!-- invalid: RULE-ENDP-066 -->
```json
{
  "operations": {
    "read": {
      "params": {
        "amount": { "in": "query", "type": "number", "required": false }
      },
      "request": {
        "method": "GET",
        "path": "/v1/invoices",
        "query": { "amount": { "from_param": "amount" } }
      },
      "filters": {
        "amount": { "gt": "amount", "lt": "amount" }
      },
      "response": {
        "records": { "ref": "response.body.data" },
        "schema": {
          "type": "object",
          "properties": {
            "data": {
              "type": "array",
              "native_type": "array",
              "arrow_type": "List",
              "items": {
                "type": "object",
                "native_type": "object",
                "arrow_type": "Object",
                "properties": {
                  "amount": { "type": "number", "native_type": "number", "arrow_type": "Float64" }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

If the provider takes every comparison through one parameter — an OData-style
`$filter`, say — that shape is not authorable at all, for the reason above.
Offer nothing on that field and raise the gap. An operator the endpoint cannot
express is one no stream should be told it may use.

## Which operators exist

The vocabulary is `RULE-ENDP-055`, printed off the live model in
`connector-builder/references/rules/api-endpoint.md`.

Offer only what the provider documents. Every entry is a promise a stream will
hold you to.
