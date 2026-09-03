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

The map is what tells a stream it may narrow `amount` with `gt` and with `lt`,
and it gives each its own param, so each comparison has somewhere of its own to
go. That is the whole point of it.

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
— the field and the operator are the keys it already sits under. A rendered
value must interpolate it and must not restate it unchanged (`RULE-ENDP-067`):
one that drops it carries a predicate the stream did not ask for, and a bare
`{"ref": "stream.filter.value"}` is what omitting `value` already spells.

A template of only that placeholder is a different thing and is allowed —
resolution stringifies what it substitutes, so `{"template": "${stream.filter.value}"}`
is how you ask for a numeric or boolean filter value in its string form.

`value` is a `ref` or a `template`, and nothing else. A `literal` is opaque to
the resolver, so it could never carry the filter's value. A `function` is
excluded because what it returns cannot be known from the document — and what
you would reach for one to do is wire encoding, which the engine owns: encoding
here sends the value double-escaped and the provider matches nothing.

## What you cannot author, and why

Two entries binding one param are refused (`RULE-ENDP-066`). A param is one slot
holding one value, so nothing in the request would say which of the two
comparisons was meant, and one of them reads the wrong rows. That is the failure
this map exists to make unrepresentable, and it is why each operator needs its
own param — a rendered value of its own is not an escape, because the two would
still contend for the one slot:

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

If the provider takes both bounds through one parameter — an OData-style
`$filter`, say — it accepts one comparison per request, so offer the operator it
is most useful for and leave the rest unoffered. An operator the endpoint cannot
express is one no stream should be told it may use.

## Which operators exist

The vocabulary is `RULE-ENDP-055`, printed off the live model in
`connector-builder/references/rules/api-endpoint.md`.

Offer only what the provider documents. Every entry is a promise a stream will
hold you to.
