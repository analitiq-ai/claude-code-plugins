# Filters: which operator reaches the wire, and how

A read operation's `filters` map is what makes a stream able to narrow the read.
It says which record fields are filterable, and — per operator — which declared
param carries the comparison. Every operator offered has to bind a param
(`RULE-ENDP-065`), because a param is the only route from this document to the
wire; an operator with no entry is one no stream is told it may use.

Read `spec-request-binding.md` first: a filter binding names a param, and the
param still reaches the wire the ordinary way.

## Contents

- The three places, again
- Keys are record fields, not param names
- When the provider spells the comparison in the value
- What you cannot author, and why
- Which operators exist

## The three places, again

Same shape as pagination and replication. A filter binding does not create a
request binding on its own — the param it names must be:

1. **declared** in `params`,
2. **named** by an operator under `filters.<record field>`, and
3. **bound** into the request with `{"from_param": …}`.

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
    "amount": {
      "gt": { "param": "min_amount" },
      "lt": { "param": "max_amount" }
    },
    "status": {
      "eq": { "param": "status" }
    }
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

A stream filtering `amount gt 100` now sends `?amount_from=100`; one filtering
`amount lt 500` sends `?amount_to=500`. Two operators, two requests — which is
the whole point of the map.

## Keys are record fields, not param names

A `filters` key is a dotted path into the record shape `response.records`
selects, and it must resolve there (`RULE-ENDP-068`) — the same resolution a
replication `cursor_field` gets. A stream's `field` names the record field it
narrows on, and the map is what turns that into a request. The param name is an
implementation detail of this endpoint and never leaves it.

## When the provider spells the comparison in the value

Some providers put the operator inside the value rather than in the key —
`amount=<>0`, `q=created>2020-01-01`, `$filter=amount gt 100`. Render it with
the binding's `value`, which is an ordinary value expression reading the
filter's own value:

<!-- validate: api-endpoint#/operations/read/filters -->
```json
{
  "created": {
    "gt": {
      "param": "q",
      "value": { "template": "created>${stream.filter.value}" }
    }
  }
}
```

`${stream.filter.value}` is the only thing a binding reads from the stream scope
— the field and the operator are the keys it already sits under — and a rendered
value that never interpolates it is refused, because it would send a predicate
the stream did not ask for (`RULE-ENDP-067`).

## What you cannot author, and why

Two entries that send one param the same value are refused (`RULE-ENDP-066`):
they build the identical request, so the provider cannot tell the comparisons
apart and one of them reads the wrong rows. That is the failure this map exists
to make unrepresentable, and it is why each operator needs its own param or its
own rendered value:

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
        "amount": {
          "gt": { "param": "amount" },
          "lt": { "param": "amount" }
        }
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

If the provider genuinely accepts only one bound on a field, offer only the
operator it accepts. An operator the endpoint cannot express is one no stream
should be told it may use.

## Which operators exist

The vocabulary is `RULE-ENDP-055`, printed off the live model in
`connector-builder/references/rules/api-endpoint.md`. It excludes the operators
only a SQL dialect can express: those compile into a predicate, and an HTTP
provider has no equivalent to serialise them into.

Offer only what the provider documents. Every entry is a promise a stream will
hold you to.
